"""Minimal, bounds-checked reader for the ink and page geometry in NTB bundles."""
import json
import math
import struct
from zipfile import ZipFile, BadZipFile

from lxml import etree


class NTBError(ValueError):
    pass


class Buffer:
    def __init__(self, data):
        self.data = data
        self.size = len(data)
        self._tables = {} if type(data) is bytes else None

    def valid(self, pos, size=1):
        return 0 <= pos and pos + size <= self.size

    def u16(self, pos):
        return struct.unpack_from('<H', self.data, pos)[0]

    def u32(self, pos):
        return struct.unpack_from('<I', self.data, pos)[0]

    def i32(self, pos):
        return struct.unpack_from('<i', self.data, pos)[0]

    def f32(self, pos):
        return struct.unpack_from('<f', self.data, pos)[0]


class Table:
    def __init__(self, buf, offset, schema=None):
        self.buf, self.offset = buf, offset
        self.schema = schema
        self.vtable = offset - buf.i32(offset)
        self.vsize, self.osize = buf.u16(self.vtable), buf.u16(self.vtable + 2)
        self.slots = (self.vsize - 4) // 2
        # Cache only immutable input. Mutable buffers remain live for repair tools.
        self._fields = (tuple(offset + rel if rel else 0 for rel in
                              struct.unpack_from('<' + 'H'*self.slots, buf.data, self.vtable+4))
                        if isinstance(buf.data, bytes) else None)

    def field(self, slot):
        if self._fields is not None:
            return self._fields[slot] if 0 <= slot < self.slots else 0
        pos = self.vtable + 4 + slot * 2
        if slot < 0 or pos + 2 > self.vtable + self.vsize:
            return 0
        rel = self.buf.u16(pos)
        return self.offset + rel if rel else 0


def table_at(buf, offset, schema=None):
    cache=buf._tables
    key=(offset,schema)
    if cache is not None and key in cache:return cache[key]
    if not buf.valid(offset, 4):
        return None
    try:
        vt = offset - buf.i32(offset)
        if not buf.valid(vt, 4):
            return None
        vs, os = buf.u16(vt), buf.u16(vt + 2)
        if vs < 4 or vs > 512 or vs % 2 or os < 4 or os > 4096 or not buf.valid(vt, vs) or not buf.valid(offset, os):
            return None
        if schema is None and buf.valid(0, 4) and offset == buf.u32(0):
            schema = 'bundle'
        t = Table(buf, offset, schema)
        if not 0 < t.slots <= 40:
            return None
        # The official JS exporter writes these structs before startObject;
        # its vtable object size therefore excludes the trailing struct.
        # Extend only schema-proven inline fields, never arbitrary offsets.
        inline = {'op': {0: 8}, 'asset': {0: 64}, 'persisted': {0: 64}}
        for slot, width in inline.get(schema, {}).items():
            p = t.field(slot)
            if p:
                if not (offset + 4 <= p <= offset + os) or not buf.valid(p, width):
                    return None
                t.osize = max(t.osize, p + width - offset)
        for slot in range(t.slots):
            p = t.field(slot)
            if p and not (offset + 4 <= p < offset + t.osize):
                return None
        if cache is not None and len(cache)<65536:cache[key]=t
        return t
    except (IndexError, struct.error):
        return None


def table_vector(buf, table, slot):
    """Return the tables in a FlatBuffers vector field, in stored order."""
    field = table.field(slot)
    if not field or not buf.valid(field, 4):
        return []
    vector = field + buf.u32(field)
    if not buf.valid(vector, 4):
        return []
    count = buf.u32(vector)
    if count > 200_000 or not buf.valid(vector + 4, count * 4):
        return []
    result = []
    for index in range(count):
        item = vector + 4 + index * 4
        offset = item + buf.u32(item)
        schema = 'op' if table.schema == 'bundle' and slot == 6 else None
        event = table_at(buf, offset, schema)
        if event:
            result.append((offset, event))
    return result


def field_u32(buf, table, slot, extra=0):
    field = table.field(slot)
    if field and buf.valid(field + extra, 4):
        return buf.u32(field + extra)
    return None


def is_16_12_page_layout(events, buf):
    """Detect the one-based page index layout observed in Notability 16.12."""
    page_markers = []
    ink_page_refs = []
    legacy_page_refs = []
    for _, event in events:
        type_pos, payload_pos = event.field(4), event.field(5)
        if not type_pos or not buf.valid(type_pos, 1) or not payload_pos:
            continue
        event_type = buf.data[type_pos]
        payload = payload_pos + buf.u32(payload_pos)
        table = table_at(buf, payload)
        if not table:
            continue
        if event_type == 3:
            page = field_u32(buf, table, 0, 4)
            if page is not None and 1 <= page <= 5000:
                page_markers.append(page)
        elif event_type == 15:
            page4 = field_u32(buf, table, 0, 4)
            page8 = field_u32(buf, table, 0, 8)
            if page4 is not None and 1 <= page4 <= 5000:
                ink_page_refs.append(page4)
            if page8 is not None:
                legacy_page_refs.append(page8)
    # 16.12 page events carry sequential IDs starting at 2. Ink records point
    # to those IDs at slot0+4. For this layout, the page ID is two greater
    # than the zero-based page index in the attached PDF (verified against
    # PDFIndex.fb / HandwritingIndex.fb in the 16.12.5 sample). Older layouts
    # store a zero-based page value at slot0+8 instead.
    sequential = len(page_markers) >= 2 and page_markers == list(
        range(page_markers[0], page_markers[0] + len(page_markers))
    )
    return (
        sequential
        and page_markers[0] >= 2
        and bool(ink_page_refs)
        and all(page <= page_markers[-1] for page in ink_page_refs)
        and legacy_page_refs
        and all(page == 0 for page in legacy_page_refs)
    )


def hidden_stroke_event_ids(events, buf):
    """Replay type-25 stroke visibility edits and return IDs still hidden.

    In the edited 16.12 NTB sample, type-25 payload slots 0 and 1 are vectors
    of 8-byte references to earlier operation IDs. Slot 0 removes strokes
    from the visible set; slot 1 restores them. Apply the operations in log
    order so a later restore overrides an earlier hide.
    """
    events_by_id = {}
    for _, event in events:
        sequence = event.field(0)
        type_pos = event.field(4)
        if (sequence and buf.valid(sequence + 4, 4) and type_pos
                and buf.valid(type_pos, 1)):
            events_by_id[buf.u32(sequence + 4)] = buf.data[type_pos]

    visible_by_id = {}
    for _, event in events:
        type_pos, payload_pos = event.field(4), event.field(5)
        if not type_pos or not buf.valid(type_pos, 1) or buf.data[type_pos] != 25:
            continue
        if not payload_pos or not buf.valid(payload_pos, 4):
            continue
        payload = table_at(buf, payload_pos + buf.u32(payload_pos))
        if not payload:
            continue
        for slot, visible in ((0, False), (1, True)):
            field = payload.field(slot)
            if not field or not buf.valid(field, 4):
                continue
            vector = field + buf.u32(field)
            if not buf.valid(vector, 4):
                continue
            count = buf.u32(vector)
            if count > 200_000 or not buf.valid(vector + 4, count * 8):
                continue
            for index in range(count):
                # Each entry is an 8-byte reference record; its second u32 is
                # the operation ID. The first word is reserved/zero here.
                target_id = buf.u32(vector + 4 + index * 8 + 4)
                if events_by_id.get(target_id) in (15,18):
                    visible_by_id[target_id] = visible
    return {event_id for event_id, visible in visible_by_id.items() if not visible}


def hidden_image_event_ids(events, buf):
    """Read the final image-visibility state of official type-25 events.

    The provided official image note (模考真题2010英语二(1).ntb) has
    type-22 image ID 3 followed by type-25 hide/show/hide, referencing the
    image in slot 0/1 via (actor=0, event ID). Replay in event order; do
    not apply these slots to page backgrounds or unrelated event types.
    """
    image_ids=set()
    for _,event in events:
        kind=event.field(4)
        ref=event.field(0)
        if kind and buf.valid(kind,1) and buf.data[kind]==22 and ref and buf.valid(ref,8):
            if buf.u32(ref)==0:
                image_ids.add(buf.u32(ref+4))
    visible={}
    for _,event in events:
        kind=event.field(4)
        if not kind or not buf.valid(kind,1) or buf.data[kind]!=25:
            continue
        payload=payload_table(buf,event)
        if payload is None:continue
        for slot,is_visible in ((0,False),(1,True)):
            field=payload.field(slot)
            if not field or not buf.valid(field,4):continue
            vector=field+buf.u32(field)
            if not buf.valid(vector,4):continue
            count=buf.u32(vector)
            if count>200000 or not buf.valid(vector+4,8*count):continue
            for i in range(count):
                off=vector+4+8*i
                actor=buf.u32(off)
                target=buf.u32(off+4)
                if actor==0 and target in image_ids:
                    visible[target]=is_visible
    return {ident for ident,enabled in visible.items() if not enabled}


def _paper_styles(events, buf):
    """Yield valid paper settings in chronological event order.

    Document settings are event-sourced; a later type-1 change supersedes the
    initial US Letter defaults without changing the original NTB bytes.
    """
    for _, event in events:
        pos = event.field(4)
        if not pos or not buf.valid(pos, 1) or buf.data[pos] != 1:
            continue
        document = payload_table(buf, event)
        settings = child_table(buf, document, 1)
        paper = child_table(buf, settings, 0)
        size_pos = paper.field(3) if paper else 0
        if not size_pos or not buf.valid(size_pos, 8):
            continue
        width, height = struct.unpack_from('<2f', buf.data, size_pos)
        if not (math.isfinite(width) and math.isfinite(height)
                and 0 < width <= 10000 and 0 < height <= 10000):
            continue
        yield width, height, paper, child_table(buf, paper, 0)


def document_page_size(events, buf):
    """Read the last valid type-1 document paper size (settings may change)."""
    latest = None
    for width, height, _, _ in _paper_styles(events, buf):
        latest = (width, height)
    return latest


