#!/usr/bin/env python3
"""
dashboard.py - Combine one or many copilot_usage_*.csv exports (produced by
extract_usage.py) into a single self-contained HTML dashboard.

This is intentionally a static-HTML output (Plotly, embedded JS) so it can be
emailed, posted to Teams/SharePoint, or opened by anyone with zero installs -
no server, no Streamlit, no Python needed to VIEW it (only to generate it).
Project AND model checkboxes let you exclude either from every chart; your
choices are remembered per-browser via localStorage. Charts show on-chart
value labels (compact, e.g. "154.9M") so you don't need to hover to read them.

Usage:
    python dashboard.py --in "copilot_usage_*.csv" --out usage_dashboard.html
    python dashboard.py --in "\\\\shared\\team-folder\\copilot_usage_*.csv"
    python dashboard.py --in "copilot_usage_*.csv" --exclude-default "Music,D:"
"""
import argparse
import glob
import json
import sys
from datetime import datetime

import pandas as pd
from plotly.offline import get_plotlyjs


def load_data(pattern: str) -> pd.DataFrame:
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"ERROR: no files matched pattern: {pattern}")
    frames = [pd.read_csv(f) for f in files]
    data = pd.concat(frames, ignore_index=True)

    # extract_usage.py exports a user's FULL history every run (not just new
    # rows), so overlapping exports (e.g. a weekly re-export) are common when
    # multiple copilot_usage_*.csv files are globbed together. Each row is
    # uniquely identified by (user, session_id, date, model, reasoning_effort)
    # per extract_usage.py's own GROUP BY - dedupe on that key, keeping the
    # last occurrence so the most recently-exported (most complete/accurate)
    # copy of a row wins. Files were sorted above, so for a given user, later
    # files (later export dates) are concatenated later and win ties.
    dedup_cols = [c for c in ("user", "session_id", "date", "model", "reasoning_effort") if c in data.columns]
    if dedup_cols:
        before = len(data)
        data = data.drop_duplicates(subset=dedup_cols, keep="last").reset_index(drop=True)
        removed = before - len(data)
        if removed:
            print(f"Note: removed {removed} duplicate row(s) found across overlapping exports (kept most recent).")

    return data


def _checkbox_items(order, totals, css_class):
    return "".join(
        f'<label class="proj-item"><input type="checkbox" class="{css_class}" value="{p.replace(chr(34), "&quot;")}" checked> '
        f'<span class="proj-name">{p}</span> <span class="proj-tok">{int(totals[p]):,}</span></label>'
        for p in order
    )


