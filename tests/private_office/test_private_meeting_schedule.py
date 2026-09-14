"""Private Meetings — the scheduling layer: time, zones, and replay.

Run either way::

    python -m pytest tests/private_office/test_private_meeting_schedule.py
    python tests/private_office/test_private_meeting_schedule.py

What these tests defend
-----------------------
* **One canonical instant.** What is stored is always UTC. A meeting booked in
  Tokyo and one booked in Los Angeles sort against each other correctly,
  because a TEXT column holding mixed offsets does not: ``22:00-07:00`` sorts
  *before* ``23:00+02:00`` while starting eight hours later.
* **The zone is not decoration.** It is stored alongside the instant because
  the instant alone cannot answer "what time did the host mean?" — needed to
  render the invitation, and needed by any future reschedule.
* **DST is resolved, not stumbled into.** A wall-clock time that does not
  exist (spring forward) moves forward past the gap rather than silently
  landing an hour early; one that happens twice (fall back) is pinned to the
  first occurrence rather than left to a default.
* **The far future works.** 2030, 2035, 2045 and a leap-day in 2048 are
  ordinary inputs, not edge cases — the product requirement is "any future
  date". Only genuine overflow is refused.
* **The server is the authority.** Past instants, zero and negative
  durations, unknown zones, and malformed input are refused with a code, by
  the model, regardless of what the client believes.
* **A double tap is one meeting.** An idempotency key makes a retried create
  return the original, and the key is scoped to its owner so it cannot be
  used to reach into someone else's account.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import pulsesoc_communications_engine as eng  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

HOST = 101
OTHER = 202


@pytest.fixture()
def cur(monkeypatch):
    monkeypatch.setenv("PRIVATE_MEETINGS_ENABLED", "1")
    monkeypatch.setattr(eng, "agora_config_status", lambda: {"configured": True})
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    meetings.reset_meetings_schema_cache()
    meetings.ensure_meetings_schema(cursor, force=True)
    cursor.execute(po_schema.AUDIT_TABLE_DDL)
    cursor.execute(
        "CREATE TABLE blocked_users (blocker_user_id INT, blocked_user_id INT)")
    cursor.execute(
        "CREATE TABLE comm_v2_blocks (id INTEGER PRIMARY KEY, "
        "blocker_user_id INT, blocked_user_id INT, status TEXT)")
    yield cursor
    conn.close()
    meetings.reset_meetings_schema_cache()


def _reject(fn, *, code=None, status=None):
    with pytest.raises(meetings.PrivateMeetingRejected) as excinfo:
        fn()
    if code is not None:
        assert excinfo.value.code == code, excinfo.value.code
    if status is not None:
        assert excinfo.value.status == status, excinfo.value.status
    return excinfo.value


# ---------------------------------------------------------------------------
# Canonical UTC
# ---------------------------------------------------------------------------


def test_every_offset_is_stored_as_the_same_utc_instant():
    """Four spellings of one moment must produce one stored string."""
    spellings = [
        "2032-05-18T20:00:00+00:00",
        "2032-05-18T20:00:00Z",
        "2032-05-18T22:00:00+02:00",
        "2032-05-18T13:00:00-07:00",
    ]
    stored = {meetings.resolve_schedule(text)[0] for text in spellings}
    assert stored == {"2032-05-18T20:00:00+00:00"}, stored


def test_utc_normalisation_fixes_the_text_sort():
    """The bug this normalisation exists to prevent, stated as an assertion.

    Two meetings, the later one written with a western offset. Compared as raw
    text the order inverts; compared after normalisation it is correct. A list
    ordered by a TEXT column of mixed offsets shows tomorrow before today.
    """
    earlier_raw = "2032-05-18T23:00:00+02:00"   # 21:00 UTC
    later_raw = "2032-05-18T22:00:00-07:00"     # 05:00 UTC next day
    assert later_raw < earlier_raw              # the naive text order is wrong

    earlier = meetings.resolve_schedule(earlier_raw)[0]
    later = meetings.resolve_schedule(later_raw)[0]
    assert earlier < later                      # canonical order is right
    assert later == "2032-05-19T05:00:00+00:00"


def test_stored_row_is_utc_even_when_booked_with_an_offset(cur):
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Board",
        scheduled_start_at="2033-02-11T09:00:00-05:00",
        timezone_name="America/New_York", duration_minutes=60)
    cur.execute(
        f"SELECT scheduled_start_at, scheduled_timezone, schedule_version "
        f"FROM {meetings.MEETINGS_TABLE} WHERE public_id=?",
        (meeting["public_id"],))
    row = cur.fetchone()
    assert row["scheduled_start_at"] == "2033-02-11T14:00:00+00:00"
    assert row["scheduled_timezone"] == "America/New_York"
    assert int(row["schedule_version"]) == 1
    # And the projection hands the client both halves, not just the instant.
    assert meeting["scheduled_start_at"] == "2033-02-11T14:00:00+00:00"
    assert meeting["scheduled_timezone"] == "America/New_York"


# ---------------------------------------------------------------------------
# Wall clock + zone
# ---------------------------------------------------------------------------


def test_wall_clock_plus_zone_is_resolved_server_side():
    """"09:00 in Tokyo" is the form a calendar UI actually produces."""
    utc_iso, zone = meetings.resolve_schedule(
        "2032-06-01T09:00:00", "Asia/Tokyo")
    assert utc_iso == "2032-06-01T00:00:00+00:00"
    assert zone == "Asia/Tokyo"


def test_naive_without_a_zone_keeps_meaning_utc():
    """Existing callers that send a bare timestamp do not change meaning."""
    utc_iso, zone = meetings.resolve_schedule("2032-06-01T09:00:00")
    assert utc_iso == "2032-06-01T09:00:00+00:00"
    assert zone == ""


def test_an_offset_and_a_zone_do_not_fight():
    """When both are given the offset wins for the instant; the zone is kept.

    They describe different things: the offset fixes *which moment*, the zone
    records *how the host was thinking about it*. A host in Berlin booking a
    call at 08:00 New York time gets the New York instant and a stored zone
    that renders the invitation the way they set it up.
    """
    utc_iso, zone = meetings.resolve_schedule(
        "2032-06-01T08:00:00-04:00", "America/New_York")
    assert utc_iso == "2032-06-01T12:00:00+00:00"
    assert zone == "America/New_York"


def test_unknown_timezone_is_refused():
    for junk in ["Mars/Olympus", "not a zone", "UTC+5", "../../etc/passwd"]:
        _reject(lambda j=junk: meetings.resolve_schedule("2032-06-01T09:00:00", j),
                code="invalid_timezone", status=400)


def test_empty_timezone_is_allowed():
    assert meetings.normalize_timezone("") == ""
    assert meetings.normalize_timezone(None) == ""


# ---------------------------------------------------------------------------
# DST
# ---------------------------------------------------------------------------


def test_spring_forward_gap_moves_forward_not_backward():
    """2:30 AM does not exist on 2032-03-14 in New York.

    ZoneInfo does not raise for a nonexistent time — it applies the
    pre-transition offset, which resolves 02:30 to 06:30 UTC, i.e. 01:30 local:
    an hour *before* what the user asked for. A meeting that drifts earlier is
    a meeting the host misses. This asserts we land at 03:30 local instead.
    """
    zone = ZoneInfo("America/New_York")
    naive = datetime(2032, 3, 14, 2, 30)
    # What ZoneInfo does unaided, recorded so the test fails loudly if CPython
    # ever changes the behaviour this guard exists to correct.
    unaided = naive.replace(tzinfo=zone).astimezone(timezone.utc)
    assert unaided == datetime(2032, 3, 14, 7, 30, tzinfo=timezone.utc)

    utc_iso, _ = meetings.resolve_schedule("2032-03-14T02:30:00", "America/New_York")
    assert utc_iso == "2032-03-14T07:30:00+00:00"
    local = datetime.fromisoformat(utc_iso).astimezone(zone)
    assert (local.hour, local.minute) == (3, 30)


def test_times_either_side_of_the_gap_are_untouched():
    for wall, expect in [("2032-03-14T01:30:00", "2032-03-14T06:30:00+00:00"),
                         ("2032-03-14T03:30:00", "2032-03-14T07:30:00+00:00")]:
        assert meetings.resolve_schedule(wall, "America/New_York")[0] == expect


def test_fall_back_ambiguity_picks_the_first_occurrence():
    """01:30 happens twice on 2032-11-07 in New York; we pin the earlier one.

    EDT (-04:00) is the first pass, EST (-05:00) the second. Without an
    explicit choice the answer is whatever `fold` happens to default to, which
    is a coin flip the host never made.
    """
    utc_iso, _ = meetings.resolve_schedule("2032-11-07T01:30:00", "America/New_York")
    assert utc_iso == "2032-11-07T05:30:00+00:00"   # EDT, the first 01:30


def test_southern_hemisphere_dst_runs_the_other_way():
    """Sydney's transitions are inverted relative to the north.

    A rule written as "spring forward is in March" would pass every US test
    and be wrong here. 2032-10-03 02:30 does not exist in Sydney.
    """
    utc_iso, _ = meetings.resolve_schedule("2032-10-03T02:30:00", "Australia/Sydney")
    local = datetime.fromisoformat(utc_iso).astimezone(ZoneInfo("Australia/Sydney"))
    assert (local.hour, local.minute) == (3, 30)


def test_a_zone_with_no_dst_at_all_is_unaffected():
    utc_iso, _ = meetings.resolve_schedule("2032-03-14T02:30:00", "Asia/Kolkata")
    assert utc_iso == "2032-03-13T21:00:00+00:00"   # IST is +05:30, year-round


# ---------------------------------------------------------------------------
# The far future, leap years, boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("year", [2030, 2035, 2045, 2099, 2500])
def test_any_future_year_is_bookable(cur, year):
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, title=f"Year {year}",
        scheduled_start_at=f"{year}-07-04T12:00:00+00:00", duration_minutes=60)
    assert meeting["scheduled_start_at"] == f"{year}-07-04T12:00:00+00:00"
    assert meeting["status"] == meetings.ST_SCHEDULED


def test_leap_day_is_a_real_date(cur):
    """29 February 2048 exists; 29 February 2047 does not."""
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="2048-02-29T10:00:00+00:00",
        duration_minutes=30)
    assert meeting["scheduled_start_at"] == "2048-02-29T10:00:00+00:00"
    _reject(lambda: meetings.resolve_schedule("2047-02-29T10:00:00+00:00"),
            code="invalid_schedule")


def test_century_leap_rule(cur):
    """2100 is not a leap year — divisible by 100, not by 400."""
    _reject(lambda: meetings.resolve_schedule("2100-02-29T10:00:00+00:00"),
            code="invalid_schedule")
    assert meetings.resolve_schedule("2400-02-29T10:00:00+00:00")[0] \
        == "2400-02-29T10:00:00+00:00"


def test_month_lengths_are_enforced_by_the_parser():
    for bad in ["2032-04-31T10:00:00+00:00", "2032-06-31T10:00:00+00:00",
                "2032-09-31T10:00:00+00:00", "2032-11-31T10:00:00+00:00",
                "2032-13-01T10:00:00+00:00", "2032-00-10T10:00:00+00:00"]:
        _reject(lambda b=bad: meetings.resolve_schedule(b), code="invalid_schedule")


def test_year_boundary_crossing_is_exact():
    """23:30 on 31 December in Tokyo is still the 31st there, already the 1st
    in UTC. Storing the local date would put this meeting in the wrong year."""
    utc_iso, _ = meetings.resolve_schedule("2032-12-31T23:30:00", "Asia/Tokyo")
    assert utc_iso == "2032-12-31T14:30:00+00:00"
    # And the other direction: New York's New Year is the 1st in UTC.
    utc_iso, _ = meetings.resolve_schedule("2032-12-31T23:30:00", "America/New_York")
    assert utc_iso == "2033-01-01T04:30:00+00:00"


def test_absurdly_far_future_is_refused_as_out_of_range():
    """The ceiling is a technical guard, and it says so with its own code.

    `schedule_out_of_range` rather than `invalid_schedule`: the client can
    tell "I sent garbage" from "I sent a real date you will not accept".
    """
    _reject(lambda: meetings.resolve_schedule("5000-01-01T10:00:00+00:00"),
            code="schedule_out_of_range", status=400)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_past_instants_are_refused(cur):
    past = (meetings._now_dt() - timedelta(days=1)).isoformat(timespec="seconds")
    _reject(lambda: meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at=past, duration_minutes=30),
        code="schedule_in_past", status=400)


def test_a_moment_ago_is_tolerated():
    """Clock skew and round-trip latency are not user errors.

    A client that renders "the next slot" and posts it a few seconds later
    must not be told its own suggestion is in the past.
    """
    recent = (meetings._now_dt() - timedelta(seconds=30)).isoformat(timespec="seconds")
    assert meetings.resolve_schedule(recent)[0]
    stale = (meetings._now_dt() - timedelta(seconds=600)).isoformat(timespec="seconds")
    _reject(lambda: meetings.resolve_schedule(stale), code="schedule_in_past")


def test_malformed_start_times_are_refused():
    for junk in ["", "not-a-time", "tomorrow", "2032-06-01", "06/01/2032",
                 "2032-06-01T25:00:00+00:00", "2032-06-01T10:61:00+00:00",
                 None, 12345]:
        _reject(lambda j=junk: meetings.resolve_schedule(j), code="invalid_schedule")


def test_durations_that_are_not_durations_are_refused(cur):
    future = (meetings._now_dt() + timedelta(days=5)).isoformat(timespec="seconds")

    def book(duration):
        return meetings.create_meeting(
            cur, owner_user_id=HOST, scheduled_start_at=future,
            duration_minutes=duration)

    for bad in [0, -1, -60, "", None, "soon", 2, 45.5, True]:
        _reject(lambda b=bad: book(b), code="invalid_duration", status=400)
    # A sane one works, and an over-long one is clamped rather than refused —
    # the ceiling is a storage guard, not an opinion about the meeting.
    assert book(45)["duration_minutes"] == 45
    assert book(99999)["duration_minutes"] == meetings.MAX_DURATION_MINUTES


def test_instant_meetings_skip_schedule_validation_entirely(cur):
    """An instant meeting starts now: it has no schedule to be wrong about."""
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Now", instant=True,
        scheduled_start_at="not-a-time", duration_minutes=0)
    assert meeting["status"] in (meetings.ST_WAITING, meetings.ST_LIVE,
                                 meetings.ST_STARTING)
    assert meeting["scheduled_start_at"] == ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _book(cur, owner=HOST, key="", days=10, title="Sync"):
    start = (meetings._now_dt() + timedelta(days=days)).replace(
        microsecond=0).isoformat(timespec="seconds")
    return meetings.create_meeting(
        cur, owner_user_id=owner, title=title, scheduled_start_at=start,
        duration_minutes=30, idempotency_key=key)


def test_a_repeated_key_returns_the_first_meeting(cur):
    first = _book(cur, key="tap-abc")
    second = _book(cur, key="tap-abc", title="Different title")
    assert second["public_id"] == first["public_id"]
    assert second["title"] == first["title"] == "Sync"
    cur.execute(f"SELECT COUNT(*) AS n FROM {meetings.MEETINGS_TABLE}")
    assert int(cur.fetchone()["n"]) == 1


def test_no_key_means_no_deduplication(cur):
    """Absence of a key is not a key. Two deliberate meetings stay two.

    The empty string is the column default on every legacy row, so treating it
    as a value would collapse a user's entire meeting history into one row.
    """
    first = _book(cur)
    second = _book(cur)
    assert first["public_id"] != second["public_id"]
    cur.execute(f"SELECT COUNT(*) AS n FROM {meetings.MEETINGS_TABLE}")
    assert int(cur.fetchone()["n"]) == 2


def test_keys_are_scoped_to_their_owner(cur):
    """Two users may pick the same key without seeing each other's meeting."""
    mine = _book(cur, owner=HOST, key="shared-key", title="Mine")
    theirs = _book(cur, owner=OTHER, key="shared-key", title="Theirs")
    assert mine["public_id"] != theirs["public_id"]
    assert theirs["title"] == "Theirs"


