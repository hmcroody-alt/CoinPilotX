"""The declared half of a delivery promise, and the defaults it refuses to have.

Five failures these tests exist to prevent, in the order they would cost the most:

* **A default handling allowance or buffer.** ``handling or 0`` is the whole bug
  in this domain: invisible, and it always moves the date earlier. If either
  reader ever returns a number nobody set, the engine stops being inert and starts
  promising dates on a deployment that configured nothing.
* **A typo read as absence.** ``PULSE_DELIVERY_BUFFER_DAYS=tow`` must be an error,
  not silence. Silence is indistinguishable from an unconfigured deployment, so
  an operator who *did* decide sees no estimates and a variable that looks right.
* **A zero handling allowance raised to one.** ``normalize.transit_days`` does
  exactly that, correctly, for freight. Borrowed here it would add a day to every
  promise on the platform -- and nothing would look wrong.
* **Quoting in the wrong timezone, in the early direction.** A Shenzhen warehouse
  at 02:00 on the 2nd is 18:00 on the 1st in UTC. Every fall-through in
  :func:`zone_for` must land on a date no earlier than the truth, including when
  the zone database is absent altogether.
* **A route policy or ceiling this module spells for itself.** Both belong to
  ``routing``; a second spelling drifts.

The environment is the subject under test, so every test sets it explicitly and
the fixture clears it. A test that inherited an ambient value would pass against a
module that ignored its own variable names.
"""
from __future__ import annotations

import datetime as dt
import re
from datetime import datetime, timedelta, timezone

import pytest

from services.delivery import estimate, policy, routing

ENV_KEYS = (policy.ENV_HANDLING, policy.ENV_BUFFER, policy.ENV_CUTOFF,
            policy.ENV_CLOSURES, policy.ENV_CEILING, policy.ENV_ROUTE_POLICY)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No ambient declaration reaches a test.

    Enumerated from the module's own name-holding constants rather than from a
    list retyped here, so a variable this module starts reading and these tests
    forget about still gets cleared -- and the one test that asserts the set of
    names is the place that notices.
    """
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def at(hour, *, day=15, month=1, year=2026, tz=timezone.utc):
    return datetime(year, month, day, hour, 0, tzinfo=tz)


def day_of(record, key):
    """Read a window edge back as a date.

    `arrival_window` returns ISO strings, deliberately -- a whole-day range has no
    defensible time of day, so there is nothing for a datetime to carry. These
    tests still need to do date arithmetic on the edges, and parsing here keeps
    that from being mistaken for an opinion about the wire type.
    """
    return dt.date.fromisoformat(record[key])


# ---------------------------------------------------------------------------
# The refusal that makes the engine ship inert
# ---------------------------------------------------------------------------

def test_an_unconfigured_deployment_declares_no_handling_and_no_buffer():
    """The whole rollout gate. Both None means `arrival_window` refuses, so the
    engine exercises every path and promises nothing."""
    assert policy.handling() is None
    assert policy.buffer_days() is None


def test_an_unconfigured_deployment_cannot_estimate_and_says_so():
    report = policy.declared()
    assert report["can_estimate"] is False
    assert report["errors"] == []


def test_the_undeclared_pair_composes_to_the_estimators_own_refusals():
    """Asserted through `arrival_window` rather than on the None, because the
    claim worth making is not "this returns None" but "this produces no window".
    Either refusal reason is the right one; which comes first is `estimate`'s
    decision and not this module's."""
    transit = {"min_days": 7, "max_days": 12, "basis": estimate.BASIS_CALENDAR}
    got = estimate.arrival_window(transit=transit, handling=policy.handling(),
                                  buffer_days=policy.buffer_days(),
                                  now=policy.now_at("CN"))
    assert got["state"] == estimate.STATE_UNAVAILABLE
    assert got["reason"] in (estimate.REASON_NO_HANDLING, estimate.REASON_NO_BUFFER)
    assert got.get("earliest") is None