def _plain_paper_style(style, buf):
    if not style:
        return False
    marker = style.field(5)
    if marker and not buf.valid(marker, 1):
        return False
    if marker and buf.data[marker] == 13:
        return True
    rgba = style.field(4)
    return ((not marker or buf.data[marker] == 0) and bool(rgba) and buf.valid(rgba, 4)
            and buf.data[rgba+3] == 255)


def document_paper_template(events, buf):
    styles = list(_paper_styles(events, buf))
    template = _document_paper_template(events, buf, styles)
    if template is None:
        return None
    style = styles[-1][3] if styles else None
    if _plain_paper_style(style, buf):
        pos = style.field(4)
        if pos and buf.valid(pos, 4):
            rgb = buf.data[pos:pos+3]
            if rgb != b'\xff\xff\xff':
                template = dict(template, background='#'+rgb.hex())
    return template


def _document_paper_template(events, buf, style_history):
    """Decode only narrowly verified native ruled/dotted/blank paper previews.

    For the user-provided 2026-10-21 official A4 dotted sample, the initial
    US Letter type-1 setting is superseded by A4; the final style's slot0=1
    plus its paper-size/pitch/margin and event-family fingerprint distinguish
    dots from the official ruled samples. Do not infer a universal enum.
    Rendering only; never edits the original paper setting in noteBundle.
    """
    kinds = [buf.data[e.field(4)] for _, e in events
             if e.field(4) and buf.valid(e.field(4), 1)]
    if not style_history:
        return None
    width, height, paper, style = style_history[-1]
    if not style:
        return None
    kind_pos = style.field(5)
    if kind_pos and buf.valid(kind_pos,1) and buf.data[kind_pos] in (1,2,7):
        # Legacy textured paper uses the same explicit flair and spacing fields.
        # Preset 2 is the supplied official yellow textured paper. It carries
        # no custom RGBA; do not substitute the former guessed yellow fill.
        legacy=buf.data[kind_pos]
        pitch=buf.f32(style.field(1)) if style.field(1) else 36.
        flag=buf.data[style.field(0)] if style.field(0) else 0
        if not math.isfinite(pitch) or not 10<=pitch<=100 or flag not in (0,1):return None
        from core.ne_paper_patterns import YELLOW_PAPER_COLOR
        template = dict(kind='dotted' if flag else 'ruled',pitch=pitch,margin=0.,
                    x_origin=pitch/2,radius=.65,color='#8ca2a9' if legacy==7 else '#b1ad9a',
                    background={1:'#faf7ef',2:YELLOW_PAPER_COLOR,7:'#dfeef3'}[legacy],
                    legacy_paper=legacy,approximate=True)
        if legacy == 2:
            template['texture'] = 'yellow-paper'
        return template
    if not _plain_paper_style(style, buf):
        return None
    # A modern custom-colour Paper can remain blank after page expansion or
    # reordering changes its event-family prefix. Explicit absence of pattern
    # fields must not discard its colour or invent horizontal rulings.
    if (not style.field(5) and not style.field(0) and not style.field(1)
            and style.field(4)):
        return dict(kind='blank', approximate=True)
    pitch_pos = style.field(1)
    pitch = buf.f32(pitch_pos) if pitch_pos and buf.valid(pitch_pos, 4) else 0.0
    insets_pos = paper.field(4)
    left = buf.f32(insets_pos) if insets_pos and buf.valid(insets_pos, 16) else 0.0
    style_flag_pos = style.field(0)
    style_flag = buf.data[style_flag_pos] if style_flag_pos and buf.valid(style_flag_pos,1) else 0
    # The newly supplied 16.13.2 A4 notes use a compact event family
    # [1,3,1,1], without the old type-8/type-14 events.
    compact = _compact_supported_paper_type(events, buf)
    if compact == 'blank':
        return dict(kind='blank', approximate=True)
    if compact == 'ruled':
        return dict(kind='ruled', pitch=36.0, margin=36.0,
                    color='#a3b7d3', approximate=True)
    if compact == 'dotted':
        origin = (width - math.floor(width / 36.0) * 36.0) / 2
        return dict(kind='dotted', pitch=36.0, x_origin=round(origin, 5),
                    radius=1.0, color='#a3b7d3', approximate=True)
    # Rendering recognition and edit-write safety are deliberately separate.
    # Explicit style 0/1 and slot1 pitch also occur in older custom-size
    # notebooks with a SINGLE settings event. Python.ntb uses 567 x 744.18756
    # pt, 33.35294pt rulings and 14.765625pt side margins, confirmed against
    # its official thumbnail. Neither A4/Letter nor a settings-change history
    # is a prerequisite for rendering. This grants no page-write permissions.
    from core.ne_paper_patterns import preview_pattern
    if (style_flag_pos and pitch_pos and buf.valid(style_flag_pos, 1)
            and buf.valid(pitch_pos, 4)):
        insets = (struct.unpack_from('<4f', buf.data, insets_pos)
                  if insets_pos and buf.valid(insets_pos, 16) else None)
        if (insets is not None and
                all(math.isfinite(v) for v in insets)
                and abs(insets[0]) < .1 and abs(insets[1]) < .1
                and 0 <= insets[2] < width / 2
                and abs(insets[2] - insets[3]) < .1):
            flag = buf.data[style_flag_pos]
            if flag in (0, 1):
                spec = preview_pattern('ruled' if flag == 0 else 'dotted',
                                       width, height, pitch)
                if spec:
                    if flag == 0:
                        spec['margin'] = float(insets[2])
                    return spec
    # This compact fresh-note event family has no type-8/type-14 markers.
    # If its paper-setting values are not one of the three verified presets,
    # refuse to *guess* horizontal lines from the generic legacy fallback.
    if (kinds[:4] == [1, 3, 1, 1] and len(style_history) == 2
            and abs(style_history[0][0]-612.) < .05
            and abs(style_history[0][1]-792.) < .05
            and ((abs(width-595.27649) < .1 and abs(height-841.89105) < .1)
                 or (abs(width-612.) < .05 and abs(height-792.) < .05))):
        # Do not infer ruled paper from a partially recognized compact A4
        # or US Letter note with changed/unknown spacing or style fields.
        return None
    # Conservative exact sample family (not all future/other dotted presets).
    # The official paired PDF contains 17 columns x 23 rows of 2pt dots,
    # spaced 36pt, with its first centre at x=(A4_width-16*36)/2, y=36.
    dotted_a4 = (
        len(style_history) == 2
        and kinds.count(3) in (1, 2) and kinds.count(8) == 1
        and kinds.count(14) == 1 and kinds.count(1) >= 2
        and style_flag == 1
        and abs(width - 595.2765) < .55
        and abs(height - 841.8909) < .55
        and math.isfinite(pitch) and abs(pitch - 36.0) < .02
        and math.isfinite(left) and abs(left) < .02
    )
    # This official A4 ruled sheet has an EXPLICIT zero style flag,
    # a 36pt pitch, and 0/0/36/36pt insets. A missing style flag is blank,
    # not ruled. Preserve the official PDF's 36pt left/right line margins.
    if _known_a4_ruled_layout(events, buf):
        return dict(kind='ruled', pitch=36.0, margin=36.0,
                    color='#a3b7d3', approximate=True)
    # The supplied dotted template uses style_flag=1, whereas the known
    # ruled template uses 0. Other style-1 dimensions are unverified: do
    # not quietly paint horizontal lines or permit page rewriting.
    if style_flag_pos and style_flag not in (0, 1):
        # No evidence of how the official app draws style codes 2+.
        return None
    if style_flag == 1 and not dotted_a4:
        return None
    # The official 2026-10-22 unmarked A4 has an initial US Letter default,
    # a later A4 override, and a style with NO pattern flag/pitch. Its companion
    # PDF is a single entirely uniform page. The old generic ruled fallback
    # fabricated horizontal lines on this valid blank document.
    if _known_a4_blank_layout(events, buf):
        return dict(kind='blank', approximate=True)
    # Another new two-settings, no-pitch native-paper variation is UNKNOWN,
    # not a verified ruled paper. Prefer an unmarked preview over inventing
    # horizontal lines when the exact blank fingerprint is no longer valid.
    if (len(style_history) >= 2 and not dotted_a4
            and abs(style_history[0][0]-612.) < .05
            and abs(style_history[0][1]-792.) < .05
            and abs(width - 595.27649) < .1
            and abs(height - 841.89105) < .1
            and kinds.count(8) == 1 and kinds.count(14) == 1):
        # Unknown changes to any of the three verified single-sheet A4
        # styles are not silently promoted to a generic ruled template.
        return None
    if dotted_a4:
        x_origin = (width - math.floor(width / pitch) * pitch) / 2
        return dict(kind='dotted', pitch=round(pitch,5),
                    x_origin=round(x_origin,5), radius=1.0,
                    color='#a3b7d3', approximate=True)
    # The official multi-page note "操作系统第2章进程管理.ntb" uses
    # one 567 x 744pt paper-settings event with style marker 13, but NO
    # pattern flag (slot 0) and NO pitch (slot 1). The prior catch-all
    # fallback incorrectly turned that empty background into 36pt rulings.
    # Marker 13 identifies paper-style metadata, NOT the ruled pattern.
    # Preserve the previously validated older 612 x 792pt ruled-paper
    # template, whose style also omits these two slots, but has an exact
    # legacy type-8/type-14 event family and four 36pt margins.
    if not style.field(0) and not style.field(1):
        insets = (struct.unpack_from('<4f', buf.data, insets_pos)
                  if insets_pos and buf.valid(insets_pos, 16) else None)
        legacy_letter_ruled = (
            len(style_history) == 1
            and abs(width - 612.0) < 0.05 and abs(height - 792.0) < 0.05
            and insets is not None
            and all(math.isfinite(v) and abs(v - 36.0) < 0.05 for v in insets)
            and kinds.count(8) == 1 and kinds.count(14) == 1
            and kinds.count(3) == 1
        )
        if legacy_letter_ruled:
            return dict(kind='ruled', pitch=36.0, margin=36.0,
                        color='#a3b7d3', approximate=True)
        # Missing explicit ruling/dot parameters => no pattern to draw.
        # This is preview-only: all native events, images and strokes remain
        # untouched. Unknown configurations must not fabricate ruled lines.
        return dict(kind='blank', approximate=True)
    # Older unknown families are not silently reclassified as horizontal
    # lines. Only the known family-specific rules above may synthesize a
    # pattern; the original NTB metadata remains unchanged.
    return None


