"""Crash and load diagnostics for the frozen Windows Qt application.

All logs are stored outside the installation directory, so they remain
available after the window closes or an EXE is updated. No note data is logged.
"""
import faulthandler
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
import threading
import warnings

_log = None
_last_stage = "startup"
LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40}
_level = 'INFO'
_lock = threading.RLock()
_previous_warning = None
_previous_qt_handler = None
# A repeated exception from a 30 ms timer must not queue hundreds of modal
# dialogs: each one starts its own event loop and freezes the UI.
_last_popup = {'key': None, 'at': 0.0}


def current_level():
    return _level


def load_level(settings=None):
    if settings is None:
        from PyQt6.QtCore import QSettings
        settings = QSettings('NotabilityEditor', 'Display')
    value = str(settings.value('log_level', 'INFO')).upper()
    return value if value in LEVELS else 'INFO'


def set_level(value, settings=None):
    global _level
    value = str(value).upper()
    if value not in LEVELS:raise ValueError('未知日志级别：'+value)
    if settings is None:
        from PyQt6.QtCore import QSettings
        settings = QSettings('NotabilityEditor', 'Display')
    settings.setValue('log_level', value)
    settings.sync()
    _level = value
    _write('Log level: '+value)


def message(level, text):
    if LEVELS[level] >= LEVELS[_level]:
        _write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {level}: {text}")


def debug(text):message('DEBUG', text)
def warning(text):message('WARNING', text)


def _python_warning(message_text, category, filename, lineno, file=None, line=None):
    warning(f'{category.__name__}: {message_text} ({Path(filename).name}:{lineno})')
    if _previous_warning:_previous_warning(message_text, category, filename, lineno, file, line)


def _qt_message(kind, context, text):
    from PyQt6.QtCore import QtMsgType
    level = {QtMsgType.QtDebugMsg:'DEBUG', QtMsgType.QtInfoMsg:'INFO',
             QtMsgType.QtWarningMsg:'WARNING', QtMsgType.QtCriticalMsg:'ERROR',
             QtMsgType.QtFatalMsg:'ERROR'}.get(kind, 'INFO')
    message(level, 'Qt: '+str(text))
    if _previous_qt_handler:_previous_qt_handler(kind, context, text)


def log_directory():
    home = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(home) / "NotabilityEditor" / "logs"


def _write(text):
    global _log
    if _log is None:
        return
    try:
        with _lock:
            _log.write(text + "\n")
            _log.flush()
    except (OSError, ValueError):
        pass


def stage(name):
    global _last_stage
    _last_stage = str(name)
    if LEVELS[_level] <= LEVELS['INFO']:
        _write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] STAGE: {_last_stage}")


def exception(context, exc):
    with _lock:
        _write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] ERROR at {context}; last stage: {_last_stage}")
        _write(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))


def install():
    global _log, _level, _previous_warning, _previous_qt_handler
    if _log is not None:
        return
    try:
        path = log_directory()
        path.mkdir(parents=True, exist_ok=True)
        # Use ASCII names even on non-English Windows installations.
        file = path / ("session-" + time.strftime("%Y%m%d-%H%M%S") + ".log")
        _log = file.open("a", encoding="utf-8", buffering=1)
        _level = load_level()
        from core.ne_app_version import APP_VERSION
        _write("NotabilityEditor " + APP_VERSION + " crash diagnostics")
        _write("Python=" + sys.version.replace("\n", " "))
        _write("Platform=" + sys.platform + "; frozen=" + str(getattr(sys, "frozen", False)))
        _write('Log level: '+_level)
        faulthandler.enable(file=_log, all_threads=True)
        sys.excepthook = _uncaught
        _previous_warning = warnings.showwarning
        warnings.showwarning = _python_warning
        from PyQt6.QtCore import qInstallMessageHandler
        _previous_qt_handler = qInstallMessageHandler(_qt_message)
        stage("application start")
    except OSError:
        # A read-only profile should not prevent opening a note.
        _log = None


def _uncaught(exc_type, value, tb):
    with _lock:
        _write("UNHANDLED (including Qt signal/slot callback):")
        _write(''.join(traceback.format_exception(exc_type, value, tb)))
    # A periodic exception (e.g. a 30 ms drag timer) must not queue a modal
    # dialog on every tick.  Keep logging every occurrence, but show the same
    # error at most once every few seconds.
    last = None
    cursor = tb
    while cursor is not None:
        last = (cursor.tb_frame.f_code.co_filename, cursor.tb_lineno)
        cursor = cursor.tb_next
    try:
        key = (getattr(exc_type, '__name__', str(exc_type)), str(value), last)
    except Exception:
        key = (str(exc_type), '', last)
    now = time.monotonic()
    with _lock:
        if key == _last_popup['key'] and now - _last_popup['at'] < 10.0:
            return
        _last_popup['key'] = key
        _last_popup['at'] = now
    # Do not rethrow from the exception hook: in PyQt, doing so can terminate
    # the application without displaying any error message.
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance() is not None:
            QTimer.singleShot(0, lambda: QMessageBox.critical(
                None, "NotabilityEditor 运行异常",
                "程序捕获到一个错误。\n请查看日志：\n" + str(log_directory()) +
                "\n\n最后阶段：" + _last_stage))
    except Exception:
        pass

