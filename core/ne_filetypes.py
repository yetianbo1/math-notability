"""File entry-point policy; deliberately Qt-free for reliable test coverage.

Opening is reserved for actual Notability NOTE/NTB files; importing PDFs
creates a separate NTB notebook and never modifies the source PDF.
"""
from pathlib import Path

OPEN_SUFFIXES = frozenset({'.ntb', '.note'})
IMPORT_SUFFIXES = frozenset({'.pdf'})


def file_action(path):
    """Return 'open', 'import' or None, accepting case-insensitive extensions."""
    extension = Path(path).suffix.lower()
    if extension in OPEN_SUFFIXES:
        return 'open'
    if extension in IMPORT_SUFFIXES:
        return 'import'
    return None
