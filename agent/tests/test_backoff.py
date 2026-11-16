"""The back-off and its bounds (spec §5.7)."""

import random

from tlg_ipam_agent.backoff import Backoff, refused_wait
from tlg_ipam_agent.protocol import CHECKIN_REFUSED_RETRY_S, CHECKIN_RETRY_MAX_S, CHECKIN_RETRY_MIN_S


def test_each_retry_stays_inside_its_window():
    for seed in range(200):
        backoff = Backoff(random.Random(seed))
        for n in range(40):
            wait = backoff.next()
            assert CHECKIN_RETRY_MIN_S <= wait <= min(CHECKIN_RETRY_MAX_S, CHECKIN_RETRY_MIN_S * 2 ** (n + 1))


def test_the_window_grows_to_the_ceiling():
    class Top(random.Random):
        def uniform(self, a, b):
            return b

    backoff = Backoff(Top())
    assert [backoff.next() for _ in range(7)] == [20, 40, 80, 160, 300, 300, 300]
    backoff.reset()
    assert backoff.next() == 20


def test_never_below_the_servers_gap():
    class Bottom(random.Random):
        def uniform(self, a, b):
            return a

    backoff = Backoff(Bottom())
    assert {backoff.next() for _ in range(10)} == {CHECKIN_RETRY_MIN_S}


def test_a_very_long_outage_does_not_overflow():
    backoff = Backoff(random.Random(0))
    backoff.attempt = 10**6
    assert CHECKIN_RETRY_MIN_S <= backoff.next() <= CHECKIN_RETRY_MAX_S


def test_refused_wait():
    rng = random.Random(0)
    waits = [refused_wait(rng) for _ in range(1000)]
    assert all(0.9 * CHECKIN_REFUSED_RETRY_S <= w <= CHECKIN_REFUSED_RETRY_S for w in waits)
