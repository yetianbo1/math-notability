"""Per-user Open With registration; default selection remains in Windows UI.

https://learn.microsoft.com/windows/apps/develop/launch/launch-default-apps-settings
No writes to Explorer FileExts/UserChoice or the machine registry.
"""
import ctypes
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from core.ne_identity import PRODUCT_NAME, LEGACY_DISPLAY_NAMES, LEGACY_CAPABILITIES

APP_NAME=PRODUCT_NAME
APP_ID='NotabilityEditor.Desktop'
CAPABILITIES=r'Software\NotabilityEditor\Capabilities'
APPLICATION_NAME='NotabilityEditor.exe'
APPLICATIONS_ROOT=r'Software\Classes\Applications'
OPEN_WITH_LIST=r'Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts'
FILE_TYPES={'.ntb':('NotabilityViewer.NTB','Notability 笔记'),
            '.note':('NotabilityViewer.NOTE','Notability 旧版笔记')}
ASSOCIATION_BACKUP=r'Software\NotabilityViewer\AssociationBackup'
_NOTABILITY_EXE=re.compile(r'^Notability(?:Viewer|Editor)(?:[-_. ].*)?\.exe$',re.IGNORECASE)

def icon_path():
    return Path(__file__).resolve().parent.parent/'assets'/'notability-viewer.ico'

def set_app_identity():
    if sys.platform=='win32':
        try:ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except OSError:pass


def _query_string(registry,key,name):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,
                              registry.KEY_QUERY_VALUE) as handle:
            value,_=registry.QueryValueEx(handle,name)
            return str(value)
    except (FileNotFoundError,OSError):
        return ''


def _delete_value(registry,key,name):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,
                              registry.KEY_SET_VALUE) as handle:
            registry.DeleteValue(handle,name)
    except (FileNotFoundError,OSError):
        pass


def _delete_tree(registry,key):
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,
                              registry.KEY_READ|registry.KEY_SET_VALUE) as handle:
            children=[]
            index=0
            while True:
                try:children.append(registry.EnumKey(handle,index))
                except OSError:break
                index+=1
        for child in children:
            _delete_tree(registry,key+'\\'+child)
        registry.DeleteKey(registry.HKEY_CURRENT_USER,key)
    except (FileNotFoundError,OSError):
        pass


def _clean_open_with_list(registry,extension):
    key=OPEN_WITH_LIST+'\\'+extension+r'\OpenWithList'
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,
                              registry.KEY_READ|registry.KEY_SET_VALUE) as handle:
            names=[]
            index=0
            while True:
                try:name,_,_=registry.EnumValue(handle,index)
                except OSError:break
                if name!='MRUList':names.append(name)
                index+=1
            values={name:_query_string(registry,key,name) for name in names}
    except (FileNotFoundError,OSError):
        return
    stale=[];kept=[];canonical_name=None
    for value_name,value in values.items():
        executable=Path(value.strip().strip('"')).name
        if not _NOTABILITY_EXE.fullmatch(executable):
            kept.append(value_name)
            continue
        if executable.casefold()==APPLICATION_NAME.casefold() and canonical_name is None:
            canonical_name=value_name;kept.append(value_name)
        else:
            stale.append(value_name)
    for value_name in stale:_delete_value(registry,key,value_name)
    if canonical_name is None:
        canonical_name=next((chr(code) for code in range(ord('a'),ord('z')+1)
                             if chr(code) not in values),None)
        if canonical_name is not None:
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,key,0,
                                      registry.KEY_SET_VALUE) as handle:
                registry.SetValueEx(handle,canonical_name,0,registry.REG_SZ,
                                    APPLICATION_NAME)
            kept.append(canonical_name)
    mru=_query_string(registry,key,'MRUList')
    ordered=[]
    for value_name in mru+''.join(sorted(kept)):
        if value_name in kept and value_name not in ordered:ordered.append(value_name)
    if canonical_name in ordered:
        ordered.remove(canonical_name);ordered.insert(0,canonical_name)
    with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,key,0,
                              registry.KEY_SET_VALUE) as handle:
        registry.SetValueEx(handle,'MRUList',0,registry.REG_SZ,''.join(ordered))


def _legacy_default_applications(registry):
    """Keep identities selected before the application key became stable."""
    selected={}
    for extension in FILE_TYPES:
        for progid in (_query_string(registry,OPEN_WITH_LIST+'\\'+extension+r'\UserChoice','ProgId'),
                       _query_string(registry,'Software\\Classes\\'+extension,'')):
            parts=progid.split('\\')
            if (len(parts)==2 and parts[0].casefold()=='applications' and
                    _NOTABILITY_EXE.fullmatch(parts[1]) and
                    parts[1].casefold()!=APPLICATION_NAME.casefold()):
                selected[parts[1].casefold()]=parts[1]
    return selected


