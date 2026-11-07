"""Page thumbnails and document-local content clipboard, rendered on demand."""
from infrastructure.ne_diagnostics import exception as diagnostic_exception
from PyQt6.QtCore import Qt,QSize,QPoint,QRectF,QTimer
from PyQt6.QtGui import QIcon,QPixmap,QPainter,QKeySequence
from ui.ne_toolbar import tool_icon
from ui.ne_style import style_menu
from ui.ne_pagecards import PageCardDelegate
from PyQt6.QtWidgets import QDockWidget,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QToolButton,QListWidget,QListWidgetItem,QMenu,QLineEdit,QAbstractItemView,QPushButton,QMessageBox


class PageList(QListWidget):
    def __init__(self,sidebar):
        super().__init__();self.sidebar=sidebar
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            index=self.indexAt(event.position().toPoint())
            if self.sidebar.batch_mode:
                if index.isValid():
                    item=self.item(index.row())
                    item.setSelected(not item.isSelected())
                event.accept();return
            if index.isValid() and self.itemDelegate().moreRect(self.visualRect(index)).contains(event.position()):
                self.setCurrentRow(index.row());self.setFocus()
                self.sidebar.popup_menu(self.viewport().mapToGlobal(event.position().toPoint()))
                event.accept();return
        super().mousePressEvent(event)
    def mouseReleaseEvent(self,event):
        if self.sidebar.batch_mode and event.button()==Qt.MouseButton.LeftButton:
            event.accept();return
        super().mouseReleaseEvent(event)
    def mouseDoubleClickEvent(self,event):
        if self.sidebar.batch_mode:
            index=self.indexAt(event.position().toPoint())
            if index.isValid():
                self.sidebar.owner.go_page(index.row()+1)
                self.sidebar.owner.statusBar().showMessage(f'跳转至第 {index.row()+1} 页（批量勾选保持不变）')
            event.accept();return
        super().mouseDoubleClickEvent(event)
    def resizeEvent(self,event):
        # Let Qt finish the QListWidget viewport/layout resize first.
        # Calling doItemsLayout() synchronously from this callback can
        # trigger another resizeEvent and overflow the C/Python stack.
        super().resizeEvent(event)
        if hasattr(self.sidebar,'timer'):
            # The existing timer handles thumbnail rendering on the next
            # event-loop turn, without forcing another layout immediately.
            self.sidebar.timer.start(80)
    def keyPressEvent(self,event):
        s=self.sidebar
        manager=getattr(s.owner,'shortcut_manager',None)
        def matches(name):
            return manager.matches(name,event) if manager else False
        if s.batch_mode:
            if matches('page_all'):
                s.select_all_pages();event.accept();return
            if matches('page_invert'):
                s.invert_pages();event.accept();return
            if event.key()==Qt.Key.Key_Escape:
                s.set_batch_mode(False);event.accept();return
            if event.key()==Qt.Key.Key_Space:
                item=self.currentItem()
                if item:item.setSelected(not item.isSelected())
                event.accept();return
            if matches('page_delete_batch'):
                s.delete_selected_pages();event.accept();return
            super().keyPressEvent(event);return
        for name,operation in (('page_copy','copy'),('page_cut','cut'),('page_paste','paste'),('page_delete','delete')):
            if matches(name):
                if s.actions[operation].isEnabled():s.actions[operation].trigger()
                else:s.explain_unavailable()
                event.accept();return
        super().keyPressEvent(event)



