"""Idempotent, backed-up migrations for notes created by this application.

New releases append narrowly scoped repairs to MIGRATIONS. Eligibility is
checked from file contents, never just from the running application version.
Unchanged notes take no write path and need no sidecar migration marker.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from zipfile import ZipFile, BadZipFile

from formats.ne_bundle_identity import (bundle_namespaces,
    repair_generated_namespaces, repair_generated_paper, repair_generated_paper_pattern)


@dataclass(frozen=True)
class CompatibilityResult:
    status: str
    repairs: tuple = ()
    backup: str | None = None
    error: str | None = None


def _namespace_repair(data, _archive):
    return repair_generated_namespaces(data)


MIGRATIONS = (
    ('generated-root-namespaces-v4', _namespace_repair),
    ('generated-custom-paper-colour-v6', repair_generated_paper),
    ('generated-native-paper-pattern-v7', repair_generated_paper_pattern),
)


def backup_directory():
    from platform_integration.ne_install import data_directory
    return data_directory() / 'compatibility-backups'


def _stamp(path):
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _hash(stream, check):
    digest = hashlib.sha256()
    while True:
        check()
        chunk = stream.read(4 * 1024 * 1024)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def repair_on_open(path, *, cancelled=lambda: False):
    """Repair before the loader captures its editing baseline, or defer safely.

    A byte-exact, content-addressed backup must exist before atomic replacement.
    Failed, read-only, externally changed or unfamiliar files are left alone.
    """
    from formats.ne_loader import LoadCancelled
    def check():
        if cancelled():
            raise LoadCancelled()
    path = Path(path)
    if path.suffix.lower() != '.ntb':
        return CompatibilityResult('unchanged')
    staged = backup_stage = None
    repairs = []
    backup = None
    try:
        check()
        original_stamp = _stamp(path)
        with path.open('rb') as original, ZipFile(original) as archive:
            try:
                manifest = json.loads(archive.read('manifest.json'))
            except (KeyError, ValueError, UnicodeError):
                return CompatibilityResult('unchanged')
            if not isinstance(manifest, dict) or manifest.get('appVersion') != 'NotabilityViewer':
                return CompatibilityResult('unchanged')
            before = archive.read('noteBundle')
            after = before
            for identifier, migrate in MIGRATIONS:
                check()
                updated = migrate(after, archive)
                if updated != after:
                    repairs.append(identifier)
                    after = updated
            if not repairs:
                return CompatibilityResult('unchanged')
            bundle_namespaces(after)
            if not path.stat().st_mode & stat.S_IWRITE:
                return CompatibilityResult('deferred', tuple(repairs), error='File is read-only')
            original.seek(0)
            digest = _hash(original, check)
            folder = backup_directory()
            folder.mkdir(parents=True, exist_ok=True)
            backup = folder / (digest + '.ntb.bak')
            if backup.exists():
                with backup.open('rb') as stream:
                    if _hash(stream, check) != digest:
                        raise ValueError('Existing compatibility backup failed hash verification')
            else:
                fd, backup_stage = tempfile.mkstemp(prefix='.compat-', dir=folder)
                original.seek(0)
                with os.fdopen(fd, 'wb') as output:
                    while True:
                        check()
                        chunk = original.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                with open(backup_stage, 'rb') as stream:
                    if _hash(stream, check) != digest:
                        raise ValueError('Compatibility backup failed hash verification')
                os.replace(backup_stage, backup)
                backup_stage = None
            fd, staged = tempfile.mkstemp(prefix='.ne-compat-', suffix='.ntb', dir=path.parent)
            os.close(fd)
            with ZipFile(staged, 'w', allowZip64=True) as output:
                output.comment = archive.comment
                for info in archive.infolist():
                    check()
                    if info.filename == 'noteBundle':
                        output.writestr(copy.copy(info), after)
                    else:
                        with archive.open(info) as source, output.open(copy.copy(info), 'w', force_zip64=True) as target:
                            while True:
                                check()
                                chunk = source.read(4 * 1024 * 1024)
                                if not chunk:
                                    break
                                target.write(chunk)
            with ZipFile(staged) as checked:
                if checked.namelist() != archive.namelist() or checked.comment != archive.comment:
                    raise ValueError('Compatibility archive member validation failed')
                if checked.read('noteBundle') != after:
                    raise ValueError('Compatibility archive verification failed')
                for info in archive.infolist():
                    if info.filename != 'noteBundle':
                        saved = checked.getinfo(info.filename)
                        if (saved.CRC, saved.file_size) != (info.CRC, info.file_size):
                            raise ValueError('Compatibility repair changed an asset')
                        with checked.open(info.filename) as stream:
                            while True:
                                check()
                                if not stream.read(4 * 1024 * 1024):
                                    break
            with open(staged, 'r+b') as stream:
                stream.flush()
                os.fsync(stream.fileno())
        # Close every original ZIP/file handle before Windows replacement.
        check()
        if _stamp(path) != original_stamp:
            raise ValueError('File changed during compatibility repair')
        with path.open('rb') as current:
            if _hash(current, check) != digest:
                raise ValueError('File contents changed during compatibility repair')
        shutil.copystat(path, staged)
        check()
        if _stamp(path) != original_stamp:
            raise ValueError('File changed before compatibility commit')
        os.replace(staged, path)
        staged = None
        return CompatibilityResult('repaired', tuple(repairs), str(backup))
    except LoadCancelled:
        raise
    except (OSError, ValueError, KeyError, BadZipFile) as exc:
        from infrastructure.ne_diagnostics import message
        message('WARNING', 'Automatic note compatibility repair deferred: ' + type(exc).__name__)
        return CompatibilityResult('deferred', tuple(repairs), str(backup) if backup else None, str(exc))
    finally:
        for temporary in (staged, backup_stage):
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
