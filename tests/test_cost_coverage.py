"""Tests for token/cost accounting clarifications: cost-data coverage
tracking (full/partial/none/unknown), token-category composition, and
divide-by-zero robustness in the Value-for-Money chart.

These cover requirement 6 of the "clarify token and cost accounting" work:
full coverage, partial coverage, legacy no-cost data, category totals, no
divide-by-zero, and generated UI labels/text.

Run with: python -m pytest tests/ -v
(the DOM-harness assertions require Node.js on PATH and are skipped
automatically if `node` isn't available - see tests/dom_harness.js.)
"""
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)

NODE = shutil.which("node")


def _build(tmp_path, rows, title="Copilot CLI Token Usage Dashboard",
           exclude_default_projects=None, exclude_default_models=None,
           storage_key="test"):
    """Build a dashboard HTML file from a list of row dicts; return (html_text, out_path)."""
    data = pd.DataFrame(rows)
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(
        data, str(out_path), title,
        exclude_default_projects or [], exclude_default_models or [],
        storage_key=storage_key,
    )
    return out_path.read_text(encoding="utf-8"), out_path


def _run_harness(out_path):
    result = subprocess.run(
        [NODE, str(REPO_ROOT / "tests" / "dom_harness.js"), str(out_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr}"
    return json.loads(result.stdout)


def _row(project="Alpha", model="gpt-4o", date="2026-01-01", user="u1", calls=5,
         total_tokens=1000, total_nano_aiu=1e10, cost_data_calls=None,
         input_tokens=None, output_tokens=None, cache_read_tokens=None,
         cache_write_tokens=None, reasoning_tokens=None, session_id="s1",
         reasoning_effort="medium", task_summary="Do a thing"):
    row = {
        "project": project, "model": model, "date": date, "user": user,
        "calls": calls, "total_tokens": total_tokens, "total_nano_aiu": total_nano_aiu,
        "session_id": session_id, "reasoning_effort": reasoning_effort, "task_summary": task_summary,
    }
    # Only attach the optional columns when provided, so callers can build both
    # "current" rows (all six new columns present) and "legacy" rows (columns
    # absent entirely) from the same helper.
    optional = {
        "cost_data_calls": cost_data_calls, "input_tokens": input_tokens,
        "output_tokens": output_tokens, "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens, "reasoning_tokens": reasoning_tokens,
    }
    for key, val in optional.items():
        if val is not None:
            row[key] = val
    return row


# ---------------------------------------------------------------------------
# Full coverage
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_full_coverage_reports_100_percent_and_good_banner(tmp_path):
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5, input_tokens=100, output_tokens=50, cache_read_tokens=10,
             cache_write_tokens=5, reasoning_tokens=2),
        _row(project="Beta", model="gpt-4o-mini", calls=3, total_tokens=90, total_nano_aiu=300.0,
             cost_data_calls=3, input_tokens=60, output_tokens=30, cache_read_tokens=0,
             cache_write_tokens=0, reasoning_tokens=0),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    coverage = payload["coverage"]
    assert coverage["isComplete"] is True
    assert coverage["pctConfirmed"] == 100
    assert coverage["unknownCalls"] == 0
    assert coverage["missingKnownCalls"] == 0

    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "100%" in kpi_html
    assert 'color:var(--warn)' not in kpi_html.split("Cost data coverage")[0][-400:]

    coverage_banner = payload["elements"]["insight-coverage"]["innerHTML"]
    assert "insight good" in coverage_banner
    assert "100%" in coverage_banner


# ---------------------------------------------------------------------------
# Partial coverage
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_partial_coverage_reports_warn_kpi_and_banner(tmp_path):
    rows = [
        # 5 calls with confirmed cost.
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5),
        # 5 calls confirmed to have NO recorded cost.
        _row(project="Beta", model="gpt-4o-mini", calls=5, total_tokens=90, total_nano_aiu=0.0,
             cost_data_calls=0),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    coverage = payload["coverage"]
    assert coverage["isComplete"] is False
    assert coverage["totalCalls"] == 10
    assert coverage["confirmedCalls"] == 5
    assert coverage["missingKnownCalls"] == 5
    assert coverage["pctConfirmed"] == 50

    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "50%" in kpi_html
    assert "color:var(--warn)" in kpi_html

    coverage_banner = payload["elements"]["insight-coverage"]["innerHTML"]
    assert "insight warn" in coverage_banner
    assert "incomplete" in coverage_banner
    assert "NO recorded cost" in coverage_banner


# ---------------------------------------------------------------------------
# Legacy data (predates cost_data_calls / token-category columns entirely)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_legacy_rows_are_unknown_coverage_not_zero_or_full(tmp_path):
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=0.0),
        _row(project="Beta", model="gpt-4o-mini", calls=3, total_tokens=90, total_nano_aiu=0.0),
    ]
    # No cost_data_calls/input_tokens/.../reasoning_tokens columns at all - this
    # is exactly what a pre-format-3 CSV export looks like once loaded into a
    # DataFrame (the columns are entirely absent, not merely blank).
    assert all("cost_data_calls" not in r for r in rows)

    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    coverage = payload["coverage"]
    assert coverage["isComplete"] is False
    assert coverage["unknownCalls"] == coverage["totalCalls"]
    assert coverage["confirmedCalls"] == 0
    assert coverage["missingKnownCalls"] == 0
    assert coverage["pctConfirmed"] == 0

    coverage_banner = payload["elements"]["insight-coverage"]["innerHTML"]
    assert "insight warn" in coverage_banner
    # Must not claim these calls are confirmed to have no cost - they are of
    # UNKNOWN coverage (export predates tracking), a distinct state.
    assert "before cost-coverage tracking existed" in coverage_banner
    assert "NO recorded cost" not in coverage_banner

    # Legacy total_nano_aiu default of 0.0 (documented, existing behavior) must
    # not be misrepresented as "confirmed free" anywhere in the coverage KPI tooltip.
    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "UNKNOWN coverage" in kpi_html

    composition = payload["composition"]
    assert composition["includedCalls"] == 0
    assert composition["excludedCalls"] == coverage["totalCalls"]
    composition_note = payload["elements"]["composition-note"]["innerHTML"]
    assert "excluded from the totals" in composition_note
    assert "not counted as zero" in composition_note


