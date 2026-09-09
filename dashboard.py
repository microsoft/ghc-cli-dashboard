#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
dashboard.py - Combine one or many copilot_usage_*.csv exports (produced by
extract_usage.py) into a single self-contained HTML dashboard.

This is intentionally a static-HTML output (Plotly, embedded JS) so it can be
shared as a file or opened by anyone with zero installs -
no server, no Streamlit, no Python needed to VIEW it (only to generate it).
Project AND model checkboxes let you exclude either from every chart; your
choices are remembered per-browser via localStorage. Note: unchecking a
project/model (or using --exclude-default) only hides it from the current
browser's view - those rows are still present in the generated HTML file
itself. To actually remove data before the file is written, use the
build-time flags --exclude-project (repeatable, exact project name) and/or
--omit-task-summaries (see README for details). Charts show on-chart value
labels (compact, e.g. "154.9M") so you don't need to hover to read them.

Usage:
    python dashboard.py --in "copilot_usage_*.csv" --out usage_dashboard.html
    python dashboard.py --in "\\\\shared\\team-folder\\copilot_usage_*.csv"
    python dashboard.py --in "copilot_usage_*.csv" --exclude-default "Music,D:"
    python dashboard.py --in "copilot_usage_*.csv" --exclude-project "Personal Project" --omit-task-summaries
