#!/usr/bin/env python3
"""
extract_usage.py - Extract GitHub Copilot CLI token-usage data from the local
session-store.db into an anonymized, shareable CSV.

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


def open_readonly_copy(db_path: str) -> sqlite3.Connection:
    """Copy the DB (plus its WAL/SHM sidecars) to a temp location and open
    read-only, so we never lock or interfere with a running Copilot CLI."""
    if not os.path.exists(db_path):
        sys.exit(f"ERROR: database not found at {db_path}")

    tmp_dir = tempfile.mkdtemp(prefix="copilot_usage_")
    tmp_db = os.path.join(tmp_dir, "session-store.db")
    shutil.copy2(db_path, tmp_db)
    for suffix in ("-wal", "-shm"):
        side = db_path + suffix
        if os.path.exists(side):
            shutil.copy2(side, tmp_db + suffix)

    conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
    return conn


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
    SUM(u.total_nano_aiu) AS total_nano_aiu
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
    conn = open_readonly_copy(args.db)
    try:
        cur = conn.execute(QUERY)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
    finally:
        conn.close()

    out_path = args.out or f"copilot_usage_{user_label}_{datetime.now().strftime('%Y-%m-%d')}.csv"

    import csv
    out_cols = [
        "user", "date", "project", "model", "reasoning_effort", "calls",
        "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "total_tokens", "total_nano_aiu", "session_id",
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
                "session_id": r[idx["session_id"]],
            }
            if args.include_task_summary:
                rec["task_summary"] = r[idx["task_summary"]] or ""
            w.writerow(rec)

    print(f"Wrote {len(rows)} rows to {out_path}")
    print("Share this CSV in a team folder, then run dashboard.py to visualize "
          "one or many users' exports together.")


if __name__ == "__main__":
    main()