def _known_single_sheet_paper(events, buf, grouped_count, page_records, template):
    """Special-case observed official A4 blank/dotted papers' 2->1 layout.

    The official companion PDF for the provided NTB contains exactly ONE
    sheet even though the internal type-3 range count equals 2. Do not apply
    this override to other paper families or when the second sheet is inked.
    """
    if not (template and template.get('kind') in ('dotted', 'blank', 'ruled')
            and grouped_count == 2 and len(page_records) == 1
            and page_records[0].get('pdf') is None):
        return False
    # Only the observed original single-sheet template uses an extra
    # (undisplayed) range slot. Rebuilt multi-sheet notes have a type-3
    # terminator, and must never be collapsed back to one visible sheet.
    kinds = [buf.data[e.field(4)] for _, e in events if e.field(4)]
    old_paper = (kinds.count(3) == 1 and kinds.count(8) == 1
                 and kinds.count(14) == 1 and kinds.count(1) >= 2)
    compact_type = _compact_supported_paper_type(events, buf)
    compact_paper = (compact_type is not None and kinds.count(3) == 1)
    # Official spacing-1/3 empty notes use only [1, 3, 1, ...] events;
    # settings change several times, while the type-3 record still stores an
    # unused second range slot. This is DISPLAY normalization only. Do not
    # grant edit/write permissions on the basis of this heuristic.
    variable_pitch_paper = (kinds.count(3) == 1 and kinds[:2] == [1, 3]
                            and all(kind in (1, 3) for kind in kinds)
                            and len(list(_paper_styles(events, buf))) >= 2
                            and template.get('kind') in ('ruled', 'dotted'))
    if not (old_paper or compact_paper or variable_pitch_paper):
        return False
    if template.get('kind') == 'dotted' and not (variable_pitch_paper or compact_type == 'dotted' or _known_a4_dots_layout(events, buf)):
        return False
    if template.get('kind') == 'blank' and not (compact_type == 'blank' or _known_a4_blank_layout(events, buf)):
        return False
    if template.get('kind') == 'ruled' and not (variable_pitch_paper or compact_type == 'ruled' or _known_a4_ruled_layout(events, buf)):
        return False
    for _, event in events:
        kind = buf.data[event.field(4)] if event.field(4) else -1
        if kind in (15, 18, 22):
            payload = payload_table(buf, event)
            slot = 2 if kind == 22 else 0
            pos = payload.field(slot) if payload else 0
            if not pos or not buf.valid(pos, 12) or buf.u32(pos + 8) != 0:
                return False
    return True


def walk_tables(buf):
    if not buf.valid(0, 4):
        raise NTBError('noteBundle is truncated')
    root = buf.u32(0)
    found, seen, pending = [], set(), [root]
    while pending:
        off = pending.pop()
        if off in seen or len(seen) > 200000:
            continue
        seen.add(off)
        table = table_at(buf, off)
        if not table:
            continue
        found.append((off, table))
        for slot in range(table.slots):
            pos = table.field(slot)
            if not pos or not buf.valid(pos, 4):
                continue
            target = pos + buf.u32(pos)
            child = table_at(buf, target)
            if child:
                pending.append(target)
                continue
            if not buf.valid(target, 4):
                continue
            length = buf.u32(target)
            if not 0 < length < 200000 or not buf.valid(target + 4, length * 4):
                continue
            body = target + 4
            for i in range(length):
                item = body + i * 4
                if not buf.valid(item, 4):
                    break
                child_off = item + buf.u32(item)
                if table_at(buf, child_off):
                    pending.append(child_off)
    return found