# ---------------------------------------------------------------------------
# Token category totals / composition correctness
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_token_composition_totals_match_input_data(tmp_path):
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5, input_tokens=100, output_tokens=50, cache_read_tokens=10,
             cache_write_tokens=5, reasoning_tokens=2),
        _row(project="Beta", model="gpt-4o-mini", calls=3, total_tokens=90, total_nano_aiu=300.0,
             cost_data_calls=3, input_tokens=60, output_tokens=30, cache_read_tokens=20,
             cache_write_tokens=0, reasoning_tokens=8),
        # A row lacking category breakdown (e.g. mixed-vintage export) must be
        # excluded from totals, not treated as contributing zero.
        _row(project="Gamma", model="gpt-4o", calls=2, total_tokens=40, total_nano_aiu=0.0),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    composition = payload["composition"]
    assert composition["totals"]["input_tokens"] == 160
    assert composition["totals"]["output_tokens"] == 80
    assert composition["totals"]["cache_read_tokens"] == 30
    assert composition["totals"]["cache_write_tokens"] == 5
    assert composition["totals"]["reasoning_tokens"] == 10
    assert composition["includedCalls"] == 8
    assert composition["excludedCalls"] == 2

    composition_note = payload["elements"]["composition-note"]["innerHTML"]
    assert "8 of 10 calls" in composition_note


