"""Windows' standard folder picker, including native multiple selection."""

import ctypes
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

HRESULT = ctypes.c_long
DWORD = ctypes.c_uint32
VOID = ctypes.c_void_p
PVOID = ctypes.POINTER(VOID)
TEXT = ctypes.c_wchar_p
CANCELLED = 0x800704C7
# FOS_NOCHANGEDIR | PICKFOLDERS | FORCEFILESYSTEM | ALLOWMULTISELECT |
# PATHMUSTEXIST | FILEMUSTEXIST. Preserve the dialog's other default options.
FOLDER_OPTIONS = 0x8 | 0x20 | 0x40 | 0x200 | 0x800 | 0x1000


class GUID(ctypes.Structure):
    _fields_ = [
        ("data1", DWORD),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(UUID(value).bytes_le)


FILE_OPEN_DIALOG = GUID.parse("dc1c5a9c-e88a-4dde-a5a1-60f82a20aef7")
I_FILE_OPEN_DIALOG = GUID.parse("d57c7288-d4ad-4768-be02-9d969532d960")
I_SHELL_ITEM = GUID.parse("43826d1e-e718-42ee-bc55-a1e261c37bfe")


def _check(result):
    if result < 0:
        raise ctypes.WinError(result)


def _call(pointer, index, types=(), *args):
    # Slots follow the Windows SDK's IFileOpenDialog/IShellItem interfaces.
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(VOID))).contents
    function = ctypes.WINFUNCTYPE(HRESULT, VOID, *types)(table[index])
    return function(pointer, *args)


@contextmanager
def _interface():
    pointer = VOID()
    try:
        yield pointer
    finally:
        if pointer.value:
            _call(pointer, 2)  # IUnknown::Release


def _libraries():
    ole = ctypes.WinDLL("ole32")
    shell = ctypes.WinDLL("shell32")
    ole.CoInitializeEx.argtypes = [VOID, DWORD]
    ole.CoInitializeEx.restype = HRESULT
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), VOID, DWORD, ctypes.POINTER(GUID), PVOID]
    ole.CoCreateInstance.restype = HRESULT
    ole.CoTaskMemFree.argtypes = [VOID]
    ole.CoTaskMemFree.restype = None
    shell.SHCreateItemFromParsingName.argtypes = [TEXT, VOID, ctypes.POINTER(GUID), PVOID]
    shell.SHCreateItemFromParsingName.restype = HRESULT
    return ole, shell


def _read_results(dialog, ole):
    paths = []
    with _interface() as items:
        _check(_call(dialog, 27, (PVOID,), ctypes.byref(items)))  # GetResults
        count = DWORD()
        _check(_call(items, 7, (ctypes.POINTER(DWORD),), ctypes.byref(count)))  # GetCount
        for index in range(count.value):
            with _interface() as item:
                _check(_call(items, 8, (DWORD, PVOID), index, ctypes.byref(item)))  # GetItemAt
                name = VOID()
                try:
                    # SIGDN_FILESYSPATH: reject virtual locations with no filesystem path.
                    _check(_call(item, 5, (DWORD, PVOID), 0x80058000, ctypes.byref(name)))
                    paths.append(Path(ctypes.wstring_at(name)))
                finally:
                    if name.value:
                        ole.CoTaskMemFree(name)
    return paths


def choose_folders(owner: int, directory: Path | None = None, *, multiple=True) -> list[Path]:
    """Show a modal Explorer folder picker owned by the publisher window.

    Cancellation returns no paths. All shell objects and allocated strings are
    released, including when Windows reports an error.
    """
    ole, shell = _libraries()
    _check(ole.CoInitializeEx(None, 2))  # COINIT_APARTMENTTHREADED
    try:
        with _interface() as dialog:
            _check(
                ole.CoCreateInstance(
                    ctypes.byref(FILE_OPEN_DIALOG),
                    None,
                    1,  # CLSCTX_INPROC_SERVER
                    ctypes.byref(I_FILE_OPEN_DIALOG),
                    ctypes.byref(dialog),
                )
            )
            options = DWORD()
            _check(_call(dialog, 10, (ctypes.POINTER(DWORD),), ctypes.byref(options)))
            flags = options.value | FOLDER_OPTIONS
            if not multiple:
                flags &= ~0x200  # FOS_ALLOWMULTISELECT
            _check(_call(dialog, 9, (DWORD,), flags))
            _check(_call(dialog, 17, (TEXT,), "Choose module folders"))
            _check(_call(dialog, 18, (TEXT,), "Add folders" if multiple else "Use folder"))
            if directory is not None and directory.is_dir():
                with _interface() as folder:
                    _check(
                        shell.SHCreateItemFromParsingName(
                            str(directory.absolute()),
                            None,
                            ctypes.byref(I_SHELL_ITEM),
                            ctypes.byref(folder),
                        )
                    )
                    _check(_call(dialog, 12, (VOID,), folder))  # SetFolder
            result = _call(dialog, 3, (VOID,), owner)  # IModalWindow::Show
            if result & 0xFFFFFFFF == CANCELLED:
                return []
            _check(result)
            return _read_results(dialog, ole)
    finally:
        ole.CoUninitialize()
