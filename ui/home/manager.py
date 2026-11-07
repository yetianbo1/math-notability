"""Four notebook-card actions; external files retain their original location."""
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMenu, QInputDialog, QMessageBox
from .operations import NotebookOperations, is_managed
from .hierarchy import populate_category_menu
from ui.ne_toolbar import tool_icon


class NotebookMenu(QMenu):
    def __init__(self, home, path):
        super().__init__(home)
        self.home, self.path = home, path
        self.operations = NotebookOperations(home.library)
        self.setStyleSheet('''
            QMenu { background:white; color:#263c5c; border:1px solid #d6e1f2;
                    border-radius:12px; padding:8px; }
            QMenu::item { padding:10px 22px 10px 12px; border-radius:6px; }
            QMenu::item:selected { background:#e8f0ff; }
        ''')
        self.addAction('重命名', self.rename)
        categories = self.addMenu('更改分类')
        entry = self.operations.entry(path) or {}
        populate_category_menu(categories, home.library,
            lambda key: home.mutate(lambda: home.library.classify(path, key)),
            {entry.get('category', '')})
        self.addAction('打开所属位置', self.open_location)
        if not is_managed(path):
            self.addAction('删除笔记记录', self.remove_record)
        self.addAction(tool_icon('delete', '#d74444'), '删除笔记', self.delete)

    def rename(self):
        name, accepted = QInputDialog.getText(self.home, '重命名笔记',
            '名称（文件将在原文件夹中改名）', text=Path(self.path).stem)
        if accepted:
            entry = self.operations.entry(self.path) or {}
            self.home.mutate(lambda: self.operations.rename(self.path, name, entry.get('category', '')))

    def open_location(self):
        folder = Path(self.path).parent
        if not folder.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.warning(self.home, '无法打开所属位置', '文件夹不存在或无法打开：\n' + str(folder))

    def remove_record(self):
        self.home.mutate(lambda: self.operations.remove(self.path))

    def delete(self):
        message = '将删除以下笔记的实际文件（移入系统回收站），并移除使用记录。是否继续？'
        if QMessageBox.question(self.home, '确认删除笔记文件', message + '\n\n' + self.path,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.home.mutate(lambda: self.operations.remove(self.path, delete_file=True))
