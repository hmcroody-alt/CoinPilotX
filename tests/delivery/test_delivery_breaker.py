"""The delivery breaker stops calling a dead provider without becoming the outage.

What this file is defending
---------------------------
Four failures, none of which shows up as a red build:

* **No breaker at all.** Every shopper pays the provider's full timeout for the
  whole duration of an outage. The page still renders, so nothing fails.
* **A breaker that never closes again.** The provider recovers and the circuit
  does not, because the single half-open probe was claimed by a worker that died
  holding it. This outlives the real outage and has no external cause to find.
* **A breaker that stacks its own backoff.** Calls already in flight when the
  circuit opens each advance the trip count, so one outage produces several
  doublings and a ten-minute cooldown from a ten-second blip.
* **A breaker wired to the write path.** If a delivery read can open a circuit
  that order submission consults, a shipping-quote outage stops orders. That is a
  worse product than showing no estimate.

Time is injected everywhere. A test that sleeps to observe a cooldown is a test
that will be deleted for being slow.
"""

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.delivery import breaker as b

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def clean_circuits():
    b.reset()
    yield
    b.reset()


def drive_to_open(key="cj", *, at=NOW):
    """Fail the threshold number of times, which is what opens a circuit."""
    for offset in range(b.FAILURE_THRESHOLD):
        b.report_failure(key, now=at + offset)
    return at + b.FAILURE_THRESHOLD - 1


# ---------------------------------------------------------------------------
# Opening: enough evidence, and no less
# ---------------------------------------------------------------------------

def test_a_provider_with_no_history_is_called():
    assert b.state(None, now=NOW)["may_call"] is True
    assert b.state(None, now=NOW)["state"] == b.STATE_CLOSED


def test_one_failure_short_of_the_threshold_still_calls():
    """A single dropped connection must not degrade every shopper's estimate."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD - 1):
        record = b.failed(record, now=NOW)
        assert b.state(record, now=NOW)["state"] == b.STATE_CLOSED
    record = b.failed(record, now=NOW)
    assert b.state(record, now=NOW)["state"] == b.STATE_OPEN


def test_the_failures_have_to_be_consecutive_rather_than_merely_numerous():
    """Cumulative counting opens the circuit on a healthy provider eventually,
    because every provider fails sometimes over enough requests."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD * 4):
        record = b.failed(record, now=NOW)
        record = b.succeeded(record, now=NOW)
    assert b.state(record, now=NOW)["state"] == b.STATE_CLOSED


def test_an_open_circuit_reports_how_long_to_wait_rather_than_only_refusing():
    """A caller that cannot tell "wait 4 seconds" from "wait 9 minutes" cannot
    decide between retrying this request and serving a stored answer."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    verdict = b.state(record, now=NOW + 10)
    assert verdict["may_call"] is False
    assert verdict["reason"] == b.DENIED_COOLING_DOWN
    assert verdict["retry_after_seconds"] == pytest.approx(b.BASE_COOLDOWN_SECONDS - 10)


# ---------------------------------------------------------------------------
# Recovering, which is the half the naive implementation gets wrong
# ---------------------------------------------------------------------------

def test_the_cooldown_expiring_reopens_the_circuit_to_exactly_one_probe():
    """Unlimited probing turns recovery into a stampede at the moment the
    provider comes back — the worst possible moment for one."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    later = NOW + b.BASE_COOLDOWN_SECONDS

    first = b.claim(record, now=later)
    assert first["verdict"]["state"] == b.STATE_HALF_OPEN
    assert first["verdict"]["may_call"] is True
    assert first["verdict"]["probe"] is True

    second = b.claim(first["record"], now=later)
    assert second["verdict"]["may_call"] is False
    assert second["verdict"]["reason"] == b.DENIED_PROBE_IN_FLIGHT


