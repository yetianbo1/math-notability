"""Focused Home dialogs with validation next to the field being edited."""
from pathlib import Path
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                            QLineEdit, QPushButton, QListWidget, QListWidgetItem,
                            QComboBox, QTreeWidget, QTreeWidgetItem, QInputDialog)
from ui.ne_toolbar import tool_icon
from .library import validate_category_name
from .operations import is_managed


class NewCategoryDialog(QDialog):
    def __init__(self, parent, library, parent_key=''):
        super().__init__(parent)
        self.library = library
        self.created_key = None
        self.setWindowTitle('新建分类')
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setFixedWidth(440)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(20)
        header = QHBoxLayout()
        header.setSpacing(12)
        icon = QLabel()
        icon.setObjectName('categoryIcon')
        icon.setFixedSize(44, 44)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(tool_icon('open', '#2563eb').pixmap(QSize(26, 26)))
        header.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        title = QLabel('新建分类')
        title.setObjectName('categoryTitle')
        titles.addWidget(title)
        subtitle = QLabel('把同一主题的笔记整理在一起')
        subtitle.setObjectName('categorySubtitle')
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        layout.addLayout(header)
        layout.addWidget(QLabel('所属一级分类'))
        self.parent_category = QComboBox()
        self.parent_category.setAccessibleName('所属一级分类')
        self.parent_category.addItem('无（创建一级分类）', '')
        for key, name, depth in library.category_rows():
            if not depth:
                self.parent_category.addItem(name, key)
        self.parent_category.setCurrentIndex(max(0, self.parent_category.findData(parent_key)))
        layout.addWidget(self.parent_category)
        field = QVBoxLayout()
        field.setSpacing(8)
        caption = QHBoxLayout()
        label = QLabel('分类名称')
        caption.addWidget(label)
        caption.addStretch()
        self.counter = QLabel('0 / 64')
        self.counter.setObjectName('categorySubtitle')
        caption.addWidget(self.counter)
        field.addLayout(caption)
        self.name = QLineEdit()
        self.name.setAccessibleName('分类名称')
        self.name.setPlaceholderText('例如：课程笔记、工作项目')
        # QLineEdit counts UTF-16 units; Python/storage count Unicode characters.
        # Validate explicitly so 64 Emoji are kept intact and 65 are rejected.
        self.name.setClearButtonEnabled(True)
        label.setBuddy(self.name)
        field.addWidget(self.name)
        self.hint = QLabel()
        self.hint.setObjectName('categoryHint')
        self.hint.setWordWrap(True)
        self.hint.setMinimumHeight(34)
        field.addWidget(self.hint)
        layout.addLayout(field)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch()
        self.cancel = QPushButton('取消')
        self.cancel.setAutoDefault(False)
        self.cancel.clicked.connect(self.reject)
        footer.addWidget(self.cancel)
        self.create = QPushButton('创建分类')
        self.create.setObjectName('createCategory')
        self.create.setDefault(True)
        self.create.clicked.connect(self.submit)
        footer.addWidget(self.create)
        layout.addLayout(footer)
        self.name.textChanged.connect(self.validate)
        self.parent_category.currentIndexChanged.connect(self.validate)
        self.validate()
        self.name.setFocus()

    def validation_error(self):
        name = self.name.text().strip()
        if not name:
            return ''
        if len(name) > 64:
            return '分类名称最多 64 个字符，请缩短名称。'
        try:
            validate_category_name(name)
        except ValueError:
            if name == '未分类':
                return '“未分类”为系统分类，请换一个名称。'
            return '名称不能包含文件夹禁用字符，也不能使用系统保留名称。'
        try:
            self.library.validate_category(name, self.parent_category.currentData())
        except ValueError as exc:
            return str(exc)
        return ''

    def show_error(self, error):
        self.hint.setText(error or '最多 64 个字符，不支持 / \\ : * ? " < > |')
        for widget in (self.name,self.hint):
            widget.setProperty('invalid', bool(error))
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def validate(self):
        error = self.validation_error()
        self.counter.setText(f'{len(self.name.text().strip())} / 64')
        self.show_error(error)
        self.create.setEnabled(bool(self.name.text().strip()) and not error)

    def submit(self):
        self.validate()
        if not self.create.isEnabled():
            return
        self.create.setEnabled(False)
        try:
            self.created_key = self.library.add_category(self.name.text().strip(), self.parent_category.currentData())
        except (OSError,ValueError) as exc:
            self.show_error('创建失败：' + str(exc))
            self.create.setEnabled(True)
            return
        self.accept()


