"""A circuit breaker for the delivery read path, and only for the delivery read path.

Why this is not ``suppliers/quota.py``
-------------------------------------
The supplier layer already has a breaker. ``quota.py``'s ``penalize()`` sets a
``blocked_until`` on the account and egress quota rows with exponential backoff,
and ``reserve_request`` consults it. That breaker is correct for what it defends:
CJ's rate limit. It fires on 429s and on CJ's own throttle codes.

It deliberately does not fire when CJ is simply *down* — a transport error or a
5xx never advances its failure counter. That looks like a gap, and for the read
path it is one: with no breaker, every shopper's request pays the full connect
plus read timeout before failing.

But ``quota.py``'s block covers the whole egress group, order submission
included. Teaching it to open on outages would let one network blip stop orders
from reaching the supplier. **A delivery estimate must never be able to block an
order.** So the read path gets its own breaker, scoped to itself, and
``quota.py`` is left alone.

What trips it, and what must not
--------------------------------
Only a failure to *reach* the provider counts. A provider that answers "no route
to that country" is a healthy provider giving a real answer; counting that as a
failure would open the circuit over an unserviceable destination and then
degrade every other destination with it. Reaching the provider and being told
something unwelcome is a success here.

What OPEN actually means
------------------------
It means *do not place a call*. It never means *do not answer*. Paired with the
cache's stale tier, an open circuit is precisely when a stale estimate earns its
keep: the provider is unreachable, the stored answer is the best truth available,
and it is served at lowered confidence. A fresh cache hit does not consult this
module at all — only a fetch does.

Two ways a breaker wedges itself
--------------------------------
Both are silent, and both are modelled here.

* **An unlimited half-open probe.** If every caller may probe once the cooldown
  expires, recovery is a thundering herd against the service that just came
  back. Exactly one probe is admitted.
* **A probe that never reports.** If the single probe is claimed and the worker
  holding it dies, the circuit stays half-open with the probe permanently taken,
  which is OPEN forever — a self-inflicted outage outliving the provider's.
  A claim therefore expires.

The bookkeeping fails open. An unreadable record is read as a fresh one, because
the cost of wrongly allowing a call is one bounded timeout, while the cost of
wrongly denying one is that estimates never recover.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

STATE_CLOSED = "CLOSED"
STATE_OPEN = "OPEN"
STATE_HALF_OPEN = "HALF_OPEN"
STATES = (STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN)

DENIED_COOLING_DOWN = "COOLING_DOWN"
DENIED_PROBE_IN_FLIGHT = "PROBE_IN_FLIGHT"

# Three consecutive unreachable calls, not one. One failure is a DNS hiccup or a
# dropped connection, and opening on it would degrade every shopper's confidence
# over noise. Three in a row, with no success between them, is not noise.
#
# The cost of the threshold is honest: each of the three pays the provider's full
# timeout, so the breaker starts helping roughly a minute into an outage rather
# than immediately. Lowering it trades that minute for false trips.
FAILURE_THRESHOLD = 3

# Backoff between reprobes. Capped far below quota.py's hour because this gates a
# read: a ten-minute ceiling means a recovered provider is noticed within ten
# minutes even with no traffic to drive the recovery.
BASE_COOLDOWN_SECONDS = 30.0
MAX_COOLDOWN_SECONDS = 600.0

# Must exceed the provider call's own worst case, or a slow-but-alive call looks
# like a dead probe and a second probe goes out beside it.
PROBE_TIMEOUT_SECONDS = 60.0


class BreakerRejected(ValueError):
    """A caller asked something the breaker cannot answer."""


class ProviderUnreachable(Exception):
    """Raised instead of calling when the circuit is open.

    Distinct from a provider error: nothing was attempted. The caller should
    fall back to a stored answer, not treat this as a fresh negative result.
    """

    def __init__(self, key: str, retry_after_seconds: float, reason: str):
        super().__init__(f"delivery provider {key} not called: {reason}")
        self.key = key
        self.retry_after_seconds = retry_after_seconds
        self.reason = reason


class NotProviderEvidence(Exception):
    """The call was abandoned before the provider was asked anything.

    A producer raises this when it discovers it cannot form a request — a variant
    with no stated weight, no SKU, no shipping properties. The distinction is not
    cosmetic: counted as failures, three such variants would open the circuit for
    the whole supplier and stop quoting the thousands of products whose data is
    complete. A handful of bad catalogue rows would take the feature down for
    everything. The provider was never contacted, so there is nothing here for it
    to be evidence *about*.

    Same reasoning as :func:`guard` catching ``Exception`` rather than
    ``BaseException``: only what the provider did counts against the provider.
    """

    def __init__(self, reason: str, detail: Optional[str] = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# The record, as pure data
# ---------------------------------------------------------------------------

def initial() -> Dict[str, Any]:
    return {
        "consecutive_failures": 0,
        "opened_at": None,
        "trips": 0,
        "probe_started_at": None,
    }


def _record(record: Any) -> Dict[str, Any]:
    """Coerce whatever was stored into a usable record, failing open."""
    if not isinstance(record, dict):
        return initial()
    clean = initial()
    failures = record.get("consecutive_failures")
    if isinstance(failures, int) and not isinstance(failures, bool) and failures >= 0:
        clean["consecutive_failures"] = failures
    trips = record.get("trips")
    if isinstance(trips, int) and not isinstance(trips, bool) and trips >= 0:
        clean["trips"] = trips
    for field in ("opened_at", "probe_started_at"):
        moment = record.get(field)
        if isinstance(moment, (int, float)) and not isinstance(moment, bool):
            clean[field] = float(moment)
    return clean


def cooldown_seconds(trips: int) -> float:
    """Exponential, capped. ``trips`` of 0 or 1 both mean the first cooldown."""
    if not isinstance(trips, int) or isinstance(trips, bool) or trips < 0:
        raise BreakerRejected("trips must be a non-negative integer")
    if trips <= 1:
        return BASE_COOLDOWN_SECONDS
    return min(MAX_COOLDOWN_SECONDS, BASE_COOLDOWN_SECONDS * 2 ** (trips - 1))


def _moment(now: Any) -> float:
    if not isinstance(now, (int, float)) or isinstance(now, bool):
        raise BreakerRejected("now must be a numeric timestamp")
    return float(now)


def state(record: Any, *, now: float) -> Dict[str, Any]:
    """Report the circuit's state without changing it."""
    moment = _moment(now)
    row = _record(record)
    opened_at = row["opened_at"]
    if opened_at is None:
        return {
            "state": STATE_CLOSED,
            "may_call": True,
            "reason": None,
            "retry_after_seconds": 0.0,
            "consecutive_failures": row["consecutive_failures"],
            "trips": row["trips"],
        }
    reopen_at = opened_at + cooldown_seconds(row["trips"])
    if moment < reopen_at:
        return {
            "state": STATE_OPEN,
            "may_call": False,
            "reason": DENIED_COOLING_DOWN,
            "retry_after_seconds": reopen_at - moment,
            "consecutive_failures": row["consecutive_failures"],
            "trips": row["trips"],
        }
    probe = row["probe_started_at"]
    held = probe is not None and moment - probe < PROBE_TIMEOUT_SECONDS
    return {
        "state": STATE_HALF_OPEN,
        "may_call": not held,
        "reason": DENIED_PROBE_IN_FLIGHT if held else None,
        "retry_after_seconds": (probe + PROBE_TIMEOUT_SECONDS - moment) if held else 0.0,
        "consecutive_failures": row["consecutive_failures"],
        "trips": row["trips"],
    }


