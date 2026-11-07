"""Migrate only an owned Viewer installation after Editor installs successfully."""
import ctypes
import json
import os
from pathlib import Path
import subprocess

from core.ne_identity import LEGACY_APP_ID, LEGACY_CAPABILITIES, LEGACY_DISPLAY_NAMES, LEGACY_UNINSTALL, PRODUCT_NAME
from platform_integration.ne_windows import APPLICATIONS_ROOT, _delete_tree, _query_string


def legacy_install_directory():
    return Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'NotabilityViewer'


def _require_closed(executable):
    if os.name != 'nt' or not executable.exists():
        return
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    # Write access also detects a running executable's mapped image section.
    handle = kernel.CreateFileW(str(executable), 0xC0000000, 0, None, 3, 0x80, None)
    if handle == w.HANDLE(-1).value:
        raise PermissionError('旧版 NotabilityViewer 仍在运行或被占用，请保存并关闭后再更新。')
    kernel.CloseHandle(handle)


def prepare_legacy_cleanup(registry):
    """Check ownership, links and locks before creating any Editor files."""
    from platform_integration.ne_install import validate_directory
    root = legacy_install_directory()
    if not root.exists():
        return None
    validate_directory(root)
    marker = root / 'installation.json'
    executable = root / 'NotabilityViewer.exe'
    for path in (marker, executable, root / 'uninstall.ps1'):
        if path.is_symlink() or (path.exists() and getattr(path.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('旧版安装文件包含链接，未执行迁移清理。')
    if marker.exists():
        try:
            owned = json.loads(marker.read_text(encoding='utf-8')).get('app_id') == LEGACY_APP_ID
        except (OSError, ValueError, AttributeError):
            owned = False
    else:
        command = APPLICATIONS_ROOT + r'\NotabilityViewer.exe\shell\open\command'
        owned = (not (root / 'uninstall.ps1').exists()
                 and _query_string(registry, command, '') == f'"{executable}" "%1"'
                 and _query_string(registry, LEGACY_CAPABILITIES, 'ApplicationName') in (*LEGACY_DISPLAY_NAMES, PRODUCT_NAME))
    if not owned:
        return None
    _require_closed(executable)
    return root


def cleanup_legacy_installation(root, registry):
    if root is None:
        return
    from platform_integration.ne_install import validate_directory
    root = validate_directory(root)
    if root != legacy_install_directory():
        raise ValueError('旧版清理目录不匹配。')
    for name in ('NotabilityViewer.exe', 'uninstall.ps1', 'installation.json'):
        target = root / name
        if target.is_symlink() or (target.exists() and getattr(target.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('旧版安装文件包含链接，未执行迁移清理。')
    for name in ('NotabilityViewer.exe', 'uninstall.ps1', 'installation.json'):
        (root / name).unlink(missing_ok=True)
    if _query_string(registry, LEGACY_UNINSTALL, 'InstallLocation') == str(root):
        _delete_tree(registry, LEGACY_UNINSTALL)
    # register_application keeps a version redirect in the old capability key
    # for legacy Viewer installers. It is not a second installed application.
    # Never recurse: user notes, settings and unknown files survive migration.
    if not any(root.iterdir()):
        root.rmdir()


def update_shortcuts(executable, legacy):
    """Replace only links pointing to our old installation; preserve user links."""
    if os.name != 'nt':
        return
    environment = dict(os.environ, NV_EDITOR_EXE=str(executable),
                       NV_VIEWER_EXE=str((legacy or legacy_install_directory()) / 'NotabilityViewer.exe'))
    script = r'''
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('DesktopDirectory'), [Environment]::GetFolderPath('Programs'))) {
    if ([string]::IsNullOrWhiteSpace($folder)) { continue }
    foreach ($oldName in @('Notability Viewer.lnk', 'NotabilityViewer.lnk')) {
        $old = Join-Path $folder $oldName
        if (Test-Path -LiteralPath $old) {
            $target = $shell.CreateShortcut($old).TargetPath
            if ($target -eq $env:NV_VIEWER_EXE -or $target -eq $env:NV_EDITOR_EXE) {
                Remove-Item -LiteralPath $old -Force
            }
        }
    }
    $link = $shell.CreateShortcut((Join-Path $folder 'NotabilityEditor.lnk'))
    $link.TargetPath = $env:NV_EDITOR_EXE
    $link.WorkingDirectory = Split-Path -Parent $env:NV_EDITOR_EXE
    $link.IconLocation = $env:NV_EDITOR_EXE + ',0'
    $link.Save()
}
'''
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
                   env=environment, check=True, capture_output=True, creationflags=flags)
