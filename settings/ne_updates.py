"""User-initiated, cancellable GitHub release checks without worker threads."""
from PyQt6 import sip
from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from dataclasses import replace

from core.ne_updates import (API_URL, FEED_URL, RELEASES_URL, PAGE_SIZE, MAX_PAGES,
    MAX_RESPONSE_BYTES, release_page, release_feed, has_next_page, official_release_url)
from core.ne_version import version_key
from platform_integration.ne_install import VERSION
from ui.ne_ui import add_dialog_header


class ReleaseChecker(QObject):
    progress = pyqtSignal(str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    REQUEST_TIMEOUT_MS = 10000
    RETRY_DELAYS_MS = (1000, 2000, 3000)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(lambda: self._failure('连接超时，请检查网络。'))
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self._begin_attempt)
        self.reply = None
        self.running = False
        self.attempt = 0
        self.page = 1
        self.releases = []
        self.buffer = bytearray()
        self.source = 'api'

    def start(self):
        if self.running:
            return
        self.running = True
        self.attempt = 0
        self.source = 'api'
        self._begin_attempt()

    def cancel(self):
        self.running = False
        self.retry_timer.stop()
        self._dispose_reply()

    def _dispose_reply(self):
        self.timeout.stop()
        reply, self.reply = self.reply, None
        if reply is not None:
            # Clear identity before abort, which can emit finished synchronously.
            reply.abort()
            reply.deleteLater()

    def _begin_attempt(self):
        if not self.running:
            return
        self.attempt += 1
        self.page = 1
        self.releases = []
        self.progress.emit('正在检查更新…' if self.attempt == 1
                           else f'正在重试（{self.attempt - 1}/3）…')
        self._request_page()

    def _request_page(self):
        if self.source == 'api':
            url = f'{API_URL}?per_page={PAGE_SIZE}&page={self.page}'
            accept = b'application/vnd.github+json'
        else:
            url = FEED_URL if self.page == 1 else f'{FEED_URL}?page={self.page}'
            accept = b'application/atom+xml'
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b'Accept', accept)
        request.setRawHeader(b'Cache-Control', b'no-cache')
        request.setRawHeader(b'User-Agent', ('NotabilityEditor/' + VERSION).encode('ascii'))
        request.setRawHeader(b'X-GitHub-Api-Version', b'2022-11-28')
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        self.buffer = bytearray()
        reply = self.reply = self.manager.get(request)
        reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        self.timeout.start(self.REQUEST_TIMEOUT_MS)

    def _read(self, reply):
        if reply is not self.reply:
            return
        self.buffer.extend(bytes(reply.readAll()))
        if len(self.buffer) > MAX_RESPONSE_BYTES:
            self._failure('发布信息过大，无法读取。')

    def _finished(self, reply):
        if reply is not self.reply or not self.running:
            return
        self._read(reply)
        if reply is not self.reply:
            return
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        link = bytes(reply.rawHeader(b'Link')).decode('ascii', errors='ignore')
        if status == 403 or status == 429:
            reason = 'GitHub 暂时限制访问，请稍后重试。'
        elif status == 404:
            reason = '未找到项目的发布列表。'
        elif status != 200 or reply.error() != QNetworkReply.NetworkError.NoError:
            reason = '无法连接 GitHub，请检查网络、代理或证书设置。'
        else:
            reason = ''
        if reason:
            self._failure(reason)
            return
        data = bytes(self.buffer)
        self._dispose_reply()
        try:
            if self.source == 'api':
                self.releases.extend(release_page(data))
                next_page = self.page + 1 if has_next_page(link) else None
            else:
                releases, next_page = release_feed(data)
                self.releases.extend(releases)
        except (ValueError, UnicodeError, RecursionError):
            self._failure('发布接口返回了无效数据。')
            return
        if next_page is not None:
            if self.page >= MAX_PAGES or next_page != self.page + 1:
                self._failure('发布列表过长，未能完成检查。')
                return
            self.page = next_page
            self._request_page()
            return
        if not self.releases:
            self._failure('项目尚未发布可识别的版本。')
            return
        latest = max(self.releases, key=lambda item: item.key)
        if self.source == 'feed':
            # Atom contains recent publications, not necessarily all releases.
            # It can prove a newer version exists, never that ours is latest.
            if latest.key <= version_key(VERSION):
                self._failure('发布订阅不足以确认最新版本，请前往发布页查看。')
                return
            latest = replace(latest, complete=False)
        self.running = False
        self.succeeded.emit(latest)

    def _failure(self, reason):
        if not self.running:
            return
        self._dispose_reply()
        if self.source == 'api':
            # The public Releases feed remains usable when the REST API is
            # rate limited. Switch once; retries never hammer that failed API.
            self.source = 'feed'
            self.page = 1
            self.releases = []
            self._request_page()
            return
        if self.attempt <= len(self.RETRY_DELAYS_MS):
            self.progress.emit(f'检查未成功，准备重试（{self.attempt}/3）…')
            self.retry_timer.start(self.RETRY_DELAYS_MS[self.attempt - 1])
        else:
            self.running = False
            self.failed.emit('获取失败\n已在首次请求失败后重试 3 次。\n' + reason)