def claim(record: Any, *, now: float) -> Dict[str, Any]:
    """Decide whether a call may be placed, taking the probe if one is needed.

    Returns ``{"record", "verdict"}``. The record is a new dict; the caller
    stores it. In the half-open state the returned record has the probe taken,
    so a second caller arriving before this one reports is denied.
    """
    moment = _moment(now)
    row = _record(record)
    verdict = state(row, now=moment)
    if verdict["state"] == STATE_HALF_OPEN and verdict["may_call"]:
        row = dict(row, probe_started_at=moment)
        verdict = dict(verdict, probe=True)
    else:
        verdict = dict(verdict, probe=False)
    return {"record": row, "verdict": verdict}


def succeeded(record: Any, *, now: float) -> Dict[str, Any]:
    """A reachable provider clears everything, including the trip count.

    The trip count is what makes the cooldown grow, so keeping it across a
    success would make a provider that recovers and later fails again back off as
    if it had never recovered.
    """
    _moment(now)
    return initial()


def failed(record: Any, *, now: float) -> Dict[str, Any]:
    """Record a failure to reach the provider."""
    moment = _moment(now)
    row = _record(record)
    before = state(row, now=moment)

    if before["state"] == STATE_OPEN:
        # A call that began before the circuit opened has landed. The circuit is
        # already open; advancing the trip count here would let one outage stack
        # several backoff doublings from calls that were all in flight together.
        return dict(row, probe_started_at=None)

    if before["state"] == STATE_HALF_OPEN:
        return {
            "consecutive_failures": row["consecutive_failures"] + 1,
            "opened_at": moment,
            "trips": row["trips"] + 1,
            "probe_started_at": None,
        }

    failures = row["consecutive_failures"] + 1
    if failures < FAILURE_THRESHOLD:
        return {
            "consecutive_failures": failures,
            "opened_at": None,
            "trips": row["trips"],
            "probe_started_at": None,
        }
    return {
        "consecutive_failures": failures,
        "opened_at": moment,
        "trips": row["trips"] + 1,
        "probe_started_at": None,
    }