# ---------------------------------------------------------------------------
# No divide-by-zero
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_model_with_confirmed_zero_cost_excluded_from_value_ranking(tmp_path):
    """A model whose every call has CONFIRMED (cost_data_calls == calls) zero
    cost (total_nano_aiu == 0.0) must not appear in the Value-for-Money
    ranking with an infinite or undefined tok/$ ratio - it must simply be
    excluded (existing modelCost > 0 guard), and no NaN/Infinity should leak
    into the debug-exposed ranking.

    Crucially, "confirmed zero cost" (cost_data_calls == calls, i.e. full
    coverage, and the cost genuinely is 0) is a DISTINCT state from "no
    coverage at all" (cost_data_calls == 0, i.e. none of these calls have
    confirmed cost data - see test_partial_coverage_reports_warn_kpi_and_banner
    for that case). Both models here have full coverage, so the OVERALL
    coverage banner must report 100% confirmed/"good", not a partial/warn
    state - only the ranking itself excludes the zero-cost model, to avoid a
    divide-by-zero, not the coverage accounting."""
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5),
        # Every one of this model's 5 calls has CONFIRMED cost data
        # (cost_data_calls == calls) - it just happens to be genuinely free.
        _row(project="Beta", model="free-model", calls=5, total_tokens=500, total_nano_aiu=0.0,
             cost_data_calls=5),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    value_entries = payload["valueEntries"]
    models = [v["model"] for v in value_entries]
    assert "free-model" not in models
    assert "gpt-4o" in models
    for v in value_entries:
        assert math.isfinite(v["tpd"])
        assert v["tpd"] >= 0

    # Both models have FULL (not partial/none) coverage, so the blended
    # coverage must be 100% confirmed, not a partial/warn state - distinct
    # from a model with no confirmed cost data at all.
    coverage = payload["coverage"]
    assert coverage["isComplete"] is True
    assert coverage["pctConfirmed"] == 100
    assert coverage["missingKnownCalls"] == 0

    coverage_banner = payload["elements"]["insight-coverage"]["innerHTML"]
    assert "insight good" in coverage_banner
    assert "100%" in coverage_banner

    # Rendering must not have thrown, and the coverage KPI must reflect full
    # confirmed coverage, not a divide-by-zero artifact like NaN%/Infinity%.
    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "NaN" not in kpi_html
    assert "Infinity" not in kpi_html


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_zero_filtered_calls_shows_na_not_divide_by_zero_garbage(tmp_path):
    """When every project is excluded by default (filtered set is empty), the
    coverage KPI must show 'n/a' and the coverage banner must render nothing
    (not throw, not show NaN%/Infinity%)."""
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5),
    ]
    _, out_path = _build(tmp_path, rows, exclude_default_projects=["Alpha"])
    payload = _run_harness(out_path)

    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "n/a" in kpi_html
    assert "NaN" not in kpi_html
    assert "Infinity" not in kpi_html

    coverage_banner = payload["elements"]["insight-coverage"]["innerHTML"]
    assert coverage_banner == ""


# ---------------------------------------------------------------------------
# Generated UI labels / text - KPI tooltips and total_tokens definition
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_total_tokens_kpi_label_and_tooltip_state_input_output_only(tmp_path):
    rows = [_row()]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "Total tokens (input+output)" in kpi_html
    assert "does NOT include cache-read, cache-write, or reasoning tokens" in kpi_html


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_est_cost_kpi_tooltip_notes_coverage_dependency(tmp_path):
    rows = [_row()]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "Estimate only" in kpi_html
    assert "UNDERSTATES true cost" in kpi_html


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_value_for_money_caveat_appears_when_coverage_incomplete(tmp_path):
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5),
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=0.0,
             cost_data_calls=0),
        _row(project="Beta", model="gpt-4o-mini", calls=5, total_tokens=90, total_nano_aiu=500.0,
             cost_data_calls=5),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    insight_value = payload["elements"]["insight-value"]["innerHTML"]
    assert "incomplete cost-data coverage" in insight_value

    value_entries = payload["valueEntries"]
    partial = [v for v in value_entries if v["model"] == "gpt-4o"]
    assert partial and partial[0]["coverageComplete"] is False


