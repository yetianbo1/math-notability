"""UI-independent document indexing and immutable save values.

This directory is the canonical source. Release assembly copies it unchanged
into each application; neither Qt nor a product-specific module is imported.
"""
from .history import HistoryIndex, ChangeSet
from .snapshot import SnapshotStore, thaw
