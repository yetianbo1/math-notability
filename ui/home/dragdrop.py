"""Internal notebook drags: files stay under library transaction control."""
import json

from PyQt6.QtCore import Qt, QMimeData, QPoint, QItemSelectionModel
from PyQt6.QtGui import QDrag, QColor
from PyQt6.QtWidgets import QApplication, QListWidget
from .selection import NotebookList


NOTE_MIME = 'application/x-notabilityeditor-notebooks'


class CategoryDropMixin:
    def init_category_drop(self, home):
        self.home = home
        self._drop_item = None
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def drop_category(self, point):
        item = self.itemAt(point)
        if item is None:
            return None
        if self is self.home.categories:
            key = item.data(Qt.ItemDataRole.UserRole)
            return '' if key == '@none' else key if key in self.home.library.categories else None
        entry = item.data(int(Qt.ItemDataRole.UserRole) + 1) or {}
        return entry.get('category') if entry.get('group') else None

    def drag_paths(self, event):
        source = event.source()
        if self.home._busy or source is not self.home.notes or not source.isEnabled():
            return ()
        if not event.mimeData().hasFormat(NOTE_MIME):
            return ()
        try:
            paths = json.loads(bytes(event.mimeData().data(NOTE_MIME)).decode('utf8'))
        except (UnicodeError, ValueError):
            return ()
        if not isinstance(paths, list) or not paths or tuple(paths) != source._drag_paths:
            return ()
        return tuple(paths)

    def clear_drop_target(self):
        if self._drop_item is not None:
            self._drop_item.setData(Qt.ItemDataRole.BackgroundRole, None)
            self._drop_item = None

    def dragEnterEvent(self, event):
        if self.drag_paths(event):
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.clear_drop_target()
        key = self.drop_category(event.position().toPoint())
        if self.drag_paths(event) and key is not None:
            self._drop_item = self.itemAt(event.position().toPoint())
            self._drop_item.setData(Qt.ItemDataRole.BackgroundRole, QColor('#dce8ff'))
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.clear_drop_target()
        event.accept()

    def dropEvent(self, event):
        self.clear_drop_target()
        key = self.drop_category(event.position().toPoint())
        paths = self.drag_paths(event)
        if paths and key is not None:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.home.queue_drop_classification(paths, key)
        else:
            event.ignore()

    def clear(self):
        self.clear_drop_target()
        super().clear()


class CategoryList(CategoryDropMixin, QListWidget):
    def __init__(self, home):
        super().__init__()
        self.init_category_drop(home)


class DraggableNotebookList(CategoryDropMixin, NotebookList):
    def __init__(self, home):
        super().__init__()
        self.init_category_drop(home)
        self._drag_start = QPoint()
        self._drag_candidate = None
        self._drag_paths = ()
        self._deferred_toggle = None
        self.setToolTip('拖动笔记到左侧分类或分类组标题；拖动勾选框可框选多本笔记。')

    def mousePressEvent(self, event):
        self._drag_candidate = self._deferred_toggle = None
        item = self.itemAt(event.position().toPoint())
        if item is not None and not item.data(Qt.ItemDataRole.UserRole):
            self.finish_pointer_gesture()
            event.accept()
            return
        candidate = (item is not None and item.data(Qt.ItemDataRole.UserRole)
                     and event.button() == Qt.MouseButton.LeftButton
                     and not event.modifiers()
                     and not self.itemDelegate().check_rect(self.visualItemRect(item)).contains(event.position())
                     and not self.itemDelegate().menu_rect(self.visualItemRect(item)).contains(event.position()))
        selection = self.selectionModel().selection()
        was_selected = bool(item and item.isSelected())
        was_batch = self.batch
        super().mousePressEvent(event)
        if candidate:
            if was_selected and was_batch:
                self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                self.batch = True
                self._deferred_toggle = item
            self._drag_start = event.position().toPoint()
            self._drag_candidate = item.data(Qt.ItemDataRole.UserRole)

    def mouseMoveEvent(self, event):
        if (self._drag_candidate and self._pointer_active and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._drag_start).manhattanLength() >= QApplication.startDragDistance()):
            paths = tuple(item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()
                          if item.data(Qt.ItemDataRole.UserRole))
            if not paths:
                paths = (self._drag_candidate,)
            self._drag_candidate = self._deferred_toggle = None
            self.finish_pointer_gesture()
            self.execute_classification_drag(paths)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def execute_classification_drag(self, paths):
        self._drag_paths = tuple(paths)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(NOTE_MIME, json.dumps(list(paths), ensure_ascii=False).encode('utf8'))
        drag.setMimeData(mime)
        try:
            return drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._drag_paths = ()
            self.home.categories.clear_drop_target()
            self.clear_drop_target()
            self.finish_pointer_gesture()

    def mouseReleaseEvent(self, event):
        self._drag_candidate = None
        if self._deferred_toggle is not None:
            self._deferred_toggle.setSelected(False)
            self._deferred_toggle = None
        super().mouseReleaseEvent(event)

    def clear(self):
        self._drag_candidate = self._deferred_toggle = None
        super().clear()