def test_a_declared_pair_produces_a_real_window(monkeypatch):
    """The other side of the gate: once declared, the same call answers. Without
    this the suite above is satisfied by a module that can only ever say no."""
    monkeypatch.setenv(policy.ENV_HANDLING, "2-4")
    monkeypatch.setenv(policy.ENV_BUFFER, "2")
    transit = {"min_days": 7, "max_days": 12, "basis": estimate.BASIS_CALENDAR}
    got = estimate.arrival_window(transit=transit, handling=policy.handling(),
                                  buffer_days=policy.buffer_days(),
                                  now=policy.now_at("CN"))
    assert got["state"] == estimate.STATE_ESTIMATED
    assert got["earliest"] < got["latest"]
    assert policy.declared()["can_estimate"] is True


# ---------------------------------------------------------------------------
# The handling allowance: its own parser, and why
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,low,high", [
    ("2", 2, 2), ("2-4", 2, 4), (" 2 - 4 ", 2, 4), ("2 to 4", 2, 4),
    ("2–4", 2, 4), ("2—4", 2, 4), ("4-2", 2, 4), ("0", 0, 0), ("0-1", 0, 1),
])
def test_a_declared_allowance_is_read_as_a_typed_range(monkeypatch, raw, low, high):
    monkeypatch.setenv(policy.ENV_HANDLING, raw)
    assert policy.handling() == {"min_days": low, "max_days": high,
                                 "basis": estimate.BASIS_BUSINESS}


def test_a_zero_handling_allowance_survives_as_zero(monkeypatch):
    """The reason this module does not reuse `normalize.transit_days`. That parser
    raises a 0 lower bound to 1, which is right for freight -- a parcel cannot
    cross a border in no days -- and wrong for a warehouse that picks same-day.
    Borrowed, it would add a day to every promise on the platform and nothing
    would look wrong."""
    monkeypatch.setenv(policy.ENV_HANDLING, "0-2")
    assert policy.handling()["min_days"] == 0


def test_a_zero_handling_allowance_really_ships_the_same_day(monkeypatch):
    """The consequence, not the value. A same-day allowance must leave the
    dispatch date alone, which is the property `estimate._advance` documents and
    the only reason the 0 matters."""
    monkeypatch.setenv(policy.ENV_HANDLING, "0")
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    transit = {"min_days": 3, "max_days": 3, "basis": estimate.BASIS_CALENDAR}
    now = at(9, day=15)  # a Thursday
    got = estimate.arrival_window(transit=transit, handling=policy.handling(),
                                  buffer_days=policy.buffer_days(), now=now)
    assert day_of(got, "earliest") == dt.date(2026, 1, 18)


def test_the_declared_basis_matches_the_arithmetic_the_estimator_performs(monkeypatch):
    """`arrival_window` advances handling in business days whatever the range
    says. Declaring anything else here would produce a record whose components
    contradicted its own dates."""
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    assert policy.handling()["basis"] == estimate.BASIS_BUSINESS


@pytest.mark.parametrize("raw", ["two", "2 days", "-1", "2-", "-2", "2..4", "",
                                 "   ", "2,4", "1e3", "2-4-6", "٢"])
def test_an_unreadable_allowance_is_an_error_or_an_absence_never_a_number(
        monkeypatch, raw):
    """A blank is absence; anything else typed is an error. What must never
    happen is a third outcome where an unreadable string becomes days."""
    monkeypatch.setenv(policy.ENV_HANDLING, raw)
    if raw.strip():
        with pytest.raises(policy.PolicyInvalid):
            policy.handling()
    else:
        assert policy.handling() is None


def test_an_implausible_allowance_is_refused(monkeypatch):
    """A ceiling catches a variable holding a year or a phone number. Set loose
    enough that a genuinely slow warehouse is not second-guessed."""
    monkeypatch.setenv(policy.ENV_HANDLING, str(policy.MAX_HANDLING_DAYS + 1))
    with pytest.raises(policy.PolicyInvalid):
        policy.handling()


