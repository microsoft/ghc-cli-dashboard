# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Tests for provider_classifier.py - the central, Python-only mapping from a
raw Copilot CLI model identifier to a best-effort AI provider label.

Covers: every model identifier/mapping actually seen in this repo's own
sample exports, case/whitespace normalization, None/empty/unknown handling,
deterministic provider ordering/coloring, and hostile input safety.

Run with: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import provider_classifier as pc  # noqa: E402  (import after sys.path tweak)


# ---------------------------------------------------------------------------
# Every model identifier/mapping this feature is required to support
# ---------------------------------------------------------------------------

def test_claude_prefixed_models_map_to_anthropic():
    for model in ["claude-opus-5", "claude-opus-4.8", "claude-sonnet-5", "claude-sonnet-4.6", "claude-haiku-4.5"]:
        assert pc.classify_provider(model) == "Anthropic", model


def test_gpt_prefixed_models_map_to_openai():
    for model in ["gpt-5.4", "gpt-5.4-mini", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.3-codex", "gpt-4o", "gpt-4o-mini"]:
        assert pc.classify_provider(model) == "OpenAI", model


def test_o1_o3_o4_prefixed_models_map_to_openai():
    for model in ["o1", "o1-preview", "o1-mini", "o3", "o3-mini", "o4", "o4-mini"]:
        assert pc.classify_provider(model) == "OpenAI", model


def test_gemini_prefixed_models_map_to_google():
    for model in ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.1-pro-preview", "gemini-1.5-pro"]:
        assert pc.classify_provider(model) == "Google", model


def test_grok_prefixed_models_map_to_xai():
    for model in ["grok-4.5", "grok-4.6", "grok-2"]:
        assert pc.classify_provider(model) == "xAI", model


# ---------------------------------------------------------------------------
# Real model identifiers present in this repo's sample CSV exports
# ---------------------------------------------------------------------------

def test_all_model_identifiers_from_repo_sample_exports_classify_as_expected():
    expected = {
        "claude-opus-4.8": "Anthropic",
        "claude-opus-5": "Anthropic",
        "claude-sonnet-4.6": "Anthropic",
        "claude-sonnet-5": "Anthropic",
        "gemini-3.5-flash": "Google",
        "gpt-5.3-codex": "OpenAI",
        "gpt-5.4": "OpenAI",
        "gpt-5.4-mini": "OpenAI",
        "gpt-5.5": "OpenAI",
        "gpt-5.6-luna": "OpenAI",
        "gpt-5.6-sol": "OpenAI",
        "gpt-5.6-terra": "OpenAI",
        "grok-4.5": "xAI",
    }
    for model, provider in expected.items():
        assert pc.classify_provider(model) == provider, model


# ---------------------------------------------------------------------------
# Case / whitespace normalization
# ---------------------------------------------------------------------------

def test_classification_is_case_insensitive():
    assert pc.classify_provider("GPT-4o") == "OpenAI"
    assert pc.classify_provider("Claude-Sonnet-5") == "Anthropic"
    assert pc.classify_provider("GEMINI-3.5-FLASH") == "Google"
    assert pc.classify_provider("GROK-4.5") == "xAI"


def test_classification_strips_leading_and_trailing_whitespace():
    assert pc.classify_provider("  gpt-4o  ") == "OpenAI"
    assert pc.classify_provider("\tclaude-opus-5\n") == "Anthropic"


def test_classification_collapses_internal_whitespace_runs():
    # Internal whitespace (tabs/newlines/multiple spaces) is collapsed to a
    # single space before matching - still a prefix match on "gpt".
    assert pc.classify_provider("gpt   5.4") == "OpenAI"
    assert pc.classify_provider("gpt\t5.4") == "OpenAI"


# ---------------------------------------------------------------------------
# None / empty / unknown handling - always the fixed label, never the raw
# model string, never an exception
# ---------------------------------------------------------------------------

def test_none_model_returns_other_unknown():
    assert pc.classify_provider(None) == pc.OTHER_UNKNOWN_PROVIDER


def test_empty_string_model_returns_other_unknown():
    assert pc.classify_provider("") == pc.OTHER_UNKNOWN_PROVIDER


def test_whitespace_only_model_returns_other_unknown():
    assert pc.classify_provider("   ") == pc.OTHER_UNKNOWN_PROVIDER
    assert pc.classify_provider("\t\n") == pc.OTHER_UNKNOWN_PROVIDER


def test_unrecognized_model_returns_other_unknown_not_raw_string():
    result = pc.classify_provider("some-brand-new-model-nobody-mapped-yet")
    assert result == pc.OTHER_UNKNOWN_PROVIDER
    assert result != "some-brand-new-model-nobody-mapped-yet"


def test_other_unknown_label_is_the_fixed_constant_everywhere():
    # The label used across every "no match" path must be identical (the
    # same object/string), not several different ad hoc "unknown" strings.
    assert pc.classify_provider(None) == pc.classify_provider("") == pc.classify_provider("nonsense-model") == pc.OTHER_UNKNOWN_PROVIDER
    assert pc.OTHER_UNKNOWN_PROVIDER == "Other / Unknown"


# ---------------------------------------------------------------------------
# Non-string input safety (defensive - real CSV data is always a string once
# read by pandas, but a NaN cell can surface as float('nan'))
# ---------------------------------------------------------------------------

def test_nan_float_model_returns_other_unknown_without_raising():
    assert pc.classify_provider(float("nan")) == pc.OTHER_UNKNOWN_PROVIDER


# ---------------------------------------------------------------------------
# Provider ordering / color determinism
# ---------------------------------------------------------------------------

def test_provider_canonical_order_and_palette_are_index_aligned_and_cover_every_rule():
    assert len(pc.PROVIDER_CANONICAL_ORDER) == len(pc.PROVIDER_PALETTE)
    mapped_providers = {provider for _, provider in pc.PROVIDER_PREFIX_RULES}
    assert mapped_providers <= set(pc.PROVIDER_CANONICAL_ORDER)
    assert pc.PROVIDER_CANONICAL_ORDER[-1] == pc.OTHER_UNKNOWN_PROVIDER


def test_provider_colors_are_fixed_per_provider_name_and_deterministic():
    # Calling classify_provider repeatedly / building PROVIDER_COLORS fresh
    # must always yield the exact same color for the exact same provider -
    # no dependency on dict iteration order, call order, or input dataset.
    colors_first = dict(zip(pc.PROVIDER_CANONICAL_ORDER, pc.PROVIDER_PALETTE))
    colors_second = dict(zip(pc.PROVIDER_CANONICAL_ORDER, pc.PROVIDER_PALETTE))
    assert colors_first == colors_second == pc.PROVIDER_COLORS
    assert pc.PROVIDER_COLORS["Anthropic"] == pc.PROVIDER_COLORS["Anthropic"]
    for provider in pc.PROVIDER_CANONICAL_ORDER:
        assert provider in pc.PROVIDER_COLORS
        assert pc.PROVIDER_COLORS[provider].startswith("#")


def test_all_provider_colors_are_distinct_hex_values():
    colors = list(pc.PROVIDER_COLORS.values())
    assert len(colors) == len(set(colors)), "every provider must have a visually distinct color"


# ---------------------------------------------------------------------------
# Hostile model strings must classify safely (no exception, no raw echo
# beyond what a legitimate prefix match would already produce)
# ---------------------------------------------------------------------------

def test_hostile_model_strings_classify_safely():
    hostile_inputs = [
        "<script>alert(1)</script>",
        "</script><script>alert(1)</script>",
        "=1+1",
        "'; DROP TABLE models; --",
        "gpt-4o<img src=x onerror=alert(1)>",  # still prefix-matches "gpt"
        "claude" + "\u2028" + "-opus-5",  # embedded line separator
    ]
    for model in hostile_inputs:
        result = pc.classify_provider(model)
        # Must always be one of the fixed, known labels - never raise, and
        # never just be the raw hostile string echoed back unmodified.
        assert result in pc.PROVIDER_CANONICAL_ORDER
