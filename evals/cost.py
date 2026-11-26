"""Dollars from recorded tokens. Never estimated from text length, never a flat rate.

Prices are US dollars per million tokens (input, output), Anthropic first-party
API list prices as of 2026-11-03. Cache writes bill at 1.25x input and cache
reads at 0.1x input; this project does not use prompt caching, so those counts
should be zero, but they are priced if they ever appear.
"""
from __future__ import annotations

PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


def cost(model: str, usage: dict) -> float:
    for name, (p_in, p_out) in PRICES.items():
        if str(model).startswith(name):
            billed_in = (usage.get("input_tokens", 0)
                         + 1.25 * usage.get("cache_creation_input_tokens", 0)
                         + 0.1 * usage.get("cache_read_input_tokens", 0))
            return (billed_in * p_in + usage.get("output_tokens", 0) * p_out) / 1_000_000
    return 0.0
