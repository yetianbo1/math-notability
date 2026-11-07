"""Read legacy keyed NOTE archives into the viewer's paginated scene.

Only objects reachable from the session are displayed. Reference PDFs beside
the input are deliberately never read: all content comes from the NOTE itself.
"""
import json
import math
import plistlib
import re
import struct
from pathlib import PurePosixPath
from lxml import etree


class KeyedArchive:
    def __init__(self, archive):
        self.objects = archive['$objects']
        self.top = archive['$top']
        self.cache = {}
        self.active = set()

    def decode(self, value):
        if isinstance(value, plistlib.UID):
            index = value.data
            if index == 0:
                return None
            if not 0 <= index < len(self.objects):
                raise ValueError('NOTE 对象引用越界')
            if index in self.active:
                raise ValueError('NOTE 对象引用循环')
            if index not in self.cache:
                self.active.add(index)
                try:
                    self.cache[index] = self.decode(self.objects[index])
                finally:
                    self.active.remove(index)
            return self.cache[index]
        if isinstance(value, list):
            return [self.decode(v) for v in value]
        if isinstance(value, dict):
            if 'NS.keys' in value:
                keys, values = value['NS.keys'], value['NS.objects']
                if len(keys) != len(values):
                    raise ValueError('NOTE 字典长度不一致')
                return {self.decode(k): self.decode(v) for k, v in zip(keys, values)}
            if 'NS.objects' in value:
                return self.decode(value['NS.objects'])
            result = {k: self.decode(v) for k, v in value.items() if k != '$class'}
            if '$class' in value:
                result['_class'] = self.decode(value['$class']).get('$classname', '')
            return result
        return value

    def session(self):
        return self.decode(next(iter(self.top.values())))


def numbers(value, count):
    values = tuple(float(v) for v in re.findall(r'[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?', str(value)))
    if len(values) != count or not all(map(math.isfinite, values)):
        raise ValueError('NOTE 坐标或尺寸无效')
    return values


