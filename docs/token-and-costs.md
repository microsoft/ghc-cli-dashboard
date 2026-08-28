---
layout: default
title: Token and cost data
nav_order: 3
---

# Token and cost data

## Token fields

The dashboard's headline token metric is:

```text
total_tokens = input_tokens + output_tokens
```

Cache and reasoning fields are separate counters:

| Field | Meaning |
| --- | --- |
| `input_tokens` | New prompt content processed for a call. |
| `output_tokens` | Content returned by the model. |
| `cache_read_tokens` | Previously cached context reused by a later call. |
| `cache_write_tokens` | Context added to cache for later reuse. |
| `reasoning_tokens` | Tokens used for model reasoning before the visible response. |

The Composition chart shows all five counters. It always uses raw token
counts because the export does not provide a separate cost for each category.

## Cost estimates

The dashboard estimates list-price cost from `total_nano_aiu`:

```text
estimated_cost_usd = total_nano_aiu / 1e11
```

This is an estimate. Plan allowances, discounts, taxes, currency, and missing
cost data can make it differ from an invoice.

`cost_data_calls` shows how many calls in an aggregated row had recorded cost
data. The Cost data coverage KPI identifies incomplete coverage. Do not treat
a zero or missing estimate as proof that usage was free.
