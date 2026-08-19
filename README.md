# Copilot CLI Token Usage Dashboard

Find out which projects/tasks consume the most GitHub Copilot CLI tokens
(and dollars), and which models drive that spend — using data that already
exists on your machine. No new logging, no org API access, no backend
required.

**Primary use case: your own local usage.** Run both scripts on your own
machine to see your own numbers — no sharing, no team folder, no exposure
of anything beyond what already lives in your local `session-store.db`.
The multi-user "team rollup" workflow described later in this README also
works (the scripts and de-duplication logic already support it), but it is
a secondary scenario, and one you should think through before using: see
**Privacy notes on the generated HTML** below.

Output is a single self-contained HTML file (Plotly, JS embedded inline) —
open it by double-click in any browser, fully offline.

![dashboard preview](docs/preview.png)

## Dashboard sections

The preview above shows **Overview**. The remaining sections provide focused
views of usage trends, pricing efficiency, model composition, and task detail.
These documentation screenshots use synthetic project and task names; the
displayed figures are representative.

<table>
  <tr>
    <td><strong>Trends over time</strong><br><img src="docs/trends.png" alt="Trends over time section" width="100%"></td>
    <td><strong>Cost &amp; value</strong><br><img src="docs/value.png" alt="Cost and value section" width="100%"></td>
  </tr>
  <tr>
    <td><strong>Composition</strong><br><img src="docs/composition.png" alt="Composition section" width="100%"></td>
    <td><strong>Task detail</strong><br><img src="docs/detail.png" alt="Task detail section" width="100%"></td>
  </tr>
</table>

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
  (`assistant_usage_events`, `total_nano_aiu`, etc.). `extract_usage.py`
  validates that the required tables/columns exist before querying and
  exits with a concise `ERROR:` message naming exactly what's missing if
  they don't (instead of a raw `sqlite3` traceback), but it can only detect
  *absence* — a schema change that silently repurposes an existing
  column's meaning wouldn't be caught. Treat this as a best-effort
  community tool, not an officially supported one.
- **macOS/Linux are untested** — the code is written to be portable
  (standard `~/.copilot` path, plain SQLite), but has only actually been
  run on Windows so far.
- **Cost estimates depend on `total_nano_aiu` being populated per row.**
  On the one install tested, coverage was 100%, but this hasn't been
  verified against older CLI versions. Unlike earlier versions of this
  tool, a coverage gap is no longer silent: `extract_usage.py` (format
  version `"3"`+) records a `cost_data_calls` counter per row so
  `dashboard.py` can distinguish "confirmed no cost data" from "unknown
  (export predates this tracking)" and surface a visible **Cost data
  coverage** KPI plus a warning banner whenever coverage is under 100% —
  see **Token and cost definitions** below.
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

