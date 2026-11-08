"""Always-on-top question cards: one continuous writable sheet of paper.

The printed question is painted as a bitmap at the top of a single viewer-local
canvas; there is no divider, no separate question widget and no second ink
layer.  Ink can therefore be written over the question and carried straight on
into the working area below it.  The screenshot and the (single) stroke list
live in the NTB's private question-card prefix; the official Notability
structure is never modified.  Closing a card only hides the window; the card
itself stays in the note and can be reopened from Tools > Question cards.
"""
from __future__ import annotations

import hashlib
import uuid

from PyQt6.QtCore import (Qt, QBuffer, QByteArray, QEvent, QIODevice, QPoint,
                          QRect, QRectF, QSize, QTimer)
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PyQt6.QtWidgets import (QBoxLayout, QHBoxLayout, QLabel, QMessageBox,
                             QSizePolicy, QToolButton, QVBoxLayout, QWidget)
from PyQt6 import sip

from document_core.ne_question_cards import encode_strokes, decode_strokes
from rendering.ne_pdfview import PdfNoteCanvas
from ui.ne_card_toolbar import CardToolBar
from ui.ne_scratch import sync_canvas_settings, undo_key

MIN_QUESTION_HEIGHT = 80
MAX_QUESTION_RATIO = 0.70
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1000, 760
_CARD_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" data-page-count="1" data-local-ink="1"/>'
_MAX_WIDGET = 16777215


def capture_page_rect(canvas, page, rect, scale=2.0):
    """Render one page with all its ink and crop ``rect`` (page coordinates).

    Returns ``(png_bytes, width_px, height_px)`` or None.  This is a bitmap of
    the whole visible page region, so printed questions, diagrams and earlier
    annotations are all present even when the marquee selected no ink at all.
    """
    from rendering.ne_mixed_layers import paint_content
    nw, nh = canvas.editPageSize(page)
    width = max(1, int(round(nw * scale)))
    height = max(1, int(round(nh * scale)))
    limit = 16_000_000
    if width * height > limit:
        factor = (limit / (width * height)) ** 0.5
        width = max(1, int(width * factor))
        height = max(1, int(height * factor))
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.scale(width / nw, height / nh)
        background = canvas._render_page(page, QSize(width, height),
                                         background_only=True)
        painter.drawImage(QRectF(0, 0, nw, nh), background)
        paint_content(canvas, painter, page, QRectF(0, 0, nw, nh),
                      include_ink=True)
    finally:
        painter.end()
    x0 = max(0, int(round(rect.left() * width / nw)))
    y0 = max(0, int(round(rect.top() * height / nh)))
    x1 = min(width, int(round(rect.right() * width / nw)))
    y1 = min(height, int(round(rect.bottom() * height / nh)))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = image.copy(x0, y0, x1 - x0, y1 - y0)
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return None
    ok = crop.save(buffer, 'PNG')
    buffer.close()
    if not ok:
        return None
    return bytes(payload), crop.width(), crop.height()


def restore_canvas_strokes(canvas, strokes):
    """Seed a viewer-local canvas with previously saved, still-editable ink."""
    canvas.edits.originals = lambda page: strokes if page == 0 else []
    canvas.original_edit_cache.clear()
    canvas.edits.cache.clear()


def make_card(page, number, rect, rendered):
    blob, width_px, height_px = rendered
    return {
        'id': uuid.uuid4().hex,
        'number': int(number),
        'page': int(page),
        'asset': hashlib.sha256(blob).hexdigest(),
        'image_width': int(width_px),
        'image_height': int(height_px),
        'rect': [float(rect.x()), float(rect.y()),
                 float(rect.width()), float(rect.height())],
        'strokes': [],
        'geometry': [0, 0, 0, 0],
        'zoom_bias': 1.0,
    }


