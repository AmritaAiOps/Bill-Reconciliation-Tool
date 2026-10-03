"""Tests for app.settings (output / log folder locations) and the Api
methods that change them.

Run from reconciliation_tool/:  python -m pytest tests -q
"""
import json
import sys
import types

import pytest

try:
    import webview  # noqa: F401
except ImportError:  # the Api tests never open a window
    sys.modules["webview"] = types.ModuleType("webview")

from app import settings, web_ui
from app.excel_store import ExcelStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point APPDATA and Documents into tmp_path."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(settings, "documents_dir", lambda: tmp_path / "Documents")
    return tmp_path


def test_defaults_in_documents(home):
    s, notes = settings.load()
    assert s["output_dir"] == home / "Documents" / "Bill Reconciliation"
    assert s["log_dir"] == home / "Documents" / "Bill Reconciliation" / "Logs"
    assert s["output_dir"].is_dir() and s["log_dir"].is_dir()
    assert notes == []


def test_save_load_round_trip(home):
    chosen = {"output_dir": home / "Out", "log_dir": home / "L"}
    settings.save(chosen)
    assert settings.load()[0] == chosen


def test_corrupt_settings_fall_back_to_defaults(home):
    settings.settings_file().parent.mkdir(parents=True)
    settings.settings_file().write_text("{not json", encoding="utf-8")
    s, notes = settings.load()
    assert s == settings.defaults() and len(notes) == 1


def test_unavailable_folder_falls_back(home, monkeypatch):
    settings.save({"output_dir": home / "gone", "log_dir": home / "L"})
    real = settings._usable
    monkeypatch.setattr(settings, "_usable", lambda p: p.name != "gone" and real(p))
    s, notes = settings.load()
    assert s["output_dir"] == settings.default_output_dir()
    assert s["log_dir"] == home / "L"
    assert "gone" in notes[0]


def test_move_file_into_empty_folder(tmp_path):
    src = tmp_path / "a" / "f.xlsx"
    src.parent.mkdir()
    src.write_text("old")
    assert settings.move_file_into(src, tmp_path / "b") is True
    assert not src.exists() and (tmp_path / "b" / "f.xlsx").read_text() == "old"


def test_move_file_never_overwrites(tmp_path):
    src = tmp_path / "a" / "f.xlsx"
    src.parent.mkdir()
    src.write_text("old")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "f.xlsx").write_text("existing")
    assert settings.move_file_into(src, tmp_path / "b") is False
    assert src.read_text() == "old" and (tmp_path / "b" / "f.xlsx").read_text() == "existing"


def test_legacy_workbook_copied_once(tmp_path):
    legacy = tmp_path / "exe" / settings.EXCEL_NAME
    legacy.parent.mkdir()
    legacy.write_text("legacy")
    out = tmp_path / "out"
    out.mkdir()
    assert settings.migrate_legacy(legacy, out) is True
    assert legacy.exists() and (out / settings.EXCEL_NAME).read_text() == "legacy"

    legacy.write_text("newer legacy")
    assert settings.migrate_legacy(legacy, out) is False  # never overwrites
    assert (out / settings.EXCEL_NAME).read_text() == "legacy"


# ---- Api -------------------------------------------------------------------

@pytest.fixture
def api(home, monkeypatch):
    s, _ = settings.load()
    store = ExcelStore(s["output_dir"] / settings.EXCEL_NAME)
    store.add_estimate({"mrd_number": "1", "patient_name": "P", "estimate_amount": 10.0})
    store.save()
    monkeypatch.setattr(web_ui, "set_log_file", lambda path: None)
    a = web_ui.Api(s)
    a.pick = None
    monkeypatch.setattr(a, "_choose_dir", lambda start: a.pick)
    return a


def test_change_output_dir_moves_workbook(api, home):
    old = api._excel_path
    api.pick = str(home / "Elsewhere")
    res = api.change_output_dir()
    assert res["ok"], res
    assert not old.exists() and (home / "Elsewhere" / settings.EXCEL_NAME).exists()
    assert api.get_summary()["counts"]["Total Patients"] == 1
    saved = json.loads(settings.settings_file().read_text(encoding="utf-8"))
    assert saved["output_dir"] == str(home / "Elsewhere")


def test_change_output_dir_uses_existing_workbook(api, home):
    other = home / "Other"
    other.mkdir()
    ExcelStore(other / settings.EXCEL_NAME).save()  # an empty workbook already there
    old = api._excel_path
    api.pick = str(other)
    res = api.change_output_dir()
    assert res["ok"] and "already had" in res["message"]
    assert old.exists()  # left in place, not overwritten
    assert api.get_summary()["counts"]["Total Patients"] == 0


def test_cancelled_dialog_changes_nothing(api):
    before = api.get_locations()
    assert api.change_output_dir() is None
    assert api.get_locations() == before


def test_failed_move_changes_nothing(api, home, monkeypatch):
    def locked(src, new_dir):
        raise PermissionError("file is open")
    monkeypatch.setattr(settings, "move_file_into", locked)
    before = api.get_locations()
    api.pick = str(home / "Elsewhere")
    res = api.change_output_dir()
    assert not res["ok"] and "close it" in res["message"]
    assert api.get_locations() == before


def test_reset_moves_back_to_defaults(api, home):
    api.pick = str(home / "Elsewhere")
    api.change_output_dir()
    res = api.reset_locations()
    assert res["ok"]
    assert api.get_locations()["output_dir"] == str(settings.default_output_dir())
    assert (settings.default_output_dir() / settings.EXCEL_NAME).exists()