def test_an_allowance_at_the_ceiling_is_allowed(monkeypatch):
    monkeypatch.setenv(policy.ENV_HANDLING, str(policy.MAX_HANDLING_DAYS))
    assert policy.handling()["max_days"] == policy.MAX_HANDLING_DAYS


def test_the_allowance_is_the_shape_the_estimator_validates(monkeypatch):
    """Pinned through `estimate._range` rather than against a literal dict, so a
    renamed key in the contract fails here instead of one layer down."""
    monkeypatch.setenv(policy.ENV_HANDLING, "1-3")
    assert estimate._range(policy.handling(), "handling") == policy.handling()


# ---------------------------------------------------------------------------
# The buffer: zero is a decision, unset is not
# ---------------------------------------------------------------------------

def test_a_declared_zero_buffer_is_honoured_as_zero(monkeypatch):
    """Not folded into None. A deployment that decided to add no padding decided
    something, and the difference between that and having decided nothing is the
    difference between an estimate and a refusal."""
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    assert policy.buffer_days() == 0
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    assert policy.declared()["can_estimate"] is True


def test_a_buffer_range_is_refused(monkeypatch):
    """A buffer is one number. A range would have to be resolved to one somewhere,
    and the place that resolved it would be choosing padding nobody declared."""
    monkeypatch.setenv(policy.ENV_BUFFER, "2-4")
    with pytest.raises(policy.PolicyInvalid):
        policy.buffer_days()


@pytest.mark.parametrize("raw", ["tow", "-1", "2 days", "1.5"])
def test_an_unreadable_buffer_is_an_error_not_a_silent_zero(monkeypatch, raw):
    monkeypatch.setenv(policy.ENV_BUFFER, raw)
    with pytest.raises(policy.PolicyInvalid):
        policy.buffer_days()


def test_an_implausible_buffer_is_refused(monkeypatch):
    monkeypatch.setenv(policy.ENV_BUFFER, str(policy.MAX_BUFFER_DAYS + 1))
    with pytest.raises(policy.PolicyInvalid):
        policy.buffer_days()


def test_the_buffer_extends_only_the_late_end(monkeypatch):
    """Asserted here as well as in the estimator's own suite, because it is the
    property that makes a buffer safe. A buffer that widened both ends would move
    the early date earlier, which is the one direction this domain forbids."""
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    transit = {"min_days": 5, "max_days": 5, "basis": estimate.BASIS_CALENDAR}
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    unpadded = estimate.arrival_window(transit=transit, handling=policy.handling(),
                                       buffer_days=policy.buffer_days(), now=at(9))
    monkeypatch.setenv(policy.ENV_BUFFER, "3")
    padded = estimate.arrival_window(transit=transit, handling=policy.handling(),
                                     buffer_days=policy.buffer_days(), now=at(9))
    assert padded["earliest"] == unpadded["earliest"]
    assert day_of(padded, "latest") == day_of(unpadded, "latest") + timedelta(days=3)


# ---------------------------------------------------------------------------
# The dispatch cutoff
# ---------------------------------------------------------------------------

def test_no_declared_cutoff_models_no_cutoff():
    """The optimistic reading, and it is still the default: a warehouse schedule
    nobody declared is one this platform does not know. Reported by `declared` so
    it is a visible gap rather than an invisible day."""
    assert policy.dispatch_cutoff_hour() is None
    assert policy.declared()["dispatch_cutoff_hour"] is None


@pytest.mark.parametrize("raw,hour", [("0", 0), ("14", 14), ("23", 23), (" 9 ", 9)])
def test_a_declared_cutoff_is_read_as_an_hour(monkeypatch, raw, hour):
    monkeypatch.setenv(policy.ENV_CUTOFF, raw)
    assert policy.dispatch_cutoff_hour() == hour


