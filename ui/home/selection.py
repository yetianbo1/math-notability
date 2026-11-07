"""Home notebook list with explicit batch selection and scoped shortcuts."""
from PyQt6.QtCore import Qt, QItemSelectionModel, QSignalBlocker, QEvent, QPointF, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QListWidget, QAbstractItemView


class NotebookList(QListWidget):
    batchChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.batch = False
        self.anchor = None
        self._pointer_active = False
        self._handled_press = False
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.itemSelectionChanged.connect(self._selection_changed)
        self.setAccessibleName('笔记列表')
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

    def _selection_changed(self):
        if len(self.selectedItems()) > 1:
            self.batch = True
        if not self.selectedItems():
            self.batch = False
        self.viewport().update()
        self.batchChanged.emit()

    def selectAll(self):
        if not self.isEnabled():
            return
        self.finish_pointer_gesture()
        self.batch = bool(self.count())
        super().selectAll()
        self.batchChanged.emit()
        self.viewport().update()

    def cancel_selection(self):
        self.finish_pointer_gesture()
        self.batch = False
        self.anchor = None
        self.clearSelection()
        self.setCurrentRow(-1)
        self.batchChanged.emit()
        self.viewport().update()

    def mousePressEvent(self, event):
        self.finish_pointer_gesture()
        self._handled_press = False
        index = self.indexAt(event.position().toPoint())
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._pointer_active = True
        if not index.isValid():
            # Qt needs the real empty-space press to anchor the rubber band.
            super().mousePressEvent(event)
            return
        rect = self.visualRect(index)
        checkbox = self.itemDelegate().check_rect(rect)
        modifiers = event.modifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        toggle = bool(modifiers & Qt.KeyboardModifier.ControlModifier) or checkbox.contains(event.position())
        if self.batch or toggle or shift:
            self._handled_press = True
            # Pair Qt's drag/release handlers with a real press, while retaining
            # the checkbox and batch-click selection rules below.
            selection = self.selectionModel().selection()
            current = self.currentIndex()
            with QSignalBlocker(self), QSignalBlocker(self.selectionModel()):
                super().mousePressEvent(event)
                self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.selectionModel().setCurrentIndex(current, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.batch = True
            if shift and self.anchor is not None:
                if not (modifiers & Qt.KeyboardModifier.ControlModifier):
                    self.clearSelection()
                for row in range(min(self.anchor,index.row()),max(self.anchor,index.row())+1):
                    self.item(row).setSelected(True)
            else:
                self.item(index.row()).setSelected(not self.item(index.row()).isSelected())
                self.anchor = index.row()
            self.selectionModel().setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
            self.batch = bool(self.selectedItems())
            self._selection_changed()
            event.accept()
            return
        self.anchor = index.row()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.finish_pointer_gesture(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def finish_pointer_gesture(self, event=None):
        """End Qt's rubber band too, including an interrupted mouse grab."""
        if not self._pointer_active and self.state() != self.State.DragSelectingState:
            return
        handled = self._handled_press
        self._pointer_active = self._handled_press = False
        if event is not None and not handled:
            super().mouseReleaseEvent(event)
        else:
            # A cleanup release must not activate a note or undo a checkbox
            # toggle. Qt still receives it to erase its band and stop scrolling.
            selection = self.selectionModel().selection()
            current = self.currentIndex()
            if event is None:
                point = QPointF(-1, -1)
                event = QMouseEvent(QEvent.Type.MouseButtonRelease, point,
                    QPointF(self.viewport().mapToGlobal(point.toPoint())),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier)
            with QSignalBlocker(self), QSignalBlocker(self.selectionModel()):
                super().mouseReleaseEvent(event)
                self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.selectionModel().setCurrentIndex(current, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.setState(self.State.NoState)
        # Repaint the gutters as well as the cards after removing the band.
        self.viewport().update()
        self._selection_changed()

    def mouseMoveEvent(self, event):
        if self._pointer_active and not event.buttons() & Qt.MouseButton.LeftButton:
            self.finish_pointer_gesture()
            event.accept()
            return
        if not self._pointer_active and event.buttons() & Qt.MouseButton.LeftButton:
            # Ignore the remainder of a drag canceled by Esc, focus loss, etc.
            event.accept()
            return
        super().mouseMoveEvent(event)

    def focusOutEvent(self, event):
        self.finish_pointer_gesture()
        super().focusOutEvent(event)

    def event(self, event):
        if hasattr(self, '_pointer_active') and event.type() in (
                QEvent.Type.WindowDeactivate, QEvent.Type.Hide, QEvent.Type.EnabledChange):
            self.finish_pointer_gesture()
        return super().event(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.UngrabMouse:
            self.finish_pointer_gesture()
        return super().viewportEvent(event)

    def clear(self):
        self.finish_pointer_gesture()
        super().clear()

    def setViewMode(self, mode):
        if mode != self.viewMode():
            self.finish_pointer_gesture()
        super().setViewMode(mode)

    def mouseDoubleClickEvent(self, event):
        if self.batch:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancel_selection()
            event.accept()
        elif event.key() == Qt.Key.Key_Space and self.currentItem():
            self.finish_pointer_gesture()
            self.batch = True
            self.currentItem().setSelected(not self.currentItem().isSelected())
            self._selection_changed()
            event.accept()
        elif self.batch and event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return):
            event.accept()
        else:
            super().keyPressEvent(event)