def test_different_keys_make_different_meetings(cur):
    assert _book(cur, key="a")["public_id"] != _book(cur, key="b")["public_id"]


def test_an_oversized_key_is_refused(cur):
    _reject(lambda: _book(cur, key="x" * 5000),
            code="invalid_idempotency_key", status=400)


def test_replay_does_not_add_a_second_host_participant(cur):
    """The replay must return early, not re-run the side effects.

    If it fell through to `_insert_participant` the host would appear twice in
    their own meeting — the failure mode that makes "just retry" unsafe.
    """
    first = _book(cur, key="tap-xyz")
    _book(cur, key="tap-xyz")
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {meetings.PARTICIPANTS_TABLE} "
        f"WHERE meeting_id=(SELECT id FROM {meetings.MEETINGS_TABLE} "
        f"WHERE public_id=?)", (first["public_id"],))
    assert int(cur.fetchone()["n"]) == 1


# ---------------------------------------------------------------------------
# Reschedule
# ---------------------------------------------------------------------------


def test_moving_the_time_bumps_the_schedule_version(cur):
    meeting = _book(cur, days=10)
    assert meeting["schedule_version"] == 1
    moved = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        scheduled_start_at="2035-04-02T14:00:00+00:00")
    assert moved["schedule_version"] == 2
    assert moved["scheduled_start_at"] == "2035-04-02T14:00:00+00:00"


