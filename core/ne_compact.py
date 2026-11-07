"""Conservative NTB resource garbage collection, integrated with Save/Save As.

Works with a structurally verified modern FlatBuffers page schema. Do not shrink a
shared multi-page PDF: the remaining pages can still reference that file.
This is not a native Notability roundtrip/compatibility guarantee.
"""

from __future__ import annotations
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile

import formats.ne_ntb as ntb
from formats.ne_native import NativeWriteError
from formats.ne_pagewrite import validate_page_structure

ASSET_FILENAME = re.compile(r"^assets/([0-9a-fA-F]{128})(?:\.(png|pdf|jpeg|jpg))?$", re.IGNORECASE)


class CompactError(ValueError):
    pass


@dataclass(frozen=True)
class CompactReport:
    before_bytes: int
    candidate_bytes: int
    candidates: tuple[str, ...]
    remaining_pages: int
    shared_pdf_bytes: int

    @property
    def estimated_bytes(self):
        return self.before_bytes - self.candidate_bytes


def _active_asset_hashes(buffer, events):
    """Collect every active page-PDF and image hash, rejecting unclear image refs."""
    hashes = set()
    pages = ntb.page_records(events, buffer)
    for page in pages:
        if page.get('asset'):
            hashes.add(page['asset'])
    for _, event in events:
        typ = event.field(4)
        if not typ or buffer.data[typ] != 22:
            continue
        payload = ntb.payload_table(buffer, event)
        image = ntb.child_table(buffer, payload, 10) if payload else None
        asset = ntb.child_table(buffer, image, 0) if image else None
        pos = asset.field(0) if asset else 0
        if not pos or not buffer.valid(pos, 64):
            raise CompactError('图片事件存在未识别的资源引用，已停止清理以防丢失图片')
        hashes.add(buffer.data[pos:pos+64].hex())
    return pages, hashes


def assets_to_remove_after_save(archive, saved_bundle, new_assets=(), ignored_metadata=()):
    """Return safe orphan names AFTER the final page/stroke bundle was built.

    This uses only validated active page and image events. Unknown metadata
    references keep their assets. Any uncertainty aborts instead of corrupting a
    note. No original archive entries are mutated during this analysis.
    """
    names = [item.filename for item in archive.infolist()]
    if len(names) != len(set(names)):
        raise CompactError('NTB 存在重复 ZIP 成员，已停止物理清理。')
    try:
        buf, _, events, page_ids = validate_page_structure(saved_bundle)
    except NativeWriteError as exc:
        raise CompactError('NTB 页面版本不支持安全物理清理：' + str(exc)) from exc
    pages, active = _active_asset_hashes(buf, events)
    if len(pages) != len(page_ids):
        raise CompactError('保存后页面结构异常，无法安全回收素材')
    assets = {}
    for info in archive.infolist():
        match = ASSET_FILENAME.fullmatch(info.filename)
        if match:
            key = match.group(1).lower()
            if key in assets:
                raise CompactError('NTB 存在重复素材哈希，已停止物理清理')
            assets[key] = info
    missing = active - (set(assets) | {name.split("/")[1].split(".")[0] for name in new_assets})
    if missing:
        raise CompactError('保留页面引用了原包中不存在的素材，已停止保存，避免损坏笔记')
    # Check non-asset metadata; it may reference otherwise orphaned assets.
    metadata = bytearray()
    for info in archive.infolist():
        if info.filename.startswith('assets/') or info.filename == 'noteBundle' or info.filename in ignored_metadata:
            continue
        if info.file_size > 32 * 1024 * 1024:
            # Unknown large metadata: conservatively retain all resources.
            return set()
        metadata.extend(archive.read(info))
    other = bytes(metadata)
    obsolete = set()
    for h, info in assets.items():
        if h in active:
            continue
        if bytes.fromhex(h) in other or h.encode('ascii') in other:
            continue
        obsolete.add(info.filename)
    return obsolete


