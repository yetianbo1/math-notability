"""Direct association with effective-default verification and explicit fallback."""
import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog,QVBoxLayout,QLabel,QPushButton,QDialogButtonBox
from platform_integration.ne_windows import open_default_settings,apply_default_associations,default_status
from platform_integration.ne_install import (install_application as register_application,install_directory,
                        installed_matches_current)
from ui.ne_ui import add_dialog_header,add_dialog_hint,add_dialog_divider
from infrastructure.ne_diagnostics import exception as diagnostic_exception,debug

class DefaultAppsDialog(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent)
        try:self._registered=installed_matches_current()
        except OSError:self._registered=False
        self.setObjectName('defaultsDialog')
        self.setWindowTitle('设置默认打开程序')
        self.setMinimumWidth(510)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(26,24,26,22)
        layout.setSpacing(16)
        add_dialog_header(layout,'设置默认打开程序','关联 .ntb 和 .note 文件，双击即可打开笔记。')
        add_dialog_divider(layout)
        description=QLabel('点击下方按钮，更新安装并继承旧版默认关联。\n'
                           '完成后会检查 Windows 实际使用的默认程序。')
        description.setWordWrap(True)
        layout.addWidget(description)
        add_dialog_hint(layout,'已默认使用旧版时，升级后沿用。当前默认是其他应用时，Windows 可能要求确认。')
        self.register_button=QPushButton('一键设置为默认程序')
        self.register_button.setObjectName('primaryAction')
        self.register_button.clicked.connect(self.register)
        layout.addWidget(self.register_button)
        self.system_button=QPushButton('在 Windows 中确认…')
        self.system_button.clicked.connect(self.open_system)
        self.system_button.hide()
        layout.addWidget(self.system_button)
        self.check_button=QPushButton('重新检查')
        self.check_button.clicked.connect(self.check)
        layout.addWidget(self.check_button)
        self.status=QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if sys.platform!='win32' or not getattr(sys,'frozen',False):
            self.register_button.setEnabled(False)
            self.check_button.setEnabled(False)
            self.status.setText('该功能仅在 Windows 打包 EXE 中提供。')
        else:self.check()

    def check(self):
        try:
            executable=install_directory()/'NotabilityEditor.exe'
            self._registered=installed_matches_current()
            if not self._registered:
                self.status.setText('当前版本尚未安装，或安装目录中仍是旧版本。\n'
                                    '点击下方按钮会更新程序并设置默认应用。')
                self.system_button.hide()
                self.register_button.setText('更新安装并设为默认程序')
                self.register_button.setEnabled(
                    sys.platform=='win32' and bool(getattr(sys,'frozen',False)))
                return
            self.show_status(default_status(executable))
        except Exception as exc:
            diagnostic_exception('check default applications',exc)
            self.status.setText('无法检查默认程序：'+str(exc))

    def show_status(self,status):
        complete=all(status.values())
        self.status.setText('\n'.join(ext+'：'+('已设为默认' if enabled else '尚未设为默认')
                                     for ext,enabled in status.items()))
        self.system_button.setVisible(not complete and self._registered)
        self.register_button.setText('已设为默认程序' if complete else '一键设置为默认程序')
        self.register_button.setEnabled(not complete and sys.platform=='win32' and bool(getattr(sys,'frozen',False)))
        debug('Default application check: '+str(status))

    def register(self):
        try:
            executable=register_application()
            self._registered=True
            self.show_status(apply_default_associations(executable))
        except Exception as exc:
            diagnostic_exception('default application registration',exc)
            self.status.setText('注册未完成：'+str(exc));return
        # Windows settings opens only on a separate, explicit user action.

    def open_system(self):
        try:open_default_settings()
        except Exception as exc:
            diagnostic_exception('open Windows default application settings',exc)
            self.status.setText('系统设置未能打开，请手动进入“设置 → 应用 → 默认应用”，搜索 NotabilityEditor。\n'+str(exc));return
        self.status.setText('在 Windows 中为 .ntb 和 .note 选择 NotabilityEditor，\n'
                            '返回后点击“重新检查”确认结果。')
