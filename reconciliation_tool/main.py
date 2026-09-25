"""Entry point for the Estimate vs Bill Reconciliation Tool."""
import sys
from pathlib import Path

from app.web_ui import run_app
from app.logger_setup import setup_logging

# When frozen by PyInstaller, __file__ lives in a temp dir that is deleted on
# exit -- the Excel output has to sit next to the .exe instead.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent

EXCEL_PATH = APP_DIR / "Reconciliation_Output.xlsx"
LOG_PATH = APP_DIR / "reconciliation_log.txt"

if __name__ == "__main__":
    setup_logging(LOG_PATH)
    run_app(EXCEL_PATH, LOG_PATH)
