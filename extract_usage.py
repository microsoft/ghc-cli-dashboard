#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""
extract_usage.py - Extract GitHub Copilot CLI token-usage data from the local
session-store.db into a privacy-reduced CSV.

Note: this reduces exposure (folder paths are minimized to a repo/folder
name) but does not anonymize the data - it still contains your OS username
(or --user-label), project/repo names, model names, and, with
--include-task-summary, free-text task summaries. Review a CSV's contents
before sharing it outside your machine.

Data source: ~/.copilot/session-store.db (SQLite). This file exists with the
same schema on every machine that runs Copilot CLI (Windows/Mac/Linux), so
this script is self-contained and needs no special permissions or APIs -
each person runs it against their own machine.

Usage:
    python extract_usage.py
    python extract_usage.py --db "D:\\custom\\path\\session-store.db"
    python extract_usage.py --user-label "team-alpha-jsmith" --include-task-summary
    python extract_usage.py --out "C:\\shared\\team-usage\\jsmith_2026-08-11.csv"

Output: a CSV with one row per (session, model, day) with token/cost totals.
Drop the resulting CSV into a shared team folder (SharePoint/Teams/OneDrive)
so dashboard.py can combine multiple people's exports into one view.
"""
import argparse
import getpass
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone


# Export schema/versioning convention
# ------------------------------------
# `export_format_version` is a simple, monotonically increasing integer
# (as a string, e.g. "2") identifying the *shape* of a copilot_usage_*.csv
# file - bump it whenever a change would matter to a consumer doing
# cross-export deduplication or column presence checks (adding/removing/
# renaming a column, changing a column's meaning, or changing the
# aggregation grain). It is NOT semver: there is exactly one integer, no
# minor/patch component, because this is an internal, single-producer
# export format (extract_usage.py is the only writer) rather than a public
# API with independent minor/patch compatibility concerns.
#
#   version 1 (implicit/legacy): the original schema, before this field
#     existed. Recognized by dashboard.py by the *absence* of the
#     `export_format_version` (and `exported_at`) column(s) - there is no
#     literal "1" ever written to a CSV.
#   version 2: adds two columns to every row: `export_format_version`
#     (this literal, as a string) and `exported_at` (a single, timezone-aware
#     ISO-8601 UTC timestamp shared by every row in the file, recording when
#     *this run* of extract_usage.py produced the file - not when any
#     individual usage event happened). dashboard.py uses `exported_at` to
#     deterministically resolve overlapping/duplicate rows across multiple
#     exports (see dashboard.py's dedup policy), instead of relying on
#     filename sort order.
#   version 3 (current): adds one column, `cost_data_calls` - see the
#     "Token/cost definitions" note below for exactly what it means and why
#     it exists. Every other column (including `total_nano_aiu` itself) is
#     unchanged in meaning; this is purely an additive coverage signal.
EXPORT_FORMAT_VERSION = "3"


# Token/cost definitions (read this before interpreting any exported column)
# ----------------------------------------------------------------------------
# - `total_tokens` = `input_tokens` + `output_tokens` ONLY. It intentionally
#   does NOT include `cache_read_tokens`, `cache_write_tokens`, or
#   `reasoning_tokens` - those three are separate, additive categories
#   reported by Copilot CLI's own usage log, not a subset already folded
#   into `total_tokens`. Do not assume input+output+cache_read+cache_write+
#   reasoning is some other "grand total" figure the CLI also reports
#   elsewhere - it isn't; this export simply passes through whatever the
#   five raw counters say, side by side.
# - `total_nano_aiu` is the underlying COST basis for the row (GitHub's
#   internal billing unit; dashboard.py converts it to an estimated USD
#   figure). It is priced per-category (e.g. cached input is typically
#   cheaper per token than fresh input, and reasoning tokens are billed
#   like output tokens) - it is NOT simply proportional to `total_tokens`,
#   and a row with a high `total_tokens` but heavy cache-read reuse can
#   cost less than a row with a lower `total_tokens` but no cache hits.
#   Treat `total_tokens` and `total_nano_aiu` as two independent figures
#   about the same row, not one derived from the other.
# - `cost_data_calls` (added in version 3) is a coverage counter, not a
#   usage metric: it's the number of underlying `assistant_usage_events`
#   rows folded into this (session, day, model, reasoning_effort) export
#   row that had a non-NULL `total_nano_aiu` in the source database.
#   Comparing it to `calls` tells a consumer (dashboard.py, or anyone else
#   reading this CSV directly) whether this row's `total_nano_aiu` is a
#   complete, confirmed sum (`cost_data_calls == calls`), a partial one
#   (`0 < cost_data_calls < calls` - some calls contributed no cost data),
#   or entirely absent (`cost_data_calls == 0`, in which case
#   `total_nano_aiu` is written as `0` by SQL SUM-of-nothing convention and
#   must NOT be read as "this row cost nothing" - it means "no cost data
#   was recorded for it"). CSVs from before version 3 have no
#   `cost_data_calls` column at all, which dashboard.py treats as "coverage
#   unknown" (neither confirmed-complete nor confirmed-missing) rather than
#   silently assuming full coverage.


def default_db_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".copilot", "session-store.db")


def normalize_project(repository, cwd):
    """Prefer the repo name (e.g. 'org/repo'); fall back to the last path
    segment of cwd so we don't leak a user's full local folder structure
    into a shared export."""
    if repository:
        return repository
    if cwd:
        cwd = cwd.rstrip("\\/")
        return os.path.basename(cwd) or cwd
    return "(unknown)"


class ReadOnlySnapshot:
    """Context manager that takes a consistent, point-in-time snapshot of
    ``db_path`` into a fresh temporary database file, opens it read-only, and
    guarantees cleanup of both the open connection and the temporary
    DB/WAL/SHM files on *every* exit path (normal completion, ``sys.exit``,
    or any other exception raised while the snapshot is in use).

    Why a backup-API snapshot instead of copying the file (+ its -wal/-shm
    sidecars) directly: Copilot CLI keeps ``session-store.db`` open in WAL
    mode while it runs, so a plain ``shutil.copy2`` of the three files taken
    while the CLI is mid-write can copy them out of sync with each other
    (a "torn" copy) - e.g. the main file copied before a checkpoint but the
    -wal copied after, or a write landing between the three copies. SQLite's
    ``Connection.backup()`` instead reads through SQLite's own machinery
    under a read lock, at a single consistent point in time, and is safe to
    run concurrently with another process (a live ``copilot`` process)
    writing new usage events - we only ever open the source database
    read-only, so we never lock or interfere with it. The tradeoff is a
    couple of extra SQLite connections and a full-database read during the
    backup step; for the local session-store.db sizes this tool targets,
    that's negligible next to the correctness benefit.

    The connection returned by ``__enter__`` MUST NOT be used after the
    ``with`` block exits - ``__exit__`` closes it and deletes the temporary
    snapshot directory, in that order, so nothing can outlive its cleanup.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._tmp_dir = None
        self._conn = None

    def __enter__(self) -> sqlite3.Connection:
        if not os.path.exists(self.db_path):
            sys.exit(f"ERROR: database not found at {self.db_path}")

        self._tmp_dir = tempfile.mkdtemp(prefix="copilot_usage_")
        try:
            tmp_db = os.path.join(self._tmp_dir, "session-store.snapshot.db")
            src_conn = None
            dst_conn = None
            try:
                try:
                    src_conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                    src_conn.execute("SELECT 1")  # fail fast if not a valid SQLite DB
                except sqlite3.Error as e:
                    sys.exit(f"ERROR: could not open database at {self.db_path} read-only: {e}")

                try:
                    dst_conn = sqlite3.connect(tmp_db)
                    src_conn.backup(dst_conn)
                except sqlite3.Error as e:
                    sys.exit(f"ERROR: failed to snapshot database at {self.db_path}: {e}")
            finally:
                if src_conn is not None:
                    src_conn.close()
                if dst_conn is not None:
                    dst_conn.close()

            self._conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            return self._conn
        except BaseException:
            # Any failure past this point (including the sys.exit() calls
            # above, which raise SystemExit) must not leak the temp dir.
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
            raise

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self._conn is not None:
                self._conn.close()
        finally:
            self._conn = None
            if self._tmp_dir is not None:
                shutil.rmtree(self._tmp_dir, ignore_errors=True)
                self._tmp_dir = None
        return False  # never suppress exceptions raised inside the `with` block


