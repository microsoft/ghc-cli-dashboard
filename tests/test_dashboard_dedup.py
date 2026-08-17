"""Tests for dashboard.py's deterministic, exported_at-based deduplication of
overlapping exports: newest-exported_at-wins ordering (independent of file
name), deterministic same-timestamp tie-breaking, safe vs. ambiguous legacy
(no session_id) identity handling, and conflicting-duplicate-value warnings.

Run with: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)


BASE_ROW = {
    "user": "alice", "date": "2026-01-01", "project": "org/repo", "model": "gpt-4o",
    "reasoning_effort": "medium", "input_tokens": 100, "output_tokens": 50,
    "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    "total_nano_aiu": 2e9, "session_id": "s1", "export_format_version": "2",
}


def _row(**overrides):
    row = dict(BASE_ROW)
    row.update(overrides)
    return row


def _write_csv(path: Path, rows: list) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# Newest exported_at wins, regardless of file name sort order
# ---------------------------------------------------------------------------

def test_newest_exported_at_wins_even_when_its_filename_sorts_first(tmp_path, capsys):
    older = _row(calls=3, total_tokens=150, exported_at="2026-01-10T00:00:00+00:00")
    newer = _row(calls=5, total_tokens=200, exported_at="2026-01-15T00:00:00+00:00")

    # File names are deliberately the OPPOSITE of export recency: the file
    # that glob/sorted() would visit FIRST holds the NEWER export.
    _write_csv(tmp_path / "a_first_but_newest.csv", [newer])
    _write_csv(tmp_path / "z_last_but_oldest.csv", [older])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    assert data.iloc[0]["calls"] == 5
    assert data.iloc[0]["total_tokens"] == 200

    captured = capsys.readouterr()
    assert "WARNING" in captured.out  # conflicting duplicate values -> reported
    assert "a_first_but_newest.csv" in captured.out


def test_newest_exported_at_wins_when_filenames_agree_with_recency_too(tmp_path):
    older = _row(calls=3, total_tokens=150, exported_at="2026-01-10T00:00:00+00:00")
    newer = _row(calls=5, total_tokens=200, exported_at="2026-01-15T00:00:00+00:00")

    _write_csv(tmp_path / "a_old.csv", [older])
    _write_csv(tmp_path / "z_new.csv", [newer])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    assert data.iloc[0]["calls"] == 5


# ---------------------------------------------------------------------------
# Same-timestamp ties: deterministic (documented: greatest file name wins)
# ---------------------------------------------------------------------------

def test_same_timestamp_tie_is_deterministic_by_filename_not_value(tmp_path):
    same_ts = "2026-01-10T00:00:00+00:00"
    # The file with the LOWER value ("aaa") has the lexicographically
    # GREATER name ("zzz" vs "aaa" is wrong on purpose - see below).
    row_in_aaa = _row(calls=2, total_tokens=200, exported_at=same_ts)
    row_in_zzz = _row(calls=1, total_tokens=100, exported_at=same_ts)

    _write_csv(tmp_path / "aaa.csv", [row_in_aaa])
    _write_csv(tmp_path / "zzz.csv", [row_in_zzz])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    # Per documented tie-break policy: lexicographically GREATEST file name
    # wins on an exact timestamp tie - "zzz.csv" (calls=1), even though
    # "aaa.csv" has the larger calls value. This proves the tie-break is
    # file-name based, not a "larger value wins" heuristic.
    assert data.iloc[0]["calls"] == 1


def test_same_timestamp_tie_is_stable_regardless_of_read_order(tmp_path):
    same_ts = "2026-01-10T00:00:00+00:00"
    row_a = _row(calls=1, total_tokens=100, exported_at=same_ts)
    row_b = _row(calls=2, total_tokens=200, exported_at=same_ts)
    _write_csv(tmp_path / "m_file.csv", [row_a])
    _write_csv(tmp_path / "n_file.csv", [row_b])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    # "n_file.csv" > "m_file.csv" lexicographically -> its row (calls=2) wins.
    assert data.iloc[0]["calls"] == 2


# ---------------------------------------------------------------------------
# Safe legacy (no session_id) dedup: identical values -> silent dedup
# ---------------------------------------------------------------------------

def test_legacy_rows_with_identical_values_are_safely_deduped(tmp_path, capsys):
    legacy_row = {
        "user": "bob", "date": "2026-01-02", "project": "org/repo2",
        "model": "gpt-4o-mini", "calls": 4, "total_tokens": 40,
    }
    # Two overlapping legacy exports capturing the exact same underlying
    # data (identical calls/total_tokens) - safe to treat as duplicates.
    _write_csv(tmp_path / "legacy_a.csv", [dict(legacy_row)])
    _write_csv(tmp_path / "legacy_b.csv", [dict(legacy_row)])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    captured = capsys.readouterr()
    # Deduped quietly (no ambiguity warning) - just the informational Note.
    assert "cannot safely tell" not in captured.out
    assert "Note: removed" in captured.out


# ---------------------------------------------------------------------------
# Unsafe legacy ambiguity: differing values -> retain both rows and warn
# ---------------------------------------------------------------------------

def test_legacy_rows_with_differing_values_are_not_dropped_and_warn(tmp_path, capsys):
    row1 = {
        "user": "bob", "date": "2026-01-02", "project": "org/repo2",
        "model": "gpt-4o-mini", "calls": 4, "total_tokens": 40,
    }
    row2 = dict(row1)
    row2["calls"] = 9
    row2["total_tokens"] = 90
    # Same (user, project, date, model, reasoning_effort) key, but different
    # aggregated values, and NEITHER row has a session_id - this could be a
    # real update to the same session, or two unrelated legacy sessions that
    # happen to collide on this key. Identity cannot be safely inferred.
    _write_csv(tmp_path / "legacy_a.csv", [row1])
    _write_csv(tmp_path / "legacy_b.csv", [row2])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    # Nothing dropped - both rows retained since identity is ambiguous.
    assert len(data) == 2
    assert set(data["calls"]) == {4, 9}

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "cannot safely tell" in captured.out
    assert "legacy_a.csv" in captured.out
    assert "legacy_b.csv" in captured.out


def test_legacy_naive_key_does_not_merge_unrelated_rows_across_projects(tmp_path):
    # Two truly unrelated legacy rows that differ by project - even though
    # user/date/model/reasoning_effort match, they must never be merged
    # (this guards against the "naive key that merges unrelated legacy
    # rows" failure mode - project must be part of the legacy identity key).
    row1 = {
        "user": "bob", "date": "2026-01-02", "project": "org/repoA",
        "model": "gpt-4o-mini", "calls": 4, "total_tokens": 40,
    }
    row2 = {
        "user": "bob", "date": "2026-01-02", "project": "org/repoB",
        "model": "gpt-4o-mini", "calls": 4, "total_tokens": 40,
    }
    _write_csv(tmp_path / "legacy_a.csv", [row1])
    _write_csv(tmp_path / "legacy_b.csv", [row2])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 2
    assert set(data["project"]) == {"org/repoA", "org/repoB"}


# ---------------------------------------------------------------------------
# Mixed legacy/current exports: legacy rows (no session_id) preserved
# alongside current (session_id-bearing) rows, without cross-contamination
# ---------------------------------------------------------------------------

def test_mixed_legacy_and_current_rows_do_not_double_count_or_cross_dedup(tmp_path):
    current_row = _row(calls=3, total_tokens=150, exported_at="2026-01-10T00:00:00+00:00")
    legacy_row = {
        "user": "alice", "date": "2026-01-01", "project": "org/repo",
        "model": "gpt-4o", "reasoning_effort": "medium", "calls": 3, "total_tokens": 150,
    }
    # legacy_row happens to share every value with current_row's (user,
    # project, date, model, reasoning_effort, calls, total_tokens) - but the
    # current row carries a session_id, so they must NOT be silently merged
    # by a key that ignores session_id.
    _write_csv(tmp_path / "current.csv", [current_row])
    _write_csv(tmp_path / "legacy.csv", [legacy_row])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 2  # both retained: distinct identity keys (sid vs legacy)


# ---------------------------------------------------------------------------
# Conflicting duplicate values for the SAME (session_id-based) identity:
# warn with file/key context and state which row wins
# ---------------------------------------------------------------------------

def test_conflicting_session_duplicate_warns_with_file_and_key_context_and_winner(tmp_path, capsys):
    older = _row(calls=3, total_tokens=150, exported_at="2026-01-10T00:00:00+00:00")
    newer = _row(calls=7, total_tokens=300, exported_at="2026-01-20T00:00:00+00:00")
    _write_csv(tmp_path / "export_old.csv", [older])
    _write_csv(tmp_path / "export_new.csv", [newer])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    assert data.iloc[0]["calls"] == 7

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    # File context for both conflicting sources.
    assert "export_old.csv" in captured.out
    assert "export_new.csv" in captured.out
    # Key context: identity fields.
    assert "session_id=s1" in captured.out
    assert "date=2026-01-01" in captured.out
    # States which row won.
    assert "kept the row from" in captured.out
    assert "export_new.csv" in captured.out.split("kept the row from")[-1]


def test_conflicting_project_value_for_same_session_is_flagged(tmp_path, capsys):
    """project isn't part of the session_id-based key, but a genuine data
    inconsistency (same session_id claiming two different projects across
    exports) must still be surfaced as a conflict, not silently ignored."""
    row1 = _row(project="org/repo-old-name", exported_at="2026-01-10T00:00:00+00:00", calls=3, total_tokens=150)
    row2 = _row(project="org/repo-new-name", exported_at="2026-01-15T00:00:00+00:00", calls=3, total_tokens=150)
    _write_csv(tmp_path / "export_old.csv", [row1])
    _write_csv(tmp_path / "export_new.csv", [row2])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    assert data.iloc[0]["project"] == "org/repo-new-name"
    captured = capsys.readouterr()
    assert "WARNING" in captured.out


# ---------------------------------------------------------------------------
# Non-conflicting duplicates (identical rows re-exported) still dedupe
# silently (no spurious conflict warning) using exported_at ordering
# ---------------------------------------------------------------------------

def test_identical_duplicate_rows_across_exports_dedupe_without_conflict_warning(tmp_path, capsys):
    row = _row(calls=3, total_tokens=150, exported_at="2026-01-10T00:00:00+00:00")
    row_rexported = dict(row)
    row_rexported["exported_at"] = "2026-01-15T00:00:00+00:00"  # same data, re-exported later

    _write_csv(tmp_path / "week1.csv", [row])
    _write_csv(tmp_path / "week2.csv", [row_rexported])

    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 1
    captured = capsys.readouterr()
    assert "conflicting duplicate" not in captured.out
    assert "Note: removed" in captured.out