def test_editing_only_the_title_does_not_bump_the_version(cur):
    """The version is a reminder-invalidation token, not an edit counter.

    If a typo fix bumped it, every pending reminder would be cancelled and
    every attendee re-mailed about a meeting that did not move.
    """
    meeting = _book(cur, days=10, title="Draft name")
    edited = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        title="Real name", agenda="Two items")
    assert edited["title"] == "Real name"
    assert edited["agenda"] == "Two items"
    assert edited["schedule_version"] == 1
    assert edited["scheduled_start_at"] == meeting["scheduled_start_at"]


def test_changing_the_duration_counts_as_a_time_change(cur):
    """The end is as much "when" as the start is.

    An attendee who set aside an hour has to be told it became three.
    """
    meeting = _book(cur, days=10)
    longer = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        duration_minutes=180)
    assert longer["duration_minutes"] == 180
    assert longer["schedule_version"] == 2


def test_rewriting_the_same_time_is_not_a_move(cur):
    """Idempotence at the edit layer: a no-op save must not re-mail anyone."""
    meeting = _book(cur, days=10)
    same = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        scheduled_start_at=meeting["scheduled_start_at"])
    assert same["schedule_version"] == 1


def test_changing_only_the_zone_re_resolves_the_instant(cur):
    """The same wall clock in a new zone is a different moment.

    A host who booked 09:00 London and switches the meeting to New York has
    moved it by five hours, even though they touched no digit of the time.
    """
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="2035-06-01T09:00:00",
        timezone_name="Europe/London", duration_minutes=60)
    assert meeting["scheduled_start_at"] == "2035-06-01T08:00:00+00:00"  # BST
    moved = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        scheduled_start_at="2035-06-01T09:00:00",
        timezone_name="America/New_York")
    assert moved["scheduled_start_at"] == "2035-06-01T13:00:00+00:00"  # EDT
    assert moved["schedule_version"] == 2