class UpdateDialog(QDialog):
    def __init__(self, owner=None):
        super().__init__(owner)
        self.setObjectName('settingsDialog')
        self.setWindowTitle('检查更新')
        self.resize(460, 210)
        self.setMinimumWidth(420)
        self.release_url = RELEASES_URL
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 22)
        layout.setSpacing(14)
        add_dialog_header(layout, '检查更新', f'当前版本：{VERSION}')
        self.status = QLabel('正在检查更新…')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName('dialogTitle')
        layout.addWidget(self.status)
        layout.addStretch()
        row = QHBoxLayout()
        self.retry = QPushButton('重新检查')
        self.retry.clicked.connect(self.check)
        self.visit = QPushButton('前往发布页')
        self.visit.clicked.connect(self.open_release)
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.retry)
        row.addStretch()
        row.addWidget(self.visit)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.checker = ReleaseChecker(self)
        self.checker.progress.connect(self.status.setText)
        self.checker.succeeded.connect(self._success)
        self.checker.failed.connect(self._error)

    def check(self):
        if self.checker.running:
            return
        self.retry.setEnabled(False)
        self.release_url = RELEASES_URL
        self.status.setToolTip('')
        self.checker.start()

    def _success(self, release):
        self.retry.setEnabled(True)
        current = version_key(VERSION)
        if release.key > current:
            self.status.setText('发现新版本：' + release.version)
        elif not release.complete:
            self._error('发布订阅不足以确认最新版本，请前往发布页查看。')
        else:
            self.status.setText('当前已是最新版本')

    def _error(self, reason):
        self.retry.setEnabled(True)
        self.status.setText('获取失败')
        self.status.setToolTip(reason)

    def open_release(self):
        if official_release_url(self.release_url):
            if not QDesktopServices.openUrl(QUrl(self.release_url)):
                self.status.setText('无法打开浏览器，请稍后重试。')

    def done(self, result):
        self.checker.cancel()
        super().done(result)

    def closeEvent(self, event):
        self.checker.cancel()
        super().closeEvent(event)


def show_update_check(owner):
    dialog = getattr(owner, '_update_check_dialog', None)
    if dialog is not None and not sip.isdeleted(dialog):
        dialog.raise_()
        dialog.activateWindow()
        return dialog
    dialog = UpdateDialog(owner)
    owner._update_check_dialog = dialog
    def closed(_result):
        owner._update_check_dialog = None
        dialog.deleteLater()
    dialog.finished.connect(closed)
    dialog.show()
    dialog.check()  # Only this explicit menu command initiates a network check.
    return dialog
