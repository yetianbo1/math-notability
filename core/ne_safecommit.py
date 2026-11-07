"""Validated NTB write/commit with Windows-aware atomic replacement.

Never delete the destination before a fully validated staged NTB exists.
Never pretend that a failed original-file overwrite is successful: callers may
save a separate recovery note. Share locks and ACLs cannot always be bypassed.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path


def _win_replace_file(staged, destination):
    """Windows ReplaceFileW, which can preserve original file metadata."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.LPCWSTR, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(destination), str(staged), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def _win_posix_rename(staged, destination):
    """Try FileRenameInfoEx / POSIX semantics on recent Windows/NTFS.

    Microsoft documents REPLACE_IF_EXISTS | POSIX_SEMANTICS for replacing a
    destination that still has open handles. The operation may still fail if
    sharing modes, filesystem support or access rules forbid it. The staged
    file is opened with DELETE access and the Windows handle is always closed.
    """
    import ctypes
    from ctypes import wintypes

    target = str(Path(destination).resolve())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                       wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    # 20 is the byte offset of the inline filename on Windows x64, but use
    # ctypes offsets to account for the current Windows pointer width.
    class RenameInfo(ctypes.Structure):
        _fields_ = [('Flags', wintypes.DWORD),
                    ('RootDirectory', wintypes.HANDLE),
                    ('FileNameLength', wintypes.DWORD),
                    ('FileName', ctypes.c_wchar * (len(target) + 1))]

    name_info = RenameInfo()
    name_info.Flags = 0x1 | 0x2  # FILE_RENAME_REPLACE_IF_EXISTS | POSIX_SEMANTICS
    name_info.RootDirectory = None
    name_info.FileNameLength = len(target.encode('utf-16-le'))
    name_info.FileName = target

    DELETE = 0x00010000
    FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80
    handle = create(str(staged), DELETE, FILE_SHARE_ALL, None,
                    OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # FILE_INFO_BY_HANDLE_CLASS.FileRenameInfoEx is 22.
        if not set_info(handle, 22, ctypes.byref(name_info), ctypes.sizeof(name_info)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)


def _record_error(kind, exc):
    code = getattr(exc, 'winerror', None)
    if code is None:
        code = getattr(exc, 'errno', None)
    return (kind, code, str(exc))


def _on_windows():
    return os.name == 'nt'


def replace_with_retry(staged, destination, *, attempts=6, delay=0.15):
    """Replace validated stage atomically or raise with actual Win32 failures.

    Windows fallback order:
      os.replace -> ReplaceFileW -> FileRenameInfoEx POSIX semantics.

    A true Windows sharing violation, antivirus lockdown, denied NTFS ACL or
    Controlled Folder Access restriction is not necessarily recoverable.
    Changing just the READONLY DOS attribute is reversible on a failed write.
    """
    staged, destination = Path(staged), Path(destination)
    if attempts < 1:
        raise ValueError('attempts must be positive')
    windows = _on_windows()
    original_mode = None
    changed_mode = False
    readonly = False
    errors = []
    if windows and destination.exists():
        original_mode = destination.stat().st_mode
        readonly = not bool(original_mode & stat.S_IWRITE)
        if readonly:
            try:
                os.chmod(destination, original_mode | stat.S_IWRITE)
                changed_mode = True
            except OSError as exc:
                errors.append(_record_error('清除只读属性', exc))
    try:
        for index in range(attempts):
            try:
                os.replace(staged, destination)
                return
            except PermissionError as exc:
                errors.append(_record_error('os.replace', exc))
                if not windows or getattr(exc, 'winerror', None) not in (5, 32, 33):
                    raise

                for label, action in (
                    ('ReplaceFileW', _win_replace_file),
                    ('FileRenameInfoEx(POSIX)', _win_posix_rename),
                ):
                    if not staged.exists():
                        # A previous native operation may have moved staged;
                        # never issue another destructive replacement blindly.
                        break
                    try:
                        action(staged, destination)
                        return
                    except OSError as native_exc:
                        errors.append(_record_error(label, native_exc))

                if not staged.exists():
                    raise PermissionError(
                        'Windows 替换调用返回失败，但暂存文件已被移动。请检查目标文件，'
                        '为避免误操作，本次不再进行重试。') from exc
                if index + 1 < attempts:
                    time.sleep(delay * (index + 1))
        summary = []
        for label, code, message in errors:
            detail = f'WinError {code}' if code is not None else '未知错误码'
            line = f'{label}: {detail} — {message}'
            if line not in summary:
                summary.append(line)
        from platform_integration.ne_winlockdiag import diagnostic_lines
        diagnostics=diagnostic_lines(destination) if windows else []
        raise PermissionError(
            'Windows 未允许覆盖原 NTB；原文件仍保留。\n'
            f'目标：{destination}\n'
            f'目标文件只读：{"是（已尝试解除）" if readonly else "否"}\n'
            '最后一次覆盖尝试结果：\n' + '\n'.join(summary[-5:]) + '\n' +
            ('只读检测和可能的锁定来源：\n' + '\n'.join(diagnostics) + '\n' if diagnostics else '') +
            'WinError 5 不意味着你启用了受控文件夹访问。请优先查看上面的 DELETE 权限与占用进程。'
        )
    finally:
        if changed_mode and destination.exists() and staged.exists():
            try:
                os.chmod(destination, original_mode)
            except OSError:
                pass
