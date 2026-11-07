"""Persistent references and categories; importing never overwrites the source."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from contextlib import contextmanager
from functools import wraps

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from settings.ne_recent_files import canonical_path, read_recent_files
from .storage import (LibraryStore, validate_library, merge_library, LibraryConflictError,
                      compatible_library, decode_library)
from .hierarchy import validate_category_name, validate_hierarchy, category_rows


def identity(path):
    return os.path.normcase(canonical_path(path))


def library_edit(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return run


class Library:
    KEY = 'windows_home/library'

    def __init__(self, settings, *, index_path=None):
        self.settings = settings
        self._transaction_depth = 0
        self.store = LibraryStore(index_path)
        self.data = self.store.load()
        migrated = self.data is None
        self.storage_notice = self.store.notice
        if migrated:
            raw = settings.value(self.KEY, '', type=str)
            try:
                self.data = decode_library(json.loads(raw) if raw else {})
            except (ValueError, TypeError):
                settings.setValue(self.KEY + '_recovery', raw)
                self.data = validate_library({})
        self._committed_data = deepcopy(self.data)
        try:
            changed = self.adopt_legacy_history()
            if (migrated or self.store.recovered or self.store.needs_compatibility_save) and not changed:
                self.commit()
        except OSError as exc:
            self.storage_notice = str(exc)

    def adopt_legacy_history(self):
        """0.7.6-Beta uses the same QSettings namespace and array schema.

        Promote references into the library index without copying notebooks or
        changing the original recent-file/reading-position records. Missing
        files remain indexed and are hidden by entries() until available.
        """
        positions={}
        count=self.settings.beginReadArray('view/last-pages')
        try:
            for i in range(count):
                self.settings.setArrayIndex(i)
                path=self.settings.value('path','',type=str)
                try:updated=float(self.settings.value('updated_at',0))
                except (TypeError,ValueError):updated=0
                if path:positions[identity(path)]=updated
        finally:
            self.settings.endArray()
        changed=False
        recent=read_recent_files(self.settings)
        for rank,path in enumerate(recent):
            key=identity(path)
            if key in self.data['notes']:continue
            self.data['notes'][key]=dict(path=path,category='',
                opened=positions.get(key) or time.time()-rank)
            changed=True
        if changed:self.commit()
        return changed

    @property
    def categories(self):
        return self.data['categories']

    @property
    def category_parents(self):
        return self.data['category_parents']

    def child_categories(self, key):
        return [child for child in self.categories if self.category_parents.get(child) == key]

    def category_rows(self):
        return list(category_rows(self.categories, self.category_parents))

    def category_label(self, key):
        if key not in self.categories:
            return '未分类'
        parent = self.category_parents.get(key)
        return (self.categories[parent] + ' / ' if parent else '') + self.categories[key]

    def directory_for_category(self, key=''):
        if not key:
            return category_directory()
        if key not in self.categories:
            raise ValueError('分类不存在。')
        directory = category_directory(self.categories[key])
        parent = self.category_parents.get(key)
        if parent:
            directory = category_directory(self.categories[parent]) / validate_category_name(self.categories[key])
        root = library_directory().resolve()
        if not directory.resolve().is_relative_to(root):
            raise ValueError('分类目录必须位于本机笔记库内。')
        return directory

    def validate_category(self, name, parent='', *, key=None):
        name = validate_category_name(name)
        if parent and (parent not in self.categories or self.category_parents.get(parent)):
            raise ValueError('请选择现有的一级分类；分类最多支持两级。')
        categories, parents = dict(self.categories), dict(self.category_parents)
        candidate = key or '@new'
        categories[candidate] = name
        if parent:
            parents[candidate] = parent
        else:
            parents.pop(candidate, None)
        validate_hierarchy(categories, parents)
        return name

    def commit(self):
        try:
            self.data = self.store.save(self.data, base=self._committed_data)
        except LibraryConflictError:
            current = self.store.load()
            self.data = current if current is not None else deepcopy(self._committed_data)
            self._committed_data = deepcopy(self.data)
            raise
        except (OSError, ValueError) as exc:
            self.data = deepcopy(self._committed_data)
            raise OSError('无法保存分类与笔记索引，请检查用户数据目录：'
                          + str(self.store.path.parent) + '\n' + str(exc)) from exc
        self._committed_data = deepcopy(self.data)
        # Retain a compatibility mirror for older releases; JSON is authoritative.
        self.settings.setValue(self.KEY, json.dumps(compatible_library(self.data), ensure_ascii=False))
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            self.storage_notice = '分类已保存到用户数据文件，旧版设置同步未完成。'

    @contextmanager
    def transaction(self):
        """Refresh before touching files, and hold the lock through rollback."""
        with self.store.locked():
            if not self._transaction_depth:
                current = self.store.load()
                if current is not None:
                    self.data = merge_library(self._committed_data, self.data, current)
                    self._committed_data = deepcopy(current)
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1

    @library_edit
    def record(self, path):
        path = canonical_path(path)
        entry = self.data['notes'].setdefault(identity(path), {'path': path, 'category': ''})
        entry.update(path=path, opened=time.time())
        self.commit()

    @library_edit
    def add_category(self, name, parent=''):
        name = self.validate_category(name, parent)
        key = uuid.uuid4().hex
        before = deepcopy(self.data)
        self.categories[key] = name
        if parent:
            self.category_parents[key] = parent
        try:
            self.directory_for_category(key).mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError):
            self.data = before
            raise
        self.commit()
        return key

    @library_edit
    def rename_category(self, key, name):
        if key not in self.categories:
            raise ValueError('分类不存在。')
        name = self.validate_category(name, self.category_parents.get(key, ''), key=key)
        from .operations import NotebookOperations
        NotebookOperations(self).reorganize_category(key, name=name)

    @library_edit
    def move_category(self, key, parent=''):
        if key not in self.categories:
            raise ValueError('分类不存在。')
        self.validate_category(self.categories[key], parent, key=key)
        from .operations import NotebookOperations
        NotebookOperations(self).reorganize_category(key, parent=parent)

    @library_edit
    def remove_category(self, key):
        from .operations import NotebookOperations
        NotebookOperations(self).reorganize_category(key, remove=True)

    @library_edit
    def classify(self, path, category):
        if category and category not in self.categories:
            raise ValueError('分类不存在。')
        path = canonical_path(path)
        from .operations import NotebookOperations, is_managed
        if is_managed(path) and Path(path).is_file():
            return NotebookOperations(self).move_category(path, category)
        entry = self.data['notes'].setdefault(identity(path), {'path': path})
        entry['category'] = category
        self.commit()

    def entries(self):
        entries = {key: dict(value) for key, value in self.data['notes'].items()}
        recent = read_recent_files(self.settings)
        for path in recent:
            entries.setdefault(identity(path), {'path': path, 'category': '', 'opened': 0})
        rank = {identity(path): i for i, path in enumerate(recent)}
        return sorted((e for e in entries.values() if Path(e['path']).is_file()), key=lambda e: (
            rank.get(identity(e['path']), len(rank)), -e.get('opened', 0), e['path']))


def library_directory():
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.local' / 'share')
    return base / 'NotabilityEditor' / 'library'


def category_directory(name='未分类'):
    if name != '未分类':
        validate_category_name(name)
    root = library_directory().resolve()
    directory = root / name
    if not directory.resolve().is_relative_to(root):
        raise ValueError('分类目录必须位于本机笔记库内。')
    return directory


class ImportCancelled(Exception):
    pass


def copy_note(source, directory, progress=lambda value: None, cancelled=lambda: False):
    """Commit into a unique directory; partial files never become library entries."""
    source = Path(source).resolve(strict=True)
    if source.suffix.lower() not in ('.ntb', '.note') or not source.is_file():
        raise ValueError('导入笔记仅支持 NTB 和 NOTE 文件。')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='note-', dir=directory))
    target = folder / source.name
    partial = folder / '.importing'
    try:
        with source.open('rb') as incoming, partial.open('xb') as outgoing:
            before = os.fstat(incoming.fileno())
            total = 0
            while True:
                if cancelled():
                    raise ImportCancelled()
                block = incoming.read(1024 * 1024)
                if not block:
                    break
                outgoing.write(block)
                total += len(block)
                progress(min(99, round(total * 100 / max(1, before.st_size))))
            after = os.fstat(incoming.fileno())
            if total != before.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise OSError('源文件在导入期间发生变化，请稍后重试。')
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if cancelled():
            raise ImportCancelled()
        os.replace(partial, target)
        progress(100)
        return str(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        folder.rmdir()
        raise


class ImportWorker(QThread):
    progress = pyqtSignal(int)
    ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.stop = threading.Event()
        self.result = None

    def run(self):
        try:
            self.result = copy_note(self.source, category_directory(), self.progress.emit, self.stop.is_set)
            self.ready.emit(self.result)
        except ImportCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))
