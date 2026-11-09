"""Single source of truth for file-backed application data.

Frozen builds keep their library, recovery snapshots, logs and compatibility
backups in ``NotabilityEditor-data`` next to the EXE, so a portable install on
another drive never scatters data into ``%LOCALAPPDATA%``.  Source checkouts
keep the historical per-user location: importing this module from the test
suite must never write into the release folder.

A registered copy of the EXE under ``%LOCALAPPDATA%\\Programs`` shares the main
EXE's data folder through ``data-root.txt``, otherwise opening a .ntb by file
association would silently create a second, empty library.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

DATA_DIR_NAME = 'NotabilityEditor-data'
OVERRIDE_ENV = 'NOTABILITYEDITOR_DATA_DIR'
POINTER_NAME = 'data-root.txt'


def legacy_data_directory():
    """The pre-0.13.0 per-user location (still used by source checkouts)."""
    return Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'NotabilityEditor'


def _pointer_target(base):
    pointer = base / POINTER_NAME
    if not pointer.is_file():
        return None
    try:
        text = pointer.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return None
    if not text:
        return None
    target = Path(text)
    return target if target.is_absolute() else None


def data_directory():
    """Directory holding the library, recovery snapshots, logs and backups."""
    override = os.environ.get(OVERRIDE_ENV, '').strip()
    if override:
        return Path(override)
    if getattr(sys, 'frozen', False):
        base = Path(sys.executable).resolve().parent
        redirected = _pointer_target(base)
        if redirected is not None:
            return redirected
        return base / DATA_DIR_NAME
    return legacy_data_directory()


def registered_program_directory():
    """Where file-association registration keeps its copy of the EXE."""
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        return None
    root = Path(local) / 'Programs' / 'NotabilityEditor'
    return root if root.is_dir() else None


def publish_data_pointer():
    """Keep a registered copy pointed at the data folder of the running EXE."""
    if not getattr(sys, 'frozen', False):
        return
    base = Path(sys.executable).resolve().parent
    if _pointer_target(base) is not None:
        return                      # this IS the redirected copy
    root = registered_program_directory()
    if root is None or root.resolve() == base:
        return
    try:
        (root / POINTER_NAME).write_text(str(data_directory()), encoding='utf-8')
    except OSError:
        pass


def _relocate_index(target, old_root, new_root):
    """Rewrite library entries that pointed inside the old data folder.

    Keys are ``identity(path)`` values, so both the path and its key must be
    updated; otherwise recent-file ranking and duplicate detection break.
    """
    from settings.ne_recent_files import canonical_path
    old = os.path.normcase(str(old_root))
    new = str(new_root)
    # The rolling .bak is a fallback index; leaving old absolute paths in it
    # would resurrect C: references after a damaged main index is restored.
    for name in ('home-library.json', 'home-library.json.bak'):
        index = target / name
        if not index.is_file():
            continue
        try:
            data = json.loads(index.read_text(encoding='utf-8'))
            notes = data['library']['notes']
            if not isinstance(notes, dict):
                continue
        except (OSError, ValueError, KeyError, TypeError):
            continue
        changed = False
        rebuilt = {}
        for key, entry in notes.items():
            if isinstance(entry, dict) and isinstance(entry.get('path'), str):
                path = entry['path']
                if os.path.normcase(path).startswith(old + os.sep):
                    path = new + path[len(old):]
                    entry = dict(entry, path=path)
                    key = os.path.normcase(canonical_path(path))
                    changed = True
            rebuilt[key] = entry
        if not changed:
            continue
        data['library']['notes'] = rebuilt
        temporary = index.with_name(index.name + '.tmp')
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                 encoding='utf-8')
            os.replace(temporary, index)
        except OSError:
            temporary.unlink(missing_ok=True)


def ensure_data_directory():
    """First-run copy of the old per-user data, without deleting the original."""
    target = data_directory()
    source = legacy_data_directory()
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        return target
    try:
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            return target
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
            _relocate_index(target, source, target)
    except OSError:
        pass                        # never block startup on a data copy
    return target
