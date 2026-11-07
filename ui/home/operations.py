"""File operations for the desktop library, with reversible index updates."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re
import tempfile
import time
from functools import wraps

from PyQt6.QtCore import QFile
from settings.ne_recent_files import read_recent_files, write_recent_files, canonical_path
from settings.ne_page_history import read_position_history, write_position_history
from .library import identity, library_directory


def is_managed(path):
    return Path(path).resolve().is_relative_to(library_directory().resolve())


def rename_file(source, target):
    file = QFile(str(source))
    if not file.rename(str(target)):
        raise OSError(file.errorString())


def trash_file(path):
    file = QFile(str(path))
    if not file.moveToTrash():
        raise OSError('无法移入回收站，文件已保留。' + file.errorString())


def library_transaction(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.library.transaction():
            return method(self, *args, **kwargs)
    return run


class NotebookOperations:
    def __init__(self, library):
        self.library = library

    def entry(self, path):
        entry = self.library.data['notes'].get(identity(path))
        if entry:
            return dict(entry)
        return next(({'path': p, 'category': '', 'opened': 0}
                     for p in read_recent_files(self.library.settings)
                     if identity(p) == identity(path)), None)

    def _update_path(self, old, new=None, category=None, movement=None, *, commit=True):
        library = self.library
        before = deepcopy(library.data)
        recent = read_recent_files(library.settings)
        history = read_position_history(library.settings)
        entry = self.entry(old)
        if entry is None:
            raise ValueError('笔记不在笔记库中。')
        try:
            library.data['notes'].pop(identity(old), None)
            if movement:
                records = library.data.setdefault('movement_history', [])
                records.append(movement)
                library.data['movement_history'] = records[-1000:]
            if new:
                entry.update(path=canonical_path(new))
                if category is not None:
                    entry['category'] = category
                library.data['notes'][identity(new)] = entry
            write_recent_files(library.settings, [new if identity(p) == identity(old) else p
                for p in recent if new or identity(p) != identity(old)])
            write_position_history(library.settings, [replace(e, path=new)
                if identity(e.path) == identity(old) else e
                for e in history if new or identity(e.path) != identity(old)])
            if commit:
                library.commit()
        except Exception:
            library.data = before
            write_recent_files(library.settings, recent)
            write_position_history(library.settings, history)
            if commit:
                library.commit()
            raise
        return before, recent, history

    @library_transaction
    def move_category(self, path, category, *, reason='change_category', cleanup=True, commit=True):
        entry = self.entry(path)
        if not entry:
            raise ValueError('笔记不在笔记库中。')
        if category and category not in self.library.categories:
            raise ValueError('分类不存在。')
        if not is_managed(path):
            raise ValueError('外部笔记保留原位置，仅修改分类记录。')
        source = Path(path)
        destination = self.library.directory_for_category(category)
        if source.parent.parent.resolve() == destination.resolve():
            if entry.get('category', '') != category:
                self._update_path(path, path, category, commit=commit)
            return str(source)
        destination.mkdir(parents=True, exist_ok=True)
        # Allocate an independent destination so equal notebook names never collide.
        folder = Path(tempfile.mkdtemp(prefix='note-', dir=destination))
        target = folder / source.name
        try:
            rename_file(source, target)
            try:
                self._update_path(path, str(target), category, movement={
                    'time': time.time(), 'from_path': str(source), 'to_path': str(target),
                    'from_category': entry.get('category', ''), 'to_category': category,
                    'reason': reason}, commit=commit)
            except Exception:
                rename_file(target, source)
                raise
        except Exception:
            folder.rmdir()
            raise
        if cleanup:
            self.cleanup_empty_folders(source)
        return str(target)

    def cleanup_empty_folders(self, source):
        root = library_directory().resolve()
        if not source.parent.name.startswith('note-'):
            return
        protected = {root, self.library.directory_for_category().resolve()}
        protected.update(self.library.directory_for_category(key).resolve() for key in self.library.categories)
        for folder in (source.parent, source.parent.parent, source.parent.parent.parent):
            resolved = folder.resolve()
            if resolved in protected or not resolved.is_relative_to(root):
                continue
            try:
                folder.rmdir()
            except OSError:
                pass

    @library_transaction
    def reorganize_category(self, key, *, name=None, parent=None, remove=False):
        library = self.library
        if key not in library.categories:
            raise ValueError('分类不存在。')
        children = library.child_categories(key)
        if remove and children:
            raise ValueError('请先移动或移除二级分类，再移除一级分类；笔记不会被删除。')
        if not remove:
            name = library.validate_category(name if name is not None else library.categories[key],
                library.category_parents.get(key, '') if parent is None else parent, key=key)
        affected = {key, *children}
        old_directories = [library.directory_for_category(k) for k in affected]
        snapshot = (deepcopy(library.data), read_recent_files(library.settings),
                    read_position_history(library.settings))
        moved = []
        try:
            if not remove:
                library.categories[key] = name
                if parent is not None:
                    if parent:
                        library.category_parents[key] = parent
                    else:
                        library.category_parents.pop(key, None)
                for affected_key in affected:
                    library.directory_for_category(affected_key).mkdir(parents=True, exist_ok=True)
            for entry in list(library.entries()):
                category = entry.get('category')
                if category not in affected:
                    continue
                path = entry['path']
                if is_managed(path):
                    target = self.move_category(path, '' if remove else category,
                        reason='remove_category' if remove else 'move_category' if parent is not None else 'rename_category',
                        cleanup=False, commit=False)
                    if target != path:
                        moved.append((path, target))
            if remove:
                for entry in library.data['notes'].values():
                    if entry.get('category') == key:
                        entry['category'] = ''
                library.categories.pop(key)
                library.category_parents.pop(key, None)
            library.commit()
            for source, target in moved:
                self.cleanup_empty_folders(Path(source))
            for old_directory in sorted(old_directories, key=lambda p: len(p.parts), reverse=True):
                if not remove and old_directory in (library.directory_for_category(k) for k in affected):
                    continue
                try:
                    old_directory.rmdir()
                except OSError:
                    pass
        except Exception:
            for source, target in reversed(moved):
                rename_file(target, source)
            self.restore(snapshot)
            raise

    def restore(self, snapshot):
        data, recent, history = snapshot
        self.library.data = data
        write_recent_files(self.library.settings, recent)
        write_position_history(self.library.settings, history)
        self.library.commit()

    @library_transaction
    def rename(self, path, name, category):
        if not self.entry(path):
            raise ValueError('笔记不在笔记库中。')
        if category and category not in self.library.categories:
            raise ValueError('分类不存在。')
        source = Path(path)
        name = name.strip()
        if name.lower().endswith(source.suffix.lower()):
            name = name[:-len(source.suffix)]
        if (not name or name.endswith(('.', ' ')) or len(name.encode('utf-16-le')) > 400
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
                or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.I)):
            raise ValueError('请输入有效的笔记名称，不包含路径或系统保留字符。')
        target = source.with_name(name + source.suffix)
        if str(source) == str(target):
            self.library.classify(path, category)
            return str(target)
        if target.exists():
            raise ValueError('已有同名文件，请使用其他名称。')
        rename_file(source, target)
        try:
            self._update_path(path, str(target), category)
        except Exception:
            rename_file(target, source)
            raise
        return str(target)

    @library_transaction
    def remove(self, path, *, delete_file=False):
        snapshot = self._update_path(path)
        if delete_file:
            try:
                trash_file(path)
            except Exception:
                self.restore(snapshot)
                raise
            self.cleanup_empty_folders(Path(path))
