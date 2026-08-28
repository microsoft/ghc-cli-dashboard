#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Generate a synthetic usage CSV for documentation screenshots.

The output contains no real usage data. Project names are drawn from the
fictitious companies Microsoft uses in documentation, and the numbers come
from a seeded random generator, so running this twice produces the same file.

Usage:
    python tools/make_sample_data.py --out sample_usage.csv
"""
import argparse
import csv
import random
from datetime import date, timedelta

PROJECTS = [
    ("contoso/insights-sample-code", 1.00),
    ("contoso/insights-toolkit-py", 0.62),
    ("northwind/site-analytics", 0.48),
    ("fabrikam/billing-api", 0.41),
    ("Project Saturn", 0.87),
    ("Project Mercury", 0.34),
    ("Project Beacon", 0.22),
    ("adventure-works/catalog", 0.18),
    ("tailwind-traders/storefront", 0.12),
    ("woodgrove-bank/ledger", 0.09),
]

MODELS = [
    ("claude-sonnet-5", 0.60, 12.0),
    ("gpt-5.6-sol", 0.34, 15.0),
    ("claude-opus-5", 0.24, 48.0),
    ("gemini-3.5-flash", 0.11, 3.0),
    ("gpt-5.4", 0.08, 11.0),
    ("grok-4.5", 0.04, 9.0),
]

EFFORTS = ["low", "medium", "high"]

SUMMARIES = [
    "Investigate failing integration test in checkout flow",
    "Refactor report builder into smaller modules",
    "Add pagination to the customer search endpoint",
    "Review pull request feedback and update docs",
    "Plan migration from the legacy scheduler",
    "Fix timezone handling in the nightly export",
    "Write unit tests for the pricing calculator",
    "Explore options for caching catalog lookups",
    "Update dependency versions and rerun the suite",
    "Draft release notes for the March milestone",
]

EXPORT_FORMAT_VERSION = "2"

FIELDS = [
    "user", "date", "project", "model", "reasoning_effort", "task_summary",
    "calls", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens",
    "total_nano_aiu", "cost_data_calls", "session_id",
    "export_format_version", "exported_at",
]


def build_rows(days, seed):
    rng = random.Random(seed)
    end = date(2026, 3, 20)
    # Fixed so repeated runs produce a byte-identical file.
    exported_at = "2026-03-20T18:00:00+00:00"
    rows = []
    session = 0

    for offset in range(days):
        day = end - timedelta(days=days - 1 - offset)
        # Taper weekends so the trend chart looks like real working patterns.
        weekday_factor = 0.25 if day.weekday() >= 5 else 1.0
        for project, project_weight in PROJECTS:
            if rng.random() > project_weight * 0.85 * weekday_factor:
                continue
            for model, model_weight, unit_cost in MODELS:
                if rng.random() > model_weight * 1.3:
                    continue
                session += 1
                calls = rng.randint(3, 60)
                scale = project_weight * model_weight * weekday_factor
                inp = int(rng.randint(4000, 45000) * calls * scale)
                out = int(inp * rng.uniform(0.08, 0.22))
                cache_read = int(inp * rng.uniform(0.6, 3.2))
                cache_write = int(inp * rng.uniform(0.05, 0.35))
                reasoning = int(out * rng.uniform(0.0, 1.4))
                # total_nano_aiu is an integer count of nano AI units.
                # 1 AI credit = $0.01, and nano_aiu / 1e11 = USD, so a
                # dollar figure maps to nano_aiu * 1e11.
                # unit_cost is USD per 1M tokens.
                usd = (inp + out) / 1000000.0 * unit_cost
                nano = int(usd * 1e11 * rng.uniform(0.85, 1.15))
                rows.append({                    "user": "sample-user",
                    "date": day.isoformat(),
                    "project": project,
                    "model": model,
                    "reasoning_effort": rng.choice(EFFORTS),
                    "task_summary": rng.choice(SUMMARIES),
                    "calls": calls,
                    "input_tokens": inp,
                    "output_tokens": out,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "reasoning_tokens": reasoning,
                    "total_tokens": inp + out,
                    "total_nano_aiu": nano,
                    "cost_data_calls": calls,
                    "session_id": "sample-%05d" % session,
                    "export_format_version": EXPORT_FORMAT_VERSION,
                    "exported_at": exported_at,
                })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="sample_usage.csv")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--seed", type=int, default=20260320)
    args = parser.parse_args()

    rows = build_rows(args.days, args.seed)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("wrote %s rows to %s" % (len(rows), args.out))


if __name__ == "__main__":
    main()