def _cleanup_legacy_registrations(registry,executable):
    """Redirect selected old identities, remove only unselected old aliases."""
    selected=_legacy_default_applications(registry)
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,APPLICATIONS_ROOT,0,
                              registry.KEY_READ) as handle:
            index=0;applications=[]
            while True:
                try:name=registry.EnumKey(handle,index)
                except OSError:break
                if _NOTABILITY_EXE.fullmatch(name):applications.append(name)
                index+=1
    except (FileNotFoundError,OSError):
        applications=[]
    for name in applications:
        if name.casefold()==APPLICATION_NAME.casefold():continue
        if name.casefold() not in selected:
            _delete_tree(registry,APPLICATIONS_ROOT+'\\'+name)
    # UserChoice's ProgId and Hash stay intact. Update the command behind the
    # selected identity, even if an earlier cleanup removed its registry key.
    for name in selected.values():
        key=APPLICATIONS_ROOT+'\\'+name
        for suffix,value_name,value in (
                ('','FriendlyAppName',APP_NAME),('','NoOpenWith',''),
                ('','NotabilityViewerCompatibility','1'),
                (r'\DefaultIcon','',f'"{executable}",0'),
                (r'\shell\open\command','',f'"{executable}" "%1"')):
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,key+suffix,0,
                                      registry.KEY_SET_VALUE) as handle:
                registry.SetValueEx(handle,value_name,0,registry.REG_SZ,value)
    for extension in FILE_TYPES:
        _clean_open_with_list(registry,extension)
    registered=r'Software\RegisteredApplications'
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER,registered,0,
                              registry.KEY_READ) as handle:
            names=[];index=0
            while True:
                try:name,_,_=registry.EnumValue(handle,index)
                except OSError:break
                names.append(name);index+=1
    except (FileNotFoundError,OSError):
        names=[]
    for name in names:
        legacy_name = name.casefold() in {value.casefold() for value in LEGACY_DISPLAY_NAMES}
        if ((name.casefold().startswith(APP_NAME.casefold()) and name!=APP_NAME)
                or (legacy_name and _query_string(registry,registered,name) in (CAPABILITIES, LEGACY_CAPABILITIES))):
            _delete_value(registry,registered,name)


def registry_entries(executable):
    """Return only our registration values; never claim the default directly."""
    executable=Path(executable).resolve()
    if not executable.is_file() or executable.suffix.lower()!='.exe':
        raise ValueError('请使用打包后的 EXE 注册默认打开程序。')
    path=str(executable)
    if any(c in path for c in ('"','\r','\n')):raise ValueError('程序路径无效')
    command=f'"{path}" "%1"'
    icon=f'"{path}",0'  # Stable EXE resource, never a onefile extraction directory.
    entries=[(CAPABILITIES,'ApplicationName',APP_NAME),
             (CAPABILITIES,'ApplicationDescription','本地查看和修改 Notability 笔记'),
             (CAPABILITIES,'ApplicationIcon',icon)]
    # Keep one Open With identity across every release. The command still
    # points at the selected EXE, while Applications\NotabilityEditor.exe is
    # overwritten when a newer build is registered.
    application=APPLICATIONS_ROOT+'\\'+APPLICATION_NAME
    entries.extend([(application,'FriendlyAppName',APP_NAME),
                    (application+r'\DefaultIcon','',icon),
                    (application+r'\shell\open\command','',command)])
    for extension,(progid,label) in FILE_TYPES.items():
        key='Software\\Classes\\'+progid
        entries.extend([(key,'',label),(key,'FriendlyTypeName',label),
                        (key+r'\DefaultIcon','',icon),
                        (key+r'\shell','', 'open'),
                        (key+r'\shell\open\command','',command),
                        (key+r'\Application','ApplicationName',APP_NAME),
                        (key+r'\Application','ApplicationIcon',icon),
                        (CAPABILITIES+r'\FileAssociations',extension,progid),
                        ('Software\\Classes\\'+extension+r'\OpenWithProgids',progid,''),
                        (application+r'\SupportedTypes',extension,'')])
    # Publish the capability registration last, after its ProgIDs are ready.
    entries.append((r'Software\RegisteredApplications',APP_NAME,CAPABILITIES))
    return entries