def test_omitted_fields_are_left_alone_and_empty_ones_clear(cur):
    """`None` is "don't touch"; `""` is "erase". They cannot be the same.

    A partial edit sends only what changed. If absent meant blank, fixing the
    title would wipe the agenda every time.
    """
    meeting = _book(cur, days=10, title="Quarterly")
    meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        agenda="Budget, headcount")
    untouched = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        title="Quarterly review")
    assert untouched["agenda"] == "Budget, headcount"
    assert untouched["title"] == "Quarterly review"
    cleared = meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"], agenda="")
    assert cleared["agenda"] == ""
    assert cleared["title"] == "Quarterly review"


def test_reschedule_re_runs_every_validation(cur):
    """An edit is not a trusted path. The same refusals apply.

    A client that cannot book a meeting in the past must not be able to move
    one there instead.
    """
    meeting = _book(cur, days=10)
    ref = meeting["public_id"]
    past = (meetings._now_dt() - timedelta(days=2)).isoformat(timespec="seconds")
    for kwargs, code in [
        ({"scheduled_start_at": past}, "schedule_in_past"),
        ({"scheduled_start_at": "not-a-time"}, "invalid_schedule"),
        ({"scheduled_start_at": "9999-01-01T10:00:00+00:00"},
         "schedule_out_of_range"),
        ({"scheduled_start_at": "2035-06-01T09:00:00", "timezone_name": "Mars/Base"},
         "invalid_timezone"),
        ({"duration_minutes": 0}, "invalid_duration"),
        ({"duration_minutes": -30}, "invalid_duration"),
    ]:
        _reject(lambda k=kwargs: meetings.reschedule_meeting(
            cur, actor_user_id=HOST, meeting_ref=ref, **k), code=code)