# Tables/columns QUERY below depends on. Copilot CLI's session-store.db
# schema is internal/undocumented (see README), so a newer or older CLI
# version could rename or drop any of these - validate_schema() checks all
# of them upfront and fails with one actionable message instead of letting
# an arbitrary sqlite3.OperationalError surface from deep inside QUERY.
REQUIRED_SCHEMA = {
    "assistant_usage_events": [
        "session_id", "created_at", "model", "reasoning_effort",
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens", "total_nano_aiu",
    ],
    "sessions": ["id", "repository", "cwd", "summary"],
}


def validate_schema(conn: sqlite3.Connection, db_path: str) -> None:
    """Check that the snapshot has every table/column QUERY needs, and exit
    with a single concise ERROR (naming every missing table/column) if not -
    this is the most common failure mode for an incompatible Copilot CLI
    schema, and should never surface as a raw sqlite3 traceback."""
    existing_tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    problems = []
    for table, columns in REQUIRED_SCHEMA.items():
        if table not in existing_tables:
            problems.append(f"missing table '{table}'")
            continue
        existing_cols = {row[1] for row in conn.execute(f"PRAGMA table_info('{table}')")}
        missing_cols = [c for c in columns if c not in existing_cols]
        if missing_cols:
            problems.append(f"table '{table}' is missing column(s): {', '.join(missing_cols)}")

    if problems:
        details = "\n  - ".join(problems)
        sys.exit(
            f"ERROR: {db_path} does not match the Copilot CLI schema this tool expects:\n"
            f"  - {details}\n"
            "This usually means an incompatible/newer (or older) Copilot CLI version "
            "changed its internal database layout - see README's 'Requirements & "
            "limitations' section. This tool relies on an internal, undocumented schema."
        )


