"""Registration ordering: a same-base relese outranks every internal vN."""
import ctypes
import json
import os
from pathlib import Path
import re
from core.ne_identity import LEGACY_APP_ID, LEGACY_CAPABILITIES, LEGACY_UNINSTALL

_VERSION=re.compile(r'^(\d+)\.(\d+)\.(\d+)(-Beta)?(?:-(?:v(\d+)|(relese|release)))?$',re.I)
_CAPABILITIES=r'Software\NotabilityEditor\Capabilities'
_UNINSTALL=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\NotabilityEditor'
_COMMAND=r'Software\Classes\Applications\NotabilityEditor.exe\shell\open\command'


class VersionDowngradeError(ValueError):
    """Raised before file replacement or registry mutation."""


def version_key(value):
    if not isinstance(value,str) or len(value)>200:
        raise ValueError('无法识别应用版本号')
    match=_VERSION.fullmatch(value.strip())
    if not match:raise ValueError('无法识别应用版本号：'+value)
    major,minor,patch,beta,iteration,_=match.groups()
    return (int(major),int(minor),int(patch),0 if beta else 1,
            0 if iteration is not None else 1,int(iteration or 0))


def executable_version(path):
    """Read the EXE's string resource; never infer its version from a filename."""
    if os.name!='nt' or not Path(path).is_file():return ''
    from ctypes import wintypes as w
    dll=ctypes.WinDLL('version',use_last_error=True)
    dll.GetFileVersionInfoSizeW.argtypes=[w.LPCWSTR,ctypes.POINTER(w.DWORD)]
    dll.GetFileVersionInfoSizeW.restype=w.DWORD
    dll.GetFileVersionInfoW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID]
    dll.GetFileVersionInfoW.restype=w.BOOL
    dll.VerQueryValueW.argtypes=[w.LPCVOID,w.LPCWSTR,ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(w.UINT)]
    dll.VerQueryValueW.restype=w.BOOL
    size=dll.GetFileVersionInfoSizeW(str(path),None)
    if not size:return ''
    buf=ctypes.create_string_buffer(size)
    if not dll.GetFileVersionInfoW(str(path),0,size,buf):return ''
    pointer=ctypes.c_void_p();length=w.UINT()
    if not dll.VerQueryValueW(buf,r'\VarFileInfo\Translation',ctypes.byref(pointer),ctypes.byref(length)):
        return ''
    pairs=ctypes.cast(pointer,ctypes.POINTER(w.WORD))
    languages=[(pairs[i],pairs[i+1]) for i in range(0,length.value//2-1,2)]
    for language,codepage in languages:
        for name in ('ProductVersion','FileVersion'):
            query=f'\\StringFileInfo\\{language:04x}{codepage:04x}\\{name}'
            if dll.VerQueryValueW(buf,query,ctypes.byref(pointer),ctypes.byref(length)) and length.value:
                value=ctypes.wstring_at(pointer,length.value).rstrip('\0')
                try:version_key(value)
                except ValueError:continue
                return value
    return ''


def ensure_registration_allowed(incoming,*,registry=None,installation=None):
    """Keep the newest owned installed/registered identity, including old markers."""
    candidate=version_key(incoming)
    if registry is None:
        if os.name!='nt':return
        import winreg as registry
    def query(key,name):
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,registry.KEY_QUERY_VALUE) as handle:
                value,_=registry.QueryValueEx(handle,name)
                return str(value)
        except (OSError,FileNotFoundError):return ''
    versions=[query(key,name) for key,name in (
        (_CAPABILITIES,'ApplicationVersion'),(_UNINSTALL,'DisplayVersion'),
        (LEGACY_CAPABILITIES,'ApplicationVersion'),(LEGACY_UNINSTALL,'DisplayVersion'))]
    programs=Path(os.environ.get('LOCALAPPDATA',''))/'Programs'
    roots={Path(installation) if installation is not None else programs/'NotabilityEditor',
           programs/'NotabilityViewer'}
    for root in roots:
        marker=root/'installation.json'
        if marker.is_file():
            try:
                info=json.loads(marker.read_text(encoding='utf-8'))
                if info.get('app_id') in ('NotabilityEditor.Desktop',LEGACY_APP_ID):
                    versions.append(info.get('version',''))
            except (ValueError,OSError,AttributeError):pass
        for name in ('NotabilityEditor.exe','NotabilityViewer.exe'):
            versions.append(executable_version(root/name))
    for key in (_COMMAND,r'Software\Classes\Applications\NotabilityViewer.exe\shell\open\command'):
        target=re.match(r'^\s*"([^"\r\n]+)"',query(key,''))
        if target:versions.append(executable_version(target[1]))
    newest=None
    for value in versions:
        try:key=version_key(value)
        except ValueError:continue
        if newest is None or key>newest[0]:newest=(key,value)
    if newest and newest[0]>candidate:
        label=newest[1]+('（relese）' if newest[0][4] else '')
        raise VersionDowngradeError(f'已安装或注册的版本 {label} 高于当前版本 {incoming}。已保留较新程序和 Windows 注册，请使用较新版本进行注册。')
