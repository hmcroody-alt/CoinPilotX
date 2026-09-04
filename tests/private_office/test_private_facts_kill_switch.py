"""Stage 11 — ``PRIVATE_FACTS_ENABLED=false`` really turns Private Facts off.

``private_facts`` is the only Private Office row that is both IMPLEMENTED and
carries a ``flag_env``. That combination is what makes the kill switch load-
bearing: every other unavailable capability is unavailable because no code
exists, which no flag can undo and no client can misread. This one is live code
behind a runtime switch, so "off" has to be a property that holds all the way
out to the surfaces, not a value sitting in an env var that one code path
happens to consult.

The failure this file exists to prevent is a partial off. There are four places
that independently decide whether a member reaches this capability — the matrix,
the shared access decision, the product state the native screen renders, and the
HTTP gate — and the switch is only honest if all four flip together. A switch
that flipped three of them would present as: the office lists Private Facts as
available, the member taps it, and the route 404s. That reads as a broken app
rather than a disabled feature, and it happens on the day of an incident, which
is the one day the switch is being used.

Three properties, then:

  1. **Off means off at every rung, including the top.** PRIVATE_OFFICE is the
     tier that pays for everything; it must not be the tier that routes around
     the switch.
  2. **Off is TEMPORARILY_DISABLED, not UPGRADE_REQUIRED and not NOT_IMPLEMENTED.**
     A disabled capability must never be sold — an upgrade prompt in front of a
     switched-off feature takes money for something the member would not get —
     and it must not claim to be unbuilt, because it is built and it is coming
     back.
  3. **Nothing downstream can bypass it.** The native client has no path to the
     store that does not pass the HTTP gate, and the gate reads the same
     decision as the matrix.

Run either way::

    python -m pytest tests/private_office/test_private_facts_kill_switch.py
    python tests/private_office/test_private_facts_kill_switch.py
"""

import contextlib
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.private_office import access  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import office  # noqa: E402
from services.private_office import tiers  # noqa: E402

FEATURE_ID = "private_facts"
FLAG = "PRIVATE_FACTS_ENABLED"

ALL_TIERS = (
    tiers.TIER_FREE,
    tiers.TIER_PREMIUM,
    tiers.TIER_PRIVATE,
    tiers.TIER_PRIVATE_OFFICE,
)

#: Tiers that would reach Private Facts if the switch were on. The switch is
#: only interesting for these — for FREE and PREMIUM the row is already out of
#: reach and a flag flip changes nothing observable.
ENTITLED_TIERS = (tiers.TIER_PRIVATE, tiers.TIER_PRIVATE_OFFICE)


@contextlib.contextmanager
def flag(value):
    """Set (or unset, with ``None``) the kill switch for the duration.

    The env var is restored rather than deleted on exit: this suite must leave
    the process exactly as it found it, or a later test in the same run inherits
    a disabled feature and fails somewhere unrelated.
    """
    previous = os.environ.get(FLAG)
    if value is None:
        os.environ.pop(FLAG, None)
    else:
        os.environ[FLAG] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(FLAG, None)
        else:
            os.environ[FLAG] = previous


def resolved(tier):
    """A healthy resolver record, so the switch is the only variable."""
    return {"resolver_state": tiers.RESOLVER_OK, "effective_tier": tier}


# --- the switch is real -----------------------------------------------------

def test_flag_off_disables_the_feature_at_every_tier_including_the_top():
    for raw in ("false", "0", "off", "no", "FALSE", "Off"):
        with flag(raw):
            for tier in ALL_TIERS:
                state = matrix.availability(FEATURE_ID, tier)
                assert state["availability"] == matrix.AVAIL_FEATURE_DISABLED, (
                    f"{FLAG}={raw!r} at {tier} left the feature "
                    f"{state['availability']}"
                )


def test_flag_absent_or_truthy_leaves_the_feature_available():
    # The default must be ON. A switch that defaults to off would make the
    # feature vanish on any host that simply never set the variable.
    for raw in (None, "", "1", "true", "on", "yes", "TRUE"):
        with flag(raw):
            for tier in ENTITLED_TIERS:
                state = matrix.availability(FEATURE_ID, tier)
                assert state["availability"] == matrix.AVAIL_ENTITLED, (
                    f"{FLAG}={raw!r} at {tier} gave {state['availability']}"
                )


