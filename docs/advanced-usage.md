---
layout: default
title: Advanced usage
---

# Advanced usage

## Privacy and sharing

Exports and generated dashboards can contain personal and confidential data:

- The default user label is the operating-system username.
- Project names, model names, dates, and session IDs are retained.
- `--include-task-summary` adds free-text task summaries.
- A dashboard embeds its source rows in the HTML file.

Treat a CSV or generated dashboard as sensitive data. Review it before
sharing it.

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
remain selected.

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
