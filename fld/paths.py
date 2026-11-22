"""Stable project paths shared by application modules."""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = PROJECT_DIR / "downloads"
STATE_PATH = PROJECT_DIR / "queue.sqlite3"
ICON_PATH = PROJECT_DIR / "assets" / "fld.ico"
