"""Verified migration from Viewer data/settings; Editor only uses new storage."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _plain_path(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('应用数据迁移路径不能包含链接。')


def _move_verified(source, target):
    """Keep existing Editor data; retain conflicting Viewer bytes separately."""
    _plain_path(source)
    _plain_path(target)
    expected = _digest(source)
    if target.exists() and (not target.is_file() or _digest(target) != expected):
        target = target.with_name(target.name + '.from-NotabilityViewer-' + expected[:16])
        _plain_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _digest(target) != expected:
            raise ValueError('迁移备份目标已存在且内容不同。')
    else:
        descriptor, name = tempfile.mkstemp(prefix='.viewer-migrate-', dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'wb') as output, source.open('rb') as original:
                for chunk in iter(lambda: original.read(1024 * 1024), b''):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if _digest(temporary) != expected or _digest(source) != expected:
                raise OSError('旧数据在迁移期间发生变化，原文件已保留。')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    # Delete the source only after both the saved copy and original match.
    if _digest(target) != expected or _digest(source) != expected:
        raise OSError('迁移内容校验失败，原文件已保留。')
    source.unlink()
    return target


def _migrate_settings(destination, factory=None):
    from PyQt6.QtCore import QSettings
    factory = factory or QSettings
    total = 0
    pairs = (('NotabilityViewer', 'NotabilityViewer', 'NotabilityEditor'),
             ('NotabilityViewer', 'Display', 'Display'),
             ('Notability Viewer', 'Notability Viewer', 'NotabilityEditor'))
    for organization, application, new_application in pairs:
        old = factory(organization, application)
        old.sync()
        keys = old.allKeys()
        if not keys:
            continue
        new = factory('NotabilityEditor', new_application)
        backup = QSettings(str(destination / ('migrated-' + organization.replace(' ', '-') + '-' + application.replace(' ', '-') + '.ini')), QSettings.Format.IniFormat)
        values = {key: old.value(key) for key in keys}
        for key, value in values.items():
            backup.setValue(key, value)
            if not new.contains(key):
                new.setValue(key, value)
        backup.sync()
        new.sync()
        if backup.status() != QSettings.Status.NoError or new.status() != QSettings.Status.NoError:
            raise OSError('无法写入新版设置，旧版设置已保留。')
        for key, value in values.items():
            if backup.value(key) != value or not new.contains(key):
                raise OSError('新版设置校验失败，旧版设置已保留。')
        for key, value in values.items():
            if old.value(key) != value:
                raise OSError('旧版设置在迁移期间改变，请关闭旧程序后重试。')
        for key in values:
            old.remove(key)
        old.sync()
        if old.status() != QSettings.Status.NoError or any(old.contains(key) for key in values):
            raise OSError('设置已迁移，旧版设置暂时无法删除。')
        total += len(values)
        del old
        if factory is QSettings and os.name == 'nt':
            import winreg
            key = 'Software\\' + organization + '\\' + application
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                    children, count, _ = winreg.QueryInfoKey(handle)
                if children == count == 0:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass
    return total


def migrate_user_data(*, base=None, settings_factory=None):
    """Move verified bytes and settings; report failures without losing originals.

    A failed/in-use source is left for the next startup to retry. Existing new
    settings win, with old values retained in an INI inside the new data folder.
    """
    from PyQt6.QtCore import QLockFile
    parent = Path(base or os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())
    source, destination = parent / 'NotabilityViewer', parent / 'NotabilityEditor'
    _plain_path(source)
    _plain_path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(destination / '.viewer-migration.lock'))
    if not lock.tryLock(5000):
        return {'status': 'pending', 'errors': ['另一进程正在迁移旧版数据。']}
    report = {'status': 'completed', 'moved_files': 0, 'settings': 0, 'errors': []}
    try:
        if source.exists():
            directories = [source]
            try:
                for current, subdirs, files in os.walk(source, followlinks=False, onerror=lambda error: report['errors'].append(str(error))):
                    current = Path(current)
                    for name in tuple(subdirs):
                        directory = current / name
                        try:
                            _plain_path(directory)
                            directories.append(directory)
                        except (OSError, ValueError) as error:
                            subdirs.remove(name)
                            report['errors'].append(str(error))
                    for name in files:
                        original = current / name
                        try:
                            _move_verified(original, destination / original.relative_to(source))
                            report['moved_files'] += 1
                        except (OSError, ValueError) as error:
                            report['errors'].append(str(error))
                for directory in reversed(directories):
                    if directory.exists() and not any(directory.iterdir()):
                        directory.rmdir()
            except OSError as error:
                report['errors'].append(str(error))
        try:
            report['settings'] = _migrate_settings(destination, settings_factory)
        except (OSError, ValueError) as error:
            report['errors'].append(str(error))
        if report['errors']:
            report['status'] = 'pending'
        report['legacy_data_removed'] = not source.exists()
        if report['moved_files'] or report['settings'] or report['errors'] or not (destination / 'viewer-migration.json').exists():
            temporary = destination / '.viewer-migration.json.tmp'
            try:
                temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, destination / 'viewer-migration.json')
            except OSError as error:
                temporary.unlink(missing_ok=True)
                report['status'] = 'pending'
                report['errors'].append('无法写入迁移报告：' + str(error))
        return report
    finally:
        lock.unlock()
