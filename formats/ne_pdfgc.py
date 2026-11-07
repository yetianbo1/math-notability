"""Physically trim unused pages from embedded NTB PDF backgrounds at save time.

Only processes structurally checked modern active per-page PDF references. Never edits
source assets; emits new content-addressed SHA-512 PDF resources and fixes active
FlatBuffers hashes/page indices. Unsupported/unreadable structures fail closed.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path
import shutil
import struct

from formats.ne_pagewrite import validate_page_structure
import formats.ne_ntb as ntb


class PdfPruneError(ValueError):
    pass


def _references(buffer, events):
    refs = defaultdict(list)
    for _, e in events:
        typ = e.field(4)
        if not typ or buffer.data[typ] != 3:
            continue
        payload = ntb.payload_table(buffer, e)
        background = ntb.child_table(buffer, payload, 1)
        pdf = ntb.child_table(buffer, background, 1)
        asset = ntb.child_table(buffer, pdf, 0)
        if not pdf or not asset:
            continue
        hash_pos = asset.field(0)
        if not hash_pos or not buffer.valid(hash_pos, 64):
            raise PdfPruneError('PDF 素材哈希字段无法识别')
        index_field = pdf.field(4)
        index = ntb.field_u32(buffer, pdf, 4) or 0
        if index_field and not buffer.valid(index_field, 4):
            raise PdfPruneError('PDF 页面索引字段损坏')
        key = buffer.data[hash_pos:hash_pos + 64].hex()
        size_field = asset.field(3)
        count_field = pdf.field(2)
        if (not size_field or not count_field or
                not buffer.valid(size_field, 4) or not buffer.valid(count_field, 4)):
            raise PdfPruneError('PDF 素材长度或总页数字段缺失，不能安全裁剪')
        refs[key].append((hash_pos, index_field, index, size_field, count_field))
    return refs


def _extract_original(archive, name, path, cancelled):
    sha = hashlib.sha512()
    with archive.open(name) as reader, open(path, 'wb') as output:
        while True:
            if cancelled():
                raise PdfPruneError('用户取消操作；原笔记未修改')
            data = reader.read(4 * 1024 * 1024)
            if not data:
                break
            output.write(data)
            sha.update(data)
    return sha.hexdigest()


def _hash_path(path, cancelled):
    result = hashlib.sha512()
    with open(path, 'rb') as stream:
        while True:
            if cancelled():
                raise PdfPruneError('用户取消操作；原笔记未修改')
            data = stream.read(4 * 1024 * 1024)
            if not data:
                break
            result.update(data)
    return result.hexdigest()


def _pdf_subset(source, target, indexes, cancelled):
    try:
        import fitz  # PyMuPDF, used without rasterising any pages.
    except ImportError as exc:
        raise PdfPruneError('缺少 PyMuPDF；请使用最新 INSTALL.bat 安装依赖后重新构建') from exc
    with fitz.open(str(source)) as doc:
        if doc.needs_pass or doc.is_encrypted:
            raise PdfPruneError('PDF 背景有加密限制，不能安全裁剪')
        if any(i >= doc.page_count or i < 0 for i in indexes):
            raise PdfPruneError('保留页面引用的 PDF 页码超出实际页数')
        if len(indexes) == doc.page_count:
            return False
        with fitz.open() as result:
            start = previous = indexes[0]
            for index in indexes[1:] + [None]:
                if index is not None and index == previous + 1:
                    previous = index
                    continue
                if cancelled():
                    raise PdfPruneError('用户取消操作；原笔记未修改')
                result.insert_pdf(doc, from_page=start, to_page=previous,
                                  links=True, annots=True, widgets=False)
                if index is None:
                    break
                start = previous = index
            result.save(str(target), garbage=4, deflate=True)
    with fitz.open(str(target)) as checked:
        if checked.page_count != len(indexes):
            raise PdfPruneError('PDF 页面裁剪后页数不一致')
    return True


def trim_embedded_pdf_pages(archive, saved_bundle: bytes, temporary_directory,
                            cancelled=lambda: False):
    """Return (patched_bundle, {new ZIP member: disk Path}).

    Keep original PDF when all its pages are used. If a smaller subset is made,
    only the active PDF reference fields are remapped. Caller garbage-collects
    original PDF via assets_to_remove_after_save and stages the new file in ZIP.
    """
    buf, _, events, _ = validate_page_structure(saved_bundle)
    refs = _references(buf, events)
    names = set(archive.namelist())
    changes = bytearray(saved_bundle)
    created = {}
    temp = Path(temporary_directory)
    for key, entries in refs.items():
        if cancelled():
            raise PdfPruneError('用户取消操作；原笔记未修改')
        name = f'assets/{key}.pdf'
        if name not in names and f'assets/{key}' in names:
            name = f'assets/{key}'
        if name not in names:
            raise PdfPruneError('当前笔记的 PDF 背景资源不存在或名称不标准：' + key[:16])
        indexes = sorted({entry[2] for entry in entries})
        if not indexes:
            continue
        source = temp / f'{key}-source.pdf'
        output = temp / f'{key}-trimmed.pdf'
        if _extract_original(archive, name, source, cancelled) != key:
            raise PdfPruneError('原 PDF 内容与 NTB 素材哈希不符，停止物理裁剪')
        if not _pdf_subset(source, output, indexes, cancelled):
            source.unlink(missing_ok=True)
            continue
        # If pages share huge bitmaps, a subset can be no smaller; don't
        # sacrifice compatibility/space for a no-op replacement.
        if output.stat().st_size >= archive.getinfo(name).file_size:
            source.unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            continue
        new_key = _hash_path(output, cancelled)
        new_name = f'assets/{new_key}.pdf'
        if new_name in names or new_name in created:
            raise PdfPruneError('PDF 新素材哈希与归档中已有条目冲突，已停止保存')
        mapping = {old: new for new, old in enumerate(indexes)}
        output_size = output.stat().st_size
        if output_size >= 2**32:
            raise PdfPruneError('裁剪后的 PDF 超过资源长度字段范围')
        for hash_pos, index_field, old_index, size_field, count_field in entries:
            changes[hash_pos:hash_pos + 64] = bytes.fromhex(new_key)
            struct.pack_into('<I', changes, size_field, output_size)
            struct.pack_into('<I', changes, count_field, len(indexes))
            new_index = mapping[old_index]
            if new_index and not index_field:
                raise PdfPruneError('新 PDF 页码需要不存在的字段，已停止保存')
            if index_field:
                struct.pack_into('<I', changes, index_field, new_index)
        created[new_name] = output
        source.unlink(missing_ok=True)

    patched = bytes(changes) if created else saved_bundle
    if created:
        checkbuf, _, checkevents, _ = validate_page_structure(patched)
        actual = ntb.page_records(checkevents, checkbuf)
        original = ntb.page_records(events, buf)
        if len(actual) != len(original):
            raise PdfPruneError('裁剪后 NTB 页面数量变化，停止保存')
        for before, after in zip(original, actual):
            if before['id'] != after['id'] or before.get('width') != after.get('width') or before.get('height') != after.get('height'):
                raise PdfPruneError('裁剪后页面标识或尺寸改变，停止保存')
        valid_hashes = {name.split('/')[1].split('.')[0] for name in created}
        if not all(rec.get('asset') in valid_hashes or rec.get('asset') == old.get('asset')
                   for rec, old in zip(actual, original)):
            raise PdfPruneError('裁剪后 PDF 引用未通过校验')
    return patched, created
