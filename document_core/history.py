"""Incremental index over the existing chronological command log."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ChangeSet:
    sequence: int
    pages: frozenset
    global_change: bool


class HistoryIndex:
    def __init__(self):
        self.applied = []
        self.pages = {}
        self.global_ops = []
        self.sequence = 0
        self.signature = None
        self.last_change = ChangeSet(0, frozenset(), False)
        self.indexed_commands = 0

    @staticmethod
    def owners(op):
        if op['kind'] in ('compound', 'transfer'):
            return frozenset(op.get('states', ()))
        if op['kind'] in ('draw', 'erase', 'move', 'delete', 'scale', 'transform'):
            return frozenset({op.get('page'), op.get('target')} - {None})
        # Page order, external image changes and unknown commands are global.
        return None

    def sync(self, history, cursor):
        signature = (cursor, history[cursor-1][0] if cursor else None)
        if signature == self.signature:
            return self
        changed = set()
        global_change = False
        while self.applied:
            i = len(self.applied)-1
            if i < cursor and self.applied[i][0] == history[i][0]:
                break
            token, op = self.applied.pop()
            owners = self.owners(op)
            if owners is None:
                self.global_ops.pop(); global_change = True
            else:
                changed.update(owners)
                for page in owners:
                    self.pages[page].pop()
                    if not self.pages[page]: del self.pages[page]
        for i in range(len(self.applied), cursor):
            record = history[i]
            self.applied.append(record)
            owners = self.owners(record[1])
            if owners is None:
                self.global_ops.append(record); global_change = True
            else:
                changed.update(owners)
                for page in owners: self.pages.setdefault(page, []).append(record)
            self.indexed_commands += 1
        self.sequence += 1
        self.signature = signature
        self.last_change = ChangeSet(self.sequence, frozenset(changed), global_change)
        return self

    def revision(self, owners):
        return (self.global_ops[-1][0] if self.global_ops else None,
                tuple((page, self.pages[page][-1][0] if page in self.pages else None)
                      for page in sorted(owners)))
