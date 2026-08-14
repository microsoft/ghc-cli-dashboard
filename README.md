# Copilot CLI Token Usage Dashboard

Find out which projects/tasks consume the most GitHub Copilot CLI tokens
(and dollars), and which models drive that spend — using data that already
exists on your machine. No new logging, no org API access, no backend
required.

Output is a single self-contained HTML file (Plotly, JS embedded inline) —
open it by double-click in any browser, fully offline. The generated HTML can
be shared by email, Teams/SharePoint, or ticket attachment after checking that
the selected projects and optional task summaries are appropriate for the
audience.

![dashboard preview](docs/preview.png)

## ⚠️ Who this is for (read before sharing widely)

This works for **GitHub Copilot CLI users only** — not GitHub Copilot in
general. Specifically:

| Works with | Does NOT work with |
|---|---|
| GitHub Copilot **CLI** (this terminal agent) | Copilot in VS Code / JetBrains / Visual Studio (IDE completions & chat) |
| | Copilot Chat on github.com |
| | Copilot coding agent (background/PR agent) |
| | Copilot mobile |

If a teammate has never run the `copilot` CLI, `~/.copilot/session-store.db`
won't exist and there's nothing to extract. This tool only sees usage from
the CLI, on whichever machine(s) you run the export on — if you use the CLI
on more than one device, each device needs its own export merged in for a
complete picture.

## Requirements & limitations

- **Python 3.9+** and `pip install -r requirements.txt` (pandas, plotly).
- **Read access to `~/.copilot/session-store.db`** — created automatically
  by Copilot CLI the first time it's used.
- **Relies on an internal, undocumented local database schema**, not a
  published/stable public API. It was reverse-engineered from one
  installation (Copilot CLI **v1.0.79**, Windows) and validated against
  ~5,300 usage rows spanning that install's full history — there is
  **no guarantee this schema is stable across CLI versions**, and a future
  update could silently change or remove fields this tool depends on
  (`assistant_usage_events`, `total_nano_aiu`, etc.). Treat this as a
  best-effort community tool, not an officially supported one.
- **macOS/Linux are untested** — the code is written to be portable
  (standard `~/.copilot` path, plain SQLite), but has only actually been
  run on Windows so far.
- **Cost estimates depend on `total_nano_aiu` being populated per row.**
  On the one install tested, coverage was 100%, but this hasn't been
  verified against older CLI versions, and any gap would silently
  *understate* cost with no warning shown in the dashboard.
- **Org/enterprise telemetry policies are unverified.** If an organization
  disables local usage logging, `session-store.db` may be missing or
  empty — this tool has no way to detect or flag that; it will just look
  like zero usage.
- **Requires comfort running Python from a terminal.** For non-technical
  stakeholders, someone technical will likely need to run the export step
  on their behalf, or you'll need to package it further (e.g. a scheduled
  task producing the CSV automatically).
- Multiple Windows/GitHub accounts on a shared machine will be pooled
  together unless each account exports separately with a distinct
  `--user-label`.

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

## Quick start

```powershell
git clone https://github.com/martinchan_microsoft/ghc-cli-dashboard.git
cd ghc-cli-dashboard
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
  --exclude-default "Project Alpha,Project Beta,Weekend Planning" `
  --exclude-default-models "gemini-3.5-flash"
