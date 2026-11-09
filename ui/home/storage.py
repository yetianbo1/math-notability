"""Version-independent user data, with atomic writes and a last-good backup."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import uuid
import threading
from contextlib import contextmanager
from copy import deepcopy

from PyQt6.QtCore import QLockFile
from .hierarchy import validate_hierarchy


class LibraryConflictError(OSError):
    """A stale window must not overwrite another editor's changes."""


_MISSING = object()
_CATEGORY_NAMES = 'category_names_v8'
_CATEGORY_ASSIGNMENT = 'category_assignment_v8'


def _unique_label(name, key, used):
    """Legacy names must be globally unique, valid folder names and <=64 chars."""
    label = name[:64].rstrip(' .')
    attempt = 0
    while label.casefold() in used:
        suffix = '-' + hashlib.sha256((key + ':' + str(attempt)).encode('utf8')).hexdigest()[:8]
        label = name[:64-len(suffix)].rstrip(' .') + suffix
        attempt += 1
    used.add(label.casefold())
    return label


def compatible_library(data):
    """Old clients see only roots; optional metadata preserves child ownership."""
    validate_library(data)
    result = deepcopy(data)
    parents = data['category_parents']
    result['categories'] = {key: name for key, name in data['categories'].items() if key not in parents}
    result['category_parents'] = {}
    result[_CATEGORY_NAMES] = dict(version=2, children={
        key: dict(name=data['categories'][key], parent=parent) for key, parent in parents.items()})
    for entry in result['notes'].values():
        category = entry.get('category', '')
        if category in parents:
            parent = parents[category]
            entry[_CATEGORY_ASSIGNMENT] = dict(category=category, parent=parent)
            entry['category'] = parent
    return result


def decode_library(data):
    """Read v1/v2 data and reconcile edits made by older flat-category clients."""
    if not isinstance(data, dict):
        return validate_library(data)
    data = deepcopy(data)
    metadata = data.pop(_CATEGORY_NAMES, None)
    if metadata is None:
        return validate_library(data)
    if isinstance(metadata, dict) and isinstance(metadata.get('version'), int) and metadata['version'] > 2:
        raise OSError('分类层级由更新版本创建，请使用较新版本打开。')
    if isinstance(metadata, dict) and metadata.get('version') == 2:
        return _decode_root_projection(data, metadata)
    if not isinstance(metadata, dict) or metadata.get('version') != 1 or not isinstance(metadata.get('names'), dict):
        raise ValueError('Invalid category name metadata')
    raw = data.get('categories')
    parents = data.get('category_parents', {})
    if not isinstance(raw, dict) or not isinstance(parents, dict):
        raise ValueError('Invalid categories')
    if any(not isinstance(parent, str) for parent in parents.values()):
        raise ValueError('Invalid category parents')
    # An older release may have removed a parent without knowing its children.
    parents = {key: parent for key, parent in parents.items() if key in raw and parent in raw}
    data['category_parents'] = parents
    restored, reserved = {}, {}
    deferred = []
    for key, label in raw.items():
        if not isinstance(key, str) or not isinstance(label, str):
            raise ValueError('Invalid categories')
        info = metadata['names'].get(key, {})
        if isinstance(info, dict) and info.get('legacy') == label and isinstance(info.get('name'), str):
            deferred.append((key, info['name'], label))
        else:
            # A new or renamed category in an old release takes its visible name.
            restored[key] = label
            reserved.setdefault(parents.get(key, ''), set()).add(label.casefold())
    for key, name, label in deferred:
        used = reserved.setdefault(parents.get(key, ''), set())
        restored[key] = _unique_label(name if name.casefold() not in used else label, key, used)
    data['categories'] = {key: restored[key] for key in raw}
    return validate_library(data)