class CardCanvas(PdfNoteCanvas):
    """A single sheet: white paper fills the widget, question bitmap on top.

    Overriding ``paintVisible`` and ``setZoom`` here keeps every change out of
    the notebook canvas.  ``pointOnPage`` uses ``left=(width-w)/2`` and
    ``pageTop`` for hit testing, so the painted target must use exactly those.
    """

    card_sheet = True

    def __init__(self, blob, width=1000.0, height=700.0):
        self.question_image = QImage.fromData(blob, 'PNG')
        super().__init__(None, _CARD_SVG, width, height)
        self.question_rect = QRectF()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(1, 1)

    # ------------------------------------------------------------- geometry
    def setZoom(self, zoom):
        # Deliberately no setFixedSize: the enclosing layout owns the widget
        # size, and the paper is always as large as that widget.
        if hasattr(self, 'edits'):
            self.finishGesture()
        self.zoom = max(0.2, min(5.0, float(zoom)))
        self.draw_sizes = [(max(1, round(w * self.zoom)), max(1, round(h * self.zoom)))
                           for w, h in self.sizes]
        self.tops = [0]
        self.clearCache()
        self._syncBrushCursor()
        self.update()

    def set_page_size(self, width, height):
        """Grow/shrink the sheet with the window; stroke coordinates never move."""
        w, h = max(120.0, float(width)), max(120.0, float(height))
        if self.sizes and abs(self.sizes[0][0] - w) < 1 and abs(self.sizes[0][1] - h) < 1:
            return
        self.sizes = [(w, h)]
        self.note_width, self.note_height = w, h
        self.page_layout = []
        self.setZoom(self.zoom)

    def question_display_height(self, page_width, page_height):
        """Question bitmap height in page coordinates, width-filling by default."""
        image = self.question_image
        if image.isNull() or not image.width() or not image.height():
            return 0.0
        natural = page_width * image.height() / image.width()
        cap = max(MIN_QUESTION_HEIGHT, page_height * MAX_QUESTION_RATIO)
        return min(max(natural, MIN_QUESTION_HEIGHT), cap)

    def question_target(self, target, nw, nh):
        """Screen rectangle occupied by the printed question, or None."""
        image = self.question_image
        if image.isNull() or not image.width() or not image.height():
            self.question_rect = QRectF()
            return None
        height = self.question_display_height(nw, nh)
        scale = min(nw / image.width(), height / image.height())
        page_w, page_h = image.width() * scale, image.height() * scale
        self.question_rect = QRectF(0, 0, page_w, page_h)
        return QRectF(target.left(), target.top(),
                      page_w * target.width() / nw,
                      page_h * target.height() / nh)

    def cardButtonVisible(self):
        """Never offer "make a card" from inside a card.

        The card window is a plain QWidget without create_question_card(), so
        the floating action inherited from the notebook canvas would be dead.
        """
        return False

    # --------------------------------------------------------------- paint
    def paintVisible(self, painter, rect, dpr):
        from rendering.ne_mixed_layers import paint_content
        painter.save()
        try:
            painter.setClipRect(rect)
            painter.fillRect(rect, QColor('#ffffff'))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            w, h = self.draw_sizes[0]
            left = (self.width() - w) / 2
            target = QRectF(left, self.pageTop(0), w, h)
            nw, nh = self.editPageSize(0)
            question = self.question_target(target, nw, nh)
            if question is not None:
                painter.drawImage(question, self.question_image)
            paint_content(self, painter, 0, target, include_ink=True)
            if self.show_ink:
                self.paintSelectionOverlay(painter, 0, target)
            self.paintImageSelection(painter, 0, target)
            self.paintEraserCursor(painter)
            self.paintBrushCursor(painter)
            if hasattr(self, 'paintShapePreview'):
                self.paintShapePreview(painter)
        finally:
            painter.restore()