```

(Only affects first load — once you toggle checkboxes yourself, your
browser's choice wins.)

### Collapsible filter panel

The Projects and Models filter sidebar can be collapsed with **Hide filters**
to give the charts more room. Its state is remembered in the browser alongside
the other dashboard selections.

### Reasoning effort mix

A chart breaks down tokens/cost by `reasoning_effort`
(`none`/`low`/`medium`/`high`) — a direct cost lever, since `high`-effort
calls burn substantially more reasoning tokens per call.

### On-chart data labels

Every chart shows compact value labels directly on it (e.g. `"154.9M"`,
`"$108.22"`) — no hovering required to read values. Hover still shows exact,
non-rounded figures.

### Explicit axis titles & hover-over explanations

Every chart has a descriptive value/category axis title (e.g. *"Total
tokens (input + output, summed)"*, *"Project (top 15 by tokens)"*) so the
unit is never ambiguous at a glance. Each chart card also has a small `?`
icon — hover it for a plain-English explanation of what the chart shows and
how to read it. KPI cards and the Date range/Chart metric labels have
hover tooltips too.

### Date range quick filters

Buttons above the charts — **Last 7 / 14 / 30 / 90 days** or **All time**
(default) — filter every chart, KPI, and the Top Tasks table to a rolling
window. "Last N days" is computed relative to the **most recent date in
your loaded CSV(s)**, not today's real-world date (the export may be a few
days old), and a hint next to the buttons always states the exact date
range currently shown. Your choice persists per-browser like the
project/model filters.

### Value for Money — tokens per dollar by model

A dedicated chart ranks models by `total_tokens ÷ estimated_cost` (tokens
bought per dollar of list-price spend), highest first, with the call count
shown per bar so you can judge how much data backs each ranking. This is a
**pricing-efficiency ratio only** — it does not measure output quality,
accuracy, or how many tokens a model actually needs to do a task well, and
a model used mostly at high reasoning-effort will look worse here even if
its answers are better (reasoning tokens cost more). An auto-generated
callout names the best/worst-value model (restricted to models with 5+
calls, to avoid noise from one-off usage).

### Narrative layout & design

The dashboard is organized into five numbered, sequential sections with a
top navigation (**Overview → Trends → Cost & Value → Composition → Task
Detail**), each with a short blurb explaining what it answers. Charts that
break down by model (pie, stacked bar, value-for-money) share one
consistent colour-per-model palette, so a given model is recognizable
across charts without re-reading legends. Auto-generated insight callouts
(e.g. top project's % share, biggest single-day usage jump, best-value
model) surface the headline takeaway of each section in plain language,
so someone skimming doesn't have to interpret raw charts unassisted.

## Scaling to a team

- **Personal**: run both scripts on your own machine to see your own usage.
- **Team-wide rollup** — no backend needed:
  1. Everyone runs `extract_usage.py` and drops the CSV into a shared
     Teams/SharePoint/OneDrive folder (optionally on a schedule, e.g. a
     weekly Windows Task Scheduler job).
     ```powershell
     python extract_usage.py --user-label "jsmith" --out "\\team\share\copilot_usage_jsmith_YYYY-MM-DD.csv"
     ```
  2. Anyone points `dashboard.py` at the shared folder to get one combined
     dashboard across the whole team (adds a Users chart automatically when
     more than one user is present):
     ```powershell
     python dashboard.py --in "\\team\share\copilot_usage_*.csv" --out team_usage_dashboard.html
     ```

## Repository and generated files

The project is maintained in the private GitHub repository
[`martinchan_microsoft/ghc-cli-dashboard`](https://github.com/martinchan_microsoft/ghc-cli-dashboard).
Generated CSV and HTML outputs are excluded by `.gitignore` because they can
contain personal usage data, project names, and optional task summaries. The
repository's preview image uses synthetic project names; its displayed
figures are representative dashboard data.

## Notes / caveats

- See **Requirements & limitations** above for scope/version/platform
  caveats. This section covers privacy and cost-accuracy notes only.
- If GitHub Enterprise admin-level Copilot usage metrics are needed instead
  of this per-machine tool, that's a separate, admin-only data source this
  project doesn't use or have access to.
- `--include-task-summary` includes the free-text session/task summary
  (e.g. "Develop Token Usage Dashboard"). Leave it off for wider/less
  trusted sharing, since summaries can contain sensitive task detail.
- Cost figures are list-price estimates derived from token counts — see
  the Estimated cost section above for the caveat on accuracy.

## Maintainer note: Plotly title syntax

`dashboard.py` bundles **Plotly.js v3.7.0** (via `plotly.offline.get_plotlyjs()`).
That version requires every `title` — chart title and axis titles — to be
an **object**, e.g. `title: { text: "..." }`, not the plain-string shorthand
(`title: "..."`) that older Plotly versions auto-converted. Passing a plain
string silently renders no title at all (no error). If you add a new chart
or edit an existing title, use the object form.
