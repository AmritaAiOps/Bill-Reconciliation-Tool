"""Entry point for the Estimate vs Bill Reconciliation Tool."""
import logging
import sys
from pathlib import Path

from app import settings
from app.web_ui import run_app
from app.logger_setup import setup_logging


def _legacy_dir() -> Path:
    """Where older versions wrote the Excel file: next to the .exe (under
    PyInstaller __file__ is in a temp dir), or next to main.py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


if __name__ == "__main__":
    config, notes = settings.load()
    legacy = _legacy_dir() / settings.EXCEL_NAME
    try:
        if settings.migrate_legacy(legacy, config["output_dir"]):
            notes.append(f"Copied existing workbook {legacy} to {config['output_dir']} "
                         f"(the original was left in place).")
    except OSError as e:
        notes.append(f"Could not copy existing workbook {legacy}: {e}")

    setup_logging(config["log_dir"] / settings.LOG_NAME)
    for note in notes:
        logging.warning(note)
    run_app(config)
