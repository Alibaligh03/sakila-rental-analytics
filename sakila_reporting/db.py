"""Read-only SQLite access. Every worker thread opens its own connection (SQLite connections are not shared across threads).

Connections are tracked and closed when the program exits, so an interrupted run does not leave database handles behind
(the equivalent of disposing a connection pool at exit).
"""
from __future__ import annotations

import atexit
import sqlite3
import threading
from pathlib import Path

import pandas as pd

from .logging_utils import LOG

CONNECT_TIMEOUT = 15  # seconds to wait for a locked database before failing, instead of blocking silently

_open: set[sqlite3.Connection] = set()
_lock = threading.Lock()


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """The report only reads. Opening the file read-only means a bad query can never modify the database."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=CONNECT_TIMEOUT, check_same_thread=False)
    with _lock:
        _open.add(conn)
    return conn


def _close(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    finally:
        with _lock:
            _open.discard(conn)


def open_connections() -> int:
    with _lock:
        return len(_open)


def cleanup_database_connections() -> None:
    """Close anything still open. Registered to run at exit; safe to call more than once."""
    with _lock:
        leftovers = list(_open)
    if not leftovers:
        return
    LOG.info("Cleaning up %d open database connection(s)", len(leftovers))
    for conn in leftovers:
        try:
            _close(conn)
        except Exception as exc:
            LOG.error("Error during database cleanup: %s", exc)


atexit.register(cleanup_database_connections)


def run_sql(db_path: Path, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run one query with named parameters (:name). Values are bound by the driver, never pasted into the SQL text."""
    conn = connect_readonly(db_path)
    try:
        return pd.read_sql(sql, conn, params=params or None)
    finally:
        _close(conn)


def rows(db_path: Path, sql: str, params: dict | None = None) -> list[tuple]:
    """Several rows from an independent verification query."""
    conn = connect_readonly(db_path)
    try:
        return conn.execute(sql, params or {}).fetchall()
    finally:
        _close(conn)


def scalar(db_path: Path, sql: str, params: dict | None = None):
    """One value from an independent verification query."""
    conn = connect_readonly(db_path)
    try:
        return conn.execute(sql, params or {}).fetchone()[0]
    finally:
        _close(conn)
