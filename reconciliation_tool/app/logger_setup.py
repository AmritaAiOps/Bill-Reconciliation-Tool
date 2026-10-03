"""Logging configuration: failures and mismatches go to a log file (in the
log folder chosen in Settings), plus the console, for manual review."""
import logging
from pathlib import Path

_FORMAT = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")


def setup_logging(log_path) -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_FORMAT)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)

    set_log_file(log_path)
    return logger


def set_log_file(log_path):
    """Point file logging at log_path, closing the current log file first
    (Windows can't move a file that is still open). None just closes it."""
    logger = logging.getLogger()
    for h in [h for h in logger.handlers if isinstance(h, logging.FileHandler)]:
        logger.removeHandler(h)
        h.close()
    if log_path is None:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(_FORMAT)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)
