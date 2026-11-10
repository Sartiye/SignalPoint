"""Keep the frozen application's native dependencies out of external programs."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def external_environment():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield None
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    length = kernel.GetDllDirectoryW(0, None)
    buffer = ctypes.create_unicode_buffer(length + 1)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    previous = buffer.value
    environment = os.environ.copy()
    bundle = Path(sys._MEIPASS).resolve()
    environment["PATH"] = os.pathsep.join(
        p
        for p in environment.get("PATH", "").split(os.pathsep)
        if p and not Path(p.strip('"')).resolve().is_relative_to(bundle)
    )
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        environment.pop(key, None)
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield environment
    finally:
        if not kernel.SetDllDirectoryW(previous or None):
            raise ctypes.WinError(ctypes.get_last_error())
