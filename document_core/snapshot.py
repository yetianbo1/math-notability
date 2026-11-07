"""Detached immutable values, shared between successive save snapshots."""
import copy


def _readonly(*args, **kwargs):
    raise TypeError('Save snapshot is read-only')


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly
    def __deepcopy__(self, memo): return thaw(self)


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _readonly
    def __deepcopy__(self, memo): return thaw(self)


class FrozenSet(set):
    add = clear = difference_update = discard = intersection_update = pop = remove = symmetric_difference_update = update = __ior__ = __iand__ = __ixor__ = __isub__ = _readonly
    def __deepcopy__(self, memo): return thaw(self)


def freeze(value):
    if isinstance(value, dict): return FrozenDict((k, freeze(v)) for k, v in value.items())
    if isinstance(value, list): return FrozenList(freeze(v) for v in value)
    if isinstance(value, set): return FrozenSet(freeze(v) for v in value)
    if isinstance(value, tuple): return tuple(freeze(v) for v in value)
    if isinstance(value, frozenset): return frozenset(freeze(v) for v in value)
    # Stroke.__deepcopy__ retains immutable geometry and drops GUI path caches.
    return copy.deepcopy(value)


def thaw(value):
    if isinstance(value, dict): return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, list): return [thaw(v) for v in value]
    if isinstance(value, set): return {thaw(v) for v in value}
    if isinstance(value, tuple): return tuple(thaw(v) for v in value)
    if isinstance(value, frozenset): return frozenset(thaw(v) for v in value)
    return copy.deepcopy(value)


class SnapshotStore:
    """One retained value per component; active workers own older snapshots."""
    def __init__(self):
        self.parts = {}
        self.hits = 0
        self.misses = 0

    def get(self, name, revision, produce):
        old = self.parts.get(name)
        if old is not None and old[0] == revision:
            self.hits += 1
            return old[1]
        value = freeze(produce())
        self.parts[name] = (revision, value)
        self.misses += 1
        return value
