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
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; padding: 24px 40px; background: #f6f8fa; color: #24292f; }}
  h1 {{ margin-bottom: 4px; }}
  h2 {{ font-size: 16px; }}
  .subtitle {{ color: #57606a; margin-top: 0; margin-bottom: 24px; }}
  .layout {{ display: flex; gap: 24px; align-items: flex-start; }}
  .sidebar {{ flex: 0 0 280px; display: flex; flex-direction: column; gap: 16px; position: sticky; top: 16px; max-height: 90vh; }}
  .side-panel {{ background: white; border: 1px solid #d0d7de; border-radius: 8px; padding: 14px; overflow-y: auto; }}
  #proj-panel {{ max-height: 52vh; }}
  #model-panel {{ max-height: 32vh; }}
  .side-panel h2 {{ margin-top: 0; }}
  .main {{ flex: 1; min-width: 0; }}
  .kpi-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 28px; }}
  .kpi {{ background: white; border: 1px solid #d0d7de; border-radius: 8px; padding: 16px 22px; min-width: 140px; }}
  .kpi-value {{ font-size: 22px; font-weight: 600; }}
  .kpi-label {{ font-size: 12px; color: #57606a; margin-top: 4px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .card {{ background: white; border: 1px solid #d0d7de; border-radius: 8px; padding: 12px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; background: white; }}
  th, td {{ border: 1px solid #d0d7de; padding: 6px 10px; font-size: 13px; }}
  th {{ background: #f6f8fa; text-align: left; }}
  .full {{ grid-column: 1 / -1; }}
  .proj-buttons {{ display: flex; gap: 8px; margin-bottom: 10px; }}
  .proj-buttons button {{ flex: 1; padding: 6px 8px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; cursor: pointer; font-size: 12px; }}
  .proj-buttons button:hover {{ background: #eaeef2; }}
  .proj-item {{ display: flex; align-items: center; gap: 6px; padding: 4px 2px; font-size: 13px; cursor: pointer; border-radius: 4px; }}
  .proj-item:hover {{ background: #f6f8fa; }}
  .proj-name {{ flex: 1; overflow-wrap: anywhere; }}
  .proj-tok {{ color: #57606a; font-size: 11px; }}
  .hint {{ font-size: 11px; color: #57606a; margin-top: 10px; }}
  .metric-toggle {{ display: flex; align-items: center; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }}
  .metric-label {{ font-size: 13px; font-weight: 600; }}
  .metric-toggle button {{ padding: 6px 14px; border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa; cursor: pointer; font-size: 13px; }}
  .metric-toggle button.active {{ background: #2f6feb; color: white; border-color: #2f6feb; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="subtitle">Generated {datetime.now():%Y-%m-%d %H:%M} &middot; source: Copilot CLI session-store.db exports &middot; uncheck a project or model to exclude it from every chart below</p>
  <div class="layout">
    <div class="sidebar">
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
      <div class="kpi-row" id="kpi-row"></div>
      <div class="metric-toggle">
        <span class="metric-label">Chart metric:</span>
        <button id="metric-tokens" onclick="setMetric('tokens')">Tokens</button>
        <button id="metric-cost" onclick="setMetric('cost')">Estimated cost ($)</button>
        <span class="hint" style="margin:0 0 0 10px;">Cost = published GitHub Copilot per-token list prices &middot; estimate only, may not match your actual invoice (plan allowances/discounts not reflected)</span>
      </div>
      <div class="grid">
        <div class="card full"><div id="fig_project" style="height:430px;"></div></div>
        <div class="card"><div id="fig_model" style="height:400px;"></div></div>
        <div class="card"><div id="fig_trend" style="height:400px;"></div></div>
        <div class="card full"><div id="fig_stack" style="height:440px;"></div></div>
        <div class="card"><div id="fig_effort" style="height:380px;"></div></div>
        {"<div class='card'><div id='fig_user' style='height:380px;'></div></div>" if n_users > 1 else ""}
      </div>
      <div id="table-wrap"></div>
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
const HAS_TASKS = {str(has_tasks).lower()};
const STORAGE_KEY_PROJECT = "copilot_usage_excluded_projects::{storage_key}";
const STORAGE_KEY_MODEL = "copilot_usage_excluded_models::{storage_key}";
const STORAGE_KEY_METRIC = "copilot_usage_metric::{storage_key}";
const DEFAULT_EXCLUDED_PROJECTS = {exclude_default_projects_json};
const DEFAULT_EXCLUDED_MODELS = {exclude_default_models_json};

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

function render() {{
  const excludedProjects = loadExcluded("project");
  const excludedModels = loadExcluded("model");
  document.querySelectorAll(".proj-check").forEach(cb => {{ cb.checked = !excludedProjects.has(cb.value); }});
  document.querySelectorAll(".model-check").forEach(cb => {{ cb.checked = !excludedModels.has(cb.value); }});

  const metric = getMetric();
  const valKey = metric === "cost" ? "estimated_cost" : "total_tokens";
  const fmtVal = metric === "cost" ? fmtCurrency : fmtCompact;
  const unitLabel = metric === "cost" ? "estimated cost ($)" : "tokens";
  document.getElementById("metric-tokens").classList.toggle("active", metric === "tokens");
  document.getElementById("metric-cost").classList.toggle("active", metric === "cost");

  const filtered = RAW.filter(r => !excludedProjects.has(r.project) && !excludedModels.has(r.model));

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
    <div class="kpi"><div class="kpi-value">${{fmt(totalTokens)}}</div><div class="kpi-label">Total tokens</div></div>
    <div class="kpi"><div class="kpi-value">${{fmtCurrency(totalCost)}}</div><div class="kpi-label">Est. cost (list price)</div></div>
    <div class="kpi"><div class="kpi-value">${{fmt(totalCalls)}}</div><div class="kpi-label">Model calls</div></div>
    <div class="kpi"><div class="kpi-value">${{nProjects}}</div><div class="kpi-label">Projects/tasks shown</div></div>
    <div class="kpi"><div class="kpi-value">${{nModels}}</div><div class="kpi-label">Models shown</div></div>
    <div class="kpi"><div class="kpi-value">${{nUsers}}</div><div class="kpi-label">Users</div></div>
    <div class="kpi"><div class="kpi-value">${{dateRange}}</div><div class="kpi-label">Date range</div></div>
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
    title: "Top Projects by " + (metric === "cost" ? "Estimated Cost" : "Total Tokens"), yaxis: {{ autorange: "reversed" }}, margin: {{ l: 260, r: 60 }},
  }}, {{ responsive: true }});

  // Share by model
  const byModel = groupSum(filtered, r => r.model, valKey);
  const modelEntries = Array.from(byModel.entries()).sort((a, b) => b[1] - a[1]);
  Plotly.react("fig_model", [{{
    labels: modelEntries.map(m => m[0]), values: modelEntries.map(m => m[1]), type: "pie", hole: 0.4,
    textinfo: "label+percent", texttemplate: "%{{label}}<br>%{{percent}} (%{{customdata}})",
    customdata: modelEntries.map(m => fmtVal(m[1])),
    hovertemplate: "%{{label}}: %{{value:,}} " + unitLabel + " (%{{percent}})<extra></extra>",
  }}], {{ title: (metric === "cost" ? "Cost Share by Model" : "Token Share by Model") + " (selected)", showlegend: false }}, {{ responsive: true }});

  // Trend over time
  const byDate = groupSum(filtered, r => r.date, valKey);
  const dateEntries = Array.from(byDate.entries()).sort((a, b) => a[0] < b[0] ? -1 : 1);
  Plotly.react("fig_trend", [{{
    x: dateEntries.map(d => d[0]), y: dateEntries.map(d => d[1]), type: "scatter", mode: "lines+markers+text",
    line: {{ color: "#238636" }}, text: dateEntries.map(d => fmtVal(d[1])), textposition: "top center",
    hovertemplate: "%{{x}}: %{{y:,}} " + unitLabel + "<extra></extra>",
  }}], {{ title: (metric === "cost" ? "Estimated Cost Over Time" : "Token Usage Over Time"), xaxis: {{ title: "Date" }}, yaxis: {{ title: metric === "cost" ? "Estimated cost ($)" : "Total tokens" }} }}, {{ responsive: true }});

  // Model mix per top project (stacked)
  const topProjectNames = topProjects.map(p => p[0]);
  const modelsSet = new Set(filtered.filter(r => topProjectNames.includes(r.project)).map(r => r.model));
  const stackTraces = Array.from(modelsSet).map(model => {{
    const perProject = topProjectNames.map(proj => sum(filtered.filter(r => r.project === proj && r.model === model), valKey));
    return {{
      name: model, x: topProjectNames, y: perProject, type: "bar",
      text: perProject.map(v => v > 0 ? fmtVal(v) : ""), textposition: "inside", insidetextanchor: "middle",
      hovertemplate: "%{{x}}<br>" + model + ": %{{y:,}} " + unitLabel + "<extra></extra>",
    }};
  }});
  Plotly.react("fig_stack", stackTraces, {{
    barmode: "stack", title: "Model Mix per Top Project", xaxis: {{ tickangle: -35 }}, yaxis: {{ title: metric === "cost" ? "Estimated cost ($)" : "Total tokens" }}, margin: {{ b: 150 }},
  }}, {{ responsive: true }});

  // By user (only if multi-user)
  if (document.getElementById("fig_user")) {{
    const byUser = groupSum(filtered, r => r.user, valKey);
    const userEntries = Array.from(byUser.entries()).sort((a, b) => b[1] - a[1]);
    Plotly.react("fig_user", [{{
      x: userEntries.map(u => u[0]), y: userEntries.map(u => u[1]), type: "bar", marker: {{ color: "#8250df" }},
      text: userEntries.map(u => fmtVal(u[1])), textposition: "outside", cliponaxis: false,
      hovertemplate: "%{{x}}: %{{y:,}} " + unitLabel + "<extra></extra>",
    }}], {{ title: (metric === "cost" ? "Estimated Cost by User" : "Total Tokens by User"), yaxis: {{ title: metric === "cost" ? "Estimated cost ($)" : "Total tokens" }} }}, {{ responsive: true }});
  }}

  // Reasoning effort mix - higher effort burns more reasoning tokens & costs more
  const effortOrder = ["none", "low", "medium", "high", "n/a"];
  const byEffort = groupSum(filtered, r => r.reasoning_effort || "n/a", valKey);
  const effortEntries = Array.from(byEffort.entries()).sort((a, b) => effortOrder.indexOf(a[0]) - effortOrder.indexOf(b[0]));
  const effortColors = {{ none: "#8c959f", low: "#54aeff", medium: "#2f6feb", high: "#a371f7", "n/a": "#d0d7de" }};
  Plotly.react("fig_effort", [{{
    x: effortEntries.map(e => e[0]), y: effortEntries.map(e => e[1]), type: "bar",
    marker: {{ color: effortEntries.map(e => effortColors[e[0]] || "#2f6feb") }},
    text: effortEntries.map(e => fmtVal(e[1])), textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{x}} effort: %{{y:,}} " + unitLabel + "<extra></extra>",
  }}], {{
    title: (metric === "cost" ? "Estimated Cost" : "Tokens") + " by Reasoning Effort",
    xaxis: {{ title: "Reasoning effort" }}, yaxis: {{ title: metric === "cost" ? "Estimated cost ($)" : "Total tokens" }},
  }}, {{ responsive: true }});

  // Top tasks table (always shown by tokens + cost together)
  const wrap = document.getElementById("table-wrap");
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
    const topTasks = Array.from(byTask.values()).sort((a, b) => b[metric === "cost" ? "cost" : "tokens"] - a[metric === "cost" ? "cost" : "tokens"]).slice(0, 20);
    const rows = topTasks.map(t => `<tr><td>${{t.project}}</td><td>${{t.task}}</td><td style="text-align:right">${{fmt(t.tokens)}}</td><td style="text-align:right">${{fmtCurrency(t.cost)}}</td></tr>`).join("");
    wrap.innerHTML = topTasks.length ? `
      <h2>Top Tasks (selected)</h2>
      <table><thead><tr><th>Project</th><th>Task</th><th>Total tokens</th><th>Est. cost</th></tr></thead><tbody>${{rows}}</tbody></table>
    ` : "";
  }}
}}

function setAll(kind, checked) {{
  const order = kind === "project" ? PROJECT_ORDER : MODEL_ORDER;
  const excluded = checked ? new Set() : new Set(order);
  saveExcluded(kind, excluded);
  render();
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
