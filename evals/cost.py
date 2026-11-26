"""Dollars from recorded tokens. Never estimated from text length, never a flat rate.

Prices are US dollars per million tokens, Anthropic first-party API list prices
as of 2026-11-03. Cache writes (5-minute lifetime) bill at 1.25x the input price.
Cache reads bill at the listed cache-read price where one is published, and at
0.1x the input price otherwise.
"""
from __future__ import annotations

PRICES = {                      # (input, output)
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}
CACHE_READ = {"claude-sonnet-5-5": 0.10, "claude-opus-5-5": 0.20}


def cost(model: str, usage: dict) -> float:
    for name, (p_in, p_out) in PRICES.items():
        if str(model).startswith(name):
            read = CACHE_READ.get(name, 0.1 * p_in)
            return (usage.get("input_tokens", 0) * p_in
                    + usage.get("cache_creation_input_tokens", 0) * 1.25 * p_in
                    + usage.get("cache_read_input_tokens", 0) * read
                    + usage.get("output_tokens", 0) * p_out) / 1_000_000
    return 0.0
