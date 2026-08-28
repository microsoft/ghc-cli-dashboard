#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Capture documentation screenshots from a generated dashboard.

Builds synthetic data, generates a dashboard, and captures one PNG per
section. Requires playwright: pip install playwright && playwright install
chromium.

Usage:
    python tools/make_screenshots.py --html sample_dashboard.html --out-dir docs/images
"""
import argparse
import os

from playwright.sync_api import sync_playwright

# Each entry is (output filename, section element id).
SECTIONS = [
    ("overview.png", "sec-overview"),
    ("trends.png", "sec-trends"),
    ("cost-and-value.png", "sec-value"),
    ("work-patterns.png", "sec-patterns"),
    ("composition.png", "sec-composition"),
    ("task-detail.png", "sec-detail"),
]


def capture(html_path, out_dir, width, height):
    os.makedirs(out_dir, exist_ok=True)
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=2,
        )
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(2500)

        page.screenshot(path=os.path.join(out_dir, "dashboard.png"))
        print("wrote dashboard.png")

        for filename, section_id in SECTIONS:
            element = page.query_selector("#" + section_id)
            if element is None:
                print("skipped %s (no #%s)" % (filename, section_id))
                continue
            element.scroll_into_view_if_needed()
            page.wait_for_timeout(1200)
            element.screenshot(path=os.path.join(out_dir, filename))
            print("wrote %s" % filename)

        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True)
    parser.add_argument("--out-dir", default="docs/images")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    args = parser.parse_args()
    capture(args.html, args.out_dir, args.width, args.height)


if __name__ == "__main__":
    main()