def make_svg(path, has_pdf_background=False, progress=None):
    try:
        with ZipFile(path) as archive:
            bundle = archive.read('noteBundle')
    except (BadZipFile, KeyError, OSError) as exc:
        raise NTBError('Not a valid NTB bundle (noteBundle is missing)') from exc
    buf = Buffer(bundle)
    if not buf.valid(0, 4):
        raise NTBError('noteBundle is truncated')
    root = table_at(buf, buf.u32(0))
    if not root:
        raise NTBError('Invalid noteBundle root')
    events = table_vector(buf, root, 6)
    # Newer schema-20 can ALSO group an entire imported PDF into a single
    # type-3 event. Use the actual page-event relationship, not the version
    # number, to decide whether each page has its own event ID.
    root_version = buf.u16(root.field(7)) if root.field(7) and buf.valid(root.field(7),2) else None
    observed = page_records(events,buf)
    grouped_pdf_layout = (grouped_pdf_page_records(events,buf)
                          or mixed_grouped_pdf_page_records(events,buf))
    native_paper_layout = native_paper_page_records(events,buf,root_version)
    from formats.ne_paper_graph import paper_graph
    paper_layout = paper_graph(events,buf,root_version)
    grouped_count = (len(grouped_pdf_layout['records'])
                     if grouped_pdf_layout else
                     len(native_paper_layout['records'])
                     if native_paper_layout else document_page_count(events,buf))
    from formats.ne_range_pages import range_page_info
    range_with_tail = range_page_info(bundle, structure=(buf,root,events))
    if range_with_tail:
        grouped_count = range_with_tail['count']
    grouped_pdf = grouped_pdf_layout is not None
    # Official Notability can also allocate MANY handwritten paper pages with
    # a single type-3 page-range event, without a PDF asset.  In the user's
    # Python.ntb (16.13.1) 89 sheets are represented by just two type-3
    # events, and type-15/type-22 page references contain (0, group_id,
    # zero_based_sheet_index).  Root version >=19 is NOT sufficient evidence
    # that every page owns a distinct event ID.  Using the latter layout
    # incorrectly collapsed ~21k strokes and ~200 images onto two pages.
    paper_template = document_paper_template(events,buf)
    paper_onepage = _known_single_sheet_paper(events,buf,grouped_count,observed,paper_template)
    paper_signature = native_paper_range_signature(events,buf,root_version)
    grouped_paper = bool(observed and not any(p.get('pdf') is not None for p in observed)
                         and (grouped_count > len(observed) or paper_signature is not None
                              or native_paper_layout is not None))
    page_layout_16_12 = (not grouped_pdf and not grouped_paper and
        ((root_version is not None and root_version >= 19) or is_16_12_page_layout(events,buf)))
    # Grouped paper uses one sheet index in each native object page reference.
    # Publish display page identities for the dedicated, validated remapper;
    # never pass these identities to the old per-page FlatBuffers writer.
    layout = observed if page_layout_16_12 else []
    if grouped_pdf:
        layout = pdf_display_page_records(events,buf,grouped_pdf_layout['records'])
    page_indices = {record['id']: i for i, record in enumerate(layout)}
    grouped_page_indices = ({reference: i for i,reference in
                            enumerate(grouped_pdf_layout['references'])}
                            if grouped_pdf else {})
    if paper_layout:
        grouped_page_indices={ref:i for i,ref in enumerate(paper_layout['references'])}
        layout=paper_layout['records']
        grouped_count=len(layout)
        grouped_paper=True
    hidden_ids = hidden_stroke_event_ids(events, buf)
    moves = stroke_position_overrides(events, buf)
    # Current bundles have an explicit event vector. Avoid scanning every byte
    # and mistaking unrelated nested objects for legacy strokes.
    tables = [] if events else walk_tables(buf)
    layer_states = native_image_event_state(events,buf)
    images = image_records(events, buf, path,
                           grouped_page_indices if grouped_pdf or paper_layout else page_indices,
                           layer_states=layer_states)
    from formats.ne_native_text import text_records
    native_text = text_records(events, buf, grouped_page_indices if paper_layout else page_indices,
                               layer_states=layer_states)
    declared_pages = (1 if paper_onepage else
                      (grouped_count if grouped_paper else (len(layout) or grouped_count)))
    strokes, page_width, page_height = [], 595.0, 842.0
    dimensions = document_page_size(events, buf)
    if dimensions:
        page_width, page_height = dimensions
    if grouped_paper:
        # A 2-sheet range has two type-3 events; the tail marker's "side=1"
        # can make page_records() order the marker first. The range creator,
        # not the marker, owns every sheet's group identity.
        if paper_layout:
            layout=paper_layout['records']
        elif native_paper_layout is not None:
            layout=[dict(record) for record in native_paper_layout['records']]
        else:
            paper_id = paper_signature[0] if paper_signature is not None else observed[0]['id']
            layout = [dict(id=paper_id, pdf=None, width=page_width, height=page_height)
                      for _ in range(declared_pages)]
    if grouped_pdf:
        # The default paper settings can be US Letter (612 x 792), while the
        # imported PDF background is 595 x 842. Preserve the imported paper's
        # actual viewport for each handwritten PDF page.
        first_pdf_page=next((record for record in layout
                             if record.get('pdf') is not None),None)
        if first_pdf_page and 'width' in first_pdf_page and 'height' in first_pdf_page:
            page_width, page_height=first_pdf_page['width'],first_pdf_page['height']
    modern = []
    for event_index, (off, event) in enumerate(events):
        if progress and event_index % 128 == 0:
            progress(event_index, len(events))
        type_pos, payload_pos = event.field(4), event.field(5)
        if not type_pos or not payload_pos or not buf.valid(type_pos, 1) or buf.data[type_pos] != 15:
            continue
        sequence = event.field(0)
        event_id = buf.u32(sequence + 4) if sequence and buf.valid(sequence + 4, 4) else None
        if event_id in hidden_ids:
            continue
        payload = payload_pos + buf.u32(payload_pos)
        pt, scale_pos, encoded_pos = table_at(buf, payload), None, None
        if not pt:
            continue
        origin_pos, scale_pos, encoded_pos = pt.field(1), pt.field(3), pt.field(9)
        is_outline=False
        custom=pt.field(10)
        if custom and buf.valid(custom,4):
            custom_vector=custom+buf.u32(custom)
            if buf.valid(custom_vector,4) and buf.u32(custom_vector)>0:
                encoded_pos=custom;is_outline=True
        width_pos, color_pos = pt.field(8), pt.field(7)
        if not origin_pos or not encoded_pos or not buf.valid(origin_pos, 8):
            continue
        x, y = struct.unpack_from('<2f', buf.data, origin_pos)
        if event_id in moves:
            x, y = moves[event_id][1]
        sx, sy = struct.unpack_from('<2f', buf.data, scale_pos) if scale_pos and buf.valid(scale_pos, 8) else (1.0, 1.0)
        vec = encoded_pos + buf.u32(encoded_pos)
        if not buf.valid(vec, 4):
            continue
        n = buf.u32(vec)
        if n > 2_000_000 or not buf.valid(vec + 4, n):
            continue
        encoded = buf.data[vec + 4:vec + 4 + n]
        # Event coordinates already include the page origin, including blanks.
        decoded = decode_modern_ink(encoded, x, y, sx, sy)
        if not decoded:
            continue
        rgba = tuple(buf.data[color_pos:color_pos+4]) if color_pos and buf.valid(color_pos, 4) else (0, 0, 0, 255)
        width = buf.f32(width_pos) if width_pos and buf.valid(width_pos, 4) else 1.0
        tool_pos = pt.field(4)
        tool = buf.data[tool_pos] if tool_pos and buf.valid(tool_pos, 1) else 0
        tool_name = 'tape' if tool == 3 else 'highlighter' if tool == 2 else 'pen'
        tape_pattern = buf.data[pt.field(6)] if pt.field(6) else 3
        # Current event-based NTB stores the page index in the third uint32
        # of payload slot 0. The trailing-byte encoding belongs to a different
        # NTB layout and silently stacks current-format pages when used here.
        page_field = pt.field(0)
        if grouped_pdf or paper_layout:
            raw_group=buf.u32(page_field+4) if page_field and buf.valid(page_field+4,4) else 0
            raw_index=buf.u32(page_field+8) if page_field and buf.valid(page_field+8,4) else 0
            page=grouped_page_indices.get((raw_group,raw_index))
        elif page_layout_16_12:
            raw_page = buf.u32(page_field + 4) if page_field and buf.valid(page_field + 4, 4) else 1
            page = page_indices.get(raw_page)
        else:
            page = buf.u32(page_field + 8) if page_field and buf.valid(page_field + 8, 4) else 0
        if event_id in moves:
            page_ref = moves[event_id][0]
            page=(grouped_page_indices.get((page_ref[1],page_ref[2]))
                  if grouped_pdf or paper_layout else
                  page_indices.get(page_ref[1])
                  if page_layout_16_12 else page_ref[2])
        rotation_pos = pt.field(2)
        rotation = buf.f32(rotation_pos) if rotation_pos and buf.valid(rotation_pos, 4) else 0.0
        if page is None or page > 100_000 or not all(math.isfinite(v) for v in (x, y, sx, sy, width, rotation)):
            continue
        outline_components=[]
        for commands, pts in decoded:
            d = f'M {pts[0][0]:.3f} {pts[0][1] + page*page_height:.3f} '
            for cmd, values in commands:
                if cmd == 0:
                    c1, c2, end = values
                    d += f'C {c1[0]:.3f} {c1[1]+page*page_height:.3f} {c2[0]:.3f} {c2[1]+page*page_height:.3f} {end[0]:.3f} {end[1]+page*page_height:.3f} '
                elif cmd == 3:
                    d += f'M {values[0][0]:.3f} {values[0][1]+page*page_height:.3f} '
            if all(math.dist(pts[0],point)<1e-8 for point in pts) and all(math.dist(pts[0],point)<1e-8 for _,values in commands for point in values):
                # QtSvg drops zero-length paths; a subpixel segment preserves dots.
                d += f'L {pts[0][0]+.001:.3f} {pts[0][1]+page*page_height:.3f} '
            if is_outline:outline_components.append(d+'Z ')
            else:
                modern.append((page, d, max(0.2, width * (abs(sx)+abs(sy))/2),
                               rgba, event_id, tool_name,False,tape_pattern))
        if outline_components:
            modern.append((page,''.join(outline_components),max(0.2,width*(abs(sx)+abs(sy))/2),
                           rgba,event_id,tool_name,True,tape_pattern))

    from formats.ne_native_shapes import shape_strokes
    shape_indices=(grouped_page_indices if grouped_pdf or paper_layout else
                   page_indices if page_layout_16_12 else
                   {(record['id'],i):i for i,record in enumerate(layout)})
    modern.extend(shape_strokes(events,buf,shape_indices,page_height))
    if modern:
        strokes = modern
    for off, table in tables:
        if table.slots == 5 and table.osize == 32:
            pos = table.field(3)
            if pos and buf.valid(pos, 8):
                w, h = struct.unpack_from('<2f', buf.data, pos)
                if math.isfinite(w) and math.isfinite(h) and 100 < w < 5000 and 100 < h < 5000:
                    page_width, page_height = w, h
    # Unknown table geometry must not overwrite the last document A4 setting.
    if paper_onepage and dimensions:
        page_width, page_height = dimensions
    for off, table in tables:
        if modern or (table.slots, table.osize) != (10, 40):
            continue
        origin_pos = table.field(1)
        payload_pos = table.field(9)
        if not origin_pos or not payload_pos or not buf.valid(origin_pos, 8):
            continue
        vector = payload_pos + buf.u32(payload_pos)
        if not buf.valid(vector, 4):
            continue
        size = buf.u32(vector)
        if size < 27 or not buf.valid(vector + 4, size):
            continue
        blob = buf.data[vector + 4:vector + 4 + size]
        ox, oy = struct.unpack_from('<2f', buf.data, origin_pos)
        colour_pos, width_pos = table.field(7), table.field(8)
        argb = buf.u32(colour_pos) if colour_pos and buf.valid(colour_pos, 4) else 0xFF000000
        scale = buf.f32(width_pos) if width_pos and buf.valid(width_pos, 4) else 0.79
        page = buf.data[off + table.osize - 4] if buf.valid(off + table.osize - 4) else 0
        segments = []
        for start in range(8, len(blob) - 18, 19):
            coords = struct.unpack_from('<6e', blob, start + 7)
            if not all(math.isfinite(v) for v in coords):
                continue
            segments.append(coords)
        if not segments:
            continue
        # The final 19-byte record is a terminator, so omit it.
        segments = segments[:-1] if len(segments) > 1 else segments
        d = f'M {ox:.3f} {oy + page * page_height:.3f} '
        for c1x, c1y, c2x, c2y, ex, ey in segments:
            d += (f'C {ox+c1x:.3f} {oy+c1y+page*page_height:.3f} '
                  f'{ox+c2x:.3f} {oy+c2y+page*page_height:.3f} '
                  f'{ox+ex:.3f} {oy+ey+page*page_height:.3f} ')
        width = max(0.3, 60 / 64 * scale * 1.2)
        strokes.append((page, d, width, (argb >> 16 & 255, argb >> 8 & 255, argb & 255, argb >> 24 & 255)))
    if not strokes and not images and not has_pdf_background and not declared_pages:
        raise NTBError('未找到可显示的笔迹、图片或页面')
    pages = sorted({s[0] for s in strokes} | {i['page'] for i in images} | {max(0, declared_pages - 1)})
    # Keep sparse page indices in document coordinates so handwriting stays on its page.
    max_page = max(pages)
    height = page_height * (max_page + 1)
    svg = etree.Element('{http://www.w3.org/2000/svg}svg', nsmap={None: 'http://www.w3.org/2000/svg'},
                        width=f'{page_width:.2f}', height=f'{height:.2f}',
                        viewBox=f'0 0 {page_width:.2f} {height:.2f}')
    svg.set('data-page-height', repr(page_height))
    svg.set('data-page-count', str(max_page + 1))
    # Native lined paper has no embedded PDF asset. Carry its presentation
    # hint into the GUI background layer, which is rendered behind ink.
    if not has_pdf_background:
        if paper_template:
            svg.set('data-paper-template', json.dumps(paper_template, separators=(',', ':')))
    if layout:
        svg.set('data-pages', json.dumps(layout, separators=(',', ':')))
    for record in images:
        etree.SubElement(svg, '{http://www.w3.org/2000/svg}image',
                         attrib={('data-' + key): str(value) for key, value in record.items()})
    for record in native_text:
        etree.SubElement(svg, '{http://www.w3.org/2000/svg}text',
                         attrib={'data-native-text':json.dumps(record,ensure_ascii=False)})
    # QtSvg renders percentage-sized rects against an incomplete viewport in
    # some Windows builds. Use explicit document units so every page stays white.
    etree.SubElement(svg, '{http://www.w3.org/2000/svg}rect', x='0', y='0',
                     width=f'{page_width:.2f}', height=f'{height:.2f}', fill='white')
    # Keep adjacent PDF pages visually distinct in the continuous scroll view.
    for page in range(max_page + 1):
        top = page * page_height
        etree.SubElement(
            svg, '{http://www.w3.org/2000/svg}rect',
            x='0.5', y=f'{top + 0.5:.2f}',
            width=f'{page_width - 1:.2f}', height=f'{page_height - 1:.2f}',
            fill='none', stroke='#d5d5d5', attrib={'stroke-width': '1'},
        )
        label = etree.SubElement(
            svg, '{http://www.w3.org/2000/svg}text',
            x='8', y=f'{top + 18:.2f}', fill='#888',
            attrib={'font-size': '11', 'font-family': 'sans-serif'},
        )
        label.text = f'Page {page + 1}'
    for page, d, width, color, *identity in strokes:
        path_el = etree.SubElement(svg, '{http://www.w3.org/2000/svg}path', d=d, fill='none',
                                   stroke=f'rgb({color[0]},{color[1]},{color[2]})',
                                   attrib={'data-page': str(page), 'stroke-width': f'{width:.3f}', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'stroke-opacity': f'{color[3]/255:.3f}'})
        if len(identity)>2 and identity[2]:
            path_el.set('fill',f'rgb({color[0]},{color[1]},{color[2]})')
            path_el.set('stroke','none');path_el.set('fill-rule','evenodd')
            path_el.set('fill-opacity',f'{color[3]/255:.3f}');path_el.set('data-ink-outline','1')
        if len(identity) > 1 and identity[1] in ('highlighter', 'tape'):
            path_el.set('data-tool', identity[1])
            if identity[1]=='tape':
                path_el.set('data-tape-pattern',str(identity[3] if len(identity)>3 else 3))
        # Preserve imported Beziers. Tool smoothing only applies to newly
        # captured input; a second pass changes native corners and endpoints.
        path_el.set('data-render-smoothed','1')
        if len(identity)>1 and identity[1]=='tape' and not (len(identity)>2 and identity[2]):
            from editing.ne_edits import svg_paths
            from editing.ne_eraser import tape_outline
            from PyQt6.QtGui import QTransform
            _,centerline=next(svg_paths((etree.tostring(path_el),),0,page_height))
            outline=tape_outline(centerline,width)
            polygons=outline.toFillPolygons(QTransform.fromScale(8.,8.))
            path_el.set('d',' '.join('M '+' L '.join(f'{p.x()/8:.4f} {p.y()/8:.4f}' for p in polygon)+' Z' for polygon in polygons))
            path_el.set('fill',path_el.get('stroke'));path_el.set('stroke','none')
            path_el.set('fill-opacity',path_el.get('stroke-opacity','1'))
            path_el.set('fill-rule','evenodd');path_el.set('data-ink-outline','1')
        if identity and identity[0] is not None:
            path_el.set('data-event-id', str(identity[0]))
            path_el.set('data-z-index',str(layer_states.get(identity[0],{}).get('z_index',0)))
    return svg, page_width, height