"""
import argparse
import colorsys
import glob
import json
import os
import sys
from datetime import datetime, timezone
from html import escape as _esc
from pathlib import Path

import numpy as np
import pandas as pd
from plotly.colors import qualitative
from plotly.offline import get_plotlyjs

from provider_classifier import PROVIDER_COLORS, classify_provider

# Columns every input CSV must have for a record to be constructed at all -
# these are read unconditionally (no getattr/default) in build_dashboard().
REQUIRED_COLUMNS = ["user", "date", "project", "model", "calls", "total_tokens"]

# Columns that older ("legacy") extract_usage.py exports may not have. When
# missing, the whole column is back-filled with a documented, safe default
# rather than raising - build_dashboard() already treats these as optional
# (task_summary is handled separately via a has_tasks column-presence check,
# so it's intentionally not defaulted here).
#
# `total_nano_aiu` keeps its long-standing `0.0` default when the column is
# *entirely absent* (a true pre-cost-tracking export) - this is a distinct,
# deliberately-preserved case from "cost data is missing for some rows",
# which is instead tracked via `cost_data_calls` (see below): a 0.0 total
# here with an unknown/zero `cost_data_calls` means "we have no idea what
# this cost", not "this was confirmed free". `input_tokens`/`output_tokens`/
# `cache_read_tokens`/`cache_write_tokens`/`reasoning_tokens`/
# `cost_data_calls` instead default to NaN ("unknown/not reported") rather
# than 0, so a legacy export that never recorded these categories isn't
# silently rendered as "confirmed zero" in the Composition/coverage views -
# see build_dashboard()'s coverage/composition handling and README's
# "Token and cost definitions" section.
OPTIONAL_COLUMN_DEFAULTS = {
    "session_id": None,
    "reasoning_effort": "n/a",
    "total_nano_aiu": 0.0,
    "input_tokens": np.nan,
    "output_tokens": np.nan,
    "cache_read_tokens": np.nan,
    "cache_write_tokens": np.nan,
    "reasoning_tokens": np.nan,
    "cost_data_calls": np.nan,
}

# Numeric columns build_dashboard() reads with int()/float() - validated
# up front so bad data produces one actionable ERROR instead of a confusing
# ValueError/AttributeError deep inside HTML generation, or (worse) silently
# becoming a misleading 0. These must always be present and finite when the
# column exists in the file at all - blank/NaN cells here are rejected, not
# treated as "unknown" (see OPTIONAL_NUMERIC_COLUMNS below for the columns
# where a per-row blank IS a legitimate "not reported" value).
NUMERIC_COLUMNS = ["calls", "total_tokens", "total_nano_aiu"]
INTEGER_NUMERIC_COLUMNS = set(NUMERIC_COLUMNS)

# Numeric columns where a blank/missing per-row value is a legitimate,
# tolerated "unknown/not reported" state (not an error) - unlike
# NUMERIC_COLUMNS above. `cost_data_calls` is the cost-coverage counter
# (see extract_usage.py's "Token/cost definitions" note); the four token
# sub-categories are additional counters that simply weren't tracked by
# every historical export. A value that IS present must still be a finite,
# non-negative whole number - only genuinely blank cells are exempt.
OPTIONAL_NUMERIC_COLUMNS = [
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "cost_data_calls",
]
INTEGER_NUMERIC_COLUMNS |= set(OPTIONAL_NUMERIC_COLUMNS)

DATE_COLUMN = "date"

# --- Export metadata / schema-version compatibility -------------------------
#
# Starting with extract_usage.py's `export_format_version = "2"`, every CSV
# row carries two extra columns: `export_format_version` (a literal version
# string) and `exported_at` (a single, timezone-aware ISO-8601 UTC timestamp
# shared by every row in the file, recording when that *file* was produced).
# A CSV that has neither column is a "version 1" / legacy export - there is
# no literal "1" ever written; version 1 is recognized purely by the absence
# of these columns. This is intentionally lenient (old exports keep working)
# but never silent: see _apply_export_metadata()'s docstring for the exact
# fallback/warning policy. Version "3" additionally adds `cost_data_calls`
# (a cost-coverage counter, not a cost figure itself - see
# extract_usage.py's EXPORT_FORMAT_VERSION comment); its absence is handled
# the same lenient-but-visible way, via OPTIONAL_COLUMN_DEFAULTS above and
# the coverage summary computed in build_dashboard().
LEGACY_EXPORT_FORMAT_VERSION = "1"
KNOWN_EXPORT_FORMAT_VERSIONS = {"1", "2", "3"}

# Columns compared for equality within a duplicate-identity group to decide
# whether duplicate rows are genuinely identical (safe, silent dedup) or a
# real conflict (values differ - see _resolve_duplicates()). Deliberately
# broader than NUMERIC_COLUMNS: also includes every token sub-total and
# `project`, per the requirement to detect conflicts in "calls, token
# columns, cost totals, project/model/date/reasoning values" (model/date/
# reasoning_effort are already part of the identity key itself, so a
# mismatch there would put rows in different groups rather than showing up
# here).
VALUE_CONFLICT_COLUMNS = [
    "calls", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens", "total_nano_aiu",
    "cost_data_calls", "project",
]


def _row_numbers(df: pd.DataFrame, mask: pd.Series) -> list:
    """Map a boolean row mask back to 1-based CSV row numbers (accounting for
    the header row), matching what a user sees opening the file in a text
    editor or spreadsheet app."""
    return (df.index[mask] + 2).tolist()


def _format_rows(rows: list, limit: int = 10) -> str:
    shown = rows[:limit]
    suffix = f" (+{len(rows) - limit} more)" if len(rows) > limit else ""
    return f"{shown}{suffix}"


def _validate_required_columns(df: pd.DataFrame, file_name: str) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        sys.exit(
            f"ERROR: {file_name}: missing required column(s): {', '.join(missing)}.\n"
            f"Expected a CSV produced by extract_usage.py with at least: "
            f"{', '.join(REQUIRED_COLUMNS)}."
        )


def _apply_optional_defaults(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in OPTIONAL_COLUMN_DEFAULTS.items():
        if col not in df.columns:
            df[col] = default
    return df


def _validate_numeric_column(df: pd.DataFrame, col: str, file_name: str, allow_missing: bool = False) -> None:
    """Reject non-numeric, infinite, negative, or fractional values in `col` -
    these are all nonsensical for a token/call/cost/coverage count, and
    coercing them to 0 would silently understate usage.

    `allow_missing=True` (used for OPTIONAL_NUMERIC_COLUMNS - the token
    sub-category counters and `cost_data_calls`) additionally tolerates a
    genuinely blank/NaN cell as a legitimate "not reported by this export"
    value, rather than an error - it is kept as NaN rather than coerced to
    0, so downstream code can tell "confirmed zero" apart from "unknown"
    (see OPTIONAL_COLUMN_DEFAULTS's docstring). A cell that has some
    non-blank-but-garbage value (e.g. the text "not-a-number") is still
    rejected either way - only an originally-empty cell is tolerated."""
    if col not in df.columns:
        return
    originally_blank = df[col].isna()
    numeric = pd.to_numeric(df[col], errors="coerce")
    check_mask = ~originally_blank if allow_missing else pd.Series(True, index=df.index)
    numeric_for_check = numeric.where(check_mask, 0.0)
    bad_mask = check_mask & (~np.isfinite(numeric_for_check.to_numpy(dtype=float)) | (numeric_for_check < 0))
    if bad_mask.any():
        rows = _row_numbers(df, bad_mask)
        sys.exit(
            f"ERROR: {file_name}: column '{col}' has invalid value(s) "
            f"(non-numeric, NaN, infinite, or negative) at CSV row(s) {_format_rows(rows)}."
        )
    if col in INTEGER_NUMERIC_COLUMNS:
        fractional_mask = check_mask & numeric_for_check.mod(1).ne(0)
        if fractional_mask.any():
            rows = _row_numbers(df, fractional_mask)
            sys.exit(
                f"ERROR: {file_name}: column '{col}' has fractional value(s) at CSV "
                f"row(s) {_format_rows(rows)}. Expected whole-number counts."
            )
    df[col] = numeric


def _validate_date_column(df: pd.DataFrame, col: str, file_name: str) -> None:
    if col not in df.columns:
        return
    date_text = df[col].astype("string")
    exact_format = date_text.str.fullmatch(r"\d{4}-\d{2}-\d{2}", na=False)
    parsed = pd.to_datetime(date_text, format="%Y-%m-%d", errors="coerce")
    bad_mask = ~exact_format | parsed.isna()
    if bad_mask.any():
        rows = _row_numbers(df, bad_mask)
        sys.exit(
            f"ERROR: {file_name}: column '{col}' has missing/malformed date value(s) "
            f"at CSV row(s) {_format_rows(rows)}. Expected YYYY-MM-DD (as written by "
            f"extract_usage.py)."
        )


def _validate_file(df: pd.DataFrame, file_name: str) -> pd.DataFrame:
    """Validate one loaded CSV's schema/values before it's combined with any
    other export, so error messages can name the offending file."""
    _validate_required_columns(df, file_name)
    for col in NUMERIC_COLUMNS:
        _validate_numeric_column(df, col, file_name)
    for col in OPTIONAL_NUMERIC_COLUMNS:
        _validate_numeric_column(df, col, file_name, allow_missing=True)
    _validate_date_column(df, DATE_COLUMN, file_name)
    return _apply_optional_defaults(df)


def _file_mtime_utc(file_name: str):
    """Best-effort proxy for "when was this file exported", used only when a
    CSV has no usable `exported_at` column - a real (if imprecise) timestamp,
    unlike parsing/guessing from the file name."""
    return datetime.fromtimestamp(os.path.getmtime(file_name), tz=timezone.utc)


def _apply_export_metadata(df: pd.DataFrame, file_name: str) -> pd.DataFrame:
    """Normalize per-file export metadata and attach an internal, always
    timezone-aware `_export_ts` (+ `_export_file`/`_export_file_basename`)
    column set used purely to order overlapping exports for deduplication -
    never exposed in the final dashboard dataset (dropped again in
    _resolve_duplicates()). `_export_file` keeps the full path/argument as
    given (useful in WARNING messages to locate the file); the same-timestamp
    tiebreak itself uses only `_export_file_basename` (`Path(file_name).name`)
    so it's based on the file *name*, per the documented policy, and doesn't
    depend on which directory (or how it was spelled on the command line) a
    file was passed from.

    Compatibility / fallback policy (never silent):
      - Both `export_format_version` and `exported_at` present: the file's
        `exported_at` values are parsed (assumed/forced to UTC) and used
        directly as `_export_ts`.
      - Both columns missing entirely: this file predates export metadata -
        printed as a WARNING (not a silent default), `_export_ts` falls back
        to the file's OS last-modified time for every row, and
        `export_format_version` is back-filled with the legacy sentinel "1".
      - Exactly ONE of the two columns is present (an inconsistent/unexpected
        export shape - this project always writes both together starting at
        version "2"): a WARNING is printed explicitly calling out the
        mismatch, rather than silently back-filling as if this were an
        ordinary legacy export. `export_format_version` is still back-filled
        with the legacy sentinel "1" when it's the missing one; `_export_ts`
        still falls back to OS last-modified time when `exported_at` is the
        missing one (the file's own `exported_at` values are used otherwise).
      - `export_format_version` present but not one of the versions this
        tool knows about (currently {"1", "2", "3"}): treated best-effort as
        equivalent to the current version (whatever columns are present are
        used), with a WARNING noting the unrecognized value.
    """
    has_version_col = "export_format_version" in df.columns
    has_exported_at_col = "exported_at" in df.columns

    if not has_version_col and not has_exported_at_col:
        print(
            f"WARNING: {file_name}: no export metadata found (missing "
            f"'export_format_version'/'exported_at' columns) - treating as a legacy "
            f"(pre-versioning, schema version '{LEGACY_EXPORT_FORMAT_VERSION}') export. "
            f"Falling back to this file's OS last-modified time to order it against other "
            f"exports for deduplication; see README's export-metadata/compatibility policy."
        )
        df["export_format_version"] = LEGACY_EXPORT_FORMAT_VERSION
        df["_export_ts"] = pd.Timestamp(_file_mtime_utc(file_name))
        df["_export_file"] = file_name
        df["_export_file_basename"] = Path(file_name).name
        return df

    if not has_version_col:
        print(
            f"WARNING: {file_name}: has 'exported_at' but no 'export_format_version' column - "
            f"an inconsistent/unexpected export shape (this project always writes both columns "
            f"together, starting at version '2'). Treating as a legacy (schema version "
            f"'{LEGACY_EXPORT_FORMAT_VERSION}') export for versioning purposes, while still using "
            f"this file's own 'exported_at' values (parsed below) to order it for deduplication."
        )
        df["export_format_version"] = LEGACY_EXPORT_FORMAT_VERSION
    else:
        df["export_format_version"] = df["export_format_version"].astype("string").fillna(LEGACY_EXPORT_FORMAT_VERSION)
        unknown_versions = sorted(set(df["export_format_version"]) - KNOWN_EXPORT_FORMAT_VERSIONS)
        if unknown_versions:
            print(
                f"WARNING: {file_name}: unrecognized export_format_version value(s) "
                f"{unknown_versions} (this tool knows about {sorted(KNOWN_EXPORT_FORMAT_VERSIONS)}) - "
                f"proceeding best-effort using whichever columns are present."
            )

    if not has_exported_at_col:
        print(
            f"WARNING: {file_name}: has 'export_format_version' but no 'exported_at' column - "
            f"falling back to this file's OS last-modified time to order it for deduplication."
        )
        df["_export_ts"] = pd.Timestamp(_file_mtime_utc(file_name))
    else:
        parsed = pd.to_datetime(df["exported_at"], errors="coerce", utc=True)
        missing_mask = parsed.isna()
        if missing_mask.any():
            mtime = _file_mtime_utc(file_name)
            rows = _row_numbers(df, missing_mask)
            print(
                f"WARNING: {file_name}: {int(missing_mask.sum())} row(s) with missing/unparseable "
                f"'exported_at' at CSV row(s) {_format_rows(rows)} - falling back to this file's "
                f"OS last-modified time ({mtime.isoformat()}) for those row(s)."
            )
            parsed = parsed.fillna(pd.Timestamp(mtime))
        df["_export_ts"] = parsed

    df["_export_file"] = file_name
    df["_export_file_basename"] = Path(file_name).name
    return df


def _resolve_duplicates(data: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate rows from overlapping exports using each row's actual
    export timestamp (`_export_ts`, from `_apply_export_metadata`) rather
    than filename sort order.

    Identity policy:
      - Rows with a non-null `session_id` are identified by
        (user, session_id, date, model, reasoning_effort). `session_id` is
        assumed unique per underlying Copilot CLI session, so two rows
        sharing this key are, with high confidence, the same underlying
        record captured by different exports.
      - Rows with a null/missing `session_id` ("legacy" rows - current
        extract_usage.py always sets it) are identified instead by
        (user, project, date, model, reasoning_effort). This key is only
        *inferred* identity: two legacy rows can share it by coincidence
        rather than truly being the same session, so it is deliberately
        NOT treated with the same confidence as the session_id key (this
        is why `project` is part of the legacy key, unlike the session_id
        key - session_id already uniquely pins a session's project).

    Cross-version reconciliation (legacy <-> current), deliberately
    conservative:
      A legacy row and a current (session_id-bearing) row are NEVER
      compared directly by value - they live in disjoint identity schemes
      on purpose, because a legacy row alone can never be *proven* to be a
      particular session. Instead, for every legacy row we ask: across ALL
      current-format rows sharing this legacy row's (user, project, date,
      model, reasoning_effort) dimensions, how many *distinct* session_id
      values exist?
      - Exactly one: there is only one possible session this legacy row
        could be re-reporting, so identity is safe to infer. The legacy
        row is folded into that session's identity group (as if it always
        carried that session_id) and resolved by the normal session_id
        rules above - i.e. the newest/current row wins, silently if values
        agree, or with a conflict WARNING if they don't. This is the one
        and only case a legacy and current row are allowed to merge.
      - Zero: no current row shares these dimensions; the legacy row is
        resolved purely against other legacy rows, as before.
      - Two or more: the dimensions alone can't tell us which of the
        distinct sessions (if any) this legacy row belongs to - merging
        it into any one of them risks silently discarding real data or
        conflating two unrelated sessions. Per policy, the legacy row is
        NOT merged into any of them; ALL rows (the legacy row(s) and every
        distinct current session sharing the dimensions) are retained, and
        a WARNING flags the ambiguity.

    Resolution per identity group with 2+ rows (after the fold-in above):
      - Every VALUE_CONFLICT_COLUMNS-listed column identical across the
        group => same record, duplicated across exports. Keep exactly one:
        the row with the greatest `_export_ts`. Ties (identical timestamps,
        e.g. two files from the same run or clock-resolution collisions)
        are broken deterministically by `_export_file_basename` - the
        lexicographically greatest file *name* (not full path - so the
        tiebreak is stable regardless of which directory a same-named file
        was passed in from) wins; this (and NOT relying on it for anything
        else) is the one documented, narrow use of file name as a
        tiebreaker.
      - Values differ AND identity is session_id-based (including a
        folded-in legacy row): a genuine conflict about the same entity
        (e.g. a later export captured more calls for that session/day, or
        a legacy row disagrees with the current row it was folded into).
        The row with the greatest `_export_ts` (same tie-break) wins, and a
        WARNING names the file/key context and which row won - conflicts
        are reported, never silently hidden.
      - Values differ AND identity is the inferred legacy key: we cannot
        safely tell whether this is a real update to the same session or a
        coincidental collision between two unrelated legacy sessions. ALL
        rows in the group are retained (nothing dropped), and a WARNING
        flags the ambiguity so it can be investigated (e.g. by re-exporting
        with a current extract_usage.py that includes session_id).

    Limitations (documented, not silently papered over): this policy can
    only reconcile a legacy row when the dimensions it carries happen to
    pin down exactly one current session. Two genuinely distinct sessions
    for the same user/project/date/model/reasoning_effort (a realistic
    case - e.g. two separate CLI sessions on the same repo the same day)
    make any legacy row sharing those dimensions permanently ambiguous;
    it is always retained rather than dropped, but may end up double
    counted against whichever of those sessions it actually belongs to.
    The only real fix is re-exporting with a session_id-bearing
    extract_usage.py.
    """
    data = data.reset_index(drop=True)
    has_session = data["session_id"].notna() if "session_id" in data.columns else pd.Series(False, index=data.index)

    def _key_part(col):
        if col not in data.columns:
            return pd.Series("", index=data.index)
        return data[col].astype("string").fillna("\u0000")

    sid_key = (
        "sid\u241f" + _key_part("user") + "\u241f" + _key_part("session_id") + "\u241f"
        + _key_part("date") + "\u241f" + _key_part("model") + "\u241f" + _key_part("reasoning_effort")
    )
    # Dimensions shared by both legacy and current rows (no session_id) -
    # used both as the legacy-only identity key and, below, as the
    # crosswalk that decides whether a legacy row can safely be folded
    # into a single current session's identity group.
    dim_key = (
        _key_part("user") + "\u241f" + _key_part("project") + "\u241f"
        + _key_part("date") + "\u241f" + _key_part("model") + "\u241f" + _key_part("reasoning_effort")
    )
    legacy_key = "legacy\u241f" + dim_key
    data["_identity_key"] = np.where(has_session, sid_key, legacy_key)
    data["_dim_key"] = dim_key

    # --- Cross-version reconciliation --------------------------------
    # For every dimension shared with at least one legacy row, count how
    # many DISTINCT current sessions exist. Exactly one => safe to fold
    # the legacy row(s) into that session's group. Two or more => flag as
    # ambiguous and leave every row (legacy and current) untouched.
    crossover_notes = []
    legacy_mask = ~has_session
    if legacy_mask.any() and has_session.any():
        session_rows = data.loc[has_session]
        dims_with_legacy = set(data.loc[legacy_mask, "_dim_key"])
        session_dims = session_rows[session_rows["_dim_key"].isin(dims_with_legacy)]
        if not session_dims.empty:
            dim_session_counts = session_dims.groupby("_dim_key")["session_id"].nunique()
            unique_dims = set(dim_session_counts[dim_session_counts == 1].index)
            ambiguous_dims = set(dim_session_counts[dim_session_counts > 1].index)

            if unique_dims:
                dim_to_sidkey = (
                    session_dims[session_dims["_dim_key"].isin(unique_dims)]
                    .drop_duplicates("_dim_key")
                    .set_index("_dim_key")["_identity_key"]
                )
                fold_mask = legacy_mask & data["_dim_key"].isin(unique_dims)
                data.loc[fold_mask, "_identity_key"] = data.loc[fold_mask, "_dim_key"].map(dim_to_sidkey)

            for dim in sorted(ambiguous_dims):
                legacy_rows_here = data[(data["_dim_key"] == dim) & legacy_mask]
                if legacy_rows_here.empty:
                    continue
                sample = legacy_rows_here.iloc[0]
                n_sessions = int(dim_session_counts[dim])
                legacy_files = sorted(set(legacy_rows_here["_export_file"]))
                crossover_notes.append(
                    f"  - user={sample.get('user')} project={sample.get('project')} "
                    f"date={sample.get('date')} model={sample.get('model')} "
                    f"reasoning_effort={sample.get('reasoning_effort')}: {len(legacy_rows_here)} "
                    f"legacy row(s) (no session_id) from file(s) {legacy_files} share these "
                    f"dimensions with {n_sessions} DISTINCT current-format sessions - cannot "
                    f"determine which (if any) they duplicate, so ALL rows sharing these "
                    f"dimensions were KEPT (none merged or dropped)."
                )

    value_cols = [c for c in VALUE_CONFLICT_COLUMNS if c in data.columns]

    # Tiebreak helper: within a group that mixes a folded-in legacy row with
    # a true current (session_id-bearing) row, the current row must win
    # regardless of exported_at ordering ("the newest trusted/current row
    # wins" - current format is trusted over legacy by policy, not just by
    # timestamp). Sorting on this first, before _export_ts/_export_file_basename,
    # makes the existing "take the last row" winner logic do that for free;
    # it's a no-op for groups that are purely legacy or purely current
    # (constant value within the group).
    data["_is_current"] = has_session.astype(int)

    keep_mask = pd.Series(True, index=data.index)
    removed = 0
    conflict_notes = []
    ambiguous_notes = []

    for key, group in data.groupby("_identity_key", sort=False):
        if len(group) < 2:
            continue

        identical = True
        if value_cols:
            identical = bool((group[value_cols].nunique(dropna=False) <= 1).all())

        if identical:
            ordered = group.sort_values(["_is_current", "_export_ts", "_export_file_basename"])
            winner = ordered.index[-1]
            drop_idx = [i for i in group.index if i != winner]
            keep_mask.loc[drop_idx] = False
            removed += len(drop_idx)
            continue

        files_involved = sorted(set(group["_export_file"]))
        if key.startswith("sid\u241f"):
            ordered = group.sort_values(["_is_current", "_export_ts", "_export_file_basename"])
            winner_idx = ordered.index[-1]
            drop_idx = [i for i in group.index if i != winner_idx]
            keep_mask.loc[drop_idx] = False
            removed += len(drop_idx)
            winner = data.loc[winner_idx]
            conflict_notes.append(
                f"  - user={winner.get('user')} session_id={winner.get('session_id')} "
                f"date={winner.get('date')} model={winner.get('model')} "
                f"reasoning_effort={winner.get('reasoning_effort')}: {len(group)} row(s) with "
                f"differing values across file(s) {files_involved}; kept the row from "
                f"'{winner['_export_file']}' (exported_at={winner['_export_ts']})."
            )
        else:
            sample = group.iloc[0]
            ambiguous_notes.append(
                f"  - user={sample.get('user')} project={sample.get('project')} "
                f"date={sample.get('date')} model={sample.get('model')} "
                f"reasoning_effort={sample.get('reasoning_effort')}: {len(group)} row(s) share this "
                f"inferred (no session_id) identity across file(s) {files_involved} but have "
                f"differing values - cannot safely tell whether this is the same session re-exported "
                f"or a coincidental collision, so ALL {len(group)} row(s) were KEPT (none dropped)."
            )

    if conflict_notes:
        print(
            "WARNING: resolved conflicting duplicate row(s) sharing the same session_id-based "
            "identity (kept the most recently exported row per key, per file exported_at):\n"
            + "\n".join(conflict_notes)
        )
    if ambiguous_notes:
        print(
            "WARNING: found row(s) sharing an inferred (legacy, no session_id) identity with "
            "differing values - retained ALL of them rather than guessing which is correct:\n"
            + "\n".join(ambiguous_notes)
        )
    if crossover_notes:
        print(
            "WARNING: found legacy (no session_id) row(s) whose (user, project, date, model, "
            "reasoning_effort) dimensions match MORE THAN ONE distinct current-format session - "
            "cannot safely reconcile, retained ALL of them rather than guessing which is correct:\n"
            + "\n".join(crossover_notes)
        )

    result = (
        data.loc[keep_mask]
        .drop(columns=["_identity_key", "_dim_key", "_is_current", "_export_ts", "_export_file", "_export_file_basename"])
        .reset_index(drop=True)
    )
    if removed:
        print(
            f"Note: removed {removed} duplicate row(s) found across overlapping exports "
            f"(kept the copy with the greatest exported_at)."
        )
    return result


def load_data(pattern: str) -> pd.DataFrame:
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"ERROR: no files matched pattern: {pattern}")

    frames = []
    for file_name in files:
        try:
            df = pd.read_csv(file_name)
        except Exception as e:
            sys.exit(f"ERROR: {file_name}: failed to read CSV: {e}")
        df = _validate_file(df, file_name)
        df = _apply_export_metadata(df, file_name)
        frames.append(df)

    data = pd.concat(frames, ignore_index=True)

    # extract_usage.py exports a user's FULL history every run (not just new
    # rows), so overlapping exports (e.g. a weekly re-export) are common when
    # multiple copilot_usage_*.csv files are globbed together. Resolve those
    # overlaps deterministically using each row's actual export timestamp -
    # see _resolve_duplicates()'s docstring for the full identity/conflict
    # policy (files are sorted above only for stable read order/error
    # messages, NOT to decide which duplicate wins).
    data = _resolve_duplicates(data)

    return data


def _json_for_script(obj) -> str:
    """Serialize obj to JSON safe for inline embedding inside a <script> block.

    json.dumps alone is not enough here: the result is spliced directly into
    an HTML <script> element via an f-string, so a data value containing
    "</script>" would terminate the block early (and start a *new*,
    attacker-controlled one), and "<!--"/"-->" sequences can confuse HTML
    parsers mid-script. U+2028/U+2029 are valid in JSON strings but are line
    terminators when they appear literally inside a JS string literal in
    older engines. Escaping '<', '>', '&' and both line separators to their
    \\uXXXX forms neutralizes all of that while leaving the *value* JSON.parse
    would see completely unchanged.
    """
    return (
        json.dumps(obj, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _model_mix_colors(models):
    """Assign distinct categorical swatches, independent of usage ranking."""
    palette = [colour.lower() for colour in qualitative.Alphabet]
    colours = {}
    used = set()
    extra_index = 0
    for index, model in enumerate(sorted(set(models))):
        if index < len(palette):
            colour = palette[index]
        else:
            # Extend instead of cycling the palette when exports contain >26 models.
            while True:
                hue = (extra_index * 0.61803398875) % 1
                saturation = 0.6 + 0.15 * (extra_index % 2)
                value = 0.65 + 0.2 * ((extra_index // 2) % 2)
                rgb = colorsys.hsv_to_rgb(hue, saturation, value)
                colour = "#" + "".join(f"{round(channel * 255):02x}" for channel in rgb)
                extra_index += 1
                if colour not in used:
                    break
        used.add(colour)
        colours[model] = colour
    return colours


def _checkbox_items(order, totals, css_class):
    kind = {"proj-check": "project", "model-check": "model", "provider-check": "provider"}[css_class]
    return "".join(
        f'<div class="filter-item"><label class="proj-item"><input type="checkbox" class="{css_class}" value="{_esc(p, quote=True)}" checked> '
        f'<span class="proj-name">{_esc(p)}</span></label>'
        f'<button class="filter-only" onclick="selectOnly(\'{kind}\', {index})" '
        f'aria-label="Show only {_esc(p, quote=True)}">Only</button>'
        f'<span class="filter-blocked" hidden>Excluded by provider</span></div>'
        for index, p in enumerate(order)
    )


def _redact_projects(data: pd.DataFrame, exclude_projects: list, exclude_default_projects: list):
    """Permanently drop rows for exact project names *before* any derived
    output (project/model orders, totals, RAW JSON, checkboxes, insights) is
    computed - unlike --exclude-default (a browser-display-only default),
    matching rows never reach the generated HTML at all, so they can't be
    recovered by unchecking a box or reading the file's source.

    Also purges the same names from `exclude_default_projects` so an
    excluded project can't be re-embedded via the DEFAULT_EXCLUDED_PROJECTS
    localStorage seed even if it was also passed to --exclude-default.

    Returns (data, exclude_default_projects) - both possibly narrowed/copied.
    """
    if not exclude_projects:
        return data, exclude_default_projects

    exclude_set = set(exclude_projects)
    present_projects = set(data["project"].unique())
    matched = sorted(exclude_set & present_projects)
    unmatched = sorted(exclude_set - present_projects)

    if unmatched:
        print(
            f"WARNING: --exclude-project name(s) not found in the dataset (no rows removed for "
            f"these - check for typos/whitespace/case): {unmatched}"
        )

    mask = data["project"].isin(exclude_set)
    removed_rows = int(mask.sum())
    data = data.loc[~mask].reset_index(drop=True)

    if removed_rows:
        print(
            f"Redacted {removed_rows} row(s) across {len(matched)} project(s) via --exclude-project "
            f"(removed before build; not recoverable in the output file): {matched}"
        )
    elif not unmatched:
        # exclude_projects was non-empty but somehow neither matched nor unmatched
        # (e.g. an empty/blank string slipped through) - still surface it.
        print("WARNING: --exclude-project matched 0 rows; the dataset was not modified.")

    exclude_default_projects = [p for p in exclude_default_projects if p not in exclude_set]

    if data.empty:
        sys.exit(
            "ERROR: --exclude-project removed every row from the dataset - nothing left to build a "
            "dashboard from. Check the project name(s) passed to --exclude-project."
        )

    return data, exclude_default_projects


def _nullable_int(value):
    """Convert a possibly-NaN/None numeric value to a plain `int`, or `None`
    if it's missing/unknown. Used for the token sub-category counters and
    `cost_data_calls`, where "not reported by this export" is a distinct,
    meaningful state from `0` ("confirmed to be exactly zero") - see
    OPTIONAL_COLUMN_DEFAULTS's docstring. Emitting `None` (JSON `null`)
    rather than `0` lets the dashboard's JS tell the two apart instead of
    silently treating every legacy row as if it had zero cache/reasoning
    usage or zero cost-data coverage."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        # pd.isna raises TypeError for types it cannot evaluate (for example a
        # list). Those are never missing values, so fall through to int().
        pass
    return int(value)


def build_dashboard(data: pd.DataFrame, out_path: str, title: str,
                     exclude_default_projects: list, exclude_default_models: list,
                     storage_key: str, exclude_projects: list = None,
                     omit_task_summaries: bool = False):
    data, exclude_default_projects = _redact_projects(data, exclude_projects or [], exclude_default_projects)

    if omit_task_summaries and "task_summary" in data.columns:
        data = data.drop(columns=["task_summary"])
        print(
            "Task summaries omitted from build (--omit-task-summaries): the task_summary column "
            "was removed before the dashboard was generated - Work Patterns/Task Detail will show "
            "their existing no-summary state."
        )

    has_tasks = "task_summary" in data.columns
    has_effort = "reasoning_effort" in data.columns

    records = []
    for r in data.itertuples():
        rec = {
            "project": r.project,
            "model": r.model,
            # Derived, display-only field: a best-effort provider inferred
            # from the raw model name at build time - see
            # provider_classifier.py's module docstring. This is heuristic
            # inference, NOT authoritative billing metadata, and the raw
            # `model` value above is left completely unchanged.
            "provider": classify_provider(r.model),
            "date": r.date,
            "user": r.user,
            "calls": int(r.calls),
            "total_tokens": int(r.total_tokens),
            "total_nano_aiu": float(getattr(r, "total_nano_aiu", 0) or 0),
            # Coverage counter for the total_nano_aiu figure above - `null`
            # means "this export predates cost-coverage tracking" (unknown),
            # NOT "zero calls had cost data" (see extract_usage.py's
            # "Token/cost definitions" note and OPTIONAL_COLUMN_DEFAULTS).
            "cost_data_calls": _nullable_int(getattr(r, "cost_data_calls", None)),
            # Token sub-categories: independent counters, NOT already folded
            # into total_tokens (= input_tokens + output_tokens only). `null`
            # means this export never recorded the category, not "zero used".
            "input_tokens": _nullable_int(getattr(r, "input_tokens", None)),
            "output_tokens": _nullable_int(getattr(r, "output_tokens", None)),
            "cache_read_tokens": _nullable_int(getattr(r, "cache_read_tokens", None)),
            "cache_write_tokens": _nullable_int(getattr(r, "cache_write_tokens", None)),
            "reasoning_tokens": _nullable_int(getattr(r, "reasoning_tokens", None)),
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

    # Provider is derived (not a CSV column) - classify once here from the
    # raw `model` value (see provider_classifier.py) and group on that
    # derived Series. Ordered by token share like project/model above, so
    # the filter panel lists the heaviest-usage provider first; the OTHER
    # UNKNOWN bucket is included like any other provider whenever present -
    # it is never dropped or hidden by default.
    provider_series = data["model"].map(classify_provider)
    providers_by_tokens = data.groupby(provider_series)["total_tokens"].sum().sort_values(ascending=False)
    provider_order = providers_by_tokens.index.tolist()

    n_users = data["user"].nunique()

    # All of these are embedded verbatim inside a <script> block below, so they
    # go through _json_for_script (not plain json.dumps) - see its docstring.
    raw_json = _json_for_script(records)
    project_order_json = _json_for_script(project_order)
    model_order_json = _json_for_script(model_order)
    model_mix_colors_json = _json_for_script(_model_mix_colors(model_order))
    provider_order_json = _json_for_script(provider_order)
    provider_colors_json = _json_for_script(PROVIDER_COLORS)
    exclude_default_projects_json = _json_for_script(exclude_default_projects)
    exclude_default_models_json = _json_for_script(exclude_default_models)
    # No CLI flag seeds a default-excluded provider set (out of scope for
    # this feature) - the Provider panel always starts with every provider
    # selected; see README's "Project, model & provider filters" section.
    exclude_default_providers_json = _json_for_script([])
    storage_key_project_json = _json_for_script(f"copilot_usage_excluded_projects::{storage_key}")
    storage_key_model_json = _json_for_script(f"copilot_usage_excluded_models::{storage_key}")
    # Deliberately a distinct/separate localStorage key from project/model -
    # see requirement to keep provider selection state independent.
    storage_key_provider_json = _json_for_script(f"copilot_usage_excluded_providers::{storage_key}")
    storage_key_metric_json = _json_for_script(f"copilot_usage_metric::{storage_key}")
    storage_key_datefilter_json = _json_for_script(f"copilot_usage_datefilter::{storage_key}")
    storage_key_trend_json = _json_for_script(f"copilot_usage_trend_granularity::{storage_key}")
    storage_key_sidebar_json = _json_for_script(f"copilot_usage_sidebar_collapsed::{storage_key}")

    project_checkbox_items = _checkbox_items(project_order, projects_by_tokens, "proj-check")
    model_checkbox_items = _checkbox_items(model_order, models_by_tokens, "model-check")
    provider_checkbox_items = _checkbox_items(provider_order, providers_by_tokens, "provider-check")

    plotly_js = get_plotlyjs()
    title_html = _esc(title)
    effort_palette_note = (
        "<p class='hint'>Viridis follows configured effort from none (purple) to high (yellow). "
        "Grey marks unrecorded or unrecognised effort.</p>"
    )
    overview_secondary_card = (
        "<div class='card'><span class='info-icon' title=\"Sums the current metric per user account across selected projects/models/dates. Useful when this export covers more than one person.\">?</span><div id='fig_user' style='height:400px;'></div></div>"
        if n_users > 1
        else "<div class='card'><span class='info-icon' title=\"Sums the current metric by reasoning-effort level (none/low/medium/high). Higher reasoning effort makes a model think more internally before responding, which increases both token usage and cost.\">?</span><div id='fig_effort' style='height:400px;'></div>" + effort_palette_note + "</div>"
    )
    value_effort_card = (
        "<div class='card full'><span class='info-icon' title=\"Sums the current metric by reasoning-effort level (none/low/medium/high) across selected projects/models/dates. Higher reasoning effort makes a model 'think' more internally before responding, which increases both token usage and cost.\">?</span><div id='fig_effort' style='height:380px;'></div>" + effort_palette_note + "</div>"
        if n_users > 1
        else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_html}</title>
<script>{plotly_js}</script>
<style>
  :root {{
    --accent: #2f6feb; --accent-dark: #1a4fc4; --good: #1a7f37; --warn: #9a6700;
    --bg: #f4f4f4; --card-bg: #ffffff; --border: #e5e7eb; --text: #111827; --muted: #59636e;
    --shadow: none;
    --shadow-hover: 0 1px 2px rgba(20,30,50,0.05);
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Segoe UI Variable", "Segoe UI", -apple-system, Roboto, sans-serif;
    margin: 0; padding: 0; background: var(--bg); color: var(--text); line-height: 1.4;
    font-variant-numeric: tabular-nums;
  }}
  .hero {{
    background: var(--card-bg); padding: 20px 32px 14px; border-bottom: 1px solid var(--border);
  }}
  .hero h1 {{ margin: 0 0 4px; font-size: 26px; font-weight: 650; letter-spacing: -0.01em; }}
  .hero .subtitle {{ color: var(--muted); margin: 0; font-size: 13px; }}
  .nav-pills {{
    display: flex; gap: 6px; flex-wrap: wrap; margin-top: 16px;
  }}
  .nav-pills a {{
    color: var(--accent-dark); text-decoration: none; font-size: 13px; font-weight: 600;
    padding: 6px 12px; border-radius: 6px; background: #f6f8fb;
    border: 1px solid var(--border); transition: background 0.15s;
  }}
  .nav-pills a:hover {{ background: #eef3ff; }}
  .page {{ padding: 20px 32px 48px; max-width: 1600px; margin: auto; }}
  h2 {{ font-size: 16px; margin: 0 0 2px; }}
  .section {{ margin-bottom: 8px; scroll-margin-top: 14px; }}
  .section-head {{ display: flex; align-items: baseline; gap: 10px; margin: 28px 0 10px; }}
  #sec-overview > .section-head {{ margin-top: 0; }}
  .section-head h2 {{ font-size: 19px; font-weight: 650; }}
  .section-desc {{ color: var(--muted); font-size: 13px; margin: 0 0 16px; max-width: 780px; }}
  .trend-heading {{ justify-content: space-between; flex-wrap: wrap; }}
  .trend-heading .trend-toggle {{ margin: 0; }}
  .layout {{ display: flex; gap: 24px; align-items: flex-start; }}
  .sidebar-shell {{ flex: 0 0 260px; min-width: 0; display: flex; flex-direction: column; gap: 12px; position: sticky; top: 14px; max-height: calc(100vh - 28px); }}
  .sidebar {{ min-height: 0; display: flex; flex-direction: column; gap: 12px; overflow-y: auto; }}
  .layout.sidebar-collapsed .sidebar-shell {{ flex-basis: 44px; }}
  .layout.sidebar-collapsed .sidebar {{ display: none; }}
  .layout.sidebar-collapsed #sidebar-toggle-label {{ display: none; }}
  .side-panel {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }}
  .side-panel h2 {{ margin-top: 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
  .sidebar-toggle {{
    display: flex; align-items: center; justify-content: center; gap: 6px;
    padding: 8px 10px; min-height: 44px; flex-shrink: 0; border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg);
    cursor: pointer; font-size: 12.5px; font-weight: 650; color: var(--muted); box-shadow: var(--shadow);
  }}
  .sidebar-toggle:hover {{ background: #f6f8fb; color: var(--text); }}
  .main {{ flex: 1; min-width: 0; }}
  .kpi-row {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; margin-bottom: 12px; }}
  /* Aggregate KPIs share one blue accent; chart palettes follow the encoded data type. */
  .kpi {{
    background: var(--card-bg); border: 1px solid var(--border); border-top: 3px solid #0072b2; border-radius: 10px;
    padding: 14px 18px; min-width: 0;
  }}
  .kpi-value {{ font-size: 36px; font-weight: 700; letter-spacing: -0.01em; }}
  .kpi-label {{ font-size: 13px; color: var(--muted); margin-bottom: 4px; font-weight: 600; }}
  .kpi-note {{ font-size: 12px; color: var(--muted); margin-top: 6px; }}
  .scope-summary {{ color: var(--muted); font-size: 13px; margin-bottom: 12px; overflow-wrap: anywhere; }}
  .filter-controls {{ display: flex; gap: 12px; align-items: center; margin-top: 10px; flex-wrap: wrap; }}
  .filter-summary {{ flex: 1; min-width: 150px; font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }}
  .filter-search {{ display: block; width: 100%; padding: 8px; margin-bottom: 10px; border: 1px solid var(--border); border-radius: 6px; font: inherit; font-size: 14px; }}
  .filter-item {{ display: flex; align-items: center; flex-wrap: wrap; gap: 4px; }}
  .filter-item[hidden], [hidden] {{ display: none !important; }}
  .filter-item .proj-item {{ flex: 1; min-width: 0; }}
  .filter-only, .filter-more {{ border: 0; background: transparent; color: var(--accent-dark); padding: 8px; cursor: pointer; font: inherit; font-size: 13px; }}
  .filter-more {{ margin-top: 6px; }}
  .filter-blocked {{ width: 100%; padding-left: 22px; color: var(--muted); font-size: 12px; }}
  .provider-excluded .proj-item {{ color: var(--muted); }}
  .filter-empty {{ font-size: 13px; color: var(--muted); }}
  .provider-legend {{ display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 12px; color: var(--muted); margin: 6px 0; }}
  .provider-key {{ display: inline-flex; align-items: center; gap: 5px; }}
  .provider-swatch {{ width: 10px; height: 10px; display: inline-block; border-radius: 2px; flex-shrink: 0; }}
  button:focus-visible, a:focus-visible, input:focus-visible, summary:focus-visible {{ outline: 2px solid var(--accent-dark); outline-offset: 3px; }}
  summary {{ cursor: pointer; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .card {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 14px;
    margin-bottom: 0; min-width: 0; box-shadow: var(--shadow); transition: box-shadow 0.15s;
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
  .proj-item {{ display: flex; align-items: center; gap: 6px; padding: 6px 2px; font-size: 14px; cursor: pointer; border-radius: 4px; }}
  .proj-item:hover {{ background: #f6f8fb; }}
  .proj-name {{ flex: 1; overflow-wrap: anywhere; }}
  .proj-tok {{ color: var(--muted); font-size: 11px; }}
  .hint {{ font-size: 11.5px; color: var(--muted); margin-top: 10px; }}
  .token-glossary-title {{ margin: 0 0 8px; font-size: 15px; }}
  .token-glossary-intro {{ margin: 0 0 14px; font-size: 13px; color: var(--text); }}
  .token-glossary-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin: 0 0 14px;
  }}
  .token-glossary-item {{
    background: #f6f8fb; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  }}
  .token-glossary-item.highlight {{ background: #eef3ff; border-color: #c9dcff; }}
  .token-glossary-item dt {{ font-weight: 700; font-size: 13px; margin-bottom: 4px; }}
  .token-glossary-item dd {{ margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.45; }}
  .token-glossary-note {{ margin: 0; font-size: 11.5px; color: var(--muted); }}
  .toolbar {{
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 16px; margin-bottom: 14px; box-shadow: var(--shadow);
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
  .date-toggle button.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .date-range-hint {{ font-size: 12px; color: var(--muted); font-weight: 600; }}
  .trend-toggle {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 12px 36px; }}
  .trend-toggle button {{ padding: 6px 14px; border: 1px solid var(--border); border-radius: 999px; background: #f6f8fb; cursor: pointer; font-size: 13px; font-weight: 600; }}
  .trend-toggle button:hover {{ background: #e9eef7; }}
  .trend-toggle button.active {{ background: var(--accent); color: white; border-color: var(--accent); }}
  .card {{ position: relative; }}
  .info-icon {{
    position: absolute; top: 10px; right: 12px; width: 20px; height: 20px; border-radius: 50%;
    background: #eef1f6; color: var(--muted); font-size: 12px; font-weight: 700; line-height: 20px;
    text-align: center; cursor: help; z-index: 5; user-select: none;
  }}
  .info-icon:hover {{ background: #d8dee7; color: var(--text); }}
  .insight-bar {{
    display: flex; flex-direction: column; gap: 8px; margin: 0 0 14px; max-width: 100%;
  }}
  .insight {{
    display: flex; gap: 10px; align-items: flex-start; font-size: 13px; background: #eef4ff;
    border-left: 3px solid var(--accent); border-radius: 6px; padding: 9px 12px; color: #1b3a7a;
  }}
  .insight.warn {{ background: #fff8e6; border-left-color: var(--warn); color: #6b4d00; }}
  .insight.good {{ background: #edf9f0; border-left-color: var(--good); color: #14532d; }}
  .insight b {{ font-weight: 700; }}
  .footer-note {{ margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--border); font-size: 12px; color: var(--muted); }}
  .chart-help {{ font-size: 13px; color: var(--muted); margin-bottom: 8px; overflow-wrap: anywhere; }}
  .chart-help p {{ margin: 8px 0; }}
  @media (max-width: 1100px) {{
    .grid {{ grid-template-columns: minmax(0, 1fr); }}
    .kpi-value {{ font-size: 30px; }}
  }}
  @media (max-width: 760px) {{
    .hero {{ padding: 16px; }}
    .hero h1 {{ font-size: 22px; }}
    .page {{ padding: 16px; }}
    .layout {{ display: block; }}
    .sidebar-shell {{ position: static; max-height: none; margin-bottom: 16px; }}
    .sidebar-toggle {{ align-self: flex-start; }}
    .layout.sidebar-collapsed #sidebar-toggle-label {{ display: inline; }}
    .kpi-row {{ grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .kpi:last-child {{ grid-column: 1 / -1; }}
    .kpi {{ padding: 12px; }}
    .kpi-value {{ font-size: 28px; }}
    .section-desc, .trend-toggle {{ margin-left: 0; }}
    .card {{ padding: 10px; }}
    .toolbar {{ padding: 12px; }}
    .date-range-hint {{ flex-basis: 100%; }}
    .metric-toggle {{ margin-top: 8px; }}
    button, .nav-pills a {{ min-height: 44px; }}
    .table-filter {{ min-width: 0; flex-wrap: wrap; }}
    .table-filter input {{ min-width: 0; width: 100%; }}
    .token-glossary-grid {{ grid-template-columns: minmax(0, 1fr); }}
    .proj-item {{ min-height: 44px; }}
  }}
</style>
</head>
<body>
  <div class="hero">
    <h1>{title_html}</h1>
    <p class="subtitle"><span id="data-freshness"></span> &middot; Generated {datetime.now():%Y-%m-%d %H:%M} &middot; Local session-store exports</p>
    <div class="nav-pills">
      <a href="#sec-overview">Overview</a>
      <a href="#sec-trends">Trends</a>
      <a href="#sec-value">Pricing efficiency</a>
      <a href="#sec-patterns">Work Patterns</a>
      <a href="#sec-composition">Composition</a>
      <a href="#sec-detail">Task Detail</a>
    </div>
  </div>
  <div class="page">
  <div class="layout sidebar-collapsed" id="layout">
    <div class="sidebar-shell" id="sidebar-shell">
      <button class="sidebar-toggle" id="sidebar-toggle" onclick="toggleSidebar()" aria-controls="filter-panel" aria-expanded="false" aria-label="Show filters" title="Show filters">
        <span id="sidebar-toggle-icon" aria-hidden="true">&raquo;</span><span id="sidebar-toggle-label">Show filters</span>
      </button>
      <aside class="sidebar" id="filter-panel" aria-label="Usage filters">
      <div class="side-panel" id="proj-panel">
        <h2>Projects</h2>
        <input class="filter-search" id="project-search" type="search" aria-label="Search projects" placeholder="Search projects" oninput="searchFilters('project', this.value)">
        <div class="proj-buttons">
          <button onclick="setAll('project', true)">Select all</button>
          <button onclick="setAll('project', false)">Select none</button>
        </div>
        <div id="proj-list">{project_checkbox_items}</div>
        <p id="project-empty" class="filter-empty" hidden>No matching projects. Clear the search to see all.</p>
        <button class="filter-more" id="project-more" onclick="showMoreFilters('project')">Show more</button>
      </div>
      <div class="side-panel" id="model-panel">
        <h2>Models</h2>
        <input class="filter-search" id="model-search" type="search" aria-label="Search models" placeholder="Search models" oninput="searchFilters('model', this.value)">
        <div class="proj-buttons">
          <button onclick="setAll('model', true)">Select all</button>
          <button onclick="setAll('model', false)">Select none</button>
        </div>
        <div id="model-list">{model_checkbox_items}</div>
        <p id="model-empty" class="filter-empty" hidden>No matching models. Clear the search to see all.</p>
        <button class="filter-more" id="model-more" onclick="showMoreFilters('model')">Show more</button>
      </div>
      <div class="side-panel" id="provider-panel">
        <h2>Providers</h2>
        <div class="proj-buttons">
          <button onclick="setAll('provider', true)">Select all</button>
          <button onclick="setAll('provider', false)">Select none</button>
        </div>
        <div id="provider-list">{provider_checkbox_items}</div>
        <p id="provider-empty" class="filter-empty" hidden>No matching providers.</p>
        <button class="filter-more" id="provider-more" onclick="showMoreFilters('provider')">Show more</button>
        <details class="hint"><summary>How provider filters work</summary>Provider is inferred from the model name, not billing metadata. Excluding a provider excludes all its models. Project, model, provider and date filters apply together. Select all/none applies to the whole group, including search-hidden items. Only selects one item in that group without changing other filters.</details>
      </div>
      <div class="hint">Your selections are remembered in this browser for this dashboard file.</div>
      </aside>
    </div>
    <main class="main" id="dashboard-main">

      <div class="section" id="sec-overview">
        <div class="section-head"><h2>Overview</h2></div>
        <div class="toolbar" id="usage-toolbar">
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
          </div>
          <div class="filter-controls">
            <span id="filter-summary" class="filter-summary" role="status"></span>
            <button class="table-action" onclick="resetFilters()" title="Include all projects, models, providers and dates, and clear filter searches">Reset filters</button>
          </div>
        </div>
        <div class="kpi-row" id="kpi-row"></div>
        <div id="scope-summary" class="scope-summary"></div>
        <div id="insight-overview" class="insight-bar"></div>

        <div id="sec-trends" class="section">
          <div class="section-head trend-heading">
            <h2>Usage over time</h2>
            <div class="trend-toggle">
              <span class="metric-label">Group by:</span>
              <button id="trend-day" onclick="setTrendGranularity('day')">Day</button>
              <button id="trend-week" onclick="setTrendGranularity('week')">Week</button>
              <button id="trend-month" onclick="setTrendGranularity('month')">Month</button>
            </div>
          </div>
          <div class="card">
            <span class="info-icon" title="Usage for the selected projects, models and dates. Weeks start on Monday; months use calendar months. The latest period may be incomplete.">?</span>
            <div id="fig_trend" style="height:300px;"></div>
          </div>
          <div id="insight-trends" class="insight-bar"></div>
        </div>

        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Sums the current metric (tokens or estimated cost) per project across every selected model and the chosen date range, then shows the top 15 projects ranked highest to lowest. Bar length = total value for that project; hover a bar for the exact figure.">?</span>
            <div id="fig_project" style="height:430px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Models ranked by selected tokens or estimated cost. Each bar shows its value and share of the current selection. Hover a bar for the full model name and exact value.">?</span>
            <div class="provider-legend" id="model-provider-legend" aria-label="Provider colours"></div>
            <div id="fig_model" style="height:400px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Each slice is one AI PROVIDER's share of the current metric (tokens or cost) - Anthropic, OpenAI, Google, xAI, etc. - inferred heuristically from each row's model name at build time (not official billing metadata; see README). Models that don't match a known provider prefix are grouped under 'Other / Unknown', which stays visible whenever any of its rows are selected. Uses the same project/model/provider/date filters as every other chart on this page.">?</span>
            <div id="fig_provider" style="height:400px;"></div>
          </div>
          {overview_secondary_card}
        </div>
      </div>

      <div class="section" id="sec-value">
        <div class="section-head"><h2>Cost &amp; pricing efficiency</h2></div>
        <p class="section-desc">Which models deliver the most tokens per dollar spent, and how reasoning effort (a setting, not a model choice) drives cost up. Value here means <b>pricing efficiency</b>, not output quality &mdash; see each chart's <span title="hover the ? icons on the charts below for the full caveat">(?)</span> for details. Cost figures depend on <code>total_nano_aiu</code> coverage being complete for the selected calls &mdash; see the coverage KPI and any warning banner below before trusting a $0 or "cheapest" result.</p>
        <div id="insight-coverage" class="insight-bar"></div>
        <div id="insight-value" class="insight-bar"></div>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Tokens received per US dollar of estimated list-price cost (total tokens \u00f7 estimated cost), summed across your currently selected projects/models/dates. Higher bars = more tokens for the same spend. This is a raw PRICING-EFFICIENCY ratio only - it does not measure output quality, accuracy, or how many tokens a model actually needs to complete a task well. Hover a bar for its call count. A model marked * also has calls with incomplete cost coverage that are excluded from this ratio.">?</span>
            <div class="provider-legend" id="value-provider-legend" aria-label="Provider colours"></div>
            <div id="fig_value" style="height:420px;"></div>
          </div>
          {value_effort_card}
        </div>
      </div>

      <div class="section" id="sec-patterns">
        <div class="section-head"><h2>Work patterns</h2></div>
        <p class="section-desc">A transparent, rule-based view of the kinds of work represented in task summaries &mdash; useful for spotting shifts between exploration, planning, execution, and review. These are inferred themes, not measures of productivity or individual performance.</p>
        <div id="work-patterns-note" class="hint" style="margin:0 0 16px 36px;"></div>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="Groups task summaries into inferred themes using transparent keyword rules, then sums the current metric by theme over the selected day/week/month buckets. Tasks that do not match a rule remain Other.">?</span>
            <div id="fig_theme_time" style="height:420px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Shows the current metric for inferred task themes split by model. This can indicate which models are being used for exploration, analysis, planning, execution, or review work.">?</span>
            <p class="hint">Viridis shading: purple = lower usage, yellow = higher usage in this selection.</p>
            <div id="fig_theme_model" style="height:420px;"></div>
          </div>
          <div class="card">
            <span class="info-icon" title="Shows the balance of inferred work modes. Exploration includes learning and analysis; execution covers building and implementation; planning and review/support are shown separately.">?</span>
            <div id="fig_work_mode" style="height:420px;"></div>
          </div>
        </div>
      </div>

      <div class="section" id="sec-composition">
        <div class="section-head"><h2>Composition</h2></div>
        <p class="section-desc">How your top projects break down by model, and how tokens break down by category &mdash; useful for spotting projects that lean heavily on one (possibly expensive) model, or heavy cache/reasoning usage that isn't visible in the headline "Total tokens" figure.</p>
        <div class="grid">
          <div class="card full">
            <span class="info-icon" title="The same top-15 projects as 'Top Projects' above, split into model segments. This chart uses a distinct categorical colour per model, independent of the provider colours elsewhere. The mapping stays fixed when filtering or switching metrics. Click a model in the legend to hide or show its segments.">?</span>
            <p class="hint">Colour identifies the individual model in this chart. Use the model legend below.</p>
            <div id="fig_stack" style="height:440px;"></div>
          </div>
          <details class="card full token-glossary">
            <summary class="token-glossary-title">What do these token categories mean?</summary>
            <p class="token-glossary-intro">Every call to a model is billed in a few different "flavours" of token. The two that trip people up most are <strong>cache read</strong> and <strong>cache write</strong> &mdash; both are about prompt caching, a mechanism models use to avoid re-processing context (like a long system prompt, file contents, or earlier conversation turns) from scratch on every single call.</p>
            <dl class="token-glossary-grid">
              <div class="token-glossary-item">
                <dt>Input</dt>
                <dd>New content sent to the model this call that it hasn't seen cached &mdash; your prompt, fresh file/tool output, etc. Billed at the standard (highest) input rate.</dd>
              </div>
              <div class="token-glossary-item">
                <dt>Output</dt>
                <dd>The model's visible reply back to you.</dd>
              </div>
              <div class="token-glossary-item highlight">
                <dt>Cache read</dt>
                <dd>Context reused from a previous call's cache instead of being reprocessed &mdash; e.g. the same system prompt or earlier turns of a long-running session. <strong>Cheaper than fresh input</strong> (often a fraction of the price), so a high cache-read count is usually a sign of an efficient, well-reused session, not something to worry about.</dd>
              </div>
              <div class="token-glossary-item highlight">
                <dt>Cache write</dt>
                <dd>Context written into the cache for the <strong>first time</strong> so it's available for cache reads on later calls. Usually carries a small one-time premium over ordinary input tokens &mdash; that upfront cost is repaid if the cached context actually gets reused (as cache reads) later in the session.</dd>
              </div>
              <div class="token-glossary-item">
                <dt>Reasoning</dt>
                <dd>Tokens the model spends on internal step-by-step reasoning before it writes its visible reply. Not shown in the output text, but still generated and billed.</dd>
              </div>
            </dl>
            <p class="token-glossary-note">These five categories are <strong>independent, additive counters</strong> &mdash; not five slices of one pie. "Total tokens" elsewhere in this dashboard is input + output <em>only</em>; cache-read, cache-write, and reasoning are layered on top and won't necessarily move in lockstep with it or with cost (since each category is billed at a different rate). Cache read/write only appear for models and CLI versions that support prompt caching, so seeing zero for a given model isn't unusual or a data problem.</p>
          </details>
          <div class="card full">
            <span class="info-icon" title="Total input, output, cache-read, cache-write, and reasoning tokens across your currently selected projects/models/dates. These are FIVE INDEPENDENT counters reported by Copilot CLI's usage log, not five parts of one pie: 'Total tokens' (shown elsewhere in this dashboard, and used for the Tokens metric/KPI) is defined as input + output ONLY - cache-read, cache-write, and reasoning tokens are separate, additive categories layered on top, and are not guaranteed to sum to Total tokens or to move in lockstep with estimated cost (which weights each category differently - e.g. cached input is typically billed cheaper per token than fresh input). Always shown as raw token counts, even when the Tokens/Cost toggle above is set to cost, since these categories are not individually costed in this export.">?</span>
            <div id="fig_token_composition" style="height:380px;"></div>
            <div id="composition-note" class="hint" style="margin-top:8px;"></div>
          </div>
        </div>
      </div>

      <div class="section" id="sec-detail">
        <div class="section-head"><h2>Task detail</h2></div>
        <p class="section-desc">The individual tasks driving the totals above, ranked by the current chart metric. Search across all tasks, sort any column, or copy the displayed top 20 rows.</p>
        <div id="table-wrap"></div>
      </div>

      <div class="footer-note">Copilot CLI Token Usage Dashboard &middot; generated locally from session-store.db exports &middot; all figures are estimates based on published list pricing.</div>
    </main>
  </div>
  </div>

<script>
const RAW = {raw_json};
// nano_aiu -> USD: verified against GitHub's published per-token Copilot pricing
// (1 AI credit = $0.01; total_nano_aiu / 1e9 = credits, so /1e11 = USD).
const NANO_AIU_TO_USD = 1e-11;
// The five independent token sub-category counters that make up a "complete"
// per-row breakdown - see has_token_categories below and TOKEN_CATEGORIES
// further down (which drives the Token Composition chart itself).
const TOKEN_CATEGORY_KEYS = ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"];
RAW.forEach(r => {{
  r.estimated_cost = (r.total_nano_aiu || 0) * NANO_AIU_TO_USD;
  // Cost-coverage classification for this row, from cost_data_calls (a
  // COVERAGE COUNT, not a cost figure - see extract_usage.py's "Token/cost
  // definitions" note): "unknown" = this export predates coverage tracking
  // entirely (cost_data_calls is null); "none"/"partial"/"full" otherwise
  // compare it against `calls`. A row's estimated_cost can be legitimately
  // 0 in ANY of these states, so cost coverage must be checked separately -
  // never infer "confirmed free" just because estimated_cost is 0.
  r.cost_coverage = r.cost_data_calls === null || r.cost_data_calls === undefined
    ? "unknown"
    : r.cost_data_calls <= 0 ? "none"
    : r.cost_data_calls >= r.calls ? "full"
    : "partial";
  // Whether this row has a COMPLETE token-category breakdown (input/output/
  // cache-read/cache-write/reasoning) - legacy exports never recorded these,
  // so absence must not be read as "zero cache/reasoning tokens used". A row
  // is only included in the composition totals when ALL FIVE categories are
  // present; a row with only some categories present (e.g. a partially
  // migrated/hand-edited export) is a genuinely incomplete breakdown and
  // would otherwise silently understate whichever categories are missing -
  // it is excluded here just like a row with none of them, and counted in
  // excludedCalls by computeTokenComposition() below.
  r.has_token_categories = TOKEN_CATEGORY_KEYS.every(k => r[k] !== null && r[k] !== undefined);
}});

const PROJECT_ORDER = {project_order_json};
const MODEL_ORDER = {model_order_json};
const PROVIDER_ORDER = {provider_order_json};
// One canonical provider palette, independent of rank or filter state.
// Models inherit the provider already classified in Python, without re-inferring it.
const PROVIDER_COLORS = {provider_colors_json};
const MODEL_COLORS = Object.fromEntries(RAW.map(r => [r.model, PROVIDER_COLORS[r.provider] || "#8c959f"]));
const MODEL_MIX_COLORS = {model_mix_colors_json};
// Okabe-Ito separates nominal categories; Viridis encodes ordered values.
// These mappings are keyed by meaning, never by the current ranking.
const OKABE_ITO = {{
  blue: "#0072b2", sky: "#56b4e9", green: "#009e73",
  orange: "#e69f00", purple: "#cc79a7", vermilion: "#d55e00", yellow: "#f0e442",
}};
const UNKNOWN_COLOR = "#8c959f";
const EFFORT_COLORS = {{
  none: "#440154", low: "#31688e", medium: "#35b779", high: "#fde725",
  "n/a": UNKNOWN_COLOR,
}};
const HAS_TASKS = {str(has_tasks).lower()};
const STORAGE_KEY_PROJECT = {storage_key_project_json};
const STORAGE_KEY_MODEL = {storage_key_model_json};
const STORAGE_KEY_PROVIDER = {storage_key_provider_json};
const STORAGE_KEY_METRIC = {storage_key_metric_json};
const STORAGE_KEY_DATEFILTER = {storage_key_datefilter_json};
const STORAGE_KEY_TREND = {storage_key_trend_json};
const STORAGE_KEY_SIDEBAR = {storage_key_sidebar_json};
const DEFAULT_EXCLUDED_PROJECTS = {exclude_default_projects_json};
const DEFAULT_EXCLUDED_MODELS = {exclude_default_models_json};
const DEFAULT_EXCLUDED_PROVIDERS = {exclude_default_providers_json};

// "Last N days" is computed relative to the most recent date in the loaded export(s),
// not the real-world today - the CSV may have been generated some time ago.
const ALL_DATES_SORTED = RAW.map(r => r.date).filter(Boolean).sort();
const GLOBAL_MAX_DATE = ALL_DATES_SORTED.length ? ALL_DATES_SORTED[ALL_DATES_SORTED.length - 1] : null;

// Single dispatch table for every checkbox "kind" (project/model/provider) -
// each independent filter dimension is described once here (its localStorage
// key, its default-excluded seed, its full ordered value list, and the CSS
// class marking its checkboxes) instead of copy-pasted project/model
// ternaries scattered across loadExcluded/saveExcluded/setAll/the change
// listener below. Adding a future filter dimension only requires one more
// entry here (plus its own checkbox markup) - see README's "Project, model &
// provider filters" section for the resulting AND semantics across kinds.
const FILTER_KINDS = {{
  project: {{ storageKey: STORAGE_KEY_PROJECT, defaultExcluded: DEFAULT_EXCLUDED_PROJECTS, order: PROJECT_ORDER, checkboxClass: "proj-check" }},
  model: {{ storageKey: STORAGE_KEY_MODEL, defaultExcluded: DEFAULT_EXCLUDED_MODELS, order: MODEL_ORDER, checkboxClass: "model-check" }},
  provider: {{ storageKey: STORAGE_KEY_PROVIDER, defaultExcluded: DEFAULT_EXCLUDED_PROVIDERS, order: PROVIDER_ORDER, checkboxClass: "provider-check" }},
}};
const filterSearch = {{ project: "", model: "", provider: "" }};
const filterLimits = {{ project: 8, model: 8, provider: 8 }};
const MODEL_PROVIDERS = new Map(RAW.map(r => [r.model, r.provider]));

function searchFilters(kind, value) {{
  filterSearch[kind] = value.trim().toLowerCase();
  filterLimits[kind] = 8;
  updateFilterList(kind);
}}

function showMoreFilters(kind) {{
  filterLimits[kind] += 10;
  updateFilterList(kind);
}}

function updateFilterList(kind) {{
  let matched = 0;
  document.querySelectorAll("." + FILTER_KINDS[kind].checkboxClass).forEach(cb => {{
    const matches = cb.value.toLowerCase().includes(filterSearch[kind]);
    cb.closest(".filter-item").hidden = !matches || ++matched > filterLimits[kind];
  }});
  const more = document.getElementById(kind + "-more");
  more.hidden = matched <= filterLimits[kind];
  more.textContent = "Show more (" + Math.max(0, matched - filterLimits[kind]) + " remaining)";
  document.getElementById(kind + "-empty").hidden = matched > 0;
}}

function selectOnly(kind, index) {{
  const order = FILTER_KINDS[kind].order;
  saveExcluded(kind, new Set(order.filter((_, i) => i !== index)));
  render();
}}

function resetFilters() {{
  Object.keys(FILTER_KINDS).forEach(kind => {{
    saveExcluded(kind, new Set());
    filterSearch[kind] = "";
    filterLimits[kind] = 8;
    const input = document.getElementById(kind + "-search");
    if (input) input.value = "";
  }});
  localStorage.setItem(STORAGE_KEY_DATEFILTER, "all");
  render();
}}

function updateFilterState(excludedProviders) {{
  document.querySelectorAll(".model-check").forEach(cb => {{
    const blocked = excludedProviders.has(MODEL_PROVIDERS.get(cb.value));
    const item = cb.closest(".filter-item");
    cb.disabled = blocked;
    item.classList.toggle("provider-excluded", blocked);
    item.querySelector(".filter-blocked").hidden = !blocked;
    item.querySelector(".filter-only").disabled = blocked;
  }});
  Object.keys(FILTER_KINDS).forEach(updateFilterList);
  const parts = Object.entries(FILTER_KINDS).map(([kind, cfg]) => {{
    const excluded = loadExcluded(kind);
    const count = cfg.order.filter(value => excluded.has(value)).length;
    return count ? `${{count}} ${{kind}}${{count === 1 ? "" : "s"}} excluded` : "";
  }}).filter(Boolean);
  document.getElementById("filter-summary").textContent = parts.length
    ? parts.join(" · ") + (excludedProviders.size ? " (including their models)" : "")
    : "All projects, models and providers";
}}

function renderProviderLegend(id, providers) {{
  document.getElementById(id).innerHTML = Object.keys(PROVIDER_COLORS)
    .filter(provider => providers.has(provider))
    .map(provider => `<span class="provider-key"><span class="provider-swatch" aria-hidden="true" style="background:${{PROVIDER_COLORS[provider]}}"></span>${{escapeHtml(provider)}}</span>`)
    .join("");
}}

function loadExcluded(kind) {{
  const cfg = FILTER_KINDS[kind];
  const stored = localStorage.getItem(cfg.storageKey);
  if (stored !== null) {{
    try {{ return new Set(JSON.parse(stored)); }} catch (e) {{ /* fall through */ }}
  }}
  return new Set(cfg.defaultExcluded);
}}

function saveExcluded(kind, excluded) {{
  localStorage.setItem(FILTER_KINDS[kind].storageKey, JSON.stringify(Array.from(excluded)));
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

// Cost-data coverage summary for a set of (already filtered) rows. Never
// divides by zero (guards totalCalls === 0), and treats "unknown" (legacy,
// pre-coverage-tracking rows) as its own bucket rather than folding it into
// either "confirmed" or "confirmed missing" - see the "cost_coverage"
// classification set on each RAW row above for what each state means.
function computeCostCoverage(rows) {{
  let totalCalls = 0, confirmedCalls = 0, unknownCalls = 0;
  for (const r of rows) {{
    const calls = r.calls || 0;
    totalCalls += calls;
    if (r.cost_coverage === "unknown") {{ unknownCalls += calls; continue; }}
    confirmedCalls += Math.min(r.cost_data_calls || 0, calls);
  }}
  const knownCalls = totalCalls - unknownCalls;
  const missingKnownCalls = Math.max(0, knownCalls - confirmedCalls);
  const pct = (n) => totalCalls > 0 ? (100 * n / totalCalls) : 0;
  return {{
    totalCalls, confirmedCalls, missingKnownCalls, unknownCalls,
    pctConfirmed: pct(confirmedCalls), pctMissingKnown: pct(missingKnownCalls), pctUnknown: pct(unknownCalls),
    isComplete: totalCalls > 0 && confirmedCalls === totalCalls,
  }};
}}

const TOKEN_CATEGORIES = [
  {{ key: "input_tokens", label: "Input", color: OKABE_ITO.blue }},
  {{ key: "output_tokens", label: "Output", color: OKABE_ITO.green }},
  {{ key: "cache_read_tokens", label: "Cache read", color: OKABE_ITO.sky }},
  {{ key: "cache_write_tokens", label: "Cache write", color: OKABE_ITO.orange }},
  {{ key: "reasoning_tokens", label: "Reasoning", color: OKABE_ITO.purple }},
];

// Token-category composition summary for a set of (already filtered) rows.
// Only rows carrying a breakdown (has_token_categories) contribute to the
// totals - rows from exports that never recorded these categories are
// counted separately (excludedCalls) rather than silently treated as zero.
function computeTokenComposition(rows) {{
  const totals = {{}};
  TOKEN_CATEGORIES.forEach(c => {{ totals[c.key] = 0; }});
  let includedCalls = 0, excludedCalls = 0;
  for (const r of rows) {{
    if (!r.has_token_categories) {{ excludedCalls += (r.calls || 0); continue; }}
    includedCalls += (r.calls || 0);
    TOKEN_CATEGORIES.forEach(c => {{ totals[c.key] += (r[c.key] || 0); }});
  }}
  return {{ totals, includedCalls, excludedCalls }};
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

function fmtHeadline(n) {{
  if (Math.abs(n) < 1000) return fmt(n);
  const unit = Math.abs(n) >= 1e9 ? [1e9, "B"] : Math.abs(n) >= 1e6 ? [1e6, "M"] : [1e3, "K"];
  return (n / unit[0]).toLocaleString(undefined, {{ maximumFractionDigits: 2 }}) + unit[1];
}}

function setPressed(button, pressed) {{
  button.classList.toggle("active", pressed);
  button.setAttribute("aria-pressed", String(pressed));
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
    if (button) setPressed(button, value === granularity);
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

// Neutralizes CSV/TSV "formula injection": if pasted or opened in Excel/Sheets,
// a cell whose text starts with =, +, -, @ (or a leading tab/CR that could shift
// which cell a formula lands in) can be interpreted as an active formula/DDE
// command instead of plain text. Prefixing with a leading apostrophe forces
// spreadsheet apps to treat the cell as literal text; embedded tab/newline
// characters are flattened so a single field can't inject extra TSV columns
// or rows. Only used for the copy-to-clipboard text - the underlying
// row.project/row.task values used for search/sort/filter are untouched.
function sanitizeForSpreadsheet(value) {{
  const s = String(value ?? "").replace(/[\\t\\r\\n]/g, " ");
  return /^[=+\\-@]/.test(s) ? "'" + s : s;
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
    ...getDisplayedTaskRows().map(row => [sanitizeForSpreadsheet(row.project), sanitizeForSpreadsheet(row.task), fmt(row.tokens), fmtCurrency(row.cost)])
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
  "Explore & learn": OKABE_ITO.blue,
  "Analyze & decide": OKABE_ITO.sky,
  "Plan & organize": OKABE_ITO.orange,
  "Build & implement": OKABE_ITO.green,
  "Review & communicate": OKABE_ITO.purple,
  "Other": UNKNOWN_COLOR,
}};
const WORK_MODE_COLORS = {{
  Exploration: OKABE_ITO.blue, Execution: OKABE_ITO.green, Planning: OKABE_ITO.orange,
  "Review/support": OKABE_ITO.purple, Other: UNKNOWN_COLOR,
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

const chartLayouts = new Map();
let chartRevision = 0;
function drawChart(id, traces, layout, config) {{
  // Plotly keeps zoom and pie-legend state until the selected data changes.
  layout = {{ ...layout, uirevision: chartRevision }};
  chartLayouts.set(id, {{ layout, config }});
  const element = document.getElementById(id);
  if (id === "fig_stack") {{
    const legendColumns = Math.max(1, Math.floor((element.clientWidth - 80) / 220));
    const legendHeight = Math.ceil(traces.length / legendColumns) * 24 + 20;
    element.style.height = (500 + legendHeight) + "px";
    layout = {{ ...layout, margin: {{ ...layout.margin, b: 150 + legendHeight }} }};
  }}
  const narrow = element.clientWidth < 500;
  const title = {{ ...layout.title, font: {{ size: narrow ? 14 : 16 }} }};
  if (narrow && title.text) {{
    title.text = title.text.replace(/(.{{1,38}})(?:\\s+|$)/g, "$1<br>").replace(/<br>$/, "");
  }}
  const margin = {{ t: narrow ? 90 : 65, b: 70, l: 65, r: 30, ...layout.margin }};
  const yaxis = {{ ...layout.yaxis, automargin: true }};
  const xaxis = {{ ...layout.xaxis, automargin: true }};
  if (narrow) {{
    margin.l = Math.min(margin.l, 115);
    margin.r = 25;
    const horizontal = traces.find(trace => trace.orientation === "h");
    if (horizontal) {{
      margin.r = Math.min((layout.margin || {{}}).r || 60, 100);
      yaxis.automargin = false;
      yaxis.title = undefined;
      yaxis.tickvals = horizontal.y;
      yaxis.ticktext = horizontal.y.map(label => {{
        const text = String(label);
        return escapeHtml(text.length > 16 ? text.slice(0, 15) + "..." : text);
      }});
    }}
  }}
  const legend = {{ orientation: "h", x: 0, y: -0.3, ...layout.legend }};
  return Plotly.react(id, traces, {{
    ...layout, title, margin, xaxis, yaxis, legend,
    autosize: true, width: element.clientWidth,
    font: {{ family: "Segoe UI, sans-serif", size: 12, color: "#374151" }},
    paper_bgcolor: "#ffffff", plot_bgcolor: "#ffffff",
  }}, config);
}}

function render() {{
  chartRevision += 1;
  const excludedProjects = loadExcluded("project");
  const excludedModels = loadExcluded("model");
  const excludedProviders = loadExcluded("provider");
  document.querySelectorAll(".proj-check").forEach(cb => {{ cb.checked = !excludedProjects.has(cb.value); }});
  document.querySelectorAll(".model-check").forEach(cb => {{ cb.checked = !excludedModels.has(cb.value); }});
  document.querySelectorAll(".provider-check").forEach(cb => {{ cb.checked = !excludedProviders.has(cb.value); }});
  updateFilterState(excludedProviders);
  document.getElementById("data-freshness").textContent = GLOBAL_MAX_DATE
    ? "Data through " + GLOBAL_MAX_DATE : "No dated usage available";

  const metric = getMetric();
  const valKey = metric === "cost" ? "estimated_cost" : "total_tokens";
  const fmtVal = metric === "cost" ? fmtCurrency : fmtCompact;
  const unitLabel = metric === "cost" ? "estimated cost ($)" : "tokens";
  // Longer, explicit axis titles so units are unambiguous at a glance (not just in hover text)
  const valAxisTitle = metric === "cost" ? "Estimated USD (list price)" : "Input + output tokens";
  setPressed(document.getElementById("metric-tokens"), metric === "tokens");
  setPressed(document.getElementById("metric-cost"), metric === "cost");

  const dateFilter = getDateFilter();
  const cutoffDateStr = computeCutoffDate(dateFilter);
  ["7", "14", "30", "90", "all"].forEach(v => {{
    const btn = document.getElementById("date-" + v);
    if (btn) setPressed(btn, dateFilter === v);
  }});
  const dateHintEl = document.getElementById("date-range-hint");
  if (dateHintEl) {{
    dateHintEl.textContent = dateFilter === "all"
      ? "All exported dates"
      : ((cutoffDateStr || "?") + " \u2192 " + (GLOBAL_MAX_DATE || "?") + " (relative to latest export date)");
  }}

  // Single choke point for every project/model/provider/date filter - every
  // KPI, chart, insight, trend, value-for-money, work-pattern view, task
  // table, and composition chart below reads exclusively from `filtered`
  // (never RAW directly), and each condition here is independent (AND, not
  // OR): a row must pass ALL FOUR to be included. In particular, a model
  // whose own checkbox is still checked is still excluded if its inferred
  // provider is unchecked - see the Provider panel's hint text.
  const filtered = RAW.filter(r => !excludedProjects.has(r.project) && !excludedModels.has(r.model) && !excludedProviders.has(r.provider) && (!cutoffDateStr || (r.date && r.date >= cutoffDateStr)));

  // KPIs (always show both tokens and cost, regardless of chart metric toggle)
  const totalTokens = sum(filtered, "total_tokens");
  const totalCost = sum(filtered, "estimated_cost");
  const totalCalls = sum(filtered, "calls");
  const coverage = computeCostCoverage(filtered);
  const coverageLabel = totalCalls > 0 ? Math.round(coverage.pctConfirmed) + "%" : "n/a";
  const coverageWarn = totalCalls > 0 && !coverage.isComplete;
  const coverageStyle = coverageWarn ? ' style="color:var(--warn);"' : "";
  const nProjects = new Set(filtered.map(r => r.project)).size;
  const nModels = new Set(filtered.map(r => r.model)).size;
  const nUsers = new Set(filtered.map(r => r.user)).size;
  const dates = filtered.map(r => r.date).filter(Boolean).sort();
  const dateRange = dates.length ? (dates[0] + " to " + dates[dates.length - 1]) : "No matching dates";
  document.getElementById("scope-summary").textContent = dates.length
    ? `${{dateRange}} · ${{nProjects}} projects · ${{nModels}} models · ${{nUsers}} user${{nUsers === 1 ? "" : "s"}}`
    : "No matching usage. Adjust the date range or reset filters.";
  document.getElementById("kpi-row").innerHTML = `
    <div class="kpi">
      <div class="kpi-label">Total tokens (input+output)</div>
      <div class="kpi-value">${{fmtHeadline(totalTokens)}}</div>
      <details class="kpi-note"><summary>Exact total &amp; definition</summary>${{fmt(totalTokens)}} tokens. This does NOT include cache-read, cache-write, or reasoning tokens. See Composition for those separate counters.</details>
    </div>
    <div class="kpi">
      <div class="kpi-label">Est. cost (list price)</div>
      <div class="kpi-value">${{fmtCurrency(totalCost)}}</div>
      <div class="kpi-note">Estimate only, not your invoice.</div>
      <details class="kpi-note"${{coverageStyle}}${{coverageWarn ? " open" : ""}}>
        <summary>Cost data coverage: ${{coverageLabel}}</summary>
        ${{fmt(coverage.confirmedCalls)}} of ${{fmt(totalCalls)}} calls confirmed.
        ${{fmt(coverage.missingKnownCalls)}} missing. ${{fmt(coverage.unknownCalls)}} calls have UNKNOWN coverage (older exports).
        ${{coverageWarn ? "Incomplete coverage UNDERSTATES true cost." : "Plan allowances and discounts are not reflected."}}
      </details>
    </div>
    <div class="kpi">
      <div class="kpi-label">Model calls</div><div class="kpi-value">${{fmt(totalCalls)}}</div>
      <div class="kpi-note">API calls in the current selection</div>
    </div>
  `;

  // Top projects
  const byProject = groupSum(filtered, r => r.project, valKey);
  const topProjects = Array.from(byProject.entries()).sort((a, b) => b[1] - a[1]).slice(0, 15);
  drawChart("fig_project", [{{
    x: topProjects.map(p => p[1]), y: topProjects.map(p => p[0]),
    type: "bar", orientation: "h", marker: {{ color: OKABE_ITO.blue }},
    text: topProjects.map(p => fmtVal(p[1])), textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{y}}: %{{x:,}} " + unitLabel + "<extra></extra>",
  }}], {{
    title: {{ text: "Top Projects by " + (metric === "cost" ? "Estimated Cost" : "Total Tokens") }},
    xaxis: {{ title: {{ text: valAxisTitle }} }},
    yaxis: {{ title: {{ text: "Project (top 15 by " + (metric === "cost" ? "cost" : "tokens") + ")" }}, autorange: "reversed" }},
    margin: {{ l: 260, r: 60 }},
  }}, {{ responsive: true }});

  // Ranked models stay readable when the selection contains many models.
  const byModel = groupSum(filtered, r => r.model, valKey);
  const modelEntries = Array.from(byModel.entries()).sort((a, b) => b[1] - a[1]);
  const modelTotal = sum(filtered, valKey);
  document.getElementById("fig_model").style.height = Math.max(320, modelEntries.length * 30 + 130) + "px";
  drawChart("fig_model", [{{
    x: modelEntries.map(m => m[1]), y: modelEntries.map(m => m[0]), type: "bar", orientation: "h",
    marker: {{ color: modelEntries.map(m => MODEL_COLORS[m[0]]) }},
    text: modelEntries.map(m => fmtVal(m[1]) + " (" + (modelTotal > 0 ? (100 * m[1] / modelTotal).toFixed(0) : 0) + "%)"),
    textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{y}}: %{{x:,}} " + unitLabel + "<extra></extra>",
  }}], {{
    title: {{ text: metric === "cost" ? "Estimated cost by model" : "Token usage by model" }},
    showlegend: false, yaxis: {{ autorange: "reversed" }}, margin: {{ l: 160, r: 100 }},
    xaxis: {{ title: {{ text: metric === "cost" ? "Estimated USD" : "Input + output tokens" }} }},
  }}, {{ responsive: true }});

  // Provider Mix - same `filtered` array (so it always reflects the current
  // project/model/provider/date selection, including the Provider panel
  // itself), grouped by the `provider` field derived once at build time by
  // provider_classifier.py (never recomputed here). Uses PROVIDER_COLORS
  // (fixed per provider name), also inherited by the model-level charts.
  // "Other / Unknown" is rendered like any other provider - it is
  // only absent from the chart when there are literally zero matching rows.
  //
  // A provider whose aggregate is exactly zero draws no pie slice and no
  // label - most likely in Cost mode, where legacy exports carry no cost
  // data at all and default to 0. The legend is therefore kept ON for this
  // chart: it is the only remaining place a
  // zero-valued provider - notably "Other / Unknown" - stays visible. If
  // EVERY selected provider is zero there is no pie to draw at all, so an
  // explicit no-data message naming those providers replaces it.
  const byProvider = groupSum(filtered, r => r.provider, valKey);
  const providerEntries = Array.from(byProvider.entries()).sort((a, b) => b[1] - a[1]);
  renderProviderLegend("model-provider-legend", new Set(byProvider.keys()));
  const providerTotal = providerEntries.reduce((acc, p) => acc + p[1], 0);
  const providerAllZero = providerEntries.length > 0 && providerTotal <= 0;
  const providerEmptyText = "No " + (metric === "cost" ? "estimated cost" : "token") + " data for the selected providers<br>("
    + providerEntries.map(p => p[0]).join(", ") + ")"
    + (metric === "cost" ? "<br>These rows have no recorded cost - switch to Tokens to see them." : "");
  drawChart("fig_provider", providerAllZero ? [] : [{{
    labels: providerEntries.map(p => p[0]), values: providerEntries.map(p => p[1]), type: "pie", hole: 0.4,
    marker: {{ colors: providerEntries.map(p => PROVIDER_COLORS[p[0]] || "#8c959f") }},
    textinfo: "percent", textposition: "inside",
    texttemplate: providerEntries.map(p => providerTotal > 0 && p[1] / providerTotal >= 0.05 ? "%{{percent}}" : ""),
    customdata: providerEntries.map(p => fmtVal(p[1])),
    hovertemplate: "%{{label}}: %{{value:,}} " + unitLabel + " (%{{percent}})<extra></extra>",
  }}], {{
    title: {{ text: metric === "cost" ? "Cost share by provider (inferred)" : "Token share by provider (inferred)" }},
    showlegend: true,
    annotations: providerAllZero
      ? [{{ text: providerEmptyText, showarrow: false, x: 0.5, y: 0.5, xref: "paper", yref: "paper", align: "center" }}]
      : [],
  }}, {{ responsive: true }});
  // Test-only hooks (mirrors the existing window.__debugValueEntries
  // pattern below) - let the Node DOM harness/pytest assert on the actual
  // filtered/aggregated data without needing a real Plotly renderer.
  window.__debugProviderEntries = providerEntries;
  window.__debugProviderAllZero = providerAllZero;
  window.__debugFilteredCount = filtered.length;

  // Overview insights: call out the top project and top model in plain language
  const insightOverview = document.getElementById("insight-overview");
  if (insightOverview) {{
    const bits = [];
    if (topProjects.length) {{
      const selectedTotal = sum(filtered, valKey);
      const topShare = selectedTotal > 0 ? (100 * topProjects[0][1] / selectedTotal).toFixed(0) : 0;
      bits.push(`<span><b>${{escapeHtml(topProjects[0][0])}}</b> accounts for <b>${{topShare}}%</b> of selected ${{unitLabel}}.</span>`);
    }}
    if (modelEntries.length) {{
      const selectedTotal = sum(filtered, valKey);
      const topModelShare = selectedTotal > 0 ? (100 * modelEntries[0][1] / selectedTotal).toFixed(0) : 0;
      bits.push(`<span>Leading model: <b>${{escapeHtml(modelEntries[0][0])}}</b> (${{topModelShare}}%).</span>`);
    }}
    insightOverview.innerHTML = bits.length ? '<div class="insight"><span>' + bits.join(" ") + '</span></div>' : "";
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
  const peakValue = dateEntries.reduce((peak, d) => Math.max(peak, d[1]), 0);
  drawChart("fig_trend", [{{
    x: dateEntries.map(d => d[0]), y: dateEntries.map(d => d[1]), type: "scatter", mode: "lines+markers+text",
    line: {{ color: OKABE_ITO.blue, width: 2.5 }}, marker: {{ size: 6 }}, fill: "tozeroy", fillcolor: "rgba(0,114,178,0.10)",
    text: dateEntries.map((d, i) => i === dateEntries.length - 1 || d[1] === peakValue ? fmtVal(d[1]) : ""),
    textposition: dateEntries.map((d, i) => i === dateEntries.length - 1 ? "top left" : "top center"),
    cliponaxis: false,
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
        ? `<div class="insight"><span>&#128640;</span><span>Biggest ${{trendUnit}} increase: <b>${{escapeHtml(dateEntries[maxJumpIdx][0])}}</b> rose to <b>${{fmtVal(dateEntries[maxJumpIdx][1])}}</b> (up ${{fmtVal(maxJump)}} from the previous ${{trendUnit}}).</span></div>`
        : `<div class="insight"><span>&#128200;</span><span>Usage held roughly steady across the selected date range - no single large ${{trendUnit}} spike.</span></div>`;
    }} else {{
      insightTrends.innerHTML = `<div class="insight"><span>&#8505;</span><span>Not enough ${{trendUnit}}s in the current selection to show a trend - widen the date range or choose a finer grouping.</span></div>`;
    }}
  }}

  // Model mix per top project (stacked)
  const topProjectNames = topProjects.map(p => p[0]);
  const modelsSet = new Set(filtered.filter(r => topProjectNames.includes(r.project)).map(r => r.model));
  const stackTraces = Array.from(modelsSet).sort().map(model => {{
    const perProject = topProjectNames.map(proj => sum(filtered.filter(r => r.project === proj && r.model === model), valKey));
    return {{
      name: model, x: topProjectNames, y: perProject, type: "bar",
      marker: {{ color: MODEL_MIX_COLORS[model], line: {{ color: "#ffffff", width: 1 }} }},
      text: perProject.map(v => v > 0 ? fmtVal(v) : ""), textposition: "inside", insidetextanchor: "middle",
      hovertemplate: "%{{x}}<br>" + model + ": %{{y:,}} " + unitLabel + "<extra></extra>",
    }};
  }});
  drawChart("fig_stack", stackTraces, {{
    barmode: "stack", title: {{ text: "Model Mix per Top Project (stacked by model)" }},
    xaxis: {{ title: {{ text: "Project (top 15 by " + (metric === "cost" ? "cost" : "tokens") + ")" }}, tickangle: -35 }},
    yaxis: {{ title: {{ text: valAxisTitle }} }},
    legend: {{ orientation: "h", x: 0, y: 0, yref: "container", yanchor: "bottom", traceorder: "normal", entrywidth: 220 }},
  }}, {{ responsive: true }});

  // Token category composition - ALWAYS raw token counts (not cost), since these five
  // categories are independent counters that are not individually costed in this export.
  // See TOKEN_CATEGORIES/computeTokenComposition() and the card's info-icon for the full
  // "these don't sum to total_tokens or to cost" caveat.
  const composition = computeTokenComposition(filtered);
  drawChart("fig_token_composition", [{{
    x: TOKEN_CATEGORIES.map(c => c.label), y: TOKEN_CATEGORIES.map(c => composition.totals[c.key]),
    type: "bar", marker: {{ color: TOKEN_CATEGORIES.map(c => c.color) }},
    text: TOKEN_CATEGORIES.map(c => fmtCompact(composition.totals[c.key])), textposition: "outside", cliponaxis: false,
    hovertemplate: "%{{x}}: %{{y:,}} tokens<extra></extra>",
  }}], {{
    title: {{ text: "Token categories (independent counters)" }},
    xaxis: {{ title: {{ text: "Token category" }} }},
    yaxis: {{ title: {{ text: "Tokens (always raw counts)" }} }},
  }}, {{ responsive: true }});
  const compositionNote = document.getElementById("composition-note");
  if (compositionNote) {{
    compositionNote.innerHTML = composition.excludedCalls > 0
      ? `${{fmt(composition.includedCalls)}} of ${{fmt(composition.includedCalls + composition.excludedCalls)}} calls in this selection have a token-category breakdown; the remaining ${{fmt(composition.excludedCalls)}} come from an export that never recorded these categories and are excluded from the totals above (not counted as zero).`
      : `All ${{fmt(composition.includedCalls)}} calls in this selection have a token-category breakdown.`;
  }}

  // Value for money: tokens per dollar by model (aggregate, not per-row average, so heavy
  // users of a model don't get diluted/inflated by one-off outlier rows). This is a pricing
  // ratio, not a quality measure - see the info-icon tooltip on the card for the full caveat.
  // Numerator/denominator safety: total_tokens and estimated_cost are both aggregated PER
  // ROW (over that row's `calls`), so a row's cost coverage can only be trusted to cover its
  // own tokens as a whole - a "partial" row (some but not all of its calls have confirmed
  // cost) cannot be safely split into a "confirmed" slice of tokens vs cost without assuming
  // an even distribution across calls that isn't guaranteed. To avoid inflating tok/$ with
  // tokens whose matching cost isn't actually confirmed, the ratio is built ONLY from rows
  // with cost_coverage === "full" (every call in that row has confirmed cost) - "none"/
  // "partial"/"unknown" rows are excluded from both the numerator and the denominator
  // entirely, never just one side. Models are only ranked here when they have SOME full-
  // coverage rows with confirmed cost (modelCost > 0), which also rules out a divide-by-zero
  // in the tpd ratio below. A model can still be ranked while its OVERALL cost coverage
  // (across every one of its rows, not just the full-coverage ones used in the ratio) is
  // <100% - in that case the ratio is accurate for the confirmed subset it's computed from,
  // but is flagged with a coverage note since it may not represent that model's full usage.
  const modelRows = new Map();
  for (const r of filtered) {{
    if (!modelRows.has(r.model)) modelRows.set(r.model, []);
    modelRows.get(r.model).push(r);
  }}
  const fullCoverageRows = filtered.filter(r => r.cost_coverage === "full");
  const modelTokens = groupSum(fullCoverageRows, r => r.model, "total_tokens");
  const modelCost = groupSum(fullCoverageRows, r => r.model, "estimated_cost");
  const modelCalls = groupSum(fullCoverageRows, r => r.model, "calls");
  const valueEntries = Array.from(modelTokens.keys())
    .filter(m => (modelCost.get(m) || 0) > 0)
    .map(m => {{
      const modelCoverage = computeCostCoverage(modelRows.get(m) || []);
      return {{
        model: m, tpd: modelTokens.get(m) / modelCost.get(m), calls: modelCalls.get(m) || 0,
        coverageComplete: modelCoverage.isComplete,
      }};
    }})
    .sort((a, b) => b.tpd - a.tpd);
  // Test-only introspection hook (not used by any production UI code): lets
  // the DOM test harness assert on the computed ranking - e.g. that a model
  // with zero confirmed cost across every one of its calls is excluded here
  // entirely (never shown with an infinite/undefined tok/$ ratio) - without
  // needing to parse rendered chart markup.
  window.__debugValueEntries = valueEntries;
  drawChart("fig_value", [{{
    x: valueEntries.map(v => v.tpd), y: valueEntries.map(v => v.model + (v.coverageComplete ? "" : " *")),
    type: "bar", orientation: "h", marker: {{ color: valueEntries.map(v => MODEL_COLORS[v.model]) }},
    text: valueEntries.map(v => fmtCompact(v.tpd) + " tok/$" + (v.coverageComplete ? "" : " *")),
    textposition: "outside", cliponaxis: false,
    hovertemplate: valueEntries.map(v => (v.model + ": %{{x:,.0f}} tokens per $ spent (" + fmt(v.calls) + " calls)" +
      (v.coverageComplete ? "" : " (this model also has some rows with incomplete cost data - ratio above is computed only from this model's rows with fully confirmed cost, and may not reflect its full usage)")) + "<extra></extra>"),
  }}], {{
    title: {{ text: "Pricing efficiency by model" }},
    xaxis: {{ title: {{ text: "Tokens per estimated USD (confirmed calls)" }} }},
    yaxis: {{ title: {{ text: "Model (tokens per dollar)" }}, autorange: "reversed" }},
    margin: {{ l: 160, r: 140 }},
  }}, {{ responsive: true }});
  renderProviderLegend("value-provider-legend", new Set(valueEntries.map(v => MODEL_PROVIDERS.get(v.model))));

  // Coverage insight: a visible warning whenever ANY call in the current selection lacks
  // confirmed cost data, so a $0/"cheap" cost figure is never mistaken for a confirmed one.
  const insightCoverage = document.getElementById("insight-coverage");
  if (insightCoverage) {{
    if (totalCalls === 0) {{
      insightCoverage.innerHTML = "";
    }} else if (coverage.isComplete) {{
      insightCoverage.innerHTML = `<div class="insight good"><span>&#9989;</span><span>Cost data is confirmed for <b>100%</b> of the ${{fmt(totalCalls)}} calls in the current selection - the Est. cost figures above should be reliable estimates.</span></div>`;
    }} else {{
      const parts = [];
      if (coverage.unknownCalls > 0) parts.push(`${{fmt(coverage.unknownCalls)}} call(s) (${{Math.round(coverage.pctUnknown)}}%) come from an export made before cost-coverage tracking existed (unknown coverage)`);
      if (coverage.missingKnownCalls > 0) parts.push(`${{fmt(coverage.missingKnownCalls)}} call(s) (${{Math.round(coverage.pctMissingKnown)}}%) are confirmed to have NO recorded cost data`);
      insightCoverage.innerHTML = `<div class="insight warn"><span>&#9888;&#65039;</span><span><b>Cost data is incomplete</b> for the current selection: ${{parts.join("; ")}}. Est. cost and Value-for-Money figures below likely <b>understate</b> true spend - do not read a low/zero cost as confirmed cheap or free.</span></div>`;
    }}
  }}

  // Value insight: name the best and worst tok/$ models with enough data to be meaningful (5+ calls)
  const insightValue = document.getElementById("insight-value");
  if (insightValue) {{
    const reliableValue = valueEntries.filter(v => v.calls >= 5);
    if (reliableValue.length >= 2) {{
      const best = reliableValue[0], worst = reliableValue[reliableValue.length - 1];
      const multiple = (best.tpd / worst.tpd).toFixed(1);
      const caveat = (!best.coverageComplete || !worst.coverageComplete)
        ? " Note: at least one of these models has incomplete cost-data coverage in this selection, so this comparison may be skewed."
        : "";
      insightValue.innerHTML = `<div class="insight good"><span>&#128181;</span><span><b>${{escapeHtml(best.model)}}</b> gives the most tokens per dollar (${{fmtCompact(best.tpd)}} tok/$), about <b>${{multiple}}&times;</b> more than <b>${{escapeHtml(worst.model)}}</b> (${{fmtCompact(worst.tpd)}} tok/$) among models with 5+ calls.${{caveat}}</span></div>`;
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
      : "No task summaries are available. Re-run <code>extract_usage.py</code> without <code>--exclude-task-summary</code> (and without <code>--omit-task-summaries</code> at build time) to populate Work patterns.";
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
  drawChart("fig_theme_time", themeTimeTraces, {{
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
  drawChart("fig_theme_model", [{{
    x: themeModels,
    y: TASK_THEME_ORDER,
    z: themeModelValues,
    type: "heatmap",
    colorscale: "Viridis",
    zmin: 0,
    zmax: themeModelValues.reduce((max, row) => row.reduce((m, value) => Math.max(m, value), max), 0) || 1,
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
  drawChart("fig_work_mode", [{{
    labels: modeEntries.map(entry => entry[0]),
    values: modeEntries.map(entry => entry[1]),
    type: "pie",
    hole: 0.48,
    marker: {{ colors: modeEntries.map(entry => WORK_MODE_COLORS[entry[0]] || UNKNOWN_COLOR) }},
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
    drawChart("fig_user", [{{
      x: userEntries.map(u => u[0]), y: userEntries.map(u => u[1]), type: "bar", marker: {{ color: OKABE_ITO.blue }},
      text: userEntries.map(u => fmtVal(u[1])), textposition: "outside", cliponaxis: false,
      hovertemplate: "%{{x}}: %{{y:,}} " + unitLabel + "<extra></extra>",
    }}], {{ title: {{ text: metric === "cost" ? "Estimated Cost by User" : "Total Tokens by User" }}, xaxis: {{ title: {{ text: "User" }} }}, yaxis: {{ title: {{ text: valAxisTitle }} }} }}, {{ responsive: true }});
  }}

  // Reasoning effort mix - higher effort burns more reasoning tokens & costs more
  const effortOrder = ["none", "low", "medium", "high", "n/a"];
  const byEffort = groupSum(filtered, r => r.reasoning_effort || "n/a", valKey);
  const effortRank = effort => effortOrder.includes(effort) ? effortOrder.indexOf(effort) : effortOrder.length;
  const effortEntries = Array.from(byEffort.entries()).sort((a, b) => effortRank(a[0]) - effortRank(b[0]));
  if (document.getElementById("fig_effort")) {{
    drawChart("fig_effort", [{{
      x: effortEntries.map(e => e[0]), y: effortEntries.map(e => e[1]), type: "bar",
      marker: {{ color: effortEntries.map(e => EFFORT_COLORS[e[0]] || UNKNOWN_COLOR) }},
      text: effortEntries.map(e => fmtVal(e[1])), textposition: "outside", cliponaxis: false,
      hovertemplate: "%{{x}} effort: %{{y:,}} " + unitLabel + "<extra></extra>",
    }}], {{
      title: {{ text: (metric === "cost" ? "Estimated Cost" : "Tokens") + " by Reasoning Effort" }},
      xaxis: {{ title: {{ text: "Configured reasoning effort" }} }}, yaxis: {{ title: {{ text: valAxisTitle }} }},
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
  const order = FILTER_KINDS[kind].order;
  const excluded = checked ? new Set() : new Set(order);
  saveExcluded(kind, excluded);
  render();
}}

function applySidebarState(collapsed) {{
  const layout = document.getElementById("layout");
  const panel = document.getElementById("sidebar-shell");
  if (window.matchMedia("(max-width: 760px)").matches) {{
    document.getElementById("usage-toolbar").after(panel);
  }} else {{
    layout.insertBefore(panel, document.getElementById("dashboard-main"));
  }}
  const icon = document.getElementById("sidebar-toggle-icon");
  const btn = document.getElementById("sidebar-toggle");
  layout.classList.toggle("sidebar-collapsed", collapsed);
  icon.innerHTML = collapsed ? "&raquo;" : "&laquo;";
  btn.title = collapsed ? "Show the Projects, Models, and Providers filter panel" : "Hide the Projects, Models, and Providers filter panel";
  btn.setAttribute("aria-expanded", String(!collapsed));
  btn.setAttribute("aria-label", collapsed ? "Show filters" : "Hide filters");
  document.getElementById("sidebar-toggle-label").textContent = collapsed ? "Show filters" : "Hide filters";
}}

function toggleSidebar() {{
  const collapsed = document.getElementById("layout").classList.contains("sidebar-collapsed");
  const next = !collapsed;
  localStorage.setItem(STORAGE_KEY_SIDEBAR, next ? "1" : "0");
  applySidebarState(next);
  // Recompute chart label spacing after the available width changes.
  scheduleChartRender();
}}

let resizeTimer;
function scheduleChartRender() {{
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {{
    // Reuse live traces so legend selections survive without rebuilding controls.
    chartLayouts.forEach(({{ layout, config }}, id) => {{
      drawChart(id, document.getElementById(id).data, layout, config);
    }});
  }}, 180);
}}

document.addEventListener("change", (e) => {{
  const kind = Object.keys(FILTER_KINDS).find(k => e.target.classList.contains(FILTER_KINDS[k].checkboxClass));
  if (!kind) return;
  const excluded = loadExcluded(kind);
  if (e.target.checked) excluded.delete(e.target.value); else excluded.add(e.target.value);
  saveExcluded(kind, excluded);
  render();
}});

function makeChartHelpAccessible() {{
  document.querySelectorAll(".info-icon").forEach(icon => {{
    const details = document.createElement("details");
    details.className = "chart-help";
    const summary = document.createElement("summary");
    summary.textContent = "About this chart";
    const explanation = document.createElement("p");
    explanation.textContent = icon.title;
    details.appendChild(summary);
    details.appendChild(explanation);
    icon.replaceWith(details);
  }});
}}

makeChartHelpAccessible();
const smallScreen = window.matchMedia("(max-width: 760px)");
applySidebarState(smallScreen.matches || localStorage.getItem(STORAGE_KEY_SIDEBAR) === "1");
smallScreen.addEventListener("change", () => {{
  applySidebarState(smallScreen.matches || localStorage.getItem(STORAGE_KEY_SIDEBAR) === "1");
  scheduleChartRender();
}});
window.addEventListener("resize", scheduleChartRender);
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
    ap.add_argument(
        "--exclude-project", dest="exclude_projects", action="append", metavar="PROJECT", default=None,
        help="Exact project name to permanently remove from the dataset BEFORE the dashboard is "
             "built (repeatable, e.g. --exclude-project Foo --exclude-project 'Bar, Inc'). Unlike "
             "--exclude-default, this is irreversible build-time redaction: matching rows are "
             "dropped before totals/orders/RAW JSON/checkboxes/insights are computed, so they never "
             "reach the output file and can't be un-hidden in the browser. Use this when sharing a "
             "dashboard with people who shouldn't see certain projects at all.",
    )
    ap.add_argument(
        "--omit-task-summaries", action="store_true",
        help="Strip all task-summary text from the dataset BEFORE the dashboard is built "
             "(irreversible). Work Patterns and Task Detail will show their existing "
             "no-summaries-available state instead of embedding any task_summary values.",
    )
    args = ap.parse_args()

    data = load_data(args.pattern)
    exclude_default_projects = [p.strip() for p in args.exclude_default.split(",") if p.strip()]
    exclude_default_models = [m.strip() for m in args.exclude_default_models.split(",") if m.strip()]
    build_dashboard(
        data, args.out, args.title, exclude_default_projects, exclude_default_models, storage_key=args.out,
        exclude_projects=args.exclude_projects, omit_task_summaries=args.omit_task_summaries,
    )


if __name__ == "__main__":
    main()