@pytest.mark.parametrize("raw", ["24", "-1", "noon", "9.5", "9pm"])
def test_an_unreadable_cutoff_is_an_error(monkeypatch, raw):
    monkeypatch.setenv(policy.ENV_CUTOFF, raw)
    with pytest.raises(policy.PolicyInvalid):
        policy.dispatch_cutoff_hour()


def test_a_declared_cutoff_pushes_a_late_order_to_the_next_day(monkeypatch):
    """The consequence. Asserted against the estimator so the hour is proved to
    be the hour that module compares, and in the origin's zone -- which is the
    pair of decisions this module exists to keep together."""
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    monkeypatch.setenv(policy.ENV_CUTOFF, "16")
    transit = {"min_days": 4, "max_days": 4, "basis": estimate.BASIS_CALENDAR}
    zone = policy.zone_for("CN")
    early = estimate.arrival_window(
        transit=transit, handling=policy.handling(), buffer_days=policy.buffer_days(),
        now=at(10, tz=zone), dispatch_cutoff_hour=policy.dispatch_cutoff_hour())
    late = estimate.arrival_window(
        transit=transit, handling=policy.handling(), buffer_days=policy.buffer_days(),
        now=at(17, tz=zone), dispatch_cutoff_hour=policy.dispatch_cutoff_hour())
    # The cutoff owns exactly one day of movement, and it is the day handling
    # *starts* on. What that becomes at the far end is `_advance`'s business and is
    # not one day: this instant is a Thursday, so a cutoff that pushes the start to
    # Friday pushes a one-business-day allowance across the weekend to Monday and
    # the arrival moves three. Asserting +1 on the arrival would have been asserting
    # that weekends do not exist.
    assert (dt.date.fromisoformat(late["components"]["dispatch_from"])
            == dt.date.fromisoformat(early["components"]["dispatch_from"])
            + timedelta(days=1))
    assert day_of(late, "earliest") > day_of(early, "earliest")


# ---------------------------------------------------------------------------
# Declared closures
# ---------------------------------------------------------------------------

def test_no_declared_closures_is_an_empty_calendar_not_a_claim():
    assert policy.closures() == ()


@pytest.mark.parametrize("raw,expected", [
    ("2026-02-17", ("2026-02-17",)),
    ("2026-02-17,2026-02-18", ("2026-02-17", "2026-02-18")),
    (" 2026-02-18 , 2026-02-17 ", ("2026-02-17", "2026-02-18")),
    ("2026-02-17;2026-02-18", ("2026-02-17", "2026-02-18")),
    ("2026-02-17,,2026-02-17", ("2026-02-17",)),
])
def test_declared_closures_are_sorted_and_deduplicated(monkeypatch, raw, expected):
    """Order-independent because the count reaches the estimate's
    `components.holidays_modelled`. Two identical deployments that typed the dates
    in a different order must not look different to an accuracy review."""
    monkeypatch.setenv(policy.ENV_CLOSURES, raw)
    assert policy.closures() == expected


@pytest.mark.parametrize("raw", ["17-02-2026", "2026-02-30", "Feb 17", "2026-2-17",
                                 "20260217"])
def test_an_unreadable_closure_date_is_an_error(monkeypatch, raw):
    """`2026-02-30` is the one that matters: it is shaped exactly like an ISO date
    and is not one, so a lenient regex would accept it and the estimator would
    then reject it one layer down, after the supplier call."""
    monkeypatch.setenv(policy.ENV_CLOSURES, raw)
    with pytest.raises(policy.PolicyInvalid):
        policy.closures()


def test_declared_closures_are_what_the_estimator_accepts(monkeypatch):
    """Fed through rather than inspected, because the shape contract is
    `_holiday_set`'s and not this module's opinion of it."""
    monkeypatch.setenv(policy.ENV_CLOSURES, "2026-01-19,2026-01-20")
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    transit = {"min_days": 2, "max_days": 2, "basis": estimate.BASIS_CALENDAR}
    got = estimate.arrival_window(
        transit=transit, handling=policy.handling(), buffer_days=policy.buffer_days(),
        now=at(9, day=16), holidays=policy.closures())
    assert got["state"] == estimate.STATE_ESTIMATED
    assert got["components"]["holidays_modelled"] == 2


