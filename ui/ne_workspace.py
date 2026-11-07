"""Home/editor routing around the existing native load/save pipeline."""
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, Qt
from PyQt6.QtWidgets import QStackedWidget, QFileDialog, QProgressDialog, QMessageBox

from ui.home.library import Library, ImportWorker
from ui.home.page import HomePage
from ui.editor.page import EditorPage


class DesktopWorkspace(QObject):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.library = Library(owner.color_settings)
        self.importer = None
        self.exiting = False
        self.sidebar_visible = not owner.page_sidebar.isHidden()
        self.editor = EditorPage(owner.takeCentralWidget())
        self.home = HomePage(self.library, new_note=owner.create_new_note,
            open_note=owner.choose_file, import_note=self.import_note,
            import_pdf=owner.import_pdf_as_ntb, open_path=owner.open_recent_file)
        self.home.changed.connect(self.library_changed)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.editor)
        owner.setCentralWidget(self.stack)
        owner._install_drop_target(self.home)
        owner._install_drop_target(self.home.notes.viewport())
        file_menu = owner.menus['文件']
        self.home_action = owner.action(owner, '返回主页', self.return_home, 'Alt+Home')
        file_menu.insertAction(file_menu.actions()[0], self.home_action)
        self.import_action = owner.action(owner, '导入笔记到笔记库…', self.import_note)
        owner.import_menu.insertAction(owner.pdf_note_action, self.import_action)
        self.show_home()

    def busy(self):
        owner = self.owner
        return bool(owner.worker or owner.saver or owner.pdf_importer or self.importer
                    or getattr(self.home,'batch_job',None))

    def refresh_state(self):
        busy = self.busy()
        at_home = self.stack.currentWidget() is self.home
        self.home.set_busy(busy)
        self.home_action.setEnabled(not busy and not at_home)
        self.import_action.setEnabled(not busy)
        self.owner.page_toggle_action.setEnabled(not at_home and not busy)
        for name in ('编辑', '工具', '页面'):
            self.owner.menus[name].setEnabled(not at_home)
        for name, menu in self.owner.menus.items():
            menu.menuAction().setVisible(not at_home or name in ('文件', '设置', '帮助'))
        if at_home:
            self.owner.page_sidebar.hide()

    def record(self, path):
        self.library.record(path)
        self.home.refresh()

    def library_changed(self):
        from ui.ne_menus import refresh_recent_menu
        refresh_recent_menu(self.owner)

    def show_editor(self):
        owner = self.owner
        owner.fullscreen_action.setEnabled(True)
        self.stack.setCurrentWidget(self.editor)
        owner.read_toolbar.show()
        owner.edit_toolbar.show()
        owner.page_sidebar.setVisible(self.sidebar_visible)
        self.refresh_state()
        owner.editor_view.schedule_cursor_refresh()

    def show_home(self):
        owner = self.owner
        owner.editor_view.leave()
        owner.editor_view.cancel_drag()
        owner.fullscreen_action.setEnabled(False)
        self.stack.setCurrentWidget(self.home)
        owner.read_toolbar.hide()
        owner.edit_toolbar.hide()
        owner.page_sidebar.hide()
        self.home.refresh()
        self.refresh_state()
        owner.statusBar().showMessage('主页 · 打开 NTB / NOTE，或导入 PDF 创建笔记')

    def return_home(self):
        if self.busy() or self.stack.currentWidget() is self.home:
            return
        owner = self.owner
        if not owner.confirm_leave(self.return_home, continue_label='返回主页'):
            return
        self.sidebar_visible = not owner.page_sidebar.isHidden()
        old = owner.scroll.takeWidget()
        owner.close_document()
        owner.canvas = None
        owner.disk_bundle = None
        if old is not None:
            old.deleteLater()
        owner.page.setRange(1, 1)
        owner.count_label.setText(' / 0  ')
        self.show_home()
        owner.edit_changed()

    def import_note(self, source=None):
        if self.busy():
            return
        owner = self.owner
        if not isinstance(source, (str, Path)):
            source, _ = QFileDialog.getOpenFileName(owner, '导入笔记到本机笔记库', '', '笔记文件 (*.ntb *.note)')
        if not source:
            return
        if not owner.confirm_leave(lambda: self.import_note(source), continue_label='导入笔记'):
            return
        worker = ImportWorker(source, owner)
        self.importer = worker
        progress = QProgressDialog('正在复制到本机笔记库…', '取消', 0, 100, owner)
        progress.setWindowTitle('导入笔记')
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(300)
        progress.setAutoClose(False)
        worker.progress.connect(progress.setValue)
        progress.canceled.connect(worker.stop.set)
        worker.failed.connect(lambda message: QMessageBox.warning(owner, '无法导入笔记', message))

        def finished():
            result = worker.result
            progress.close()
            progress.deleteLater()
            self.importer = None
            worker.deleteLater()
            from ui.ne_menus import refresh_menu_state
            refresh_menu_state(owner)
            if result and not self.exiting:
                # The existing reader validates the copy before it is indexed.
                QTimer.singleShot(0, lambda: owner.open_file(result, skip_leave_check=True))
            else:
                owner.statusBar().showMessage('笔记导入已取消或未完成', 4000)

        worker.finished.connect(finished)
        from ui.ne_menus import refresh_menu_state
        refresh_menu_state(owner)
        worker.start()
