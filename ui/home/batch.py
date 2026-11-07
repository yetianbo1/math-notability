"""Sequential library mutations on the settings-owning UI thread."""
from pathlib import Path
from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QProgressDialog
from .library import identity
from .operations import is_managed, NotebookOperations


class LibraryJob(QObject):
    finished = pyqtSignal()

    def __init__(self, home, paths, title):
        super().__init__(home)
        self.home, self.paths, self.title = home, tuple(paths), title
        self.index = 0
        self.succeeded, self.skipped, self.failed = [], [], []
        self.cancelled = False
        self.done = False
        self.progress = QProgressDialog('正在'+title+'…','取消',0,len(paths),home)
        self.progress.setWindowTitle('批量'+title)
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.canceled.connect(self.cancel)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.step)

    def cancel(self):
        self.cancelled = True

    def start(self):
        if getattr(self.home,'batch_job',None) is not None:
            raise RuntimeError('请等待当前整理任务完成')
        self.home.batch_job = self
        self.refresh_busy(True)
        self.progress.show()
        self.timer.start(0)

    def step(self):
        if self.done:
            return
        if self.cancelled or self.index >= len(self.paths):
            self.finish()
            return
        path = self.paths[self.index]
        self.progress.setLabelText(f'正在处理 {self.index+1}/{len(self.paths)}：{Path(path).name}')
        try:
            if not Path(path).is_file():
                raise OSError('文件已移动或不存在')
            entry = NotebookOperations(self.home.library).entry(path)
            if entry is None:
                raise ValueError('笔记记录已不存在')
            if not self.apply(path,entry):
                self.skipped.append(path)
            else:
                self.succeeded.append(path)
        except Exception as exc:
            self.failed.append((path,str(exc)))
        self.index += 1
        self.progress.setValue(self.index)
        self.timer.start(0)

    def finish(self):
        if self.done:
            return
        self.done = True
        self.timer.stop()
        self.progress.close()
        self.progress.deleteLater()
        home = self.home
        home.batch_job = None
        self.refresh_busy(False)
        home.refresh()
        home.notes.cancel_selection()
        failed = {identity(path) for path,_ in self.failed}
        for row in range(home.notes.count()):
            item = home.notes.item(row)
            if item.data(Qt.ItemDataRole.UserRole) and identity(item.data(Qt.ItemDataRole.UserRole)) in failed:
                item.setSelected(True)
        home.notes.batch = bool(home.notes.selectedItems())
        home.selection_changed()
        home.changed.emit()
        remaining = len(self.paths)-self.index
        summary = self.title+f'：成功 {len(self.succeeded)} 本，跳过 {len(self.skipped)} 本，失败 {len(self.failed)} 本，未处理 {remaining} 本。'
        home.result.setText(summary)
        self.finished.emit()
        if self.failed and not getattr(home.window(),'_closing_batch',False):
            box = QMessageBox(QMessageBox.Icon.Warning,'部分笔记未完成',summary,parent=home)
            box.setDetailedText('\n'.join(path+'：'+error for path,error in self.failed))
            retry = box.addButton('重试失败项',QMessageBox.ButtonRole.AcceptRole)
            box.addButton('关闭',QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                self.retry([p for p,_ in self.failed])
        self.deleteLater()

    def refresh_busy(self, busy):
        self.home.set_busy(busy)
        owner = self.home.window()
        if hasattr(owner,'workspace'):
            from ui.ne_menus import refresh_menu_state
            refresh_menu_state(owner)


class ClassificationJob(LibraryJob):
    def __init__(self,home,paths,category):
        self.category = category
        super().__init__(home,paths,'更改分类')

    def apply(self,path,entry):
        if self.category and self.category not in self.home.library.categories:
            raise ValueError('目标分类已不存在')
        if entry.get('category','') == self.category:
            return False
        self.home.library.classify(path,self.category)
        return True

    def retry(self,paths):
        ClassificationJob(self.home,paths,self.category).start()


class DeletionJob(LibraryJob):
    def __init__(self,home,paths):
        super().__init__(home,paths,'删除笔记')

    def apply(self,path,entry):
        NotebookOperations(self.home.library).remove(path,delete_file=True)
        return True

    def retry(self,paths):
        DeletionJob(self.home,paths).start()


def delete_dialog(home):
    paths = tuple(home.selected_paths())
    if not paths or home._busy:
        return
    from .dialogs import DeleteNotesDialog
    dialog = DeleteNotesDialog(home,paths)
    try:
        if dialog.exec() and not home._busy:
            DeletionJob(home,paths).start()
    finally:
        dialog.deleteLater()


def classify_dialog(home, category):
    paths = tuple(home.selected_paths())
    if not paths:
        return
    name = home.library.category_label(category)
    managed = sum(is_managed(path) for path in paths)
    text = (f'将 {len(paths)} 本笔记更改到「{name}」。\n\n'
        f'笔记库内 {managed} 本：移动文件到目标分类目录。\n'
        f'外部笔记 {len(paths)-managed} 本：仅修改分类记录，文件保留原位置。')
    if QMessageBox.question(home,'确认更改分类',text,
            QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok:
        ClassificationJob(home,paths,category).start()
