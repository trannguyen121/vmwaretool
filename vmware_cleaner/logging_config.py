from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def default_log_dir() -> Path:
    base = Path.home() / "AppData" / "Local" / "VMwareDiskCleaner" / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def configure_logging(log_dir: Path | None = None) -> Path:
    target = log_dir or default_log_dir()
    target.mkdir(parents=True, exist_ok=True)
    log_file = target / "vmware-disk-cleaner.log"
    root = logging.getLogger("vmware_cleaner")
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    return log_file

