---
layout: default
title: Advanced usage
nav_order: 4
---

# Advanced usage

## Privacy and sharing

Exports and generated dashboards can contain personal and confidential data:

- The default user label is the operating-system username.
- Project names, model names, dates, and session IDs are retained.
- Free-text task summaries are included by default (this tool is meant for
  personal viewing); pass `--exclude-task-summary` to `extract_usage.py` to
  leave them out, or `--omit-task-summaries` to `dashboard.py` to strip them
  at build time before sharing.
- A dashboard embeds its source rows in the HTML file.

Treat a CSV or generated dashboard as sensitive data. Review it before
sharing it.

The legacy `--include-task-summary` flag remains supported for existing
commands. It cannot be combined with `--exclude-task-summary`.

The sidebar filters and these options only change what the browser displays:

```powershell
python dashboard.py --exclude-default "Personal Project"
python dashboard.py --exclude-default-models "gemini-3.5-flash"
```

Use build-time options when content must be removed from a shared dashboard:

```powershell
python dashboard.py --in "copilot_usage_*.csv" --out shared_dashboard.html `
  --exclude-project "Personal Project" `
  --omit-task-summaries
```

`--exclude-project` can be repeated. It removes matching rows before the
dashboard is written. `--omit-task-summaries` removes all task-summary text.
These options do not anonymize the remaining fields.

The CSV export itself is intended for programmatic use. Import it as text
when opening it in spreadsheet software because project names and task
summaries can begin with formula characters.

## Combining exports

Point `dashboard.py` at a pattern that matches multiple CSV files:

```powershell
python dashboard.py --in "exports\copilot_usage_*.csv" --out combined_dashboard.html
```

Each export contains the available history at the time it was created.
`dashboard.py` removes overlapping rows by using the export timestamp and
session identity when available. Older exports without metadata still load,
but produce warnings because their ordering and identity are less certain.

Keep source CSV files in a location appropriate for their sensitivity. The
tool does not upload or transmit them.

## Filters and providers

Projects, models, providers, and date range all apply together. Disabling a
provider also excludes its models, even if the corresponding model checkboxes
remain selected. Those model controls are disabled and marked **Excluded by
provider** until the provider is included again. The saved model selection
does not change.

- Search projects and models without changing the active selection. The
  first eight matches are shown, with **Show more** revealing ten more.
- **Only** selects one project, model or provider within its group. Other
  groups and the date range still apply.
- **Select all** and **Select none** apply to the entire group, including
  items hidden by search or pagination.
- **Reset filters** includes every project, model, provider and date, and
  clears project/model searches. It does not restore build-time default
  exclusions, change the chart metric, or undo build-time redaction.

The sidebar's **Hide filters** button collapses the project/model/provider
list into a narrow reopen rail on desktop. The rail stays available while
scrolling, and the selection summary remains visible in the toolbar.
Reloading preserves the collapse choice, and resetting filters does not
reveal the panel. On small screens, the panel starts collapsed and expands
inline below the controls.

For demos, collapse the sidebar before presenting. This hides the filter list
only: project names remain in charts and task detail, and all source rows are
still embedded in the HTML. Use build-time redaction for a shareable artefact
that must not contain those projects.

Date presets are relative to the latest date in the loaded exports, not the
current date. The header shows **Data through** separately from the generation
time. The latest day, week or month can be incomplete.

Provider labels are inferred from model-name prefixes:

| Prefix | Provider |
| --- | --- |
| `claude*` | Anthropic |
| `gpt*`, `o1*`, `o3*`, `o4*` | OpenAI |
| `gemini*` | Google |
| `grok*` | xAI |
| Other values | `Other / Unknown` |

These labels help group usage. They are not billing metadata. To extend the
mapping, update `PROVIDER_PREFIX_RULES` in
[`provider_classifier.py`](../provider_classifier.py).

The provider palette is shared by the model ranking, model pricing-efficiency
chart and provider mix. Colours do not change with ranking, date range or
filter selection. Inline provider legends accompany those model charts.

**Model Mix per Top Project** instead uses a distinct colour for each model,
including models from the same provider. It uses Plotly's Alphabet categorical
palette, extended with non-repeating generated swatches beyond 26 models.
Assignments are made from the full exported model list in alphabetical order
and stay fixed through filtering and metric changes. Rebuilding with a
different model set can change the assignments. The model legend is ordered
alphabetically, with space reserved below the axes. Click a legend entry to
hide or show that model's segments in this chart.

Other charts choose palettes according to what colour encodes:

| Data type | Palette | Charts |
| --- | --- | --- |
| Nominal categories | Okabe-Ito, with fixed colours per category | Token categories, work themes and work modes |
| Continuous magnitude | Viridis, from dark purple to yellow | Task-theme-by-model heatmap |
| Ordered categories | Four Viridis samples, from purple to yellow | Reasoning effort: none, low, medium, high |
| One aggregate series | Okabe-Ito blue | Project totals, usage trends and user totals |

The heatmap scale starts at zero and extends to the selected maximum, using an
upper bound of one only for all-zero selections. Its colour bar shows the
current units and scale. Category colours stay fixed when filtered or ranked.
Unknown or unclassified categories retain grey. Warning colours keep their
separate meaning for data-quality messages.
