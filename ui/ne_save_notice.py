"""Save-As and recovery notices with an exact, copyable file path.

Ordinary in-place Save deliberately uses QMessageBox.information instead, without
showing a path or a clipboard button.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox


def show_copyable_path_notice(parent, title, message, path):
    """Show a path-bearing notice; copy only the exact destination when requested.

    The path is selectable with the mouse/keyboard. '复制路径' copies to the system
    clipboard; '确定' dismisses the notice without changing the clipboard.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Information)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse |
        Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    box.setText(message)
    copy_button = box.addButton('复制路径', QMessageBox.ButtonRole.ActionRole)
    ok_button = box.addButton('确定', QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(ok_button)
    box.exec()
    if box.clickedButton() is copy_button:
        QApplication.clipboard().setText(str(path))
        parent.statusBar().showMessage('保存路径已复制到剪贴板', 3500)