class MoveCategoryDialog(QDialog):
    def __init__(self, parent, library, key):
        super().__init__(parent)
        self.library, self.key = library, key
        self.setWindowTitle('移动分类')
        self.setFixedWidth(460)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel('移动「' + library.category_label(key) + '」')
        title.setObjectName('categoryTitle')
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(QLabel('目标位置'))
        self.destination = QComboBox()
        self.destination.setAccessibleName('分类目标位置')
        self.destination.addItem('顶层（作为一级分类）', '')
        for candidate, name, depth in library.category_rows():
            if not depth and candidate != key:
                self.destination.addItem(name, candidate)
        self.destination.setCurrentIndex(max(0, self.destination.findData(library.category_parents.get(key, ''))))
        layout.addWidget(self.destination)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('categoryHint')
        layout.addWidget(self.hint)
        info = QLabel('库内笔记随分类移动；外部笔记保留原位置。分类最多支持两级。')
        info.setObjectName('categorySubtitle')
        info.setWordWrap(True)
        layout.addWidget(info)
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton('取消')
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        self.confirm = QPushButton('移动分类')
        self.confirm.setObjectName('createCategory')
        self.confirm.clicked.connect(self.submit)
        footer.addWidget(self.confirm)
        layout.addLayout(footer)
        self.destination.currentIndexChanged.connect(self.validate)
        self.validate()

    def validate(self):
        parent = self.destination.currentData()
        error = ''
        try:
            self.library.validate_category(self.library.categories[self.key], parent, key=self.key)
        except (KeyError, ValueError) as exc:
            error = str(exc)
        changed = parent != self.library.category_parents.get(self.key, '')
        self.confirm.setEnabled(changed and not error)
        self.hint.setText(error or ('请选择新的位置。' if not changed else
            '将移至「' + (self.library.categories[parent] if parent else '顶层') + '」。'))

    def submit(self):
        self.validate()
        if not self.confirm.isEnabled():
            return
        self.confirm.setEnabled(False)
        try:
            self.library.move_category(self.key, self.destination.currentData())
        except (OSError, ValueError) as exc:
            self.hint.setText('移动失败：' + str(exc))
            self.confirm.setEnabled(True)
            return
        self.accept()


class CategoryManagerDialog(QDialog):
    def __init__(self, parent, library, selected_key=''):
        super().__init__(parent)
        self.library = library
        self.setWindowTitle('分类管理')
        self.resize(720, 540)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel('分类管理')
        title.setObjectName('categoryTitle')
        layout.addWidget(title)
        subtitle = QLabel('选择一级分类可新建二级分类，或将分类移动到其他一级分类／顶层。')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['分类', '笔记数（含下级）'])
        self.tree.setColumnWidth(0, 440)
        self.tree.setAccessibleName('两级分类')
        self.tree.currentItemChanged.connect(self.update_actions)
        layout.addWidget(self.tree, 1)
        controls = QHBoxLayout()
        self.buttons = {}
        for key, text, callback in (
            ('root', '新建一级', lambda: self.create_category('')),
            ('child', '新建二级', self.create_child),
            ('rename', '重命名', self.rename_category),
            ('move', '移动分类', self.move_category),
            ('remove', '移除分类', self.remove_category),
        ):
            button = QPushButton(text)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            self.buttons[key] = button
            controls.addWidget(button)
        layout.addLayout(controls)
        self.message = QLabel('移除分类会保留笔记；有二级分类时请先整理下级分类。')
        self.message.setObjectName('categoryHint')
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        close = QPushButton('完成')
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self.refresh(selected_key)

    def selected_key(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ''

    def refresh(self, selected_key=''):
        self.tree.clear()
        roots = {}
        entries = self.library.entries()
        for key, name, depth in self.library.category_rows():
            group = {key, *self.library.child_categories(key)}
            count = sum(entry.get('category') in group for entry in entries)
            item = QTreeWidgetItem([name, str(count)])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setToolTip(0, self.library.category_label(key))
            if depth:
                roots[self.library.category_parents[key]].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
                roots[key] = item
            if key == selected_key:
                self.tree.setCurrentItem(item)
        self.tree.expandAll()
        self.update_actions()

    def update_actions(self, *_):
        key = self.selected_key()
        exists = key in self.library.categories
        for name in ('child', 'rename', 'move', 'remove'):
            self.buttons[name].setEnabled(exists)
        self.buttons['child'].setEnabled(exists and not self.library.category_parents.get(key))
        has_children = bool(self.library.child_categories(key)) if exists else False
        self.buttons['move'].setEnabled(exists and not has_children)
        self.buttons['move'].setToolTip('请先移动二级分类，避免产生第三级。' if has_children else '移入一级分类或移回顶层')
        self.buttons['remove'].setEnabled(exists and not has_children)

    def create_category(self, parent_key):
        dialog = NewCategoryDialog(self, self.library, parent_key)
        try:
            if dialog.exec():
                self.refresh(dialog.created_key)
        finally:
            dialog.deleteLater()

    def create_child(self):
        key = self.selected_key()
        if key in self.library.categories and not self.library.category_parents.get(key):
            self.create_category(key)

    def rename_category(self):
        key = self.selected_key()
        if key not in self.library.categories:
            return
        name, accepted = QInputDialog.getText(self, '重命名分类', '分类名称', text=self.library.categories[key])
        if accepted:
            self.mutate(lambda: self.library.rename_category(key, name), key)

    def move_category(self):
        key = self.selected_key()
        if key not in self.library.categories:
            return
        dialog = MoveCategoryDialog(self, self.library, key)
        try:
            if dialog.exec():
                self.refresh(key)
        finally:
            dialog.deleteLater()

    def remove_category(self):
        key = self.selected_key()
        if key in self.library.categories:
            self.mutate(lambda: self.library.remove_category(key))

    def mutate(self, callback, selected_key=''):
        try:
            callback()
            self.message.setText('分类已更新。')
        except (OSError, ValueError) as exc:
            self.message.setText(str(exc))
        self.refresh(selected_key)


class DeleteNotesDialog(QDialog):
    def __init__(self,parent,paths):
        super().__init__(parent)
        self.paths = tuple(paths)
        self.setWindowTitle('删除笔记')
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint,False)
        self.setFixedWidth(500)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24,24,24,24)
        layout.setSpacing(16)
        title = QLabel(f'删除选中的 {len(paths)} 本笔记？')
        title.setObjectName('categoryTitle')
        title.setWordWrap(True)
        layout.addWidget(title)
        description = QLabel('文件将移至系统回收站，并从笔记列表移除。\n你可以从回收站恢复文件，再重新打开。')
        description.setObjectName('categorySubtitle')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.files = QListWidget()
        self.files.setAccessibleName('待删除的笔记文件')
        self.files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.setFixedHeight(min(176,54*len(paths)+12))
        for path in paths:
            item = QListWidgetItem(Path(path).name+'\n'+str(Path(path).parent))
            item.setToolTip(str(path))
            item.setSizeHint(QSize(420,54))
            self.files.addItem(item)
        layout.addWidget(self.files)
        self.external = sum(not is_managed(path) for path in paths)
        self.warning = QLabel(f'其中 {self.external} 本来自外部位置，外部原文件也会移至回收站。'
            if self.external else '移至回收站后，笔记的打开记录也会一并移除。')
        self.warning.setObjectName('deleteWarning')
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch()
        self.cancel = QPushButton('取消')
        self.cancel.setDefault(True)
        self.cancel.clicked.connect(self.reject)
        footer.addWidget(self.cancel)
        self.confirm = QPushButton('移至回收站')
        self.confirm.setObjectName('confirmDelete')
        self.confirm.setIcon(tool_icon('delete','#ffffff'))
        self.confirm.setAutoDefault(False)
        self.confirm.clicked.connect(self.accept)
        footer.addWidget(self.confirm)
        layout.addLayout(footer)
        self.cancel.setFocus()


