"""
corvus_hack/db.py — Session + account pool database.

SQLite with WAL mode. All four CorvusClient accounts are seeded at init.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent / "corvus_sessions.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                account_name TEXT PRIMARY KEY,
                browser      TEXT NOT NULL,
                status       TEXT DEFAULT 'available',
                user_id      INTEGER,
                session_id   TEXT,
                last_seen    REAL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id    TEXT PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                account_name  TEXT,
                status        TEXT DEFAULT 'idle',
                platform      TEXT,
                session_start REAL,
                session_end   REAL,
                is_free_trial INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS clients (
                chat_id       INTEGER PRIMARY KEY,
                username      TEXT,
                sessions_done INTEGER DEFAULT 0,
                registered_at REAL DEFAULT (unixepoch())
            );
        """)
        for name, browser in [
            ("CorvusGoLogin",   "GoLogin"),
            ("CorvusIXBrowser", "IXBrowser"),
            ("CorvusMoreLogin", "MoreLogin"),
            ("CorvusAdsPower",  "AdsPower"),
        ]:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (account_name, browser) VALUES (?,?)",
                (name, browser),
            )


def allocate_account(session_id: str, user_id: int, platform: str) -> str | None:
    """Claim an available account. Returns account_name or None if all busy."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT account_name FROM accounts WHERE status='available' LIMIT 1"
        ).fetchone()
        if not row:
            return None
        name = row["account_name"]
        now = time.time()
        conn.execute(
            "UPDATE accounts SET status='active', user_id=?, session_id=?, last_seen=? WHERE account_name=?",
            (user_id, session_id, now, name),
        )
        conn.execute(
            """INSERT OR REPLACE INTO sessions
               (session_id, user_id, account_name, status, platform, session_start)
               VALUES (?,?,?,?,?,?)""",
            (session_id, user_id, name, "active", platform, now),
        )
    return name


def release_account(session_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE accounts SET status='available', user_id=NULL, session_id=NULL WHERE session_id=?",
            (session_id,),
        )
        conn.execute(
            "UPDATE sessions SET status='ended', session_end=? WHERE session_id=?",
            (time.time(), session_id),
        )


def get_session(session_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def get_client_session(chat_id: int) -> dict | None:
    """Active session for a client chat_id."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE user_id=? AND status='active'", (chat_id,)
        ).fetchone()
        return dict(row) if row else None


def register_client(chat_id: int, username: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO clients (chat_id, username) VALUES (?,?)",
            (chat_id, username),
        )


def is_registered_client(chat_id: int) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM clients WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return row is not None


def client_sessions_done(chat_id: int) -> int:
    with _conn() as conn:
        row = conn.execute(
            "SELECT sessions_done FROM clients WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return row["sessions_done"] if row else 0


def increment_client_sessions(chat_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE clients SET sessions_done = sessions_done + 1 WHERE chat_id=?",
            (chat_id,),
        )


def account_heartbeat(account_name: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE accounts SET last_seen=? WHERE account_name=?",
            (time.time(), account_name),
        )


def pool_status() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY account_name").fetchall()
        return [dict(r) for r in rows]
