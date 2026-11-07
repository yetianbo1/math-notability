"""Application information, per-user installation and safe detached restart."""
from pathlib import Path
import sys

from PyQt6.QtCore import QProcess, QProcessEnvironment, QSettings, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                            QDialogButtonBox, QMessageBox, QFormLayout)
from platform_integration.ne_install import (VERSION, data_directory, install_application,
                        install_directory, installed_matches_current)
from core.ne_version import VersionDowngradeError
from platform_integration.ne_windows import apply_default_associations, open_default_settings
from ui.home.storage import library_index_path


def restart_process(note_path=None):
    process = QProcess()
    if getattr(sys, 'frozen', False):
        process.setProgram(sys.executable)
        arguments = []
    else:
        process.setProgram(sys.executable)
        arguments = [str(Path(__file__).resolve().parent.parent / 'main.py')]
    if note_path:arguments.append(str(note_path))
    process.setArguments(arguments)
    process.setWorkingDirectory(str(Path(sys.executable).resolve().parent))
    environment = QProcessEnvironment.systemEnvironment()
    # New onefile process must own its extraction directory after parent exits.
    environment.insert('PYINSTALLER_RESET_ENVIRONMENT', '1')
    process.setProcessEnvironment(environment)
    success, _pid = process.startDetached()
    return success


def restart_viewer(owner):
    if owner.worker or owner.saver or owner.pdf_importer:
        owner.statusBar().showMessage('请等待当前加载或保存完成后再重启。', 5000)
        return
    if not owner.confirm_leave(owner.restart_application, continue_label='重启'):return
    is_draft=(owner._is_unsaved_draft()
              if hasattr(owner,'_is_unsaved_draft')
              else owner._is_unsaved_pdf_draft())
    note = owner.prepared.path if owner.prepared and not is_draft else None
    try:
        if not restart_process(note):raise OSError('无法启动新的程序进程。')
    except Exception as exc:
        QMessageBox.warning(owner, '重启未完成', str(exc))
        return
    owner._restart_approved = True
    owner.close()


def register_current_application():
    """Shared registration action for Settings and the startup invitation."""
    exe = install_application()
    return exe, apply_default_associations(exe)


class ApplicationDialog(QDialog):
    def __init__(self, owner=None):
        super().__init__(owner)
        self.setWindowTitle('应用信息与数据位置')
        self.resize(700, 440)
        layout = QVBoxLayout(self)
        title = QLabel(f'NotabilityEditor {VERSION}')
        title.setStyleSheet('font-size:20px;font-weight:600')
        layout.addWidget(title)
        form = QFormLayout()
        self.paths = {
            '当前程序': str(Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()),
            '注册安装目录': str(install_directory()),
            '应用数据与日志': str(data_directory()),
            '分类与笔记索引': str(library_index_path()),
            '常用颜色与快捷键设置': QSettings('NotabilityEditor', 'NotabilityEditor').fileName(),
            'GPU 与日志设置': QSettings('NotabilityEditor', 'Display').fileName(),
            '临时数据': 'Windows 临时目录；关闭笔记后清理',
            '笔记文件': '存放在用户打开或另存为的位置，卸载时始终保留',
        }
        for name, value in self.paths.items():
            label = QLabel(value)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(name, label)
        layout.addLayout(form)
        if sys.platform == 'win32' and getattr(sys, 'frozen', False):
            installed = install_directory() / 'NotabilityEditor.exe'
            if installed_matches_current():
                initial = '当前版本已安装。注册会核验并设置 .ntb、.note 的默认应用。'
            elif installed.is_file():
                initial = '检测到旧版本安装，当前版本尚未注册。注册会更新程序并设置默认应用。'
            else:
                initial = '当前版本尚未注册。注册会安装程序并设置默认应用。'
        else:
            initial = '注册新版会自动替换已注册的旧版程序，迁移原有设置并保留笔记文件。'
        self.status = QLabel(initial + '\n注册后可在 Windows“已安装的应用”中卸载，并选择是否删除应用设置。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.register_button = QPushButton('注册并设为默认应用')
        self.register_button.setEnabled(sys.platform == 'win32' and bool(getattr(sys, 'frozen', False)))
        self.register_button.clicked.connect(self.register)
        layout.addWidget(self.register_button)
        self.system_button = QPushButton('在 Windows 中确认默认应用')
        self.system_button.setEnabled(sys.platform == 'win32')
        self.system_button.clicked.connect(open_default_settings)
        self.system_button.hide()
        layout.addWidget(self.system_button)
        for label, path in (('打开注册安装目录', install_directory()), ('打开应用数据目录', data_directory())):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, p=path: self.open_directory(p))
            layout.addWidget(button)
        windows = QPushButton('打开 Windows 已安装的应用')
        windows.setEnabled(sys.platform == 'win32')
        windows.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('ms-settings:appsfeatures')))
        layout.addWidget(windows)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def open_directory(self, path):
        if path.is_dir():QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:self.status.setText('该目录尚未创建。')

    def register(self):
        try:
            exe, status = register_current_application()
            complete = all(status.values())
            details = '\n'.join(extension + '：' +
                                ('已设为默认' if enabled else '尚未设为默认')
                                for extension, enabled in status.items())
            if complete:
                self.status.setText(f'注册完成，默认应用已切换：\n{details}\n'
                                    f'安装位置：{exe}\n原有设置已迁移，笔记文件保持不变。')
                self.register_button.setText('注册并设为默认应用')
                self.system_button.hide()
            else:
                self.status.setText(f'程序已注册，但默认应用还有未完成项：\n{details}\n'
                                    'Windows 可能要求你确认当前默认应用。')
                self.system_button.show()
        except VersionDowngradeError as exc:
            self.status.setText(str(exc))
        except Exception as exc:
            self.status.setText(f'注册未完成：{exc}\n如已安装程序正在运行，请先保存并关闭后重试。')


def show_application_info(owner):
    ApplicationDialog(owner).exec()


def show_registration(owner):
    dialog = ApplicationDialog(owner)
    dialog.setWindowTitle('注册应用到 Windows')
    dialog.exec()