def test_an_empty_edit_is_refused(cur):
    """A request that changes nothing is a client bug, not a success."""
    meeting = _book(cur, days=10)
    _reject(lambda: meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"]),
        code="no_changes", status=400)


def test_only_the_host_may_reschedule(cur):
    meeting = _book(cur, days=10)
    for stranger in (OTHER, 999):
        _reject(lambda s=stranger: meetings.reschedule_meeting(
            cur, actor_user_id=s, meeting_ref=meeting["public_id"],
            title="Hijacked"), code="forbidden", status=403)
    _reject(lambda: meetings.reschedule_meeting(
        cur, actor_user_id=0, meeting_ref=meeting["public_id"], title="x"),
        code="unauthorized", status=401)
    # And the refusal was real, not just a raised exception.
    cur.execute(f"SELECT title FROM {meetings.MEETINGS_TABLE} WHERE public_id=?",
                (meeting["public_id"],))
    assert cur.fetchone()["title"] == "Sync"


def test_a_meeting_that_already_happened_cannot_be_moved(cur):
    """Rescheduling a plan is coherent; rescheduling a fact is not.

    409, not 403: the host had every right, the meeting was in the wrong
    state. Collapsing the two would tell a host they lack permission over
    their own meeting.
    """
    meeting = _book(cur, days=10)
    meetings.cancel_meeting(cur, actor_user_id=HOST,
                            meeting_ref=meeting["public_id"])
    _reject(lambda: meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        scheduled_start_at="2035-04-02T14:00:00+00:00"),
        code="not_reschedulable", status=409)


