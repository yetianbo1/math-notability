"""Create new native-paper NTB notes and show the creation dialog."""
from __future__ import annotations

from dataclasses import dataclass
import os
import json
import math
import struct
import time
import uuid
from zipfile import ZipFile, ZIP_DEFLATED
from pathlib import Path
import tempfile

from core.ne_paper_patterns import dotted_centres, ruled_segments


@dataclass(frozen=True)
class PaperPreset:
    key: str
    label: str
    width: float
    height: float


PAPER_PRESETS = (
    PaperPreset('a4', 'A4（210 × 297 mm）', 595.27649, 841.89105),
    PaperPreset('envelope', 'US Letter 信纸（216 × 279 mm）', 612., 792.),
    PaperPreset('a5', 'A5（148 × 210 mm）', 419.52756, 595.27559),
    PaperPreset('b5', 'B5 ISO（176 × 250 mm）', 498.89764, 708.66142),
    PaperPreset('legal', 'US Legal（216 × 356 mm）', 612., 1008.),
    PaperPreset('a3', 'A3（297 × 420 mm）', 841.88976, 1190.55118),
    PaperPreset('dl', 'DL 信封（110 × 220 mm）', 311.81102, 623.62205),
    PaperPreset('c5', 'C5 信封（162 × 229 mm）', 459.21260, 649.13386),
)

BACKGROUNDS = (
    ('blank', '空白'),
    ('ruled', '横线'),
    ('dotted', '点状'),
)


def paper_preset(key):
    for preset in PAPER_PRESETS:
        if preset.key == key:
            return preset
    raise ValueError('未知的纸张尺寸')


def paper_color(value):
    import re
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('底页颜色必须是 #RRGGBB 格式')
    return value.lower()


def _native_paper_bundle(title, preset, background, color='#ffffff'):
    """Schema-20 paper, following the official compact metadata history."""
    from formats.ne_native import Builder
    builder = Builder()
    builder.data = bytearray(4)
    now = struct.pack('<Q', int(time.time()*1000))
    root, fields = builder.table(8, {
        0: (uuid.uuid4().bytes, 8), 3: (bytes(4), 4), 4: (now, 8),
        5: (bytes(4), 4), 6: (bytes(4), 4), 7: (struct.pack('<H', 20), 2),
    })
    struct.pack_into('<I', builder.data, 0, root)
    namespace = ('_'+uuid.uuid4().hex).encode('ascii')
    namespace_at = builder.vector(namespace+b'\0', len(namespace))
    for slot in (3, 5):builder.uoffset(fields[slot], namespace_at)
    vector = builder.vector(bytes(16), 4)
    builder.uoffset(fields[6], vector)
    payload_fields = []
    for sequence, kind in enumerate((1, 3, 1, 1)):
        event, event_fields = builder.table(6, {
            0: (struct.pack('<II', 0, sequence), 4),
            1: (now, 8), 2: (bytes(8), 8),
            4: (bytes((kind,)), 1), 5: (bytes(4), 4),
        })
        builder.uoffset(vector+4+sequence*4, event)
        payload_fields.append(event_fields[5])

    def string(value):
        encoded = value.encode('utf-8')
        return builder.vector(encoded+b'\0', len(encoded))

    def metadata(target, *, size=None, style='blank', layout=None, name=None):
        values = {}
        if name is not None:
            values[0] = (bytes(4), 4)
        if size is not None:
            values[1] = (bytes(4), 4)
        if layout is not None:
            values[6] = (bytes((layout,)), 1)
        table, slots = builder.table(11, values)
        builder.uoffset(target, table)
        if name is not None:
            wrapper, wrapped = builder.table(1, {0: (bytes(4), 4)})
            builder.uoffset(slots[0], wrapper)
            builder.uoffset(wrapped[0], string(name))
        if size is not None:
            wrapper, wrapped = builder.table(1, {0: (bytes(4), 4)})
            builder.uoffset(slots[1], wrapper)
            margins = (36.,)*4 if style == 'blank' else (0., 0., 36., 36.)
            page, page_fields = builder.table(5, {
                0: (bytes(4), 4), 3: (struct.pack('<ff', *size), 4),
                4: (struct.pack('<ffff', *margins), 4),
            })
            builder.uoffset(wrapped[0], page)
            # Use the modern Paper fields alone. legacyPaperIndex is nullable;
            # even an explicit zero is a legacy value, not an absent setting.
            paper_fields = {4: (bytes.fromhex(paper_color(color)[1:])+b'\xff', 4)}
            if style != 'blank':
                paper_fields.update({
                    0: (bytes((0 if style == 'ruled' else 1,)), 1),
                    1: (struct.pack('<f', 36.), 4),
                    2: (b'\0', 1), 3: (b'\0', 1),
                })
            paper, _ = builder.table(6, paper_fields)
            builder.uoffset(page_fields[0], paper)

    metadata(payload_fields[0], size=(612., 792.), layout=1, name=title)
    create, _ = builder.table(4, {2: (struct.pack('<I', 2), 4)})
    builder.uoffset(payload_fields[1], create)
    metadata(payload_fields[2], layout=0)
    metadata(payload_fields[3], size=(preset.width, preset.height), style=background)
    return bytes(builder.data)