def build_dashboard(data: pd.DataFrame, out_path: str, title: str,
                     exclude_default_projects: list, exclude_default_models: list,
                     storage_key: str):
    has_tasks = "task_summary" in data.columns
    has_effort = "reasoning_effort" in data.columns

    records = []
    for r in data.itertuples():
        rec = {
            "project": r.project,
            "model": r.model,
            "date": r.date,
            "user": r.user,
            "calls": int(r.calls),
            "total_tokens": int(r.total_tokens),
            "total_nano_aiu": float(getattr(r, "total_nano_aiu", 0) or 0),
            "session_id": getattr(r, "session_id", None),
            "reasoning_effort": getattr(r, "reasoning_effort", "n/a") or "n/a" if has_effort else "n/a",
        }
        if has_tasks:
            rec["task_summary"] = getattr(r, "task_summary", "") or ""
        records.append(rec)

    projects_by_tokens = data.groupby("project")["total_tokens"].sum().sort_values(ascending=False)
    project_order = projects_by_tokens.index.tolist()

    models_by_tokens = data.groupby("model")["total_tokens"].sum().sort_values(ascending=False)
    model_order = models_by_tokens.index.tolist()

    n_users = data["user"].nunique()

    raw_json = json.dumps(records, ensure_ascii=False)
    project_order_json = json.dumps(project_order, ensure_ascii=False)
    model_order_json = json.dumps(model_order, ensure_ascii=False)
    exclude_default_projects_json = json.dumps(exclude_default_projects, ensure_ascii=False)
    exclude_default_models_json = json.dumps(exclude_default_models, ensure_ascii=False)

    project_checkbox_items = _checkbox_items(project_order, projects_by_tokens, "proj-check")
    model_checkbox_items = _checkbox_items(model_order, models_by_tokens, "model-check")

    plotly_js = get_plotlyjs()

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<script>{plotly_js}</script>
<style>
  :root {{
    --accent: #2f6feb; --accent-dark: #1a4fc4; --good: #1a7f37; --warn: #9a6700;
    --bg: #f0f3f8; --card-bg: #ffffff; --border: #d8dee7; --text: #1b1f24; --muted: #59636e;
    --shadow: 0 1px 2px rgba(20,30,50,0.04), 0 4px 12px rgba(20,30,50,0.05);
    --shadow-hover: 0 2px 4px rgba(20,30,50,0.06), 0 8px 24px rgba(20,30,50,0.09);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI Variable", "Segoe UI", -apple-system, Roboto, sans-serif;
    margin: 0; padding: 0; background: var(--bg); color: var(--text); line-height: 1.4;
  }}
  .hero {{
    background: linear-gradient(135deg, #14294f 0%, #1f3d78 55%, #2f6feb 100%);
    color: white; padding: 30px 40px 22px; box-shadow: var(--shadow);
  }}
  .hero h1 {{ margin: 0 0 4px; font-size: 26px; font-weight: 650; letter-spacing: -0.01em; }}
  .hero .subtitle {{ color: #cfe0ff; margin: 0; font-size: 13px; }}
  .nav-pills {{
    display: flex; gap: 6px; flex-wrap: wrap; margin-top: 16px;
  }}
  .nav-pills a {{
    color: #e3ecff; text-decoration: none; font-size: 12.5px; font-weight: 600;
    padding: 6px 12px; border-radius: 999px; background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.22); transition: background 0.15s;
  }}
  .nav-pills a:hover {{ background: rgba(255,255,255,0.26); }}
  .page {{ padding: 24px 40px 60px; }}
  h2 {{ font-size: 16px; margin: 0 0 2px; }}
  .section {{ margin-bottom: 8px; scroll-margin-top: 14px; }}
  .section-head {{ display: flex; align-items: baseline; gap: 10px; margin: 40px 0 14px; }}
  .section-head .num {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 7px; background: var(--accent); color: white;
    font-size: 13px; font-weight: 700; flex: 0 0 auto;
  }}
  .section-head h2 {{ font-size: 19px; font-weight: 650; }}
  .section-desc {{ color: var(--muted); font-size: 13px; margin: 0 0 16px 36px; max-width: 780px; }}
  .layout {{ display: flex; gap: 24px; align-items: flex-start; }}
  .sidebar {{ flex: 0 0 270px; display: flex; flex-direction: column; gap: 14px; position: sticky; top: 14px; max-height: 92vh; transition: flex-basis 0.15s, width 0.15s; }}
  .layout.sidebar-collapsed .sidebar {{ flex: 0 0 auto; }}
  .layout.sidebar-collapsed .side-panel,
  .layout.sidebar-collapsed .sidebar .hint {{ display: none; }}
  .side-panel {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px; overflow-y: auto; box-shadow: var(--shadow); }}
  #proj-panel {{ max-height: 46vh; }}
  #model-panel {{ max-height: 30vh; }}
  .side-panel h2 {{ margin-top: 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
  .sidebar-toggle {{
    display: flex; align-items: center; justify-content: center; gap: 6px; width: 100%;
    padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg);
    cursor: pointer; font-size: 12.5px; font-weight: 650; color: var(--muted); box-shadow: var(--shadow);
  }}
  .sidebar-toggle:hover {{ background: #f6f8fb; color: var(--text); }}
  .layout.sidebar-collapsed .sidebar-toggle {{ width: 40px; padding: 8px; }}
  .layout.sidebar-collapsed .sidebar-toggle .toggle-label {{ display: none; }}
  .main {{ flex: 1; min-width: 0; }}
  .kpi-row {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }}
  .kpi {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 20px; min-width: 132px; box-shadow: var(--shadow); transition: box-shadow 0.15s, transform 0.15s;
  }}
  .kpi:hover {{ box-shadow: var(--shadow-hover); transform: translateY(-1px); }}
  .kpi-value {{ font-size: 21px; font-weight: 700; letter-spacing: -0.01em; }}
  .kpi-label {{ font-size: 11.5px; color: var(--muted); margin-top: 3px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    margin-bottom: 0; box-shadow: var(--shadow); transition: box-shadow 0.15s;
  }}
  .card:hover {{ box-shadow: var(--shadow-hover); }}
  table {{ border-collapse: collapse; width: 100%; background: var(--card-bg); font-size: 13px; }}
  th, td {{ border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; }}
  th {{ background: #f6f8fb; font-weight: 650; color: var(--muted); text-transform: uppercase; font-size: 11px; letter-spacing: 0.03em; }}
  tr:hover td {{ background: #f6f9ff; }}
  .table-toolbar {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }}
  .table-filter {{ flex: 1 1 280px; display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 650; color: var(--muted); }}
  .table-filter input {{ flex: 1; min-width: 160px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; font: inherit; color: var(--text); background: white; }}
  .table-action {{ padding: 7px 12px; border: 1px solid var(--border); border-radius: 6px; background: #f6f8fb; cursor: pointer; font-size: 12px; font-weight: 650; }}
  .table-action:hover {{ background: #e9eef7; }}
  .table-status {{ font-size: 12px; color: var(--muted); }}
  .table-sort {{ border: 0; padding: 0; background: transparent; color: inherit; font: inherit; font-weight: inherit; text-transform: inherit; letter-spacing: inherit; cursor: pointer; }}
  .table-sort:hover {{ color: var(--accent-dark); }}
  .full {{ grid-column: 1 / -1; }}
  .proj-buttons {{ display: flex; gap: 8px; margin-bottom: 10px; }}
  .proj-buttons button {{ flex: 1; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: #f6f8fb; cursor: pointer; font-size: 12px; font-weight: 600; }}
  .proj-buttons button:hover {{ background: #e9eef7; }}
  .proj-item {{ display: flex; align-items: center; gap: 6px; padding: 4px 2px; font-size: 13px; cursor: pointer; border-radius: 4px; }}
  .proj-item:hover {{ background: #f6f8fb; }}
  .proj-name {{ flex: 1; overflow-wrap: anywhere; }}
  .proj-tok {{ color: var(--muted); font-size: 11px; }}
  .hint {{ font-size: 11.5px; color: var(--muted); margin-top: 10px; }}
  .toolbar {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 18px; margin-bottom: 28px; box-shadow: var(--shadow);
  }}
  .metric-toggle {{ display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
  .metric-label {{ font-size: 13px; font-weight: 650; cursor: help; }}
  .metric-toggle button, .date-toggle button {{
    padding: 6px 14px; border: 1px solid var(--border); border-radius: 999px; background: #f6f8fb;
    cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.12s;
  }}
  .metric-toggle button:hover, .date-toggle button:hover {{ background: #e9eef7; }}
  .metric-toggle button.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .date-toggle {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .date-toggle button.active {{ background: var(--good); color: white; border-color: var(--good); }}
  .date-range-hint {{ font-size: 12px; color: var(--muted); font-weight: 600; }}
  .trend-toggle {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 12px 36px; }}
  .trend-toggle button {{ padding: 6px 14px; border: 1px solid var(--border); border-radius: 999px; background: #f6f8fb; cursor: pointer; font-size: 13px; font-weight: 600; }}
  .trend-toggle button:hover {{ background: #e9eef7; }}
  .trend-toggle button.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .kpi {{ cursor: help; }}
  .card {{ position: relative; }}
  .info-icon {{
    position: absolute; top: 10px; right: 12px; width: 20px; height: 20px; border-radius: 50%;
    background: #eef1f6; color: var(--muted); font-size: 12px; font-weight: 700; line-height: 20px;
    text-align: center; cursor: help; z-index: 5; user-select: none;
  }}
  .info-icon:hover {{ background: #d8dee7; color: var(--text); }}
  .insight-bar {{
    display: flex; flex-direction: column; gap: 8px; margin: 0 0 18px 36px; max-width: 780px;
  }}
  .insight {{
    display: flex; gap: 10px; align-items: flex-start; font-size: 13px; background: #eef4ff;
    border-left: 3px solid var(--accent); border-radius: 6px; padding: 9px 12px; color: #1b3a7a;
  }}
  .insight.warn {{ background: #fff8e6; border-left-color: var(--warn); color: #6b4d00; }}
  .insight.good {{ background: #edf9f0; border-left-color: var(--good); color: #14532d; }}
  .insight b {{ font-weight: 700; }}
  .footer-note {{ margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--border); font-size: 12px; color: var(--muted); }}
</style>
</head>
<body>
  <div class="hero">
    <h1>{title}</h1>
    <p class="subtitle">Generated {datetime.now():%Y-%m-%d %H:%M} &middot; source: Copilot CLI session-store.db exports &middot; uncheck a project or model in the sidebar to exclude it from every chart</p>
    <div class="nav-pills">
      <a href="#sec-overview">Overview</a>
      <a href="#sec-trends">Trends</a>
      <a href="#sec-value">Cost &amp; Value</a>
      <a href="#sec-patterns">Work Patterns</a>
      <a href="#sec-composition">Composition</a>
      <a href="#sec-detail">Task Detail</a>
    </div>
  </div>
  <div class="page">
  <div class="layout" id="layout">
    <div class="sidebar">
      <button class="sidebar-toggle" id="sidebar-toggle" onclick="toggleSidebar()" title="Show/hide the Projects and Models filter panel">
        <span id="sidebar-toggle-icon">&laquo;</span><span class="toggle-label">Hide filters</span>
      </button>
      <div class="side-panel" id="proj-panel">
        <h2>Projects</h2>
        <div class="proj-buttons">
          <button onclick="setAll('project', true)">Select all</button>
          <button onclick="setAll('project', false)">Select none</button>
        </div>
        <div id="proj-list">{project_checkbox_items}</div>
      </div>
      <div class="side-panel" id="model-panel">
        <h2>Models</h2>
        <div class="proj-buttons">
          <button onclick="setAll('model', true)">Select all</button>
          <button onclick="setAll('model', false)">Select none</button>
        </div>
        <div id="model-list">{model_checkbox_items}</div>
      </div>
      <div class="hint">Your selections are remembered in this browser for this dashboard file.</div>
    </div>
    <div class="main">

      <div class="section" id="sec-overview">
        <div class="section-head"><span class="num">1</span><h2>Overview</h2></div>
        <p class="section-desc">The headline numbers for whatever you've currently selected (projects, models, date range) &mdash; use this as your at-a-glance sanity check before reading the detail charts below.</p>
        <div class="kpi-row" id="kpi-row"></div>
        <div id="insight-overview" class="insight-bar"></div>

        <div class="toolbar">
          <div class="date-toggle">
            <span class="metric-label" title="Quick filters compute 'last N days' relative to the most recent date present in your data export (not necessarily today's real-world date). Choose 'All time' to see every row in the loaded CSV(s).">Date range:</span>
            <button id="date-7" onclick="setDateFilter('7')">Last 7 days</button>
            <button id="date-14" onclick="setDateFilter('14')">Last 14 days</button>
            <button id="date-30" onclick="setDateFilter('30')">Last 30 days</button>
            <button id="date-90" onclick="setDateFilter('90')">Last 90 days</button>
            <button id="date-all" onclick="setDateFilter('all')">All time</button>
            <span class="date-range-hint" id="date-range-hint"></span>
          </div>
          <div class="metric-toggle" style="margin-bottom:0;">
            <span class="metric-label" title="Switches every chart's value axis between raw token counts and estimated dollar cost. Does not change which projects/models/dates are included.">Chart metric:</span>
            <button id="metric-tokens" onclick="setMetric('tokens')">Tokens</button>
            <button id="metric-cost" onclick="setMetric('cost')">Estimated cost ($)</button>
            <span class="hint" style="margin:0 0 0 10px;">Cost = published GitHub Copilot per-token list prices &middot; estimate only, may not match your actual invoice (plan allowances/discounts not reflected)</span>
          </div>
        </div>

        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Sums the current metric (tokens or estimated cost) per project across every selected model and the chosen date range, then shows the top 15 projects ranked highest to lowest. Bar length = total value for that project; hover a bar for the exact figure.">?</span>
            <div id="fig_project" style="height:430px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Each slice is one model's share of the current metric (tokens or cost), summed across your currently selected projects, models, and date range. Slice size + label percentage always add up to 100% of what's selected.">?</span>
            <div id="fig_model" style="height:400px;"></div>
          </div>
          {"<div class='card'><span class='info-icon' title=\"Sums the current metric per user account across selected projects/models/dates. Useful when this export covers more than one person.\">?</span><div id='fig_user' style='height:400px;'></div></div>" if n_users > 1 else "<div class='card'><span class='info-icon' title=\"Sums the current metric by reasoning-effort level (none/low/medium/high). Higher reasoning effort makes a model think more internally before responding, which increases both token usage and cost.\">?</span><div id='fig_effort' style='height:400px;'></div></div>"}
        </div>
      </div>

      <div class="section" id="sec-trends">
        <div class="section-head"><span class="num">2</span><h2>Trends over time</h2></div>
        <p class="section-desc">How usage moves over time &mdash; look here for spikes tied to specific work pushes, or a steady baseline that suggests routine usage.</p>
        <div class="trend-toggle">
          <span class="metric-label" title="Choose the time bucket used to aggregate the trend chart. Week uses Monday-starting weeks; month uses calendar months.">Group by:</span>
          <button id="trend-day" onclick="setTrendGranularity('day')">Day</button>
          <button id="trend-week" onclick="setTrendGranularity('week')">Week</button>
          <button id="trend-month" onclick="setTrendGranularity('month')">Month</button>
        </div>
        <div id="insight-trends" class="insight-bar"></div>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="One point per selected day, Monday-starting week, or calendar month: the sum of the current metric across all selected projects/models within that period. Respects the date-range filter above.">?</span>
            <div id="fig_trend" style="height:400px;"></div>
          </div>
        </div>
      </div>

      <div class="section" id="sec-value">
        <div class="section-head"><span class="num">3</span><h2>Cost &amp; value</h2></div>
        <p class="section-desc">Which models deliver the most tokens per dollar spent, and how reasoning effort (a setting, not a model choice) drives cost up. Value here means <b>pricing efficiency</b>, not output quality &mdash; see each chart's <span title="hover the ? icons on the charts below for the full caveat">(?)</span> for details.</p>
        <div id="insight-value" class="insight-bar"></div>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Tokens received per US dollar of estimated list-price cost (total tokens \u00f7 estimated cost), summed across your currently selected projects/models/dates. Higher bars = more tokens for the same spend. This is a raw PRICING-EFFICIENCY ratio only - it does not measure output quality, accuracy, or how many tokens a model actually needs to complete a task well, and models used mostly at high reasoning-effort will look worse here even if their answers are better, since reasoning tokens add cost. Number in parentheses = call count, for a sense of how much data backs each bar.">?</span>
            <div id="fig_value" style="height:420px;"></div>
          </div>
          {"<div class='card full'><span class='info-icon' title=\"Sums the current metric by reasoning-effort level (none/low/medium/high) across selected projects/models/dates. Higher reasoning effort makes a model 'think' more internally before responding, which increases both token usage and cost.\">?</span><div id='fig_effort' style='height:380px;'></div></div>" if n_users > 1 else ""}
        </div>
      </div>

      <div class="section" id="sec-patterns">
        <div class="section-head"><span class="num">4</span><h2>Work patterns</h2></div>
        <p class="section-desc">A transparent, rule-based view of the kinds of work represented in task summaries &mdash; useful for spotting shifts between exploration, planning, execution, and review. These are inferred themes, not measures of productivity or individual performance.</p>
        <div id="work-patterns-note" class="hint" style="margin:0 0 16px 36px;"></div>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Groups task summaries into inferred themes using transparent keyword rules, then sums the current metric by theme over the selected day/week/month buckets. Tasks that do not match a rule remain Other.">?</span>
            <div id="fig_theme_time" style="height:420px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Shows the current metric for inferred task themes split by model. This can indicate which models are being used for exploration, analysis, planning, execution, or review work.">?</span>
            <div id="fig_theme_model" style="height:420px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Shows the balance of inferred work modes. Exploration includes learning and analysis; execution covers building and implementation; planning and review/support are shown separately.">?</span>
            <div id="fig_work_mode" style="height:420px;"></div>
          </div>
        </div>
      </div>

      <div class="section" id="sec-composition">
        <div class="section-head"><span class="num">5</span><h2>Composition</h2></div>
        <p class="section-desc">How your top projects break down by model &mdash; useful for spotting projects that lean heavily on one (possibly expensive) model.</p>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="The same top-15 projects as 'Top Projects' above, but each bar is split (stacked) by model, with colour = model. Segment height shows how much of that project's usage came from each model, so you can see model mix per project at a glance.">?</span>
            <div id="fig_stack" style="height:440px;"></div>
          </div>
        </div>
      </div>

      <div class="section" id="sec-detail">
        <div class="section-head"><span class="num">6</span><h2>Task detail</h2></div>
        <p class="section-desc">The individual tasks driving the totals above, ranked by the current chart metric. Search across all tasks, sort any column, or copy the displayed top 20 rows.</p>
        <div id="table-wrap"></div>
      </div>

      <div class="footer-note">Copilot CLI Token Usage Dashboard &middot; generated locally from session-store.db exports &middot; all figures are estimates based on published list pricing.</div>
    </div>
  </div>
  </div>

<script>
const RAW = {raw_json};
// nano_aiu -> USD: verified against GitHub's published per-token Copilot pricing
// (1 AI credit = $0.01; total_nano_aiu / 1e9 = credits, so /1e11 = USD).
const NANO_AIU_TO_USD = 1e-11;
RAW.forEach(r => {{ r.estimated_cost = (r.total_nano_aiu || 0) * NANO_AIU_TO_USD; }});

const PROJECT_ORDER = {project_order_json};
const MODEL_ORDER = {model_order_json};
// Shared qualitative palette so a model's colour is consistent across the pie, stacked-bar,
// and value-for-money charts - critical for scanning multiple charts without re-reading legends.
const MODEL_PALETTE = ["#2f6feb", "#f0883e", "#3fb950", "#a371f7", "#db6d28", "#79c0ff", "#f778ba", "#56d364", "#bf8700", "#8250df", "#ff7b72", "#39c5cf"];
const MODEL_COLORS = {{}};
MODEL_ORDER.forEach((m, i) => {{ MODEL_COLORS[m] = MODEL_PALETTE[i % MODEL_PALETTE.length]; }});
const HAS_TASKS = {str(has_tasks).lower()};
const STORAGE_KEY_PROJECT = "copilot_usage_excluded_projects::{storage_key}";
const STORAGE_KEY_MODEL = "copilot_usage_excluded_models::{storage_key}";
const STORAGE_KEY_METRIC = "copilot_usage_metric::{storage_key}";
const STORAGE_KEY_DATEFILTER = "copilot_usage_datefilter::{storage_key}";
const STORAGE_KEY_TREND = "copilot_usage_trend_granularity::{storage_key}";
const STORAGE_KEY_SIDEBAR = "copilot_usage_sidebar_collapsed::{storage_key}";
const DEFAULT_EXCLUDED_PROJECTS = {exclude_default_projects_json};
const DEFAULT_EXCLUDED_MODELS = {exclude_default_models_json};

// "Last N days" is computed relative to the most recent date in the loaded export(s),
// not the real-world today - the CSV may have been generated some time ago.
const ALL_DATES_SORTED = RAW.map(r => r.date).filter(Boolean).sort();
const GLOBAL_MAX_DATE = ALL_DATES_SORTED.length ? ALL_DATES_SORTED[ALL_DATES_SORTED.length - 1] : null;

function loadExcluded(kind) {{
  const key = kind === "project" ? STORAGE_KEY_PROJECT : STORAGE_KEY_MODEL;
  const dflt = kind === "project" ? DEFAULT_EXCLUDED_PROJECTS : DEFAULT_EXCLUDED_MODELS;
  const stored = localStorage.getItem(key);
  if (stored !== null) {{
    try {{ return new Set(JSON.parse(stored)); }} catch (e) {{ /* fall through */ }}
  }}
  return new Set(dflt);
}}

function saveExcluded(kind, excluded) {{
  const key = kind === "project" ? STORAGE_KEY_PROJECT : STORAGE_KEY_MODEL;
  localStorage.setItem(key, JSON.stringify(Array.from(excluded)));
}}

function sum(arr, key) {{ return arr.reduce((a, r) => a + (r[key] || 0), 0); }}

function groupSum(arr, keyFn, valKey) {{
  const m = new Map();
  for (const r of arr) {{
    const k = keyFn(r);
    m.set(k, (m.get(k) || 0) + (r[valKey] || 0));
  }}
  return m;
}}

function fmt(n) {{ return Math.round(n).toLocaleString(); }}

// Compact on-chart labels, e.g. 154878110 -> "154.9M", 36057 -> "36.1K"
function fmtCompact(n) {{
  const sign = n < 0 ? "-" : "";
  n = Math.abs(n);
  if (n >= 1e9) return sign + (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return sign + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return sign + (n / 1e3).toFixed(1) + "K";
  return sign + Math.round(n).toString();
}}

function fmtCurrency(n) {{
  const sign = n < 0 ? "-" : "";
  n = Math.abs(n);
  if (n >= 1000) return sign + "$" + fmtCompact(n);
  return sign + "$" + n.toFixed(2);
}}

function getMetric() {{ return localStorage.getItem(STORAGE_KEY_METRIC) || "tokens"; }}

function setMetric(metric) {{
  localStorage.setItem(STORAGE_KEY_METRIC, metric);
  render();
}}

function getDateFilter() {{ return localStorage.getItem(STORAGE_KEY_DATEFILTER) || "all"; }}

function setDateFilter(v) {{
  localStorage.setItem(STORAGE_KEY_DATEFILTER, v);
  render();
}}

function getTrendGranularity() {{ return localStorage.getItem(STORAGE_KEY_TREND) || "day"; }}

function setTrendGranularity(v) {{
  localStorage.setItem(STORAGE_KEY_TREND, v);
  render();
}}

function updateTrendGranularityButtons(granularity) {{
  ["day", "week", "month"].forEach(value => {{
    const button = document.getElementById("trend-" + value);
    if (button) button.classList.toggle("active", value === granularity);
  }});
}}

function trendBucket(date, granularity) {{
  if (granularity === "month") return date.slice(0, 7);
  if (granularity === "week") {{
    const d = new Date(date + "T00:00:00Z");
    const mondayOffset = (d.getUTCDay() + 6) % 7;
    d.setUTCDate(d.getUTCDate() - mondayOffset);
    return d.toISOString().slice(0, 10);
  }}
  return date;
}}

let taskTableRows = [];
let taskSearchQuery = "";
let taskSortKey = null;
let taskSortAscending = false;

function escapeHtml(value) {{
  return String(value ?? "").replace(/[&<>"']/g, ch =>
    ch === "&" ? "&amp;" :
    ch === "<" ? "&lt;" :
    ch === ">" ? "&gt;" :
    ch === '"' ? "&quot;" : "&#39;"
  );
}}

function taskSortValue(row, key) {{
  return key === "project" ? row.project :
    key === "task" ? row.task :
    key === "cost" ? row.cost : row.tokens;
}}

function taskSortIndicator(key) {{
  return taskSortKey === key ? (taskSortAscending ? " ▲" : " ▼") : "";
}}

function getVisibleTaskRows() {{
  const query = taskSearchQuery.trim().toLowerCase();
  const rows = taskTableRows.filter(row =>
    !query || `${{row.project}} ${{row.task}}`.toLowerCase().includes(query)
  );
  const key = taskSortKey || "tokens";
  rows.sort((a, b) => {{
    const av = taskSortValue(a, key);
    const bv = taskSortValue(b, key);
    const result = typeof av === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), undefined, {{ sensitivity: "base" }});
    return taskSortAscending ? result : -result;
  }});
  return rows;
}}

function getDisplayedTaskRows() {{
  return getVisibleTaskRows().slice(0, 20);
}}

function renderTaskTable() {{
  const wrap = document.getElementById("table-wrap");
  if (!HAS_TASKS || !taskTableRows.length) {{
    wrap.innerHTML = "";
    return;
  }}
  const matchingRows = getVisibleTaskRows();
  const rows = matchingRows.slice(0, 20);
  const htmlRows = rows.map(row => `
    <tr>
      <td>${{escapeHtml(row.project)}}</td>
      <td>${{escapeHtml(row.task)}}</td>
      <td style="text-align:right">${{fmt(row.tokens)}}</td>
      <td style="text-align:right">${{fmtCurrency(row.cost)}}</td>
    </tr>
  `).join("");
  const countText = taskSearchQuery.trim()
    ? `Showing ${{rows.length}} of ${{matchingRows.length}} matching tasks`
    : `Showing top ${{rows.length}} of ${{taskTableRows.length}} tasks`;
  wrap.innerHTML = `
    <div class="card full" style="overflow-x:auto;">
      <div class="table-toolbar">
        <label class="table-filter">Filter tasks
          <input id="task-filter" type="search" placeholder="Search project or task..." value="${{escapeHtml(taskSearchQuery)}}" oninput="setTaskSearch(this.value)">
        </label>
        <button class="table-action" onclick="copyTaskTable()">Copy table</button>
        <span class="table-status" id="task-copy-status">${{countText}}</span>
      </div>
      <table id="task-table">
        <thead><tr>
          <th><button class="table-sort" onclick="sortTaskTable('project')">Project${{taskSortIndicator("project")}}</button></th>
          <th><button class="table-sort" onclick="sortTaskTable('task')">Task${{taskSortIndicator("task")}}</button></th>
          <th style="text-align:right"><button class="table-sort" onclick="sortTaskTable('tokens')">Total tokens${{taskSortIndicator("tokens")}}</button></th>
          <th style="text-align:right"><button class="table-sort" onclick="sortTaskTable('cost')">Est. cost${{taskSortIndicator("cost")}}</button></th>
        </tr></thead>
        <tbody>${{htmlRows || '<tr><td colspan="4" style="text-align:center;color:var(--muted);">No matching tasks</td></tr>'}}</tbody>
      </table>
    </div>
  `;
}}

function setTaskSearch(value) {{
  taskSearchQuery = value;
  renderTaskTable();
  const input = document.getElementById("task-filter");
  if (input) {{
    input.focus();
    input.setSelectionRange(value.length, value.length);
  }}
}}

function sortTaskTable(key) {{
  if (taskSortKey === key) taskSortAscending = !taskSortAscending;
  else {{
    taskSortKey = key;
    taskSortAscending = key === "project" || key === "task";
  }}
  renderTaskTable();
}}

function taskTableText() {{
  return [
    ["Project", "Task", "Total tokens", "Est. cost"],
    ...getDisplayedTaskRows().map(row => [row.project, row.task, fmt(row.tokens), fmtCurrency(row.cost)])
  ].map(row => row.join("\\t")).join("\\n");
}}

function showTaskCopyStatus(message) {{
  const status = document.getElementById("task-copy-status");
  if (status) status.textContent = message;
}}

function fallbackCopyTaskTable(text) {{
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  let copied = false;
  try {{ copied = document.execCommand("copy"); }}
  finally {{ area.remove(); }}
  showTaskCopyStatus(copied ? "Copied to clipboard" : "Copy failed; select the table manually.");
}}

function copyTaskTable() {{
  const text = taskTableText();
  if (navigator.clipboard && navigator.clipboard.writeText) {{
    navigator.clipboard.writeText(text).then(
      () => showTaskCopyStatus("Copied to clipboard"),
      () => fallbackCopyTaskTable(text)
    );
  }} else {{
    fallbackCopyTaskTable(text);
  }}
}}

const TASK_THEME_ORDER = [
  "Explore & learn",
  "Analyze & decide",
  "Plan & organize",
  "Build & implement",
  "Review & communicate",
  "Other",
];
const TASK_THEME_MODE = {{
  "Explore & learn": "Exploration",
  "Analyze & decide": "Exploration",
  "Plan & organize": "Planning",
  "Build & implement": "Execution",
  "Review & communicate": "Review/support",
  "Other": "Other",
}};
const TASK_THEME_COLORS = {{
  "Explore & learn": "#8250df",
  "Analyze & decide": "#a371f7",
  "Plan & organize": "#bf8700",
  "Build & implement": "#2f6feb",
  "Review & communicate": "#1a7f37",
  "Other": "#8c959f",
}};

function inferTaskTheme(summary) {{
  const text = String(summary || "").toLowerCase();
  if (/(implement|build|create|develop|add|integrat|install|fix|enhanc|code|debug)/.test(text)) return "Build & implement";
  if (/(research|explor|investigat|understand|discover|learn)/.test(text)) return "Explore & learn";
  if (/(analy|evaluat|assess|compar|clarif|validat|decid)/.test(text)) return "Analyze & decide";
  if (/(plan|prepar|organiz|tidy|itinerary)/.test(text)) return "Plan & organize";
  if (/(review|improv|document|report|comment|summar|update)/.test(text)) return "Review & communicate";
  return "Other";
}}

// Returns the cutoff date string (inclusive) for the active quick filter, or null for "all time"
function computeCutoffDate(dateFilter) {{
  if (dateFilter === "all" || !GLOBAL_MAX_DATE) return null;
  const days = parseInt(dateFilter, 10);
  const cutoff = new Date(GLOBAL_MAX_DATE + "T00:00:00Z");
  cutoff.setUTCDate(cutoff.getUTCDate() - (days - 1));
  return cutoff.toISOString().slice(0, 10);
}}

function render() {{
  const excludedProjects = loadExcluded("project");
  const excludedModels = loadExcluded("model");
  document.querySelectorAll(".proj-check").forEach(cb => {{ cb.checked = !excludedProjects.has(cb.value); }});
  document.querySelectorAll(".model-check").forEach(cb => {{ cb.checked = !excludedModels.has(cb.value); }});

  const metric = getMetric();
  const valKey = metric === "cost" ? "estimated_cost" : "total_tokens";
  const fmtVal = metric === "cost" ? fmtCurrency : fmtCompact;
  const unitLabel = metric === "cost" ? "estimated cost ($)" : "tokens";
  // Longer, explicit axis titles so units are unambiguous at a glance (not just in hover text)
  const valAxisTitle = metric === "cost" ? "Estimated cost, USD (list price)" : "Total tokens (input + output, summed)";
  document.getElementById("metric-tokens").classList.toggle("active", metric === "tokens");
  document.getElementById("metric-cost").classList.toggle("active", metric === "cost");

  const dateFilter = getDateFilter();
  const cutoffDateStr = computeCutoffDate(dateFilter);
  ["7", "14", "30", "90", "all"].forEach(v => {{
    const btn = document.getElementById("date-" + v);
    if (btn) btn.classList.toggle("active", dateFilter === v);
  }});
  const dateHintEl = document.getElementById("date-range-hint");
  if (dateHintEl) {{
    dateHintEl.textContent = dateFilter === "all"
      ? "Showing all dates in the loaded export(s)"
      : ("Showing " + (cutoffDateStr || "?") + " \u2192 " + (GLOBAL_MAX_DATE || "?") + " (last " + dateFilter + " days of data)");
  }}

  const filtered = RAW.filter(r => !excludedProjects.has(r.project) && !excludedModels.has(r.model) && (!cutoffDateStr || (r.date && r.date >= cutoffDateStr)));

  // KPIs (always show both tokens and cost, regardless of chart metric toggle)
  const totalTokens = sum(filtered, "total_tokens");
  const totalCost = sum(filtered, "estimated_cost");
  const totalCalls = sum(filtered, "calls");
  const nProjects = new Set(filtered.map(r => r.project)).size;
  const nModels = new Set(filtered.map(r => r.model)).size;
  const nUsers = new Set(filtered.map(r => r.user)).size;
  const dates = filtered.map(r => r.date).filter(Boolean).sort();
  const dateRange = dates.length ? (dates[0] + " &rarr; " + dates[dates.length - 1]) : "n/a";
  document.getElementById("kpi-row").innerHTML = `
    <div class="kpi" title="Sum of total_tokens (prompt + completion) across every call matching the current project/model/date filters."><div class="kpi-value">${{fmt(totalTokens)}}</div><div class="kpi-label">Total tokens</div></div>
    <div class="kpi" title="Estimated USD cost using GitHub's published per-token list prices, applied to the same filtered calls. Estimate only - plan allowances, included credits, or discounts are not reflected."><div class="kpi-value">${{fmtCurrency(totalCost)}}</div><div class="kpi-label">Est. cost (list price)</div></div>
    <div class="kpi" title="Number of model invocations (API calls) in the current filters."><div class="kpi-value">${{fmt(totalCalls)}}</div><div class="kpi-label">Model calls</div></div>
    <div class="kpi" title="Number of distinct projects/tasks with at least one matching call."><div class="kpi-value">${{nProjects}}</div><div class="kpi-label">Projects/tasks shown</div></div>
    <div class="kpi" title="Number of distinct AI models used among the matching calls."><div class="kpi-value">${{nModels}}</div><div class="kpi-label">Models shown</div></div>
    <div class="kpi" title="Number of distinct user accounts represented in the matching calls."><div class="kpi-value">${{nUsers}}</div><div class="kpi-label">Users</div></div>
    <div class="kpi" title="Earliest and latest calendar date among the matching calls, after applying the Date range quick filter below."><div class="kpi-value">${{dateRange}}</div><div class="kpi-label">Date range</div></div>
  `;

  // Top projects
  const byProject = groupSum(filtered, r => r.project, valKey);
  const topProjects = Array.from(byProject.entries()).sort((a, b) => b[1] - a[1]).slice(0, 15);
  Plotly.react("fig_project", [{{
    x: topProjects.map(p => p[1]), y: topProjects.map(p => p[0]),
    type: "bar", orientation: "h", marker: {{ color: "#2f6feb" }},
    text: topProjects.map(p => fmtVal(p[1])), textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{y}}: %{{x:,}} " + unitLabel + "<extra></extra>",
  }}], {{
    title: {{ text: "Top Projects by " + (metric === "cost" ? "Estimated Cost" : "Total Tokens") }},
    xaxis: {{ title: {{ text: valAxisTitle }} }},
    yaxis: {{ title: {{ text: "Project (top 15 by " + (metric === "cost" ? "cost" : "tokens") + ")" }}, autorange: "reversed" }},
    margin: {{ l: 260, r: 60 }},
  }}, {{ responsive: true }});

  // Share by model
  const byModel = groupSum(filtered, r => r.model, valKey);
  const modelEntries = Array.from(byModel.entries()).sort((a, b) => b[1] - a[1]);
  Plotly.react("fig_model", [{{
    labels: modelEntries.map(m => m[0]), values: modelEntries.map(m => m[1]), type: "pie", hole: 0.4,
    marker: {{ colors: modelEntries.map(m => MODEL_COLORS[m[0]] || "#2f6feb") }},
    textinfo: "label+percent", texttemplate: "%{{label}}<br>%{{percent}} (%{{customdata}})",
    customdata: modelEntries.map(m => fmtVal(m[1])),
    hovertemplate: "%{{label}}: %{{value:,}} " + unitLabel + " (%{{percent}})<extra></extra>",
  }}], {{ title: {{ text: (metric === "cost" ? "Cost Share by Model" : "Token Share by Model") + " (selected)" }}, showlegend: false }}, {{ responsive: true }});

  // Overview insights: call out the top project and top model in plain language
  const insightOverview = document.getElementById("insight-overview");
  if (insightOverview) {{
    const bits = [];
    if (topProjects.length) {{
      const topShare = totalTokens > 0 ? (100 * sum(filtered.filter(r => r.project === topProjects[0][0]), "total_tokens") / totalTokens).toFixed(0) : 0;
      bits.push(`<div class="insight"><span>&#128200;</span><span><b>${{topProjects[0][0]}}</b> is your top project, accounting for <b>${{topShare}}%</b> of total tokens in the current selection.</span></div>`);
    }}
    if (modelEntries.length) {{
      const topModelShare = totalTokens > 0 ? (100 * sum(filtered.filter(r => r.model === modelEntries[0][0]), "total_tokens") / totalTokens).toFixed(0) : 0;
      bits.push(`<div class="insight good"><span>&#129504;</span><span><b>${{modelEntries[0][0]}}</b> is your most-used model, at <b>${{topModelShare}}%</b> of total tokens.</span></div>`);
    }}
    insightOverview.innerHTML = bits.join("");
  }}

  // Trend over time
  const granularity = getTrendGranularity();
  updateTrendGranularityButtons(granularity);
  const trendUnit = granularity === "day" ? "day" : granularity === "week" ? "week" : "month";
  const byDate = groupSum(filtered, r => trendBucket(r.date, granularity), valKey);
  const dateEntries = Array.from(byDate.entries()).sort((a, b) => a[0] < b[0] ? -1 : 1);
  const trendTitle = metric === "cost" ? "Estimated Cost Over Time" : "Token Usage Over Time";
  const trendAxisTitle = granularity === "day"
    ? "Date (calendar day)"
    : granularity === "week" ? "Week starting (Monday)" : "Month (calendar)";
  Plotly.react("fig_trend", [{{
    x: dateEntries.map(d => d[0]), y: dateEntries.map(d => d[1]), type: "scatter", mode: "lines+markers+text",
    line: {{ color: "#238636", width: 2.5 }}, marker: {{ size: 6 }}, fill: "tozeroy", fillcolor: "rgba(35,134,54,0.08)",
    text: dateEntries.map(d => fmtVal(d[1])), textposition: "top center",
    hovertemplate: "%{{x}}: %{{y:,}} " + unitLabel + "<extra></extra>",
  }}], {{ title: {{ text: trendTitle + " (by " + trendUnit + ")" }}, xaxis: {{ title: {{ text: trendAxisTitle }} }}, yaxis: {{ title: {{ text: valAxisTitle }} }} }}, {{ responsive: true }});

  // Trend insight: flag the single biggest period-over-period jump, if any
  const insightTrends = document.getElementById("insight-trends");
  if (insightTrends) {{
    if (dateEntries.length >= 2) {{
      let maxJumpIdx = -1, maxJump = 0;
      for (let i = 1; i < dateEntries.length; i++) {{
        const jump = dateEntries[i][1] - dateEntries[i - 1][1];
        if (jump > maxJump) {{ maxJump = jump; maxJumpIdx = i; }}
      }}
      insightTrends.innerHTML = maxJumpIdx > 0
        ? `<div class="insight"><span>&#128640;</span><span>Biggest ${{trendUnit}} increase: <b>${{dateEntries[maxJumpIdx][0]}}</b> rose to <b>${{fmtVal(dateEntries[maxJumpIdx][1])}}</b> (up ${{fmtVal(maxJump)}} from the previous ${{trendUnit}}).</span></div>`
        : `<div class="insight"><span>&#128200;</span><span>Usage held roughly steady across the selected date range - no single large ${{trendUnit}} spike.</span></div>`;
    }} else {{
      insightTrends.innerHTML = `<div class="insight"><span>&#8505;</span><span>Not enough ${{trendUnit}}s in the current selection to show a trend - widen the date range or choose a finer grouping.</span></div>`;
    }}
  }}

  // Model mix per top project (stacked)
  const topProjectNames = topProjects.map(p => p[0]);
  const modelsSet = new Set(filtered.filter(r => topProjectNames.includes(r.project)).map(r => r.model));
  const stackTraces = Array.from(modelsSet).map(model => {{
    const perProject = topProjectNames.map(proj => sum(filtered.filter(r => r.project === proj && r.model === model), valKey));
    return {{
      name: model, x: topProjectNames, y: perProject, type: "bar",
      marker: {{ color: MODEL_COLORS[model] || "#2f6feb" }},
      text: perProject.map(v => v > 0 ? fmtVal(v) : ""), textposition: "inside", insidetextanchor: "middle",
      hovertemplate: "%{{x}}<br>" + model + ": %{{y:,}} " + unitLabel + "<extra></extra>",
    }};
  }});
  Plotly.react("fig_stack", stackTraces, {{
    barmode: "stack", title: {{ text: "Model Mix per Top Project (stacked by model)" }},
    xaxis: {{ title: {{ text: "Project (top 15 by " + (metric === "cost" ? "cost" : "tokens") + ")" }}, tickangle: -35 }},
    yaxis: {{ title: {{ text: valAxisTitle }} }}, margin: {{ b: 150 }},
  }}, {{ responsive: true }});

  // Value for money: tokens per dollar by model (aggregate, not per-row average, so heavy
  // users of a model don't get diluted/inflated by one-off outlier rows). This is a pricing
  // ratio, not a quality measure - see the info-icon tooltip on the card for the full caveat.
  const modelTokens = groupSum(filtered, r => r.model, "total_tokens");
  const modelCost = groupSum(filtered, r => r.model, "estimated_cost");
  const modelCalls = groupSum(filtered, r => r.model, "calls");
  const valueEntries = Array.from(modelTokens.keys())
    .filter(m => (modelCost.get(m) || 0) > 0)
    .map(m => ({{ model: m, tpd: modelTokens.get(m) / modelCost.get(m), calls: modelCalls.get(m) || 0 }}))
    .sort((a, b) => b.tpd - a.tpd);
  Plotly.react("fig_value", [{{
    x: valueEntries.map(v => v.tpd), y: valueEntries.map(v => v.model),
    type: "bar", orientation: "h", marker: {{ color: valueEntries.map(v => MODEL_COLORS[v.model] || "#bf8700") }},
    text: valueEntries.map(v => fmtCompact(v.tpd) + " tok/$ (" + fmt(v.calls) + " calls)"),
    textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{y}}: %{{x:,.0f}} tokens per $ spent<extra></extra>",
  }}], {{
    title: {{ text: "Value for Money \u2014 Tokens per Dollar by Model" }},
    xaxis: {{ title: {{ text: "Tokens per USD of estimated cost (higher = cheaper per token)" }} }},
    yaxis: {{ title: {{ text: "Model (ranked highest value first)" }}, autorange: "reversed" }},
    margin: {{ l: 160, r: 140 }},
  }}, {{ responsive: true }});

  // Value insight: name the best and worst tok/$ models with enough data to be meaningful (5+ calls)
  const insightValue = document.getElementById("insight-value");
  if (insightValue) {{
    const reliableValue = valueEntries.filter(v => v.calls >= 5);
    if (reliableValue.length >= 2) {{
      const best = reliableValue[0], worst = reliableValue[reliableValue.length - 1];
      const multiple = (best.tpd / worst.tpd).toFixed(1);
      insightValue.innerHTML = `<div class="insight good"><span>&#128181;</span><span><b>${{best.model}}</b> gives the most tokens per dollar (${{fmtCompact(best.tpd)}} tok/$), about <b>${{multiple}}&times;</b> more than <b>${{worst.model}}</b> (${{fmtCompact(worst.tpd)}} tok/$) among models with 5+ calls.</span></div>`;
    }} else {{
      insightValue.innerHTML = `<div class="insight"><span>&#8505;</span><span>Not enough models with 5+ calls in the current selection for a reliable value comparison.</span></div>`;
    }}
  }}

  // Work patterns: transparent rule-based themes from task summaries
  const taskData = HAS_TASKS
    ? filtered.filter(r => r.task_summary).map(r => ({{ ...r, theme: inferTaskTheme(r.task_summary) }}))
    : [];
  const patternsNote = document.getElementById("work-patterns-note");
  if (patternsNote) {{
    patternsNote.innerHTML = taskData.length
      ? "Themes are inferred from task-summary keywords; unclassified summaries appear as <b>Other</b>. Use this as a workflow signal, not a productivity score."
      : "No task summaries are available. Re-run <code>extract_usage.py --include-task-summary</code> to populate Work patterns.";
  }}

  const themeTimeMap = new Map();
  for (const r of taskData) {{
    const bucket = trendBucket(r.date, granularity);
    if (!themeTimeMap.has(bucket)) themeTimeMap.set(bucket, new Map());
    const themeValues = themeTimeMap.get(bucket);
    themeValues.set(r.theme, (themeValues.get(r.theme) || 0) + (r[valKey] || 0));
  }}
  const themeBuckets = Array.from(themeTimeMap.keys()).sort();
  const themeTimeTraces = TASK_THEME_ORDER.map(theme => ({{
    name: theme,
    x: themeBuckets,
    y: themeBuckets.map(bucket => themeTimeMap.get(bucket).get(theme) || 0),
    type: "bar",
    marker: {{ color: TASK_THEME_COLORS[theme] }},
    hovertemplate: "%{{x}}<br>" + theme + ": %{{y:,}} " + unitLabel + "<extra></extra>",
  }})).filter(trace => trace.y.some(v => v > 0));
  Plotly.react("fig_theme_time", themeTimeTraces, {{
    barmode: "stack",
    title: {{ text: "Inferred Task Themes Over Time (by " + trendUnit + ")" }},
    xaxis: {{ title: {{ text: trendAxisTitle }} }},
    yaxis: {{ title: {{ text: valAxisTitle }} }},
  }}, {{ responsive: true }});

  const themeModels = MODEL_ORDER.filter(model => taskData.some(r => r.model === model));
  const themeModelValues = TASK_THEME_ORDER.map(theme =>
    themeModels.map(model => taskData
      .filter(r => r.theme === theme && r.model === model)
      .reduce((total, r) => total + (r[valKey] || 0), 0))
  );
  Plotly.react("fig_theme_model", [{{
    x: themeModels,
    y: TASK_THEME_ORDER,
    z: themeModelValues,
    type: "heatmap",
    colorscale: "Blues",
    text: themeModelValues.map(row => row.map(v => fmtVal(v))),
    texttemplate: "%{{text}}",
    hovertemplate: "%{{y}}<br>%{{x}}: %{{z:,}} " + unitLabel + "<extra></extra>",
    colorbar: {{ title: {{ text: unitLabel }} }},
  }}], {{
    title: {{ text: "Inferred Task Theme by Model" }},
    xaxis: {{ title: {{ text: "Model" }} }},
    yaxis: {{ title: {{ text: "Inferred theme" }} }},
    margin: {{ l: 150, b: 120 }},
  }}, {{ responsive: true }});

  const modeValues = new Map();
  for (const r of taskData) {{
    const mode = TASK_THEME_MODE[r.theme] || "Other";
    modeValues.set(mode, (modeValues.get(mode) || 0) + (r[valKey] || 0));
  }}
  const modeEntries = Array.from(modeValues.entries()).sort((a, b) => b[1] - a[1]);
  const modeColors = {{ Exploration: "#8250df", Execution: "#2f6feb", Planning: "#bf8700", "Review/support": "#1a7f37", Other: "#8c959f" }};
  Plotly.react("fig_work_mode", [{{
    labels: modeEntries.map(entry => entry[0]),
    values: modeEntries.map(entry => entry[1]),
    type: "pie",
    hole: 0.48,
    marker: {{ colors: modeEntries.map(entry => modeColors[entry[0]] || "#8c959f") }},
    textinfo: "label+percent",
    texttemplate: "%{{label}}<br>%{{percent}} (%{{value:.3s}})",
    hovertemplate: "%{{label}}: %{{value:,}} " + unitLabel + " (%{{percent}})<extra></extra>",
  }}], {{
    title: {{ text: "Inferred Work-mode Balance" }},
    showlegend: false,
  }}, {{ responsive: true }});

  // By user (only if multi-user)
  if (document.getElementById("fig_user")) {{
    const byUser = groupSum(filtered, r => r.user, valKey);
    const userEntries = Array.from(byUser.entries()).sort((a, b) => b[1] - a[1]);
    Plotly.react("fig_user", [{{
      x: userEntries.map(u => u[0]), y: userEntries.map(u => u[1]), type: "bar", marker: {{ color: "#8250df" }},
      text: userEntries.map(u => fmtVal(u[1])), textposition: "outside", cliponaxis: false,
      hovertemplate: "%{{x}}: %{{y:,}} " + unitLabel + "<extra></extra>",
    }}], {{ title: {{ text: metric === "cost" ? "Estimated Cost by User" : "Total Tokens by User" }}, xaxis: {{ title: {{ text: "User" }} }}, yaxis: {{ title: {{ text: valAxisTitle }} }} }}, {{ responsive: true }});
  }}

  // Reasoning effort mix - higher effort burns more reasoning tokens & costs more
  const effortOrder = ["none", "low", "medium", "high", "n/a"];
  const byEffort = groupSum(filtered, r => r.reasoning_effort || "n/a", valKey);
  const effortEntries = Array.from(byEffort.entries()).sort((a, b) => effortOrder.indexOf(a[0]) - effortOrder.indexOf(b[0]));
  const effortColors = {{ none: "#8c959f", low: "#54aeff", medium: "#2f6feb", high: "#a371f7", "n/a": "#d0d7de" }};
  if (document.getElementById("fig_effort")) {{
    Plotly.react("fig_effort", [{{
      x: effortEntries.map(e => e[0]), y: effortEntries.map(e => e[1]), type: "bar",
      marker: {{ color: effortEntries.map(e => effortColors[e[0]] || "#2f6feb") }},
      text: effortEntries.map(e => fmtVal(e[1])), textposition: "outside", cliponaxis: false,
      hovertemplate: "%{{x}} effort: %{{y:,}} " + unitLabel + "<extra></extra>",
    }}], {{
      title: {{ text: (metric === "cost" ? "Estimated Cost" : "Tokens") + " by Reasoning Effort" }},
      xaxis: {{ title: {{ text: "Reasoning effort (model's reasoning depth setting)" }} }}, yaxis: {{ title: {{ text: valAxisTitle }} }},
    }}, {{ responsive: true }});
  }}

  // Top tasks table (always shown by tokens + cost together)
  if (HAS_TASKS) {{
    const byTask = new Map();
    for (const r of filtered) {{
      if (!r.task_summary) continue;
      const k = r.project + "||" + r.task_summary + "||" + (r.session_id || "");
      if (!byTask.has(k)) byTask.set(k, {{ project: r.project, task: r.task_summary, tokens: 0, cost: 0 }});
      const t = byTask.get(k);
      t.tokens += r.total_tokens;
      t.cost += r.estimated_cost;
    }}
    taskTableRows = Array.from(byTask.values());
    if (!taskSortKey) taskSortKey = metric === "cost" ? "cost" : "tokens";
    renderTaskTable();
  }}
}}

function setAll(kind, checked) {{
  const order = kind === "project" ? PROJECT_ORDER : MODEL_ORDER;
  const excluded = checked ? new Set() : new Set(order);
  saveExcluded(kind, excluded);
  render();
}}

function resizeAllCharts() {{
  document.querySelectorAll(".js-plotly-plot").forEach(el => {{
    try {{ Plotly.Plots.resize(el); }} catch (e) {{ /* chart not yet drawn */ }}
  }});
}}

function applySidebarState(collapsed) {{
  const layout = document.getElementById("layout");
  const icon = document.getElementById("sidebar-toggle-icon");
  const btn = document.getElementById("sidebar-toggle");
  layout.classList.toggle("sidebar-collapsed", collapsed);
  icon.innerHTML = collapsed ? "&raquo;" : "&laquo;";
  btn.title = collapsed ? "Show the Projects and Models filter panel" : "Hide the Projects and Models filter panel";
}}

function toggleSidebar() {{
  const collapsed = document.getElementById("layout").classList.contains("sidebar-collapsed");
  const next = !collapsed;
  localStorage.setItem(STORAGE_KEY_SIDEBAR, next ? "1" : "0");
  applySidebarState(next);
  // Wait for the flex transition to finish before telling Plotly to resize.
  setTimeout(resizeAllCharts, 180);
}}

document.addEventListener("change", (e) => {{
  let kind = null;
  if (e.target.classList.contains("proj-check")) kind = "project";
  else if (e.target.classList.contains("model-check")) kind = "model";
  if (!kind) return;
  const excluded = loadExcluded(kind);
  if (e.target.checked) excluded.delete(e.target.value); else excluded.add(e.target.value);
  saveExcluded(kind, excluded);
  render();
}});

applySidebarState(localStorage.getItem(STORAGE_KEY_SIDEBAR) === "1");
render();
</script>
</body>
</html>
"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dashboard written to {out_path} ({len(html)/1024:.0f} KB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="pattern", default="copilot_usage_*.csv", help="Glob pattern for input CSV export(s)")
    ap.add_argument("--out", default="usage_dashboard.html", help="Output HTML path")
    ap.add_argument("--title", default="Copilot CLI Token Usage Dashboard", help="Dashboard title")
    ap.add_argument("--exclude-default", default="", help="Comma-separated project names unchecked by default on first load (only applies until you toggle checkboxes yourself, then your browser's choice takes over)")
    ap.add_argument("--exclude-default-models", default="", help="Comma-separated model names unchecked by default on first load")
    args = ap.parse_args()

    data = load_data(args.pattern)
    exclude_default_projects = [p.strip() for p in args.exclude_default.split(",") if p.strip()]
    exclude_default_models = [m.strip() for m in args.exclude_default_models.split(",") if m.strip()]
    build_dashboard(data, args.out, args.title, exclude_default_projects, exclude_default_models, storage_key=args.out)


if __name__ == "__main__":
    main()