1. **`extract_usage.py`** reads `~/.copilot/session-store.db` through a
   read-only, point-in-time snapshot (see below) and writes a
   privacy-reduced CSV: one row per session/model/day/reasoning-effort, with
   token and cost totals. Local folder paths are reduced to just the repo
   name or last folder name, so exports don't leak your full local directory
   structure. This is **path-minimization, not anonymization** — the CSV
   still contains your username/label, project and model names, and
   (with `--include-task-summary`) free-text task summaries; review its
   contents before sharing it anywhere.
   - **Temporary snapshot handling:** rather than copying the DB file (plus
     its `-wal`/`-shm` sidecars) directly — which can produce a torn,
     inconsistent copy if Copilot CLI is writing to it at the same time —
     `extract_usage.py` uses SQLite's online backup API to take a single,
     transactionally-consistent snapshot into a temporary file, opened
     read-only, and never locks or writes to the live database. The
     temporary snapshot (and its directory) is always deleted afterward,
     both on success and if extraction fails partway through.
   - **Schema validation:** before querying, the required tables/columns are
     checked; if the local `session-store.db` doesn't match what this
     script expects (e.g. an incompatible Copilot CLI version), it exits
     with an `ERROR:` message naming the missing table(s)/column(s) instead
     of a raw SQLite exception.
   - **Export metadata / schema version:** every row also carries
     `export_format_version` and `exported_at`. `export_format_version` is a
     single, monotonically increasing integer string (currently `"3"`) that
     identifies the *shape* of the CSV — it's bumped whenever a change would
     matter to a consumer doing cross-export deduplication or column
     presence checks (this is a plain integer, not semver, since
     `extract_usage.py` is the format's only writer). Version `"3"` added
     `cost_data_calls` — see **Token and cost definitions** below.
     `exported_at` is one
     timezone-aware ISO-8601 UTC timestamp, shared by every row in the file,
     recording when *that run* of `extract_usage.py` produced the file (not
     when any individual usage event happened). `dashboard.py` uses
     `exported_at` — not the file name — to resolve rows that appear in more
     than one overlapping export.
2. **`dashboard.py`** reads one or many of those CSVs and renders the HTML
   dashboard: top projects/tasks, model mix, cost trend, reasoning-effort
   breakdown — with live Project/Model checkbox filters and a Tokens ↔ Cost
   toggle. Each `extract_usage.py` export contains a user's **full** history
   (not just what's new since the last run), so re-exporting weekly and
   globbing all `copilot_usage_*.csv` files is expected to produce
   overlapping rows across files.
   - **Input validation:** each CSV is checked before use. Required columns
     — `user`, `date`, `project`, `model`, `calls`, `total_tokens` — must be
     present or the file is rejected with an `ERROR:` naming the file and
     the missing column(s). Older exports missing optional columns
     (`session_id`, `reasoning_effort`, `total_nano_aiu`) are still
     accepted; each missing optional column is back-filled with a
     documented default (`None`, `"n/a"`, `0.0` respectively) rather than
     raising. Six further optional columns —
     `cost_data_calls`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
     `cache_write_tokens`, `reasoning_tokens` — back-fill to *unknown*
     (`null`, not `0`) when the whole column is absent (a pre-format-`"3"`
     export), and individual blank cells within an otherwise-present column
     are likewise preserved as unknown rather than coerced to zero; see
     **Token and cost definitions** below for what "unknown" means here.
     `calls`, `total_tokens`, and `total_nano_aiu` (when present)
     must be finite, non-negative numbers, and `date` must parse as a
     valid date — invalid values are rejected with an `ERROR:` naming the
     file, column, and CSV row number(s), rather than being silently
     coerced to a misleading `0`.
   - **Legacy (pre-metadata) compatibility policy:** a CSV missing both
     `export_format_version` and `exported_at` is treated as schema
     "version 1" (there is no literal `"1"` ever written — version 1 is
     recognized purely by the *absence* of these columns) and is still
     loaded, but never silently: a `WARNING:` is printed naming the file and
     stating that it's being treated as legacy. Since a legacy file has no
     real export timestamp, `dashboard.py` falls back to that **file's OS
     last-modified time** to order it against other exports — a best-effort
     approximation (a copied/re-saved file's mtime may not match its true
     original export time), also always accompanied by a `WARNING:`. A CSV
     with only **one** of `export_format_version`/`exported_at` present (a
     shape `extract_usage.py` never itself produces, since it always writes
     both columns together) is *not* treated as an ordinary legacy file —
     it gets its own explicit `WARNING:` calling out the specific mismatch,
     rather than being silently folded into the "both absent" case above.
     `export_format_version` still back-fills to the legacy sentinel `"1"`
     when it's the missing column; `exported_at` still falls back to OS
     mtime when it's the missing one. A CSV
     with an `export_format_version` value this tool doesn't recognize
     (i.e. not `"1"`, `"2"`, or `"3"`) is still loaded best-effort, with a
     `WARNING:` about the unrecognized version.
   - **Deduplication policy (deterministic, timestamp-based):** overlapping
     exports are resolved using each row's actual `exported_at` (or the
     mtime fallback above) — **not** file name sort order. Rows are grouped
     by identity:
     - Rows with a `session_id` are identified by
       `(user, session_id, date, model, reasoning_effort)` — `session_id` is
       assumed unique per Copilot CLI session, so this is a high-confidence
       identity.
     - Rows without a `session_id` ("legacy" rows) are identified instead by
       `(user, project, date, model, reasoning_effort)` — only an *inferred*
       identity, since two unrelated legacy sessions could coincidentally
       share this key.

     Within each identity group with more than one row:
     - If every aggregated value (`calls`, the token columns, `total_nano_aiu`,
       and `project`) is identical across the group, the rows are the same
       record duplicated across exports: exactly one is kept — the row with
       the **greatest `exported_at`**. An exact tie (identical timestamps,
       e.g. two files from the same run) is broken **deterministically by
       source file name (basename only, not the full path)**: the
       lexicographically **greatest** file name wins, so the same file name
       ties the same way regardless of which directory it was passed in
       from. This is documented, narrow use of file name only as a
       last-resort tiebreaker, never as the primary ordering.
     - If the values differ **and** the identity is `session_id`-based, this
       is a genuine conflict for the same entity (e.g. a later export
       captured more calls for that session/day). The row with the greatest
       `exported_at` (same tiebreak) wins, and a `WARNING:` is printed
       naming the conflicting file(s), the identity key, and which row won
       — conflicts are always reported, never silently hidden.
     - If the values differ **and** the identity is the inferred legacy
       key, `dashboard.py` cannot safely tell whether this is a real update
       to the same session or a coincidental collision between two
       unrelated legacy sessions. Per policy, **all** rows in the group are
       retained (nothing is dropped), and a `WARNING:` flags the ambiguity
       so it can be investigated — e.g. by re-exporting with a current
       `extract_usage.py`, which always includes `session_id`.
   - **Cross-version (legacy ↔ current) reconciliation, conservative by
     design:** the `session_id`-based and legacy identity schemes above are
     deliberately disjoint — comparing a legacy row directly against a
     current row's `session_id` key would never match. Left unreconciled,
     the *same* underlying session captured once by an old
     `extract_usage.py` (no `session_id`) and once by a current one (with
     `session_id`) would be counted **twice**. To fix this without
     guessing, `dashboard.py` cross-checks every legacy row against
     current-format rows sharing its `(user, project, date, model,
     reasoning_effort)` dimensions and counts how many **distinct**
     `session_id` values appear there:
     - **Exactly one** current session shares those dimensions: the legacy
       row is safely folded into that session's identity group and
       resolved by the normal `session_id` rules above — the current
       (trusted) row always wins over the legacy row, **regardless of
       which `exported_at` is newer**, silently if their aggregated values
       agree or with a conflict `WARNING:` if they don't.
     - **Two or more** distinct current sessions share those dimensions
       (e.g. two separate CLI sessions against the same repo on the same
       day): the dimensions alone can't tell which one (if any) the legacy
       row duplicates. Per policy, it is **never** merged into either —
       **every** row sharing those dimensions (the legacy row(s) and each
       distinct current session) is retained, and a `WARNING:` names the
       ambiguity.
     - **Known limitation:** this can only reconcile a legacy row when its
       dimensions pin down exactly one current session. Two genuinely
       distinct sessions with identical `(user, project, date, model,
       reasoning_effort)` make any legacy row sharing those dimensions
       permanently ambiguous — it's always retained (never dropped), but
       may still end up double-counted against whichever session it
       actually belongs to. There is no purely dimensional fix for that;
       re-exporting with a `session_id`-bearing `extract_usage.py` is the
       only way to fully disambiguate.

## Token and cost definitions

The dashboard's token/cost figures rely on several distinct, non-overlapping
data points from `assistant_usage_events`. Getting the relationships wrong
is the single easiest way to misread this data, so they're stated
explicitly here:

- **`total_tokens` = `input_tokens + output_tokens` ONLY.** It does **not**
  include cache-read, cache-write, or reasoning tokens. This is what every
  "tokens" chart, KPI, and the Tokens/Cost toggle's "Tokens" mode sum.
- **`cache_read_tokens`, `cache_write_tokens`, and `reasoning_tokens` are
  separate, independent additive counters** — not subsets of
  `total_tokens`, and not guaranteed to sum to anything in particular
  together with it. The **Token Composition by Category** chart (in the
  Composition section) is the only place these five categories are shown
  side by side; it always displays raw token counts (it ignores the
  Tokens/Cost toggle), because these categories are not individually
  costed in this data model — there's no per-category dollar figure to
  switch to.
- **`total_nano_aiu` is the cost basis, and is *not* derived from, or
  necessarily proportional to, displayed input+output token counts.** It's
  an independent, per-category-weighted accumulator (e.g. cached input is
  billed differently from fresh input) recorded by Copilot CLI itself.
  `estimated_cost_usd = total_nano_aiu / 1e11` (see **Estimated cost ($)**
  below) is always computed from this field, never approximated from
  token counts.
- **`cost_data_calls` is a coverage counter, not a cost figure.** Each
  aggregated row also carries a count of how many of its underlying calls
  had a non-null `total_nano_aiu` recorded (added in export format version
  `"3"`). `dashboard.py` uses it to classify the calls behind every
  project/model/date selection into:
  - **Full** — every call has confirmed cost data (`cost_data_calls >=
    calls`).
  - **Partial** — some, but not all, calls have confirmed cost data.
  - **None (confirmed missing)** — `cost_data_calls == 0`: it's confirmed
    these calls have no recorded cost, not merely unmeasured.
  - **Unknown** — the row predates `cost_data_calls` entirely (export
    format version before `"3"`, or a whole-column-absent legacy CSV). This
    is deliberately **not** treated as "confirmed missing" or as `0`
    coverage in the arithmetic sense — it's its own bucket, so a legacy
    export is never misread as "this usage was free."
  A **Cost data coverage** KPI shows the percentage of confirmed-cost
  calls for the current selection, and a warning banner appears whenever
  coverage is under 100%, explicitly stating that Est. cost and the
  Value-for-Money chart likely *understate* true spend in that case — a
  low or zero cost figure is never presented as proof of free/cheap usage.
  Legacy CSVs written before this feature existed continue to load
  without error; their `cost_data_calls` is `null` (unknown coverage), not
  `0` or 100%.
- **Pricing certainty is not overstated.** `total_nano_aiu`'s conversion to
  USD list price was verified against GitHub's published per-token rates
  for the CLI version/models this tool has actually been tested against
  (see **Estimated cost ($)** below) — it is not re-derived or
  independently re-verified by this change, and remains a *list-price
  estimate*, not a literal invoice line.

## Privacy notes on the generated HTML

- **Browser display filters (reversible, not privacy-safe for sharing).**
  The Project/Model checkboxes and the `--exclude-default` /
  `--exclude-default-models` flags only control what's **visually shown**
  in the browser. They do **not** remove any row from the generated HTML
  file — every project, model, date, and (if included) task summary from
  the input CSV(s) is embedded in the file's data and is recoverable by
  anyone who opens the file's source (e.g. "View Page Source" or a text
  editor), even for items you've unchecked or excluded by default. Do
  **not** rely on these for sharing a dashboard with someone who shouldn't
  see certain projects.