def decode_modern_ink(data, ox, oy, sx, sy):
    """Decode attributed centerlines and nonattributed custom outlines."""
    if len(data) < 4 or data[0] not in (0, 1):
        return []
    precision, count, kind = data[0], struct.unpack_from('<H', data, 1)[0], data[3]
    if count < 1 or kind not in (3,7):
        return []
    scalar = 2 if precision == 0 else 4
    cursor=3;paths=[];points=[];commands=[]
    for _ in range(count):
        if cursor>=len(data):return []
        command=data[cursor];cursor+=1
        ncoords=6 if command in (0,4) else 4 if command in (1,5) else 2 if command in (2,3,6,7) else 0
        attributes=6 if command<4 else 0
        if not ncoords or cursor+ncoords*scalar+attributes>len(data):
            return []
        vals = []
        for i in range(ncoords):
            part = data[cursor+i*scalar:cursor+(i+1)*scalar]
            value = struct.unpack('<e' if precision == 0 else '<f', part)[0]
            if not math.isfinite(value):
                return []
            vals.append(value)
        coords = [(ox + vals[i]*sx, oy + vals[i+1]*sy) for i in range(0, ncoords, 2)]
        if command in (3,7):
            if points:
                paths.append((commands, points))
            commands, points = [], [coords[0]]
        else:
            if not points:return []
            if command in (1,5):
                p=points[-1];control,end=coords
                coords=[tuple(p[i]+2*(control[i]-p[i])/3 for i in (0,1)),
                        tuple(end[i]+2*(control[i]-end[i])/3 for i in (0,1)),end]
            elif command in (2,6):
                p=points[-1];end=coords[0]
                coords=[tuple(p[i]+(end[i]-p[i])/3 for i in (0,1)),
                        tuple(p[i]+2*(end[i]-p[i])/3 for i in (0,1)),end]
            commands.append((0,coords));points.append(coords[-1])
        cursor += ncoords*scalar+attributes
    if points:
        paths.append((commands, points))
    return paths


def payload_table(buf, event):
    p = event.field(5)
    kind = buf.data[event.field(4)] if event.field(4) and buf.valid(event.field(4)) else None
    schema = {2: 'persisted', 3: 'page', 22: 'block'}.get(kind)
    return table_at(buf, p + buf.u32(p), schema) if p and buf.valid(p, 4) else None


def is_asset_cloud_persisted(buf, event):
    """Recognize the server's asset acknowledgement, not another content author."""
    ident, kind = event.field(0), event.field(4)
    # Ordinary content has actor zero; it cannot be a cloud acknowledgement.
    if not (ident and buf.valid(ident,8) and buf.u32(ident)==65535):return False
    payload = payload_table(buf, event)
    return bool(ident and buf.valid(ident, 8) and buf.u32(ident) == 65535
                and kind and buf.valid(kind) and buf.data[kind] == 2
                and event.slots <= 6 and not event.field(3)
                and (not event.field(2) or buf.valid(event.field(2), 8))
                and payload and payload.slots == 1 and payload.field(0)
                and buf.valid(payload.field(0), 64))


def document_page_count(events, buf):
    """Type 3 can allocate a range of pages in a single event (16.12.x)."""
    count = 0
    for _, event in events:
        p = event.field(4)
        if p and buf.data[p] == 3:
            t = payload_table(buf, event)
            n = field_u32(buf, t, 2) if t else None
            if n and n <= 100_000:
                count = max(count, n)
    return count


def _compact_native_paper_type(events, buf, *, paper_size):
    """Identify exact 16.13.2 compact A4/Letter blank, ruled or dot samples.

    Size and background are independent properties. A4 has a genuine
    Letter -> A4 size change; Letter retains 612 x 792pt in both settings.
    UI spacing level 4 is stored as 36pt in the supplied ruled/dot samples.
    Unverified layout variants are *not* granted native page write access.
    """
    kinds = [buf.data[e.field(4)] for _, e in events
             if e.field(4) and buf.valid(e.field(4), 1)]
    if kinds[:4] != [1, 3, 1, 1] or any(k not in (1, 3, 15, 17, 18, 22, 23, 24, 25)
                                            for k in kinds[4:]):
        return None
    # Checked lock/z-order edits do not change the paper family. Reject
    # unknown state payloads instead of disabling page management for every
    # compact note containing a locked native image.
    if any(buf.data[e.field(4)] in (17,23,24)
           and not valid_layer_state_event(events,buf,e) for _,e in events):
        return None
    history = list(_paper_styles(events, buf))
    if len(history) != 2:
        return None
    first_w, first_h, _, _ = history[0]
    width, height, paper, style = history[-1]
    if (abs(first_w-612.0) > .02 or abs(first_h-792.0) > .02
            or not paper or not style):
        return None
    if paper_size == 'A4':
        if abs(width-595.27649) > .1 or abs(height-841.89105) > .1:
            return None
    elif paper_size == 'Letter':
        if abs(width-612.) > .02 or abs(height-792.) > .02:
            return None
    elif paper_size == 'custom':
        if not all(math.isfinite(v) and 0<v<=10000 for v in (width,height)):
            return None
    else:
        return None
    marker=style.field(5)
    if not _plain_paper_style(style, buf):
        return None
    margins=paper.field(4)
    if not margins or not buf.valid(margins,16):
        return None
    insets=struct.unpack_from('<4f', buf.data, margins)
    if any(not math.isfinite(v) for v in insets):
        return None
    flag_pos=style.field(0)
    pitch_pos=style.field(1)
    if not flag_pos and not pitch_pos:
        return 'blank' if all(abs(v-36.0)<.05 for v in insets) else None
    if not (flag_pos and pitch_pos and buf.valid(flag_pos,1)
            and buf.valid(pitch_pos,4)
            and abs(buf.f32(pitch_pos)-36.0)<.02
            and all(abs(v-r)<.05 for v,r in zip(insets,(0.,0.,36.,36.)))):
        return None
    return {0:'ruled',1:'dotted'}.get(buf.data[flag_pos])


def _compact_a4_paper_type(events, buf):
    """Official 595.28 x 841.89 pt A4 compact notes only."""
    return _compact_native_paper_type(events, buf, paper_size='A4')


def _compact_letter_paper_type(events, buf):
    """Official 612 x 792 pt US Letter compact notes only."""
    return _compact_native_paper_type(events, buf, paper_size='Letter')


def _compact_supported_paper_type(events, buf):
    """Recognize the same explicit background fields at any valid size."""
    return _compact_native_paper_type(events, buf, paper_size='custom')


def _known_a4_blank_layout(events, buf):
    """Recognize ONLY the observed official schema-20 unmarked A4 family.

    It shares schema 20, type-3 range count 2, and style byte 13 with both
    native ruled and dotted samples, so NONE of those fields suffice alone.
    The verified 16.13.2 PDF has a single unmarked A4 page. This check uses
    the distinctive last paper setting (no style flag/pitch, 36pt insets)
    plus initial Letter -> A4 event history, without touching source bytes.
    """
    if _compact_a4_paper_type(events, buf) == 'blank':
        return True
    history = list(_paper_styles(events, buf))
    if len(history) != 2:
        return False
    first_w, first_h, _, _ = history[0]
    width, height, paper, style = history[-1]
    if (abs(first_w-612.) > .02 or abs(first_h-792.) > .02
            or abs(width-595.27649) > .1 or abs(height-841.89105) > .1
            or not paper or not style or style.field(0) or style.field(1)):
        return False
    marker = style.field(5)
    if not marker or not buf.valid(marker, 1) or buf.data[marker] != 13:
        return False
    margin = paper.field(4)
    if not margin or not buf.valid(margin, 16):
        return False
    insets = struct.unpack_from('<4f', buf.data, margin)
    if any(not math.isfinite(v) or abs(v-36.) > .05 for v in insets):
        return False
    kinds = [buf.data[e.field(4)] for _,e in events
             if e.field(4) and buf.valid(e.field(4), 1)]
    return (kinds.count(1) >= 2 and kinds.count(3) in (1,2)
            and kinds.count(8) == 1 and kinds.count(14) == 1)


