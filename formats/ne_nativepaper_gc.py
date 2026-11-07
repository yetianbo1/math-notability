"""Conservative *physical* resource reclamation for native-paper page edits.

This module is NOT the PDF importer or a general NTB compactor.  It only
removes a content-addressed image when (a) a page edit explicitly discards a
source page, (b) the image was referenced by a recognized type-22 image event
before the edit, (c) no surviving event refers to the same SHA-512 hash, and
(d) no other archive metadata mentions that hash.  Unknown files survive.

The native-paper writer currently retains unreachable historical FlatBuffers
bytes in the preserved noteBundle suffix, so this is resource-file garbage
collection, NOT a forensic/secure-delete mechanism.
"""
from __future__ import annotations

import re
from zipfile import ZipFile

import formats.ne_ntb as ntb
from formats.ne_native import read_structure


class NativePaperGCError(ValueError):
    pass


IMAGE_ASSET = re.compile(r'^assets/([0-9a-fA-F]{128})\.(png|jpeg|jpg)$', re.IGNORECASE)
ANY_HASH_ASSET = re.compile(r'^assets/([0-9a-fA-F]{128})\.[a-zA-Z0-9]+$', re.IGNORECASE)


def _image_hashes(bundle: bytes) -> set[str]:
    """Find live image hashes by traversing the *active event vector* only.

    Dead objects still physically present in the FlatBuffers tail are not
    active references, and must not prevent actual archive cleanup.
    """
    buf, _, events = read_structure(bundle)
    hashes = set()
    for _, event in events:
        kind_at = event.field(4)
        if not kind_at or not buf.valid(kind_at) or bundle[kind_at] != 22:
            continue
        payload = ntb.payload_table(buf, event)
        if not payload:
            raise NativePaperGCError('图片事件缺少有效载荷，无法判断素材是否仍在使用')
        pointer = payload.field(10)
        if not pointer:
            # The official Python.ntb has one empty image-placement placeholder
            # (event 5) without a resource reference. Keep the event as-is.
            continue
        image = ntb.child_table(buf, payload, 10)
        asset = ntb.child_table(buf, image, 0)
        hash_at = asset.field(0) if asset else 0
        if not hash_at or not buf.valid(hash_at, 64):
            raise NativePaperGCError('图片事件存在不完整的 SHA-512 资源引用')
        hashes.add(bundle[hash_at:hash_at + 64].hex())
    return hashes


def orphan_images_after_page_edit(archive: ZipFile, before: bytes, after: bytes, plan) -> set[str]:
    """Determine which image ZIP members can be physically omitted.

    No pruning for a pure rearrangement or duplicated page. Caller must have
    already structurally verified the native-paper input and output. Every
    candidate is reachable in `before` but absent from `after`; unrecognized
    orphan assets in an existing file are intentionally left untouched.
    """
    from formats.ne_grouped_paper import native_paper_info

    count = native_paper_info(before)
    if native_paper_info(after) != len(plan):
        raise NativePaperGCError('页面编辑后的页数与预期不符')
    kept = {item.get('source', item.get('background_source')) for item in plan}
    kept.update(item.get('background_source') for item in plan)
    kept.discard(None)
    if set(range(count)) <= kept:
        return set()

    names = archive.namelist()
    if len(names) != len(set(names)):
        raise NativePaperGCError('原文件存在重复 ZIP 成员，无法安全回收空间')
    files = {}
    all_files = {}
    for name in names:
        any_match = ANY_HASH_ASSET.fullmatch(name)
        if not any_match:
            continue
        digest = any_match.group(1).lower()
        if digest in all_files:
            raise NativePaperGCError('存在相同哈希的多个素材文件，无法确定资源归属')
        all_files[digest] = name
        if IMAGE_ASSET.fullmatch(name):
            files[digest] = name

    original = _image_hashes(before)
    surviving = _image_hashes(after)
    if not surviving <= original:
        raise NativePaperGCError('重建笔记出现新的未知图片引用，不能删除原始素材')
    missing = original - all_files.keys()
    if missing:
        raise NativePaperGCError('原笔记引用的图片素材缺失，已停止物理清理')
    candidates = original - surviving
    if not candidates:
        return set()

    # External indexes and manifests may hold references to otherwise deleted
    # images. Search their raw hash AND printable SHA-512, and keep on match.
    # Be conservative if unknown huge metadata cannot be inspected safely.
    metadata = bytearray()
    for info in archive.infolist():
        if info.filename in ('noteBundle', 'thumbnail.png') or info.filename.startswith('assets/'):
            continue
        if info.file_size > 32 * 1024 * 1024:
            return set()
        metadata.extend(archive.read(info))
    other = bytes(metadata)
    obsolete = set()
    for digest in candidates:
        if digest not in files:  # unknown image format: keep without guessing
            continue
        if bytes.fromhex(digest) in other or digest.encode('ascii') in other or digest.upper().encode('ascii') in other:
            continue
        obsolete.add(files[digest])
    return obsolete


def validate_native_images(archive: ZipFile, bundle: bytes) -> None:
    """Post-save check that every active image reference has an archive file."""
    files = {m.group(1).lower() for name in archive.namelist()
             if (m := ANY_HASH_ASSET.fullmatch(name))}
    missing = _image_hashes(bundle) - files
    if missing:
        raise NativePaperGCError('保存后的 NTB 遗失仍被引用的图片，已停止替换目标文件')
