"""Saved grade reports, keyed by an opaque id.

The default backend is a SQLite file (stdlib only). It works on Render's local
disk without a new service. Point DATA_DIR or REPORT_SQLITE_PATH at a persistent
disk to keep reports across restarts. ReportStore is the seam for a later
database backend: add a class and a REPORT_STORE branch, and leave callers alone.
"""

import json
import os
import re
import secrets
import sqlite3
import threading
import time

import config

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22,}$")
_SCRUB_KEYS = ("access_token", "account_id")


def new_opaque_id(nbytes=16):
    """URL-safe token from at least 16 random bytes."""
    if nbytes < 16:
        raise ValueError("opaque ids must be at least 16 bytes")
    return secrets.token_urlsafe(nbytes)


def _scrub(value):
    """Drop access tokens and ad account ids anywhere in the stored document."""
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in value.items()
            if key not in _SCRUB_KEYS
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


class ReportStore:
    def save(self, report):
        raise NotImplementedError

    def get(self, report_id):
        raise NotImplementedError


class SqliteReportStore(ReportStore):
    def __init__(self, path):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def save(self, report):
        if not isinstance(report, dict):
            raise TypeError("report must be a dict")
        if "access_token" in report:
            raise ValueError("refusing to store an access token with a report")
        payload = json.dumps(_scrub(report), separators=(",", ":"))
        report_id = new_opaque_id(16)
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO reports (id, payload, created_at) VALUES (?, ?, ?)",
                    (report_id, payload, int(time.time())),
                )
                conn.commit()
                # Fold the write into the main file so a crash or a disk copy
                # does not leave the grade only in the WAL sidecar.
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.DatabaseError:
                    pass
            finally:
                conn.close()
        return report_id

    def get(self, report_id):
        if not isinstance(report_id, str) or not _ID_RE.fullmatch(report_id):
            return None
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT payload FROM reports WHERE id = ?",
                    (report_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return json.loads(row["payload"])


def open_report_store():
    backend = config.REPORT_STORE
    if backend == "sqlite":
        return SqliteReportStore(config.report_sqlite_path())
    raise RuntimeError(
        "Unsupported REPORT_STORE %r. Use sqlite (the default). "
        "It stores reports in REPORT_SQLITE_PATH and needs no extra service."
        % backend
    )
