"""Owned, per-user Windows installation; no administrator or default takeover."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from datetime import date

from platform_integration.ne_windows import (APP_ID, APP_NAME, APPLICATION_NAME, APPLICATIONS_ROOT,
                        CAPABILITIES, _query_string, register_application)
from core.ne_version import ensure_registration_allowed
from core.ne_identity import LEGACY_DISPLAY_NAMES
from core.ne_app_version import APP_VERSION

# Release channel: relese. A same-base relese outranks every internal vN.
VERSION = APP_VERSION
COMPATIBILITY_REVISION = '2026-10-29'
UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotabilityEditor'
MARKER = 'installation.json'


def install_directory():
    return Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'NotabilityEditor'


def data_directory():
    return Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'NotabilityEditor'


def installed_matches_current(executable=None, installed=None):
    """Return whether the installed EXE is the running, frozen version."""
    if executable is None:
        if not getattr(sys, 'frozen', False):
            return False
        executable = sys.executable
    source = Path(executable).resolve()
    target = Path(installed or install_directory() / APPLICATION_NAME).resolve()
    if not source.is_file() or not target.is_file():
        return False
    try:
        if source.stat().st_size != target.stat().st_size:
            return False
        with source.open('rb') as left, target.open('rb') as right:
            while True:
                a = left.read(1024 * 1024)
                b = right.read(1024 * 1024)
                if a != b:
                    return False
                if not a:
                    return True
    except OSError:
        return False


def validate_directory(root):
    """Refuse junctions/symlinks so an owned name cannot redirect file writes."""
    root = Path(root).absolute()
    for path in (root, *root.parents):
        if path.is_symlink() or (path.exists() and
                getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('安装路径不能包含符号链接或目录联接。')
    return root


def registration_values(root):
    root = Path(root).absolute()
    exe = root / APPLICATION_NAME
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = (f'"{powershell}" -NoProfile -NonInteractive -ExecutionPolicy Bypass '
               f'-WindowStyle Hidden -File "{root / "uninstall.ps1"}"')
    return {
        'DisplayName': APP_NAME,
        'DisplayVersion': VERSION,
        'Publisher': 'NotabilityEditor 项目（非官方）',
        'InstallLocation': str(root),
        'DisplayIcon': f'"{exe}",0',
        'UninstallString': command,
        'QuietUninstallString': command + ' -Quiet',
        'InstallDate': date.today().strftime('%Y%m%d'),
        'DataLocation': str(data_directory()),
        'NoModify': 1, 'NoRepair': 1,
        'EstimatedSize': max(1, (exe.stat().st_size + 1023) // 1024),
    }


def install_application(executable=None, *, registry=None, notify=True):
    if executable is None:
        if not getattr(sys, 'frozen', False):
            raise ValueError('请运行 EXE 版本以注册应用。')
        executable = sys.executable
    source = Path(executable).resolve(strict=True)
    if source.suffix.lower() != '.exe':
        raise ValueError('只能安装 EXE 程序。')
    if registry is None:
        import winreg as registry
    root = validate_directory(install_directory())
    ensure_registration_allowed(VERSION,registry=registry,installation=root)
    from platform_integration.ne_migrate import prepare_legacy_cleanup, cleanup_legacy_installation, update_shortcuts
    legacy = prepare_legacy_cleanup(registry)
    root.mkdir(parents=True, exist_ok=True)
    exe = root / APPLICATION_NAME
    marker = root / MARKER
    owned = False
    if marker.exists():
        owned = json.loads(marker.read_text(encoding='utf-8')).get('app_id') == APP_ID
        if not owned:raise ValueError('目标目录不属于本应用。')
    # Before v15 the one-click deployment registered Open With, but did not
    # create an ownership manifest. Adopt only our exact fixed-path identity.
    legacy_owned = (not marker.exists() and
        _query_string(registry, APPLICATIONS_ROOT+'\\'+APPLICATION_NAME+r'\shell\open\command', '') ==
            f'"{exe}" "%1"' and
        _query_string(registry, CAPABILITIES, 'ApplicationName') in (APP_NAME, *LEGACY_DISPLAY_NAMES))
    if not owned and not legacy_owned and ((exe.exists() and source != exe.resolve()) or
                      (root / 'uninstall.ps1').exists()):
        raise ValueError('目标目录已有未注册的程序；请先移走或备份这些文件。')
    if not owned and (root / 'uninstall.ps1').exists():
        raise ValueError('目标目录已有无法验证的卸载文件。')
    for name in (APPLICATION_NAME, MARKER, 'uninstall.ps1'):
        target = root / name
        if target.is_symlink() or (target.exists() and
                getattr(target.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('安装文件不能是符号链接。')
    # Replacing a running Windows EXE fails safely, without terminating notes.
    if source != exe.resolve():
        fd, staging = tempfile.mkstemp(prefix='.install-', suffix='.tmp', dir=root)
        os.close(fd)
        try:
            shutil.copyfile(source, staging)
            os.replace(staging, exe)
        finally:
            Path(staging).unlink(missing_ok=True)
    marker.write_text(json.dumps({'app_id': APP_ID, 'version': VERSION},
                                ensure_ascii=False), encoding='utf-8')
    script = Path(__file__).resolve().parent.parent / 'assets' / 'uninstall.ps1'
    # BOM is required for Chinese text in Windows PowerShell 5.1.
    (root / 'uninstall.ps1').write_text(script.read_text(encoding='utf-8-sig'),
                                       encoding='utf-8-sig')
    register_application(exe, registry=registry, notify=notify)
    for name, value in registration_values(root).items():
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, UNINSTALL_KEY, 0,
                                  registry.KEY_SET_VALUE) as handle:
            registry.SetValueEx(handle, name, 0,
                                registry.REG_DWORD if isinstance(value, int) else registry.REG_SZ,
                                value)
    # Never remove the old installation until the new program and registration
    # have both succeeded. Cleanup is limited to exact owned program files.
    cleanup_legacy_installation(legacy, registry)
    if getattr(sys, 'frozen', False):
        update_shortcuts(exe, legacy)
    return exe

