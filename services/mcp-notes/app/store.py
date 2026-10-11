"""Notes storage in SQLite. One short-lived connection per call, so the file is always consistent
and the server can be restarted at any time without losing data."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone


class NotFound(Exception):
    pass


class NoteStore:
    def __init__(self, path: str) -> None:
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _row(r: sqlite3.Row, full: bool) -> dict:
        out = {"id": r["id"], "title": r["title"], "created_at": r["created_at"]}
        out["body"] = r["body"] if full else (r["body"][:120] + ("…" if len(r["body"]) > 120 else ""))
        return out

    def add(self, title: str, body: str) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with closing(self._connect()) as db, db:
            cur = db.execute("INSERT INTO notes (title, body, created_at) VALUES (?,?,?)", (title, body, now))
            return self._row(db.execute("SELECT * FROM notes WHERE id=?", (cur.lastrowid,)).fetchone(), full=True)

    def list(self, search: str | None, limit: int) -> list[dict]:
        # A plain substring search: escape the characters that LIKE treats as wildcards.
        escaped = (search or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM notes WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' ORDER BY id DESC LIMIT ?",
                (like, like, limit)).fetchall()
        return [self._row(r, full=False) for r in rows]

    def get(self, note_id: int) -> dict:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if row is None:
            raise NotFound(f"There is no note with id {note_id}.")
        return self._row(row, full=True)

    def delete(self, note_id: int) -> dict:
        with closing(self._connect()) as db, db:
            if db.execute("DELETE FROM notes WHERE id=?", (note_id,)).rowcount == 0:
                raise NotFound(f"There is no note with id {note_id}.")
        return {"deleted": note_id}
