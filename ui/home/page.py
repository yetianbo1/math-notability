"""Desktop notebook home: independent layout and library interactions."""
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QSize, QDate, QTimer, QEvent, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem, QComboBox,
    QInputDialog, QMessageBox, QMenu, QAbstractItemView, QListView, QToolButton, QSizePolicy, QCheckBox, QStyledItemDelegate)

from settings.ne_recent_files import read_recent_files
from .library import identity
from .cards import NotebookCard
from .dragdrop import CategoryList, DraggableNotebookList
from .operations import is_managed
from .hierarchy import populate_category_menu
from ui.ne_toolbar import tool_icon
from platform_integration.ne_windows import icon_path


class HomePage(QWidget):
    changed = pyqtSignal()

    def __init__(self, library, *, new_note, open_note, import_note, import_pdf, open_path):
        super().__init__()
        self.library, self.open_path = library, open_path
        self.filter = '@home'
        self._busy = False
        self._scope = None
        self.setObjectName('desktopHome')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        arrow = (icon_path().parent / 'chevron-down.svg').as_posix()
        self.setStyleSheet(STYLE.replace('__HOME_ARROW__',arrow))
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = QWidget()
        self.sidebar.setObjectName('homeSidebar')
        self.sidebar.setFixedWidth(220)
        left = QVBoxLayout(self.sidebar)
        left.setContentsMargins(16, 24, 16, 18)
        left.setSpacing(20)
        brand = self.label('NotabilityEditor', 'homeBrand')
        brand.setStyleSheet('font-size:16px; font-weight:700;')
        left.addWidget(brand)
        self.search = QLineEdit()
        self.search.setObjectName('homeSearch')
        self.search.setPlaceholderText('搜索当前列表')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_notes)
        left.addWidget(self.search)
        self.categories = CategoryList(self)
        self.categories.setObjectName('homeCategories')
        self.categories.setSpacing(5)
        self.categories.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.categories.currentItemChanged.connect(self.filter_changed)
        self.categories.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.categories.customContextMenuRequested.connect(self.category_menu)
        left.addWidget(self.categories, 1)
        self.add_category_button = QPushButton('＋  新建分类')
        self.add_category_button.clicked.connect(self.add_category)
        left.addWidget(self.add_category_button)
        self.manage_categories_button = QPushButton('分类管理')
        self.manage_categories_button.clicked.connect(self.manage_categories)
        left.addWidget(self.manage_categories_button)
        left.addWidget(self.label('打开笔记记录原位置\n导入与新建存入笔记库', 'homeSubtitle'))
        root.addWidget(self.sidebar)
        details = QWidget()
        content = QVBoxLayout(details)
        content.setContentsMargins(24, 18, 24, 18)
        content.setSpacing(12)
        root.addWidget(details, 1)
        self.top_bar = QWidget()
        top = QHBoxLayout(self.top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        self.breadcrumb = self.label('首页', 'homeSubtitle')
        top.addWidget(self.breadcrumb)
        top.addStretch()
        self.actions = {}
        button = QPushButton('导入笔记')
        button.setIcon(tool_icon('import', '#2860d9'))
        button.setToolTip('将 NTB / NOTE 复制到本机笔记库，再打开副本')
        button.clicked.connect(import_note)
        self.actions['import'] = button
        top.addWidget(button)
        content.addWidget(self.top_bar)
        self.upper = QWidget()
        upper = QHBoxLayout(self.upper)
        upper.setContentsMargins(0, 8, 0, 8)
        upper.setSpacing(18)
        self.date = self.label('', 'homeSubtitle')
        top.insertWidget(1, self.date)
        actions = QGridLayout()
        actions.setSpacing(14)
        for index, (key, text, icon, callback, tip) in enumerate((
            ('open', '打开笔记', 'open', open_note, '直接打开 NTB / NOTE 原文件，仅记录使用历史'),
            ('pdf', '导入 PDF', 'import', import_pdf, '将 PDF 转为可编辑的 NTB 笔记，保留原 PDF'),
            ('new', '新建笔记', 'new_note', new_note, '选择纸张和底页，新建 NTB 笔记'),
        )):
            button = QToolButton()
            button.setText(text)
            button.setIcon(tool_icon(icon, '#ffffff' if key == 'new' else '#2860d9'))
            button.setIconSize(QSize(20, 20))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setObjectName('homePrimary' if key == 'new' else 'homeAction')
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMinimumHeight(40)
            button.setToolTip(tip)
            button.clicked.connect(callback)
            self.actions[key] = button
            actions.addWidget(button, 0, index)
            actions.setColumnStretch(index, 1)
        upper.addLayout(actions)
        content.addWidget(self.upper)
        heading = QHBoxLayout()
        self.heading = self.label('最近打开的笔记', 'homeHeading')
        heading.addWidget(self.heading)
        heading.addStretch()
        self.count = self.label('', 'homeSubtitle')
        heading.addWidget(self.count)
        content.addLayout(heading)
        controls = QHBoxLayout()
        self.select_all = QCheckBox('全选')
        self.select_all.setTristate(True)
        self.select_all.clicked.connect(self.toggle_all)
        controls.addWidget(self.select_all)
        self.selection_count = self.label('', 'homeSubtitle')
        controls.addWidget(self.selection_count)
        controls.addStretch()
        self.sort = QComboBox()
        self.sort.addItems(['最近打开', '名称升序', '名称降序'])
        self.sort.setAccessibleName('笔记排序')
        self.sort.currentIndexChanged.connect(self.refresh_notes)
        controls.addWidget(self.sort)
        self.view_mode = QComboBox()
        self.view_mode.addItems(['封面视图', '列表视图'])
        self.view_mode.setAccessibleName('笔记显示方式')
        self.view_mode.currentIndexChanged.connect(self.layout_cards)
        controls.addWidget(self.view_mode)
        content.addLayout(controls)
        self.notes = DraggableNotebookList(self)
        self.notes.setObjectName('homeNotes')
        self.notes.viewport().installEventFilter(self)
        self.notes.setViewMode(QListView.ViewMode.IconMode)
        self.notes.setResizeMode(QListView.ResizeMode.Adjust)
        self.notes.setMovement(QListView.Movement.Static)
        self.notes.setWrapping(True)
        self.notes.setSpacing(10)
        self.notes.setItemDelegate(NotebookCard(self.notes))
        self.notes.itemDelegate().menuRequested.connect(self.note_menu_at)
        self.notes.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.notes.customContextMenuRequested.connect(self.note_menu_at)
        self.notes.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.notes.itemActivated.connect(lambda item: self.open_selection() if not self.notes.batch else None)
        self.notes.currentItemChanged.connect(self.selection_changed)
        self.notes.batchChanged.connect(self.selection_changed)
        content.addWidget(self.notes, 1)
        self.empty = self.label('还没有打开过笔记，点击上方入口开始。', 'homeSubtitle')
        self.empty.setWordWrap(True)
        content.addWidget(self.empty)
        footer = QHBoxLayout()
        self.selection_hint = self.label('勾选笔记可批量整理', 'homeSubtitle')
        footer.addWidget(self.selection_hint)
        self.apply_category = QToolButton()
        self.apply_category.setText('更改分类')
        self.apply_category.setObjectName('homeBatchAction')
        self.apply_category.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.apply_category.setIcon(tool_icon('pages'))
        self.apply_category.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.classify_menu = QMenu(self.apply_category)
        self.classify_menu.aboutToShow.connect(self.populate_classify_menu)
        self.apply_category.setMenu(self.classify_menu)
        footer.addWidget(self.apply_category)
        self.delete_notes = QPushButton('删除笔记')
        self.delete_notes.setObjectName('homeDelete')
        self.delete_notes.setIcon(tool_icon('delete','#bf5661'))
        self.delete_notes.setToolTip('将选中的笔记文件移至回收站')
        self.delete_notes.clicked.connect(self.delete_selected)
        footer.addWidget(self.delete_notes)
        self.cancel_select = QPushButton('取消选择')
        self.cancel_select.clicked.connect(self.notes.cancel_selection)
        footer.addWidget(self.cancel_select)
        footer.addStretch()
        self.open_selected = QPushButton('打开选中笔记')
        self.open_selected.clicked.connect(self.open_selection)
        footer.addWidget(self.open_selected)
        content.addLayout(footer)
        self.result = self.label(library.storage_notice, 'homeSubtitle')
        self.result.setWordWrap(True)
        content.addWidget(self.result)
        for combo in (self.sort,self.view_mode):
            popup = QListView(combo)
            popup.setItemDelegate(QStyledItemDelegate(popup))
            popup.setUniformItemSizes(True)
            popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            combo.setView(popup)
            combo.setMaxVisibleItems(10)
            combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.date_timer = QTimer(self)
        self.date_timer.setInterval(60000)
        self.date_timer.timeout.connect(self.refresh_date)
        self.date_timer.start()
        self.file_timer = QTimer(self)
        self.file_timer.setInterval(3000)
        self.file_timer.timeout.connect(self.refresh_missing_files)
        self.file_timer.start()
        self.refresh_date()
        self.refresh()

    def refresh_date(self):
        date = QDate.currentDate()
        self.date.setText(date.toString('M月d日') + '  星期' + '一二三四五六日'[date.dayOfWeek() - 1])

    def refresh_missing_files(self):
        if self.isVisible() and self.notes.isEnabled():
            # Refresh on presence changes only; retain the index for disconnected drives.
            present = {identity(entry['path']) for entry in self.library.entries()}
            if present != getattr(self, '_present_paths', present):
                self.refresh_notes()
                self.changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'notes'):
            self.sidebar.setFixedWidth(180 if self.width() < 900 else 220)
            QTimer.singleShot(0, self.layout_cards)

    def layout_cards(self):
        compact = self.view_mode.currentIndex() == 1 or self.filter == '@all'
        if self.notes.property('compactList') != compact:
            self.notes.setProperty('compactList',compact)
            self.notes.style().unpolish(self.notes)
            self.notes.style().polish(self.notes)
        if compact:
            self.notes.setViewMode(QListView.ViewMode.ListMode)
            self.notes.setWrapping(False)
            self.notes.setSpacing(2)
            self.notes.setGridSize(QSize())
            for row in range(self.notes.count()):
                item = self.notes.item(row)
                entry = item.data(int(Qt.ItemDataRole.UserRole) + 1) or {}
                item.setSizeHint(QSize(180, 44 if entry.get('group') else 68))
            self.notes.viewport().update()
            return
        self.notes.setViewMode(QListView.ViewMode.IconMode)
        self.notes.setSpacing(10)
        self.notes.setWrapping(True)
        width = self.notes.viewport().width()
        columns = max(1, min(4, width // 210))
        card_width = max(145, min(250, (width - 24 * columns) // columns))
        card_height = round(card_width * .72) + 78
        size = QSize(card_width, card_height)
        self.notes.setGridSize(QSize(size.width() + 14, size.height() + 14))
        for index in range(self.notes.count()):
            self.notes.item(index).setSizeHint(size)

    def eventFilter(self, watched, event):
        if (hasattr(self, 'notes') and watched is self.notes.viewport()
                and event.type() == QEvent.Type.Resize):
            QTimer.singleShot(0, self.layout_cards)
        return super().eventFilter(watched, event)

    @staticmethod
    def label(text, name):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setObjectName(name)
        return label

    def refresh(self):
        self.categories.blockSignals(True)
        self.categories.clear()
        choices = [('@home', '首页'), ('@all', '笔记'), ('@none', '未分类')]
        choices += [(key, name) for key, name, depth in self.library.category_rows()]
        if self.filter not in dict(choices):
            self.filter = '@home'
        for index, (key, name) in enumerate(choices):
            if index == 2:
                heading = QListWidgetItem('分类')
                heading.setFlags(Qt.ItemFlag.NoItemFlags)
                self.categories.addItem(heading)
            child = self.library.category_parents.get(key)
            label = name if key in ('@home', '@all') else ('    └  ' if child else '●  ') + name
            item = QListWidgetItem(label)
            if key in ('@home', '@all'):
                item.setIcon(tool_icon('pages' if key == '@home' else 'new_note', '#42669b'))
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(self.library.category_label(key) if key in self.library.categories else name)
            item.setData(Qt.ItemDataRole.AccessibleTextRole,
                         self.library.category_label(key) if key in self.library.categories else name)
            self.categories.addItem(item)
            if key == self.filter:
                self.categories.setCurrentItem(item)
        self.categories.blockSignals(False)
        self.refresh_notes()

    def refresh_notes(self):
        if not hasattr(self, 'notes'):return
        selected = {identity(p) for p in self.selected_paths()}
        current = self.selected_path()
        anchor = self.notes.item(self.notes.anchor) if self.notes.anchor is not None and self.notes.anchor < self.notes.count() else None
        anchor_path = identity(anchor.data(Qt.ItemDataRole.UserRole)) if anchor else None
        batch = self.notes.batch
        scope = (self.filter, self.search.text().strip().casefold())
        if scope != self._scope:
            selected = set()
            batch = False
            self.notes.anchor = None
            current = anchor_path = None
        self._scope = scope
        self.notes.blockSignals(True)
        self.notes.clear()
        self.notes.anchor = None
        search = self.search.text().strip().casefold()
        recent = {identity(path) for path in read_recent_files(self.library.settings)}
        entries = self.library.entries()
        if self.sort.currentIndex():
            entries.sort(key=lambda e: (Path(e['path']).stem.casefold(),identity(e['path'])),
                         reverse=self.sort.currentIndex() == 2)
        self._present_paths = {identity(entry['path']) for entry in entries}
        category_scope = {self.filter, *self.library.child_categories(self.filter)}
        visible_entries = []
        for entry in entries:
            path = entry['path']
            category = entry.get('category', '')
            category_name = self.library.category_label(category)
            if self.filter == '@home' and identity(path) not in recent:
                continue
            if self.filter == '@none' and category:
                continue
            if not self.filter.startswith('@') and category not in category_scope:
                continue
            if search and search not in (path + ' ' + category_name).casefold():
                continue
            visible_entries.append(entry)
        rows = visible_entries
        if self.filter == '@all':
            rows = []
            grouped = {}
            for entry in visible_entries:
                category = entry.get('category', '')
                grouped.setdefault(category if category in self.library.categories else '', []).append(entry)
            for key, name, depth in [*self.library.category_rows(), ('', '未分类', 0)]:
                group_entries = grouped.get(key, [])
                total = len(group_entries) + sum(len(grouped.get(child, [])) for child in self.library.child_categories(key))
                if search and not total:
                    continue
                rows.append(dict(group=True, category=key, title=self.library.category_label(key),
                                 count=total, depth=depth))
                rows.extend(group_entries)
        for entry in rows:
            if entry.get('group'):
                item = QListWidgetItem(entry['title'])
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
                item.setData(int(Qt.ItemDataRole.UserRole) + 1, entry)
                item.setToolTip('将笔记拖到此标题，移入「' + entry['title'] + '」')
                self.notes.addItem(item)
                continue
            path = entry['path']
            category_name = self.library.category_label(entry.get('category', ''))
            value = Path(path)
            stamp = entry.get('opened', 0)
            when = datetime.fromtimestamp(stamp).strftime('%Y-%m-%d') if stamp else '历史记录'
            item = QListWidgetItem(value.stem)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setData(int(Qt.ItemDataRole.UserRole) + 1, {**entry, 'detail': when + ' · ' + category_name,
                'when': when, 'category_name': category_name, 'managed': is_managed(path)})
            item.setToolTip(path + '\n' + category_name)
            self.notes.addItem(item)
            if identity(path) in selected:
                item.setSelected(True)
            if anchor_path == identity(path):
                self.notes.anchor = self.notes.row(item)
            if current and identity(current) == identity(path):
                from PyQt6.QtCore import QItemSelectionModel
                self.notes.selectionModel().setCurrentIndex(self.notes.indexFromItem(item),QItemSelectionModel.SelectionFlag.NoUpdate)
        self.notes.batch = batch and bool(self.notes.selectedItems())
        self.notes.blockSignals(False)
        names = {'@home': '最近打开的笔记', '@all': '全部笔记', '@none': '未分类'}
        self.top_bar.setVisible(self.filter == '@home')
        self.upper.setVisible(self.filter == '@home')
        self.view_mode.setVisible(self.filter != '@all')
        title = names.get(self.filter, self.library.category_label(self.filter))
        self.breadcrumb.setText('首页' if self.filter == '@home' else title)
        self.heading.setText(title)
        self.count.setText(f'{self.note_count()} 本笔记')
        self.empty.setVisible(not self.note_count())
        self.empty.setText('没有找到匹配的笔记。' if search else '暂无笔记，可在首页打开、导入或新建。')
        self.selection_changed()
        self.layout_cards()

    def selected_path(self):
        item = self.notes.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_paths(self):
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.notes.selectedItems()
                if item.data(Qt.ItemDataRole.UserRole)]

    def note_count(self):
        return sum(bool(self.notes.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.notes.count()))

    def toggle_all(self):
        if len(self.selected_paths()) == self.note_count():
            self.notes.cancel_selection()
        else:
            self.notes.selectAll()

    def selection_changed(self, *_):
        paths = self.selected_paths()
        path = paths[0] if len(paths) == 1 else None
        count = len(paths)
        self.selection_hint.setVisible(not count)
        self.apply_category.setVisible(bool(count))
        self.apply_category.setEnabled(bool(count) and not self._busy)
        self.delete_notes.setVisible(bool(count))
        self.delete_notes.setEnabled(bool(count) and not self._busy)
        self.open_selected.setEnabled(bool(path) and not self.notes.batch and not self._busy)
        self.cancel_select.setVisible(bool(count))
        self.selection_count.setText(f'已选择 {count} 本' if count else '')
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(Qt.CheckState.Unchecked if not count else
            Qt.CheckState.Checked if count == self.note_count() else Qt.CheckState.PartiallyChecked)
        self.select_all.setEnabled(bool(self.note_count()) and not self._busy)
        self.select_all.blockSignals(False)

    def open_selection(self):
        paths = self.selected_paths()
        path = paths[0] if len(paths) == 1 and not self.notes.batch else None
        if path:
            self.open_path(path)

    def filter_changed(self, current, previous):
        if current:
            self.filter = current.data(Qt.ItemDataRole.UserRole)
            self.refresh_notes()

    def mutate(self, callback):
        try:
            callback()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '无法整理笔记', str(exc))
        self.refresh()
        self.changed.emit()

    def note_menu_at(self, position):
        item = self.notes.itemAt(position)
        if item is None or not item.data(Qt.ItemDataRole.UserRole) or not self.notes.isEnabled() or self.notes.batch:
            return
        self.notes.setCurrentItem(item)
        from .manager import NotebookMenu
        menu = NotebookMenu(self, item.data(Qt.ItemDataRole.UserRole))
        menu.exec(self.notes.viewport().mapToGlobal(position))
        menu.deleteLater()

    def add_category(self, checked=False, *, parent_key=None):
        from .dialogs import NewCategoryDialog
        if parent_key is None:
            parent_key = (self.library.category_parents.get(self.filter, self.filter)
                          if self.filter in self.library.categories else '')
        dialog = NewCategoryDialog(self,self.library,parent_key)
        try:
            if dialog.exec():
                self.refresh()
                self.changed.emit()
                self.result.setText(f'已创建分类「{self.library.category_label(dialog.created_key)}」。')
        finally:
            dialog.deleteLater()

    def manage_categories(self):
        from .dialogs import CategoryManagerDialog
        dialog = CategoryManagerDialog(self, self.library, self.filter)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
            self.refresh()
            self.changed.emit()

    def move_category(self, key):
        from .dialogs import MoveCategoryDialog
        dialog = MoveCategoryDialog(self, self.library, key)
        try:
            if dialog.exec():
                self.refresh()
                self.changed.emit()
        finally:
            dialog.deleteLater()

    def build_category_menu(self, key=''):
        menu = QMenu(self)
        if not key:
            menu.addAction('新建分类', lambda: self.add_category(parent_key=''))
            return menu
        if not self.library.category_parents.get(key):
            menu.addAction('新建二级分类', lambda: self.add_category(parent_key=key))
        menu.addAction('重命名分类', lambda: self.rename_category(key))
        move = menu.addAction('移动分类…', lambda: self.move_category(key))
        remove = menu.addAction('移除分类（保留笔记）', lambda: self.mutate(lambda: self.library.remove_category(key)))
        if self.library.child_categories(key):
            move.setEnabled(False)
            remove.setEnabled(False)
            move.setToolTip('请先移动二级分类，避免产生第三级。')
            remove.setToolTip('请先移动或移除二级分类。')
            menu.setToolTipsVisible(True)
        return menu

    def rename_category(self, key):
        name, accepted = QInputDialog.getText(self, '重命名分类', '分类名称', text=self.library.categories[key])
        if accepted:
            self.mutate(lambda: self.library.rename_category(key, name))

    def category_menu(self, position):
        if self._busy or not self.categories.isEnabled():
            return
        item = self.categories.itemAt(position)
        key = item.data(Qt.ItemDataRole.UserRole) if item else ''
        if key and key not in self.library.categories:
            return
        if key:
            self.categories.setCurrentItem(item)
        menu = self.build_category_menu(key)
        try:
            menu.exec(self.categories.viewport().mapToGlobal(position))
        finally:
            menu.deleteLater()

    def set_busy(self, busy):
        busy = busy or getattr(self,'batch_job',None) is not None
        self._busy = busy
        for button in self.actions.values():
            button.setEnabled(not busy)
        self.notes.setEnabled(not busy)
        for widget in (self.search,self.categories,self.sort,self.view_mode,self.add_category_button,self.manage_categories_button,self.cancel_select):
            widget.setEnabled(not busy)
        self.selection_changed()

    def populate_classify_menu(self):
        self.classify_menu.clear()
        selected = {self.library.data['notes'].get(identity(path),{}).get('category','')
                    for path in self.selected_paths()}
        populate_category_menu(self.classify_menu, self.library, self.classify_selected, selected)

    def classify_selected(self, category):
        from .batch import classify_dialog
        if not self._busy:
            classify_dialog(self,category)

    def delete_selected(self):
        from .batch import delete_dialog
        delete_dialog(self)

    def queue_drop_classification(self, paths, category):
        if self._busy or getattr(self, '_drop_pending', False):
            return
        self._drop_pending = True
        paths = tuple(dict.fromkeys(paths))

        def apply():
            self._drop_pending = False
            if self._busy:
                return
            if category and category not in self.library.categories:
                self.result.setText('目标分类已不存在，请重新拖动。')
                return
            from .batch import ClassificationJob
            ClassificationJob(self, paths, category).start()

        QTimer.singleShot(0, apply)


STYLE = """
QWidget#desktopHome { background:#f6f8fc; color:#1e293b; }
QWidget#homeSidebar { background:#edf3fd; border-right:1px solid #dce5f4; }
QLabel { color:#263c5c; background:transparent; }
QLabel#homeBrand { font-size:18px; font-weight:700; color:#17478e; }
QLabel#homeHeading { font-size:22px; font-weight:700; color:#1c3558; }
QLabel#homeDate { font-size:27px; font-weight:700; color:#19365f; }
QLabel#homeSubtitle { color:#73849e; font-size:12px; }
QPushButton { background:#ffffff; color:#2b5fb9; border:1px solid #cbdaf1; border-radius:9px; padding:8px 12px; }
QPushButton:hover { background:#e8f0ff; border-color:#8aaff4; }
QPushButton:disabled { color:#9baac0; border-color:#e0e7f1; background:#f4f7fb; }
QPushButton#homeDelete { color:#bf5661; background:#fff8f8; border-color:#f0d8dc; }
QPushButton#homeDelete:hover { background:#ffeff1; border-color:#dfa5ae; }
QPushButton#homeDelete:focus { border-color:#cc7684; }
QPushButton#homeDelete:disabled { color:#c9abb0; background:#fcf8f9; border-color:#f0e5e7; }
QToolButton#homeAction, QToolButton#homePrimary { background:#ffffff; color:#2458aa; border:1px solid #cddcf4;
 border-radius:8px; padding:8px; font-size:13px; font-weight:600; }
QToolButton#homePrimary { background:#2563eb; border-color:#2563eb; color:#ffffff; }
QToolButton#homeAction:hover { background:#eaf2ff; border-color:#8eb2f5; }
QToolButton#homePrimary:hover { background:#1d53cd; border-color:#1d53cd; }
QToolButton:disabled { color:#94a3b8; background:#eef3fa; border-color:#dee7f4; }
QToolButton#homeBatchAction { background:#e8f0ff; color:#2458aa; border:1px solid #d7e4fa;
 border-radius:8px; padding:8px 30px 8px 12px; font-size:13px; }
QToolButton#homeBatchAction:hover { background:#dce9ff; border-color:#a8c4f3; }
QToolButton#homeBatchAction:focus { border-color:#4d86ee; }
QToolButton#homeBatchAction:disabled { color:#9aa8bd; background:#f1f5fa; border-color:#e0e7f1; }
QToolButton#homeBatchAction::menu-indicator { image:url("__HOME_ARROW__"); width:12px; height:8px;
 subcontrol-origin:padding; subcontrol-position:right center; right:10px; }
QListWidget#homeCategories { background:transparent; color:#526988; border:0; outline:0; font-size:14px; }
QListWidget#homeCategories::item { padding:11px 9px; border-radius:9px; }
QListWidget#homeCategories::item:selected { background:#dce8ff; color:#215bd0; }
QListWidget#homeCategories::item:hover { background:#e4edfc; }
QListWidget#homeCategories::item:disabled { color:#8493ab; }
QListWidget#homeNotes { background:transparent; border:0; outline:0; }
QListWidget#homeNotes[compactList="true"] { background:#ffffff; border:1px solid #e0e7f2;
 border-radius:10px; padding:4px; }
QListWidget#homeNotes QScrollBar:vertical { background:transparent; width:9px; margin:3px 0; }
QListWidget#homeNotes QScrollBar::handle:vertical { background:#c3cfe1; border-radius:4px; min-height:28px; }
QListWidget#homeNotes QScrollBar::handle:vertical:hover { background:#9eb5d6; }
QListWidget#homeNotes QScrollBar::add-line:vertical, QListWidget#homeNotes QScrollBar::sub-line:vertical { height:0; }
QListWidget#homeNotes QScrollBar::add-page:vertical, QListWidget#homeNotes QScrollBar::sub-page:vertical { background:transparent; }
QLineEdit#homeSearch { background:#ffffff; color:#334b6d; border:1px solid #cbdaf1; border-radius:10px; padding:10px; }
QLineEdit#homeSearch:focus { border-color:#6598ee; }
QComboBox { background:#ffffff; color:#334b6d; border:1px solid #d3def0; border-radius:8px;
 padding:8px 34px 8px 12px; min-height:18px; font-size:13px; }
QComboBox:hover { background:#f4f8ff; border-color:#95b6ed; }
QComboBox:focus, QComboBox:on { background:#ffffff; border-color:#4d86ee; }
QComboBox:disabled { background:#f1f5fa; color:#9aa8bd; border-color:#e0e7f1; }
QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:top right;
 width:30px; border:0; background:transparent; }
QComboBox::down-arrow { image:url("__HOME_ARROW__"); width:12px; height:8px; }
QComboBox::down-arrow:disabled { image:none; }
QComboBox QAbstractItemView { background:#ffffff; color:#334b6d; border:1px solid #d3def0;
 border-radius:8px; padding:5px; outline:0; selection-background-color:#e8f0ff;
 selection-color:#215bd0; font-size:13px; }
QComboBox QAbstractItemView::item { min-height:26px; padding:5px 10px; border-radius:5px; }
QComboBox QAbstractItemView::item:hover { background:#f0f5ff; color:#215bd0; }
QComboBox QAbstractItemView::item:selected { background:#e2ecff; color:#215bd0; }
QMenu { background:#ffffff; color:#334b6d; border:1px solid #d3def0; border-radius:8px; padding:6px; }
QMenu::item { padding:9px 20px; border-radius:5px; }
QMenu::item:selected { background:#e8f0ff; color:#215bd0; }
QMenu::item:disabled { color:#9aa8bd; }
QMenu::separator { height:1px; background:#e7edf6; margin:5px 8px; }
"""