def test_a_live_meeting_cannot_be_moved(cur):
    live = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Now", instant=True)
    _reject(lambda: meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=live["public_id"],
        scheduled_start_at="2035-04-02T14:00:00+00:00"),
        code="not_reschedulable", status=409)


def test_reschedule_is_audited_without_leaking_content(cur):
    """The log records that a meeting moved, never what it is called."""
    meeting = _book(cur, days=10, title="Acquisition of Northwind")
    meetings.reschedule_meeting(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        scheduled_start_at="2035-04-02T14:00:00+00:00")
    cur.execute(f"SELECT action, object_id FROM {po_schema.AUDIT_TABLE} "
                f"WHERE action=?", (meetings.audit.ACTION_MEETING_RESCHEDULE,))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert "Northwind" not in rows[0]["object_id"]
    assert rows[0]["object_id"].startswith("MEETING:")


# ---------------------------------------------------------------------------
# The calendar window — a grid asks for six weeks, never for a lifetime
# ---------------------------------------------------------------------------


def _at(cur, when, *, title="Meeting", owner=HOST, minutes=30):
    return meetings.create_meeting(
        cur, owner_user_id=owner, title=title,
        scheduled_start_at=when, duration_minutes=minutes)


def test_a_month_window_returns_only_that_month(cur):
    _at(cur, "2035-02-27T12:00:00+00:00", title="February")
    _at(cur, "2035-03-14T12:00:00+00:00", title="March")
    _at(cur, "2035-04-02T12:00:00+00:00", title="April")
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    assert [m["title"] for m in window["meetings"]] == ["March"]
    assert window["days"] == {"2035-03-14": 1}


def test_a_far_future_month_is_reachable_without_loading_the_past(cur):
    """The mutation: a calendar that filters the recent-first list client-side.

    Twelve meetings this year would push a lone 2045 booking out of any
    anchored-to-now window, and March 2045 would render empty forever while
    the row sat in the table.
    """
    base = datetime.now(timezone.utc) + timedelta(days=1)
    for day in range(12):
        _at(cur, (base + timedelta(days=day)).isoformat(timespec="seconds"),
              title=f"Soon {day}")
    _at(cur, "2045-03-19T09:00:00+00:00", title="The one that matters")
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2045-03-01T00:00:00+00:00",
        end="2045-04-01T00:00:00+00:00")
    assert [m["title"] for m in window["meetings"]] == ["The one that matters"]


