---
layout: default
title: Compatibility and data format
---

# Compatibility and data format

## Environment

The project requires Python 3.9 or later, `pandas`, and `plotly`.

The extractor reads the local Copilot CLI session store at
`~/.copilot/session-store.db`. GitHub documents the local session store, but
does not document the usage-event schema as a stable public API. The
integration has been validated against Copilot CLI 1.0.79 on Windows.

macOS and Linux support has not been verified against real Copilot CLI data.
The extractor validates the tables and columns it needs and stops with a
clear error if the local store is incompatible.

## Export format

`extract_usage.py` creates one CSV row per session, model, day, and reasoning
effort. The required dashboard columns are:

```text
user, date, project, model, calls, total_tokens
```

Current exports also include token categories, cost coverage, a session ID,
and export metadata. `export_format_version` identifies the CSV layout.
`exported_at` records when the file was written and lets the dashboard handle
overlapping exports deterministically.

Older CSVs continue to load where possible. The dashboard warns when metadata
or optional fields are missing.

## Schema boundary

The extractor currently reads `assistant_usage_events` and `sessions` from
the local session store. The exact table layout can change with Copilot CLI
updates. This project uses a read-only SQLite snapshot and does not modify
the live database.

For implementation details, see `REQUIRED_SCHEMA` and `QUERY` in
[`extract_usage.py`](../extract_usage.py).
