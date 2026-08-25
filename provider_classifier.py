#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""provider_classifier.py - Central, Python-only mapping from a raw Copilot
CLI model identifier (e.g. "gpt-5.4", "claude-opus-5", "gemini-3.5-flash",
"grok-4.5") to a best-effort AI *provider* label ("OpenAI", "Anthropic",
"Google", "xAI").

IMPORTANT - this is heuristic inference, not authoritative billing/vendor
metadata. It is a prefix match against GitHub Copilot's own model-naming
conventions at the time this was written. GitHub/model providers can and do
add new model families without notice, rename models, or reuse prefixes in
ways this module doesn't anticipate - any model string that doesn't match a
known prefix (including genuinely new/renamed models, internal test model
names, or malformed input) safely falls back to OTHER_UNKNOWN_PROVIDER
rather than guessing, and the raw model string itself is NEVER surfaced as a
provider label.

Classification happens exactly once, here, at dashboard-BUILD time (see
dashboard.py's build_dashboard(), which calls classify_provider() while
constructing each record). The resulting `provider` field then travels with
the row into the generated dashboard's embedded RAW JSON like any other
column - the client-side JS in dashboard.py must never reimplement this
prefix matching; it only ever reads the `provider` value that was already
computed here.

--- Maintaining the mapping -----------------------------------------------
To add support for a new model family:
  1. Add a (lowercase_prefix, provider_name) tuple to PROVIDER_PREFIX_RULES.
     Rules are checked in order and the FIRST matching prefix wins, so put
     more specific prefixes before more general ones if they could overlap.
  2. If provider_name is a provider not already in PROVIDER_CANONICAL_ORDER,
     append it there AND add one more matching hex color to PROVIDER_PALETTE
     at the same index (these two lists are zip()-ed together into
     PROVIDER_COLORS, which drives the dashboard's Provider Mix chart and
     filter panel colors). Keeping colors keyed by provider *name* - not by
     a model's rank/frequency in any particular dataset - means a provider's
     color is stable across different filtered views of the same dashboard,
     and across different dashboards built from different exports.

Raw model names themselves are never altered anywhere in this module - only
this derived, display-only `provider` label is computed.
"""
from typing import Optional

# Fixed label for any model that cannot be confidently classified. Deliberately
# NOT the raw model string - never surfaced verbatim as a "provider".
OTHER_UNKNOWN_PROVIDER = "Other / Unknown"

# Checked in order; the first matching prefix wins. Model identifiers are
# normalized (whitespace-collapsed + lowercased) before matching via
# classify_provider(), so every prefix here only needs to be lowercase.
PROVIDER_PREFIX_RULES = [
    ("claude", "Anthropic"),
    ("gpt", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("o4", "OpenAI"),
    ("gemini", "Google"),
    ("grok", "xAI"),
]

# Canonical provider display order + a matching fixed hex palette (index-
# aligned, extend both together - see module docstring). OTHER_UNKNOWN_PROVIDER
# is always last so the "everything else" bucket reads as a deliberate
# catch-all rather than a ranked provider.
PROVIDER_CANONICAL_ORDER = ["Anthropic", "OpenAI", "Google", "xAI", OTHER_UNKNOWN_PROVIDER]
PROVIDER_PALETTE = ["#c96442", "#10a37f", "#4285f4", "#000000", "#8c959f"]

# provider name -> fixed hex color. Fixed (not rank-based) so a provider's
# color never changes depending on which other providers are present/filtered
# in a given view - see module docstring, point 2.
PROVIDER_COLORS = dict(zip(PROVIDER_CANONICAL_ORDER, PROVIDER_PALETTE))


def classify_provider(model: Optional[str]) -> str:
    """Infer a best-effort provider label from a raw model identifier.

    Safe for any input: None, "", whitespace-only, mixed case, internal
    whitespace runs, or a value that simply isn't a recognized model prefix
    all resolve to the fixed OTHER_UNKNOWN_PROVIDER label - never an
    exception, and never the raw model string itself. The raw `model` value
    passed in is never mutated or echoed back; only this derived label is
    returned.
    """
    if model is None:
        return OTHER_UNKNOWN_PROVIDER
    # Collapse all whitespace runs (spaces/tabs/newlines) and normalize case
    # before matching - handles "  GPT-4o  ", "Claude\tOpus", etc.
    normalized = " ".join(str(model).split()).lower()
    if not normalized:
        return OTHER_UNKNOWN_PROVIDER
    for prefix, provider in PROVIDER_PREFIX_RULES:
        if normalized.startswith(prefix):
            return provider
    return OTHER_UNKNOWN_PROVIDER