def test_a_declared_closure_moves_the_window_later(monkeypatch):
    """The direction. A closure calendar that changed nothing would be a setting
    an operator believed they had used."""
    monkeypatch.setenv(policy.ENV_HANDLING, "1")
    monkeypatch.setenv(policy.ENV_BUFFER, "0")
    transit = {"min_days": 2, "max_days": 2, "basis": estimate.BASIS_CALENDAR}
    args = dict(transit=transit, handling=policy.handling(),
                buffer_days=policy.buffer_days(), now=at(9, day=15))
    open_house = estimate.arrival_window(**args)
    monkeypatch.setenv(policy.ENV_CLOSURES, "2026-01-16")
    closed = estimate.arrival_window(**dict(args, holidays=policy.closures()))
    assert closed["earliest"] > open_house["earliest"]


# ---------------------------------------------------------------------------
# Route selection: borrowed from routing, never respelled
# ---------------------------------------------------------------------------

def test_the_default_route_policy_is_routings_own():
    """Borrowed rather than repeated. Two spellings of "what does this platform
    pick" is two answers that drift, and the drift is invisible: both are valid
    policy names."""
    assert policy.route_policy() == routing.POLICY_CHEAPEST_ACCEPTABLE


@pytest.mark.parametrize("name", routing.POLICIES)
def test_every_policy_routing_offers_can_be_declared(monkeypatch, name):
    """Parametrized over `routing.POLICIES` so a policy added there and not
    accepted here is a failure rather than a silently unreachable option."""
    monkeypatch.setenv(policy.ENV_ROUTE_POLICY, name.lower())
    assert policy.route_policy() == name


@pytest.mark.parametrize("raw", ["CHEAPEST", "fastest ", "SLOWEST", "cheapest-acceptable"])
def test_an_unrecognised_route_policy_is_an_error_not_the_default(monkeypatch, raw):
    """`"fastest "` is the one that must pass and does; the rest must raise. A
    near-miss silently falling back to the default gives a deployment the
    cheapest route while its dashboard says FASTEST."""
    monkeypatch.setenv(policy.ENV_ROUTE_POLICY, raw)
    if raw.strip().upper() in routing.POLICIES:
        assert policy.route_policy() == raw.strip().upper()
    else:
        with pytest.raises(policy.PolicyInvalid):
            policy.route_policy()


def test_the_accepted_route_policies_are_borrowed_and_not_retyped():
    """A source-level check, because this is a defect with no behavioural
    signature. A tuple of the same two strings written out here passes every
    behavioural test in this file and every one in `routing` -- and then stops
    agreeing the moment `routing` adds a third policy, at which point a legitimate
    declaration is refused as unknown. There is nothing to observe at runtime, so
    the only place to catch it is the text.
    """
    source = open(policy.__file__).read()
    assert "routing.POLICIES" in source
    for name in routing.POLICIES:
        assert f'"{name}"' not in source, (
            f"{name} is spelled out here as well as in routing; borrow the tuple")


def test_no_declared_ceiling_means_no_ceiling():
    """Neutral absence, unlike handling and buffer: no ceiling means every route
    the supplier offers is eligible, which is the supplier's own answer and not a
    substituted one."""
    assert policy.ceiling_days() is None


@pytest.mark.parametrize("raw,days", [("1", 1), ("30", 30), (" 45 ", 45)])
def test_a_declared_ceiling_is_read_as_days(monkeypatch, raw, days):
    monkeypatch.setenv(policy.ENV_CEILING, raw)
    assert policy.ceiling_days() == days


