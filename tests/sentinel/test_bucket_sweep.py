"""Stage 21: the in-process limiters give their memory back.

``security_guard.BUCKETS``, ``pulse_security_core._RATE_BUCKETS`` and
``bot.RATE_LIMIT_BUCKETS`` all prune *stamps* when a key is read and never
removed the *key*. One entry per (subject, path) a worker had ever seen, held
for the life of a long-lived gunicorn process, reclaimed by nothing. Organic
traffic grows it slowly; anything sending unique subjects grows it as fast as it
can send them.

The sweep is only worth having if it is decision-neutral, so most of this file
is about proving it changes no verdict rather than proving it frees memory.
"""

import time

import pytest

from services import pulse_security_core, security_guard


@pytest.fixture(autouse=True)
def _clean():
    security_guard.BUCKETS.clear()
    security_guard._SWEEPS.clear()
    pulse_security_core._RATE_BUCKETS.clear()
    yield
    security_guard.BUCKETS.clear()
    security_guard._SWEEPS.clear()
    pulse_security_core._RATE_BUCKETS.clear()


class TestTheSweepFreesWhatNothingIsCounting:
    def test_a_key_nobody_has_touched_for_a_full_window_is_dropped(self):
        now = 1_000_000.0
        buckets = {"gone": [now - 700.0], "here": [now - 10.0]}
        dropped = security_guard.sweep_expired("t", buckets, 600, now=now)
        assert dropped == 1
        assert set(buckets) == {"here"}

    def test_it_actually_shrinks_under_the_traffic_that_grows_it(self):
        """The shape of the leak, not a unit of it: many subjects arrive once
        and never return."""
        now = 1_000_000.0
        for i in range(500):
            security_guard.rate_limited(f"ip-{i}:/api/sms/send", 8, 600)
        assert len(security_guard.BUCKETS) == 500

        # A minute later, one more caller. Nothing has swept yet because the
        # sweep is rate-limited to once a minute, which is the point of the
        # interval — the cost is amortised, not paid per request.
        security_guard.sweep_expired(
            "security_guard.BUCKETS", security_guard.BUCKETS, 600,
            now=time.time() + 601)
        assert security_guard.BUCKETS == {}

    def test_an_empty_bucket_is_dropped_too(self):
        """The refusal path stores a filtered bucket back, which can be empty
        when the limit is zero. Those keys leak the same way."""
        buckets = {"empty": []}
        assert security_guard.sweep_expired("t", buckets, 600, now=1_000_000.0) == 1
        assert buckets == {}

    def test_the_sweep_is_rate_limited_so_it_is_not_paid_per_request(self):
        """It walks the whole dict, so it must not run on every call. A second
        sweep a moment later is a no-op even though there is stale work."""
        now = 1_000_000.0
        buckets = {"gone": [now - 700.0]}
        assert security_guard.sweep_expired("t", buckets, 600, now=now) == 1
        buckets["also_gone"] = [now - 700.0]
        assert security_guard.sweep_expired("t", buckets, 600, now=now + 1) == 0
        assert security_guard.sweep_expired("t", buckets, 600, now=now + 61) == 1