def test_an_unbounded_window_is_refused(cur):
    """No half-open requests. Every default for the missing end is a guess,
    and the wrong guess is 'all of it'."""
    for start, end in [("", "2035-04-01T00:00:00+00:00"),
                       ("2035-03-01T00:00:00+00:00", ""),
                       ("", "")]:
        _reject(lambda s=start, e=end: meetings.calendar_range(
            cur, user_id=HOST, start=s, end=e), code="invalid_range", status=400)


def test_a_window_wider_than_the_cap_is_refused(cur):
    _reject(lambda: meetings.calendar_range(
        cur, user_id=HOST, start="2030-01-01T00:00:00+00:00",
        end="2045-01-01T00:00:00+00:00"), code="range_too_wide", status=400)


def test_a_backwards_window_is_refused(cur):
    _reject(lambda: meetings.calendar_range(
        cur, user_id=HOST, start="2035-04-01T00:00:00+00:00",
        end="2035-03-01T00:00:00+00:00"), code="invalid_range")


def test_a_year_wide_window_is_allowed(cur):
    """A year jump is a legitimate request; the cap is against histories."""
    _at(cur, "2035-06-06T06:00:00+00:00", title="Mid-year")
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2035-01-01T00:00:00+00:00",
        end="2035-12-31T00:00:00+00:00")
    assert len(window["meetings"]) == 1


def test_days_are_keyed_in_the_viewers_zone_not_utc(cur):
    """23:30 UTC on the 4th is the 5th in Tokyo. A grid that disagrees with
    the invitation is worse than no grid."""
    _at(cur, "2035-03-04T23:30:00+00:00")
    utc = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    tokyo = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00", timezone_name="Asia/Tokyo")
    assert utc["days"] == {"2035-03-04": 1}
    assert tokyo["days"] == {"2035-03-05": 1}


def test_the_hosts_chosen_zone_still_travels_with_each_entry(cur):
    meetings.create_meeting(
        cur, owner_user_id=HOST, scheduled_start_at="2035-03-04T09:00:00",
        timezone_name="Asia/Tokyo", duration_minutes=30)
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00", timezone_name="America/New_York")
    assert window["meetings"][0]["scheduled_timezone"] == "Asia/Tokyo"


def test_an_unknown_viewer_zone_is_refused_not_coerced(cur):
    _reject(lambda: meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00", timezone_name="Mars/Olympus"),
        code="invalid_timezone")


def test_two_meetings_on_one_day_count_once_each(cur):
    _at(cur, "2035-03-14T09:00:00+00:00", title="Morning")
    _at(cur, "2035-03-14T17:00:00+00:00", title="Evening")
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    assert window["days"] == {"2035-03-14": 2}


def test_the_window_is_half_open_at_the_end(cur):
    """Adjacent months must not both claim the boundary instant, or a meeting
    at midnight on the 1st appears twice as the user swipes."""
    _at(cur, "2035-04-01T00:00:00+00:00", title="Boundary")
    march = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    april = meetings.calendar_range(
        cur, user_id=HOST, start="2035-04-01T00:00:00+00:00",
        end="2035-05-01T00:00:00+00:00")
    assert march["meetings"] == []
    assert [m["title"] for m in april["meetings"]] == ["Boundary"]