- **Build-time exclusion (irreversible, use this for sharing).** Two flags
  actually drop data before the HTML file is written, so excluded content
  is never embedded and cannot be recovered from the shared file:
  - `--exclude-project PROJECT` (repeatable) removes every row for an
    **exact** project name. Use it once per project — repeat the flag for
    more than one, which also avoids any ambiguity with project names that
    contain commas:
    ```powershell
    python dashboard.py --in "copilot_usage_*.csv" --out shared_dashboard.html `
      --exclude-project "Personal Side Project" `
      --exclude-project "Client A, Confidential"
    ```
  - `--omit-task-summaries` strips the free-text `task_summary` column
    entirely before build, so no task-summary text (from any project) is
    embedded. Work Patterns and Task Detail then correctly show their
    existing "no task summaries available" state.
  - Both apply before project/model orders, totals, the embedded RAW JSON,
    the Project/Model checkboxes, and every insight callout are computed —
    and an excluded project is also purged from `--exclude-default`'s
    embedded default list, so it can't reappear there either. Console
    output reports exactly how many rows/projects were removed, warns if
    an `--exclude-project` name matched nothing (rather than silently
    implying redaction happened), and exits with an error instead of
    writing a broken/empty dashboard if exclusion would remove every row.
  - **Example — sharing your own individual dashboard** while holding back
    a couple of personal/confidential projects and any free-text task
    detail:
    ```powershell
    python dashboard.py --in "copilot_usage_martinchan_*.csv" --out martinchan_shareable.html `
      --exclude-project "Home Automation" --exclude-project "Job Search 2026" `
      --omit-task-summaries
    ```
  - **This is not anonymization.** `--exclude-project` and
    `--omit-task-summaries` only remove the specific project names / task
    text you name. Every other field is retained and still identifies the
    export as yours, including: the `user` label baked into the CSV by
    `extract_usage.py --user-label` (or your machine username by default),
    `session_id` values, and the names of every **non-excluded** project,
    model, date, and (unless `--omit-task-summaries` is used) task summary.
    Review the underlying CSV(s) yourself before sharing if you need
    anything beyond exact project-name/task-summary redaction.
