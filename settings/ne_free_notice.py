"""A five-second free-software notice, acknowledged only on dismissal."""
import math
import sys

from PyQt6 import sip
from PyQt6.QtCore import Qt, QElapsedTimer, QEvent, QSettings, QTimer
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                            QLabel, QPushButton)
from settings.ne_startup_registration import (show_startup_registration,
                            track_seen, mark_track_seen)
from platform_integration.ne_install import VERSION
from ui.ne_toolbar import tool_icon

LEGACY_ACKNOWLEDGED_KEY = 'startup/free_software_notice_acknowledged/1'


def acknowledgement_key(version=None):
    """Each application version needs its own completed reading acknowledgement."""
    return LEGACY_ACKNOWLEDGED_KEY + '/versions/' + (VERSION if version is None else version)


ACKNOWLEDGED_KEY = acknowledgement_key()
MINIMUM_READ_MS = 5000


class FreeSoftwareNotice(QDialog):
    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.acknowledged_key = acknowledgement_key()
        self._elapsed_ms = 0
        self._counting = False
        self._clock = QElapsedTimer()
        self._clock.start()
        self._acknowledged = False
        self.setWindowTitle('免费软件告知')
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(500)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(18)
        header = QHBoxLayout()
        header.setSpacing(12)
        icon = QLabel()
        icon.setObjectName('freeNoticeIcon')
        icon.setFixedSize(46, 46)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(tool_icon('new_note', '#2563eb').pixmap(28, 28))
        header.addWidget(icon)
        headings = QVBoxLayout()
        headings.setSpacing(4)
        title = QLabel('本软件免费提供')
        title.setObjectName('freeNoticeTitle')
        headings.addWidget(title)
        subtitle = QLabel('请花 5 秒阅读以下说明')
        subtitle.setObjectName('freeNoticeSubtitle')
        headings.addWidget(subtitle)
        header.addLayout(headings, 1)
        layout.addLayout(header)
        description = QLabel('NotabilityEditor 是免费软件。\n'
            '使用本软件无需购买、付费激活或付费注册。')
        description.setWordWrap(True)
        layout.addWidget(description)
        refund = QLabel('如果您是付费购买本软件的，\n'
            '请联系卖家或购买平台申请退款。')
        refund.setObjectName('freeNoticeRefund')
        refund.setWordWrap(True)
        layout.addWidget(refund)
        self.countdown = QLabel('请阅读说明，5 秒后可关闭。')
        self.countdown.setObjectName('freeNoticeSubtitle')
        self.countdown.setWordWrap(True)
        layout.addWidget(self.countdown)
        self.confirm_button = QPushButton('我已阅读并知晓（5 秒）')
        self.confirm_button.setEnabled(False)
        self.confirm_button.setAutoDefault(False)
        self.confirm_button.clicked.connect(self.accept)
        layout.addWidget(self.confirm_button)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self.update_countdown)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self.update_countdown)

    def _can_read(self):
        parent = self.parentWidget()
        return (self.isVisible() and self.isActiveWindow() and not self.isMinimized()
                and (parent is None or not parent.window().isMinimized()))

    def update_countdown(self, *_):
        delta = self._clock.restart()
        if self._counting:
            self._elapsed_ms = min(MINIMUM_READ_MS, self._elapsed_ms + delta)
        self._counting = self._can_read()
        remaining = max(0, MINIMUM_READ_MS - self._elapsed_ms)
        ready = remaining == 0
        self.confirm_button.setEnabled(ready)
        if ready:
            self.confirm_button.setText('我已阅读并知晓')
            self.countdown.setText('阅读时间已满，可以确认关闭。')
            self._timer.stop()
        else:
            seconds = math.ceil(remaining / 1000)
            self.confirm_button.setText(f'我已阅读并知晓（{seconds} 秒）')
            self.countdown.setText(f'请阅读说明，{seconds} 秒后可关闭。' if self._counting else
                f'阅读计时已暂停，请保持此窗口在前台（剩余 {seconds} 秒）。')

    def can_dismiss(self):
        self.update_countdown()
        return self._elapsed_ms >= MINIMUM_READ_MS

    def showEvent(self, event):
        super().showEvent(event)
        self.update_countdown()
        if self._elapsed_ms < MINIMUM_READ_MS:
            self._timer.start()

    def hideEvent(self, event):
        self.update_countdown()
        self._counting = False
        self._timer.stop()
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, '_timer') and event.type() in (
                QEvent.Type.ActivationChange, QEvent.Type.WindowStateChange):
            self.update_countdown()

    def done(self, result):
        if self._acknowledged or not self.can_dismiss():
            return
        # Never mark the notice read on show, timer expiry, or process shutdown.
        # A force-killed process before explicit dismissal leaves it unread.
        self.settings.setValue(self.acknowledged_key, True)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            # Allow use with read-only settings, but retry the notice next time.
            self.settings.remove(self.acknowledged_key)
            self.settings.sync()
        else:
            # A patch/minor update inside the same major release must not
            # resurrect the five-second notice.
            mark_track_seen(self.settings, LEGACY_ACKNOWLEDGED_KEY)
        self._acknowledged = True
        self._timer.stop()
        super().done(result)

    def closeEvent(self, event):
        if not self.can_dismiss():
            event.ignore()
            return
        super().closeEvent(event)


def show_free_software_notice(owner, *, finished=None):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        if finished:
            finished()
        return None
    settings = owner.color_settings
    # One acknowledgement per major release.  Exact-version keys written by
    # older builds are still recognised, so small updates never re-prompt.
    if track_seen(settings, LEGACY_ACKNOWLEDGED_KEY):
        if finished:
            finished()
        return None
    existing = getattr(owner, '_free_notice_dialog', None)
    if existing is not None and not sip.isdeleted(existing):
        return existing
    dialog = FreeSoftwareNotice(owner, settings)
    owner._free_notice_dialog = dialog
    def closed(_result):
        owner._free_notice_dialog = None
        dialog.deleteLater()
        if finished:
            QTimer.singleShot(0, finished)
    dialog.finished.connect(closed)
    dialog.open()
    return dialog


def show_startup_notices(owner, *, finished=None):
    """Registration, then the independent free notice, then document opening."""
    if getattr(owner, '_startup_notices_started', False):
        return
    owner._startup_notices_started = True
    def after_registration():
        if not sip.isdeleted(owner) and owner.isVisible():
            show_free_software_notice(owner, finished=finished)
    show_startup_registration(owner, finished=after_registration)


STYLE = '''
QDialog { background:#ffffff; color:#263c5c; }
QLabel { font-size:14px; }
QLabel#freeNoticeTitle { font-size:20px; font-weight:600; color:#17375e; }
QLabel#freeNoticeSubtitle { font-size:12px; color:#7b8ca5; }
QLabel#freeNoticeIcon { background:#edf4ff; border-radius:12px; }
QLabel#freeNoticeRefund { background:#fff7ed; color:#9a4b10;
    border:1px solid #ffdfb8; border-radius:10px; padding:18px; font-weight:600; }
QPushButton { background:#2563eb; color:#ffffff; border:1px solid #2563eb;
    border-radius:8px; padding:12px 18px; font-size:14px; }
QPushButton:hover { background:#1d4ed8; }
QPushButton:disabled { background:#edf2fa; color:#91a0b7; border-color:#e0e7f2; }
'''
