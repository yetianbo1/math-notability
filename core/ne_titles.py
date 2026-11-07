"""Read the effective NTB note title from real Notability title-change events.

A title-change event has event type 1, payload slot 0 -> table slot 0 ->
FlatBuffers UTF-8 string. Later events override earlier ones. The ZIP's
manifest.subjectName identifies the notebook/subject, not the note title.
"""
from __future__ import annotations


def validate_note_title(title: str) -> str:
    if not isinstance(title, str) or not title or title in ('.', '..'):
        raise ValueError('笔记名称不能为空')
    if any(ord(c) < 32 for c in title) or '\x00' in title:
        raise ValueError('笔记名称含不支持的控制字符')
    if len(title.encode('utf-8')) > 1024:
        raise ValueError('笔记名称过长（UTF-8 不得超过 1024 字节）')
    return title


def effective_title(bundle: bytes, *, structure=None) -> str | None:
    from formats.ne_native import read_structure
    from formats.ne_ntb import child_table
    buf, _root, events = read_structure(bundle) if structure is None else structure
    for _offset, event in reversed(events):
        typ = event.field(4)
        if not typ or not buf.valid(typ, 1) or buf.data[typ] != 1:
            continue
        payload = child_table(buf, event, 5)
        name_table = child_table(buf, payload, 0) if payload else None
        pos = name_table.field(0) if name_table else 0
        if not pos or not buf.valid(pos, 4):
            continue
        string_pos = pos + buf.u32(pos)
        if not buf.valid(string_pos, 4):
            continue
        length = buf.u32(string_pos)
        if length > 1024 * 1024 or not buf.valid(string_pos + 4, length + 1):
            continue
        if buf.data[string_pos + 4 + length] != 0:
            continue
        try:
            title = buf.data[string_pos + 4:string_pos + 4 + length].decode('utf-8', errors='strict')
            return validate_note_title(title)
        except (UnicodeDecodeError, ValueError):
            continue
    return None