def analyze_ntb(path):
    """Analyze without writing; never count live/shared assets as garbage."""
    path = Path(path)
    if path.suffix.lower() != '.ntb':
        raise CompactError('仅支持 NTB 文件')
    with ZipFile(path) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        if len(names) != len(set(names)):
            raise CompactError('压缩包存在重名成员，无法安全清理')
        bundle = archive.read('noteBundle')
        try:
            buf, root, events, page_ids = validate_page_structure(bundle)
        except NativeWriteError as exc:
            raise CompactError('当前 NTB 页面结构不满足安全清理条件：' + str(exc)) from exc
        pages, used_hashes = _active_asset_hashes(buf, events)
        if len(pages) != len(page_ids):
            raise CompactError('页面事件数量异常，无法安全清理')
        assets = {}
        for member in members:
            match = ASSET_FILENAME.fullmatch(member.filename)
            if match:
                assets[match.group(1).lower()] = member
        absent = used_hashes - assets.keys()
        if absent:
            raise CompactError('文件内有已引用但缺失的图片/PDF 素材，暂不清理')
        # No blind archive compression: only unreferenced content-addressed
        # assets can be removed. Keep entries of other formats unchanged.
        candidates = [item for key, item in assets.items() if key not in used_hashes]
        # Do not delete files also referenced in other metadata/index records.
        metadata = b''
        for item in members:
            if item.filename.startswith('assets/') or item.filename == 'noteBundle':
                continue
            if item.file_size > 32*1024*1024:
                raise CompactError('存在体积过大的未知索引文件，无法安全确认资源依赖')
            metadata += archive.read(item)
        safe = []
        for item in candidates:
            key = ASSET_FILENAME.fullmatch(item.filename).group(1)
            raw_hash = bytes.fromhex(key)
            if raw_hash in metadata or key.encode('ascii') in metadata:
                continue
            safe.append(item)
        pdf_bytes = sum(item.file_size for key,item in assets.items()
                        if key in used_hashes and item.filename.endswith('.pdf'))
        # Total on-disk savings include per-entry ZIP headers, but compressed
        # byte totals give a conservative meaningful estimate.
        return CompactReport(path.stat().st_size, sum(item.compress_size for item in safe),
                             tuple(item.filename for item in safe), len(pages), pdf_bytes)


def compact_ntb(source, destination, progress=lambda value: None, cancelled=lambda: False):
    """Write a separately named copy, CRC-check kept entries, atomic commit."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise CompactError('为保护原始文件，清理后的文件必须另存为不同的路径')
    if destination.suffix.lower() != '.ntb':
        raise CompactError('输出文件必须使用 .ntb 扩展名')
    if destination.exists():
        raise CompactError('目标文件已存在，请使用新的文件名，避免覆盖已有笔记')
    report = analyze_ntb(source)
    if not report.candidates:
        raise CompactError('未找到可以安全移除的独立素材。剩余页面可能共享原始 PDF；不能强制删除。')
    obsolete = set(report.candidates)
    temporary = None
    try:
        with ZipFile(source) as original:
            kept = [item for item in original.infolist() if item.filename not in obsolete]
            total = sum(item.file_size for item in kept)
            done = 0
            fd, temporary = tempfile.mkstemp(prefix='ntb-compact-', suffix='.tmp', dir=destination.parent)
            os.close(fd)
            with ZipFile(temporary, 'w', allowZip64=True) as new:
                new.comment = original.comment
                for item in kept:
                    if cancelled():
                        raise CompactError('用户取消了清理操作，原文件未更改')
                    with original.open(item) as src, new.open(item, 'w', force_zip64=True) as dst:
                        while True:
                            if cancelled():
                                raise CompactError('用户取消了清理操作，原文件未更改')
                            chunk = src.read(4*1024*1024)
                            if not chunk:
                                break
                            dst.write(chunk)
                            done += len(chunk)
                            progress(min(95, int(95*done/max(1,total))))
            with ZipFile(temporary) as new:
                if new.namelist() != [item.filename for item in kept]:
                    raise CompactError('清理结果成员列表异常')
                for item in kept:
                    actual = new.getinfo(item.filename)
                    if (actual.CRC, actual.file_size) != (item.CRC, item.file_size):
                        raise CompactError('清理后的文件存在素材校验错误')
                if new.testzip() is not None:
                    raise CompactError('清理后的 ZIP CRC 检查失败')
            # Re-analyze to make sure all active references are satisfied.
            final = _validate_staged_ntb(temporary, report.remaining_pages)
            if not final:
                raise CompactError('清理结果没有通过结构验证')
        if cancelled():
            raise CompactError('用户取消了清理操作，原文件未更改')
        os.replace(temporary, destination)
        temporary = None
        progress(100)
        return report
    finally:
        if temporary is not None:
            try: os.unlink(temporary)
            except FileNotFoundError: pass


def _validate_staged_ntb(path, expected_pages):
    with ZipFile(path) as archive:
        buf, _, events, ids = validate_page_structure(archive.read('noteBundle'))
        if len(ids) != expected_pages:
            return False
        _, active = _active_asset_hashes(buf, events)
        present = {m.group(1).lower() for f in archive.namelist()
                   if (m := ASSET_FILENAME.fullmatch(f))}
        return active <= present
