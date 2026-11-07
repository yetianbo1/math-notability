"""Persistent most-recently-used list for local note files."""
from pathlib import Path
import os


MAX_RECENT_FILES = 100
VISIBLE_RECENT_FILES = 10
ARRAY_KEY = 'recent/files'


def canonical_path(path):
    """Return a stable absolute path without requiring the file to exist."""
    value = Path(os.path.expandvars(os.path.expanduser(str(path))))
    try:
        return str(value.resolve(strict=False))
    except OSError:
        return str(value.absolute())


def _identity(path):
    return os.path.normcase(canonical_path(path))


def read_recent_files(settings, *, limit=MAX_RECENT_FILES):
    size = settings.beginReadArray(ARRAY_KEY)
    paths = []
    seen = set()
    try:
        for index in range(size):
            settings.setArrayIndex(index)
            value = settings.value('path', '', type=str)
            if not value:
                continue
            path = canonical_path(value)
            identity = _identity(path)
            if identity in seen:
                continue
            seen.add(identity)
            paths.append(path)
            if len(paths) >= limit:
                break
    finally:
        settings.endArray()
    return paths


def write_recent_files(settings, paths, *, limit=MAX_RECENT_FILES):
    unique = []
    seen = set()
    for path in paths:
        value = canonical_path(path)
        identity = _identity(value)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(value)
        if len(unique) >= limit:
            break
    settings.beginWriteArray(ARRAY_KEY, len(unique))
    try:
        for index, path in enumerate(unique):
            settings.setArrayIndex(index)
            settings.setValue('path', path)
    finally:
        settings.endArray()
        settings.sync()
    return unique


def add_recent_file(settings, path, *, limit=MAX_RECENT_FILES):
    value = canonical_path(path)
    identity = _identity(value)
    current = read_recent_files(settings, limit=limit)
    paths = [value]
    paths.extend(item for item in current if _identity(item) != identity)
    return write_recent_files(settings, paths, limit=limit)


def remove_recent_file(settings, path, *, limit=MAX_RECENT_FILES):
    identity = _identity(path)
    current = read_recent_files(settings, limit=limit)
    return write_recent_files(
        settings, (item for item in current if _identity(item) != identity),
        limit=limit)


def prune_missing_recent_files(settings, *, limit=MAX_RECENT_FILES):
    current = read_recent_files(settings, limit=limit)
    existing = [path for path in current if Path(path).is_file()]
    if existing != current:
        write_recent_files(settings, existing, limit=limit)
    return existing


def clear_recent_files(settings, *, limit=MAX_RECENT_FILES):
    return write_recent_files(settings, (), limit=limit)
