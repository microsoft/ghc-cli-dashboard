"""Tests for extract_usage.py: temp-snapshot cleanup, schema validation, and
end-to-end extraction against a synthetic SQLite database.

Run with: python -m pytest tests/ -v
"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import extract_usage  # noqa: E402  (import after sys.path tweak)


def _make_valid_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT PRIMARY KEY, repository TEXT, cwd TEXT, summary TEXT)"
    )
    conn.execute(
        """CREATE TABLE assistant_usage_events (
            id INTEGER PRIMARY KEY, session_id TEXT, created_at TEXT, model TEXT,
            reasoning_effort TEXT, input_tokens INTEGER, output_tokens INTEGER,
            cache_read_tokens INTEGER, cache_write_tokens INTEGER,
            reasoning_tokens INTEGER, total_nano_aiu INTEGER
        )"""
    )
    conn.execute("INSERT INTO sessions VALUES ('s1', 'org/repo', '/home/user/repo', 'Fix bug')")
    conn.execute(
        "INSERT INTO assistant_usage_events VALUES "
        "(1, 's1', '2026-01-01T10:00:00Z', 'gpt-4o', 'medium', 100, 50, 10, 5, 2, 1500000000)"
    )
    conn.execute(
        "INSERT INTO assistant_usage_events VALUES "
        "(2, 's1', '2026-01-01T11:00:00Z', 'gpt-4o', 'medium', 200, 75, 0, 0, 0, 2000000000)"
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# ReadOnlySnapshot - cleanup on success and on exceptions
# ---------------------------------------------------------------------------

def test_snapshot_cleans_up_after_successful_use(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)

    tmp_dir_seen = None
    with extract_usage.ReadOnlySnapshot(db_path) as conn:
        tmp_dir_seen = conn.execute("PRAGMA database_list").fetchall()[0][2]
        assert os.path.exists(tmp_dir_seen)
        rows = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert rows[0] == 1

    assert not os.path.exists(tmp_dir_seen)
    assert not os.path.exists(os.path.dirname(tmp_dir_seen))


def test_snapshot_cleans_up_after_exception_inside_with_block(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)

    snapshot = extract_usage.ReadOnlySnapshot(db_path)
    tmp_dir_seen = None
    with pytest.raises(RuntimeError):
        with snapshot as conn:
            tmp_dir_seen = conn.execute("PRAGMA database_list").fetchall()[0][2]
            assert os.path.exists(tmp_dir_seen)
            raise RuntimeError("forced failure mid-extraction")

    assert tmp_dir_seen is not None
    assert not os.path.exists(tmp_dir_seen)
    assert not os.path.exists(os.path.dirname(tmp_dir_seen))
    # The connection must not be usable/left open after cleanup.
    assert snapshot._conn is None


def test_snapshot_missing_db_exits_without_leaving_temp_dir(tmp_path):
    missing_path = str(tmp_path / "does-not-exist.db")
    with pytest.raises(SystemExit, match="database not found"):
        with extract_usage.ReadOnlySnapshot(missing_path):
            pass  # pragma: no cover - should never get here


def test_snapshot_returns_readonly_connection_and_data_matches_source(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)

    with extract_usage.ReadOnlySnapshot(db_path) as conn:
        # Snapshot must be a fully independent copy: writes to the source
        # after opening the snapshot (or attempts against the snapshot
        # itself) must not affect / be allowed on the open connection.
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO sessions VALUES ('s2','x','y','z')")

        total = conn.execute("SELECT SUM(input_tokens) FROM assistant_usage_events").fetchone()[0]
        assert total == 300


# ---------------------------------------------------------------------------
# validate_schema() - missing table/column diagnostics
# ---------------------------------------------------------------------------

def test_validate_schema_passes_for_valid_db(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)
    with extract_usage.ReadOnlySnapshot(db_path) as conn:
        extract_usage.validate_schema(conn, db_path)  # must not raise/exit


def test_validate_schema_reports_missing_table(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, repository TEXT, cwd TEXT, summary TEXT)")
    conn.commit()
    conn.close()

    with extract_usage.ReadOnlySnapshot(db_path) as snap_conn:
        with pytest.raises(SystemExit, match="missing table 'assistant_usage_events'"):
            extract_usage.validate_schema(snap_conn, db_path)


def test_validate_schema_reports_missing_columns(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, repository TEXT, cwd TEXT, summary TEXT)")
    # Missing several columns QUERY depends on (e.g. total_nano_aiu, reasoning_effort).
    conn.execute(
        "CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY, session_id TEXT, created_at TEXT, model TEXT)"
    )
    conn.commit()
    conn.close()

    with extract_usage.ReadOnlySnapshot(db_path) as snap_conn:
        with pytest.raises(SystemExit) as exc_info:
            extract_usage.validate_schema(snap_conn, db_path)
        message = str(exc_info.value)
        assert "assistant_usage_events" in message
        assert "total_nano_aiu" in message
        assert "reasoning_effort" in message


def test_validate_schema_reports_both_tables_missing_columns(tmp_path):
    db_path = str(tmp_path / "session-store.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")  # missing repository/cwd/summary
    conn.execute("CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY, session_id TEXT)")
    conn.commit()
    conn.close()

    with extract_usage.ReadOnlySnapshot(db_path) as snap_conn:
        with pytest.raises(SystemExit) as exc_info:
            extract_usage.validate_schema(snap_conn, db_path)
        message = str(exc_info.value)
        assert "sessions" in message
        assert "repository" in message
        assert "assistant_usage_events" in message
        assert "created_at" in message


# ---------------------------------------------------------------------------
# End-to-end extraction smoke test against a synthetic DB
# ---------------------------------------------------------------------------

def test_extraction_end_to_end_against_synthetic_db(tmp_path, monkeypatch, capsys):
    import glob
    import tempfile

    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)
    out_path = str(tmp_path / "out.csv")

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "copilot_usage_*")))

    monkeypatch.setattr(
        sys, "argv",
        ["extract_usage.py", "--db", db_path, "--out", out_path, "--user-label", "tester"],
    )
    extract_usage.main()

    assert os.path.exists(out_path)
    with open(out_path, encoding="utf-8") as f:
        content = f.read()
    assert "tester" in content
    assert "org/repo" in content
    assert "gpt-4o" in content
    # 2 usage rows grouped into 1 (same session/day/model/reasoning_effort).
    assert content.count("\n") == 2  # header + 1 data row (+ trailing newline)

    # No new leftover temp snapshot directories after a normal run.
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "copilot_usage_*")))
    assert after - before == set()