@pytest.mark.parametrize("raw", ["0", "-5", "soon", "1.5"])
def test_an_unreadable_ceiling_is_an_error(monkeypatch, raw):
    """Zero is refused, not treated as "nothing is acceptable": a ceiling of zero
    excludes every route, so a deployment that typed it would silently stop
    quoting delivery entirely."""
    monkeypatch.setenv(policy.ENV_CEILING, raw)
    with pytest.raises(policy.PolicyInvalid):
        policy.ceiling_days()


def test_a_declared_ceiling_is_what_routing_accepts(monkeypatch):
    """Proved by selecting with it rather than by comparing to an int, so the
    validation rule stays `routing`'s."""
    monkeypatch.setenv(policy.ENV_CEILING, "20")
    options = [{"option_id": "slow", "available": True,
                "transit": {"min_days": 25, "max_days": 40,
                            "basis": estimate.BASIS_CALENDAR},
                "provider_total": "1.00"},
               {"option_id": "ok", "available": True,
                "transit": {"min_days": 8, "max_days": 15,
                            "basis": estimate.BASIS_CALENDAR},
                "provider_total": "9.00"}]
    chosen = routing.select_route(options, policy=policy.route_policy(),
                                  ceiling_days=policy.ceiling_days())
    assert chosen["ceiling_days"] == 20
    # The cheap one is excluded for being slow, so the declared ceiling is proved
    # by what it removed and not only by what came back.
    assert chosen["selected"]["option_id"] == "ok"
    assert {"option_id": "slow", "reason": routing.EXCLUDED_TOO_SLOW} in chosen["excluded"]


def test_the_unspecified_basis_is_not_configurable():
    """A constant, and the test says why: it is `estimate`'s rounding-direction
    decision, and a switch here would exist only so a deployment could pick the
    earlier reading of a supplier's silence."""
    assert policy.unspecified_basis() == estimate.BASIS_BUSINESS
    assert "unspecified_basis" not in "".join(ENV_KEYS).lower()


# ---------------------------------------------------------------------------
# The origin timezone: every fall-through rounds the date later
# ---------------------------------------------------------------------------

def test_the_moment_is_timezone_aware_in_the_origins_zone():
    """`arrival_window` raises on a naive `now` and documents that the zone is the
    origin's, because it is the warehouse that has to pick the item."""
    moment = policy.now_at("CN")
    assert moment.tzinfo is not None
    assert moment.utcoffset() == timedelta(hours=8)


def test_the_instant_is_preserved_and_only_the_zone_changes():
    """Same moment, different clock face. A conversion that changed the instant
    would move every window by the offset."""
    instant = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
    assert policy.now_at("CN", clock=lambda: instant) == instant


def test_a_utc_evening_is_already_tomorrow_at_the_warehouse():
    """The defect this exists to prevent, stated as a date. 18:00 UTC on the 1st
    is 02:00 on the 2nd in Shenzhen, so quoting in UTC starts handling a day early
    and closes the window a day early."""
    instant = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
    assert instant.date() == dt.date(2026, 1, 1)
    assert policy.now_at("CN", clock=lambda: instant).date() == dt.date(2026, 1, 2)


def test_a_naive_clock_is_refused():
    with pytest.raises(policy.PolicyInvalid):
        policy.now_at("CN", clock=lambda: datetime(2026, 1, 1, 18, 0))


@pytest.mark.parametrize("origin", ["cn", " CN ", "Cn"])
def test_an_origin_is_matched_case_and_space_insensitively(origin):
    """`origin` arrives from `origin._country`, which upper-cases -- but this
    module is also called by an endpoint, and a second normalization rule would
    mean a lower-case country silently got the fallback zone."""
    assert policy.zone_for(origin) is policy.zone_for("CN")


@pytest.mark.parametrize("origin", [None, "", "  ", "ZZ", "USA", 42, True, ["CN"],
                                   {"country": "CN"}])
def test_an_unmapped_origin_never_lands_on_utc(origin):
    """UTC is behind every zone in the map, so it is the early direction. An
    unrecognised warehouse must be quoted later than the truth, never earlier."""
    offset = policy.now_at(origin).utcoffset()
    assert offset > timedelta(0)


