"""Waiting after a refusal (spec §5.7; the constants are agent_protocol.CHECKIN_*)."""

import random

from .protocol import CHECKIN_REFUSED_RETRY_S, CHECKIN_RETRY_MAX_S, CHECKIN_RETRY_MIN_S


class Backoff:
    """Bounded exponential back-off with jitter, for refusals that clear by themselves
    (`429`, `503`, other 5xx, no answer). Retry n (from 0) waits a time drawn uniformly
    from [low, min(high, low * 2**(n+1))]: never less than `low`, the gap the server
    enforces between check-ins, and never more than `high`. Neither refusal carries
    `Retry-After`, so the jitter is what keeps agents that were refused together from
    retrying together."""

    def __init__(
        self,
        rng: random.Random,
        low: float = CHECKIN_RETRY_MIN_S,
        high: float = CHECKIN_RETRY_MAX_S,
    ):
        self._rng, self.low, self.high = rng, low, high
        self.attempt = 0

    def next(self) -> float:
        # the exponent stops growing once the ceiling is reached, so it never overflows
        ceiling = min(self.high, self.low * 2 ** min(self.attempt + 1, 32))
        self.attempt += 1
        return self._rng.uniform(self.low, ceiling)

    def reset(self) -> None:
        self.attempt = 0


def refused_wait(rng: random.Random) -> float:
    """The wait after a refusal only the owner can clear (`401`, `403`, `409`, `422`,
    other 4xx): 90-100% of CHECKIN_REFUSED_RETRY_S, never a tight loop."""
    return rng.uniform(0.9 * CHECKIN_REFUSED_RETRY_S, CHECKIN_REFUSED_RETRY_S)
