# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Tests for dashboard.py's build-time, sharing-safe redaction flags:

- ``--exclude-project`` (repeatable): permanently drops rows for exact
  project names *before* project/model orders, totals, RAW JSON, checkboxes,
  and insights are computed, so excluded names/task summaries never reach
  the generated HTML (unlike ``--exclude-default``, which only seeds a
  browser-display default and leaves every row embedded in the file).
- ``--omit-task-summaries``: strips all task-summary text before build so
  Work Patterns / Task Detail correctly show their existing no-summary
  state instead of carrying hidden values.

Run with: python -m pytest tests/ -v
"""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)

SCRIPT_BREAKOUT_PROJECT = 'Proj</script><script>alert(1)</script>'
COMMA_QUOTE_PROJECT = 'Weird, "Project" & <Name>'
UNICODE_PROJECT = 'Projet-café-日本語-🚀'
HTML_TASK = '<img src=x onerror=alert(1)> summary with </script> breakout'


def _rows():
    return [
        {
            "project": "Keep-Me", "model": "gpt-4o", "date": "2026-01-01", "user": "u1",
            "calls": 3, "total_tokens": 300, "total_nano_aiu": 3e10,
            "session_id": "s1", "reasoning_effort": "medium", "task_summary": "Keep this summary",
        },
        {
            "project": "Secret-Project", "model": "gpt-4o", "date": "2026-01-02", "user": "u1",
            "calls": 5, "total_tokens": 5000, "total_nano_aiu": 5e10,
            "session_id": "s2", "reasoning_effort": "high", "task_summary": "Confidential task detail",
        },
        {
            "project": "Other-Secret", "model": "gpt-4o-mini", "date": "2026-01-03", "user": "u1",
            "calls": 2, "total_tokens": 200, "total_nano_aiu": 2e10,
            "session_id": "s3", "reasoning_effort": "low", "task_summary": "Another confidential task",
        },
    ]


def _build(tmp_path, rows, exclude_projects=None, omit_task_summaries=False,
           exclude_default_projects=None, storage_key="test"):
    data = pd.DataFrame(rows)
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(
        data, str(out_path), "Copilot CLI Token Usage Dashboard",
        exclude_default_projects or [], [],
        storage_key=storage_key,
        exclude_projects=exclude_projects,
        omit_task_summaries=omit_task_summaries,
    )
    return out_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Single and repeated project exclusions
# ---------------------------------------------------------------------------

def test_single_project_exclusion_removes_rows(tmp_path, capsys):
    out = _build(tmp_path, _rows(), exclude_projects=["Secret-Project"])
    captured = capsys.readouterr()

    assert "Secret-Project" not in out
    assert "Confidential task detail" not in out
    assert "Keep-Me" in out
    assert "Other-Secret" in out

    assert "Redacted 1 row(s) across 1 project(s)" in captured.out
    assert "Secret-Project" in captured.out


def test_multiple_repeated_project_exclusions(tmp_path, capsys):
    out = _build(tmp_path, _rows(), exclude_projects=["Secret-Project", "Other-Secret"])
    captured = capsys.readouterr()

    assert "Secret-Project" not in out
    assert "Other-Secret" not in out
    assert "Confidential task detail" not in out
    assert "Another confidential task" not in out
    assert "Keep-Me" in out

    assert "Redacted 2 row(s) across 2 project(s)" in captured.out


def test_repeated_exclusion_via_cli_flag(tmp_path):
    """Exercise the actual --exclude-project (repeatable) argparse wiring."""
    csv_path = tmp_path / "usage.csv"
    pd.DataFrame(_rows()).to_csv(csv_path, index=False)
    out_path = tmp_path / "out.html"

    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "dashboard.py"),
            "--in", str(csv_path), "--out", str(out_path),
            "--exclude-project", "Secret-Project",
            "--exclude-project", "Other-Secret",
        ],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "Redacted 2 row(s) across 2 project(s)" in result.stdout

    html = out_path.read_text(encoding="utf-8")
    assert "Secret-Project" not in html
    assert "Other-Secret" not in html
    assert "Keep-Me" in html


# ---------------------------------------------------------------------------
# Hostile / unusual project names: commas, quotes, HTML/script, unicode
# ---------------------------------------------------------------------------

def test_exclusion_handles_commas_quotes_html_and_unicode_names(tmp_path):
    rows = _rows() + [
        {
            "project": SCRIPT_BREAKOUT_PROJECT, "model": "gpt-4o", "date": "2026-01-04", "user": "u1",
            "calls": 1, "total_tokens": 10, "total_nano_aiu": 1e9,
            "session_id": "s4", "reasoning_effort": "medium", "task_summary": "script breakout task",
        },
        {
            "project": COMMA_QUOTE_PROJECT, "model": "gpt-4o", "date": "2026-01-05", "user": "u1",
            "calls": 1, "total_tokens": 10, "total_nano_aiu": 1e9,
            "session_id": "s5", "reasoning_effort": "medium", "task_summary": "comma quote task",
        },
        {
            "project": UNICODE_PROJECT, "model": "gpt-4o", "date": "2026-01-06", "user": "u1",
            "calls": 1, "total_tokens": 10, "total_nano_aiu": 1e9,
            "session_id": "s6", "reasoning_effort": "medium", "task_summary": "unicode task",
        },
    ]
    out = _build(
        tmp_path, rows,
        exclude_projects=[SCRIPT_BREAKOUT_PROJECT, COMMA_QUOTE_PROJECT, UNICODE_PROJECT],
    )

    assert SCRIPT_BREAKOUT_PROJECT not in out
    assert "script breakout task" not in out
    assert COMMA_QUOTE_PROJECT not in out
    assert "comma quote task" not in out
    assert UNICODE_PROJECT not in out
    assert "unicode task" not in out
    # Repeatable --exclude-project (a list of exact strings) makes these
    # representable without any comma-splitting ambiguity - unlike
    # --exclude-default's comma-separated string.
    assert "Keep-Me" in out


# ---------------------------------------------------------------------------
# Excluded text absent byte-for-byte; retained content/totals unaffected
# ---------------------------------------------------------------------------

def test_excluded_project_and_task_text_absent_from_full_html_bytes(tmp_path):
    out = _build(tmp_path, _rows(), exclude_projects=["Secret-Project"])

    assert "Secret-Project" not in out
    assert "Confidential task detail" not in out
    # Not just the RAW JSON block - nowhere in the file at all (checkboxes,
    # project order, insights, etc. all derive from the already-redacted data).
    for needle in ("Secret-Project", "Confidential task detail"):
        assert out.count(needle) == 0


def test_non_excluded_content_present_and_totals_orders_reflect_retained_rows(tmp_path):
    out = _build(tmp_path, _rows(), exclude_projects=["Secret-Project"])

    assert "Keep-Me" in out
    assert "Other-Secret" in out
    assert "Keep this summary" in out
    assert "Another confidential task" in out

    start = out.index("const PROJECT_ORDER = ") + len("const PROJECT_ORDER = ")
    end = out.index(";\nconst MODEL_ORDER", start)
    project_order = json.loads(out[start:end])
    assert project_order == ["Keep-Me", "Other-Secret"]
    assert "Secret-Project" not in project_order

    start = out.index("const RAW = ") + len("const RAW = ")
    end = out.index(";\n// nano_aiu", start)
    records = json.loads(out[start:end])
    assert {r["project"] for r in records} == {"Keep-Me", "Other-Secret"}
    assert sum(r["total_tokens"] for r in records) == 300 + 200


# ---------------------------------------------------------------------------
# --omit-task-summaries
# ---------------------------------------------------------------------------

def test_omit_task_summaries_removes_summaries_and_disables_sections(tmp_path, capsys):
    out = _build(tmp_path, _rows(), omit_task_summaries=True)
    captured = capsys.readouterr()

    assert "Confidential task detail" not in out
    assert "Keep this summary" not in out
    assert "Another confidential task" not in out
    assert "const HAS_TASKS = false;" in out
    assert "Task summaries omitted from build" in captured.out

    start = out.index("const RAW = ") + len("const RAW = ")
    end = out.index(";\n// nano_aiu", start)
    records = json.loads(out[start:end])
    assert all("task_summary" not in r for r in records)


def test_without_omit_flag_task_summaries_are_present(tmp_path):
    out = _build(tmp_path, _rows())
    assert "const HAS_TASKS = true;" in out
    assert "Keep this summary" in out


# ---------------------------------------------------------------------------
# Overlap with --exclude-default: excluded project must not be re-embedded
# via the DEFAULT_EXCLUDED_PROJECTS localStorage seed.
# ---------------------------------------------------------------------------

def test_exclude_default_overlap_does_not_reembed_excluded_project(tmp_path):
    out = _build(
        tmp_path, _rows(),
        exclude_projects=["Secret-Project"],
        exclude_default_projects=["Secret-Project", "Other-Secret"],
    )

    assert "Secret-Project" not in out

    start = out.index("const DEFAULT_EXCLUDED_PROJECTS = ") + len("const DEFAULT_EXCLUDED_PROJECTS = ")
    end = out.index(";\n", start)
    default_excluded = json.loads(out[start:end])
    assert "Secret-Project" not in default_excluded
    assert default_excluded == ["Other-Secret"]


# ---------------------------------------------------------------------------
# Unmatched exclusion -> clear warning, not silent success
# ---------------------------------------------------------------------------

def test_unmatched_exclusion_warns_clearly(tmp_path, capsys):
    out = _build(tmp_path, _rows(), exclude_projects=["Does-Not-Exist"])
    captured = capsys.readouterr()

    assert "WARNING" in captured.out
    assert "Does-Not-Exist" in captured.out
    assert "not found in the dataset" in captured.out
    # Nothing should have been silently removed.
    assert "Keep-Me" in out
    assert "Secret-Project" in out
    assert "Other-Secret" in out


# ---------------------------------------------------------------------------
# All rows excluded -> hard error, not a broken/misleading dashboard
# ---------------------------------------------------------------------------

def test_all_rows_excluded_raises_clear_error(tmp_path):
    rows = [_rows()[0]]  # single-project dataset
    data = pd.DataFrame(rows)
    out_path = tmp_path / "out.html"

    with pytest.raises(SystemExit) as exc_info:
        dashboard.build_dashboard(
            data, str(out_path), "Title", [], [], storage_key="k",
            exclude_projects=["Keep-Me"],
        )
    message = str(exc_info.value)
    assert "ERROR" in message
    assert "removed every row" in message
    assert not out_path.exists()


def test_all_rows_excluded_via_cli_exits_nonzero(tmp_path):
    rows = [_rows()[0]]
    csv_path = tmp_path / "usage.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    out_path = tmp_path / "out.html"

    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "dashboard.py"),
            "--in", str(csv_path), "--out", str(out_path),
            "--exclude-project", "Keep-Me",
        ],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "ERROR" in result.stdout or "ERROR" in result.stderr
    assert not out_path.exists()
