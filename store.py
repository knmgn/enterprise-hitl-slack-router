"""SQLite-backed state: idempotency, ticket lifecycle and audit trail (stdlib only)."""
import json
import sqlite3
import threading
import time
from typing import Optional


class Store:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id  TEXT PRIMARY KEY,
                    status     TEXT NOT NULL,
                    attempts   INTEGER NOT NULL DEFAULT 1,
                    snapshot   TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL NOT NULL,
                    ticket_id TEXT NOT NULL,
                    event     TEXT NOT NULL,
                    detail    TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
                """
            )
            self._conn.commit()

    # --- idempotency / lifecycle -------------------------------------------------
    def claim(self, ticket_id: str, max_attempts: int = 3) -> bool:
        """Return True if the caller now owns processing of this ticket.

        A ticket is processed at most once; a previously failed one may be retried
        until `max_attempts` is reached.
        """
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO tickets(ticket_id,status,created_at,updated_at) VALUES(?,?,?,?)",
                (ticket_id, "processing", now, now),
            )
            if cur.rowcount == 1:
                self._conn.commit()
                return True
            cur = self._conn.execute(
                "UPDATE tickets SET status='processing', attempts=attempts+1, updated_at=? "
                "WHERE ticket_id=? AND status='error' AND attempts<?",
                (now, ticket_id, max_attempts),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def transition(self, ticket_id: str, from_status: str, to_status: str) -> bool:
        """Atomic compare-and-set on status (prevents double approval clicks)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=? AND status=?",
                (to_status, time.time(), ticket_id, from_status),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def set_status(self, ticket_id: str, status: str, snapshot: Optional[dict] = None) -> None:
        with self._lock:
            if snapshot is not None:
                self._conn.execute(
                    "UPDATE tickets SET status=?, snapshot=?, updated_at=? WHERE ticket_id=?",
                    (status, json.dumps(snapshot, ensure_ascii=False), time.time(), ticket_id),
                )
            else:
                self._conn.execute(
                    "UPDATE tickets SET status=?, updated_at=? WHERE ticket_id=?",
                    (status, time.time(), ticket_id),
                )
            self._conn.commit()

    def get(self, ticket_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["snapshot"] = json.loads(d["snapshot"]) if d.get("snapshot") else None
        return d

    # --- audit -------------------------------------------------------------------
    def audit(self, ticket_id: str, event: str, detail: Optional[dict] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(ts,ticket_id,event,detail) VALUES(?,?,?,?)",
                (time.time(), ticket_id, event, json.dumps(detail or {}, ensure_ascii=False)),
            )
            self._conn.commit()

    def audit_trail(self, ticket_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts,event,detail FROM audit WHERE ticket_id=? ORDER BY id", (ticket_id,)
            ).fetchall()
        return [{"ts": r["ts"], "event": r["event"], "detail": json.loads(r["detail"] or "{}")} for r in rows]

    # --- meta --------------------------------------------------------------------
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row["v"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (key, value))
            self._conn.commit()