@pytest.mark.parametrize("origin", [None, "ZZ", 42])
def test_an_unmapped_origins_date_is_never_before_any_declared_origins(origin):
    """The property, not the implementation. Whatever the fallback is, its local
    date must be no earlier than every declared zone's -- that is what makes an
    unknown warehouse safe rather than merely non-UTC."""
    instant = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    fallback = policy.now_at(origin, clock=lambda: instant).date()
    for code in policy.ORIGIN_ZONES:
        assert fallback >= policy.now_at(code, clock=lambda: instant).date()


def test_the_fallback_is_derived_from_the_map_and_not_written_down(monkeypatch):
    """Added a warehouse further east and the fallback follows it, so the safety
    property survives a new entry rather than needing a second edit."""
    monkeypatch.setitem(policy.ORIGIN_ZONES, "NZ", "Pacific/Auckland")
    instant = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    assert (policy.now_at("ZZ", clock=lambda: instant).utcoffset()
            == policy.now_at("NZ", clock=lambda: instant).utcoffset())


def test_every_declared_zone_actually_loads():
    """A typo in an IANA name is not a crash -- `zone_for` falls through to the
    fallback -- so it would silently quote a German warehouse on Shanghai time.
    Skipped rather than failed where no zone loads at all, because that is the
    no-tzdata container and it has its own test."""
    if policy._zone("Asia/Shanghai") is None:
        pytest.skip("no zone database on this machine")
    for code, name in policy.ORIGIN_ZONES.items():
        assert policy._zone(name) is not None, f"{code} names a zone that will not load"


def test_a_container_with_no_zone_database_still_rounds_later(monkeypatch):
    """The no-tzdata case, which is live: two modules in this repo already fall
    back to UTC when a zone will not load, and UTC is the early direction here.
    The fixed offset is exact rather than approximate for China, which observes no
    daylight saving and holds most of this supplier's stock."""
    monkeypatch.setattr(policy, "_zone", lambda name: None)
    moment = policy.now_at("CN")
    assert moment.utcoffset() == timedelta(hours=policy._FALLBACK_OFFSET_HOURS)
    assert moment.utcoffset() >= timedelta(0)


def test_the_fixed_fallback_offset_is_at_least_every_declared_zones(monkeypatch):
    """Pins the constant against the map rather than against 8. Declare a zone
    further east and this fails, which is the reminder that the no-tzdata fallback
    stopped being the latest one."""
    if policy._zone("Asia/Shanghai") is None:
        pytest.skip("no zone database on this machine")
    instant = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    fixed = timedelta(hours=policy._FALLBACK_OFFSET_HOURS)
    for name in policy.ORIGIN_ZONES.values():
        offset = instant.astimezone(policy._zone(name)).utcoffset()
        assert fixed >= offset, f"{name} is further east than the fixed fallback"


def test_daylight_saving_is_the_libraries_problem_not_a_table(monkeypatch):
    """Berlin is +1 in January and +2 in July. A map of numeric offsets would be
    wrong for half the year in a way no test of a single date would catch."""
    if policy._zone("Europe/Berlin") is None:
        pytest.skip("no zone database on this machine")
    winter = policy.now_at("DE", clock=lambda: datetime(2026, 1, 15, 12, 0,
                                                        tzinfo=timezone.utc))
    summer = policy.now_at("DE", clock=lambda: datetime(2026, 7, 15, 12, 0,
                                                        tzinfo=timezone.utc))
    assert summer.utcoffset() > winter.utcoffset()


# ---------------------------------------------------------------------------
# The health surface
# ---------------------------------------------------------------------------

def test_a_misconfigured_deployment_is_reported_and_not_raised(monkeypatch):
    """A health surface that 500s when the thing it monitors is broken is useless
    at the one moment it was needed. The readers still raise; this one reports."""
    monkeypatch.setenv(policy.ENV_HANDLING, "two")
    monkeypatch.setenv(policy.ENV_BUFFER, "1")
    report = policy.declared()
    assert report["can_estimate"] is False
    assert any(policy.ENV_HANDLING in message for message in report["errors"])
    assert report["handling"] is None


