# Copilot CLI Token Usage Dashboard

Find out which projects/tasks consume the most GitHub Copilot CLI tokens
(and dollars), and which models drive that spend — using data that already
exists on your machine. No new logging, no org API access, no backend
required.

Output is a single self-contained HTML file (Plotly, JS embedded inline) —
open it by double-click in any browser, fully offline. Safe to email, post
to Teams/SharePoint, or attach to a ticket.

![dashboard preview](docs/preview.png)

## How it works

1. **`extract_usage.py`** reads `~/.copilot/session-store.db` (a safe
   read-only copy — never touches or locks the live DB) and writes an
   anonymized CSV: one row per session/model/day/reasoning-effort, with
   token and cost totals. Local folder paths are reduced to just the repo
   name or last folder name, so exports are safe to share outside your
   machine.
2. **`dashboard.py`** reads one or many of those CSVs and renders the HTML
   dashboard: top projects/tasks, model mix, cost trend, reasoning-effort
   breakdown — with live Project/Model checkbox filters and a Tokens ↔ Cost
   toggle.

## Requirements

- Python 3.9+
- `pip install -r requirements.txt` (pandas, plotly)

## Quick start

```powershell
git clone <this-repo-url>
cd copilot-usage-dashboard
pip install -r requirements.txt

# 1. Export your usage (run anytime, e.g. weekly)
python extract_usage.py --include-task-summary

# 2. Build your personal dashboard
python dashboard.py --in "copilot_usage_*.csv" --out usage_dashboard.html

# 3. Open usage_dashboard.html in any browser
```

## Features

### Estimated cost ($)

Click **Estimated cost ($)** above the charts to switch every chart (and the
top-tasks table) from raw tokens to a real-dollar cost estimate, computed
per usage row as:

```
estimated_cost_usd = total_nano_aiu / 1e11
```

(1 AI credit = $0.01; `total_nano_aiu`'s underlying batch pricing was
verified against GitHub's published per-token rates — see
[Models and pricing for GitHub Copilot](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing) —
and matches exactly for input/cached-input/cache-write/output across
several models.)

**Caveat:** this is a *list-price estimate*, not necessarily your literal
invoice line — it doesn't account for included plan allowances, enterprise
discounts, or currency differences. Use it for relative comparison (which
project/model costs more), not as an exact bill.

### Project & model filters

Two independent checklists in the sidebar — **Projects** and **Models** —
each with Select all/Select none. Unchecking an item instantly excludes it
from every chart and KPI. Each selection is saved in the browser's
`localStorage` per dashboard file, so your choice persists next time you
reopen it. Seed default exclusions (e.g. for personal projects you never
want shown by default) with:

```powershell
python dashboard.py --in "copilot_usage_*.csv" `
  --exclude-default "Music,D:,2026 - Durham,Admiral" `
  --exclude-default-models "gemini-3.5-flash"
```

(Only affects first load — once you toggle checkboxes yourself, your
browser's choice wins.)

### Reasoning effort mix

A chart breaks down tokens/cost by `reasoning_effort`
(`none`/`low`/`medium`/`high`) — a direct cost lever, since `high`-effort
calls burn substantially more reasoning tokens per call.

### On-chart data labels

Every chart shows compact value labels directly on it (e.g. `"154.9M"`,
`"$108.22"`) — no hovering required to read values. Hover still shows exact,
non-rounded figures.

## Scaling to a team

- **Personal**: run both scripts on your own machine to see your own usage.
- **Team-wide rollup** — no backend needed:
  1. Everyone runs `extract_usage.py` and drops the CSV into a shared
     Teams/SharePoint/OneDrive folder (optionally on a schedule, e.g. a
     weekly Windows Task Scheduler job).
     ```powershell
     python extract_usage.py --user-label "jsmith" --out "\\team\share\copilot_usage_jsmith_2026-08-11.csv"
     ```
  2. Anyone points `dashboard.py` at the shared folder to get one combined
     dashboard across the whole team (adds a Users chart automatically when
     more than one user is present):
     ```powershell
     python dashboard.py --in "\\team\share\copilot_usage_*.csv" --out team_usage_dashboard.html
     ```

## Sharing this repo

This repo is currently local-only (`git init` has been run here, nothing
pushed). To share it later: push to a GitHub repo (personal or org) when
ready, or just zip the folder / share it via a network drive — there's no
dependency on any particular host. `.gitignore` already excludes generated
CSV/HTML outputs (which contain your personal usage data and task
summaries) so they're never committed.

## Notes / caveats

- Data source is the local `session-store.db` that ships with Copilot CLI on
  every machine (Windows/Mac/Linux) — this is a personal, per-machine log,
  not an org-wide telemetry API. There's no evidence of a broader org-wide
  usage API accessible from this tool; if GitHub Enterprise admin Copilot
  usage metrics are needed instead, that's a separate, admin-only data
  source.
- `--include-task-summary` includes the free-text session/task summary
  (e.g. "Develop Token Usage Dashboard"). Leave it off for wider/less
  trusted sharing, since summaries can contain sensitive task detail.
- Cost figures are list-price estimates derived from token counts — see
  the Estimated cost section above for the caveat on accuracy.