- All data-derived text (project/model names, task summaries, the
  dashboard title) is HTML/JS-escaped when the file is generated, so it
  can't inject scripts or break the page - but escaping controls how the
  data is *rendered*, not whether it's *present*. For anything
  `--exclude-project` / `--omit-task-summaries` don't cover (e.g. dropping
  a specific date range, a specific model, or a specific user in a
  team-wide export), filter your input CSV(s) before running
  `dashboard.py`.
- If you share a generated dashboard, treat it the same as you'd treat the
  source CSV(s) it was built from.
- **Spreadsheet formula injection.** The dashboard's "Copy table" button
  (Task detail section) neutralizes cell text that would otherwise be
  interpreted as an active formula/DDE command if pasted into Excel/Sheets
  (values starting with `=`, `+`, `-`, or `@` are prefixed with a leading
  `'`, and embedded tabs/newlines are flattened so a single field can't
  inject extra rows/columns). **The raw CSV files produced by
  `extract_usage.py` are not similarly neutralized** — if a project name or
  task summary happens to start with one of those characters and the CSV
  is opened directly in a spreadsheet application (rather than through
  `dashboard.py`), that cell could be interpreted as a formula. This is not
  mitigated in this release because doing so would require prefixing the
  `project` field itself, which is also used as an identifier for matching
  and de-duplication elsewhere in this tool, and changing its stored value
  is a format decision that deserves its own change/testing pass rather
  than a drive-by fix here. If you open exported CSVs directly in Excel,
  be aware of this risk, or import them as plain text.

