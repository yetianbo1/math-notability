"""Temporary local NTB workspace for PDF imports awaiting first user save.

The workspace is owned by the application, not the PDF importer worker, so a
successful import can remain fully editable until the user chooses Save.
Only explicitly requested Save / Save As moves data to a user-selected path.
"""
from pathlib import Path
from datetime import datetime
import tempfile


class ImportDraft:
    def __init__(self, pdf_source):
        source = Path(pdf_source).resolve()
        if source.suffix.lower() != '.pdf' or not source.is_file():
            raise ValueError('请选择可读取的 PDF 文件')
        self.pdf_source = source
        self.kind = 'pdf'
        self._storage = tempfile.TemporaryDirectory(prefix='notabilityviewer-pdf-draft-')
        # Names can be Unicode; the temporary NTB stores the PDF title correctly.
        self.path = Path(self._storage.name) / (source.stem + '.ntb')
        self.closed = False

    @property
    def suggested_path(self):
        """User's natural first-save location. No NTB is created here until Save."""
        return self.pdf_source.with_suffix('.ntb')

    def matches(self, path):
        return not self.closed and Path(path).resolve() == self.path.resolve()

    def cleanup(self):
        if not self.closed:
            self._storage.cleanup()
            self.closed = True


class NewNoteDraft:
    """Temporary NTB for a new note awaiting its first explicit save."""
    def __init__(self):
        stamp = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
        stamp += f'-{datetime.now().microsecond//1000:03d}'
        filename = f'新建笔记 {stamp}.ntb'
        self.kind = 'new'
        self._storage = tempfile.TemporaryDirectory(
            prefix='notabilityviewer-new-note-')
        self.path = Path(self._storage.name) / filename
        documents = Path.home() / 'Documents'
        self.suggested_path = (documents if documents.is_dir() else Path.home()) / filename
        self.closed = False

    def matches(self, path):
        return not self.closed and Path(path).resolve() == self.path.resolve()

    def cleanup(self):
        if not self.closed:
            self._storage.cleanup()
            self.closed = True
