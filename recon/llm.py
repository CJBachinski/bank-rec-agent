"""The only place this project talks to the Anthropic API.

The key comes from the environment (ANTHROPIC_API_KEY); nothing is read from a
file in the repo. Locally that means running under a secrets manager:

    doppler run --only-secrets ANTHROPIC_API_KEY -- uv run python -m evals.run ...

Retries are done here, not by the SDK, so that every attempt is counted and the
time reported for a call is the time of the request that succeeded, not the time
spent waiting out a rate limit.

No sampling parameters are sent: the current models reject temperature. Run to
run variation is handled by repeating runs and reporting the spread.

Server-side model fallbacks are deliberately not enabled. In an evaluation that
compares models, a silent switch to another model would corrupt the comparison;
a refusal is recorded as a refusal instead.
"""
from __future__ import annotations

import os
import random
import time


class MissingKey(RuntimeError):
    pass


class AnthropicLLM:
    def __init__(self, max_attempts: int = 5, timeout: float = 120.0):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise MissingKey("ANTHROPIC_API_KEY is not set. Run under `doppler run -- ...`.")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=0, timeout=timeout)
        self.max_attempts = max_attempts

    def create(self, **kwargs):
        """One Messages request. Returns (response, {"attempts", "latency_s"})."""
        a = self._anthropic
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                response = self.client.messages.create(**kwargs)
                return response, {"attempts": attempt, "latency_s": time.monotonic() - started}
            except a.RateLimitError:
                retryable = True
            except a.APIStatusError as exc:
                retryable = exc.status_code >= 500
                if not retryable:
                    raise
            except a.APIConnectionError:          # includes timeouts
                retryable = True
            if attempt == self.max_attempts:
                raise RuntimeError(f"gave up after {attempt} attempts")
            time.sleep(min(2 ** attempt + random.uniform(0, 1), 30))
