from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session TEXT, role TEXT, content TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS traps(id TEXT PRIMARY KEY, session TEXT, request TEXT, status TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, event TEXT, detail TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))
        self.db.commit()

    def message(self, session, role, content):
        self.db.execute("INSERT INTO messages(session,role,content,created) VALUES(?,?,?,?)", (session, role, content, time.time()))
        self.db.commit()

    def history(self, session, limit=40):
        rows = self.db.execute("SELECT role,content FROM (SELECT id,role,content FROM messages WHERE session=? ORDER BY id DESC LIMIT ?) ORDER BY id", (session, limit)).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def trap(self, session, request):
        trap_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO traps VALUES(?,?,?,?,?)", (trap_id, session, request, "pending", time.time()))
        self.db.commit()
        return trap_id

    def pending(self):
        return [dict(zip(("id", "session", "request", "status", "created"), row)) for row in self.db.execute("SELECT * FROM traps WHERE status='pending' ORDER BY created")]

    def resolve(self, trap_id, approved):
        row = self.db.execute("SELECT session FROM traps WHERE id=? AND status='pending'", (trap_id,)).fetchone()
        if not row:
            raise ValueError("Pending trap not found")
        self.db.execute("UPDATE traps SET status=? WHERE id=?", ("approved" if approved else "rejected", trap_id))
        self.db.commit()
        return row[0]

    def audit(self, event, detail=""):
        self.db.execute("INSERT INTO audit(event,detail,created) VALUES(?,?,?)", (event, detail, time.time()))
        self.db.commit()

    def events(self):
        return [dict(zip(("event", "detail", "created"), row)) for row in self.db.execute("SELECT event,detail,created FROM audit ORDER BY id DESC LIMIT 30")]

    def flush(self, session, scope):
        if scope == "all":
            self.db.execute("DELETE FROM messages")
        elif scope in ("session", "ephemeral"):
            self.db.execute("DELETE FROM messages WHERE session=?", (session,))
        else:
            raise ValueError("Invalid flush scope")
        self.db.commit()