def _decode_root_projection(data, metadata):
    """Root edits are authoritative; a legacy move overrides hidden membership."""
    children = metadata.get('children')
    categories = data.get('categories')
    if not isinstance(children, dict) or not isinstance(categories, dict):
        raise ValueError('Invalid category hierarchy metadata')
    parents = {}
    for key, info in children.items():
        if (not isinstance(key, str) or not isinstance(info, dict)
                or not isinstance(info.get('name'), str) or not isinstance(info.get('parent'), str)
                or key in categories or info['parent'] in children):
            raise ValueError('Invalid hidden child category')
        # Removing a root in an old release also removes its hidden category tree.
        # The legacy writer has already moved its notes to the uncategorized group.
        if info['parent'] in categories:
            parents[key] = info['parent']
    for key, parent in parents.items():
        categories[key] = children[key]['name']
    data['category_parents'] = parents
    notes = data.get('notes', {})
    if not isinstance(notes, dict):
        raise ValueError('Invalid notebook references')
    for entry in notes.values():
        if not isinstance(entry, dict):
            raise ValueError('Invalid notebook reference')
        assignment = entry.pop(_CATEGORY_ASSIGNMENT, None)
        if assignment is None:
            continue
        if (not isinstance(assignment, dict) or not isinstance(assignment.get('category'), str)
                or not isinstance(assignment.get('parent'), str)):
            raise ValueError('Invalid hidden notebook category')
        child, parent = assignment['category'], assignment['parent']
        if parents.get(child) == parent and entry.get('category', '') == parent:
            entry['category'] = child
    return validate_library(data)


def needs_compatibility_save(document):
    """Upgrade both the original schema 2 and the earlier flat-child projection."""
    data = document.get('library', {})
    metadata = data.get(_CATEGORY_NAMES, {}) if isinstance(data, dict) else {}
    return (document.get('schema_version') == 2
            or (isinstance(metadata, dict) and metadata.get('version') == 1)
            or (isinstance(data, dict) and bool(data.get('category_parents'))))


def merge_library(base, local, remote):
    """Three-way merge: unchanged fields follow disk; conflicting edits fail."""
    def merge(old, ours, theirs, path=()):
        if ours == old:
            return deepcopy(theirs) if theirs is not _MISSING else _MISSING
        if theirs == old or ours == theirs:
            return deepcopy(ours) if ours is not _MISSING else _MISSING
        if all(isinstance(v, dict) for v in (old, ours, theirs)):
            result = {}
            for key in old.keys() | ours.keys() | theirs.keys():
                value = merge(old.get(key, _MISSING), ours.get(key, _MISSING),
                              theirs.get(key, _MISSING), path + (key,))
                if value is not _MISSING:
                    result[key] = value
            return result
        if len(path) == 3 and path[0] == 'notes' and path[2] == 'opened':
            if all(isinstance(v, (int, float)) for v in (ours, theirs)):
                return max(ours, theirs)
        if path == ('movement_history',) and all(isinstance(v, list) for v in (ours, theirs)):
            previous = old if isinstance(old, list) else []
            added = [v for v in ours if v not in previous and v not in theirs]
            return sorted(deepcopy(theirs + added), key=lambda v: v.get('time', 0))[-1000:]
        raise LibraryConflictError('分类或笔记已被另一窗口修改，本次操作未保存。请刷新后重试。')

    result = merge(base, local, remote)
    try:
        validate_hierarchy(result['categories'], result.get('category_parents', {}))
    except ValueError as exc:
        raise LibraryConflictError('分类层级或名称已被另一窗口修改，请刷新后重试。\n' + str(exc)) from exc
    if any(entry.get('category') and entry['category'] not in result['categories']
           for entry in result['notes'].values()):
        raise LibraryConflictError('分类已被另一窗口删除或修改，本次操作未保存。请刷新后重试。')
    return validate_library(result)


def library_index_path():
    from core.ne_paths import data_directory
    return data_directory() / 'home-library.json'


