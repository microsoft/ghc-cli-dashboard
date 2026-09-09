# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Overview and filter interaction regressions using the existing DOM harness."""
import json
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import dashboard


NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("dom_harness.js")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not available")


@pytest.fixture
def ui_dashboard(tmp_path):
    rows = [
        {
            "project": "Project %02d" % i, "model": "gpt-5.4" if i % 2 else "claude-opus-5",
            "date": "2026-09-09" if i % 2 else "2026-08-01",
            "user": "example", "calls": 2, "cost_data_calls": 2,
            "total_tokens": (i + 1) * 1000, "total_nano_aiu": (15 - i) * 1e10,
            "task_summary": "Build sample %d" % i,
        }
        for i in range(15)
    ]
    out = tmp_path / "ui.html"
    dashboard.build_dashboard(
        pd.DataFrame(rows), str(out), "Example usage", ["Project 00"], [], "ux"
    )
    return out


def run_ui(path, actions=(), seed=None):
    result = subprocess.run(
        [NODE, str(HARNESS), str(path), json.dumps(seed or {}), json.dumps(actions)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def action(name, *args):
    return {"name": name, "args": list(args)}


def test_three_primary_kpis_and_trend_before_project_ranking(ui_dashboard):
    out = run_ui(ui_dashboard)
    kpis = out["elements"]["kpi-row"]["innerHTML"]
    assert kpis.count('class="kpi"') == 3
    assert "Cost data coverage: 100%" in kpis
    assert "119K" in kpis
    assert "119,000" in kpis  # exact total is still accessible
    assert "14 projects" in out["elements"]["scope-summary"]["textContent"]
    html = ui_dashboard.read_text(encoding="utf-8")
    assert html.index('class="toolbar"') < html.index('id="kpi-row"')
    assert html.index('id="fig_trend"') < html.index('id="fig_project"')
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in html
    assert 'aria-controls="filter-panel"' in html
    assert 'Data through 2026-09-09' == out["elements"]["data-freshness"]["textContent"]


def test_filter_search_is_case_insensitive_and_does_not_filter_usage(ui_dashboard):
    baseline = run_ui(ui_dashboard)
    out = run_ui(ui_dashboard, [action("searchFilters", "project", "pROJECT 02")])
    visible = [c for c in out["checkboxes"] if c["kind"] == "proj-check" and not c["hidden"]]
    assert [c["value"] for c in visible] == ["Project 02"]
    assert out["filteredCount"] == baseline["filteredCount"]
    assert out["elements"]["project-more"]["hidden"]
    out = run_ui(ui_dashboard, [action("searchFilters", "project", "no such project")])
    assert not out["elements"]["project-empty"]["hidden"]
    assert out["filteredCount"] == baseline["filteredCount"]


def test_filter_show_more_and_search_reset_pagination(ui_dashboard):
    out = run_ui(ui_dashboard)
    assert sum(not c["hidden"] for c in out["checkboxes"] if c["kind"] == "proj-check") == 8
    out = run_ui(ui_dashboard, [action("showMoreFilters", "project")])
    assert sum(not c["hidden"] for c in out["checkboxes"] if c["kind"] == "proj-check") == 15
    assert out["elements"]["project-more"]["hidden"]
    out = run_ui(ui_dashboard, [action("showMoreFilters", "project"), action("searchFilters", "project", "")])
    assert sum(not c["hidden"] for c in out["checkboxes"] if c["kind"] == "proj-check") == 8


def test_select_only_preserves_other_dimensions(ui_dashboard):
    out = run_ui(ui_dashboard, [action("selectOnly", "project", 0)])
    selected = [c for c in out["checkboxes"] if c["kind"] == "proj-check" and c["checked"]]
    assert len(selected) == 1
    assert out["filteredCount"] == 1
    out = run_ui(ui_dashboard, [action("setAll", "provider", False), action("selectOnly", "project", 0)])
    assert out["filteredCount"] == 0
    assert "reset filters" in out["elements"]["scope-summary"]["textContent"]


def test_provider_exclusion_visibly_blocks_models_without_changing_model_selection(ui_dashboard):
    seed = {"copilot_usage_excluded_providers::ux": json.dumps(["Anthropic"])}
    out = run_ui(ui_dashboard, seed=seed)
    model = next(c for c in out["checkboxes"] if c["value"] == "claude-opus-5")
    assert model["checked"]
    assert model["disabled"] and model["providerBlocked"] and model["onlyDisabled"]
    assert "1 provider excluded" in out["elements"]["filter-summary"]["textContent"]
    out = run_ui(ui_dashboard, [action("setAll", "provider", True)], seed)
    model = next(c for c in out["checkboxes"] if c["value"] == "claude-opus-5")
    assert model["checked"]
    assert not model["disabled"] and not model["providerBlocked"]


def test_reset_includes_all_data_and_clears_search_but_keeps_metric(ui_dashboard):
    out = run_ui(ui_dashboard, [
        action("setMetric", "cost"), action("setDateFilter", "7"),
        action("setAll", "provider", False), action("searchFilters", "project", "absent"),
        action("resetFilters"),
    ])
    assert out["filteredCount"] == 15
    assert out["storage"]["copilot_usage_metric::ux"] == "cost"
    assert out["storage"]["copilot_usage_datefilter::ux"] == "all"
    assert out["elements"]["project-empty"]["hidden"]
    assert out["elements"]["filter-summary"]["textContent"] == "All projects, models and providers"
    assert all(c["checked"] and not c["disabled"] for c in out["checkboxes"])


def test_selected_control_states_and_cost_insight_follow_metric(ui_dashboard):
    out = run_ui(ui_dashboard, [
        action("setMetric", "cost"), action("setDateFilter", "7"),
        action("setTrendGranularity", "week"), action("toggleSidebar"),
    ])
    elements = out["elements"]
    assert elements["metric-cost"]["attributes"]["aria-pressed"] == "true"
    assert elements["metric-tokens"]["attributes"]["aria-pressed"] == "false"
    assert elements["date-7"]["attributes"]["aria-pressed"] == "true"
    assert elements["trend-week"]["attributes"]["aria-pressed"] == "true"
    assert elements["sidebar-toggle"]["attributes"]["aria-expanded"] == "false"
    assert elements["sidebar-toggle-label"]["textContent"] == "Show filters"
    assert "relative to latest export date" in elements["date-range-hint"]["textContent"]
    insight = elements["insight-overview"]["innerHTML"]
    assert "estimated cost ($)" in insight and "total tokens" not in insight
    assert out["figures"]["fig_model"]["data"][0]["type"] == "bar"


@pytest.fixture
def palette_dashboard(tmp_path):
    rows = [
        {
            "project": "Sample %d" % i, "model": "gpt-5.4" if i % 2 else "claude-opus-5",
            "date": "2026-09-09", "user": "example", "calls": 2, "cost_data_calls": 2,
            "total_tokens": (i + 1) * 1000, "total_nano_aiu": (i + 1) * 1e10,
            "input_tokens": (i + 1) * 600, "output_tokens": (i + 1) * 400,
            "cache_read_tokens": 50, "cache_write_tokens": 20, "reasoning_tokens": 10,
            "task_summary": summary,
            "reasoning_effort": ["high", "low", "medium", "none", "n/a", "unrecognised"][i],
        }
        for i, summary in enumerate([
            "Explore documentation", "Analyze results", "Plan launch",
            "Build component", "Review changes", "Unclassified activity",
        ])
    ]
    out = tmp_path / "palettes.html"
    dashboard.build_dashboard(pd.DataFrame(rows), str(out), "Palette examples", [], [], "ux")
    return out


@pytest.mark.parametrize("actions", [
    [],
    [action("setMetric", "cost")],
    [action("selectOnly", "project", 0)],
    [action("setAll", "project", False)],
])
def test_palettes_follow_data_type_and_remain_stable(palette_dashboard, actions):
    figures = run_ui(palette_dashboard, actions)["figures"]
    heatmap = figures["fig_theme_model"]["data"][0]
    assert heatmap["colorscale"] == "Viridis"
    assert heatmap["zmin"] == 0
    assert heatmap["zmax"] == (max([0] + [value for row in heatmap["z"] for value in row]) or 1)
    themes = {
        "Explore & learn": "#0072b2", "Analyze & decide": "#56b4e9",
        "Plan & organize": "#e69f00", "Build & implement": "#009e73",
        "Review & communicate": "#cc79a7", "Other": "#8c959f",
    }
    for trace in figures["fig_theme_time"]["data"]:
        assert trace["marker"]["color"] == themes[trace["name"]]
    modes = {
        "Exploration": "#0072b2", "Execution": "#009e73", "Planning": "#e69f00",
        "Review/support": "#cc79a7", "Other": "#8c959f",
    }
    mode_trace = figures["fig_work_mode"]["data"][0]
    for label, colour in zip(mode_trace["labels"], mode_trace["marker"]["colors"]):
        assert colour == modes[label]
    token_trace = figures["fig_token_composition"]["data"][0]
    assert token_trace["marker"]["color"] == ["#0072b2", "#009e73", "#56b4e9", "#e69f00", "#cc79a7"]
    efforts = {
        "none": "#440154", "low": "#31688e", "medium": "#35b779", "high": "#fde725",
        "n/a": "#8c959f", "unrecognised": "#8c959f",
    }
    effort_trace = figures["fig_effort"]["data"][0]
    for level, colour in zip(effort_trace["x"], effort_trace["marker"]["color"]):
        assert colour == efforts[level]
    assert effort_trace["x"] == [level for level in efforts if level in effort_trace["x"]]
    assert figures["fig_trend"]["data"][0]["line"]["color"] == "#0072b2"


@pytest.fixture
def browser_page():
    """Use the project's existing optional screenshot dependency, when available."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch()
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Playwright Chromium is not installed")
            raise
        page = browser.new_page()
        yield page
        browser.close()


@pytest.mark.parametrize("width,height", [(390, 844), (768, 1024), (1440, 1000)])
def test_responsive_layout_and_filter_interactions(ui_dashboard, browser_page, width, height):
    page = browser_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.set_viewport_size({"width": width, "height": height})
    page.goto(ui_dashboard.as_uri(), wait_until="networkidle")
    assert page.locator(".main").evaluate("(el) => el.parentElement.id") == "layout"
    assert page.locator("#kpi-row").evaluate("(el) => el.closest('main').id") == "dashboard-main"
    assert page.locator(".token-glossary .token-glossary-item").count() == 5
    assert page.locator(".token-glossary").evaluate("(el) => el.tagName") == "DETAILS"
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.locator("#kpi-row > .kpi").count() == 3
    if width == 1440:
        assert page.locator("#fig_trend").bounding_box()["y"] + 300 < height
    if width <= 760:
        assert not page.locator("#filter-panel").is_visible()
        page.get_by_role("button", name="Show filters", exact=True).click()
        assert page.locator("#filter-panel").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= width

    page.get_by_role("searchbox", name="Search projects", exact=True).fill("Project 02")
    assert page.locator("#proj-list .filter-item:visible").count() == 1
    page.get_by_role("button", name="Show only Project 02", exact=True).click()
    assert "1 projects" in page.locator("#scope-summary").inner_text()
    page.get_by_role("button", name="Reset filters", exact=True).click()
    assert page.get_by_role("searchbox", name="Search projects", exact=True).input_value() == ""
    assert "15 projects" in page.locator("#scope-summary").inner_text()

    page.locator('.provider-check[value="Anthropic"]').uncheck()
    model = page.locator('.model-check[value="claude-opus-5"]')
    assert model.is_checked() and model.is_disabled()
    assert model.locator("xpath=../..").inner_text().endswith("Excluded by provider")
    page.get_by_role("button", name="Hide filters", exact=True).click()
    assert "1 provider excluded" in page.locator("#filter-summary").inner_text()
    page.get_by_role("button", name="Reset filters", exact=True).click()
    assert not model.is_disabled()
    page.get_by_role("button", name="Last 7 days", exact=True).click()
    assert page.locator("#date-7").get_attribute("aria-pressed") == "true"
    assert "2026-09-03" in page.locator("#date-range-hint").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= width

    # Mobile-to-desktop changes should restore the saved collapsed preference.
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.wait_for_timeout(250)
    assert not page.locator("#filter-panel").is_visible()
    page.get_by_role("button", name="Show filters", exact=True).click()
    assert page.locator("#filter-panel").is_visible()
    assert not errors


def test_collapsed_sidebar_stays_available_without_exposing_filters(ui_dashboard, browser_page):
    page = browser_page
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(ui_dashboard.as_uri(), wait_until="networkidle")
    assert page.locator("#sidebar-toggle").evaluate("(el) => el.parentElement.id") == "sidebar-shell"
    expanded_width = page.locator("main").bounding_box()["width"]
    page.get_by_role("button", name="Hide filters", exact=True).click()
    page.wait_for_function("""() => {
        const chart = document.getElementById('fig_trend');
        return chart._fullLayout.width === chart.clientWidth;
    }""")
    assert not page.locator("#filter-panel").is_visible()
    assert page.locator("#sidebar-shell").bounding_box()["width"] == 44
    assert page.locator("main").bounding_box()["width"] > expanded_width
    page.reload(wait_until="networkidle")
    assert not page.locator("#filter-panel").is_visible()
    assert page.locator("#sidebar-toggle").get_attribute("aria-expanded") == "false"
    page.locator("#sec-detail").scroll_into_view_if_needed()
    button = page.get_by_role("button", name="Show filters", exact=True)
    assert 0 <= button.bounding_box()["y"] < 1000
    # Reset changes filters but must not reveal the project list during a demo.
    page.get_by_role("button", name="Reset filters", exact=True).click()
    assert not page.locator("#filter-panel").is_visible()
    button.click()
    assert page.locator("#filter-panel").is_visible()
    page.reload(wait_until="networkidle")
    assert page.locator("#filter-panel").is_visible()


@pytest.mark.parametrize("width", [390, 1440])
def test_model_mix_legend_has_space_and_toggles_models(ui_dashboard, browser_page, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(ui_dashboard.as_uri(), wait_until="networkidle")
    chart = page.locator("#fig_stack")
    chart.scroll_into_view_if_needed()
    legend_box = chart.locator(".legend").bounding_box()
    title_box = chart.locator(".xtitle").bounding_box()
    chart_box = chart.bounding_box()
    assert legend_box["y"] >= title_box["y"] + title_box["height"]
    assert legend_box["y"] + legend_box["height"] <= chart_box["y"] + chart_box["height"] + 1
    chart.locator(".legend .legendtoggle").first.click()
    page.wait_for_function("""() => document.getElementById('fig_stack').data
        .some(trace => trace.visible === 'legendonly')""")
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.set_viewport_size({"width": 1440 if width == 390 else 390, "height": 1000})
    page.wait_for_timeout(500)
    assert chart.evaluate("(el) => el.data.filter(t => t.visible === 'legendonly').length") == 1
    legend_box = chart.locator(".legend").bounding_box()
    title_box = chart.locator(".xtitle").bounding_box()
    assert legend_box["y"] >= title_box["y"] + title_box["height"]
    page.locator("#sidebar-toggle").click()
    page.wait_for_timeout(500)
    assert chart.evaluate("(el) => el.data.filter(t => t.visible === 'legendonly').length") == 1


def test_resizing_preserves_task_search_focus(ui_dashboard, browser_page):
    page = browser_page
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.goto(ui_dashboard.as_uri(), wait_until="networkidle")
    search = page.locator("#task-filter")
    search.fill("Build sample")
    page.set_viewport_size({"width": 390, "height": 1000})
    page.wait_for_timeout(500)
    assert search.evaluate("(el) => el === document.activeElement")
    assert search.input_value() == "Build sample"


@pytest.mark.parametrize("width", [390, 1440])
def test_resizing_preserves_pie_legend_and_zoom(ui_dashboard, browser_page, width):
    page = browser_page
    page.set_viewport_size({"width": width, "height": 1000})
    page.goto(ui_dashboard.as_uri(), wait_until="networkidle")
    chart = page.locator("#fig_provider")
    chart.scroll_into_view_if_needed()
    chart.locator(".legend .legendtoggle").first.click()
    page.wait_for_function("""() =>
        document.getElementById('fig_provider').layout.hiddenlabels?.length === 1""")
    hidden_labels = chart.evaluate("(el) => el.layout.hiddenlabels")
    zoom_range = ["2026-08-10", "2026-08-20"]
    page.evaluate("""range => Plotly.relayout('fig_trend', {
        'xaxis.range': range, 'xaxis.autorange': false
    })""", zoom_range)
    page.set_viewport_size({"width": 1440 if width == 390 else 390, "height": 1000})
    page.wait_for_timeout(500)
    assert chart.evaluate("(el) => el.layout.hiddenlabels") == hidden_labels
    assert page.locator("#fig_trend").evaluate("(el) => el.layout.xaxis.range") == zoom_range
    page.locator("#sidebar-toggle").click()
    page.wait_for_timeout(500)
    assert chart.evaluate("(el) => el.layout.hiddenlabels") == hidden_labels
    assert page.locator("#fig_trend").evaluate("(el) => el.layout.xaxis.range") == zoom_range
    page.get_by_role("button", name="Last 7 days", exact=True).click()
    assert not chart.evaluate("(el) => el.layout.hiddenlabels?.length")
    assert page.locator("#fig_trend").evaluate("(el) => el.layout.xaxis.autorange")