## Quick start

```powershell
git clone https://github.com/microsoft/ghc-cli-dashboard.git
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
project/model costs more), not as an exact bill. It is also only as
complete as the **Cost data coverage** KPI reports for the current
selection — see **Token and cost definitions** above — and this KPI is
displayed alongside Est. cost with a visible warning whenever coverage is
under 100%, so an incomplete/missing cost figure is never mistaken for a
confirmed cheap or free result.

### Token Composition by Category

A chart in the Composition section shows raw `input_tokens`,
`output_tokens`, `cache_read_tokens`, `cache_write_tokens`, and
`reasoning_tokens` totals side by side for the current selection. Unlike
every other chart, it always shows token counts — it does not switch to
cost with the Tokens/Cost toggle, since these five categories aren't
individually costed in this data model (see **Token and cost
definitions**). A row only contributes to these totals when it has **all
five** categories recorded; a row missing even one of them (a partially
migrated or hand-edited export, as well as an export that predates the
breakdown entirely) is excluded from the totals rather than partially
folded in — partial data would otherwise silently understate whichever
categories that row is missing. A note beneath the chart states how many
calls in the current selection actually have the full breakdown recorded;
the rest are excluded from the totals, not counted as zero.

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
browser's choice wins. See **Privacy notes on the generated HTML** above —
this only hides rows in the browser; use `--exclude-project` /
`--omit-task-summaries` to actually remove data before sharing a file.)

### Collapsible filter panel

The Projects and Models filter sidebar can be collapsed with **Hide filters**
to give the charts more room. Its state is remembered in the browser alongside
the other dashboard selections.

### Task detail table

The Task detail section includes a client-side search across project and task
names, clickable sorting for every column, and a **Copy table** button that
copies the currently displayed top 20 rows as tab-separated text for pasting
into Excel, Teams, or an email.

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

### Trend aggregation

The Trends over time chart can be grouped by **Day**, **Week**, or **Month**.
Weekly buckets start on Monday, and monthly buckets use calendar months. The
selected grouping is remembered in the browser for that dashboard file.

### Work patterns

When task summaries are included, the Work patterns section applies transparent
keyword rules to infer themes such as **Explore & learn**, **Analyze & decide**,
**Plan & organize**, **Build & implement**, and **Review & communicate**. It
shows theme mix over time, theme by model, and an exploration/execution-style
work-mode balance. These are inferred workflow signals, not productivity or
performance measures.

### Value for Money — tokens per dollar by model

A dedicated chart ranks models by `total_tokens ÷ estimated_cost` (tokens
bought per dollar of list-price spend), highest first, with the call count
shown per bar so you can judge how much data backs each ranking. This is a
**pricing-efficiency ratio only** — it does not measure output quality,
accuracy, or how many tokens a model actually needs to do a task well, and
a model used mostly at high reasoning-effort will look worse here even if
its answers are better (reasoning tokens cost more). Both the token
numerator and cost denominator are computed **only from rows whose cost
data is fully confirmed** (`cost_data_calls` covers every one of that row's
`calls`) — a row with partial, missing, or unknown cost coverage is
excluded from the ratio entirely (never just from the cost side), so a
row's uncosted tokens can never inflate another row's confirmed-cost ratio.
Models are only ranked here when they have at least one such fully-confirmed
row with nonzero cost (a model with zero confirmed cost is excluded rather
than shown with an infinite or misleadingly "free" ratio). A model whose
**overall** cost-data coverage (across all of its rows, not just the ones
used in the ratio) is under 100% still gets a `*` suffix on its label, a `⚠`
marker on its bar, and a hover note that the ratio only reflects its
fully-confirmed rows and may not represent its full usage. An
auto-generated callout names the best/worst-value model (restricted to
models with 5+ calls, to avoid noise from one-off usage), and adds a caveat
when either side has incomplete cost-data coverage.

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

- **Personal (primary scenario)**: run both scripts on your own machine to
  see your own usage — nothing leaves your machine.
- **Team-wide rollup** — no backend needed, but read
  **Privacy notes on the generated HTML** above first, since the resulting
  file embeds every row from every teammate's CSV, and checkbox/default
  filters only hide rows visually:
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

The project is maintained in the GitHub repository
[`microsoft/ghc-cli-dashboard`](https://github.com/microsoft/ghc-cli-dashboard).
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
- Cost-data coverage tracking (`cost_data_calls`, added in export format
  version `"3"`) is a *presence* check on `total_nano_aiu`, not an
  independent audit of pricing correctness — see **Token and cost
  definitions** above for the full unknown/none/partial/full model and its
  residual limits.

## Running tests

A small pytest suite in `tests/` guards the security-sensitive parts of
`dashboard.py` — HTML/JS escaping of project/model names, the dashboard
title, and the task-detail table's `</script>`-safe JSON embedding, plus
formula-injection neutralization in the "Copy table" TSV clipboard text —
and the build-time redaction flags (`--exclude-project`,
`--omit-task-summaries`), including hostile/unusual project names,
`--exclude-default` overlap, unmatched-exclusion warnings, and the
all-rows-excluded error. It also covers CSV schema/value validation, and
export metadata / deduplication: `export_format_version`/`exported_at`
emission and parsing, newest-`exported_at`-wins ordering, deterministic
same-timestamp tie-breaking, legacy (pre-metadata) fallback warnings, and
the safe-dedup vs. ambiguous-and-retained handling of legacy rows without
`session_id`, including cross-version reconciliation between legacy and
current rows for the same underlying session (see
`tests/test_export_metadata.py` and `tests/test_dashboard_dedup.py`).
`tests/test_cost_coverage.py` covers the cost-data coverage KPI/banner
(full/partial/none/unknown), token-category composition totals, legacy
(pre-`cost_data_calls`) CSVs, and divide-by-zero robustness in the
Value-for-Money chart when cost data is missing or zero.

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -v
```

A few tests execute the generated dashboard's inline JavaScript in a
lightweight fake-DOM harness (`tests/dom_harness.js`) via Node.js to verify
runtime behavior end-to-end; those are skipped automatically if `node` isn't
on `PATH`.

## Maintainer note: Plotly title syntax

`dashboard.py` bundles **Plotly.js v3.7.0** (via `plotly.offline.get_plotlyjs()`).
That version requires every `title` — chart title and axis titles — to be
an **object**, e.g. `title: { text: "..." }`, not the plain-string shorthand
(`title: "..."`) that older Plotly versions auto-converted. Passing a plain
string silently renders no title at all (no error). If you add a new chart
or edit an existing title, use the object form.
