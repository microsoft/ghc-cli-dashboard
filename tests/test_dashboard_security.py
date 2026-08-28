# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Security regression tests for dashboard.py's generated HTML/JS output.

These tests prove that data-derived strings (project names, model names,
task summaries, the --title flag) cannot become active markup/script or
spreadsheet formulas in the generated dashboard, while the underlying
values remain intact and available where the dashboard is supposed to
display them.

Run with: python -m pytest tests/ -v
(requires the dev-only dependency in requirements-dev.txt, and Node.js on
PATH for the DOM-harness tests; those are skipped automatically if `node`
isn't available.)
"""
import html as html_mod
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

# Hostile payloads exercised across the tests below.
SCRIPT_BREAKOUT = '</script><script>alert(1)</script>'
IMG_XSS = '<img src=x onerror=alert(1)>'
QUOTES_AND_AMP = 'Weird "Project" & <Name>'
LINE_SEPARATORS = 'Line1\u2028Line2\u2029End'
FORMULA_PROJECT = '=HYPERLINK("http://evil.example","click me")'
FORMULA_TASK = "+cmd|'/c calc'!A1\tTAB\nNEWLINE\rCR"

NODE = shutil.which("node")


def _build(tmp_path, rows, title="Copilot CLI Token Usage Dashboard",
           exclude_default_projects=None, exclude_default_models=None,
           storage_key="test") -> str:
    """Build a dashboard HTML string from a list of row dicts and return it."""
    data = pd.DataFrame(rows)
    out_path = tmp_path / "out.html"
    dashboard.build_dashboard(
        data, str(out_path), title,
        exclude_default_projects or [], exclude_default_models or [],
        storage_key=storage_key,
    )
    return out_path.read_text(encoding="utf-8")


def _base_rows():
    return [
        {
            "project": "Alpha", "model": "gpt-4o", "date": "2026-01-01", "user": "u1",
            "calls": 5, "total_tokens": 1000, "total_nano_aiu": 1e10,
            "session_id": "s1", "reasoning_effort": "medium", "task_summary": "Do a thing",
        },
    ]


# ---------------------------------------------------------------------------
# --title -> <title> / <h1>
# ---------------------------------------------------------------------------

def test_title_is_html_escaped(tmp_path):
    hostile_title = f"Dashboard {IMG_XSS} {SCRIPT_BREAKOUT}"
    out = _build(tmp_path, _base_rows(), title=hostile_title)

    assert IMG_XSS not in out
    assert SCRIPT_BREAKOUT not in out
    escaped = html_mod.escape(hostile_title)
    assert f"<title>{escaped}</title>" in out
    assert f"<h1>{escaped}</h1>" in out


# ---------------------------------------------------------------------------
# _checkbox_items() - project/model labels and value attributes
# ---------------------------------------------------------------------------

def test_checkbox_items_escape_label_and_value():
    order = [IMG_XSS, 'Proj"onmouseover=alert(1)']
    totals = {p: 42 for p in order}
    out = dashboard._checkbox_items(order, totals, "proj-check")

    assert IMG_XSS not in out
    assert '"onmouseover=alert(1)' not in out
    assert html_mod.escape(IMG_XSS) in out
    assert html_mod.escape('Proj"onmouseover=alert(1)', quote=True) in out
    # Semantic value must still be recoverable (round-trip through an HTML parser).
    from html.parser import HTMLParser

    class _Collector(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = []

        def handle_starttag(self, tag, attrs):
            if tag == "input":
                self.values.append(dict(attrs).get("value"))

    collector = _Collector()
    collector.feed(out)
    assert order == collector.values


# ---------------------------------------------------------------------------
# _json_for_script() - the RAW/PROJECT_ORDER/MODEL_ORDER embedding helper
# ---------------------------------------------------------------------------

def test_json_for_script_prevents_script_breakout_and_roundtrips():
    payload = {
        "a": SCRIPT_BREAKOUT,
        "b": IMG_XSS,
        "c": QUOTES_AND_AMP,
        "d": LINE_SEPARATORS,
    }
    out = dashboard._json_for_script(payload)

    assert "</script" not in out.lower()
    assert "<script" not in out.lower()
    assert "<" not in out
    assert ">" not in out
    assert "&" not in out
    assert "\u2028" not in out
    assert "\u2029" not in out
    # The escaped text must still be valid JSON that reproduces the exact values.
    assert json.loads(out) == payload


def test_raw_json_block_is_safe_and_preserves_values(tmp_path):
    rows = _base_rows()
    rows[0]["project"] = SCRIPT_BREAKOUT
    rows[0]["task_summary"] = IMG_XSS + LINE_SEPARATORS
    out = _build(tmp_path, rows)

    start = out.index("const RAW = ") + len("const RAW = ")
    end = out.index(";\n// nano_aiu", start)
    raw_text = out[start:end]

    assert "</script" not in raw_text.lower()
    assert "\u2028" not in raw_text
    assert "\u2029" not in raw_text

    records = json.loads(raw_text)
    assert records[0]["project"] == SCRIPT_BREAKOUT
    assert records[0]["task_summary"] == IMG_XSS + LINE_SEPARATORS

    # No literal script-breakout sequence should appear anywhere in the file.
    assert SCRIPT_BREAKOUT not in out


def test_project_and_model_order_json_blocks_are_safe(tmp_path):
    rows = [
        {**_base_rows()[0], "project": IMG_XSS, "model": SCRIPT_BREAKOUT},
    ]
    out = _build(tmp_path, rows)

    start = out.index("const PROJECT_ORDER = ") + len("const PROJECT_ORDER = ")
    end = out.index(";\nconst MODEL_ORDER", start)
    project_order_text = out[start:end]
    assert json.loads(project_order_text) == [IMG_XSS]
    assert "<" not in project_order_text

    start2 = out.index("const MODEL_ORDER = ") + len("const MODEL_ORDER = ")
    end2 = out.index(";\n", start2)
    model_order_text = out[start2:end2]
    assert json.loads(model_order_text) == [SCRIPT_BREAKOUT]
    assert "</script" not in model_order_text.lower()

    assert IMG_XSS not in out
    assert SCRIPT_BREAKOUT not in out


def test_storage_key_is_script_safe_and_roundtrips(tmp_path):
    hostile_key = 'C:\\reports\\x";alert(1);//</script>\u2028.html'
    out = _build(tmp_path, _base_rows(), storage_key=hostile_key)

    match = re.search(r"const STORAGE_KEY_PROJECT = (.*?);\n", out)
    assert match is not None
    serialized = match.group(1)
    assert "</script" not in serialized.lower()
    assert "\u2028" not in serialized
    assert json.loads(serialized) == f"copilot_usage_excluded_projects::{hostile_key}"
    assert '\\";alert(1);//' in serialized


# ---------------------------------------------------------------------------
# End-to-end DOM-harness tests (require Node.js) - insight innerHTML sinks
# and the TSV clipboard formula-injection guard.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_insights_and_table_escape_hostile_project_and_model_names(tmp_path):
    rows = [
        {
            "project": IMG_XSS, "model": SCRIPT_BREAKOUT, "date": "2026-01-01", "user": "u1",
            "calls": 5, "total_tokens": 100000, "total_nano_aiu": 1e11,
            "session_id": "s1", "reasoning_effort": "high", "task_summary": SCRIPT_BREAKOUT,
        },
        {
            "project": FORMULA_PROJECT, "model": "gpt-4o-mini", "date": "2026-01-02", "user": "u1",
            "calls": 5, "total_tokens": 1000, "total_nano_aiu": 5e10,
            "session_id": "s2", "reasoning_effort": "low", "task_summary": FORMULA_TASK,
        },
    ]
    _build(tmp_path, rows)
    out_path = tmp_path / "out.html"

    result = subprocess.run(
        [NODE, str(REPO_ROOT / "tests" / "dom_harness.js"), str(out_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr}"
    payload = json.loads(result.stdout)

    elements = payload["elements"]
    overview_html = elements.get("insight-overview", {}).get("innerHTML", "")
    assert IMG_XSS not in overview_html
    assert SCRIPT_BREAKOUT not in overview_html
    assert html_mod.escape(IMG_XSS) in overview_html
    assert html_mod.escape(SCRIPT_BREAKOUT) in overview_html

    value_html = elements.get("insight-value", {}).get("innerHTML", "")
    if value_html:
        assert SCRIPT_BREAKOUT not in value_html
        assert IMG_XSS not in value_html

    table_html = elements.get("table-wrap", {}).get("innerHTML", "")
    assert IMG_XSS not in table_html
    assert SCRIPT_BREAKOUT not in table_html

    # TSV clipboard text: formula-triggering prefixes must be neutralized and
    # embedded tab/newline/CR characters must not create extra TSV fields/rows.
    tsv = payload["tsv"]
    lines = tsv.split("\n")
    cells = [cell for line in lines[1:] for cell in line.split("\t")]  # skip header row
    for line in lines[1:]:
        assert line.count("\t") == 3, f"TSV row must have exactly 4 columns, got: {line!r}"
    assert any(cell.startswith("'=") for cell in cells), "leading '=' must be neutralized with a leading apostrophe"
    assert any(cell.startswith("'+cmd") for cell in cells), "leading '+' must be neutralized with a leading apostrophe"
    assert "NEWLINE" in tsv and "\nNEWLINE" not in tsv, "embedded newline in a cell must not start a new TSV row"


@pytest.mark.skipif(NODE is None, reason="Node.js not available on PATH")
def test_sanitize_and_escape_helper_probes(tmp_path):
    _build(tmp_path, _base_rows())
    out_path = tmp_path / "out.html"
    result = subprocess.run(
        [NODE, str(REPO_ROOT / "tests" / "dom_harness.js"), str(out_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"harness failed: {result.stderr}"
    payload = json.loads(result.stdout)
    probes = payload["probes"]

    sfs = probes["sanitizeForSpreadsheet"]
    assert sfs["=1+1"] == "'=1+1"
    assert sfs["+1"] == "'+1"
    assert sfs["-1"] == "'-1"
    assert sfs["@SUM(1)"] == "'@SUM(1)"
    assert sfs["plain"] == "plain"
    assert sfs["a\tb\nc\rd"] == "a b c d"
    # Non-formula hostile HTML payloads pass through unescaped here - this
    # function's only job is spreadsheet-formula neutralization, HTML escaping
    # for this same text happens separately wherever it is rendered as markup.
    assert sfs[IMG_XSS] == IMG_XSS

    esc = probes["escapeHtml"]
    assert esc[IMG_XSS] == html_mod.escape(IMG_XSS)
    assert esc[SCRIPT_BREAKOUT] == html_mod.escape(SCRIPT_BREAKOUT)
    assert esc['He said "hi" & \'bye\''] == html_mod.escape('He said "hi" & \'bye\'', quote=True).replace("&#x27;", "&#39;")


# ---------------------------------------------------------------------------
# Smoke test: dashboard generation succeeds end-to-end for a normal dataset.
# ---------------------------------------------------------------------------

def test_dashboard_smoke_generation(tmp_path):
    rows = [
        {
            "project": "demo-project", "model": "gpt-4o", "date": "2026-01-01", "user": "demo",
            "calls": 3, "total_tokens": 500, "total_nano_aiu": 2e10,
            "session_id": "sess-1", "reasoning_effort": "medium", "task_summary": "Investigate bug",
        },
    ]
    out = _build(tmp_path, rows)
    assert "<html>" in out
    assert "Copilot CLI Token Usage Dashboard" in out
    assert "demo-project" in out