def test_the_error_names_the_variable_to_fix(monkeypatch):
    """An operator reads this instead of the source. A message that said only
    "invalid range" would send them to the wrong one of six variables."""
    monkeypatch.setenv(policy.ENV_CUTOFF, "noon")
    assert any(policy.ENV_CUTOFF in message
               for message in policy.declared()["errors"])


def test_a_typo_is_distinguishable_from_an_unset_variable(monkeypatch):
    """The two must never look the same. Unset is "we have not decided", typed
    wrong is "we decided and it is not being applied", and an operator in the
    second case is looking at a variable that appears correct."""
    quiet = policy.declared()
    monkeypatch.setenv(policy.ENV_BUFFER, "tow")
    noisy = policy.declared()
    assert quiet["can_estimate"] is noisy["can_estimate"] is False
    assert quiet["errors"] == []
    assert noisy["errors"]


def test_the_report_distinguishes_unconfigured_from_unable(monkeypatch):
    """`can_estimate` is the derived field, and it is derived from exactly the
    pair `arrival_window` refuses on. A report that only listed six variables
    would leave the operator to work out which two matter."""
    monkeypatch.setenv(policy.ENV_HANDLING, "2")
    assert policy.declared()["can_estimate"] is False
    monkeypatch.setenv(policy.ENV_BUFFER, "1")
    assert policy.declared()["can_estimate"] is True


def test_the_report_carries_no_secret_and_no_buyer_data(monkeypatch):
    """It is a health surface, so it will be rendered somewhere an operator can
    see and probably logged. Nothing here is a credential, and nothing here is
    about a person -- this module never sees a buyer."""
    monkeypatch.setenv(policy.ENV_HANDLING, "2")
    monkeypatch.setenv(policy.ENV_BUFFER, "1")
    body = repr(policy.declared())
    for leak in ("token", "key", "secret", "password", "buyer", "user"):
        assert leak not in body.lower()


def test_the_report_says_whether_the_zone_database_loaded(monkeypatch):
    """Separate from every other field, because a missing zone database is a
    deployment defect whose only symptom is estimates that are quietly a day
    optimistic -- there is no error and no empty value to notice."""
    assert isinstance(policy.declared()["timezones_loaded"], bool)
    monkeypatch.setattr(policy, "_zone", lambda name: None)
    assert policy.declared()["timezones_loaded"] is False


# ---------------------------------------------------------------------------
# The contract with the caller and with the environment gate
# ---------------------------------------------------------------------------

def test_every_variable_this_module_reads_is_named_by_a_constant():
    """The env-contract gate scans for name-holding constants. A literal inside a
    function body is a variable `.env.example` will never document, which on this
    platform is a silent feature-off switch."""
    source = (policy.__file__ and open(policy.__file__).read()) or ""
    body = source.split('"""', 2)[-1]
    for name in re.findall(r"PULSE_DELIVERY_[A-Z_]+", body):
        assert name in ENV_KEYS, f"{name} is read without a constant"


def test_every_declared_variable_is_documented_in_the_env_example():
    """Asserted here as well as in the protection gate, so a name added to this
    module fails its own suite first -- next to the test that explains why."""
    import pathlib
    root = pathlib.Path(policy.__file__).resolve().parents[2]
    contract = (root / ".env.example").read_text()
    for name in ENV_KEYS:
        assert f"\n{name}=" in contract, f"{name} is undocumented"


def test_nothing_here_returns_a_supplier_fact():
    """The boundary. Transit time is the supplier's to state and this module must
    never have an opinion about it, or the two would disagree and the declared one
    would win because it is the one that always answers."""
    names = [name for name in dir(policy) if not name.startswith("_")]
    assert "transit" not in " ".join(names).lower()
    assert "freight" not in " ".join(names).lower()