def released(record: Any, *, probe_started_at: Optional[float]) -> Dict[str, Any]:
    """Hand back a probe that was claimed but never spent, counting nothing.

    Matched on the claim moment rather than cleared outright. An unconditional
    clear would also release a *different* caller's probe in the one case that
    matters — ours expired, someone else claimed a fresh one — quietly admitting
    two concurrent probes into a provider that is still failing, which is the
    stampede the half-open state exists to prevent.
    """
    row = _record(record)
    if probe_started_at is None or row["probe_started_at"] != probe_started_at:
        return row
    return dict(row, probe_started_at=None)


# ---------------------------------------------------------------------------
# A process-local registry over that record
# ---------------------------------------------------------------------------
#
# Per-process, like the cache's single-flight, and for the same reason: a shared
# breaker needs a store, and a store shared with order submission is how a
# delivery read starts being able to block a write. Several workers each
# discovering the outage separately is an acceptable price for that isolation.

_CIRCUITS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def _key(key: Any) -> str:
    if not isinstance(key, str) or not key.strip():
        raise BreakerRejected("a circuit needs a non-empty key")
    return key.strip()


def inspect_circuit(key: str, *, now: float) -> Dict[str, Any]:
    name = _key(key)
    with _LOCK:
        record = _CIRCUITS.get(name)
    return state(record, now=now)


def report_success(key: str, *, now: float) -> None:
    name = _key(key)
    fresh = succeeded(None, now=now)
    with _LOCK:
        _CIRCUITS[name] = fresh


def report_failure(key: str, *, now: float) -> Dict[str, Any]:
    name = _key(key)
    with _LOCK:
        _CIRCUITS[name] = failed(_CIRCUITS.get(name), now=now)
        return state(_CIRCUITS[name], now=now)


def reset(key: Optional[str] = None) -> None:
    with _LOCK:
        if key is None:
            _CIRCUITS.clear()
        else:
            _CIRCUITS.pop(_key(key), None)


def guard(key: str, producer: Callable[[], Any], *, clock: Callable[[], float]) -> Any:
    """Run ``producer`` only if the circuit permits, and record what happened.

    Takes a clock rather than a single moment because the interesting call is the
    slow one. A twenty-five second failure timed at its start would date the
    cooldown from before the call, shortening every backoff by however long the
    provider took to not answer.

    ``ProviderUnreachable`` means nothing was attempted. ``NotProviderEvidence``
    means the producer gave up before asking, and is re-raised uncounted with any
    probe handed back. Any other exception propagates after being counted, because
    the caller decides whether a failed fetch is servable from a stored answer —
    this module does not.
    """
    name = _key(key)
    with _LOCK:
        claimed_at = _moment(clock())
        decision = claim(_CIRCUITS.get(name), now=claimed_at)
        _CIRCUITS[name] = decision["record"]
    verdict = decision["verdict"]
    if not verdict["may_call"]:
        raise ProviderUnreachable(name, verdict["retry_after_seconds"], verdict["reason"])
    probe_at = claimed_at if verdict.get("probe") else None
    try:
        value = producer()
    except NotProviderEvidence:
        with _LOCK:
            _CIRCUITS[name] = released(_CIRCUITS.get(name), probe_started_at=probe_at)
        raise
    except Exception:
        # Deliberately not BaseException: an interrupt or a shutdown is not
        # evidence about the provider, and counting it would open the circuit on
        # a deploy.
        with _LOCK:
            _CIRCUITS[name] = failed(_CIRCUITS.get(name), now=_moment(clock()))
        raise
    with _LOCK:
        _CIRCUITS[name] = succeeded(None, now=_moment(clock()))
    return value
