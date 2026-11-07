"""Read-only Win32 file-access and Restart Manager diagnostics.

Never delete, rename, close or modify the inspected destination. Windows
Restart Manager may not identify antivirus filters or every short-lived lock.
"""
from __future__ import annotations
import os
from pathlib import Path


def deletion_access_probe(path):
    if os.name != 'nt':
        return '仅 Windows 支持 DELETE 访问检测'
    import ctypes
    from ctypes import wintypes
    k32=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=k32.CreateFileW
    fn.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,
                 wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    fn.restype=wintypes.HANDLE
    closer=k32.CloseHandle
    closer.argtypes=[wintypes.HANDLE]
    closer.restype=wintypes.BOOL
    # DELETE access, allowing read/write/delete sharing. This operation
    # does NOT change the target. It distinguishes denial from a share lock.
    handle=fn(str(Path(path).resolve()),0x10000,0x7,None,3,0x80,None)
    if handle==ctypes.c_void_p(-1).value:
        code=ctypes.get_last_error()
        return f'DELETE 权限探测失败：WinError {code} ({ctypes.FormatError(code).strip()})'
    closer(handle)
    return 'DELETE 权限探测通过（并不保证后续替换不受其他进程阻止）'


def locking_processes(path):
    """Return (pid, appname) tuples reported by Windows Restart Manager."""
    if os.name!='nt':return []
    import ctypes
    from ctypes import wintypes
    class FILETIME(ctypes.Structure):
        _fields_=[('dwLowDateTime',wintypes.DWORD),('dwHighDateTime',wintypes.DWORD)]
    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_=[('dwProcessId',wintypes.DWORD),('ProcessStartTime',FILETIME)]
    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_=[('Process',RM_UNIQUE_PROCESS),('strAppName',wintypes.WCHAR*256),
                  ('strServiceShortName',wintypes.WCHAR*64),('ApplicationType',wintypes.DWORD),
                  ('AppStatus',wintypes.DWORD),('TSSessionId',wintypes.DWORD),
                  ('bRestartable',wintypes.BOOL)]
    rm=ctypes.WinDLL('Rstrtmgr',use_last_error=True)
    start=rm.RmStartSession
    start.argtypes=[ctypes.POINTER(wintypes.DWORD),wintypes.DWORD,wintypes.LPWSTR]
    start.restype=wintypes.DWORD
    register=rm.RmRegisterResources
    register.argtypes=[wintypes.DWORD,wintypes.UINT,ctypes.POINTER(wintypes.LPCWSTR),
                       wintypes.UINT,ctypes.c_void_p,wintypes.UINT,ctypes.c_void_p]
    register.restype=wintypes.DWORD
    getlist=rm.RmGetList
    getlist.argtypes=[wintypes.DWORD,ctypes.POINTER(wintypes.UINT),
                      ctypes.POINTER(wintypes.UINT),ctypes.POINTER(RM_PROCESS_INFO),
                      ctypes.POINTER(wintypes.DWORD)]
    getlist.restype=wintypes.DWORD
    end=rm.RmEndSession
    end.argtypes=[wintypes.DWORD]
    end.restype=wintypes.DWORD
    session=wintypes.DWORD(0)
    key=ctypes.create_unicode_buffer(33)
    code=start(ctypes.byref(session),0,key)
    if code:raise OSError(f'RmStartSession 返回错误 {code}')
    try:
        resources=(wintypes.LPCWSTR*1)(str(Path(path).resolve()))
        code=register(session.value,1,resources,0,None,0,None)
        if code:raise OSError(f'RmRegisterResources 返回错误 {code}')
        needed=wintypes.UINT(0); count=wintypes.UINT(0); restart_reason=wintypes.DWORD(0)
        code=getlist(session.value,ctypes.byref(needed),ctypes.byref(count),None,ctypes.byref(restart_reason))
        if code==0:return []
        if code!=234:raise OSError(f'RmGetList 返回错误 {code}')
        for _ in range(3):
            entries=(RM_PROCESS_INFO*max(1,needed.value))()
            count=wintypes.UINT(len(entries))
            code=getlist(session.value,ctypes.byref(needed),ctypes.byref(count),entries,
                         ctypes.byref(restart_reason))
            if code==0:
                return [(p.Process.dwProcessId,p.strAppName) for p in entries[:count.value]]
            if code!=234:raise OSError(f'RmGetList 返回错误 {code}')
        raise OSError('锁定进程列表在读取时反复发生变化')
    finally:
        end(session.value)


def diagnostic_lines(path):
    """Best-effort read-only snapshot, suitable for a GUI failure message."""
    if os.name!='nt':return []
    lines=[]
    try:lines.append(deletion_access_probe(path))
    except Exception as exc:lines.append('DELETE 权限探测不可用：'+str(exc))
    try:
        processes=locking_processes(path)
        if processes:
            lines.append('Windows 检测到可能占用目标的进程：'+
                         ', '.join(f'{name or "未知进程"}(PID {pid})' for pid,name in processes[:8]))
        else:
            lines.append('Restart Manager 未报告持有该文件的进程（不排除短暂占用或过滤驱动）')
    except Exception as exc:lines.append('占用进程检测不可用：'+str(exc))
    return lines
