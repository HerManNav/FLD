"""SQLite checkpoints and a process lock for the durable queue."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import uuid


class QueueStore:
    """Own SQLite state and the process lock for the stable FCP client name."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.path.with_suffix(".lock").open("a+b")
        self.lock_file.seek(0, 2)
        if not self.lock_file.tell():
            self.lock_file.write(b"0")
            self.lock_file.flush()
        self.lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock_file.close()
            raise OSError("FLD is already open. Use the existing window.") from exc
        try:
            self.db = sqlite3.connect(self.path, check_same_thread=False)
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("This queue was created by a newer FLD version.")
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('client', ?)", ("FLD-" + uuid.uuid4().hex,))
                self.db.execute("PRAGMA user_version=1")
            self.client_name = self.get_metadata("client")
        except Exception:
            self.close()
            raise

    def get_metadata(self, name):
        row = self.db.execute("SELECT value FROM metadata WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def load(self):
        return {row[0]: json.loads(row[1]) for row in self.db.execute("SELECT id,payload FROM tasks ORDER BY rowid")}

    def save(self, tasks=None, *, metadata=None, defaults=None):
        with self.db:
            if tasks is not None:
                self.db.execute("DELETE FROM tasks")
                self.db.executemany("INSERT INTO tasks VALUES (?,?)",
                                    [(key, json.dumps(value)) for key, value in tasks.items()])
            if metadata:
                self.db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", metadata.items())
            if defaults:
                self.db.executemany("INSERT OR IGNORE INTO metadata VALUES (?,?)", defaults.items())

    def close(self):
        if hasattr(self, "db"):
            self.db.close()
        self.lock_file.close()
