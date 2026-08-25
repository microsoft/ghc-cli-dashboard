"""Tests for dashboard.py's Provider filter/visual feature:

- Derived `provider` field embedded in every generated record (from
  provider_classifier.classify_provider(), never recomputed in JS).
- The Provider filter panel: Select all/none, checkbox markup, a storage key
  independent from project/model, and "all selected" default.
- Independent AND semantics across project/model/provider/date filters at
  the single RAW.filter() choke point, exercised end-to-end via the Node DOM
  harness (falls back to skip if Node.js isn't on PATH).
- Zero-state (all providers excluded), Unknown-provider visibility, hostile
  model/provider safety, and current/legacy CSV compatibility.

Run with: python -m pytest tests/ -v
(DOM-harness tests require Node.js on PATH; they are skipped automatically
if `node` isn't available - see test_dashboard_security.py's docstring.)
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import dashboard  # noqa: E402  (import after sys.path tweak)
import provider_classifier as pc  # noqa: E402

NODE = shutil.which("node")
HARNESS = str(REPO_ROOT / "tests" / "dom_harness.js")


def _build(tmp_path, rows, storage_key="test", **kwargs) -> str:
    data = pd.DataFrame(rows)
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(
        data, str(out_path), "Copilot CLI Token Usage Dashboard",
        kwargs.pop("exclude_default_projects", []) or [],
        kwargs.pop("exclude_default_models", []) or [],
        storage_key=storage_key, **kwargs,
    )
    return out_path.read_text(encoding="utf-8")


def _row(**overrides):
    row = {
        "project": "Alpha", "model": "gpt-4o", "date": "2026-01-01", "user": "u1",
        "calls": 5, "total_tokens": 1000, "total_nano_aiu": 1e10,
        "session_id": "s1", "reasoning_effort": "medium", "task_summary": "Do a thing",
    }
    row.update(overrides)
    return row


def _run_harness(out_path, storage_seed=None):
    args = [NODE, HARNESS, str(out_path)]
    if storage_seed is not None:
        args.append(json.dumps(storage_seed))
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"harness failed: {result.stderr}"
    return json.loads(result.stdout)


def _extract_json_const(html, name):
    """Extract and JSON-parse the RHS of `const {name} = ...;` from generated
    HTML. Each `const X = ...;` is emitted on its own single physical line
    (see build_dashboard()'s f-string template), so the first ";\\n" after
    the value's start is always its own terminator - robust regardless of
    whatever comments/other consts happen to follow on subsequent lines."""
    marker = f"const {name} = "
    start = html.index(marker) + len(marker)
    end = html.index(";\n", start)
    return json.loads(html[start:end])


# ---------------------------------------------------------------------------
# Derived provider field in generated records (no CSV schema change)
# ---------------------------------------------------------------------------

def test_provider_field_present_in_every_record_and_csv_schema_unchanged(tmp_path):
    rows = [
        _row(model="claude-opus-5"),
        _row(model="gpt-5.4", session_id="s2"),
        _row(model="gemini-3.5-flash", session_id="s3"),
        _row(model="grok-4.5", session_id="s4"),
        _row(model="some-unmapped-model", session_id="s5"),
    ]
    out = _build(tmp_path, rows)
    records = _extract_json_const(out, "RAW")
    by_model = {r["model"]: r["provider"] for r in records}

    assert by_model["claude-opus-5"] == "Anthropic"
    assert by_model["gpt-5.4"] == "OpenAI"
    assert by_model["gemini-3.5-flash"] == "Google"
    assert by_model["grok-4.5"] == "xAI"
    assert by_model["some-unmapped-model"] == pc.OTHER_UNKNOWN_PROVIDER

    # Raw model values are completely unchanged - only a NEW derived field
    # (`provider`) was added; nothing about the model identifier itself was
    # rewritten, and no CSV column was introduced (this is HTML/JSON output).
    for r in records:
        assert r["model"] in by_model
    assert set(by_model.keys()) == {
        "claude-opus-5", "gpt-5.4", "gemini-3.5-flash", "grok-4.5", "some-unmapped-model",
    }


def test_provider_order_and_checkboxes_present_in_sidebar(tmp_path):
    rows = [_row(model="claude-opus-5"), _row(model="gpt-5.4", session_id="s2")]
    out = _build(tmp_path, rows)

    assert 'id="provider-panel"' in out
    assert 'class="provider-check"' in out
    assert "Anthropic" in out
    assert "OpenAI" in out
    provider_order = _extract_json_const(out, "PROVIDER_ORDER")
    assert set(provider_order) == {"Anthropic", "OpenAI"}


def test_provider_colors_embedded_and_fixed_per_name(tmp_path):
    out = _build(tmp_path, [_row(model="claude-opus-5"), _row(model="grok-4.5", session_id="s2")])
    provider_colors = _extract_json_const(out, "PROVIDER_COLORS")
    assert provider_colors == pc.PROVIDER_COLORS
    assert provider_colors["Anthropic"] == "#c96442"
    assert provider_colors[pc.OTHER_UNKNOWN_PROVIDER] == "#8c959f"


# ---------------------------------------------------------------------------
# Storage key: separate from project/model, default all-selected
# ---------------------------------------------------------------------------

def test_provider_storage_key_is_separate_from_project_and_model(tmp_path):
    out = _build(tmp_path, [_row()], storage_key="mydash.html")

    proj_key = _extract_json_const(out, "STORAGE_KEY_PROJECT")
    model_key = _extract_json_const(out, "STORAGE_KEY_MODEL")
    provider_key = _extract_json_const(out, "STORAGE_KEY_PROVIDER")

    assert proj_key == "copilot_usage_excluded_projects::mydash.html"
    assert model_key == "copilot_usage_excluded_models::mydash.html"
    assert provider_key == "copilot_usage_excluded_providers::mydash.html"
    assert len({proj_key, model_key, provider_key}) == 3


def test_default_excluded_providers_is_empty_all_selected_by_default(tmp_path):
    out = _build(tmp_path, [_row(model="claude-opus-5"), _row(model="gpt-5.4", session_id="s2")])
    default_excluded = _extract_json_const(out, "DEFAULT_EXCLUDED_PROVIDERS")
    assert default_excluded == []


# ---------------------------------------------------------------------------
# End-to-end DOM-harness: provider exclusion propagates through the single
# RAW.filter() choke point to every downstream KPI/chart/table.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_default_load_includes_every_provider(tmp_path):
    rows = [
        _row(model="claude-opus-5", total_tokens=1000, calls=1),
        _row(model="gpt-5.4", total_tokens=2000, calls=1, session_id="s2"),
        _row(model="gemini-3.5-flash", total_tokens=3000, calls=1, session_id="s3"),
    ]
    out = _build(tmp_path, rows)
    out_path = tmp_path / "out.html"
    payload = _run_harness(out_path)
    providers = {p for p, _ in payload["providerEntries"]}
    assert providers == {"Anthropic", "OpenAI", "Google"}
    assert payload["filteredCount"] == 3


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_excluding_a_provider_removes_its_rows_from_every_downstream_output(tmp_path):
    rows = [
        _row(model="claude-opus-5", total_tokens=1000, calls=1),
        _row(model="gpt-5.4", total_tokens=2000, calls=1, session_id="s2"),
    ]
    out = _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"

    seed = {"copilot_usage_excluded_providers::dash.html": json.dumps(["Anthropic"])}
    payload = _run_harness(out_path, seed)

    providers = {p for p, _ in payload["providerEntries"]}
    assert providers == {"OpenAI"}
    assert payload["filteredCount"] == 1
    # KPI row (a downstream consumer of the same `filtered` array) must
    # reflect the reduced total, not the full unfiltered 3000 tokens.
    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "2,000" in kpi_html
    assert "3,000" not in kpi_html


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_provider_exclusion_applies_even_if_its_models_checkbox_stays_selected(tmp_path):
    """Independent AND semantics: excluding a provider must remove its rows
    even though every individual model checkbox is still (default) checked -
    the model-level exclusion set is deliberately left empty here."""
    rows = [
        _row(model="claude-opus-5", total_tokens=1000, calls=1),
        _row(model="gpt-5.4", total_tokens=2000, calls=1, session_id="s2"),
    ]
    out = _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"

    seed = {
        "copilot_usage_excluded_providers::dash.html": json.dumps(["Anthropic"]),
        # Model-level exclusion set is explicitly EMPTY - both models' own
        # checkboxes remain checked; only the provider filter should exclude claude.
        "copilot_usage_excluded_models::dash.html": json.dumps([]),
    }
    payload = _run_harness(out_path, seed)
    assert payload["filteredCount"] == 1
    assert {p for p, _ in payload["providerEntries"]} == {"OpenAI"}


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_all_providers_excluded_yields_zero_state_without_error(tmp_path):
    rows = [_row(model="claude-opus-5"), _row(model="gpt-5.4", session_id="s2")]
    out = _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"

    seed = {"copilot_usage_excluded_providers::dash.html": json.dumps(["Anthropic", "OpenAI"])}
    payload = _run_harness(out_path, seed)

    assert payload["filteredCount"] == 0
    assert payload["providerEntries"] == []
    kpi_html = payload["elements"]["kpi-row"]["innerHTML"]
    assert "n/a" in kpi_html  # cost-coverage/date-range fall back to "n/a" with 0 calls
    assert "ERROR" not in json.dumps(payload)


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_unknown_provider_remains_visible_when_present(tmp_path):
    rows = [
        _row(model="claude-opus-5", total_tokens=1000, calls=1),
        _row(model="some-brand-new-model", total_tokens=500, calls=1, session_id="s2"),
    ]
    out = _build(tmp_path, rows)
    out_path = tmp_path / "out.html"
    payload = _run_harness(out_path)

    providers = {p for p, _ in payload["providerEntries"]}
    assert pc.OTHER_UNKNOWN_PROVIDER in providers
    assert "Other / Unknown" in out  # present in checkbox list / PROVIDER_ORDER


# ---------------------------------------------------------------------------
# Hostile model/provider output safety
# ---------------------------------------------------------------------------

def test_hostile_model_name_still_classifies_to_a_safe_fixed_provider_label(tmp_path):
    hostile_model = 'gpt-4o</script><script>alert(1)</script>'
    out = _build(tmp_path, [_row(model=hostile_model)])

    records = _extract_json_const(out, "RAW")
    assert records[0]["provider"] in pc.PROVIDER_CANONICAL_ORDER
    assert records[0]["provider"] == "OpenAI"  # still matches the leading "gpt" prefix rule
    # The hostile model string itself is neutralized by the existing
    # _json_for_script() hardening (proven in test_dashboard_security.py) -
    # the literal breakout sequence must never appear verbatim in the output.
    assert hostile_model not in out
    assert "</script><script>alert(1)</script>" not in out


def test_hostile_model_that_matches_no_prefix_falls_back_to_other_unknown_safely(tmp_path):
    hostile_model = "'; DROP TABLE models; --"
    out = _build(tmp_path, [_row(model=hostile_model)])
    records = _extract_json_const(out, "RAW")
    assert records[0]["provider"] == pc.OTHER_UNKNOWN_PROVIDER


# ---------------------------------------------------------------------------
# Current / legacy CSV compatibility (no CSV schema change required)
# ---------------------------------------------------------------------------

def test_provider_derived_correctly_for_legacy_csv_without_session_or_version_columns(tmp_path):
    """A legacy (pre-versioning) export has none of session_id,
    export_format_version, exported_at, reasoning_effort, task_summary - the
    provider classifier must still work from `model` alone."""
    csv_path = tmp_path / "legacy.csv"
    pd.DataFrame([
        {"user": "bob", "date": "2026-01-02", "project": "legacy/proj", "model": "gpt-4o-mini", "calls": 4, "total_tokens": 40},
        {"user": "bob", "date": "2026-01-03", "project": "legacy/proj", "model": "gemini-1.5-pro", "calls": 2, "total_tokens": 20},
    ]).to_csv(csv_path, index=False)

    data = dashboard.load_data(str(csv_path))
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(data, str(out_path), "Title", [], [], storage_key="legacy")
    out = out_path.read_text(encoding="utf-8")

    records = _extract_json_const(out, "RAW")
    by_model = {r["model"]: r["provider"] for r in records}
    assert by_model["gpt-4o-mini"] == "OpenAI"
    assert by_model["gemini-1.5-pro"] == "Google"


def test_provider_derived_correctly_for_current_full_schema_csv(tmp_path):
    csv_path = tmp_path / "current.csv"
    pd.DataFrame([{
        "user": "alice", "date": "2026-01-01", "project": "org/repo", "model": "claude-sonnet-5",
        "reasoning_effort": "medium", "calls": 3, "total_tokens": 150, "input_tokens": 100,
        "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
        "total_nano_aiu": 2e9, "session_id": "s1", "export_format_version": "3",
        "exported_at": "2026-01-10T00:00:00+00:00", "cost_data_calls": 3,
    }]).to_csv(csv_path, index=False)

    data = dashboard.load_data(str(csv_path))
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(data, str(out_path), "Title", [], [], storage_key="current")
    out = out_path.read_text(encoding="utf-8")

    records = _extract_json_const(out, "RAW")
    assert records[0]["provider"] == "Anthropic"
    assert records[0]["model"] == "claude-sonnet-5"  # raw model left unchanged


# ---------------------------------------------------------------------------
# Cost-metric zero aggregates: a provider whose value is 0 (e.g. legacy rows
# with no cost data) draws no pie slice/label, so it must stay visible via
# the legend, or via an explicit no-data state when everything is zero.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_provider_pie_keeps_legend_so_zero_valued_providers_stay_visible(tmp_path):
    """In Cost mode "Other / Unknown" here has zero cost, so Plotly draws no
    slice for it - the legend is the only place it can still be seen."""
    rows = [
        _row(model="gpt-5.4", total_tokens=1000, calls=1, total_nano_aiu=1e10),
        _row(model="some-brand-new-model", total_tokens=500, calls=1, session_id="s2", total_nano_aiu=0),
    ]
    _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"

    seed = {"copilot_usage_metric::dash.html": "cost"}
    payload = _run_harness(out_path, seed)

    entries = dict(payload["providerEntries"])
    assert entries[pc.OTHER_UNKNOWN_PROVIDER] == 0
    assert payload["providerAllZero"] is False
    fig = payload["figures"]["fig_provider"]
    assert fig["layout"]["showlegend"] is True
    assert pc.OTHER_UNKNOWN_PROVIDER in fig["data"][0]["labels"]


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_provider_pie_shows_explicit_no_cost_data_state_when_every_provider_is_zero(tmp_path):
    """Legacy exports carry no cost data at all, so in Cost mode every
    provider aggregates to 0 and there is no pie to draw - an explicit
    message naming the selected providers replaces it."""
    rows = [
        _row(model="gpt-5.4", total_tokens=1000, calls=1, total_nano_aiu=0),
        _row(model="some-brand-new-model", total_tokens=500, calls=1, session_id="s2", total_nano_aiu=0),
    ]
    _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"

    seed = {"copilot_usage_metric::dash.html": "cost"}
    payload = _run_harness(out_path, seed)

    assert payload["providerAllZero"] is True
    fig = payload["figures"]["fig_provider"]
    assert fig["data"] == []
    annotation_text = fig["layout"]["annotations"][0]["text"]
    assert "No estimated cost data" in annotation_text
    assert "OpenAI" in annotation_text
    assert pc.OTHER_UNKNOWN_PROVIDER in annotation_text
    assert "ERROR" not in json.dumps(payload)


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_provider_pie_renders_normally_in_token_mode_for_the_same_rows(tmp_path):
    """The no-data state is specific to an all-zero aggregate - the same rows
    in the default Tokens mode still draw a normal pie."""
    rows = [
        _row(model="gpt-5.4", total_tokens=1000, calls=1, total_nano_aiu=0),
        _row(model="some-brand-new-model", total_tokens=500, calls=1, session_id="s2", total_nano_aiu=0),
    ]
    _build(tmp_path, rows, storage_key="dash.html")
    out_path = tmp_path / "out.html"
    payload = _run_harness(out_path)

    assert payload["providerAllZero"] is False
    fig = payload["figures"]["fig_provider"]
    assert fig["data"][0]["values"] == [1000, 500]
    assert fig["layout"]["annotations"] == []
