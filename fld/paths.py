"""Stable resource and writable-data paths for source and frozen runs."""

from pathlib import Path
import sys


# PyInstaller sets __file__ inside its extraction tree in one-file builds.
RESOURCE_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = RESOURCE_DIR
FROZEN = bool(getattr(sys, "frozen", False))
DATA_DIR = Path(sys.executable).resolve().parent if FROZEN else PROJECT_DIR

DOWNLOAD_DIR = DATA_DIR / "downloads"
STATE_PATH = DATA_DIR / "queue.sqlite3"
LOCK_PATH = DATA_DIR / "queue.lock"
ICON_PATH = RESOURCE_DIR / "assets" / "fld.ico"