class PageSidebar(QDockWidget):
    def __init__(self,owner):
        super().__init__('页面',owner);self.owner=owner;self.clipboard=None;self.canvas=None;self.rendered=set();self.batch_mode=False
        self.setObjectName('pageSidebar');self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea|Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setMinimumWidth(228);self.expanded=False
        title=QWidget();title.setFixedHeight(0);self.setTitleBarWidget(title)
        box=QWidget();box.setObjectName('pageSidebarContents')
        box.setStyleSheet("""
            QWidget#pageSidebarContents { background:#f8fafc; border-left:1px solid #e2e8f0; }
            QLabel { color:#334155; background:transparent; border:0; }
            QToolButton { color:#475569; border:0; background:transparent; border-radius:8px; padding:6px; }
            QToolButton:hover { background:#e8eefb; }
            QToolButton:disabled { color:#a0a8b5; }
            QPushButton { background:#ffffff; border:1px solid #d8e0eb; color:#334155;
                border-radius:8px; font-size:12px; font-weight:500; padding:8px 6px; }
            QPushButton:hover { background:#edf4ff; border-color:#b4cbf4; color:#1d4ed8; }
            QPushButton:disabled { background:#f1f5f9; border-color:#e2e8f0; color:#94a3b8; }
            QPushButton#batchMode { background:#eef4ff; border-color:#bfdbfe; color:#1d4ed8; font-weight:600; }
            QPushButton#batchMode:hover { background:#dbeafe; }
            QPushButton#deleteBatch { background:#dc2626; border-color:#dc2626; color:#ffffff; font-weight:600; }
            QPushButton#deleteBatch:hover { background:#b91c1c; }
            QPushButton#deleteBatch:disabled { background:#fee2e2; border-color:#fecaca; color:#b91c1c; }
            QWidget#batchControls { background:#ffffff; border:1px solid #dce5f2; border-radius:10px; }
            QLabel#selectionCount { color:#1d4ed8; font-weight:600; }
            QLineEdit { background:white; border:1px solid #e0e4ec; border-radius:18px; padding:9px 10px; color:#334155; font-size:13px; }
            QLineEdit:focus { border-color:#8caaf0; }
            QScrollBar:vertical { width:7px; background:transparent; margin:4px 0; }
            QScrollBar::handle:vertical { background:#cbd2df; border-radius:3px; min-height:32px; }
            QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
            QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background:none; }
        """)
        layout=QVBoxLayout(box);layout.setContentsMargins(0,12,0,8);layout.setSpacing(8)
        title_row=QHBoxLayout();title_row.setContentsMargins(16,0,10,0)
        title_label=QLabel('页面');title_label.setStyleSheet('font-size:15px;font-weight:600;');title_row.addWidget(title_label);title_row.addStretch()
        close=QToolButton();close.setIcon(tool_icon('close'));close.setIconSize(QSize(18,18));close.setFixedSize(30,30)
        close.setToolTip('收起页面侧栏');close.setAccessibleName('收起页面侧栏');close.clicked.connect(lambda:self.set_user_visible(False));title_row.addWidget(close);layout.addLayout(title_row)
        search_row=QHBoxLayout();search_row.setContentsMargins(14,0,14,0)
        self.search=QLineEdit();self.search.setPlaceholderText('查找页码，如 12');self.search.setAccessibleName('查找页码')
        self.search.setClearButtonEnabled(True);self.search.addAction(tool_icon('search'),QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(self.find_page);search_row.addWidget(self.search);layout.addLayout(search_row)
        header=QHBoxLayout();header.setContentsMargins(16,2,14,0)
        self.label=QLabel('所有页面');self.label.setStyleSheet('font-size:14px;font-weight:600;');header.addWidget(self.label);header.addStretch()
        self.count_label=QLabel('0 页');self.count_label.setStyleSheet('color:#8791a0;font-size:12px;');header.addWidget(self.count_label);layout.addLayout(header)
        self.reason_button=QPushButton('页面编辑不可用 · 查看原因')
        self.reason_button.setStyleSheet('QPushButton { color:#9a3412;background:#fff7ed;border:1px solid #fed7aa; text-align:left; padding:9px; }')
        self.reason_button.clicked.connect(self.explain_unavailable)
        self.reason_button.hide()
        layout.addWidget(self.reason_button)
        batch_row=QHBoxLayout();batch_row.setContentsMargins(12,0,12,0)
        self.batch_button=QPushButton('批量选择页面');self.batch_button.setObjectName('batchMode');self.batch_button.setToolTip('多选页面并批量删除；不会改变正常的翻页操作')
        self.batch_button.clicked.connect(lambda:self.set_batch_mode(not self.batch_mode))
        batch_row.addWidget(self.batch_button);layout.addLayout(batch_row)
        self.batch_controls=QWidget();self.batch_controls.setObjectName('batchControls');controls=QVBoxLayout(self.batch_controls)
        controls.setContentsMargins(10,12,10,12);controls.setSpacing(12)
        choices=QHBoxLayout();choices.setContentsMargins(0,0,0,0);choices.setSpacing(6)
        self.all_button=QPushButton('全选');self.none_button=QPushButton('取消全选');self.invert_button=QPushButton('反选')
        for button,callback in ((self.all_button,self.select_all_pages),(self.none_button,self.clear_page_selection),
                                (self.invert_button,self.invert_pages)):
            choices.addWidget(button);button.clicked.connect(callback)
        controls.addLayout(choices)
        remove_row=QHBoxLayout();remove_row.setContentsMargins(0,0,0,0);remove_row.setSpacing(6)
        self.selected_label=QLabel('已选 0 页');self.selected_label.setObjectName('selectionCount');remove_row.addWidget(self.selected_label);remove_row.addStretch()
        self.delete_batch_button=QPushButton('删除所选')
        self.delete_batch_button.setObjectName('deleteBatch')
        self.delete_batch_button.setToolTip('删除选中页面；保存前可撤销，保存后物理清理不再使用的资源')
        self.delete_batch_button.clicked.connect(self.delete_selected_pages);remove_row.addWidget(self.delete_batch_button)
        controls.addLayout(remove_row);layout.addWidget(self.batch_controls)
        self.batch_controls.hide()
        self.menu=QMenu(self);style_menu(self.menu)
        self.explain_action=self.menu.addAction('页面操作不可用？查看原因…',self.explain_unavailable)
        self.explain_separator=self.menu.addSeparator()
        self.actions={}
        entries=[('insert','添加空白页（后）','page_add'),('before','添加空白页（前）','page_add'),None,
                 ('cut','剪切','cut'),('copy','拷贝','copy'),('duplicate','复制页面','pages'),None,
                 ('move_up','上移一页','pages'),('move_down','下移一页','pages'),None,('delete','删除','delete')]
        for entry in entries:
            if entry is None:self.menu.addSeparator();continue
            name,label,icon=entry;action=self.menu.addAction(tool_icon(icon,'#df5151' if name=='delete' else '#475569'),label)
            action.setIconVisibleInMenu(True);action.triggered.connect(lambda checked=False,n=name:self.perform(n));self.actions[name]=action
        # Three explicit destinations share one document-local clipboard. Ctrl+V
        # retains the familiar "insert after" behavior for existing users.
        self.paste_menu=QMenu('粘贴页面',self.menu)
        self.paste_menu.setIcon(tool_icon('paste'))
        self.menu.insertMenu(self.actions['duplicate'],self.paste_menu)
        style_menu(self.paste_menu)
        for name,label in (('paste_before','粘贴到当前页之前'),('paste','粘贴到当前页之后（Ctrl+V）'),
                           ('paste_replace','覆盖当前页')):
            action=self.paste_menu.addAction(tool_icon('paste'),label)
            action.triggered.connect(lambda checked=False,n=name:self.perform(n));self.actions[name]=action
        self.actions['copy'].setToolTip('拷贝完整页面，包括笔迹、图片和背景底页（Ctrl+C，页面列表获得焦点时）')
        self.actions['cut'].setToolTip('拷贝完整页面并移除原页面（Ctrl+X）')
        self.actions['paste_before'].setToolTip('在当前页前插入剪贴板页面，保留当前页')
        self.actions['paste'].setToolTip('在当前页后插入剪贴板页面，保留当前页（Ctrl+V）')
        self.actions['paste_replace'].setToolTip('以剪贴板整页替换当前页（包括笔迹、图片和底图），可通过 Ctrl+Z 撤销')
        # A new blank NOTE page follows this document's native paper template.
        # Background type is NOT chosen per page: for native NTB it belongs to
        # the document (blank / ruled / dotted), and the old choice menu could
        # misleadingly promise a mixed native template that cannot be saved.
        self.menu.addSeparator()
        self.batch_action=self.menu.addAction('批量选择页面…')
        self.batch_action.triggered.connect(lambda checked=False:self.set_batch_mode(True))
        self.menu.aboutToShow.connect(self.refresh)
        self.list=PageList(self);layout.addWidget(self.list,1)
        self.list.setItemDelegate(PageCardDelegate(self));self.list.setSpacing(0);self.list.setUniformItemSizes(False)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet('QListWidget { background:transparent; border:0; outline:0; }')
        self.list.currentRowChanged.connect(self.navigate)
        self.list.itemSelectionChanged.connect(self.update_selected_count)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context_menu)
        footer=QHBoxLayout();footer.setContentsMargins(12,0,12,0)
        current=QToolButton();current.setText('定位当前页');current.setStyleSheet('color:#3769d8;font-size:12px;');current.clicked.connect(self.locate_current);footer.addWidget(current);footer.addStretch()
        self.expand_button=QToolButton();self.expand_button.setIcon(tool_icon('expand'));self.expand_button.setIconSize(QSize(20,20))
        self.expand_button.setToolTip('放大页面预览');self.expand_button.setAccessibleName('放大页面预览');self.expand_button.clicked.connect(self.toggle_expanded)
        footer.addWidget(self.expand_button);layout.addLayout(footer);self.setWidget(box)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.render_visible)
        self.list.verticalScrollBar().valueChanged.connect(lambda:self.timer.start(30))
        self.visibilityChanged.connect(lambda visible:self.timer.start(0) if visible else self.timer.stop())

    def set_user_visible(self, visible):
        """Persist explicit panel choices; home/fullscreen hides are temporary."""
        visible = bool(visible)
        self.setVisible(visible)
        self.owner.color_settings.setValue('view/page_sidebar_visible', visible)
        self.owner.color_settings.sync()
        workspace = getattr(self.owner, 'workspace', None)
        if workspace is not None:workspace.sidebar_visible = visible
        view = getattr(self.owner, 'editor_view', None)
        if view is not None and view.saved is not None:
            geometry, state, _ = view.saved
            view.saved = (geometry, state, visible)

    def popup_menu(self,pos):
        self.refresh();self.menu.popup(pos)
    def find_page(self):
        value=self.search.text().strip();count=self.canvas.page_count if self.canvas else 0
        if not value.isdecimal() or not 1<=int(value)<=count:
            self.owner.statusBar().showMessage(f'请输入 1 到 {count} 的页码');return
        page=int(value)-1;self.owner.go_page(page+1);self.sync(page)
        self.list.scrollToItem(self.list.item(page),QAbstractItemView.ScrollHint.PositionAtCenter);self.timer.start(0)
    def locate_current(self):
        if not self.canvas:return
        page=self.owner.page.value()-1;self.sync(page)
        self.list.scrollToItem(self.list.item(page),QAbstractItemView.ScrollHint.PositionAtCenter);self.timer.start(0)
    def toggle_expanded(self):
        self.expanded=not self.expanded
        width=min(380 if self.expanded else 280,max(228,self.owner.width()-420))
        self.owner.resizeDocks([self],[width],Qt.Orientation.Horizontal)
        self.expand_button.setIcon(tool_icon('collapse' if self.expanded else 'expand'))
        label='恢复预览宽度' if self.expanded else '放大页面预览'
        self.expand_button.setToolTip(label);self.expand_button.setAccessibleName(label)
        self.list.doItemsLayout();self.invalidate()

    def attach(self,canvas):
        tiles=getattr(self,'_tiles_connected',None)
        if tiles is not None:
            try:tiles.thumbnail_ready.disconnect(self.thumbnail_ready)
            except (RuntimeError,TypeError):pass
        self._tiles_connected=None
        self.set_batch_mode(False)
        self.clipboard=None;self.search.clear()
        self.canvas=canvas if hasattr(canvas,'page_count') else None
        if (self.canvas and getattr(self.canvas,'page_edit_error','')
                and getattr(self.owner,'prepared',None)
                and getattr(self.owner.prepared,'bundle_data',None)):
            # Re-check after the canvas/page model has been installed. This
            # also repairs a stale disabled menu when the *actual* 43-page
            # native per-page writer already validates the note successfully.
            from pages.ne_page_access import grant_page_access
            grant_page_access(self.canvas,self.owner.prepared)
        self.reset()
    def reset(self):
        self.set_batch_mode(False)
        core=getattr(getattr(self.canvas,'edits',None),'core',None)
        self._thumbnail_revision=(core.token,core.cursor) if core else (None,0)
        self.timer.stop();self.rendered.clear();self.list.blockSignals(True);self.list.clear()
        count=self.canvas.page_count if self.canvas else 0
        for page in range(count):
            item=QListWidgetItem(f'{page+1}');item.setToolTip(f'第 {page+1} 页 · 点击缩略图浏览，点击 … 或右键管理页面');self.list.addItem(item)
        self.list.blockSignals(False);self.count_label.setText(f'{count} 页');self.sync(self.owner.page.value()-1);self.refresh();self.timer.start(0)
    def sync(self,page):
        if self.batch_mode:return  # Browsing/scrolling must never change checked pages.
        if not self.canvas or not 0<=page<self.list.count():return
        self.list.blockSignals(True);self.list.setCurrentRow(page);self.list.blockSignals(False)
        self.refresh();self.timer.start(30)
    def navigate(self,page):
        if self.batch_mode:return
        if page>=0:self.owner.go_page(page+1);self.refresh();self.timer.start(0)
    def context_menu(self,pos):
        if self.batch_mode:
            menu=QMenu(self);style_menu(menu)
            menu.addAction('全选',self.select_all_pages)
            menu.addAction('取消全选',self.clear_page_selection)
            menu.addAction('反选',self.invert_pages)
            menu.addSeparator()
            delete=menu.addAction('删除选中页面…',self.delete_selected_pages)
            delete.setEnabled(bool(self.list.selectedItems()) and self.delete_batch_button.isEnabled())
            menu.addAction('退出批量选择',lambda:self.set_batch_mode(False))
            menu.exec(self.list.viewport().mapToGlobal(pos));return
        item=self.list.itemAt(pos)
        if not item:return
        self.list.setCurrentItem(item);self.popup_menu(self.list.viewport().mapToGlobal(pos))
    def unavailable_reason(self):
        w=self.owner
        if not self.canvas or not w.prepared:
            return '尚未打开笔记，请先打开原始 NTB 文件。'
        if w.worker or w.saver:
            return '当前正在打开或保存文件，请等待操作完成。'
        if not w.prepared.path.lower().endswith('.ntb'):
            return '当前文件是 NOTE 或 PDF，只支持阅读。添加、粘贴、删除及批量删除页面需要打开原始 NTB 文件。'
        if not hasattr(self.canvas,'edits') or not hasattr(self.canvas.edits,'insert'):
            return '此文件尚未提供可安全写入的页面编辑模型。'
        return getattr(self.canvas,'page_edit_error','') or ''

    def explain_unavailable(self):
        reason=self.unavailable_reason()
        if reason:
            QMessageBox.information(self,'页面操作暂不可用',reason+'\n\n为了保护原始笔记，程序不会绕过结构校验强制删除页面。')

    def refresh(self):
        w=self.owner
        if hasattr(w,'seamless_pages_action'):
            w.seamless_pages_action.setEnabled(bool(self.canvas and not w.worker and not w.saver))
        editable=bool(self.canvas and w.prepared and w.prepared.path.lower().endswith('.ntb')
                      and not w.worker and not w.saver
                      and not getattr(self.canvas,'page_edit_error','')
                      and hasattr(getattr(self.canvas,'edits',None),'insert'))
        model=getattr(self.canvas,'edits',None)
        # During deletion/undo the model order changes before the old list
        # is rebuilt; selectionChanged can refresh this menu in between.
        count=(len(model.order) if hasattr(model,'order') else
               self.canvas.page_count if self.canvas else 0)
        current=max(0,min(self.list.currentRow(),count-1))
        minimum=getattr(model,'min_pages',1)
        maximum=getattr(model,'max_pages',None)
        capacity=bool(editable and (maximum is None or count<maximum))
        known=bool(editable and model and model.is_known_page(current))
        for name,action in self.actions.items():
            action.setEnabled(False)
        # Non-destructive full-page duplicate and new blank page should not
        # require the *selected source page* to be known. A compatible writer
        # is still required; otherwise a Save would silently fail.
        for name in ('insert','before','duplicate'):
            self.actions[name].setEnabled(capacity)
        self.actions['copy'].setEnabled(known)
        self.actions['cut'].setEnabled(bool(known and model.can_cut_page(current)))
        self.actions['delete'].setEnabled(bool(known and model.can_delete_pages((current,))))
        clipboard_known=bool(self.clipboard and self.clipboard.get('known_page'))
        can_paste=bool(capacity and known and clipboard_known)
        self.paste_menu.setEnabled(bool(can_paste or (editable and known and clipboard_known)))
        for name in ('paste','paste_before'):
            self.actions[name].setEnabled(can_paste)
        self.actions['paste_replace'].setEnabled(bool(editable and known and clipboard_known))
        self.actions['move_up'].setEnabled(editable and current>0)
        self.actions['move_down'].setEnabled(editable and current<count-1)
        self.batch_action.setEnabled(editable)
        self.batch_button.setEnabled(editable or self.batch_mode)
        reason=self.unavailable_reason()
        self.reason_button.setVisible(bool(self.canvas and reason))
        self.reason_button.setToolTip(reason)
        self.explain_action.setVisible(bool(reason))
        self.explain_separator.setVisible(bool(reason))
        chosen=self.selected_page_indices()
        self.delete_batch_button.setEnabled(bool(editable and model and model.can_delete_pages(chosen)))
        self.list.setEnabled(not w.worker and not w.saver)
        self.search.setEnabled(bool(self.canvas and not w.worker and not w.saver))
        if not w.worker and not w.saver:self.timer.start(30)
        self.list.setToolTip(getattr(self.canvas,'page_edit_error','') if self.canvas else '打开笔记以浏览页面')
        if 0<=current<self.list.count() and model and not self.batch_mode:
            known_label='已知：可剪切、拷贝和删除' if model.is_known_page(current) else '未知来源页：可新增同底页及复制整页（如已验证写入器），删除等需验证'
            if w.prepared and w.prepared.path.lower().endswith('.note'):
                known_label='NOTE 只读 · 可浏览和导出'
            self.list.item(current).setToolTip(f'第 {current+1} 页 · '+known_label)
    def set_batch_mode(self,enabled):
        enabled=bool(enabled and self.canvas and self.canvas.page_count and
                     self.owner.prepared and self.owner.prepared.path.lower().endswith('.ntb') and
                     not self.owner.worker and not self.owner.saver and
                     not getattr(self.canvas,'page_edit_error',''))
        if enabled==self.batch_mode:return
        if enabled and not self.isVisible():self.show()
        self.batch_mode=enabled
        # Do not leak the former current-page selection into the batch.
        self.list.clearSelection()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection if enabled else
                                   QAbstractItemView.SelectionMode.SingleSelection)
        self.list.clearSelection()
        self.batch_button.setText('完成选择  ✓' if enabled else '批量选择页面')
        self.batch_controls.setVisible(enabled)
        for i in range(self.list.count()):
            self.list.item(i).setToolTip('单击选择或取消选择第 %d 页' % (i+1) if enabled else
                                         '第 %d 页 · 点击缩略图浏览，点击 … 或右键管理页面' % (i+1))
        self.update_selected_count()
        self.list.viewport().update()
        if not enabled and self.canvas:self.sync(self.owner.page.value()-1)
        self.refresh()

    def selected_page_indices(self):
        return sorted(self.list.row(item) for item in self.list.selectedItems()) if self.batch_mode else []

    def update_selected_count(self):
        pages=self.selected_page_indices()
        self.selected_label.setText(f'已选 {len(pages)} / {self.list.count()} 页')
        if hasattr(self,'delete_batch_button'):
            model=getattr(self.canvas,'edits',None)
            self.delete_batch_button.setEnabled(bool(model and hasattr(model,'can_delete_pages')
                and model.can_delete_pages(pages) and
                self.canvas and not getattr(self.canvas,'page_edit_error','')
                and not self.owner.worker and not self.owner.saver))
        self.list.viewport().update()

    def select_all_pages(self):
        if not self.batch_mode:return
        self.list.blockSignals(True)
        try:
            for i in range(self.list.count()):self.list.item(i).setSelected(True)
        finally:self.list.blockSignals(False)
        self.update_selected_count()

    def clear_page_selection(self):
        if not self.batch_mode:return
        self.list.clearSelection();self.update_selected_count()

    def invert_pages(self):
        if not self.batch_mode:return
        previous=set(self.selected_page_indices())
        self.list.blockSignals(True)
        try:
            for i in range(self.list.count()):self.list.item(i).setSelected(i not in previous)
        finally:self.list.blockSignals(False)
        self.update_selected_count()

    def delete_selected_pages(self):
        if not self.batch_mode or not self.delete_batch_button.isEnabled():return
        pages=self.selected_page_indices()
        if not pages:return
        minimum=getattr(self.canvas.edits,'min_pages',1)
        if self.canvas.page_count-len(pages)<minimum:
            QMessageBox.warning(self,'无法删除这些页面',f'笔记至少必须保留 {minimum} 页，请减少所选页数。')
            return
        if not self.canvas.edits.can_delete_pages(pages):
            QMessageBox.warning(self,'无法删除这些页面','所选页面包含未知结构；请只选择已经验证或自己添加的页面。')
            return
        summary=(', '.join(str(i+1) for i in pages[:12])+('…' if len(pages)>12 else ''))
        answer=QMessageBox.question(self,'确认批量删除',
            f'确定删除选中的 {len(pages)} 页吗？\n页码：{summary}\n\n保存前可按 Ctrl+Z 一次撤销。保存或另存为后，将清理不再引用的素材；已物理清理的页面无法再撤销。',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        self.canvas.finishGesture()
        if self.canvas.edits.delete_many(pages):
            # applyPageSpecs -> pagesChanged -> reset also exits batch mode.
            self.canvas.editsChanged.emit()
            self.owner.statusBar().showMessage(f'已删除 {len(pages)} 页（Ctrl+Z 可一次恢复）')

    def perform(self,name):
        if not self.actions[name].isEnabled():
            return
        c=self.canvas;c.finishGesture();page=max(0,self.list.currentRow());model=c.edits
        try:
            if name=='copy':
                if not model.can_copy_page(page):raise ValueError('未知页面不可拷贝到剪贴板')
                self.clipboard=model.snapshot(page)
            elif name=='cut':
                if not model.can_cut_page(page):raise ValueError('当前页面不允许剪切')
                self.clipboard=model.snapshot(page);model.delete(page)
            elif name in ('paste_before','paste','paste_replace'):
                if not self.clipboard or not self.clipboard.get('known_page'):
                    raise ValueError('剪贴板内容未知，不能粘贴')
                if not model.is_known_page(page):raise ValueError('未知页面不能作为粘贴目标')
                if name=='paste_before':model.insert(page,self.clipboard)
                elif name=='paste':model.insert(page+1,self.clipboard)
                else:model.replace_page(page,self.clipboard)
            elif name=='duplicate':model.insert(page+1,model.snapshot(page))
            elif name=='move_up':model.move(page,page-1)
            elif name=='move_down':model.move(page,page+1)
            elif name in ('before','insert'):
                model.insert(page+(name=='insert'),paper_kind='follow')
            elif name=='delete':model.delete(page)
        except (ValueError,IndexError,KeyError) as exc:
            QMessageBox.warning(self,'页面操作受到保护',str(exc));return
        if name!='copy':c.editsChanged.emit()
        self.refresh()
        if name in ('copy','cut'):
            self.owner.statusBar().showMessage('已拷贝已知页面（含笔迹、图片和底页），可在其他已知页面前后粘贴')
    def invalidate(self,*,ink_change=False):
        pages=None
        core=getattr(getattr(self.canvas,'edits',None),'core',None)
        if core:
            token,cursor=getattr(self,'_thumbnail_revision',(None,0))
            if ink_change and core.cursor==cursor+1 and (core.history[cursor-1][0] if cursor else None)==token:
                op=core.history[core.cursor-1][1]
                draws=(op,) if op['kind']=='draw' else op.get('_append_draws',())
                if draws:
                    owners={draw['page'] for draw in draws}
                    pages={p for p,key in enumerate(self.canvas.edits.order) if key in owners}
            self._thumbnail_revision=(core.token,core.cursor)
        if pages is not None:
            # Retain the previous icon until its replacement is ready. Debounce
            # rapid taps and leave every untouched page's thumbnail cached.
            self.rendered.difference_update(pages);self.timer.start(300)
            return
        for page in self.rendered:
            item=self.list.item(page)
            if item:item.setIcon(QIcon())
        self.rendered.clear();self.timer.start(100)
    def thumbnail_ready(self,page):
        if self.canvas and self.sender() is getattr(self.canvas,'view_tiles',None):self.timer.start(0)

    def render_visible(self):
        c=self.canvas;w=self.owner
        if not c or not self.isVisible() or w.worker or w.saver:return
        if (c.edits.pending or getattr(c,'_continuous_draw',None) is not None or c.selection_drag or getattr(c,'_image_drag',None)
                or getattr(c,'_image_resize_drag',None)):
            self.timer.start(80);return
        viewport=self.list.viewport().rect()
        tiles=getattr(c,'view_tiles',None)
        if tiles is not None and getattr(self,'_tiles_connected',None) is not tiles:
            self._tiles_connected=tiles
            tiles.thumbnail_ready.connect(self.thumbnail_ready)
        for page in range(self.list.count()):
            item=self.list.item(page)
            if page in self.rendered or not self.list.visualItemRect(item).intersects(viewport):continue
            width,height=c.sizes[page];factor=min(max(320,self.list.viewport().width()*2)/width,800/height);size=QSize(max(1,round(width*factor)),max(1,round(height*factor)))
            try:
                pair=tiles.thumbnail(page,size) if tiles is not None else None
                if tiles is not None and pair is None:
                    self.timer.start(60);continue
                rasters=getattr(c,'pdf_rasters',None)
                if rasters and c.pdf_pages[page] is not None:
                    key=(c.pdf_pages[page],size.width(),size.height())
                    if rasters.get(key) is None:
                        rasters.request_thumbnail(key);self.timer.start(80);return
                image=c._render_page(page,size,background_only=True,allow_async=bool(rasters)).copy();painter=QPainter(image)
                try:
                    if pair is not None:
                        painter.drawImage(QRectF(image.rect()),pair[0]);painter.drawImage(QRectF(image.rect()),pair[1])
                    else:
                        from rendering.ne_mixed_layers import paint_content
                        paint_content(c,painter,page,QRectF(image.rect()))
                finally:painter.end()
                pixmap=QPixmap.fromImage(image);icon=QIcon(pixmap);icon.addPixmap(pixmap,QIcon.Mode.Selected)
                item.setIcon(icon);self.rendered.add(page)
                # Bound thumbnail storage too, independently of the canvas raster cache.
                if len(self.rendered)>24:
                    old=max(self.rendered,key=lambda p:abs(p-page));self.list.item(old).setIcon(QIcon());self.rendered.remove(old)
            except Exception as exc:
                diagnostic_exception('page sidebar thumbnail ' + str(page+1), exc)
                item.setToolTip('缩略图生成失败，请查看日志：' + str(exc))
                self.rendered.add(page)
            self.timer.start(0);break
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'timer'):self.timer.start(30)
