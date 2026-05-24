"""Test that src/dashboard/db.py opens connections with WAL + busy_timeout."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from src.dashboard.db import _conn


def test_conn_sets_wal_journal_mode(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with _conn(db) as con:
        mode = con.execute("PRAGMA journal_mode;").fetchone()[0]
    # SQLite returns the mode lowercase; WAL pragma is persisted at file level
    assert mode.lower() == "wal", f"expected wal, got {mode!r}"


def test_conn_sets_busy_timeout(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with _conn(db) as con:
        timeout = con.execute("PRAGMA busy_timeout;").fetchone()[0]
    assert timeout == 5000, f"expected 5000ms, got {timeout}"


def test_conn_closes_on_exit(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with _conn(db) as con:
        captured = con
    # After context exit, executing on the closed connection must raise
    import pytest
    with pytest.raises(sqlite3.ProgrammingError):
        captured.execute("SELECT 1")


def test_conn_row_factory_is_row(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with _conn(db) as con:
        con.execute("CREATE TABLE t (a INT, b TEXT)")
        con.execute("INSERT INTO t VALUES (1, 'x')")
        row = con.execute("SELECT a, b FROM t").fetchone()
        assert row["a"] == 1
        assert row["b"] == "x"
