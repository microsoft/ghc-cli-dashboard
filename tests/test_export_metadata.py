"""Tests for extract_usage.py's export metadata (`export_format_version`,
`exported_at`) and dashboard.py's handling of that metadata: parsing/
timezone-awareness, and the legacy (pre-metadata) compatibility fallback.

Run with: python -m pytest tests/ -v
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)
import extract_usage  # noqa: E402


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
    conn.commit()
    conn.close()


CURRENT_ROW = {
    "user": "alice", "date": "2026-01-01", "project": "org/repo", "model": "gpt-4o",
    "reasoning_effort": "medium", "calls": 3, "input_tokens": 100, "output_tokens": 50,
    "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    "total_tokens": 150, "total_nano_aiu": 2e9, "session_id": "s1",
}

# A true legacy export: predates export_format_version/exported_at entirely.
LEGACY_ROW = {
    "user": "bob", "date": "2026-01-02", "project": "org/repo2", "model": "gpt-4o-mini",
    "calls": 1, "total_tokens": 20,
}


def _write_csv(path: Path, rows: list) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# extract_usage.py emits parseable, timezone-aware export metadata
# ---------------------------------------------------------------------------

def test_extract_usage_emits_export_format_version_and_exported_at(tmp_path, monkeypatch):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)
    out_path = str(tmp_path / "out.csv")

    monkeypatch.setattr(
        sys, "argv",
        ["extract_usage.py", "--db", db_path, "--out", out_path, "--user-label", "tester"],
    )
    extract_usage.main()

    df = pd.read_csv(out_path)
    assert "export_format_version" in df.columns
    assert "exported_at" in df.columns
    assert (df["export_format_version"].astype(str) == extract_usage.EXPORT_FORMAT_VERSION).all()
    assert extract_usage.EXPORT_FORMAT_VERSION == "2"

    # exported_at must be parseable and timezone-aware (not a naive timestamp).
    parsed = pd.to_datetime(df["exported_at"])
    assert parsed.dt.tz is not None
    # All rows in a single run share one export timestamp.
    assert parsed.nunique() == 1


def test_extract_usage_exported_at_is_utc(tmp_path, monkeypatch):
    db_path = str(tmp_path / "session-store.db")
    _make_valid_db(db_path)
    out_path = str(tmp_path / "out.csv")

    monkeypatch.setattr(
        sys, "argv",
        ["extract_usage.py", "--db", db_path, "--out", out_path, "--user-label", "tester"],
    )
    extract_usage.main()

    df = pd.read_csv(out_path)
    parsed = pd.to_datetime(df["exported_at"])
    assert str(parsed.iloc[0].tzinfo) in ("UTC", "UTC+00:00") or parsed.iloc[0].utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# dashboard.py compatibility policy: legacy CSVs (no metadata) load, warn
# ---------------------------------------------------------------------------

def test_legacy_csv_without_metadata_loads_with_warning(tmp_path, capsys):
    csv_path = _write_csv(tmp_path / "legacy.csv", [LEGACY_ROW])
    data = dashboard.load_data(csv_path)

    assert len(data) == 1
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "legacy.csv" in captured.out
    assert "export metadata" in captured.out
    # Back-filled with the documented legacy sentinel version.
    assert (data["export_format_version"] == dashboard.LEGACY_EXPORT_FORMAT_VERSION).all()


def test_current_csv_with_metadata_loads_without_metadata_warning(tmp_path, capsys):
    row = dict(CURRENT_ROW)
    row["export_format_version"] = "2"
    row["exported_at"] = "2026-01-03T00:00:00+00:00"
    csv_path = _write_csv(tmp_path / "current.csv", [row])

    data = dashboard.load_data(csv_path)
    assert len(data) == 1
    captured = capsys.readouterr()
    assert "WARNING" not in captured.out


def test_unrecognized_export_format_version_warns_but_still_loads(tmp_path, capsys):
    row = dict(CURRENT_ROW)
    row["export_format_version"] = "99"
    row["exported_at"] = "2026-01-03T00:00:00+00:00"
    csv_path = _write_csv(tmp_path / "future.csv", [row])

    data = dashboard.load_data(csv_path)
    assert len(data) == 1
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "unrecognized export_format_version" in captured.out


def test_mixed_legacy_and_current_csvs_both_load(tmp_path, capsys):
    current_row = dict(CURRENT_ROW)
    current_row["export_format_version"] = "2"
    current_row["exported_at"] = "2026-01-03T00:00:00+00:00"
    _write_csv(tmp_path / "current.csv", [current_row])
    _write_csv(tmp_path / "legacy.csv", [LEGACY_ROW])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 2
    captured = capsys.readouterr()
    assert "legacy.csv" in captured.out
    assert "WARNING" in captured.out