QUERY = """
SELECT
    s.id                AS session_id,
    s.repository         AS repository,
    s.cwd                 AS cwd,
    s.summary             AS task_summary,
    substr(u.created_at, 1, 10) AS day,
    u.model               AS model,
    COALESCE(u.reasoning_effort, 'n/a') AS reasoning_effort,
    COUNT(*)              AS calls,
    SUM(u.input_tokens)   AS input_tokens,
    SUM(u.output_tokens)  AS output_tokens,
    SUM(u.cache_read_tokens)  AS cache_read_tokens,
    SUM(u.cache_write_tokens) AS cache_write_tokens,
    SUM(u.reasoning_tokens) AS reasoning_tokens,
    SUM(u.total_nano_aiu) AS total_nano_aiu,
    -- Coverage counter, not a usage metric: how many of the COUNT(*) calls in
    -- this group actually had a non-NULL total_nano_aiu to contribute to the
    -- SUM above. SQL's SUM() silently ignores NULLs, so without this a group
    -- where some/all calls lack cost data is indistinguishable from a group
    -- that is genuinely free - see the "Token/cost definitions" note above.
    COUNT(u.total_nano_aiu) AS cost_data_calls
FROM assistant_usage_events u
JOIN sessions s ON s.id = u.session_id
GROUP BY s.id, day, u.model, reasoning_effort
ORDER BY day DESC
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=default_db_path(), help="Path to session-store.db (default: ~/.copilot/session-store.db)")
    ap.add_argument("--out", default=None, help="Output CSV path (default: ./copilot_usage_<user>_<date>.csv)")
    ap.add_argument("--user-label", default=None, help="Label to identify you in a shared/team rollup (default: OS username)")
    ap.add_argument("--include-task-summary", action="store_true", help="Include the free-text session/task summary (off by default - may contain sensitive detail)")
    args = ap.parse_args()

    user_label = args.user_label or getpass.getuser()
    with ReadOnlySnapshot(args.db) as conn:
        validate_schema(conn, args.db)
        cur = conn.execute(QUERY)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]

    out_path = args.out or f"copilot_usage_{user_label}_{datetime.now().strftime('%Y-%m-%d')}.csv"

    # A single, timezone-aware timestamp for *this run* - the same value is
    # written into every row of the file, so dashboard.py can treat it as
    # "when was this file exported" for deterministic dedup ordering across
    # overlapping exports (see README/export_format_version comment above).
    # timezone.utc (rather than the naive local time datetime.now() used for
    # the default filename above) guarantees the value is unambiguous and
    # comparable across machines/timezones.
    exported_at = datetime.now(timezone.utc).isoformat()

    import csv
    out_cols = [
        "user", "date", "project", "model", "reasoning_effort", "calls",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "total_tokens", "total_nano_aiu", "cost_data_calls", "session_id",
        "export_format_version", "exported_at",
    ]
    if args.include_task_summary:
        out_cols.insert(5, "task_summary")

    idx = {c: i for i, c in enumerate(cols)}
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for r in rows:
            project = normalize_project(r[idx["repository"]], r[idx["cwd"]])
            input_tokens = r[idx["input_tokens"]] or 0
            output_tokens = r[idx["output_tokens"]] or 0
            rec = {
                "user": user_label,
                "date": r[idx["day"]],
                "project": project,
                "model": r[idx["model"]] or "(unknown)",
                "reasoning_effort": r[idx["reasoning_effort"]] or "n/a",
                "calls": r[idx["calls"]] or 0,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": r[idx["cache_read_tokens"]] or 0,
                "cache_write_tokens": r[idx["cache_write_tokens"]] or 0,
                "reasoning_tokens": r[idx["reasoning_tokens"]] or 0,
                "total_tokens": input_tokens + output_tokens,
                "total_nano_aiu": r[idx["total_nano_aiu"]] or 0,
                # See "Token/cost definitions" above: this is a COVERAGE count
                # (how many of the `calls` calls had real cost data), not a
                # cost figure itself - do not confuse it with total_nano_aiu.
                "cost_data_calls": r[idx["cost_data_calls"]] or 0,
                "session_id": r[idx["session_id"]],
                "export_format_version": EXPORT_FORMAT_VERSION,
                "exported_at": exported_at,
            }
            if args.include_task_summary:
                rec["task_summary"] = r[idx["task_summary"]] or ""
            w.writerow(rec)

    print(f"Wrote {len(rows)} rows to {out_path}")
    print("Share this CSV in a team folder, then run dashboard.py to visualize "
          "one or many users' exports together.")


if __name__ == "__main__":
    main()
