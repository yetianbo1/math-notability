"""Viewer-private storage for question cards.

Question cards live in a dedicated ZIP prefix inside the NTB.  The official
Notability structure (noteBundle and its events) is never touched: write_ntb
copies unknown members verbatim, so these entries travel with the notebook and
are simply ignored by the official app.  That is the whole compatibility
contract for this feature.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
from copy import copy
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

PREFIX = 'NotabilityViewer/question-cards/'
MANIFEST = PREFIX + 'manifest.json'
VERSION = 1
MAX_CARDS = 100
MAX_ASSET = 12 * 1024 * 1024
MAX_STROKES = 4000
MAX_POINTS = 20000
_FIELDS = frozenset(('id', 'number', 'page', 'asset', 'image_width', 'image_height',
                     'rect', 'strokes', 'geometry', 'zoom_bias'))


def _finite(value, low=-1e7, high=1e7):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def encode_strokes(strokes):
    """Plain-JSON representation of editable ink; keeps shape/linear flags."""
    result = []
    for stroke in strokes:
        result.append({
            'points': [[float(x), float(y)] for x, y in stroke.points],
            'color': str(stroke.color),
            'width': float(stroke.width),
            'opacity': float(stroke.opacity),
            'tool': str(stroke.tool),
            'smoothed': bool(stroke.geometry_is_smoothed),
            'linear': bool(stroke.geometry_is_linear),
            'outline': ([[float(x), float(y)] for x, y in stroke.outline]
                        if stroke.outline is not None else None),
            'tape_pattern': int(stroke.tape_pattern),
            'smart': bool(stroke.smart_interpolation),
        })
    return result


def _validate_ink(items):
    """Pure-JSON validation; safe to call from the background save thread."""
    if not isinstance(items, list) or len(items) > MAX_STROKES:
        raise ValueError('题目卡草稿笔迹数量异常')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('题目卡草稿笔迹格式错误')
        points = item.get('points')
        if not isinstance(points, list) or not points or len(points) > MAX_POINTS:
            raise ValueError('题目卡草稿笔迹点数异常')
        for point in points:
            if not isinstance(point, list) or len(point) != 2 or not all(_finite(v) for v in point):
                raise ValueError('题目卡草稿笔迹坐标异常')
        outline = item.get('outline')
        if outline is not None:
            if not isinstance(outline, list) or len(outline) > MAX_POINTS:
                raise ValueError('题目卡草稿轮廓异常')
            for point in outline:
                if not isinstance(point, list) or len(point) != 2 or not all(_finite(v) for v in point):
                    raise ValueError('题目卡草稿轮廓坐标异常')
        if not _finite(item.get('width'), 0, 1e5) or not _finite(item.get('opacity'), 0, 1):
            raise ValueError('题目卡草稿笔宽或透明度异常')


def decode_strokes(items):
    from editing.ne_edits import Stroke
    _validate_ink(items)
    strokes = []
    for item in items:
        outline = item.get('outline')
        strokes.append(Stroke(
            points=tuple(map(tuple, item['points'])),
            color=str(item.get('color', '#202124')),
            width=float(item['width']),
            opacity=float(item.get('opacity', 1.0)),
            source_id=None,
            tool=str(item.get('tool', 'pen')),
            geometry_is_smoothed=bool(item.get('smoothed')),
            geometry_is_linear=bool(item.get('linear')),
            outline=(tuple(map(tuple, outline)) if outline is not None else None),
            tape_pattern=int(item.get('tape_pattern', 3)),
            smart_interpolation=bool(item.get('smart')),
        ))
    return strokes


def _validate_card(card):
    if not isinstance(card, dict) or set(card) != _FIELDS:
        raise ValueError('题目卡记录字段不符合规范')
    if not isinstance(card['id'], str) or not 1 <= len(card['id']) <= 64:
        raise ValueError('题目卡编号不合法')
    if not isinstance(card['number'], int) or not 1 <= card['number'] <= 10000:
        raise ValueError('题目卡序号不合法')
    if not isinstance(card['page'], int) or not 0 <= card['page'] < 10000:
        raise ValueError('题目卡页码不合法')
    asset = card['asset']
    if (not isinstance(asset, str) or len(asset) != 64
            or any(ch not in '0123456789abcdef' for ch in asset)):
        raise ValueError('题目卡截图哈希不合法')
    for key in ('image_width', 'image_height'):
        if not isinstance(card[key], int) or not 1 <= card[key] <= 20000:
            raise ValueError('题目卡截图尺寸不合法')
    rect = card['rect']
    if (not isinstance(rect, list) or len(rect) != 4
            or not all(_finite(v) for v in rect) or rect[2] <= 0 or rect[3] <= 0):
        raise ValueError('题目卡圈选区域不合法')
    geometry = card['geometry']
    if (not isinstance(geometry, list) or len(geometry) != 4
            or not all(_finite(v) for v in geometry)):
        raise ValueError('题目卡窗口位置不合法')
    if not _finite(card['zoom_bias'], 0.2, 4.0):
        raise ValueError('题目卡缩放倍率不合法')
    # Validate ink without importing Qt-heavy modules at read/save time.
    _validate_ink(card['strokes'])


def _validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {'version', 'cards', 'assets'}:
        raise ValueError('题目卡数据包格式错误')
    if payload['version'] != VERSION or not isinstance(payload['cards'], list):
        raise ValueError('题目卡数据版本不支持')
    if len(payload['cards']) > MAX_CARDS:
        raise ValueError('题目卡数量超过上限')
    assets = payload['assets']
    if not isinstance(assets, dict):
        raise ValueError('题目卡截图素材格式错误')
    referenced = set()
    seen = set()
    for card in payload['cards']:
        _validate_card(card)
        if card['id'] in seen:
            raise ValueError('题目卡编号重复')
        seen.add(card['id'])
        referenced.add(card['asset'])
    if set(assets) != referenced:
        raise ValueError('题目卡截图素材与清单不匹配')
    for digest, blob in assets.items():
        if (not isinstance(blob, bytes) or len(blob) > MAX_ASSET
                or hashlib.sha256(blob).hexdigest() != digest):
            raise ValueError('题目卡截图素材校验失败')
        if len(blob) < 24 or blob[:8] != b'\x89PNG\r\n\x1a\n' or blob[12:16] != b'IHDR':
            raise ValueError('题目卡截图不是有效的 PNG 文件')
        width, height = struct.unpack_from('>II', blob, 16)
        if not width or not height or width * height > 16_000_000:
            raise ValueError('题目卡截图尺寸超出安全限制')
    return referenced


def read_cards(path):
    """Return ``(cards, assets)`` from an NTB; empty when no cards are stored."""
    with ZipFile(path) as archive:
        names = archive.namelist()
        if MANIFEST not in names:
            return [], {}
        if archive.getinfo(MANIFEST).file_size > 2 * 1024 * 1024:
            raise ValueError('题目卡清单异常庞大')
        obj = json.loads(archive.read(MANIFEST))
        if (not isinstance(obj, dict) or obj.get('version') != VERSION
                or not isinstance(obj.get('cards'), list)):
            raise ValueError('题目卡清单格式错误')
        cards = obj['cards']
        if len(cards) > MAX_CARDS:
            raise ValueError('题目卡数量超过上限')
        assets = {}
        for card in cards:
            _validate_card(card)
            digest = card['asset']
            if digest in assets:
                continue
            name = PREFIX + digest + '.png'
            if name not in names or archive.getinfo(name).file_size > MAX_ASSET:
                raise ValueError('题目卡截图缺失或超出限制')
            blob = archive.read(name)
            if hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError('题目卡截图 SHA-256 校验失败')
            assets[digest] = blob
        return cards, assets


def write_cards(stage, payload):
    """Replace every card member inside an already staged NTB, atomically.

    Called on the private scratch file produced by write_ntb, before the final
    commit, so a failure never reaches the user's notebook.
    """
    stage = Path(stage)
    _validate_payload(payload)
    cards = payload['cards']
    assets = payload['assets']
    manifest = json.dumps({'version': VERSION, 'cards': cards},
                          ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    fd, temp = tempfile.mkstemp(prefix='.ne-cards-', suffix='.tmp', dir=stage.parent)
    os.close(fd)
    try:
        with ZipFile(stage) as old, ZipFile(temp, 'w', allowZip64=True) as output:
            if old.testzip() is not None:
                raise ValueError('暂存 NTB ZIP 校验失败')
            output.comment = old.comment
            for info in old.infolist():
                if info.filename.startswith(PREFIX):
                    continue
                with old.open(info) as src, output.open(copy(info), 'w', force_zip64=True) as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
            if cards:
                output.writestr(MANIFEST, manifest, compress_type=ZIP_DEFLATED)
                for digest, blob in sorted(assets.items()):
                    output.writestr(PREFIX + digest + '.png', blob,
                                    compress_type=ZIP_DEFLATED)
        with ZipFile(temp) as check:
            if check.testzip() is not None or 'noteBundle' not in check.namelist():
                raise ValueError('写入题目卡后的 NTB 校验失败')
            if cards:
                stored = json.loads(check.read(MANIFEST))
                if stored['cards'] != cards:
                    raise ValueError('题目卡清单写入验证失败')
        os.replace(temp, stage)
        temp = None
    finally:
        if temp is not None:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