def test_a_stranger_sees_none_of_it(cur):
    _at(cur, "2035-03-14T12:00:00+00:00", title="Private")
    window = meetings.calendar_range(
        cur, user_id=OTHER, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    assert window["meetings"] == []
    assert window["days"] == {}


def test_a_removed_participant_stops_seeing_the_day(cur):
    meeting = _at(cur, "2035-03-14T12:00:00+00:00", title="Board")
    meetings.invite_users(cur, meeting_ref=meeting["public_id"],
                          actor_user_id=HOST, user_ids=[OTHER])
    assert meetings.calendar_range(
        cur, user_id=OTHER, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")["days"] == {"2035-03-14": 1}
    meetings.remove_participant(cur, meeting_ref=meeting["public_id"],
                                actor_user_id=HOST, user_id=OTHER)
    assert meetings.calendar_range(
        cur, user_id=OTHER, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")["days"] == {}


def test_an_entry_carries_no_meeting_code(cur):
    """A grid cell draws a dot. The code is the thing that lets someone into
    the room, and a month of them in one payload is a month of keys."""
    _at(cur, "2035-03-14T12:00:00+00:00")
    entry = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")["meetings"][0]
    assert "meeting_code" not in entry
    assert "call_public_id" not in entry
    assert "participants" not in entry


def test_the_row_cap_is_reported_not_silently_applied(cur, monkeypatch):
    monkeypatch.setattr(meetings, "MAX_CALENDAR_ROWS", 2)
    for hour in (9, 11, 13):
        _at(cur, f"2035-03-14T{hour:02d}:00:00+00:00", title=f"At {hour}")
    window = meetings.calendar_range(
        cur, user_id=HOST, start="2035-03-01T00:00:00+00:00",
        end="2035-04-01T00:00:00+00:00")
    assert len(window["meetings"]) == 2
    assert window["truncated"] is True


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_ensure_schema_migrates_a_pre_existing_table(monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` is a no-op on a deployed database.

    Every column added after the first deploy therefore has to arrive by
    ALTER too. This builds the *old* shape by hand and checks that ensuring
    the schema over it produces a table a scheduled meeting can be written to
    — which is what will happen to production on the next boot.
    """
    monkeypatch.setenv("PRIVATE_MEETINGS_ENABLED", "1")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"CREATE TABLE {meetings.MEETINGS_TABLE} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, owner_user_id INTEGER, "
            "public_id TEXT, meeting_code TEXT, title TEXT, status TEXT, "
            "waiting_room_enabled INTEGER DEFAULT 1, locked INTEGER DEFAULT 0, "
            "call_public_id TEXT DEFAULT '', channel_name TEXT DEFAULT '', "
            "call_id INTEGER DEFAULT 0, started_at TEXT DEFAULT '', "
            "ended_at TEXT DEFAULT '', end_reason TEXT DEFAULT '', "
            "code_rotated_at TEXT DEFAULT '', "
            "created_at TEXT, updated_at TEXT)")
        cursor.execute(
            f"INSERT INTO {meetings.MEETINGS_TABLE} "
            f"(owner_user_id, public_id, meeting_code, title, status, "
            f" created_at, updated_at) "
            f"VALUES (?,?,?,?,?,?,?)",
            (HOST, "mtg_legacy", "111-222-333", "Legacy",
             meetings.ST_ENDED, "2026-01-01T00:00:00+00:00",
             "2026-01-01T00:00:00+00:00"))

        meetings.reset_meetings_schema_cache()
        meetings.ensure_meetings_schema(cursor, force=True)

        columns = {row["name"] for row in cursor.execute(
            f"PRAGMA table_info({meetings.MEETINGS_TABLE})")}
        for added in ("scheduled_start_at", "scheduled_timezone",
                      "schedule_version", "agenda", "idempotency_key",
                      "duration_minutes"):
            assert added in columns, added

        # The pre-existing row survived and reads back with the defaults.
        cursor.execute(
            f"SELECT * FROM {meetings.MEETINGS_TABLE} WHERE public_id='mtg_legacy'")
        legacy = cursor.fetchone()
        assert legacy["title"] == "Legacy"
        assert legacy["scheduled_start_at"] == ""
        assert int(legacy["schedule_version"]) == 1

        # Ensuring twice is not an error — the ALTERs must be conditional.
        meetings.ensure_meetings_schema(cursor, force=True)
    finally:
        conn.close()
        meetings.reset_meetings_schema_cache()


def test_added_columns_and_the_create_statement_agree():
    """A column in one and not the other reaches only half the fleet.

    In the CREATE only: fresh databases get it, upgraded ones never do. In
    ADDED_COLUMNS only: the reverse. Both halves are silent until a query
    touches the missing column on the wrong kind of database.
    """
    for table, column, _definition in meetings.ADDED_COLUMNS:
        ddl = {meetings.MEETINGS_TABLE: meetings.MEETINGS_TABLE_DDL}[table]
        assert f"{column} " in ddl, f"{table}.{column} missing from its CREATE"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:randomly"]))
