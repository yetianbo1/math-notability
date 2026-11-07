"""Per-note reading-position history stored in the application settings."""
from dataclasses import dataclass
import math
import os
import time

from settings.ne_recent_files import canonical_path


ARRAY_KEY = 'view/last-pages'
MAX_PAGE_HISTORY = 50
MAX_HISTORY_AGE_SECONDS = 365 * 24 * 60 * 60


@dataclass(frozen=True)
class ReadingPosition:
    path: str
    page: int
    zoom: float | None = None
    vertical: float = 0.0
    horizontal: float = 0.0
    fit_width: bool = True
    updated_at: float = 0.0


def _identity(path):
    return os.path.normcase(canonical_path(path))


def _page_number(value):
    if isinstance(value, bool):
        return None
    try:
        page = int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return page if page > 0 else None


def _zoom_number(value):
    try:
        zoom = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return zoom if math.isfinite(zoom) and 0.2 <= zoom <= 5.0 else None


def _horizontal_fraction(value):
    try:
        fraction = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return min(1.0, max(0.0, fraction)) if math.isfinite(fraction) else 0.0


def _timestamp(value):
    try:
        updated = float(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return updated if math.isfinite(updated) and updated > 0 else 0.0


def read_position_history(settings, *, limit=MAX_PAGE_HISTORY):
    """Return newest-first reading positions."""
    now=time.time()
    size = settings.beginReadArray(ARRAY_KEY)
    entries = []
    seen = set()
    needs_write = False
    try:
        for index in range(size):
            settings.setArrayIndex(index)
            path = settings.value('path', '', type=str)
            page = _page_number(settings.value('page', '', type=str))
            if not path or page is None:
                needs_write = True
                continue
            path = canonical_path(path)
            identity = _identity(path)
            if identity in seen:
                needs_write = True
                continue
            seen.add(identity)
            updated_at=_timestamp(settings.value('updated_at','',type=str))
            if not updated_at:
                # Old v6/v7 page histories did not store a timestamp.
                updated_at=now;needs_write=True
            if updated_at < now-MAX_HISTORY_AGE_SECONDS:
                needs_write=True
                continue
            entries.append(ReadingPosition(
                path=path,
                page=page,
                zoom=_zoom_number(settings.value('zoom', '', type=str)),
                vertical=_horizontal_fraction(
                    settings.value('vertical_fraction', '0', type=str)),
                horizontal=_horizontal_fraction(
                    settings.value('horizontal_fraction', '0', type=str)),
                fit_width=settings.value('fit_width', True, type=bool),
                updated_at=updated_at,
            ))
            if len(entries) >= limit:
                needs_write = needs_write or index < size-1
                break
    finally:
        settings.endArray()
    if needs_write:
        write_position_history(settings, entries, limit=limit, now=now)
    return entries


def write_position_history(settings, entries, *, limit=MAX_PAGE_HISTORY, now=None):
    now=time.time() if now is None else float(now)
    cutoff=now-MAX_HISTORY_AGE_SECONDS
    unique = []
    seen = set()
    for item in entries:
        page = _page_number(item.page)
        if not item.path or page is None:
            continue
        updated_at=_timestamp(item.updated_at) or now
        if updated_at < cutoff:
            continue
        path = canonical_path(item.path)
        identity = _identity(path)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(ReadingPosition(
            path=path,
            page=page,
            zoom=_zoom_number(item.zoom) if item.zoom is not None else None,
            vertical=_horizontal_fraction(item.vertical),
            horizontal=_horizontal_fraction(item.horizontal),
            fit_width=bool(item.fit_width),
            updated_at=updated_at,
        ))
        if len(unique) >= limit:
            break
    settings.beginWriteArray(ARRAY_KEY, len(unique))
    try:
        for index, item in enumerate(unique):
            settings.setArrayIndex(index)
            settings.setValue('path', item.path)
            settings.setValue('page', item.page)
            if item.zoom is None:
                settings.remove('zoom')
            else:
                settings.setValue('zoom', item.zoom)
            settings.setValue('vertical_fraction', item.vertical)
            settings.setValue('horizontal_fraction', item.horizontal)
            settings.setValue('fit_width', item.fit_width)
            settings.setValue('updated_at', item.updated_at)
    finally:
        settings.endArray()
        settings.sync()
    return unique


def read_page_history(settings, *, limit=MAX_PAGE_HISTORY):
    """Backward-compatible ``(path, page)`` view of the stored positions."""
    return [(item.path, item.page)
            for item in read_position_history(settings, limit=limit)]


def write_page_history(settings, entries, *, limit=MAX_PAGE_HISTORY):
    positions = (ReadingPosition(canonical_path(path), _page_number(page))
                 for path, page in entries)
    return [(item.path, item.page)
            for item in write_position_history(settings, positions, limit=limit)]


def last_position_for(settings, path):
    identity = _identity(path)
    return next((item for item in read_position_history(settings)
                 if _identity(item.path) == identity), None)


def last_page_for(settings, path):
    position = last_position_for(settings, path)
    return position.page if position else None


def remember_last_position(settings, path, page, *, zoom=None, vertical=0.0,
                           horizontal=0.0, fit_width=True,
                           limit=MAX_PAGE_HISTORY):
    page = _page_number(page)
    if not path or page is None:
        return False
    identity = _identity(path)
    current = read_position_history(settings, limit=limit)
    entries = [ReadingPosition(
        canonical_path(path), page, zoom, vertical, horizontal, fit_width)]
    entries.extend(item for item in current if _identity(item.path) != identity)
    write_position_history(settings, entries, limit=limit)
    return True


def remember_last_page(settings, path, page, *, limit=MAX_PAGE_HISTORY):
    return remember_last_position(settings, path, page, limit=limit)


def forget_last_page(settings, path, *, limit=MAX_PAGE_HISTORY):
    identity = _identity(path)
    current = read_position_history(settings, limit=limit)
    remaining = [item for item in current if _identity(item.path) != identity]
    return write_position_history(settings, remaining, limit=limit)


def clear_last_pages(settings):
    return write_page_history(settings, ())

