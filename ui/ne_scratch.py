"""Disposable floating scratch pad.

The pad owns an in-memory single-page canvas.  Nothing is ever written to the
current notebook, to an NTB, or to disk until the user explicitly asks to pin
the ink onto a page.  Closing the window discards the drawing.
"""
from __future__ import annotations

from PyQt6.QtCore import (Qt, QBuffer, QByteArray, QEvent, QIODevice, QPoint,
                          QTimer)
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QMessageBox, QToolButton,
                             QVBoxLayout, QWidget)

from editing.ne_edits import paint_strokes

PAGE_WIDTH = 1000.0
PAGE_HEIGHT = 700.0
RENDER_SCALE = 2
MIN_ZOOM, MAX_ZOOM = 0.3, 2.5
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1060, 790

# A blank viewer-local page: no PDF, no archive, no native events.
_SCRATCH_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" data-page-count="1" data-local-ink="1"/>'
# Tools that make no sense on a scratch sheet fall back to the pen.  Everything
# else (pen, highlighter, eraser, select, shape) follows the main window.
_ALLOWED_TOOLS = frozenset(('draw', 'highlight', 'erase', 'select', 'shape'))


def make_canvas():
    """Build a canvas that lives only in memory; no file is ever created."""
    from rendering.ne_pdfview import PdfNoteCanvas
    return PdfNoteCanvas(None, _SCRATCH_SVG, PAGE_WIDTH, PAGE_HEIGHT)


def render_ink_png(canvas):
    """Return ``(png_bytes, width_px, height_px)`` or None when blank.

    Only the ink is drawn, on a fully transparent background, so a pinned
    scratch note never hides the question it is pasted next to.
    """
    strokes = canvas.edits.state(0)[0]
    if not strokes:
        return None
    boxes = [stroke.bounds for stroke in strokes]
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    pad = max(8.0, max(float(stroke.width) for stroke in strokes))
    left = max(0.0, left - pad)
    top = max(0.0, top - pad)
    right = min(PAGE_WIDTH, right + pad)
    bottom = min(PAGE_HEIGHT, bottom + pad)
    width = max(1, int(round((right - left) * RENDER_SCALE)))
    height = max(1, int(round((bottom - top) * RENDER_SCALE)))
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    try:
        painter.scale(RENDER_SCALE, RENDER_SCALE)
        painter.translate(-left, -top)
        paint_strokes(painter, strokes, smooth=False)
    finally:
        painter.end()
    raw = bytes(image.constBits().asstring(image.sizeInBytes()))
    if not any(raw[3::4]):
        return None
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return None
    ok = image.save(buffer, 'PNG')
    buffer.close()
    if not ok:
        return None
    return bytes(payload), width, height


def sync_canvas_settings(owner, canvas):
    """Mirror the main window's pen/tool settings onto a viewer-local canvas.

    Shared by the scratch pad and the question cards so both always behave the
    same way when the user changes tools, colours or widths in the main window.
    """
    canvas.pen_color = getattr(owner, 'current_color', canvas.pen_color)
    if hasattr(owner, 'pen_size'):
        canvas.pen_width = owner.pen_size.value()
    if hasattr(owner, 'current_highlight_color'):
        canvas.highlight_color = owner.current_highlight_color
    if hasattr(owner, 'highlight_size'):
        canvas.highlight_width = owner.highlight_size.value()
    if hasattr(owner, 'highlight_opacity'):
        canvas.highlight_opacity = owner.highlight_opacity.value() / 100.0
    if hasattr(owner, 'current_tape_color'):
        canvas.tape_color = owner.current_tape_color
    if hasattr(owner, 'tape_size'):
        canvas.tape_width = owner.tape_size.value()
    if hasattr(owner, 'current_tape_pattern'):
        canvas.tape_pattern = owner.current_tape_pattern
    if hasattr(owner, 'erase_size'):
        canvas.eraser_width = owner.erase_size.value()
    if hasattr(owner, 'erase_mode'):
        canvas.eraser_mode = 'partial' if owner.erase_mode.currentIndex() == 0 else 'stroke'
    canvas.shape_kind = getattr(owner, 'shape_kind', canvas.shape_kind)
    main_canvas = getattr(owner, 'canvas', None)
    tool = getattr(main_canvas, 'edit_tool', 'draw') or 'draw'
    if tool not in _ALLOWED_TOOLS:
        tool = 'draw'
    if tool != canvas.edit_tool:
        canvas.setEditTool(tool)
    canvas.update()


def undo_key(canvas, event):
    """Ctrl+Z / Ctrl+Y on a viewer-local canvas, without touching the note."""
    if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
        return False
    key = event.key()
    shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
    if key == Qt.Key.Key_Z and not shift:
        canvas.undoEdit()
        return True
    if key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift):
        canvas.redoEdit()
        return True
    return False