def create_newnote_ntb(target, preset, background, *, color='#ffffff', overwrite=False,
                       progress=lambda _value:None,
                       cancelled=lambda:False):
    """Create an atomic NTB with native paper; no PDF or ink background."""
    target = Path(target).resolve()
    if target.suffix.lower() != '.ntb':
        raise ValueError('新建笔记必须保存为 .ntb 文件')
    if not target.parent.is_dir():
        raise FileNotFoundError('保存目录不存在')
    if target.exists() and not overwrite:
        raise FileExistsError('目标 NTB 已存在')
    if background not in dict(BACKGROUNDS):
        raise ValueError('未知的底页类型')
    if not all(math.isfinite(v) and 0 < v <= 10000
               for v in (preset.width, preset.height)):
        raise ValueError('无效的纸张尺寸')
    if cancelled():
        raise InterruptedError('新建笔记已取消')
    progress(2)
    color = paper_color(color)
    bundle = _native_paper_bundle(target.stem, preset, background, color)
    with tempfile.TemporaryDirectory(
            prefix='.nv-new-note-build-',dir=target.parent) as folder:
        staged = Path(folder)/target.name
        import fitz
        thumbnail = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1), False)
        thumbnail.set_pixel(0, 0, tuple(bytes.fromhex(color[1:])))
        with ZipFile(staged, 'w', compression=ZIP_DEFLATED) as archive:
            archive.writestr('version', b'1')
            archive.writestr('noteBundle', bundle)
            archive.writestr('manifest.json', json.dumps({'appVersion': 'NotabilityViewer'}))
            archive.writestr('thumbnail.png', thumbnail.tobytes('png'))
        progress(85)
        from formats.ne_native import read_structure
        from formats.ne_bundle_identity import bundle_namespaces
        from formats.ne_ntb import document_page_size, document_paper_template
        with ZipFile(staged) as archive:
            if archive.testzip() is not None:
                raise ValueError('新建笔记 ZIP 校验失败')
            saved_bundle = archive.read('noteBundle')
            bundle_namespaces(saved_bundle)
            buf, _, events = read_structure(saved_bundle)
            size = document_page_size(events, buf)
            template = document_paper_template(events, buf)
            if (size is None or template is None or template['kind'] != background
                    or template.get('background','#ffffff') != color
                    or any(abs(a-b) > .02 for a,b in zip(size,(preset.width,preset.height)))):
                raise ValueError('新建笔记纸张元数据校验失败')
        if cancelled():
            raise InterruptedError('新建笔记已取消')
        with open(staged, 'r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, target)
    progress(100)
    return target


class NewNoteDialog:
    """Qt dialog kept behind a local import so the writer stays Qt-free."""
    def __init__(self, owner=None):
        from PyQt6.QtCore import QSize, Qt
        from PyQt6.QtGui import QIcon, QImage, QPainter, QColor, QPen
        from PyQt6.QtWidgets import (
            QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
            QLabel, QToolButton, QVBoxLayout, QWidget,
        )
        self._Qt = Qt
        self._QImage = QImage
        self._QPainter = QPainter
        self._QColor = QColor
        self._QPen = QPen
        self.paper_color = '#ffffff'
        self.dialog = QDialog(owner)
        self.dialog.setWindowTitle('新建笔记')
        self.dialog.setMinimumWidth(620)
        layout = QVBoxLayout(self.dialog)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        heading = QLabel('选择纸张与底页')
        heading.setStyleSheet('font-size:18px;font-weight:600;color:#0f172a;')
        layout.addWidget(heading)
        form = QFormLayout()
        self.size_combo = QComboBox()
        for preset in PAPER_PRESETS:
            self.size_combo.addItem(preset.label, preset.key)
        form.addRow('纸张大小', self.size_combo)
        layout.addLayout(form)
        label = QLabel('底页类型')
        label.setStyleSheet('font-weight:600;color:#334155;')
        layout.addWidget(label)
        choices = QHBoxLayout()
        self.group = QButtonGroup(self.dialog)
        self.buttons = {}
        for key, text in BACKGROUNDS:
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(72, 92))
            choices.addWidget(button, 1)
            self.group.addButton(button)
            self.buttons[key] = button
        self.buttons['blank'].setChecked(True)
        layout.addLayout(choices)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(360)
        self.preview.setStyleSheet(
            'background:#eef2f7;border:1px solid #d7dee8;'
            'border-radius:8px;padding:12px;')
        layout.addWidget(self.preview)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel('底页颜色'))
        for label, rgb in (('白色','#ffffff'),('暖白','#faf7ef'),('浅黄','#fff5bd'),
                           ('浅蓝','#dfeef3'),('浅绿','#e7f1e8'),('深灰','#25282e')):
            button = QToolButton()
            button.setText(label); button.setAccessibleName('底页颜色：'+label)
            button.setStyleSheet('background:'+rgb+';color:'+('#ffffff' if rgb=='#25282e' else '#334155')+';padding:6px;')
            button.clicked.connect(lambda checked=False, c=rgb:self.set_paper_color(c))
            color_row.addWidget(button)
        self.color_button = QToolButton()
        self.color_button.setAccessibleName('自定义底页颜色')
        self.color_button.clicked.connect(self.choose_paper_color)
        color_row.addWidget(self.color_button)
        layout.addLayout(color_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('创建笔记')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.dialog.accept)
        buttons.rejected.connect(self.dialog.reject)
        layout.addWidget(buttons)
        self.size_combo.currentIndexChanged.connect(self.refresh)
        self.group.buttonToggled.connect(lambda *_args:self.refresh())
        self.refresh()

    def background(self):
        for key, button in self.buttons.items():
            if button.isChecked():
                return key
        return 'blank'

    def preset(self):
        return paper_preset(self.size_combo.currentData())

    def refresh(self):
        self.color_button.setText('自定义 '+self.paper_color.upper())
        preset = self.preset()
        background = self.background()
        for key, button in self.buttons.items():
            button.setIcon(self._icon(preset, key, 72, 92))
        width = 310
        height = max(1, round(width*preset.height/preset.width))
        if height > 315:
            height = 315
            width = max(1, round(height*preset.width/preset.height))
        image = self._preview_image(preset, background, width, height)
        from PyQt6.QtGui import QPixmap
        self.preview.setPixmap(QPixmap.fromImage(image))

    def _preview_image(self, preset, background, width, height):
        image = self._QImage(width, height, self._QImage.Format.Format_ARGB32)
        image.fill(self._QColor(self.paper_color))
        painter = self._QPainter(image)
        painter.setRenderHint(self._QPainter.RenderHint.Antialiasing, True)
        pen = self._QPen(self._QColor('#a3b7d3'))
        pen.setWidthF(.7)
        painter.setPen(pen)
        sx = width/preset.width
        sy = height/preset.height
        if background == 'ruled':
            for x0, y0, x1, y1 in ruled_segments(
                    preset.width, preset.height, 36., 36.):
                painter.drawLine(
                    round(x0*sx), round(y0*sy),
                    round(x1*sx), round(y1*sy))
        elif background == 'dotted':
            painter.setPen(self._Qt.PenStyle.NoPen)
            painter.setBrush(self._QColor('#a3b7d3'))
            for x, y in dotted_centres(preset.width, preset.height, 36.):
                painter.drawEllipse(
                    round(x*sx)-1, round(y*sy)-1, 2, 2)
        painter.end()
        return image

    def _icon(self, preset, background, width, height):
        from PyQt6.QtGui import QIcon, QPixmap
        return QIcon(QPixmap.fromImage(
            self._preview_image(preset, background, width, height)))

    def values(self):
        return self.preset(), self.background()

    def set_paper_color(self, color):
        self.paper_color = paper_color(color)
        self.refresh()

    def choose_paper_color(self):
        from PyQt6.QtWidgets import QColorDialog
        color = QColorDialog.getColor(self._QColor(self.paper_color), self.dialog, '自定义底页颜色')
        if color.isValid():
            self.set_paper_color(color.name())

    def exec(self):
        return self.dialog.exec()
