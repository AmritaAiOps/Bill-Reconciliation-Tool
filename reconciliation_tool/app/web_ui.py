"""pywebview front end: an HTML/CSS/JS dashboard (app/ui/) backed by the
Python processing code through a JS API object."""
import json
import logging
import os
import sys
import threading
from pathlib import Path

import webview

from . import settings
from .excel_store import ExcelStore
from .logger_setup import set_log_file
from .processing import (pdf_files, process_estimate_folder, process_billing_folder,
                         recycle_files)

logger = logging.getLogger(__name__)


def _ui_dir() -> Path:
    # Under PyInstaller the bundled data files are unpacked to sys._MEIPASS.
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "app" / "ui"
    return Path(__file__).resolve().parent / "ui"


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)  # e.g. a datetime typed into the workbook by hand


def _summary_json(store) -> dict:
    s = store.summary()
    s["rows"] = [{k: _jsonable(v) for k, v in r.items()} for r in s["rows"]]
    return s


class Api:
    """Methods here are callable from JS as `window.pywebview.api.<name>()`.
    Attributes are underscore-prefixed so pywebview doesn't expose them."""

    def __init__(self, config):
        """config: {"output_dir": Path, "log_dir": Path} from settings.load()."""
        self._settings = {k: Path(v) for k, v in config.items()}
        self._store = ExcelStore(self._excel_path)
        self._lock = threading.Lock()
        self._window = None
        self._last_dirs = {}

    @property
    def _excel_path(self):
        return self._settings["output_dir"] / settings.EXCEL_NAME

    @property
    def _log_path(self):
        return self._settings["log_dir"] / settings.LOG_NAME

    # ---------------------------------------------------------- queries --
    def get_info(self):
        logger.info("UI loaded (workbook %s)", self._excel_path)
        return {"workbook": self._excel_path.name, "workbook_path": str(self._excel_path)}

    def get_locations(self):
        d = settings.defaults()
        return {"output_dir": str(self._settings["output_dir"]),
                "log_dir": str(self._settings["log_dir"]),
                "default_output_dir": str(d["output_dir"]),
                "default_log_dir": str(d["log_dir"])}

    def get_summary(self):
        return _summary_json(self._store)

    def pick_folder(self, kind):
        path = self._choose_dir(self._last_dirs.get(kind, ""))
        if not path:
            return None
        self._last_dirs[kind] = str(Path(path).parent)  # reopen near the last pick
        return {"path": path, "pdf_count": len(pdf_files(path))}

    def _choose_dir(self, start):
        """Show a folder picker; returns the chosen path or None."""
        if not self._window:
            return None
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result

    # --------------------------------------------------------- settings --
    def change_output_dir(self):
        new_dir = self._choose_dir(str(self._settings["output_dir"]))
        return self._with_lock(lambda: self._relocate({"output_dir": new_dir})) if new_dir else None

    def change_log_dir(self):
        new_dir = self._choose_dir(str(self._settings["log_dir"]))
        return self._with_lock(lambda: self._relocate({"log_dir": new_dir})) if new_dir else None

    def reset_locations(self):
        return self._with_lock(lambda: self._relocate(settings.defaults()))

    def open_folder(self, kind):
        folder = self._settings["output_dir" if kind == "output" else "log_dir"]
        folder.mkdir(parents=True, exist_ok=True)
        return self._open(folder, "Folder not found.")

    def _with_lock(self, fn):
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": "Wait for the current run to finish, then try again.",
                    "locations": self.get_locations()}
        try:
            return fn()
        finally:
            self._lock.release()

    def _relocate(self, changes):
        """Move the Excel file and/or log into new folders (the settings keys in
        `changes`) and remember the choice. A file that already exists in the
        new folder is used as-is, never overwritten."""
        messages, ok = [], True
        for key, new_dir in changes.items():
            new_dir = Path(new_dir)
            if new_dir.resolve() == self._settings[key].resolve():
                continue
            is_excel = key == "output_dir"
            old_file = self._excel_path if is_excel else self._log_path
            what = "Excel file" if is_excel else "log"
            if not is_excel:
                set_log_file(None)  # release the log so Windows lets us move it
            try:
                moved = settings.move_file_into(old_file, new_dir) if old_file.exists() else None
            except OSError as e:
                ok = False
                if not is_excel:
                    set_log_file(self._log_path)
                logger.error("Could not move %s to %s: %s", old_file, new_dir, e)
                hint = " If it is open in Excel, close it and try again." if is_excel else ""
                messages.append(f"Could not move the {what} to {new_dir} ({e}).{hint}")
                continue

            self._settings[key] = new_dir
            if is_excel:
                self._store = ExcelStore(self._excel_path)
            else:
                set_log_file(self._log_path)
            if moved is False:
                messages.append(f"{new_dir} already had a {old_file.name}, so the app is now using "
                                f"that file. Your previous {what} is still in {old_file.parent}.")
            elif moved:
                messages.append(f"{what[0].upper() + what[1:]} moved to {new_dir}.")
            else:
                messages.append(f"{what[0].upper() + what[1:]} folder set to {new_dir}.")
            logger.info("%s folder changed to %s", what, new_dir)

        try:
            settings.save(self._settings)
        except OSError as e:
            ok = False
            logger.error("Could not save settings: %s", e)
            messages.append(f"The change works for now but could not be remembered ({e}).")
        return {"ok": ok, "message": " ".join(messages) or "Nothing to change.",
                "locations": self.get_locations()}

    # ---------------------------------------------------------- actions --
    def run(self, estimate_folder, billing_folder):
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "message": "A run is already in progress."}
        try:
            return self._run(estimate_folder, billing_folder)
        except Exception as e:
            logger.exception("Run failed")
            return {"ok": False, "message": f"Run failed: {e}. See reconciliation_log.txt."}
        finally:
            self._lock.release()

    def _run(self, estimate_folder, billing_folder):
        est = bill = None
        # Estimates first: bills can only match patients that already have a row.
        if estimate_folder:
            self._push_status("Reading estimate PDFs…")
            est = process_estimate_folder(
                self._store, estimate_folder, lambda d, t: self._push_progress("estimate", d, t))
        if billing_folder:
            self._push_status("Reading bill PDFs…")
            bill = process_billing_folder(
                self._store, billing_folder, lambda d, t: self._push_progress("billing", d, t))

        try:
            self._store.save()
        except PermissionError:
            logger.error("Could not save %s -- is it open in Excel?", self._store.path)
            return {"ok": False, "summary": _summary_json(self._store),
                    "message": "Could not save the Excel file -- close it in Excel and click Run again."}

        # Only now that the data is safely on disk, remove the PDFs it came from.
        removal_issues = []
        for result in (est, bill):
            if result:
                done = result.pop("done_files")
                failures = recycle_files(done)
                result["removed"] = len(done) - len(failures)
                removal_issues += failures

        parts = []
        if est:
            parts.append(f"Estimates: {est['files']} PDFs · {est['added']} new · "
                         f"{est['skipped']} already present · {est['failed']} failed · "
                         f"{est['removed']} removed")
        if bill:
            parts.append(f"Bills: {bill['files']} PDFs · {bill['matched']} matched · "
                         f"{bill['unmatched']} unmatched · {bill['failed']} failed · "
                         f"{bill['removed']} removed")
        if bill:
            bill["run_rows"] = [{k: _jsonable(v) for k, v in r.items()} for r in bill["run_rows"]]
        issues = ((est or {}).get("issues", []) + (bill or {}).get("issues", [])
                  + removal_issues)
        message = "  |  ".join(parts) + "."
        if issues:
            message += f"  {len(issues)} need{'s' if len(issues) == 1 else ''} review."
        return {
            "ok": True,
            "estimate": est,
            "billing": bill,
            "issues": issues,
            "summary": _summary_json(self._store),
            "message": message,
        }

    def open_excel(self):
        return self._open(self._excel_path, "No Excel file yet -- run an estimate folder first.")

    def open_log(self):
        return self._open(self._log_path, "No log file yet.")

    def _open(self, path, missing_msg):
        if not path.exists():
            return missing_msg
        try:
            os.startfile(path)
            return None
        except OSError as e:
            logger.error("Could not open %s: %s", path, e)
            return f"Could not open {path.name}: {e}"

    # ---------------------------------------------------- push to page --
    def _js(self, fn, *args):
        if self._window:
            self._window.evaluate_js(f"{fn}({', '.join(json.dumps(a) for a in args)})")

    def _push_progress(self, phase, done, total):
        self._js("onProgress", phase, done, total)

    def _push_status(self, text):
        self._js("setStatus", text)


def run_app(config):
    api = Api(config)
    window = webview.create_window(
        "Estimate vs Bill Reconciliation",
        url=str(_ui_dir() / "index.html"),
        js_api=api,
        width=1220, height=820, min_size=(1000, 660),
        background_color="#F7F8FA",
    )
    api._window = window
    webview.start()