STYLE = """
QDialog { background:#ffffff; }
QLabel { color:#334b6d; font-size:13px; background:transparent; }
QLabel#categoryIcon { background:#edf4ff; border-radius:12px; }
QLabel#categoryTitle { font-size:20px; font-weight:700; color:#1c3558; }
QLabel#categorySubtitle { color:#8090a8; font-size:12px; }
QLabel#categoryHint { color:#8795aa; font-size:12px; }
QLabel#categoryHint[invalid="true"] { color:#c34b55; }
QLineEdit { background:#f8faff; color:#263c5c; border:1px solid #d4dff0;
 border-radius:8px; padding:11px 12px; font-size:14px; selection-background-color:#dce8ff; }
QLineEdit:focus { background:#ffffff; border-color:#4d86ee; }
QComboBox { background:#f8faff; color:#263c5c; border:1px solid #d4dff0; border-radius:8px;
 padding:9px 12px; font-size:13px; min-height:22px; }
QComboBox QAbstractItemView { background:#ffffff; color:#263c5c; selection-background-color:#dce8ff; }
QTreeWidget { background:#f8faff; color:#334b6d; border:1px solid #e0e7f2; border-radius:8px;
 padding:6px; outline:0; }
QTreeWidget::item { min-height:32px; }
QTreeWidget::item:selected { background:#dce8ff; color:#215bd0; }
QPushButton:disabled { background:#f1f5fa; color:#9aa8bd; border-color:#e0e7f1; }
QLineEdit[invalid="true"] { border-color:#d77d85; background:#fffafb; }
QPushButton { background:#ffffff; color:#526988; border:1px solid #d4dff0;
 border-radius:8px; padding:9px 18px; font-size:13px; min-width:60px; }
QPushButton:hover { background:#f2f6fc; border-color:#a7bfe4; }
QPushButton:focus { border-color:#4d86ee; }
QPushButton#createCategory { background:#2563eb; color:#ffffff; border-color:#2563eb; }
QPushButton#createCategory:hover { background:#1d53cd; border-color:#1d53cd; }
QPushButton#createCategory:disabled { background:#e9eff9; color:#9aaac2; border-color:#e9eff9; }
QLabel#deleteWarning { background:#fff7f7; color:#b85a64; border-radius:7px; padding:10px; font-size:12px; }
QListWidget { background:#f8faff; color:#526988; border:1px solid #e0e7f2; border-radius:8px;
 padding:5px; font-size:12px; outline:0; }
QListWidget::item { padding:0 6px; }
QPushButton#confirmDelete { color:#ffffff; background:#c95765; border-color:#c95765; }
QPushButton#confirmDelete:hover { background:#b84252; border-color:#b84252; }
QPushButton#confirmDelete:focus { border-color:#8e2634; }
"""