def test_a_probe_that_never_reports_expires_rather_than_wedging_the_circuit():
    """The failure this defends outlives the provider's outage and has no
    external cause: a worker died mid-probe, so the probe is held forever, so the
    circuit is half-open-but-uncallable, which is open with no way out."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    later = NOW + b.BASE_COOLDOWN_SECONDS
    held = b.claim(record, now=later)["record"]

    assert b.state(held, now=later + b.PROBE_TIMEOUT_SECONDS - 1)["may_call"] is False
    assert b.state(held, now=later + b.PROBE_TIMEOUT_SECONDS)["may_call"] is True


def test_a_successful_probe_closes_the_circuit_and_forgets_the_backoff():
    """Keeping the trip count across a recovery makes a provider that fails again
    next week back off as though it had never recovered."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    later = NOW + b.BASE_COOLDOWN_SECONDS
    probing = b.claim(record, now=later)["record"]

    closed = b.succeeded(probing, now=later + 1)
    assert b.state(closed, now=later + 1)["state"] == b.STATE_CLOSED
    assert closed["trips"] == 0
    assert closed["consecutive_failures"] == 0
    assert closed["probe_started_at"] is None


def test_a_failed_probe_reopens_for_longer_than_the_first_time():
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    first_wait = b.state(record, now=NOW)["retry_after_seconds"]

    later = NOW + b.BASE_COOLDOWN_SECONDS
    probing = b.claim(record, now=later)["record"]
    reopened = b.failed(probing, now=later + 5)

    assert b.state(reopened, now=later + 5)["state"] == b.STATE_OPEN
    assert b.state(reopened, now=later + 5)["retry_after_seconds"] > first_wait
    assert reopened["probe_started_at"] is None


def test_failures_arriving_while_the_circuit_is_already_open_do_not_stack_backoff():
    """Requests in flight when the circuit opens all land afterwards. If each one
    doubles the cooldown, a brief blip produces the maximum backoff."""
    record = b.initial()
    for _ in range(b.FAILURE_THRESHOLD):
        record = b.failed(record, now=NOW)
    opened_wait = b.state(record, now=NOW)["retry_after_seconds"]

    for offset in range(1, 8):
        record = b.failed(record, now=NOW + offset)

    assert b.state(record, now=NOW)["retry_after_seconds"] == pytest.approx(opened_wait)
    assert record["trips"] == 1


@pytest.mark.parametrize("trips,expected", [
    (0, b.BASE_COOLDOWN_SECONDS), (1, b.BASE_COOLDOWN_SECONDS),
    (2, b.BASE_COOLDOWN_SECONDS * 2), (3, b.BASE_COOLDOWN_SECONDS * 4),
])
def test_the_cooldown_grows_with_repeated_trips(trips, expected):
    assert b.cooldown_seconds(trips) == expected


def test_the_cooldown_is_capped_so_a_recovered_provider_is_noticed():
    """An uncapped exponential reaches days. A read path that gives up for a day
    is indistinguishable from a removed feature."""
    assert b.cooldown_seconds(99) == b.MAX_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# Failing open, not closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("junk", [
    None, "OPEN", 42, [], {"opened_at": "soon"}, {"opened_at": True},
    {"consecutive_failures": -3}, {"trips": "many"},
])
def test_an_unreadable_record_is_read_as_a_fresh_one(junk):
    """Wrongly allowing a call costs one bounded timeout. Wrongly denying one
    costs the feature until someone notices, so the ambiguity resolves to CLOSED."""
    verdict = b.state(junk, now=NOW)
    assert verdict["state"] == b.STATE_CLOSED
    assert verdict["may_call"] is True


@pytest.mark.parametrize("bad", [None, "now", True, object()])
def test_an_unusable_clock_reading_raises_rather_than_being_guessed(bad):
    with pytest.raises(b.BreakerRejected):
        b.state(b.initial(), now=bad)


@pytest.mark.parametrize("bad", ["", "   ", None, 7])
def test_a_circuit_needs_a_key_because_one_global_circuit_blinds_every_supplier(bad):
    with pytest.raises(b.BreakerRejected):
        b.report_failure(bad, now=NOW)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------