def test_the_row_still_reports_itself_as_built_while_switched_off():
    # `implementation` describes the code; the flag describes the moment. A
    # switch that rewrote IMPLEMENTED to NOT_IMPLEMENTED would be lying about a
    # module that exists, and the surfaces would offer no way back.
    with flag("false"):
        state = matrix.availability(FEATURE_ID, tiers.TIER_PRIVATE_OFFICE)
        assert state["implementation"] == matrix.IMPL_IMPLEMENTED
        assert FEATURE_ID not in matrix.implemented_feature_ids()

    with flag(None):
        assert FEATURE_ID in matrix.implemented_feature_ids()


# --- every surface flips with it --------------------------------------------

def test_the_shared_access_decision_refuses_and_offers_nothing_to_buy():
    with flag("false"):
        for tier in ENTITLED_TIERS:
            decision = access.decide(resolved(tier), FEATURE_ID)
            assert decision["decision"] == access.FEATURE_DISABLED
            # Dropped on purpose: a surface that rendered an upgrade prompt from
            # a leftover minimum_tier would be charging for a switched-off thing.
            assert decision["minimum_tier"] == ""
            assert decision["decision"] != access.NOT_ENTITLED


def test_the_office_lists_it_as_temporarily_off_rather_than_unbuilt():
    with flag("false"):
        state = office.product_state(tiers.TIER_PRIVATE_OFFICE)
        rows = {row["feature_id"]: row for row in state["unavailable"]}
        assert FEATURE_ID in rows, "switched-off row vanished from the office"
        row = rows[FEATURE_ID]
        # The three words the native screen keeps apart. TEMPORARILY_DISABLED is
        # the one that says "this is coming back".
        assert row["reason"] == "TEMPORARILY_DISABLED"
        assert row["availability"] == matrix.AVAIL_FEATURE_DISABLED
        assert row["opens"] is False
        assert FEATURE_ID not in {r["feature_id"] for r in state["available"]}


def test_the_entry_does_not_open_on_a_switched_off_only_office():
    # With Private Facts off, nothing else in the office is currently built, so
    # the entry has nothing to open and nothing to sell.
    with flag("false"):
        state = office.product_state(tiers.TIER_PRIVATE_OFFICE)
        assert state["available"] == []
        assert state["state"] == office.ENTRY_UNAVAILABLE
        assert state["upgrade_tier"] is None


def test_the_entry_opens_again_when_the_switch_comes_back():
    # The point of a switch rather than an edit: it is reversible in place, with
    # no deploy and no data touched.
    with flag(None):
        state = office.product_state(tiers.TIER_PRIVATE_OFFICE)
        assert FEATURE_ID in {row["feature_id"] for row in state["available"]}
        assert state["state"] == office.ENTRY_AVAILABLE


def test_a_degraded_resolver_is_still_not_reported_as_disabled():
    # Independent failures must stay independent. If a degraded resolve started
    # reporting FEATURE_DISABLED, an outage would look like an intentional
    # shutdown — and the retry affordance the member needs would disappear.
    with flag("false"):
        decision = access.decide(
            {"resolver_state": tiers.RESOLVER_DEGRADED, "effective_tier": ""},
            FEATURE_ID,
        )
        assert decision["decision"] == access.UNAVAILABLE


def test_the_switch_touches_nothing_else_in_the_office():
    # A kill switch with collateral is worse than no kill switch: reaching for
    # it during an incident must not change any other row's answer.
    with flag(None):
        before = office.product_state(tiers.TIER_PRIVATE_OFFICE)
    with flag("false"):
        after = office.product_state(tiers.TIER_PRIVATE_OFFICE)

    def others(state):
        rows = state["available"] + state["unavailable"]
        return {r["feature_id"]: r for r in rows if r["feature_id"] != FEATURE_ID}

    assert others(before) == others(after)


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