def validate_library(data):
    if (not isinstance(data, dict)
            or not isinstance(data.get('categories', {}), dict)
            or not isinstance(data.get('notes', {}), dict)):
        raise ValueError('Invalid library index')
    if any(not isinstance(k, str) or not isinstance(v, str)
           for k, v in data.get('categories', {}).items()):
        raise ValueError('Invalid categories')
    for key, entry in data.get('notes', {}).items():
        if (not isinstance(key, str) or not isinstance(entry, dict)
                or not isinstance(entry.get('path'), str)
                or not isinstance(entry.get('category', ''), str)
                or not isinstance(entry.get('opened', 0), (int, float))):
            raise ValueError('Invalid notebook reference')
    data.setdefault('categories', {})
    data.setdefault('notes', {})
    data.setdefault('category_parents', {})
    validate_hierarchy(data['categories'], data['category_parents'])
    return data


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=path.name + '.', suffix='.tmp',
                                         dir=path.parent, delete=False) as output:
            name = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name is not None:
            name.unlink(missing_ok=True)


class LibraryStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else library_index_path()
        self.backup = self.path.with_suffix('.json.bak')
        self.notice = ''
        self.recovered = False
        self.needs_compatibility_save = False
        self._mutex = threading.RLock()
        self._lock_depth = 0
        self._file_lock = None

    @contextmanager
    def locked(self):
        # The mutex also makes reentrancy safe when worker threads share a store.
        with self._mutex:
            if not self._lock_depth:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                lock = QLockFile(str(self.path) + '.lock')
                if not lock.tryLock(3000):
                    raise OSError('另一窗口正在更新分类与笔记索引，请稍后重试。')
                self._file_lock = lock
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
                if not self._lock_depth:
                    self._file_lock.unlock()
                    self._file_lock = None

    @staticmethod
    def decode(raw):
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError('Invalid user data')
        version = document.get('schema_version')
        if isinstance(version, int) and version > 2:
            raise OSError('分类数据由更新版本创建，请使用较新版本打开。')
        if version not in (1, 2):
            raise ValueError('Invalid user data version')
        return decode_library(document.get('library'))

    def load(self):
        with self.locked():
            return self._load()

    def _load(self):
        broken = []
        for path in (self.path, self.backup):
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            try:
                data = self.decode(raw)
            except (ValueError, TypeError, UnicodeError):
                preserved = path.with_name(path.name + '.corrupt-' + uuid.uuid4().hex)
                write_atomic(preserved, raw)
                broken.append(path)
                continue
            if path == self.backup:
                self.recovered = True
                self.notice = '分类数据已从备份恢复；原始文件已保留。' if broken else '分类数据已从备份恢复。'
            self.needs_compatibility_save = needs_compatibility_save(json.loads(raw))
            return data
        if broken:
            self.notice = '分类数据无法读取，已保留原文件，并尝试恢复旧版设置。'
        return None

    def save(self, data, *, base=None):
        with self.locked():
            current = self._load()
            if base is not None:
                data = merge_library(base, data, current if current is not None else base)
            self._save(data)
            return deepcopy(data)

    def _save(self, data):
        validate_library(data)
        raw = json.dumps(dict(schema_version=1, library=compatible_library(data)), ensure_ascii=False,
                         indent=2, allow_nan=False).encode('utf-8')
        try:
            previous = self.path.read_bytes()
        except FileNotFoundError:
            previous = None
        if previous is not None:
            try:
                self.decode(previous)
            except (ValueError, TypeError, UnicodeError):
                previous = None
        if previous is not None:
            if needs_compatibility_save(json.loads(previous)):
                # Keep an exact permanent copy of the earlier incompatible index,
                # while making the last-good backup readable by old clients too.
                digest = hashlib.sha256(previous).hexdigest()
                preserved = self.path.with_name(self.path.stem + '.before-v8-compat-' + digest + '.json')
                if not preserved.exists():
                    write_atomic(preserved, previous)
                if preserved.read_bytes() != previous:
                    raise OSError('无法校验分类兼容修复前的备份。')
                previous = json.dumps(dict(schema_version=1, library=compatible_library(self.decode(previous))),
                                      ensure_ascii=False, indent=2, allow_nan=False).encode('utf8')
            write_atomic(self.backup, previous)
        write_atomic(self.path, raw)
        self.needs_compatibility_save = False
