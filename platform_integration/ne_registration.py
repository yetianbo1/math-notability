"""Read the active Editor or legacy Viewer registration without changing it."""
import os
from pathlib import Path
import re
import sys

from core.ne_identity import LEGACY_CAPABILITIES, LEGACY_DISPLAY_NAMES
from core.ne_version import executable_version, version_key
from platform_integration.ne_windows import (APP_NAME, CAPABILITIES,
    APPLICATION_NAME, APPLICATIONS_ROOT, _query_string)


def registered_version_order(registration, version):
    """Return -1/0/1 for a valid registered version, or None if unverifiable."""
    if not registration or not registration.get('version_consistent', True):
        return None
    try:
        registered = version_key(registration.get('version'))
        current = version_key(version)
    except ValueError:
        return None
    return (registered > current) - (registered < current)


def registration_matches_version(registration, version):
    """Require the exact release/iteration and consistent registry/EXE metadata."""
    return registered_version_order(registration, version) == 0


def registered_application(*, registry=None):
    if registry is None:
        if sys.platform != 'win32':
            return None
        import winreg as registry
    names = (APP_NAME, *LEGACY_DISPLAY_NAMES)
    for capabilities, application in (
            (CAPABILITIES, APPLICATION_NAME),
            (LEGACY_CAPABILITIES, 'NotabilityViewer.exe')):
        published = any(_query_string(registry, r'Software\RegisteredApplications', name).casefold()
                        == capabilities.casefold() for name in names)
        if not published or _query_string(registry, capabilities, 'ApplicationName') not in names:
            continue
        command = _query_string(registry,
            APPLICATIONS_ROOT + '\\' + application + r'\shell\open\command', '')
        match = re.match(r'^\s*"([^"\r\n]+\.exe)"(?:\s|$)', command, re.IGNORECASE)
        if match is None:
            continue
        executable = Path(os.path.expandvars(match[1]))
        try:
            if not executable.is_file():
                continue
        except OSError:
            continue
        recorded_version = _query_string(registry, capabilities, 'ApplicationVersion')
        binary_version = executable_version(executable)
        info = {'path': str(executable), 'version': binary_version or recorded_version}
        if recorded_version and binary_version:
            info['version_consistent'] = registration_matches_version(
                {'version': recorded_version}, binary_version)
        return info
    return None
