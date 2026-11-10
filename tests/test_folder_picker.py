import ctypes
import sys

import pytest

from signalpoint import folder_picker as picker

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shell integration")


def shell_item_array(paths, output):
    """Use real shell items to exercise Unicode paths and GetResults ownership."""
    ole, shell = picker._libraries()
    shell.SHParseDisplayName.argtypes = [
        picker.TEXT,
        picker.VOID,
        picker.PVOID,
        picker.DWORD,
        ctypes.POINTER(picker.DWORD),
    ]
    shell.SHParseDisplayName.restype = picker.HRESULT
    shell.SHCreateShellItemArrayFromIDLists.argtypes = [picker.DWORD, picker.PVOID, picker.PVOID]
    shell.SHCreateShellItemArrayFromIDLists.restype = picker.HRESULT
    pidls = []
    try:
        for path in paths:
            pidl = picker.VOID()
            picker._check(shell.SHParseDisplayName(str(path), None, ctypes.byref(pidl), 0, None))
            pidls.append(pidl)
        array = (picker.VOID * len(pidls))(*pidls)
        return shell.SHCreateShellItemArrayFromIDLists(len(pidls), array, output)
    finally:
        for pidl in pidls:
            ole.CoTaskMemFree(pidl)


@pytest.mark.parametrize("outcome", ["selected", "cancelled", "failed"])
@pytest.mark.parametrize("multiple", [True, False])
def test_native_folder_picker_options_results_and_cancel(tmp_path, monkeypatch, outcome, multiple):
    paths = [tmp_path / name for name in ("Warlords", "Armory – şövalye")]
    for path in paths:
        path.mkdir()
    if not multiple:
        paths = paths[:1]
    call = picker._call
    shown, released = [], []

    def shell_call(pointer, index, types=(), *args):
        if index == 3:  # Only replace Show; no interactive dialog in tests.
            shown.append(args[0])
            options = picker.DWORD()
            picker._check(call(pointer, 10, (ctypes.POINTER(picker.DWORD),), ctypes.byref(options)))
            required = picker.FOLDER_OPTIONS & ~0x200
            assert options.value & required == required
            assert bool(options.value & 0x200) == multiple
            return {"selected": 0, "cancelled": -2147023673, "failed": -2147467259}[outcome]
        if index == 27:  # Feed GetResults a genuine multi-item Windows shell array.
            return shell_item_array(paths, args[0])
        result = call(pointer, index, types, *args)
        if index == 2:
            released.append(pointer.value)
        return result

    monkeypatch.setattr(picker, "_call", shell_call)
    if outcome == "failed":
        with pytest.raises(OSError):
            picker.choose_folders(0, tmp_path, multiple=multiple)
    else:
        assert picker.choose_folders(0, tmp_path, multiple=multiple) == (
            paths if outcome == "selected" else []
        )
    assert shown == [0]
    # Default folder and dialog are released for every outcome, plus the results
    # array and each selected shell item when selection succeeds.
    assert len(released) == (3 + len(paths) if outcome == "selected" else 2)