class TestTheSweepCannotChangeAVerdict:
    """The whole argument for doing this without a kill switch."""

    def test_a_key_still_inside_its_window_survives(self):
        now = 1_000_000.0
        buckets = {"live": [now - 599.0]}
        assert security_guard.sweep_expired("t", buckets, 600, now=now) == 0
        assert buckets == {"live": [now - 599.0]}

    def test_a_steadily_active_key_survives_its_own_oldest_stamp(self):
        """A single-stamp bucket cannot tell ``max`` from ``min``, and every
        other test here uses one — so this is the case that distinguishes them.

        A subject calling steadily has stamps on both sides of the window edge:
        the oldest has expired, the newest has not. Judging the key by its
        oldest stamp deletes the bucket that is actively being counted, which
        hands the caller a fresh allowance. The busier the subject, the more
        reliably it happens — the sweep would loosen the limit exactly for
        whoever is hitting it hardest.
        """
        now = 1_000_000.0
        stamps = [now - 900.0, now - 700.0, now - 300.0, now - 5.0]
        buckets = {"steady": list(stamps)}
        assert security_guard.sweep_expired("t", buckets, 600, now=now) == 0
        assert buckets == {"steady": stamps}

    def test_a_busy_caller_is_not_handed_a_fresh_allowance(self):
        """The same property observed through the limiter rather than the dict,
        so it survives a rewrite of the sweep's internals."""
        now = time.time()
        # Four hits already recorded across the window, the oldest expired.
        security_guard.BUCKETS["busy"] = [now - 700.0, now - 400.0,
                                          now - 200.0, now - 100.0]
        security_guard._SWEEPS.clear()
        # Limit 5 in a 600s window: three of those four still count, so the
        # caller has two left, not five.
        assert security_guard.rate_limited("busy", 5, 600) is False
        assert security_guard.rate_limited("busy", 5, 600) is False
        assert security_guard.rate_limited("busy", 5, 600) is True

    def test_limiting_still_refuses_at_the_same_request(self):
        """Sweeping is interleaved with counting now. If it dropped a live key
        the caller would get extra requests for free, which is the failure that
        matters."""
        for _ in range(8):
            assert security_guard.rate_limited("subject:/api/sms/send", 8, 600) is False
        assert security_guard.rate_limited("subject:/api/sms/send", 8, 600) is True

    def test_a_swept_key_and_an_expired_key_are_indistinguishable(self):
        """Removing the key must produce the same answer as leaving it to prune
        to empty on read — that equivalence is the entire safety argument."""
        now = time.time()
        stale = [now - 700.0]

        security_guard.BUCKETS["swept"] = list(stale)
        security_guard.sweep_expired(
            "security_guard.BUCKETS", security_guard.BUCKETS, 600, now=now)
        assert "swept" not in security_guard.BUCKETS

        security_guard.BUCKETS["kept"] = list(stale)
        swept_verdict = security_guard.rate_limited("swept", 1, 600)
        kept_verdict = security_guard.rate_limited("kept", 1, 600)
        assert swept_verdict == kept_verdict is False

    def test_the_widest_window_governs_not_the_current_call(self):
        """The ordering property the sweep rests on.

        ``BUCKETS`` is shared by callers with different windows — 600s for
        ``/api/sms/``, 60s for ``/api/chat/``. Sweeping against the window of
        whichever request happened to arrive would delete a live 600-second key
        the first time a 60-second one came through. Since a key can only have
        been created by a call that already recorded its own window, and the
        recorded maximum only grows, the widest window is always at least the
        one governing any existing key.
        """
        now = 1_000_000.0
        security_guard.sweep_expired("t", {}, 600, now=now - 10_000)
        buckets = {"slow": [now - 300.0]}
        assert security_guard.sweep_expired("t", buckets, 60, now=now) == 0
        assert set(buckets) == {"slow"}

    def test_without_that_the_key_would_have_gone(self):
        """Anti-vacuity partner: the key above is only saved by the remembered
        window, not because 300 seconds is somehow inside 60."""
        now = 1_000_000.0
        buckets = {"slow": [now - 300.0]}
        assert security_guard.sweep_expired("fresh", buckets, 60, now=now) == 1
        assert buckets == {}


class TestTheOlderGuardSweepsToo:
    def test_pulse_security_core_reclaims_its_own_dict(self):
        for i in range(50):
            pulse_security_core.rate_limited(
                path="/api/mobile/auth/recover", method="POST", ip_hash=f"h{i}")
        assert len(pulse_security_core._RATE_BUCKETS) == 50

        security_guard.sweep_expired(
            "pulse_security_core._RATE_BUCKETS", pulse_security_core._RATE_BUCKETS,
            600, now=time.time() + 3601)
        assert pulse_security_core._RATE_BUCKETS == {}

    def test_it_still_limits_at_its_configured_rule(self):
        """Partner: the sweep must not have loosened the guard it is housekeeping
        for. ``/api/mobile/auth/recover`` is 5 per 600s."""
        verdicts = [
            pulse_security_core.rate_limited(
                path="/api/mobile/auth/recover", method="POST", ip_hash="steady")
            for _ in range(6)
        ]
        assert [v["limited"] for v in verdicts] == [False] * 5 + [True]