def _known_a4_ruled_layout(events, buf):
    """Match the supplied 16.13.2 one-sheet A4 ruled native-paper family.

    The historical US Letter ruled template is structurally different. The
    16.13.2 blank and dotted A4 templates share all event types and page
    range counts; distinguish them by the final style's *presence* and value
    of slot0 and the horizontal-line pitch, not the shared style marker 13.
    """
    if _compact_a4_paper_type(events, buf) == 'ruled':
        return True
    history = list(_paper_styles(events, buf))
    if len(history) != 2:
        return False
    fw, fh, _, _ = history[0]
    width, height, paper, style = history[-1]
    if (abs(fw-612.) > .02 or abs(fh-792.) > .02
            or abs(width-595.27649) > .1 or abs(height-841.89105) > .1
            or not paper or not style):
        return False
    flag = style.field(0)
    pitch = style.field(1)
    marker = style.field(5)
    if not (flag and pitch and marker
            and buf.valid(flag, 1) and buf.data[flag] == 0
            and buf.valid(pitch, 4) and abs(buf.f32(pitch)-36.) < .02
            and buf.valid(marker, 1) and buf.data[marker] == 13):
        return False
    margins = paper.field(4)
    if not margins or not buf.valid(margins, 16):
        return False
    insets = struct.unpack_from('<4f', buf.data, margins)
    if any(not math.isfinite(v) or abs(v-ref) > .05
           for v, ref in zip(insets, (0.,0.,36.,36.))):
        return False
    kinds = [buf.data[e.field(4)] for _, e in events
             if e.field(4) and buf.valid(e.field(4), 1)]
    return (kinds.count(1) >= 2 and kinds.count(3) in (1,2)
            and kinds.count(8) == 1 and kinds.count(14) == 1)


def _known_a4_dots_layout(events, buf):
    """Match official A4 dotted style, never just the common A4 geometry.

    All three user-supplied 2026-10-21/22 paper templates have two valid
    type-1 geometry settings and the same type-3 range count. The final
    *explicit* style flag 1, 36pt pitch and 0/0/36/36 margins identify dots.
    A later title-only event does not override paper settings.
    """
    if _compact_a4_paper_type(events, buf) == 'dotted':
        return True
    history=list(_paper_styles(events,buf))
    if len(history)!=2:
        return False
    fw,fh,_,_=history[0]
    width,height,paper,style=history[-1]
    if (abs(fw-612.)>.02 or abs(fh-792.)>.02
            or abs(width-595.27649)>.1 or abs(height-841.89105)>.1
            or not paper or not style):
        return False
    flag=style.field(0)
    pitch=style.field(1)
    marker=style.field(5)
    if not (flag and pitch and marker and buf.valid(flag,1) and buf.data[flag]==1
            and buf.valid(pitch,4) and abs(buf.f32(pitch)-36.)<.02
            and buf.valid(marker,1) and buf.data[marker]==13):
        return False
    margin=paper.field(4)
    if not margin or not buf.valid(margin,16):
        return False
    insets=struct.unpack_from('<4f',buf.data,margin)
    if any(not math.isfinite(v) or abs(v-ref)>.05
           for v,ref in zip(insets,(0.,0.,36.,36.))):
        return False
    kinds=[buf.data[e.field(4)] for _,e in events
           if e.field(4) and buf.valid(e.field(4),1)]
    return (kinds.count(1)>=2 and kinds.count(3) in (1,2)
            and kinds.count(8)==1 and kinds.count(14)==1)


def native_paper_range_signature(events, buf, root_version=None):
    """Recognize two observed schema-20 *non-PDF* paper-range families.

    (A) A range creator plus a type-3 terminal marker (Python.ntb).
    (B) A single range creator with an initial type-8/type-14 paper-setting
        pair (the user's ruled-paper blank NTB). Both store sheet indices in
        native object refs as (author=0, group_id, zero_based_sheet_index).

    This only identifies the page model. Permission for destructive edits is
    independently checked in formats.ne_grouped_paper.native_paper_info().
    """
    ranges, terminal = [], []
    for _,event in events:
        pos=event.field(4)
        if not pos or not buf.valid(pos,1) or buf.data[pos]!=3:
            continue
        payload=payload_table(buf,event)
        if not payload:
            return None
        count=field_u32(buf,payload,2)
        if count is not None and 1<=count<=10000:
            seq=event.field(0)
            if not seq or not buf.valid(seq,8):return None
            ranges.append((buf.u32(seq+4),count,payload))
        else:
            at=payload.field(0)
            if payload.slots!=1 or not at or not buf.valid(at,12):return None
            seq=event.field(0)
            if not seq or not buf.valid(seq,8):return None
            terminal.append((buf.u32(seq+4),
                             (buf.u32(at),buf.u32(at+4),buf.u32(at+8))))
    if len(ranges)!=1:return None
    group,count,payload=ranges[0]
    if payload.field(0) or payload.field(1):return None
    if len(terminal)==1:
        return ((group,count)
                if terminal[0][1]==(0,group,count-1) else None)
    if len(terminal)==2:
        first_id,first_ref=terminal[0]
        second_id,second_ref=terminal[1]
        if (first_ref==(0,group,count-1)
                and second_ref==(0,first_id,0)):
            return group,count
    if terminal:return None
    template=document_paper_template(events,buf)
    dimensions=document_page_size(events,buf)
    if (template and template.get('kind')=='ruled' and dimensions
            and abs(dimensions[0]-567.)<.01 and abs(dimensions[1]-744.187561)<.01):
        # Canonical rewritten Python ranges omit the old extra blank creator.
        return group,count
    # Official empty ruled-paper template, including after a legal one-sheet
    # save. Do not classify a normal PDF import as this family.
    kinds=[buf.data[e.field(4)] for _,e in events if e.field(4)]
    compact = _compact_supported_paper_type(events,buf)
    if (compact and kinds.count(3)==1
            and (count==2 or root_version==18)):
        return group,count
    # The supplied tape notebook has document-level blank paper metadata
    # without legacy SetPageBackground/ModifyPage events. Only the page model
    # is recognized here; native_paper_info separately checks the full schema.
    if (template and template.get('kind')=='blank' and dimensions
            and kinds.count(3)==1 and all(k in (1,3,15,18,22,23,24,25) for k in kinds)):
        return group,count
    if kinds.count(3)!=1 or kinds.count(8)!=1 or kinds.count(14)!=1:
        return None
    if kinds.count(1)<1:return None
    template=document_paper_template(events,buf)
    if not template or template.get('kind') not in ('ruled', 'dotted', 'blank'):return None
    # Single-range dotted A4 paper has a later type-1 override carrying
    # the actual A4 geometry. Restrict this extension to that exact family.
    if template.get('kind') in ('dotted', 'blank'):
        match = (_known_a4_dots_layout(events, buf) if template['kind']=='dotted'
                 else _known_a4_blank_layout(events, buf))
        if kinds.count(1) < 2 or not match:
            return None
    if template.get('kind') == 'ruled' and len(list(_paper_styles(events, buf))) >= 2:
        # A4 ruled (two geometry-bearing settings events) is NOT the older Letter family.
        if not _known_a4_ruled_layout(events, buf):
            return None
    for _,event in events:
        kind=buf.data[event.field(4)] if event.field(4) else -1
        if kind==8:
            sheet=payload_table(buf,event)
            if not sheet or sheet.slots!=2 or not sheet.field(1):return None
        elif kind==14:
            meta=payload_table(buf,event)
            if not meta or meta.slots!=3 or not meta.field(2):return None
    return group,count


def native_paper_page_records(events, buf, root_version=None):
    """Resolve schema-18 range sheets and marker-created blank pages."""
    if root_version!=18:
        return None
    ranges=[]
    for _,event in events:
        kind=event.field(4)
        if not kind or buf.data[kind]!=3 or not event.field(0):
            continue
        payload=payload_table(buf,event)
        if payload is None:
            return None
        count=field_u32(buf,payload,2)
        background=child_table(buf,payload,1)
        pdf=child_table(buf,background,1)
        if pdf is not None:
            return None
        if count is not None:
            if not 1<=count<=10000:
                return None
            ranges.append((buf.u32(event.field(0)+4),count))
    if len(ranges)!=1:
        return None
    group,count=ranges[0]
    dimensions=document_page_size(events,buf)
    if not dimensions:
        return None
    width,height=dimensions
    records=[dict(id=group,event_id=group,page_index=index,pdf=None,
                  width=width,height=height)
             for index in range(count)]
    markers=0
    for _,event in events:
        kind=event.field(4)
        if not kind or buf.data[kind]!=3 or not event.field(0):
            continue
        payload=payload_table(buf,event)
        if payload is None or field_u32(buf,payload,2) is not None:
            continue
        page_id=buf.u32(event.field(0)+4)
        records.append(dict(id=page_id,event_id=page_id,page_index=0,pdf=None,
                            width=width,height=height))
        markers+=1
    from formats.ne_page_model import resolve_page_order, PageModelError
    try:
        result=resolve_page_order(events,buf,records)
    except PageModelError:
        return None
    result.update(group=group,range_count=count,marker_count=markers)
    return result