def unpack(data, code):
    size = struct.calcsize('<' + code)
    if not isinstance(data, bytes) or len(data) % size:
        raise ValueError('NOTE 笔迹数组长度无效')
    return struct.unpack('<' + str(len(data) // size) + code, data)


def asset_name(archive, base, relative):
    """Resolve only archive members, including older renamed package roots."""
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('NOTE 素材路径无效')
    name = str(PurePosixPath(base) / rel)
    if name in archive.namelist():
        return name
    matches = [n for n in archive.namelist() if n.endswith('/' + str(rel))]
    if len(matches) != 1:
        raise ValueError('NOTE 素材缺失或不唯一：' + str(rel))
    return matches[0]


def paper_geometry(rich, paper):
    attributes = paper.get('documentPaperAttributes') or {}
    width = float((rich.get('reflowState') or {}).get('pageWidthInDocumentCoordsKey',
                                                                  rich.get('pageWidthInDocumentCoordsKey', 567)))
    sizing = attributes.get('paperSizingBehavior', '')
    # Legacy locked-width iPad paper uses 21:16, including its export padding.
    # This is also the 567 x 744.1875 geometry preserved in migrated NTB files.
    if sizing.startswith('lockedWidth:') or attributes.get('paperSize', 'letter') == 'letter':
        height = width * 21 / 16
    elif attributes.get('paperSize') == 'a4':
        height = width * 840 / 593.3302612304688
    else:
        raise ValueError('尚未支持的 NOTE 纸张尺寸：' + str(attributes.get('paperSize')))
    if not all(math.isfinite(v) and 0 < v <= 10000 for v in (width, height)):
        raise ValueError('NOTE 页面尺寸无效')
    return width, height


def read_note(archive, session_entry, report=lambda value, message: None):
    """Return SVG, page dimensions, and ordered embedded PDF member names."""
    session = KeyedArchive(plistlib.loads(archive.read(session_entry))).session()
    rich = session.get('richText') or {}
    paper = session.get('NBNoteTakingSessionDocumentPaperLayoutModelKey') or {}
    width, height = paper_geometry(rich, paper)
    base = session_entry.rsplit('/', 1)[0] if '/' in session_entry else ''
    ink = (rich.get('Handwriting Overlay') or {}).get('SpatialHash') or {}
    counts = unpack(ink.get('curvesnumpoints', b''), 'i')
    points = unpack(ink.get('curvespoints', b''), 'f')
    widths = unpack(ink.get('curveswidth', b''), 'f')
    colors = ink.get('curvescolors', b'')
    styles = ink.get('curvesstyles', b'')
    if (any(n < 0 for n in counts) or sum(counts) * 2 != len(points)
            or len(widths) != len(counts) or len(colors) != len(counts) * 4
            or not all(map(math.isfinite, points))
            or not all(math.isfinite(v) and v >= 0 for v in widths)):
        raise ValueError('NOTE 笔迹数据不完整或无效')
    # The experimental exporter uses int32 styles; official v9 uses uint8.
    if len(styles) == len(counts) * 4:
        styles = unpack(styles, 'i')
    media = rich.get('mediaObjects') or []
    extent = max(points[1::2], default=0)
    for obj in media:
        _, y = numbers(obj.get('documentContentOrigin', obj.get('documentOrigin')), 2)
        w, h = numbers(obj.get('unscaledContentSize'), 2)
        if w <= 0 or h <= 0:
            raise ValueError('NOTE 媒体尺寸无效')
        extent = max(extent, y + h)
    layout = paper.get('pageLayoutArray') or rich.get('pageLayoutArray') or []
    layout_pages = [int(p['kPageLayoutDocumentPageNumberKey']) for p in layout]
    if any(page < 1 for page in layout_pages):
        raise ValueError('NOTE 页面布局编号无效')
    # When a current page layout is present it is authoritative. Notability
    # retains deleted-page media in mediaObjects for history/undo; using the
    # furthest retained object as a page count resurrects those old pages.
    count = max(1, max(layout_pages) if layout_pages else math.ceil(extent / height))
    if count > 10000:
        raise ValueError('NOTE 页面过多')
    pages = [dict(width=width, height=height, pdf=None) for _ in range(count)]
    pdf_names = []
    pdf_offsets = {}
    import pymupdf
    for record in layout:
        page = int(record['kPageLayoutDocumentPageNumberKey']) - 1
        filename = record.get('kPageLayoutPDFFileNameKey')
        if not filename:
            continue
        member = asset_name(archive, base, 'PDFs/' + filename)
        if member not in pdf_offsets:
            with pymupdf.open(stream=archive.read(member), filetype='pdf') as doc:
                pdf_offsets[member] = (sum(n for _, n in pdf_names), len(doc))
                pdf_names.append((member, len(doc)))
        offset, pdf_count = pdf_offsets[member]
        index = int(record['kPageLayoutPDFPageNumberKey']) - 1
        if not 0 <= index < pdf_count:
            raise ValueError('NOTE 内嵌 PDF 页码越界')
        pages[page]['pdf'] = offset + index
    root = etree.Element('svg', xmlns='http://www.w3.org/2000/svg',
                         width=str(width), height=str(height * count),
                         viewBox=f'0 0 {width} {height * count}')
    root.set('data-page-height', str(height))
    root.set('data-page-count', str(count))
    root.set('data-pages', json.dumps(pages, separators=(',', ':')))
    root.set('data-note-format', 'keyed-v9')
    attributes = paper.get('documentPaperAttributes') or {}
    line_style = attributes.get('lineStyle2', 'No Lines')
    if line_style == 'Lines:false:false:0.5':
        root.set('data-paper-template', json.dumps(dict(kind='ruled', pitch=width / 17,
                                                       margin=width * 5 / 192, color='#a3b7d3')))
    z_ink = {}
    for obj in media:
        for index in obj.get('handwritingZIndecesKey', []):
            z_ink[index] = max(z_ink.get(index, 0), int(obj.get('zIndex', 0)) + 1)
    start = 0
    ink_x_offset = width * 5 / 192
    for index, length in enumerate(counts):
        if index % 500 == 0:
            report(10 + int(45 * index / max(1, len(counts))), '正在恢复 NOTE 笔迹…')
        xy = points[start * 2:(start + length) * 2]
        start += length
        if not length:
            continue
        page = int(xy[1] // height)
        if not 0 <= page < count:
            continue
        rgba = colors[index * 4:index * 4 + 4]
        path = etree.SubElement(root, 'path', fill='none',
                                stroke='#' + rgba[:3].hex())
        path.set('d', 'M' + ' L'.join(
            f'{x + ink_x_offset:.5f} {y:.5f}'
            for x, y in zip(xy[::2], xy[1::2])))
        path.set('stroke-width', str(widths[index]))
        path.set('stroke-opacity', str(rgba[3] / 255))
        path.set('stroke-linecap', 'round')
        path.set('stroke-linejoin', 'round')
        path.set('data-page', str(page))
        path.set('data-event-id', str(index))
        path.set('data-z-index', str(z_ink.get(index, 1)))
        # NOTE highlighters still overlay image backgrounds; use their archived
        # alpha and z order instead of NTB's global behind-all-content layer.
        if index < len(styles) and styles[index] == 4:
            path.set('data-note-highlighter', '1')
    next_id = len(counts)
    for index, obj in enumerate(media):
        report(55 + int(35 * index / max(1, len(media))), '正在恢复 NOTE 图片与文字…')
        x, y = numbers(obj.get('documentContentOrigin', obj.get('documentOrigin')), 2)
        # Legacy media origins are relative to the text area's left inset;
        # handwriting already uses the full page coordinate system. Confirmed
        # against both legacy NOTE/PDF pairs and their migrated NTB geometry.
        x += width * 5 / 192
        w, h = numbers(obj['unscaledContentSize'], 2)
        first = max(0, int(y // height))
        last = int((y + h - 1e-7) // height)
        if first >= count or last < 0:
            continue
        last = min(count - 1, last)
        if obj.get('_class') == 'ImageMediaObject':
            figure = obj['figure']
            snapshot = figure['FigureBackgroundObjectKey']['kImageObjectSnapshotKey']
            asset = asset_name(archive, base, snapshot['relativePath'])
            crop = numbers(figure['FigureCropRectKey'], 4)
            for page in range(first, last + 1):
                record = etree.SubElement(root, 'image')
                values = dict(page=page, asset=asset, x=x, y=y-page*height, width=w, height=h,
                              **{'event-id': next_id, 'z-index': int(obj.get('zIndex', 0)),
                                 'note-image': '1',
                                 'locked': 'true', 'crop-x': crop[0], 'crop-y': crop[1],
                                 'crop-width': crop[2], 'crop-height': crop[3],
                                 'rotation': float(obj.get('rotationDegrees', 0)),
                                 'flip-x': int(bool(obj.get('isFlippedHorizontal'))),
                                 'flip-y': int(bool(obj.get('isFlippedVertical')))})
                for key, value in values.items():
                    record.set('data-' + key, str(value))
                next_id += 1
        elif obj.get('_class') == 'TextBlockMediaObject':
            text = obj['textStore'].get('attributedString') or {}
            content = text.get('stringKey', '')
            if isinstance(content, dict):
                content = content.get('NS.bytes', b'').decode('utf-8')
            runs = text.get('subRangesKey') or [{}]
            font = runs[0].get('subRangeFontKey') or {}
            for page in range(first, last + 1):
                record = dict(page=page, event_id=next_id, z_index=int(obj.get('zIndex', 0)),
                              x=x, y=y-page*height, width=w, height=h, text=content,
                              font_size=font.get('NSFontSizeAttribute', 15),
                              font_family=font.get('NSFontNameAttribute', 'Arial'), margins=(0, 0, 0, 0))
                el = etree.SubElement(root, 'text')
                el.set('data-native-text', json.dumps(record))
                next_id += 1
        else:
            raise ValueError('尚未支持的 NOTE 媒体对象：' + str(obj.get('_class')))
    return root, width, height, [name for name, _ in pdf_names]