def test_a_reachable_provider_is_a_success_even_when_its_answer_is_unwelcome():
    """"We do not ship to that country" is a healthy provider answering. Counting
    it as a failure opens the circuit over an unserviceable destination and then
    degrades every other destination with it."""
    unwelcome = {"supported": False, "reason": "NO_ROUTE"}
    for offset in range(b.FAILURE_THRESHOLD * 3):
        assert b.guard("cj", lambda: unwelcome, clock=lambda: NOW + offset) == unwelcome
    assert b.inspect_circuit("cj", now=NOW)["state"] == b.STATE_CLOSED


def test_an_open_circuit_does_not_call_the_provider_at_all():
    calls = []

    def producer():
        calls.append(1)
        raise RuntimeError("cj down")

    for offset in range(b.FAILURE_THRESHOLD):
        with pytest.raises(RuntimeError):
            b.guard("cj", producer, clock=lambda: NOW + offset)
    assert len(calls) == b.FAILURE_THRESHOLD

    with pytest.raises(b.ProviderUnreachable):
        b.guard("cj", producer, clock=lambda: NOW + b.FAILURE_THRESHOLD)
    assert len(calls) == b.FAILURE_THRESHOLD, "the circuit was open and it called anyway"


def test_not_calling_is_distinguishable_from_calling_and_failing():
    """A caller that cannot tell these apart treats an untried request as a fresh
    negative answer, and caches it."""
    for offset in range(b.FAILURE_THRESHOLD):
        with pytest.raises(RuntimeError):
            b.guard("cj", lambda: (_ for _ in ()).throw(RuntimeError("down")),
                    clock=lambda: NOW + offset)
    with pytest.raises(b.ProviderUnreachable) as caught:
        b.guard("cj", lambda: None, clock=lambda: NOW + 1)
    assert caught.value.reason == b.DENIED_COOLING_DOWN
    assert caught.value.retry_after_seconds > 0
    assert not isinstance(caught.value, RuntimeError)


def test_a_slow_failure_is_timed_when_it_finishes_not_when_it_started():
    """Dating a twenty-five second failure from its start shortens every cooldown
    by however long the provider took to not answer."""
    ticks = iter([NOW, NOW + 25])

    def producer():
        raise RuntimeError("timed out")

    b.report_failure("cj", now=NOW)
    b.report_failure("cj", now=NOW)
    # The next failure is the one that opens the circuit.
    with pytest.raises(RuntimeError):
        b.guard("cj", producer, clock=lambda: next(ticks))

    verdict = b.inspect_circuit("cj", now=NOW + 25)
    assert verdict["retry_after_seconds"] == pytest.approx(b.BASE_COOLDOWN_SECONDS)


def test_an_interrupt_is_not_evidence_about_the_provider():
    """Counting a shutdown as a provider failure opens circuits during a deploy."""
    with pytest.raises(KeyboardInterrupt):
        b.guard("cj", lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
                clock=lambda: NOW)
    assert b.inspect_circuit("cj", now=NOW)["consecutive_failures"] == 0


def test_one_suppliers_outage_does_not_blind_another():
    for offset in range(b.FAILURE_THRESHOLD):
        b.report_failure("cj", now=NOW + offset)
    assert b.inspect_circuit("cj", now=NOW)["state"] == b.STATE_OPEN
    assert b.inspect_circuit("other", now=NOW)["state"] == b.STATE_CLOSED
    assert b.guard("other", lambda: "quoted", clock=lambda: NOW) == "quoted"