def native_image_event_state(events, buf):
    """Replay verified native image state edits in chronological order.

    Image type-22 contains the baseline geometry and optional scale (slot 5).
    Type-24 has a vector of target records; slot 2 is absolute position,
    slot 4 -> nested slot 0 is a 2-float scale when present. The most recent
    valid edit to each property wins. Type-23 slot 0 holds a forward
    uoffset to a vector of (actor, image event ID) structs; slot 17 is
    1 for locked and 0 for unlocked. Later state events take priority.
    """
    state = {}
    for _, event in events:
        kind_pos=event.field(4)
        if not kind_pos or not buf.valid(kind_pos,1):
            continue
        kind=buf.data[kind_pos]
        if kind==24:
            payload=payload_table(buf,event)
            if not payload:continue
            for _,record in table_vector(buf,payload,0):
                target=record.field(0)
                if not target or not buf.valid(target,8):continue
                actor,ident=struct.unpack_from('<2I',buf.data,target)
                if actor!=0:continue
                info=state.setdefault(ident,{})
                z=record.field(5)
                if z and buf.valid(z,8):info['z_index']=struct.unpack_from('<Q',buf.data,z)[0]
                position=record.field(2)
                if position and buf.valid(position,8):
                    xy=struct.unpack_from('<2f',buf.data,position)
                    if all(math.isfinite(q) for q in xy):
                        info['position']=(xy,position)
                scale_table=child_table(buf,record,4)
                scale=scale_table.field(0) if scale_table else 0
                if scale and buf.valid(scale,8):
                    values=struct.unpack_from('<2f',buf.data,scale)
                    if all(math.isfinite(q) and 0.001<q<1000 for q in values):
                        info['scale']=(values,scale)
        elif kind in (15,18,22):
            payload=payload_table(buf,event)
            ref=event.field(0)
            if payload and ref and buf.valid(ref,8):
                actor,ident=struct.unpack_from('<2I',buf.data,ref)
                z=payload.field(14 if kind==15 else 12 if kind==18 else 9) or event.field(1)
                if actor==0 and z and buf.valid(z,8):
                    state.setdefault(ident,{})['z_index']=struct.unpack_from('<Q',buf.data,z)[0]
            lock=payload.field(20) if kind==22 and payload and payload.slots>=21 else 0
            ref=event.field(0)
            if lock and ref and buf.valid(lock,1) and buf.valid(ref,8):
                actor,ident=struct.unpack_from('<2I',buf.data,ref)
                value=buf.data[lock]
                if actor==0 and value in (0,1):
                    info=state.setdefault(ident,{})
                    info['locked']=bool(value)
                    info['lock_source']='slot20'
        elif kind in (17,23):
            # Official T01/T02/T03: payload slot0 is a FLATBUFFERS UOFFSET
            # to a vector of (actor, image event ID) 8-byte structs.
            # The raw slot value is usually 4 (relative offset), not an ID!
            payload=payload_table(buf,event)
            flag=payload.field(17) if kind==23 and payload else 0
            targets=payload.field(0) if payload else 0
            if not (targets and buf.valid(targets,4)):
                continue
            pos=targets+buf.u32(targets)
            if not buf.valid(pos,4):continue
            count=buf.u32(pos)
            if not 1<=count<=5000 or not buf.valid(pos+4,8*count):continue
            value=buf.data[flag] if flag and buf.valid(flag,1) else None
            z=payload.field(13 if kind==17 else 9)
            for j in range(count):
                actor,ident=struct.unpack_from('<2I',buf.data,pos+4+8*j)
                if actor==0:
                    info=state.setdefault(ident,{})
                    if value in (0,1):info.update(locked=bool(value),lock_source='type23')
                    if z and buf.valid(z,8):info['z_index']=struct.unpack_from('<Q',buf.data,z)[0]
    return state


def valid_layer_state_event(events, buf, event):
    """Validate the checked lock and zIndex event layouts.

    These events do not own pages. They may survive a page rewrite with a
    target that is no longer visible, so only their structure, actor and
    primitive values are checked here.
    """
    kind_pos=event.field(4)
    if (not kind_pos or not buf.valid(kind_pos,1) or event.slots!=6
            or not event.field(0) or not buf.valid(event.field(0),8)
            or not event.field(1) or not buf.valid(event.field(1),8)
            or not event.field(5) or not buf.valid(event.field(5),4)):
        return False
    kind=buf.data[kind_pos]
    payload=payload_table(buf,event)
    if not payload:return False
    if kind in (17,23):
        if payload.slots!=18:return False
        allowed={0,9,12,13} if kind==17 else {0,9,17}
        if any(payload.field(slot) and slot not in allowed for slot in range(payload.slots)):
            return False
        targets=payload.field(0)
        if not targets or not buf.valid(targets,4):return False
        position=targets+buf.u32(targets)
        if not buf.valid(position,4):return False
        count=buf.u32(position)
        if not 1<=count<=5000 or not buf.valid(position+4,8*count):return False
        for index in range(count):
            actor,ident=struct.unpack_from('<2I',buf.data,position+4+8*index)
            if actor!=0:return False
        z=payload.field(13 if kind==17 else 9)
        if z and not buf.valid(z,8):return False
        if kind==17 and not z:return False
        if kind==23:
            flag=payload.field(17)
            if not z and not flag:return False
            if flag and (not buf.valid(flag,1) or buf.data[flag] not in (0,1)):return False
        return True
    if kind==24:
        if payload.slots!=1:return False
        records=table_vector(buf,payload,0)
        if not 1<=len(records)<=5000:return False
        for _,record in records:
            if record.slots>6 or not record.field(0) or not buf.valid(record.field(0),8):
                return False
            actor,ident=struct.unpack_from('<2I',buf.data,record.field(0))
            if actor!=0:return False
            z=record.field(5)
            if z and not buf.valid(z,8):return False
            position=record.field(2)
            if position:
                if not buf.valid(position,8):return False
                xy=struct.unpack_from('<2f',buf.data,position)
                if not all(math.isfinite(value) for value in xy):return False
            rotation=record.field(3)
            if rotation and not buf.valid(rotation,4):return False
            scale_table=child_table(buf,record,4)
            scale=scale_table.field(0) if scale_table else 0
            if scale:
                if not buf.valid(scale,8):return False
                values=struct.unpack_from('<2f',buf.data,scale)
                if not all(math.isfinite(value) and 0.001<value<1000 for value in values):
                    return False
        return True
    return False


def image_records(events, buf, path, page_indices=None, *, layer_states=None):
    """Read type-22 image placement and its nested 64-byte content hash.

    The supplied 16.12.3 bundle uses slot 2's third word as page index;
    slots 3/6 hold origin/display size, slot 11 is the source crop rectangle.
    These coordinates match all image matrices in the supplied PDF export.
    """
    with ZipFile(path) as archive:
        assets = {name.rsplit('/', 1)[-1].split('.')[0]: name
                  for name in archive.namelist() if name.startswith('assets/')}
    records = []
    image_state=(native_image_event_state(events,buf) if layer_states is None else layer_states)
    hidden_image_ids=hidden_image_event_ids(events,buf)
    for _, event in events:
        p = event.field(4)
        if not p or buf.data[p] != 22:
            continue
        t = payload_table(buf, event)
        if not t:
            continue
        image = child_table(buf, t, 10)
        asset = child_table(buf, image, 0)
        p = asset.field(0) if asset else 0
        if not p or not buf.valid(p, 64):
            continue
        name = assets.get(buf.data[p:p+64].hex())
        if not name:
            raise NTBError('笔记引用的图片素材缺失')
        origin, size, crop = t.field(3), t.field(6), t.field(11)
        owner = field_u32(buf, t, 2, 4)
        page_index = field_u32(buf, t, 2, 8)
        page = page_index
        if page_indices:
            page = page_indices.get((owner,page_index),
                                    page_indices.get(owner))
        if not origin or not size or not buf.valid(origin, 8) or not buf.valid(size, 8):
            continue
        x, y = struct.unpack_from('<2f', buf.data, origin)
        w, h = struct.unpack_from('<2f', buf.data, size)
        event_ref=event.field(0)
        if not event_ref or not buf.valid(event_ref,8):continue
        actor,image_id=struct.unpack_from('<2I',buf.data,event_ref)
        if actor != 0 or image_id in hidden_image_ids:continue
        edits=image_state.get(image_id,{})
        native_scale=t.field(5)
        sx,sy=(struct.unpack_from('<2f',buf.data,native_scale)
               if native_scale and buf.valid(native_scale,8) else (1.0,1.0))
        if 'position' in edits:x,y=edits['position'][0]
        if 'scale' in edits:sx,sy=edits['scale'][0]
        w,h=w*sx,h*sy
        if (page is None or page > 100_000 or
                not all(math.isfinite(v) for v in (x,y,w,h)) or min(w,h) <= 0):
            continue
        record = dict(page=page, asset=name, x=x, y=y, width=w, height=h,
                      **{'z-index':edits.get('z_index',0)},
                      locked=bool(edits.get('locked',False)),
                      lock_source=edits.get('lock_source','unmarked'),**{'event-id':image_id})
        if t.field(4) and buf.valid(t.field(4),4):
            rotation=buf.f32(t.field(4))
            if math.isfinite(rotation):
                record.update(rotation=math.degrees(rotation),**{'rotation-origin':'1'})
        if crop and buf.valid(crop, 16):
            values = struct.unpack_from('<4f', buf.data, crop)
            if all(math.isfinite(v) for v in values) and min(values[2:]) > 0:
                record.update(zip(('crop-x','crop-y','crop-width','crop-height'), values))
        # Type-22 is an IMAGE placement, not a type-3 page-creation event.
        # A previous page-order fix accidentally copied the page insertion
        # handler here and referenced an undefined ``payload`` variable.
        # Preserve image insertion order; page association was determined
        # from the type-22 placement payload above.
        records.append(record)
    return records


def stroke_position_overrides(events, buf):
    """Type 24 position edits: target op, page reference, absolute origin.

    In the 16.12.5 sample, paired moves/undos restore the exact original
    coordinates. Later records replace earlier positions, not add to them.
    Other transform fields are retained by the native writer unchanged.
    """
    result = {}
    for _, event in events:
        type_pos = event.field(4)
        if not type_pos or buf.data[type_pos] != 24:
            continue
        payload = payload_table(buf, event)
        if not payload:
            continue
        for _, record in table_vector(buf, payload, 0):
            target, page, origin = record.field(0), record.field(1), record.field(2)
            if not all((target, page, origin)) or not buf.valid(target, 8) or not buf.valid(page, 12) or not buf.valid(origin, 8):
                continue
            point = struct.unpack_from('<2f', buf.data, origin)
            if all(math.isfinite(v) for v in point):
                result[buf.u32(target+4)] = (struct.unpack_from('<3I', buf.data, page), point)
    return result