def register_application(executable=None,registry=None,notify=True):
    if executable is None:
        if not getattr(sys,'frozen',False):raise ValueError('请运行 EXE 版本以注册默认打开程序。')
        executable=sys.executable
    entries=registry_entries(executable)
    if registry is None:
        import winreg as registry
    from platform_integration.ne_install import VERSION
    from core.ne_version import ensure_registration_allowed
    ensure_registration_allowed(VERSION,registry=registry)
    entries.insert(-1,(CAPABILITIES,'ApplicationVersion',VERSION))
    # Viewer releases only know their old capability key. Keep a redirect
    # version marker there so an old installer cannot downgrade Editor.
    for name,value in (('ApplicationVersion',VERSION),('ApplicationName',APP_NAME),
                       ('CompatibilityTarget',str(Path(executable).resolve()))):
        entries.insert(-1,(LEGACY_CAPABILITIES,name,value))
    _cleanup_legacy_registrations(registry,Path(executable).resolve())
    for key,name,value in entries:
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,key,0,registry.KEY_SET_VALUE) as handle:
            registry.SetValueEx(handle,name,0,registry.REG_SZ,value)
    if notify and sys.platform=='win32':
        # SHCNE_ASSOCCHANGED / SHCNF_IDLIST: refresh Explorer's association cache.
        ctypes.windll.shell32.SHChangeNotify(0x08000000,0,None,None)

def open_default_settings():
    uri='ms-settings:defaultapps'
    if sys.getwindowsversion().build>=22000:
        uri+='?registeredAppUser='+quote(APP_NAME,safe='')
    try:os.startfile(uri)
    except OSError:
        if '?' not in uri:raise
        os.startfile('ms-settings:defaultapps')


def associated_executable(extension):
    """Ask the shell for the effective executable, including protected choices."""
    if sys.platform!='win32':return ''
    from ctypes import wintypes
    query=ctypes.windll.shlwapi.AssocQueryStringW
    query.argtypes=[wintypes.DWORD,ctypes.c_int,wintypes.LPCWSTR,
                    wintypes.LPCWSTR,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    query.restype=ctypes.c_long
    size=wintypes.DWORD(32768)
    buffer=ctypes.create_unicode_buffer(size.value)
    # ASSOCSTR_EXECUTABLE; never launch a note to test its association.
    result=query(0,2,extension,'open',buffer,ctypes.byref(size))
    return buffer.value if result==0 else ''


def default_status(executable,query=None):
    query=query or associated_executable
    target=os.path.normcase(os.path.abspath(executable))
    return {ext:bool((value:=query(ext)) and
                    os.path.normcase(os.path.abspath(value))==target)
            for ext in FILE_TYPES}


def apply_default_associations(executable,registry=None,query=None,notify=True):
    """Associate unprotected types and verify the shell's actual selection.

    UserChoice keys are never changed. Save the previous per-user fallback
    once so uninstall can restore it when our fallback is still in place.
    """
    if registry is None:
        import winreg as registry
    status=default_status(executable,query)
    for ext,(progid,_) in FILE_TYPES.items():
        if status[ext]:continue
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER,
                                  OPEN_WITH_LIST+'\\'+ext+r'\UserChoice',0,
                                  registry.KEY_READ):
                continue
        except FileNotFoundError:pass
        key='Software\\Classes\\'+ext
        backup=ASSOCIATION_BACKUP+'\\'+ext
        if _query_string(registry,backup,'saved')!='1':
            try:
                with registry.OpenKey(registry.HKEY_CURRENT_USER,key,0,
                                      registry.KEY_QUERY_VALUE) as handle:
                    previous,kind=registry.QueryValueEx(handle,'')
                if kind!=registry.REG_SZ:continue
                present=True
            except FileNotFoundError:
                previous='';present=False
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,backup,0,
                                      registry.KEY_SET_VALUE) as handle:
                for name,value in (('previous',previous),('present','1' if present else '0'),('saved','1')):
                    registry.SetValueEx(handle,name,0,registry.REG_SZ,value)
        with registry.CreateKeyEx(registry.HKEY_CURRENT_USER,key,0,
                                  registry.KEY_SET_VALUE) as handle:
            registry.SetValueEx(handle,'',0,registry.REG_SZ,progid)
    if notify and sys.platform=='win32':
        ctypes.windll.shell32.SHChangeNotify(0x08000000,0x1000,None,None)
    result=default_status(executable,query)
    if notify:
        deadline=time.monotonic()+2
        while not all(result.values()) and time.monotonic()<deadline:
            time.sleep(.1)
            result=default_status(executable,query)
    return result
