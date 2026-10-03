"""Where the Excel output and the log live, and remembering the user's choice.

Defaults are Documents\\Bill Reconciliation\\ (Excel) and ...\\Logs\\ (log),
so the files end up in the same predictable place for everyone, however the
exe was shared or wherever it was launched from. The user can change either
folder from the Settings dialog; the choice is kept in
%APPDATA%\\EstimateVsBillReconciliation\\settings.json."""
import ctypes
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_NAME = "EstimateVsBillReconciliation"
APP_FOLDER = "Bill Reconciliation"
EXCEL_NAME = "Reconciliation_Output.xlsx"
LOG_NAME = "reconciliation_log.txt"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


# {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
_FOLDERID_DOCUMENTS = _GUID(0xFDD39AD0, 0x238F, 0x46AF,
                            (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))


def documents_dir() -> Path:
    """The user's real Documents folder. Asks Windows rather than assuming
    ~/Documents, because OneDrive often redirects it elsewhere."""
    if sys.platform == "win32":
        buf = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(_FOLDERID_DOCUMENTS), 0, None, ctypes.byref(buf))
        try:
            if hr == 0 and buf.value:
                return Path(buf.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(buf)
    return Path.home() / "Documents"


def default_output_dir() -> Path:
    return documents_dir() / APP_FOLDER


def default_log_dir() -> Path:
    return default_output_dir() / "Logs"


def defaults() -> dict:
    return {"output_dir": default_output_dir(), "log_dir": default_log_dir()}


def settings_file() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / APP_NAME / "settings.json"


def _usable(folder: Path) -> bool:
    """True if the folder exists (or can be created) and can be written to."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


def load():
    """Return (settings, notes). settings maps output_dir / log_dir to Paths,
    both created. notes are warnings to log once logging is set up (e.g. a
    saved folder on a drive that is no longer there)."""
    result, notes = defaults(), []
    try:
        saved = json.loads(settings_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        saved = {}
    except (OSError, ValueError) as e:
        saved = {}
        notes.append(f"Could not read {settings_file()} ({e}); using default folders.")

    for key, default in list(result.items()):
        chosen = saved.get(key) if isinstance(saved, dict) else None
        if chosen and _usable(Path(chosen)):
            result[key] = Path(chosen)
        else:
            if chosen:
                notes.append(f"Saved folder {chosen} is not available; using {default} instead.")
            default.mkdir(parents=True, exist_ok=True)
    return result, notes


def save(settings: dict):
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: str(v) for k, v in settings.items()}, indent=2),
                    encoding="utf-8")


def move_file_into(src: Path, new_dir: Path) -> bool:
    """Move src into new_dir, keeping its name. If new_dir already has a file
    of that name, nothing is moved or overwritten and False is returned.
    Raises OSError (e.g. the file is open in Excel)."""
    src, new_dir = Path(src), Path(new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)
    target = new_dir / src.name
    if target.exists():
        return False
    shutil.move(str(src), str(target))
    return True


def migrate_legacy(legacy_file: Path, output_dir: Path) -> bool:
    """Copy a workbook left in the old location (next to the exe) into the
    output folder, if the output folder has none yet. The original is kept
    as a backup. Returns True if a copy was made; raises OSError on failure."""
    legacy_file, target = Path(legacy_file), Path(output_dir) / EXCEL_NAME
    if not legacy_file.exists() or target.exists():
        return False
    if legacy_file.resolve() == target.resolve():
        return False
    shutil.copy2(legacy_file, target)
    return True