def child_table(buf, table, slot):
    p = table.field(slot) if table else 0
    schema = {('page', 1): 'background', ('background', 1): 'pdf',
              ('pdf', 0): 'asset', ('block', 10): 'image',
              ('image', 0): 'asset'}.get((table.schema, slot)) if table else None
    return table_at(buf, p + buf.u32(p), schema) if p and buf.valid(p, 4) else None


def _place_page_record(records, record, reference, mode):
    """Apply an observed page insertion relative to an existing page ID.

    The newer type-3 page payload's 12-byte reference is (actor, page_id,
    side). For the tested Notability 20 layouts, side=0 appends *after* the
    referenced page, whereas side=1 inserts *before* it. Keep existing page
    order as a safe fallback if a reference or side value is unknown.

    This only constructs an in-memory viewing order; it never modifies the
    source event graph or claims the page writer can rewrite such a document.
    """
    if reference is not None and mode in (0, 1):
        for index, existing in enumerate(records):
            if existing['id'] == reference:
                records.insert(index + (mode == 0), record)
                return
    records.append(record)


def page_records(events, buf, *, resolve=True):
    """Schema-19/20 per-page objects, including blanks and repeated PDF pages.

    Event ID identifies the page; payload slot 0 may contain an insertion
    anchor. Replaying this insertion order is essential for blank pages
    inserted *before* existing handwritten pages. Background slot 1 references
    PDF data; PDF slot 4 is the zero-based source page (omitted for zero).
    """
    records = []
    creations = []
    placements = []
    independent = True
    for _, event in events:
        typ = event.field(4)
        if not typ or buf.data[typ] != 3:
            continue
        p = event.field(0)
        if not p or not buf.valid(p, 8):
            continue
        payload = payload_table(buf, event)
        # A bare type-3 marker is a valid single blank page (schema 20),
        # also accepted by resolve_page_order and validate_page_structure.
        # Only an existing payload can carry an explicit page range count.
        count = field_u32(buf,payload,2) if payload is not None else None
        independent = independent and (count or 1)==1
        background = child_table(buf, payload, 1)
        pdf = child_table(buf, background, 1)
        asset = child_table(buf, pdf, 0)
        record = dict(id=buf.u32(p+4), pdf=None)
        size = background.field(3) if background else 0
        if size and buf.valid(size, 8):
            width, height = struct.unpack_from('<2f', buf.data, size)
            if all(math.isfinite(v) and v > 0 for v in (width, height)):
                record.update(width=width, height=height)
        if pdf and asset:
            hash_pos = asset.field(0)
            if hash_pos and buf.valid(hash_pos, 64):
                record['asset'] = buf.data[hash_pos:hash_pos+64].hex()
                record['pdf'] = field_u32(buf, pdf, 4) or 0
        creations.append(record)
        # Retain the legacy viewer fallback for old, unverified layouts.
        # Valid modern graphs are resolved by identity below.
        anchor = payload.field(0) if payload else 0
        relative_to, side = None, None
        if anchor and buf.valid(anchor, 12):
            actor, candidate, kind = struct.unpack_from('<3I', buf.data, anchor)
            if actor == 0 and kind in (0, 1):
                relative_to, side = candidate, kind
        placements.append((record,relative_to,side))
    if not resolve:return creations
    if independent and creations:
        from formats.ne_page_model import resolve_page_order, PageModelError
        try:
            return resolve_page_order(events,buf,creations)['records']
        except PageModelError:
            pass
    for record,relative_to,side in placements:
        _place_page_record(records,record,relative_to,side)
    return records


def grouped_pdf_page_records(events, buf):
    """Expand PDF page ranges and anchored one-page creators.

    Official CreatePage.pageCount can create several pages from one event.
    A PDF source must consume the same number of pages. Later backgroundless
    CreatePage events are ordinary anchored pages, not extra PDF groups.
    """
    creators=[]
    has_pdf_range=False
    for _,event in events:
        kind_pos=event.field(4)
        if not kind_pos or not buf.valid(kind_pos,1) or buf.data[kind_pos]!=3:
            continue
        payload=payload_table(buf,event)
        if not payload or not event.field(0) or not buf.valid(event.field(0),8):
            return None
        count=field_u32(buf,payload,2) or 1
        if not 1<=count<=100_000:
            return None
        background=child_table(buf,payload,1)
        pdf=child_table(buf,background,1)
        asset=child_table(buf,pdf,0)
        is_pdf=bool(pdf and asset and asset.field(0)
                    and buf.valid(asset.field(0),64))
        if is_pdf:
            consumed=field_u32(buf,pdf,3)
            if (1 if consumed is None else consumed)!=count:
                return None
            has_pdf_range=True
        elif count!=1:
            return None
        elif not payload.field(0) or not buf.valid(payload.field(0),12):
            return None
        creators.append((event,payload,count,background,pdf,asset,is_pdf))
    if not has_pdf_range:
        return None
    records=[]
    for event,payload,count,background,pdf,asset,is_pdf in creators:
        base_id=buf.u32(event.field(0)+4)
        width=height=None
        size=background.field(3) if background else 0
        if size and buf.valid(size,8):
            width,height=struct.unpack_from('<2f',buf.data,size)
            if not (math.isfinite(width) and math.isfinite(height)
                    and width>0 and height>0):
                return None
        asset_hash=None
        pdf_start=0
        if is_pdf:
            hash_pos=asset.field(0)
            if not hash_pos or not buf.valid(hash_pos,64):
                return None
            asset_hash=buf.data[hash_pos:hash_pos+64].hex()
            pdf_start=field_u32(buf,pdf,4) or 0
        for index in range(count):
            record=dict(id=base_id,event_id=base_id,page_index=index,
                        pdf=(pdf_start+index if is_pdf else None))
            if width is not None:record.update(width=width,height=height)
            if asset_hash is not None:record['asset']=asset_hash
            records.append(record)
    from formats.ne_page_model import resolve_page_order, PageModelError
    try:
        return resolve_page_order(events,buf,records)
    except PageModelError:
        return None


def mixed_grouped_pdf_page_records(events, buf):
    """Resolve schema-18 PDF ranges interleaved with explicit blank pages."""
    records=[]
    assets=set()
    for _,event in events:
        kind=event.field(4)
        if not kind or not buf.valid(kind,1) or buf.data[kind]!=3:
            continue
        if not event.field(0) or not buf.valid(event.field(0),8):
            return None
        payload=payload_table(buf,event)
        if payload is None:
            return None
        count=field_u32(buf,payload,2) or 1
        if not 1<=count<=100_000:
            return None
        page_id=buf.u32(event.field(0)+4)
        background=child_table(buf,payload,1)
        pdf=child_table(buf,background,1)
        asset=child_table(buf,pdf,0)
        size=background.field(3) if background else 0
        width=height=None
        if size and buf.valid(size,8):
            width,height=struct.unpack_from('<2f',buf.data,size)
            if not (math.isfinite(width) and math.isfinite(height)
                    and width>0 and height>0):
                return None
        asset_hash=None
        pdf_start=0
        if pdf and asset and asset.field(0) and buf.valid(asset.field(0),64):
            asset_hash=buf.data[asset.field(0):asset.field(0)+64].hex()
            assets.add(asset_hash)
            total_pages=field_u32(buf,pdf,2) or 1
            consumed=field_u32(buf,pdf,3) or 1
            pdf_start=field_u32(buf,pdf,4) or 0
            if consumed!=count or total_pages<pdf_start+count:
                return None
        elif pdf or asset:
            return None
        for index in range(count):
            record=dict(id=page_id,event_id=page_id,page_index=index,
                        pdf=(pdf_start+index if asset_hash is not None else None))
            if width is not None:
                record.update(width=width,height=height)
            if asset_hash is not None:
                record['asset']=asset_hash
            records.append(record)
    if not records or not assets:
        return None
    from formats.ne_page_model import resolve_page_order, PageModelError
    try:
        result=resolve_page_order(events,buf,records)
    except PageModelError:
        return None
    result['assets']=sorted(assets)
    return result


def pdf_display_page_records(events, buf, records):
    """Match the official width-fit PDF viewport; keep ink coordinates intact.

    PageBackground.size is metadata, not the PDF rendering transform. In a
    mixed-size PDF the crop boxes determine each aspect ratio while the
    document settings determine the common coordinate width.
    """
    from core.ne_page_geometry import pdf_page_size
    dimensions=document_page_size(events,buf)
    if not dimensions:
        return records
    crops={}
    for _,event in events:
        if not event.field(4) or buf.data[event.field(4)]!=3:
            continue
        payload=payload_table(buf,event)
        bg=child_table(buf,payload,1)
        pdf=child_table(buf,bg,1)
        field=pdf.field(5) if pdf else 0
        if not field or not buf.valid(field,4):continue
        vector=field+buf.u32(field)
        if not buf.valid(vector,4):continue
        count=buf.u32(vector)
        if not 0<count<=100000 or not buf.valid(vector+4,count*8):continue
        page_id=buf.u32(event.field(0)+4)
        crops[page_id]=[struct.unpack_from('<2f',buf.data,vector+4+i*8)
                        for i in range(count)]
    output=[]
    for record in records:
        item=dict(record)
        values=crops.get(record.get('event_id',record['id']))
        if (record.get('pdf') is not None and values
                and abs(record.get('width',dimensions[0])-dimensions[0])>.01):
            index=min(record.get('page_index',0),len(values)-1)
            try:
                width,height=pdf_page_size(dimensions[0],*values[index])
                item.update(width=width,height=height)
            except ValueError:
                pass
        elif record.get('pdf') is None and 'width' not in item:
            item.update(width=dimensions[0],height=dimensions[1])
        output.append(item)
    return output