class QuestionCardWindow(QWidget):
    """One always-on-top card on a single continuous, writable sheet."""

    def __init__(self, owner, card, blob):
        flags = Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.owner = owner
        self.card = dict(card)
        self.card_id = card['id']
        self._zoom_bias = float(card.get('zoom_bias', 1.0))
        self._deleted = False
        self._fullscreen = False
        self._saved_geometry = None
        self._hidden_peers = []
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(40)
        self._layout_timer.timeout.connect(self._refresh_layout)
        self.setWindowTitle(f"第 {int(card['page']) + 1} 页 · 题目卡 {int(card['number']):02d}")
        self.setMinimumSize(420, 360)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The canvas is created before the toolbar: CardToolBar binds its
        # undo/redo actions and the settings pages to this very canvas.
        self.canvas = CardCanvas(blob)
        self.canvas.setObjectName('cardCanvas')
        restore_canvas_strokes(self.canvas, decode_strokes(card.get('strokes', [])))
        layout.addWidget(self.canvas, 1)
        # Historical name: the working area used to be a separate host widget.
        self.host = self.canvas
        self.canvas.installEventFilter(self)
        self.bar = self._build_bar()
        layout.insertWidget(0, self.bar)
        # Every finished stroke goes straight into the note's in-memory card
        # data, then debounces a silent save.  Previously ink was only synced
        # when the window closed or the user pressed Ctrl+S.
        self.canvas.editsChanged.connect(self._ink_changed)

        self._restore_geometry()
        sync_canvas_settings(owner, self.canvas)
        self._bind_owner_actions()
        self.sync_toolbar()
        QTimer.singleShot(0, self._refresh_layout)
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ ui
    def _build_bar(self):
        bar = QWidget()
        bar.setObjectName('cardBar')
        shell = QVBoxLayout(bar)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        self.hint = QLabel('题目与演算在同一张纸')
        self.hint.setObjectName('cardHint')
        self.full_button = self._bar_button('全屏', self.toggle_fullscreen)
        self.clear_button = self._bar_button('清空草稿', self.clear_draft)
        self.delete_button = self._bar_button('删除卡片', self.delete_card)
        for button, name in ((self.full_button, 'cardFullscreen'),
                             (self.clear_button, 'cardClear'),
                             (self.delete_button, 'cardDelete')):
            button.setObjectName(name)
        self.toolbar = CardToolBar(
            self.owner, self,
            extras=(self.hint, self.full_button, self.clear_button,
                    self.delete_button))
        shell.addWidget(self.toolbar)
        return bar

    def sync_toolbar(self):
        toolbar = getattr(self, 'toolbar', None)
        if toolbar is not None:
            toolbar.set_tool(toolbar._current_tool())
            toolbar.sync_controls()

    def _bar_button(self, text, callback):
        button = QToolButton()
        button.setText(text)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.clicked.connect(callback)
        return button

    def _bind_owner_actions(self):
        """Mirror tool/color changes made from the card's own full-screen strip."""
        actions = list((getattr(self.owner, 'tools', {}) or {}).values())
        actions += list((getattr(self.owner, 'color_actions', {}) or {}).values())
        for action in actions:
            try:
                action.triggered.connect(self._sync_owner_tools)
            except (AttributeError, RuntimeError):
                pass

    def _sync_owner_tools(self, *args):
        def apply():
            try:
                if sip.isdeleted(self) or sip.isdeleted(self.canvas):
                    return
                sync_canvas_settings(self.owner, self.canvas)
                self.sync_toolbar()
            except (RuntimeError, AttributeError):
                pass
        QTimer.singleShot(0, apply)

    # --------------------------------------------------------------- layout
    def _question_height(self):
        nw, nh = self.canvas.editPageSize(0)
        return int(round(self.canvas.question_display_height(nw, nh) * self.canvas.zoom))

    def _refresh_layout(self):
        self.canvas.set_page_size(self.canvas.width(), self.canvas.height())
        self._apply_zoom()

    def _apply_zoom(self):
        zoom = max(0.2, min(5.0, self._zoom_bias))
        if abs(zoom - self.canvas.zoom) > 0.015:
            self.canvas.setZoom(zoom)

    def _fit(self):
        """Kept for callers that used to recompute the fitted page zoom."""
        self._refresh_layout()

    def _restore_geometry(self):
        geometry = self.card.get('geometry') or []
        x = y = w = h = None
        if isinstance(geometry, list) and len(geometry) == 4:
            try:
                x, y, w, h = (int(v) for v in geometry)
            except (TypeError, ValueError):
                x = y = w = h = None
        screens = QGuiApplication.screens() or []
        visible = (x is not None and 420 <= w <= 4000 and 360 <= h <= 3000
                   and any(screen.availableGeometry().adjusted(-40, -40, 40, 40)
                           .contains(QPoint(x, y)) for screen in screens))
        if not visible:
            screen = QGuiApplication.primaryScreen()
            area = screen.availableGeometry() if screen else None
            w, h = DEFAULT_WIDTH, DEFAULT_HEIGHT
            if area is not None:
                w = min(w, max(420, area.width() - 40))
                h = min(h, max(360, area.height() - 40))
                x = area.x() + (area.width() - w) // 2
                y = area.y() + (area.height() - h) // 2
            else:
                x = y = 100
        self.setGeometry(x, y, w, h)

    # ----------------------------------------------------------- fullscreen
    def _fullscreen_target(self):
        """The whole screen: the card carries its own full toolbar now."""
        screen = (QGuiApplication.screenAt(self.geometry().center())
                  or (QGuiApplication.screens() or [None])[0])
        return QRect(screen.availableGeometry()) if screen else QRect(0, 0, 1920, 1080)

    def is_card_fullscreen(self):
        return self._fullscreen

    def toggle_fullscreen(self):
        if self._fullscreen:
            self.restore_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        if self._fullscreen:
            return
        geometry = self._fullscreen_target()
        self._saved_geometry = QRect(self.geometry())
        self._hidden_peers = []
        windows = getattr(self.owner, '_question_card_windows', None)
        if isinstance(windows, dict):
            for other in windows.values():
                if other is self:
                    continue
                try:
                    if not sip.isdeleted(other) and other.isVisible():
                        self._hidden_peers.append(other)
                        other.hide()
                except RuntimeError:
                    pass
        self.full_button.setText('还原')
        self._fullscreen = True
        # Deliberately keep the original window flags: changing them on a
        # visible always-on-top Tool window recreates the native surface and
        # has been observed to crash on exit.  Geometry alone is enough.
        self.setGeometry(geometry)
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._refresh_layout)

    def _restore_peers(self):
        for other in self._hidden_peers:
            try:
                if not sip.isdeleted(other):
                    other.show()
            except RuntimeError:
                pass
        self._hidden_peers = []

    def restore_fullscreen(self):
        if not self._fullscreen:
            return
        self._fullscreen = False
        self.full_button.setText('全屏')
        if self._saved_geometry is not None:
            self.setGeometry(self._saved_geometry)
        self.show()
        self.raise_()
        self._restore_peers()
        QTimer.singleShot(0, self._refresh_layout)

    # --------------------------------------------------------------- data
    def card_data(self):
        data = dict(self.card)
        rect = self._saved_geometry if self._fullscreen else self.geometry()
        data['strokes'] = encode_strokes(self.canvas.edits.state(0)[0])
        data['geometry'] = [rect.x(), rect.y(), rect.width(), rect.height()]
        data['zoom_bias'] = float(self._zoom_bias)
        return data

    def _mark_dirty(self):
        marker = getattr(self.owner, 'mark_cards_dirty', None)
        if marker is not None:
            marker()

    def _sync_to_owner(self):
        update = getattr(self.owner, 'update_question_card', None)
        if update is not None:
            update(self.card_id, self.card_data())

    def _ink_changed(self):
        """A stroke just finished: persist it and schedule a silent save."""
        if self._deleted:
            return
        self._sync_to_owner()
        self._mark_dirty()
        self._schedule_autosave()

    def _schedule_autosave(self):
        schedule = getattr(self.owner, 'schedule_card_autosave', None)
        if schedule is not None:
            schedule()

    def _flush_autosave(self):
        flush = getattr(self.owner, 'flush_card_autosave', None)
        if flush is not None:
            flush()

    def clear_draft(self):
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
        # deleteSelection() emits editsChanged, so _ink_changed already ran.
        self._sync_to_owner()
        self._mark_dirty()
        self._schedule_autosave()

    def delete_card(self):
        answer = QMessageBox.question(
            self, '删除题目卡', '删除后这张卡片的题目截图和笔迹都会移除，无法恢复。确定删除吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._deleted = True
        remove = getattr(self.owner, 'delete_question_card', None)
        if remove is not None:
            remove(self.card_id)
        self.close()

    # ------------------------------------------------------------- events
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._refresh_layout)

    def closeEvent(self, event):
        try:
            self.canvas.finishGesture()
        except Exception:
            pass
        # Sync first: while still marked full-screen, card_data() reports the
        # saved windowed geometry rather than the temporary screen rectangle.
        if not self._deleted:
            self._sync_to_owner()
            self._mark_dirty()
            # A pending debounce may never fire if the app is closing, so
            # commit the last strokes through the normal save pipeline now.
            self._flush_autosave()
        if self._fullscreen:
            # Do NOT call restore_fullscreen() here: it calls show() on a
            # widget that is in the middle of closing.
            self._fullscreen = False
            self._restore_peers()
        windows = getattr(self.owner, '_question_card_windows', None)
        if isinstance(windows, dict) and windows.get(self.card_id) is self:
            del windows[self.card_id]
        self.deleteLater()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if self._fullscreen and event.key() == Qt.Key.Key_Escape:
            self.restore_fullscreen()
            event.accept()
            return
        if undo_key(self.canvas, event):
            event.accept()
            # undoEdit() emits editsChanged, so the ink is already synced.
            self._schedule_autosave()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            delta = event.angleDelta().y()
            if delta:
                bias = self._zoom_bias * (1.15 if delta > 0 else 1 / 1.15)
                self._zoom_bias = max(0.5, min(2.5, bias))
                self._apply_zoom()
                self._sync_to_owner()
                self._mark_dirty()
                self._schedule_autosave()
            event.accept()
            return True
        if (watched is self.canvas and event.type() == QEvent.Type.KeyPress
                and undo_key(self.canvas, event)):
            event.accept()
            self._schedule_autosave()
            return True
        return super().eventFilter(watched, event)
