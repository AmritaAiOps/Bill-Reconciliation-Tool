"""pywebview front end: an HTML/CSS/JS dashboard (app/ui/) backed by the
Python processing code through a JS API object."""
import json
import logging
import os
import sys
import threading
from pathlib import Path

import webview

from .excel_store import ExcelStore
from .processing import pdf_files, process_estimate_folder, process_billing_folder

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

    def __init__(self, excel_path, log_path):
        self._excel_path = Path(excel_path)
        self._log_path = Path(log_path)
        self._store = ExcelStore(excel_path)
        self._lock = threading.Lock()
        self._window = None
        self._last_dirs = {}

    # ---------------------------------------------------------- queries --
    def get_info(self):
        logger.info("UI loaded (workbook %s)", self._excel_path)
        return {"workbook": self._excel_path.name}

    def get_summary(self):
        return _summary_json(self._store)

    def pick_folder(self, kind):
        if not self._window:
            return None
        start = self._last_dirs.get(kind, "")
        result = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        self._last_dirs[kind] = str(Path(path).parent)  # reopen near the last pick
        return {"path": path, "pdf_count": len(pdf_files(path))}

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

        parts = []
        if est:
            parts.append(f"Estimates: {est['files']} PDFs · {est['added']} new · "
                         f"{est['skipped']} already present · {est['failed']} failed")
        if bill:
            parts.append(f"Bills: {bill['files']} PDFs · {bill['matched']} matched · "
                         f"{bill['unmatched']} unmatched · {bill['failed']} failed")
        if bill:
            bill["run_rows"] = [{k: _jsonable(v) for k, v in r.items()} for r in bill["run_rows"]]
        issues = (est or {}).get("issues", []) + (bill or {}).get("issues", [])
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


def run_app(excel_path, log_path):
    api = Api(excel_path, log_path)
    window = webview.create_window(
        "Estimate vs Bill Reconciliation",
        url=str(_ui_dir() / "index.html"),
        js_api=api,
        width=1220, height=820, min_size=(1000, 660),
        background_color="#F7F8FA",
    )
    api._window = window
    webview.start()
