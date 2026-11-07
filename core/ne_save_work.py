"""Bounded, operation-local reuse of immutable checked note data."""
from collections import OrderedDict
from contextvars import ContextVar
from functools import wraps
from inspect import signature
from time import monotonic

_active = ContextVar('save_work', default=None)
_session_cache = ContextVar('session_validation_cache', default=None)


def checkpoint():
    work = _active.get()
    if work is not None:
        work.poll()


class _Work:
    def __init__(self, progress, cancelled):
        self.progress, self.cancelled = progress, cancelled
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.started = monotonic()
        self.last_poll = 0
        self.last_progress = -1

    def emit(self, value):
        if value > self.last_progress:
            self.last_progress = value
            self.progress(value)

    def poll(self):
        now = monotonic()
        if now - self.last_poll < .05:
            return
        self.last_poll = now
        if self.cancelled():
            from formats.ne_loader import LoadCancelled
            raise LoadCancelled()
        self.emit(min(29, 1 + int((now - self.started) * 2)))


def checked_once(function):
    @wraps(function)
    def checked(data, *args, **kwargs):
        work = _active.get()
        if work is None or type(data) is not bytes or args or kwargs:
            return function(data, *args, **kwargs)
        work.poll()
        key = (function, data)
        if key in work.cache:
            work.cache.move_to_end(key)
            return work.cache[key]
        result = function(data)
        work.cache[key] = result
        work.cache_bytes += len(data)
        while len(work.cache) > 16 or work.cache_bytes > 64 * 1024 * 1024:
            removed, _ = work.cache.popitem(last=False)
            work.cache_bytes -= len(removed[1])
        return result
    return checked


def save_operation(function):
    params = signature(function)
    @wraps(function)
    def save(*args, **kwargs):
        bound = params.bind(*args, **kwargs)
        bound.apply_defaults()
        if _active.get() is not None:
            return function(*args, **kwargs)
        work = _Work(bound.arguments['progress'], bound.arguments['cancelled'])
        shared=_session_cache.get()
        if shared is not None:
            work.cache=shared
            work.cache_bytes=sum(len(key[1]) for key in shared)
        # Reserve the initial range for native page work. Archive copy and
        # validation retain their own measured byte/stage progress afterward.
        first = True
        def report(value):
            nonlocal first
            if first and value == 0:
                first = False
                work.poll()
                return
            first = False
            work.emit(30 + int(value * .7) if value < 100 else 100)
        bound.arguments['progress'] = report
        token = _active.set(work)
        try:
            work.poll()
            return function(*bound.args, **bound.kwargs)
        finally:
            if shared is None:work.cache.clear()
            _active.reset(token)
    return save


from contextlib import contextmanager


@contextmanager
def session_validation(cache):
    """Reuse exact immutable bytes in a bounded, session-owned read index."""
    token=_session_cache.set(cache)
    try:yield
    finally:
        while len(cache)>2 or sum(len(key[1]) for key in cache)>16*1024*1024:
            cache.popitem(last=False)
        _session_cache.reset(token)