class ScratchWindow(QWidget):
    """One-off tool window.  Reopening always starts with an empty sheet."""

    def __init__(self, owner):
        super().__init__(None, Qt.WindowType.Tool)
        self.owner = owner
        self.settings = owner.color_settings
        self._fitting = False
        self.setWindowTitle('草稿纸')
        self.setMinimumSize(420, 320)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

        self.canvas = make_canvas()
        self.canvas.setObjectName('scratchCanvas')
        self.canvas.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.hint = QLabel('草稿纸 · 关闭即弃 · 工具与颜色跟随主窗口')
        self.hint.setObjectName('scratchHint')
        bar.addWidget(self.hint)
        bar.addStretch(1)
        self.clear_button = QToolButton()
        self.clear_button.setText('清空')
        self.clear_button.setObjectName('scratchClear')
        self.clear_button.clicked.connect(self.clear_ink)
        self.pin_button = QToolButton()
        self.pin_button.setText('贴到当前页')
        self.pin_button.setObjectName('scratchPin')
        self.pin_button.clicked.connect(self.pin_to_page)
        bar.addWidget(self.clear_button)
        bar.addWidget(self.pin_button)
        layout.addLayout(bar)

        self.host = QWidget()
        self.host.setObjectName('scratchHost')
        host_layout = QVBoxLayout(self.host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.addStretch(1)
        middle = QHBoxLayout()
        middle.addStretch(1)
        middle.addWidget(self.canvas, 0, Qt.AlignmentFlag.AlignCenter)
        middle.addStretch(1)
        host_layout.addLayout(middle)
        host_layout.addStretch(1)
        layout.addWidget(self.host, 1)

        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(60)
        self._fit_timer.timeout.connect(self._fit)

        self._restore_geometry()
        self.sync_settings()
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ tools
    def sync_settings(self):
        """Mirror the main window's pen/tool settings onto the pad."""
        sync_canvas_settings(self.owner, self.canvas)

    def clear_ink(self):
        """Undoable clear: one Ctrl+Z brings the whole sheet back."""
        canvas = self.canvas
        if not canvas.edits.state(0)[0]:
            return
        previous = canvas.edit_tool
        canvas.finishGesture()
        canvas.setEditTool('select')
        canvas.selectAllInk()
        canvas.deleteSelection()
        canvas.setEditTool(previous)
        canvas.update()
        self.hint.setText('已清空 · Ctrl+Z 可恢复')

    # ------------------------------------------------------------------- pin
    def pin_to_page(self):
        owner = self.owner
        target = getattr(owner, 'canvas', None)
        prepared = getattr(owner, 'prepared', None)
        if (prepared is None or target is None
                or not hasattr(target, 'image_layers')
                or not str(prepared.path).lower().endswith('.ntb')
                or getattr(owner, 'worker', None) or getattr(owner, 'saver', None)):
            QMessageBox.information(self, '草稿纸',
                                    '请先打开一份 NTB 笔记，才能把草稿贴到当前页。')
            return
        rendered = render_ink_png(self.canvas)
        if rendered is None:
            QMessageBox.information(self, '草稿纸', '草稿还是空的。')
            return
        blob, width_px, height_px = rendered
        page = max(0, min(target.page_count - 1, owner.page.value() - 1))
        target.addImage(blob, width_px, height_px, page)
        self.hint.setText(f'已贴到第 {page + 1} 页')
        owner.statusBar().showMessage(
            '草稿已贴到当前页：可用选择工具拖动、四角缩放或删除；保存后随笔记保留。', 6000)

    # -------------------------------------------------------------- geometry
    def _restore_geometry(self):
        try:
            x, y, w, h = (int(self.settings.value('scratch/' + key))
                          for key in ('x', 'y', 'w', 'h'))
        except (TypeError, ValueError):
            x = y = w = h = None
        screens = QGuiApplication.screens() or []
        visible = (x is not None and 320 <= w <= 4000 and 240 <= h <= 3000
                   and any(screen.availableGeometry().adjusted(-40, -40, 40, 40)
                           .contains(QPoint(x, y)) for screen in screens))
        if not visible:
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen else None
            w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
            if area is not None:
                w = min(w, max(420, area.width() - 40))
                h = min(h, max(320, area.height() - 40))
                x = area.x() + (area.width() - w) // 2
                y = area.y() + (area.height() - h) // 2
            else:
                x = y = 100
        self.setGeometry(x, y, w, h)

    def _save_geometry(self):
        rect = self.geometry()
        for key, value in (('x', rect.x()), ('y', rect.y()),
                           ('w', rect.width()), ('h', rect.height())):
            self.settings.setValue('scratch/' + key, value)
        self.settings.sync()

    def _fit(self):
        if self._fitting:
            return
        size = self.host.size()
        if size.width() < 60 or size.height() < 60:
            return
        zoom = min((size.width() - 8) / (PAGE_WIDTH + 32),
                   (size.height() - 8) / (PAGE_HEIGHT + 24))
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self.canvas.zoom) < 0.015:
            return
        self._fitting = True
        try:
            self.canvas.setZoom(zoom)
        finally:
            self._fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit)

    def closeEvent(self, event):
        self._save_geometry()
        try:
            self.canvas.finishGesture()
        except Exception:
            pass
        if getattr(self.owner, '_scratch_window', None) is self:
            self.owner._scratch_window = None
        self.deleteLater()
        super().closeEvent(event)

    # ------------------------------------------------------------- keyboard
    def _undo_key(self, event):
        return undo_key(self.canvas, event)

    def keyPressEvent(self, event):
        if self._undo_key(event):
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if (watched is self.canvas and event.type() == QEvent.Type.KeyPress
                and self._undo_key(event)):
            event.accept()
            return True
        return super().eventFilter(watched, event)
