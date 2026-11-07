"""Keep Qt's split-wheel DLL directories available in frozen builds."""

import ctypes
import os
import sys
from infrastructure import ne_ai_runtime


_dll_handles = []
_loaded_dlls = []

if getattr(sys, "frozen", False) and os.name == "nt":
    _bundle = sys._MEIPASS
    _qt_bin = os.path.join(_bundle, "PyQt6", "Qt6", "bin")
    for _directory in (_qt_bin, _bundle):
        if os.path.isdir(_directory):
            os.environ["PATH"] = _directory + os.pathsep + os.environ.get("PATH", "")
            _dll_handles.append(os.add_dll_directory(_directory))

    # These modules are loaded from sibling directories in PyQt6's split
    # wheel. Loading them by absolute path keeps the dependency chain alive
    # before QtCore.pyd starts resolving its imports.
    for _filename in (
        os.path.join(_bundle, "icudt78.dll"),
        os.path.join(_bundle, "icuuc.dll"),
        os.path.join(_qt_bin, "vcruntime140.dll"),
        os.path.join(_qt_bin, "vcruntime140_1.dll"),
        os.path.join(_qt_bin, "msvcp140.dll"),
        os.path.join(_qt_bin, "msvcp140_1.dll"),
        os.path.join(_qt_bin, "msvcp140_2.dll"),
        os.path.join(_qt_bin, "Qt6Core.dll"),
    ):
        if os.path.isfile(_filename):
            _loaded_dlls.append(ctypes.WinDLL(_filename))
