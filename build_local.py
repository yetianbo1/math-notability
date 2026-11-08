"""Build this local fork's one-file Windows EXE.

Usage (from the repository root):
    .build-venv\\Scripts\\python.exe build_local.py

The version lives in exactly one place: core/ne_app_version.py.  This script
regenerates windows_version_info.ver and names the EXE from that value, so
bumping the version for a new feature batch is a one-line edit:

    APP_VERSION = '0.11.0-Beta'     # new features  -> minor bump
    APP_VERSION = '0.10.2-Beta'     # bug fixes only -> patch bump
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.ne_app_version import APP_VERSION, exe_basename, version_info_text  # noqa: E402

VERSION_FILE = ROOT / 'windows_version_info.ver'


def main():
    VERSION_FILE.write_text(version_info_text(), encoding='utf-8')
    name = exe_basename()
    command = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--clean', '--onefile', '--windowed', '--noupx',
        '--name', name,
        '--icon', str(ROOT / 'assets' / 'notability-viewer.ico'),
        '--version-file', str(VERSION_FILE),
        '--add-data', f'{ROOT / "assets"};assets',
        '--runtime-hook', str(ROOT / 'runtime' / 'ne_runtime_qt.py'),
        '--hidden-import', 'document_core.ne_question_cards',
        '--hidden-import', 'ui.ne_question_card',
        '--hidden-import', 'ui.ne_card_toolbar',
        '--hidden-import', 'core.ne_app_version',
        '--distpath', str(ROOT / 'dist'),
        '--workpath', str(ROOT / '.pyinstaller-build'),
        '--specpath', str(ROOT / '.pyinstaller-build'),
        str(ROOT / 'main.py'),
    ]
    print(f'Building {name}.exe (version {APP_VERSION}) ...', flush=True)
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode:
        raise SystemExit(f'PyInstaller failed with exit code {result.returncode}')
    target = ROOT / 'dist' / f'{name}.exe'
    digest = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    print(f'OK: {target}')
    print(f'Size: {target.stat().st_size:,} bytes')
    print(f'SHA256: {digest}')


if __name__ == '__main__':
    main()