def test_a_success_through_the_runner_clears_a_partial_failure_streak():
    b.report_failure("cj", now=NOW)
    assert b.inspect_circuit("cj", now=NOW)["consecutive_failures"] == 1
    b.guard("cj", lambda: "quoted", clock=lambda: NOW + 1)
    assert b.inspect_circuit("cj", now=NOW + 1)["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# The isolation that is the reason this module exists
# ---------------------------------------------------------------------------

def test_the_delivery_breaker_cannot_reach_the_supplier_quota_breaker():
    """quota.py's block covers the whole egress group, order submission included.
    If this module could write to it, a shipping-quote outage could stop orders —
    which is the entire reason this is a second breaker rather than a change
    there.

    Asserted on the import graph rather than on the text, because the text
    discusses quota.py at length on purpose and a substring check would only be
    measuring the prose.
    """
    tree = ast.parse(open(b.__file__, encoding="utf-8").read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
    offending = [name for name in imported
                 if "business_os" in name or name.endswith("quota")]
    assert offending == [], f"the delivery breaker imports the supplier layer: {offending}"
    assert imported <= {"__future__", "__future__.annotations", "threading", "typing",
                        "typing.Any", "typing.Callable", "typing.Dict", "typing.Optional"}, \
        "the breaker grew a dependency; it is meant to be pure policy over a clock"


# --- a call that was never placed -----------------------------------------
#
# Four things go wrong if "we could not describe the request" is counted as "the
# provider failed", and all four are invisible in the breaker's own arithmetic:
# the circuit opens for a whole supplier on the strength of a few bad catalogue
# rows; the products with complete data stop being quoted; the cooldown doubles on
# each further bad row; and the logs blame an outage that never happened.


def _boom():
    raise RuntimeError("the provider failed")


def _abandoned():
    raise b.NotProviderEvidence("variant_weight_unknown")


def test_a_call_that_was_never_placed_is_not_counted_against_the_provider():
    for _ in range(b.FAILURE_THRESHOLD + 3):
        with pytest.raises(b.NotProviderEvidence):
            b.guard("never", _abandoned, clock=lambda: NOW)
    circuit = b.inspect_circuit("never", now=NOW)
    assert circuit["state"] == b.STATE_CLOSED
    assert circuit["consecutive_failures"] == 0
    assert circuit["trips"] == 0


def test_an_abandoned_call_does_not_clear_a_real_failure_streak_either():
    """It is not a success. A supplier that failed twice and then met one variant
    we could not describe has still failed twice, and forgetting that would keep
    the circuit permanently one failure short of opening."""
    for _ in range(b.FAILURE_THRESHOLD - 1):
        with pytest.raises(RuntimeError):
            b.guard("mixed", _boom, clock=lambda: NOW)
    with pytest.raises(b.NotProviderEvidence):
        b.guard("mixed", _abandoned, clock=lambda: NOW)
    assert b.inspect_circuit("mixed", now=NOW)["consecutive_failures"] \
        == b.FAILURE_THRESHOLD - 1


def test_an_abandoned_probe_is_handed_back_rather_than_spent():
    """The recovery probe is the scarcest thing the breaker owns: one call per
    cooldown decides whether the provider is back. Spending it on a request that
    was never sent means the provider stays presumed-down for another full
    cooldown with nothing having been learned about it."""
    for _ in range(b.FAILURE_THRESHOLD):
        with pytest.raises(RuntimeError):
            b.guard("handback", _boom, clock=lambda: NOW)
    recovered = NOW + b.BASE_COOLDOWN_SECONDS + 1
    assert b.inspect_circuit("handback", now=recovered)["state"] == b.STATE_HALF_OPEN

    with pytest.raises(b.NotProviderEvidence):
        b.guard("handback", _abandoned, clock=lambda: recovered)

    after = b.inspect_circuit("handback", now=recovered)
    assert after["state"] == b.STATE_HALF_OPEN, "the probe was consumed by a call never made"
    assert after["may_call"] is True
    assert b.guard("handback", lambda: "alive", clock=lambda: recovered) == "alive"


def test_releasing_a_probe_leaves_another_callers_probe_alone():
    """The one case an unconditional clear gets wrong: ours expired, someone else
    claimed a fresh one, and clearing it admits two concurrent probes into a
    provider that is still failing — the stampede half-open exists to prevent."""
    theirs = dict(b.initial(), opened_at=NOW, trips=1, probe_started_at=NOW + 90)
    assert b.released(theirs, probe_started_at=NOW + 5)["probe_started_at"] == NOW + 90
    assert b.released(theirs, probe_started_at=NOW + 90)["probe_started_at"] is None
    assert b.released(theirs, probe_started_at=None)["probe_started_at"] == NOW + 90


def test_a_closed_circuit_holds_no_probe_to_hand_back():
    """A caller in the closed state never took one, so releasing must be a no-op
    rather than an eraser aimed at whatever marker happens to be there."""
    fresh = b.initial()
    assert b.released(fresh, probe_started_at=NOW) == fresh
