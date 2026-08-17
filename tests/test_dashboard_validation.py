"""Tests for dashboard.py's CSV schema/value validation: required vs.
optional columns, numeric coercion/rejection, and date validation.

Run with: python -m pytest tests/ -v
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)


CURRENT_ROW = {
    "user": "alice", "date": "2026-01-01", "project": "org/repo", "model": "gpt-4o",
    "reasoning_effort": "medium", "calls": 3, "input_tokens": 100, "output_tokens": 50,
    "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
    "total_tokens": 150, "total_nano_aiu": 2e9, "session_id": "s1",
}

# A "legacy" export missing optional columns that current extract_usage.py
# adds (session_id, reasoning_effort, total_nano_aiu, task_summary).
LEGACY_ROW = {
    "user": "bob", "date": "2026-01-02", "project": "org/repo2", "model": "gpt-4o-mini",
    "calls": 1, "total_tokens": 20,
}


def _write_csv(path: Path, rows: list) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---------------------------------------------------------------------------
# Valid current / legacy CSV loading (compatibility)
# ---------------------------------------------------------------------------

def test_loads_valid_current_csv(tmp_path):
    csv_path = _write_csv(tmp_path / "current.csv", [CURRENT_ROW])
    data = dashboard.load_data(csv_path)
    assert len(data) == 1
    assert data.iloc[0]["project"] == "org/repo"
    assert data.iloc[0]["session_id"] == "s1"


def test_loads_valid_legacy_csv_with_documented_defaults(tmp_path):
    csv_path = _write_csv(tmp_path / "legacy.csv", [LEGACY_ROW])
    data = dashboard.load_data(csv_path)
    assert len(data) == 1
    row = data.iloc[0]
    # Optional columns must be back-filled with safe, documented defaults.
    assert row["reasoning_effort"] == "n/a"
    assert row["total_nano_aiu"] == 0.0
    assert pd.isna(row["session_id"]) or row["session_id"] is None
    assert "task_summary" not in data.columns  # never defaulted; has_tasks stays False


def test_loads_mixed_current_and_legacy_csvs_together(tmp_path):
    current_path = _write_csv(tmp_path / "current.csv", [CURRENT_ROW])
    legacy_path = _write_csv(tmp_path / "legacy.csv", [LEGACY_ROW])
    data = dashboard.load_data(str(tmp_path / "*.csv"))
    assert len(data) == 2
    assert set(data["project"]) == {"org/repo", "org/repo2"}


def test_dashboard_smoke_generation_from_valid_csv(tmp_path):
    csv_path = _write_csv(tmp_path / "current.csv", [CURRENT_ROW])
    data = dashboard.load_data(csv_path)
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(data, str(out_path), "Title", [], [], storage_key="k")
    html = out_path.read_text(encoding="utf-8")
    assert "<html>" in html
    assert "org/repo" in html


# ---------------------------------------------------------------------------
# Missing required columns -> actionable ERROR, not AttributeError/KeyError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_col", dashboard.REQUIRED_COLUMNS)
def test_missing_required_column_raises_actionable_error(tmp_path, missing_col):
    row = dict(CURRENT_ROW)
    del row[missing_col]
    csv_path = _write_csv(tmp_path / "bad.csv", [row])

    with pytest.raises(SystemExit) as exc_info:
        dashboard.load_data(csv_path)
    message = str(exc_info.value)
    assert "ERROR" in message
    assert missing_col in message
    assert "bad.csv" in message


def test_missing_multiple_required_columns_lists_all(tmp_path):
    row = dict(CURRENT_ROW)
    del row["calls"]
    del row["total_tokens"]
    csv_path = _write_csv(tmp_path / "bad.csv", [row])

    with pytest.raises(SystemExit) as exc_info:
        dashboard.load_data(csv_path)
    message = str(exc_info.value)
    assert "calls" in message
    assert "total_tokens" in message


# ---------------------------------------------------------------------------
# Invalid numeric values: non-numeric, NaN, infinity, negative
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("col", ["calls", "total_tokens", "total_nano_aiu"])
@pytest.mark.parametrize("bad_value", ["not-a-number", float("nan"), float("inf"), float("-inf"), -5])
def test_invalid_numeric_value_is_rejected_not_coerced_to_zero(tmp_path, col, bad_value):
    row = dict(CURRENT_ROW)
    row[col] = bad_value
    csv_path = _write_csv(tmp_path / "bad_numeric.csv", [row])

    with pytest.raises(SystemExit) as exc_info:
        dashboard.load_data(csv_path)
    message = str(exc_info.value)
    assert "ERROR" in message
    assert col in message
    assert "bad_numeric.csv" in message
    assert "row" in message.lower()


def test_valid_zero_numeric_values_are_accepted(tmp_path):
    row = dict(CURRENT_ROW)
    row["calls"] = 0
    row["total_tokens"] = 0
    row["total_nano_aiu"] = 0
    csv_path = _write_csv(tmp_path / "zero.csv", [row])
    data = dashboard.load_data(csv_path)
    assert len(data) == 1
    assert data.iloc[0]["calls"] == 0


def test_numeric_error_identifies_specific_row(tmp_path):
    rows = [dict(CURRENT_ROW), dict(CURRENT_ROW)]
    rows[1]["calls"] = -1
    rows[1]["session_id"] = "s2"
    csv_path = _write_csv(tmp_path / "bad_row.csv", rows)

    with pytest.raises(SystemExit) as exc_info:
        dashboard.load_data(csv_path)
    message = str(exc_info.value)
    # Row 2 in the DataFrame is CSV row 3 (header + 1-based data rows).
    assert "3" in message


# ---------------------------------------------------------------------------
# Malformed / missing dates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_date", ["not-a-date", "2026-13-40", "", "32/13/2026"])
def test_malformed_date_is_rejected(tmp_path, bad_date):
    row = dict(CURRENT_ROW)
    row["date"] = bad_date
    csv_path = _write_csv(tmp_path / "bad_date.csv", [row])

    with pytest.raises(SystemExit) as exc_info:
        dashboard.load_data(csv_path)
    message = str(exc_info.value)
    assert "ERROR" in message
    assert "date" in message
    assert "bad_date.csv" in message


def test_valid_date_formats_are_accepted(tmp_path):
    row = dict(CURRENT_ROW)
    row["date"] = "2026-01-01"
    csv_path = _write_csv(tmp_path / "good_date.csv", [row])
    data = dashboard.load_data(csv_path)
    assert len(data) == 1