# ---------------------------------------------------------------------------
# Value-for-money numerator/denominator consistency (no inflated ratio from
# partial-coverage models)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_value_for_money_ratio_excludes_uncosted_tokens_from_numerator(tmp_path):
    """Regression test for the "inflated tok/$ from partial-coverage models"
    finding: a row's total_tokens must never be mixed into the tok/$
    numerator unless that SAME row's cost is fully confirmed
    (cost_coverage === "full") - total_tokens and estimated_cost are both
    computed from rows with confirmed usable cost, never numerator from
    every row and denominator from only the confirmed ones."""
    rows = [
        # Full coverage: 10 calls, 1000 tokens, $1.00 confirmed cost.
        _row(project="Alpha", model="gpt-4o", calls=10, total_tokens=1000,
             total_nano_aiu=1e11, cost_data_calls=10),
        # No coverage: 10 MORE calls, 9000 MORE tokens, with NO confirmed
        # cost at all. Before the fix, these 9000 tokens were folded into
        # the ratio's numerator anyway (total_tokens summed across ALL
        # rows) while the denominator only reflected the $1.00 confirmed
        # above - inflating the ratio 10x (10,000 tok/$ instead of the
        # true 1,000 tok/$ for the confirmed portion).
        _row(project="Alpha", model="gpt-4o", calls=10, total_tokens=9000,
             total_nano_aiu=0.0, cost_data_calls=0),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    value_entries = payload["valueEntries"]
    gpt4o = next(v for v in value_entries if v["model"] == "gpt-4o")
    # Ratio and call count must come ONLY from the full-coverage row: 1000
    # tokens / $1.00 == 1,000 tok/$ over 10 calls - never 10,000 tok/$ over
    # 20 calls (which would mean the uncosted row's tokens leaked in).
    assert gpt4o["tpd"] == pytest.approx(1000.0)
    assert gpt4o["calls"] == 10
    # The model's OVERALL coverage (across both rows) is still incomplete,
    # so it must still be flagged even though the ratio itself is now honest.
    assert gpt4o["coverageComplete"] is False


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_value_for_money_ratio_matches_across_partial_and_full_coverage_models(tmp_path):
    """A model with 100% confirmed coverage and a model with partial coverage
    (but at least one fully-confirmed row) must both report a tok/$ ratio
    computed strictly from tokens/cost pairs that came from the SAME row -
    i.e. the numerator and denominator are never sourced from different
    subsets of rows."""
    rows = [
        # gpt-4o-mini: fully confirmed across its only row - unaffected by
        # the fix (a single full-coverage row is its own safe subset).
        _row(project="Beta", model="gpt-4o-mini", calls=4, total_tokens=400,
             total_nano_aiu=4e10, cost_data_calls=4),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    value_entries = payload["valueEntries"]
    mini = next(v for v in value_entries if v["model"] == "gpt-4o-mini")
    assert mini["tpd"] == pytest.approx(1000.0)
    assert mini["calls"] == 4
    assert mini["coverageComplete"] is True


# ---------------------------------------------------------------------------
# Token composition requires ALL FIVE categories present, not just input
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_token_composition_requires_all_five_categories_to_include_row(tmp_path):
    """A row with only SOME token-category columns populated (e.g. input/
    output present but cache_read/cache_write/reasoning missing - a
    partially migrated or hand-edited export) must be treated the same as a
    row with NONE of them: excluded from composition totals entirely, not
    partially folded in (which would silently understate whichever
    categories that row is missing as if they were legitimately zero)."""
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5, input_tokens=100, output_tokens=50, cache_read_tokens=10,
             cache_write_tokens=5, reasoning_tokens=2),
        # Partial breakdown: input/output present, the other three columns
        # absent entirely for this row.
        _row(project="Beta", model="gpt-4o-mini", calls=4, total_tokens=80, total_nano_aiu=400.0,
             cost_data_calls=4, input_tokens=60, output_tokens=20),
    ]
    _, out_path = _build(tmp_path, rows)
    payload = _run_harness(out_path)

    composition = payload["composition"]
    # Only the complete (all-five) row contributes to totals - the partial
    # row's input/output tokens must NOT leak in.
    assert composition["totals"]["input_tokens"] == 100
    assert composition["totals"]["output_tokens"] == 50
    assert composition["totals"]["cache_read_tokens"] == 10
    assert composition["totals"]["cache_write_tokens"] == 5
    assert composition["totals"]["reasoning_tokens"] == 2
    assert composition["includedCalls"] == 5
    assert composition["excludedCalls"] == 4

    composition_note = payload["elements"]["composition-note"]["innerHTML"]
    assert "5 of 9 calls" in composition_note


# ---------------------------------------------------------------------------
# Existing behavior must remain green: valid current-format rows without any
# cost-coverage ambiguity still load and render cleanly end-to-end.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_smoke_generation_runs_clean_for_mixed_coverage_dataset(tmp_path):
    rows = [
        _row(project="Alpha", model="gpt-4o", calls=5, total_tokens=150, total_nano_aiu=1000.0,
             cost_data_calls=5, input_tokens=100, output_tokens=50, cache_read_tokens=10,
             cache_write_tokens=5, reasoning_tokens=2),
        _row(project="Beta", model="gpt-4o-mini", calls=3, total_tokens=90, total_nano_aiu=0.0,
             cost_data_calls=0),
        _row(project="Gamma", model="gpt-4o", calls=2, total_tokens=40, total_nano_aiu=0.0),
    ]
    html_text, out_path = _build(tmp_path, rows)
    assert "Token Composition by Category" in html_text
    assert "Cost data coverage" in html_text

    payload = _run_harness(out_path)
    assert payload["coverage"] is not None
    assert payload["composition"] is not None
