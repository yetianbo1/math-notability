"""A Windows registration invitation with a separate opt-out per version."""
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                            QPushButton, QCheckBox)
from platform_integration.ne_install import VERSION
from platform_integration.ne_registration import (registered_application,
                            registered_version_order, registration_matches_version)
from settings.ne_appsettings import register_current_application
from platform_integration.ne_windows import open_default_settings
from ui.ne_toolbar import tool_icon

LEGACY_DISABLED_KEY = 'startup/registration_prompt_disabled'


def disabled_key(version=None):
    return LEGACY_DISABLED_KEY + '/versions/' + (VERSION if version is None else version)


DISABLED_KEY = disabled_key()
# Legacy seen records are preserved but no longer control the reminder.
SEEN_PREFIX = 'startup/registration_seen/'


def should_prompt(settings, registration, *, version=VERSION):
    """Only this version's opt-out or a current/newer registration can skip."""
    if settings.value(disabled_key(version), False, type=bool):
        return False
    order = registered_version_order(registration, version)
    return order is None or order < 0


class StartupRegistrationDialog(QDialog):
    def __init__(self, parent, registration):
        super().__init__(parent)
        self._busy = False
        self.setWindowTitle('注册 NotabilityEditor')
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(500)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(16)
        header = QHBoxLayout()
        icon = QLabel()
        icon.setObjectName('registrationIcon')
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(tool_icon('new_note', '#2563eb').pixmap(28, 28))
        header.addWidget(icon)
        headings = QVBoxLayout()
        headings.setSpacing(4)
        title = QLabel('将应用注册到 Windows？')
        title.setObjectName('registrationTitle')
        headings.addWidget(title)
        version = QLabel('当前版本 ' + VERSION)
        version.setObjectName('registrationSubtitle')
        headings.addWidget(version)
        header.addLayout(headings, 1)
        header.setSpacing(12)
        layout.addLayout(header)
        reason = ('已检测到当前版本的应用注册，可以核对或重新注册。'
                  if registration_matches_version(registration, VERSION) else
                  '当前版本尚未完成有效注册，可以现在更新 Windows 中的应用注册。'
                  if registration else '尚未检测到有效的应用注册，可以现在完成设置。')
        description = QLabel(reason)
        description.setWordWrap(True)
        layout.addWidget(description)
        benefits = QLabel('注册后可以：\n'
            '• 在 Windows 中管理和卸载应用\n'
            '• 设置 .ntb / .note 笔记的默认打开方式\n\n'
            '程序将安装到当前用户目录，保留已有分类、设置和笔记。')
        benefits.setObjectName('registrationBenefits')
        benefits.setWordWrap(True)
        layout.addWidget(benefits)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.system_button = QPushButton('前往 Windows 默认应用设置')
        self.system_button.setAutoDefault(False)
        self.system_button.clicked.connect(self.open_system_settings)
        self.system_button.hide()
        layout.addWidget(self.system_button)
        self.never_ask = QCheckBox('此版本不再询问')
        self.never_ask.setToolTip('仅当前版本不再显示；升级后重新检查注册状态。仍可从“设置”菜单手动注册。')
        layout.addWidget(self.never_ask)
        hint = QLabel('也可稍后在“设置 → 注册应用到 Windows…”中完成。')
        hint.setObjectName('registrationSubtitle')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.later_button = QPushButton('暂不注册')
        self.later_button.setAutoDefault(False)
        self.later_button.clicked.connect(self.reject)
        buttons.addWidget(self.later_button)
        self.register_button = QPushButton('立即注册')
        self.register_button.setObjectName('registerNow')
        self.register_button.setAutoDefault(False)
        self.register_button.clicked.connect(self.start_registration)
        buttons.addWidget(self.register_button)
        layout.addLayout(buttons)
        self.later_button.setFocus()

    def reject(self):
        if not self._busy:
            super().reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)

    def start_registration(self):
        if self._busy:
            return
        self._busy = True
        self.register_button.setEnabled(False)
        self.register_button.setText('正在注册…')
        self.later_button.setEnabled(False)
        self.never_ask.setEnabled(False)
        self.status.setText('正在安装并更新 Windows 注册，请稍候…')
        self.status.show()
        QTimer.singleShot(0, self.finish_registration)

    def finish_registration(self):
        try:
            executable, associations = register_current_application()
        except Exception as exc:
            self.status.setText('注册未完成：' + str(exc))
            self.register_button.setText('重试注册')
            self.register_button.setEnabled(True)
        else:
            complete = all(associations.get(ext, False) for ext in ('.ntb', '.note'))
            self.status.setText('注册完成，可以开始使用。' if complete else
                '应用已注册。Windows 仍需你确认部分笔记格式的默认应用，可前往系统设置完成。')
            self.system_button.setVisible(not complete)
            self.register_button.hide()
            self.later_button.setText('开始使用')
        finally:
            self._busy = False
            self.later_button.setEnabled(True)
            self.never_ask.setEnabled(True)

    def open_system_settings(self):
        try:
            open_default_settings()
        except OSError as exc:
            self.status.setText('无法打开系统设置：' + str(exc))
            self.status.show()


def show_startup_registration(owner, *, finished=None):
    """Keep command-line document opening behind this one startup dialog."""
    if getattr(owner, '_startup_registration_checked', False):
        return None
    owner._startup_registration_checked = True
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        if finished:
            finished()
        return None
    settings = owner.color_settings
    current_version = VERSION
    opt_out_key = disabled_key(current_version)
    # The former global boolean has no version attribution. Preserve it, but
    # never let it silence a newer release. Only an explicit version key counts.
    if settings.value(opt_out_key, False, type=bool):
        if finished:
            finished()
        return None
    registration = registered_application()
    if not should_prompt(settings, registration, version=current_version):
        if finished:
            finished()
        return None
    dialog = StartupRegistrationDialog(owner, registration)
    owner._startup_registration_dialog = dialog
    def closed(_result):
        settings.setValue(opt_out_key, dialog.never_ask.isChecked())
        settings.sync()
        owner._startup_registration_dialog = None
        dialog.deleteLater()
        if finished:
            QTimer.singleShot(0, finished)
    dialog.finished.connect(closed)
    dialog.open()
    return dialog


STYLE = '''
QDialog { background:#ffffff; color:#263c5c; }
QLabel { font-size:14px; }
QLabel#registrationTitle { font-size:20px; font-weight:600; color:#17375e; }
QLabel#registrationSubtitle { font-size:12px; color:#7b8ca5; }
QLabel#registrationIcon { background:#edf4ff; border-radius:12px; }
QLabel#registrationBenefits { background:#f5f8fd; border-radius:10px; padding:16px; }
QPushButton { border:1px solid #d5e1f3; background:#ffffff; color:#3564aa;
    border-radius:8px; padding:10px 18px; font-size:14px; min-width:80px; }
QPushButton:hover { background:#f0f5ff; border-color:#a5c3f5; }
QPushButton#registerNow { background:#2563eb; color:#ffffff; border-color:#2563eb; }
QPushButton#registerNow:hover { background:#1d4ed8; }
QPushButton:disabled { color:#94a3b8; border-color:#e2e8f0; }
QCheckBox { font-size:13px; spacing:8px; color:#5b708e; }
'''
