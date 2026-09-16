"""Private Meetings — inviting someone who is not a PulseSoc member.

Why this file exists
--------------------
A meeting was scheduled on a device and there was nowhere to type who it was
with. The wizard collected a title, a time and a duration, and the backend
could only invite ``user_ids`` — so a meeting with an accountant, a lawyer or a
client, which is most of what a Private Office meeting *is*, could not be
expressed at all. "Invitees" was not broken; it was absent on both sides.

The model those invitations now share
-------------------------------------
One identity, ``invitee_key``, either ``u:<user_id>`` or ``e:<address>``. It is
the whole reason this is one feature and not two:

* Invited twice, once by account and once by the address on that account, is
  one invitee — because the address resolves to the account before the key is
  built.
* Two different outside guests are two invitees. That sounds too obvious to
  test until you notice they both store ``invitee_user_id = 0``, which the old
  ``UNIQUE(meeting_id, invitee_user_id)`` would have read as the same person.
  ``test_two_outside_guests_can_both_be_invited`` is the test that fails if
  that constraint ever comes back.
* Nobody is identified by their name. Two people called "Chris Taylor" are two
  people, and a name-keyed identity would either merge them or attach one
  member's private notes to a stranger.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import pulsesoc_communications_engine as eng  # noqa: E402
from services.private_office import facts as facts_mod  # noqa: E402
from services.private_office import meetings  # noqa: E402
from services.private_office import relationships  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

HOST = 101
MEMBER = 202
STRANGER = 303

HOST_EMAIL = "host@example.com"
MEMBER_EMAIL = "member@example.com"


def _future(days=30, hour=10):
    moment = datetime.now(timezone.utc).replace(
        hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)
    return moment.isoformat()


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
    po_schema.ensure_private_schema(cursor, force=True)
    cursor.execute(
        "CREATE TABLE blocked_users (blocker_user_id INT, blocked_user_id INT)")
    cursor.execute(
        "CREATE TABLE comm_v2_blocks (id INTEGER PRIMARY KEY, "
        "blocker_user_id INT, blocked_user_id INT, status TEXT)")
    # Production's shape: keyed on user_id, and no `id`. See
    # test_private_meeting_persistence.py for why that sentence is in a comment.
    cursor.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, "
        "display_name TEXT, email TEXT)")
    for user_id, email in ((HOST, HOST_EMAIL), (MEMBER, MEMBER_EMAIL),
                           (STRANGER, "stranger@example.com")):
        cursor.execute("INSERT INTO users (user_id, email) VALUES (?, ?)",
                       (user_id, email))
    yield cursor
    conn.close()
    meetings.reset_meetings_schema_cache()


@pytest.fixture()
def outbox(monkeypatch):
    """Capture every enqueue instead of writing the real queue."""
    captured = []

    def fake_queue(user_id, to_email, subject, html_body, text_body="",
                   email_type="transactional", metadata=None, notification_id=0,
                   send_after=""):
        captured.append({
            "user_id": user_id, "to_email": to_email, "subject": subject,
            "metadata": dict(metadata or {}), "send_after": send_after,
        })
        return {"ok": True, "status": "queued", "queue_id": len(captured)}

    from services import notification_service

    monkeypatch.setattr(notification_service, "_queue_email_job", fake_queue)
    return captured


def _schedule(cur, **kwargs):
    return meetings.create_meeting(
        cur, owner_user_id=HOST, title="Estate review",
        scheduled_start_at=_future(), duration_minutes=60,
        timezone_name="UTC", **kwargs)


def _meeting_id(cur, meeting):
    """Resolve the row id the way every caller outside this module has to.

    ``_project_meeting`` deliberately does not hand out the autoincrement id —
    ``public_id`` is the only name a client is ever given, so a test that
    reached for ``meeting["id"]`` would be asserting against a field the API
    does not have. Looking it up here keeps the tests honest about that and
    doubles as a existence check: a meeting that was rolled back has no row to
    resolve, and the ``None`` unpacks into a failure rather than a zero.
    """
    cur.execute("SELECT id FROM private_meetings WHERE public_id=?",
                (str(meeting["public_id"]),))
    row = cur.fetchone()
    assert row is not None, f"no meeting row for {meeting['public_id']!r}"
    return int(row[0])


def _invites(cur, meeting):
    cur.execute(
        "SELECT invitee_user_id, invitee_key, invitee_email, invitee_name, status "
        "FROM private_meeting_invites WHERE meeting_id=? ORDER BY id",
        (_meeting_id(cur, meeting),))
    return [dict(row) for row in cur.fetchall()]


def _participants(cur, meeting):
    cur.execute(
        "SELECT user_id, state FROM private_meeting_participants "
        "WHERE meeting_id=? ORDER BY id", (_meeting_id(cur, meeting),))
    return [dict(row) for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# The guest who has no account
# ---------------------------------------------------------------------------

def test_a_guest_with_no_account_is_invited_by_name_and_email(cur, outbox):
    """The reported gap: the person you are actually meeting."""
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    assert result["invited_contacts"] == [
        {"email": "dana@outside.example", "name": "Dana Reeves"}]
    rows = _invites(cur, meeting)
    assert len(rows) == 1
    assert rows[0]["invitee_email"] == "dana@outside.example"
    assert rows[0]["invitee_name"] == "Dana Reeves"
    assert rows[0]["invitee_key"] == "e:dana@outside.example"
    assert int(rows[0]["invitee_user_id"]) == 0


def test_the_guest_is_emailed_at_the_address_the_host_typed(cur, outbox):
    """An invitation nobody receives is a note to self."""
    meeting = _schedule(cur)
    outbox.clear()

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "Dana@Outside.example"}])

    addressed = [mail["to_email"] for mail in outbox]
    assert "dana@outside.example" in addressed, (
        f"the guest was never mailed; outbox went to {addressed}")
    guest_mail = next(m for m in outbox if m["to_email"] == "dana@outside.example")
    assert int(guest_mail["user_id"]) == 0, (
        "a guest with no account must not be filed under someone else's id")
    assert meeting["public_id"] in guest_mail["metadata"].values()


def test_two_outside_guests_can_both_be_invited(cur, outbox):
    """The constraint test.

    Both of these store ``invitee_user_id = 0``. Under the original
    ``UNIQUE(meeting_id, invitee_user_id)`` the second one was not a second
    guest, it was an integrity error — so "invite the couple" or "invite both
    advisors" was unrepresentable. If this ever fails with an integrity error,
    that constraint has come back.
    """
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"},
                  {"name": "Sam Okafor", "email": "sam@outside.example"}])

    assert len(result["invited_contacts"]) == 2
    keys = {row["invitee_key"] for row in _invites(cur, meeting)}
    assert keys == {"e:dana@outside.example", "e:sam@outside.example"}


def test_nobody_can_answer_an_outside_guests_invitation_for_them(cur, outbox):
    """A hole this very change opened, closed and pinned.

    Storing outside guests as ``invitee_user_id = 0`` made zero a value the
    table actually holds. ``respond_invite`` looked invitations up by that
    column, and its ``int(user_id or 0)`` turns a missing identity into zero —
    so a caller with no identity matched the first guest row on the meeting and
    could accept or decline on that person's behalf. The query was never safe;
    it was only unreachable, because until now no row could hold zero.
    """
    meeting = _schedule(cur)
    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    for anonymous in (0, None):
        with pytest.raises(meetings.PrivateMeetingRejected) as caught:
            meetings.respond_invite(cur, user_id=anonymous,
                                    meeting_ref=meeting["public_id"],
                                    accept=True)
        assert caught.value.status == 404

    assert [row["status"] for row in _invites(cur, meeting)] == ["PENDING"], (
        "an anonymous caller answered a guest's invitation")


def test_a_member_can_still_answer_their_own_invitation(cur, outbox):
    """The guard above must not have cost the real feature its function."""
    meeting = _schedule(cur)
    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=meeting["public_id"],
                          user_ids=[MEMBER])

    result = meetings.respond_invite(cur, user_id=MEMBER,
                                     meeting_ref=meeting["public_id"],
                                     accept=True)

    assert result["invite_status"] == "ACCEPTED"


def test_a_guest_is_not_a_participant_until_they_have_an_account(cur, outbox):
    """Participation is keyed on user_id, and they have none.

    A placeholder row here would put a participant that identifies nobody into
    the table the waiting room, the capacity check and the roster all read.
    """
    meeting = _schedule(cur)

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    assert [row["user_id"] for row in _participants(cur, meeting)] == [HOST]


# ---------------------------------------------------------------------------
# One identity
# ---------------------------------------------------------------------------

def test_the_same_address_typed_twice_is_one_invitee(cur, outbox):
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"},
                  {"name": "Dana R.", "email": "  DANA@Outside.Example  "}])

    assert len(result["invited_contacts"]) == 1, (
        "case and surrounding space are not a different person")
    assert len(_invites(cur, meeting)) == 1


def test_re_inviting_the_same_guest_does_not_make_a_second_row(cur, outbox):
    meeting = _schedule(cur)
    guest = [{"name": "Dana Reeves", "email": "dana@outside.example"}]

    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=meeting["public_id"], invitees=guest)
    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=meeting["public_id"], invitees=guest)

    assert len(_invites(cur, meeting)) == 1


def test_an_address_belonging_to_a_member_invites_the_member(cur, outbox):
    """Identity is the account; the address is just how the host reached for it.

    Otherwise the same person is both a member invitee and an outside guest,
    gets two emails, and shows twice on the roster.
    """
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "A Member", "email": MEMBER_EMAIL.upper()}])

    assert result["invited"] == [MEMBER]
    assert result["invited_contacts"] == []
    rows = _invites(cur, meeting)
    assert len(rows) == 1
    assert rows[0]["invitee_key"] == f"u:{MEMBER}"
    assert MEMBER in [row["user_id"] for row in _participants(cur, meeting)], (
        "a member invited by address is still a member")


def test_a_member_invited_by_id_and_by_address_is_one_invitee(cur, outbox):
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        user_ids=[MEMBER], invitees=[{"name": "Dup", "email": MEMBER_EMAIL}])

    assert result["invited"] == [MEMBER]
    assert len(_invites(cur, meeting)) == 1


# ---------------------------------------------------------------------------
# What is refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("address", ["not-an-email", "@example.com", "a@b",
                                     "two@@at.example", "spaces in@x.example"])
def test_a_malformed_address_is_refused_rather_than_stored(cur, outbox, address):
    """Storing it would mean a silent non-delivery later.

    The host is told now, while they are still looking at the field, instead of
    the invitation simply never arriving.
    """
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Typo", "email": address}])

    assert result["invited_contacts"] == []
    assert [s["reason"] for s in result["skipped"]] == ["email_invalid"]
    assert _invites(cur, meeting) == []


def test_an_invitee_with_a_name_but_no_address_is_refused(cur, outbox):
    """A name alone is not an identity and cannot be invited anywhere."""
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Only A Name", "email": ""}])

    assert result["invited_contacts"] == []
    assert [s["reason"] for s in result["skipped"]] == ["email_required"]
    assert _invites(cur, meeting) == []


def test_the_host_is_not_invited_to_their_own_meeting_by_their_address(cur, outbox):
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Me", "email": HOST_EMAIL}])

    assert result["invited"] == []
    assert result["invited_contacts"] == []
    assert [s["reason"] for s in result["skipped"]] == ["is_host"]


def test_one_bad_address_does_not_cost_the_others_their_invitation(cur, outbox):
    """Partial success, reported honestly: some invited, the rest named."""
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Good", "email": "good@outside.example"},
                  {"name": "Bad", "email": "nope"},
                  {"name": "Also Good", "email": "also@outside.example"}])

    assert {c["email"] for c in result["invited_contacts"]} == {
        "good@outside.example", "also@outside.example"}
    assert [s["reason"] for s in result["skipped"]] == ["email_invalid"]


# ---------------------------------------------------------------------------
# Scheduling and inviting in one request
# ---------------------------------------------------------------------------

def test_scheduling_with_invitees_invites_them_in_the_same_request(cur, outbox):
    """The wizard collects both before the host presses confirm.

    Two requests would mean a meeting could exist with nobody on it because
    the app was closed in between — with the host already told otherwise.
    """
    meeting = _schedule(
        cur, invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    assert meeting["invite_result"]["invited_contacts"] == [
        {"email": "dana@outside.example", "name": "Dana Reeves"}]
    cur.execute("SELECT invitee_email FROM private_meeting_invites "
                "WHERE meeting_id=?", (_meeting_id(cur, meeting),))
    assert [row[0] for row in cur.fetchall()] == ["dana@outside.example"]


def test_a_meeting_scheduled_with_invitees_still_exists_if_their_mail_fails(
        cur, monkeypatch):
    """Mission §13, extended to the invitee path.

    The confirmation is a courtesy; the booking is the commitment.
    """
    from services.private_office import meeting_emails

    monkeypatch.setattr(
        meeting_emails, "announce",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down")))

    meeting = _schedule(
        cur, invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    cur.execute("SELECT COUNT(*) FROM private_meetings WHERE public_id=?",
                (meeting["public_id"],))
    assert cur.fetchone()[0] == 1, "the meeting was lost with the email"
    cur.execute("SELECT COUNT(*) FROM private_meeting_invites WHERE meeting_id=?",
                (_meeting_id(cur, meeting),))
    assert cur.fetchone()[0] == 1, "the invitation was lost with the email"


# ---------------------------------------------------------------------------
# Relationship Intelligence
# ---------------------------------------------------------------------------

def _person_facts(cur, owner):
    cur.execute(
        "SELECT subject_id, fact_type, provenance_ref FROM private_facts "
        "WHERE owner_user_id=? ORDER BY id", (owner,))
    return [dict(row) for row in cur.fetchall()]


def test_an_invitee_becomes_a_person_keyed_on_their_address(cur, outbox):
    meeting = _schedule(cur)

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    cur.execute(
        "SELECT external_ref FROM private_graph_nodes "
        "WHERE owner_user_id=? AND node_type='PERSON'", (HOST,))
    refs = [row[0] for row in cur.fetchall()]
    assert len(refs) == 1
    assert refs[0] == relationships._invitee_external_ref(
        email="dana@outside.example")
    assert "dana@outside.example" not in refs[0], (
        "a person's address should not sit in a join key in the clear")


def test_inviting_the_same_person_to_a_second_meeting_reuses_the_person(cur, outbox):
    guest = [{"name": "Dana Reeves", "email": "dana@outside.example"}]
    first = _schedule(cur)
    second = _schedule(cur)

    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=first["public_id"], invitees=guest)
    meetings.invite_users(cur, actor_user_id=HOST,
                          meeting_ref=second["public_id"], invitees=guest)

    cur.execute(
        "SELECT COUNT(*) FROM private_graph_nodes "
        "WHERE owner_user_id=? AND node_type='PERSON'", (HOST,))
    assert cur.fetchone()[0] == 1, (
        "meeting the same person twice should not create a second person")


def test_two_guests_who_share_a_name_are_two_people(cur, outbox):
    """Mission §4: never merge by name alone.

    A name-keyed identity would either fuse these two into one person or, worse,
    file one member's private notes about their accountant against an unrelated
    namesake.
    """
    meeting = _schedule(cur)

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Chris Taylor", "email": "chris.a@outside.example"},
                  {"name": "Chris Taylor", "email": "chris.b@outside.example"}])

    cur.execute(
        "SELECT COUNT(*) FROM private_graph_nodes "
        "WHERE owner_user_id=? AND node_type='PERSON'", (HOST,))
    assert cur.fetchone()[0] == 2


def test_a_name_with_no_address_creates_no_person(cur, outbox):
    """No identity, no link. The refusal is the feature."""
    assert relationships._invitee_external_ref(email="") == ""
    assert relationships.link_meeting_invitee(
        cur, owner_user_id=HOST, name="Only A Name") == {}

    cur.execute("SELECT COUNT(*) FROM private_graph_nodes "
                "WHERE owner_user_id=?", (HOST,))
    assert cur.fetchone()[0] == 0


def test_the_person_records_the_meeting_it_came_from(cur, outbox):
    """Provenance, so "why does PulseSoc know this?" has an answer."""
    meeting = _schedule(cur)

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    refs = [facts_mod.decode_provenance_ref(row["provenance_ref"])
            for row in _person_facts(cur, HOST) if row["provenance_ref"]]
    assert refs, "the person's facts carry no provenance ref"
    assert any(ref.source_type == relationships.PROVENANCE_MEETING_INVITEE
               and ref.source_id == meeting["public_id"] for ref in refs), (
        f"no fact traces back to this meeting; got "
        f"{[(r.source_type, r.source_id) for r in refs]}")


def test_linking_a_person_sends_them_nothing(cur, outbox):
    """Being remembered is not an event the other person is told about.

    The only mail this path produces is the invitation itself.
    """
    meeting = _schedule(cur)
    outbox.clear()

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    # The first assertion is what stops the second from being vacuous: an
    # outbox that is empty because the mail path broke would otherwise satisfy
    # "nothing extra was sent" perfectly.
    to = {mail["to_email"] for mail in outbox}
    assert "dana@outside.example" in to, (
        f"the invitation itself never went out; got {to}")
    kinds = {mail["metadata"].get("meeting_email_kind") for mail in outbox}
    assert kinds <= {"CONFIRMATION"}, (
        f"something other than the invitation was sent: {kinds}")


def test_a_relationship_failure_does_not_cost_the_invitation(cur, monkeypatch,
                                                             outbox):
    """The graph is downstream of the booking, in both directions."""
    monkeypatch.setattr(
        relationships, "link_meeting_invitee",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("graph down")))
    meeting = _schedule(cur)

    result = meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])

    assert len(result["invited_contacts"]) == 1
    assert len(_invites(cur, meeting)) == 1


def test_one_unlinkable_invitee_does_not_cost_the_others_their_link(
        cur, monkeypatch, outbox):
    """The inner guard, which the outer one otherwise hides.

    ``_link_relationships`` catches per invitee *and* wraps the whole loop in a
    savepointed swallow. The outer swallow alone would satisfy "a graph failure
    does not cancel the booking" — it just does so by abandoning the rest of
    the loop, so one guest whose link fails silently costs every guest after
    them theirs. Only a mixed batch can tell the two apart.
    """
    real = relationships.link_meeting_invitee
    seen = []

    def flaky(cur_, **kwargs):
        seen.append(kwargs.get("email"))
        if kwargs.get("email") == "dana@outside.example":
            raise RuntimeError("graph down for this one")
        return real(cur_, **kwargs)

    monkeypatch.setattr(relationships, "link_meeting_invitee", flaky)
    meeting = _schedule(cur)

    meetings.invite_users(
        cur, actor_user_id=HOST, meeting_ref=meeting["public_id"],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"},
                  {"name": "Sam Okafor", "email": "sam@outside.example"}])

    assert seen == ["dana@outside.example", "sam@outside.example"], (
        f"the loop stopped at the failure instead of continuing; tried {seen}")
    cur.execute(
        "SELECT external_ref FROM private_graph_nodes "
        "WHERE owner_user_id=? AND node_type='PERSON'", (HOST,))
    refs = {row[0] for row in cur.fetchall()}
    assert refs == {relationships._invitee_external_ref(
        email="sam@outside.example")}, (
        "the invitee after the failure was never linked")


# ---------------------------------------------------------------------------
# Identity helpers, directly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("  Dana@Outside.Example ", "dana@outside.example"),
    ("dana@outside.example", "dana@outside.example"),
    ("", ""),
    (None, ""),
])
def test_addresses_fold_the_way_accounts_do(raw, expected):
    assert meetings.normalize_invite_email(raw) == expected


def test_dots_and_plus_tags_are_not_folded_away():
    """Provider-specific folding would merge two real, different people.

    Correct at Gmail, wrong nearly everywhere else — and a wrong merge sends
    someone else's meeting to the wrong inbox.
    """
    assert (meetings.normalize_invite_email("a.b+tag@outside.example")
            != meetings.normalize_invite_email("ab@outside.example"))


def test_identity_prefers_the_account_over_the_address():
    assert meetings.invite_identity(user_id=7, email="x@y.example") == "u:7"
    assert meetings.invite_identity(email="X@Y.example") == "e:x@y.example"
    assert meetings.invite_identity() == ""


# ---------------------------------------------------------------------------
# What the confirmation screen is allowed to claim
# ---------------------------------------------------------------------------
#
# The wizard used to close on success and say nothing, which is indistinguishable
# from a wizard that closed on failure. Replacing it with a confirmation screen
# only helps if every line on that screen is something the server asserted —
# otherwise the fake success moves one screen later and gets more convincing.
# These tests pin the three fields that screen reads.


def test_the_create_response_reports_the_reminders_that_exist(cur, outbox):
    """Not the ladder — the rows.

    ``DEFAULT_REMINDER_OFFSETS`` is intent. ``_plan_reminders`` is non-fatal by
    design, so intent and reality can differ, and a screen reciting the ladder
    from a constant would promise three mails in exactly the case where none
    were planned.
    """
    meeting = _schedule(cur)

    offsets = [row["offset_minutes"] for row in meeting["reminders"]]
    assert offsets, "the create response claimed no reminders at all"
    assert sorted(offsets, reverse=True) == sorted(
        meetings.DEFAULT_REMINDER_OFFSETS, reverse=True), (
        f"reported {offsets}, ladder is {meetings.DEFAULT_REMINDER_OFFSETS}")

    # And they are the rows, not a copy of the constant: every one must be
    # findable in the table with the send_at that was reported for it.
    meeting_id = _meeting_id(cur, meeting)
    cur.execute(
        "SELECT offset_minutes, send_at FROM private_meeting_reminders "
        "WHERE meeting_id=? AND user_id=? AND kind='REMINDER'",
        (meeting_id, HOST))
    stored = {int(row[0]): str(row[1]) for row in cur.fetchall()}
    assert {row["offset_minutes"]: row["send_at"]
            for row in meeting["reminders"]} == stored


def test_a_meeting_whose_reminders_could_not_be_planned_admits_it(
        cur, monkeypatch, outbox):
    """The case the constant would have lied about.

    A booking survives a reminder store that is down — that is settled
    elsewhere. What is settled here is that it does not go on to tell the host
    they will be reminded.
    """
    from services.private_office import meeting_reminders

    def broken(*args, **kwargs):
        raise RuntimeError("reminder store down")

    monkeypatch.setattr(meeting_reminders, "plan_reminders", broken)
    meeting = _schedule(cur)

    assert _meeting_id(cur, meeting), "the booking did not survive"
    assert meeting["reminders"] == [], (
        f"claimed reminders none of which were planned: {meeting['reminders']}")


def test_an_offset_already_in_the_past_is_reported_not_hidden(cur, outbox):
    """Booking 30 minutes out cannot honour a 24h reminder.

    The row is written SKIPPED rather than dropped, and it is reported rather
    than filtered, because "you will be reminded 24 hours before" is false and
    a silence where that line would be is not how anyone reads a list.
    """
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    meeting = meetings.create_meeting(
        cur, owner_user_id=HOST, title="Soon", scheduled_start_at=soon,
        duration_minutes=15, timezone_name="UTC")

    by_offset = {row["offset_minutes"]: row["status"]
                 for row in meeting["reminders"]}
    assert by_offset.get(1440) == "SKIPPED", (
        f"the unhonourable 24h reminder was not reported as skipped: {by_offset}")
    assert by_offset.get(15) == "PENDING", (
        f"the 15m reminder should still be live: {by_offset}")


def test_a_member_invited_by_address_is_named_not_counted(cur, outbox):
    """The host typed an address; the confirmation must be able to show it.

    An address belonging to a member is invited as that member, and the id is
    all ``invited`` carries. A screen with nothing but that id can only say
    "1 member invited" — which is precisely the summary that lets a wrong
    address through unread. ``invited_members`` is the same person with the
    label the host supplied.
    """
    meeting = _schedule(cur, invitees=[
        {"name": "Morgan Ellis", "email": MEMBER_EMAIL.upper()},
        {"name": "Dana Reeves", "email": "dana@outside.example"},
    ])
    result = meeting["invite_result"]

    assert result["invited"] == [MEMBER]
    assert result["invited_members"] == [
        {"user_id": MEMBER, "email": MEMBER_EMAIL, "name": "Morgan Ellis"}], (
        f"the member's own typed address was lost: {result['invited_members']}")
    assert result["invited_contacts"] == [
        {"email": "dana@outside.example", "name": "Dana Reeves"}]


def test_the_label_survives_being_offered_by_id_first(cur, outbox):
    """Otherwise the confirmation could name someone or not by request order.

    Sending a member's id *and* typing their address is one invitee. The id
    offer arrives first and carries no label, and the address offer that would
    have supplied one is deduplicated away — so without the merge the host
    watches the same person be nameable or not depending on which list they
    happened to end up in.
    """
    meeting = _schedule(
        cur, invite_user_ids=[MEMBER],
        invitees=[{"name": "Morgan Ellis", "email": MEMBER_EMAIL}])
    result = meeting["invite_result"]

    assert result["invited"] == [MEMBER], "one person, invited twice"
    assert result["invited_members"] == [
        {"user_id": MEMBER, "email": MEMBER_EMAIL, "name": "Morgan Ellis"}]


def test_every_invited_member_is_listed_even_with_nothing_to_show(cur, outbox):
    """`invited_members` is `invited` with labels, not the subset that had any.

    A member invited by id alone has no name and no typed address, so the
    temptation is to leave them out of a list whose whole purpose is display.
    That drops them off the confirmation entirely while `invited` still counts
    them — the host reads a shorter guest list than the one the server acted on,
    with nothing on screen suggesting anyone is missing. It is the counting bug
    again, arrived at by trying to avoid an empty row.

    The empty entry is renderable: the screen names the member id rather than
    leaving a blank, which is thin but visibly thin. Same length and same order
    as `invited`, always — that parity is the contract.
    """
    meeting = _schedule(
        cur, invite_user_ids=[MEMBER],
        invitees=[{"name": "Dana Reeves", "email": "dana@outside.example"}])
    result = meeting["invite_result"]

    assert result["invited"] == [MEMBER]
    assert result["invited_members"] == [
        {"user_id": MEMBER, "email": "", "name": ""}], (
        "a member with no label was dropped from the display list: "
        f"{result['invited_members']}")
    assert [m["user_id"] for m in result["invited_members"]] == result["invited"]


def test_a_replayed_booking_confirms_as_much_as_the_first_one(cur, outbox):
    """A double-tapped Schedule must not report less than a single tap.

    Idempotency returns the original meeting, and the original meeting's
    confirmation screen is the one the host is about to read. A bare projection
    there would show a meeting with no invitees and no reminders — a second tap
    quietly retracting the first tap's answer.
    """
    first = _schedule(cur, idempotency_key="tap-1", invitees=[
        {"name": "Dana Reeves", "email": "dana@outside.example"}])
    second = _schedule(cur, idempotency_key="tap-1", invitees=[
        {"name": "Dana Reeves", "email": "dana@outside.example"}])

    assert second["public_id"] == first["public_id"], "a second meeting was made"
    assert second["invite_result"]["invited_contacts"] == [
        {"email": "dana@outside.example", "name": "Dana Reeves"}]
    assert ([row["offset_minutes"] for row in second["reminders"]]
            == [row["offset_minutes"] for row in first["reminders"]])


def test_a_replay_reports_the_rows_not_the_request(cur, outbox):
    """The retry's body is not evidence of anything.

    A replay that echoed its own ``invitees`` back would confirm an invitation
    this call never wrote — which is the same fabrication as the original bug,
    reached by retrying instead of by failing.
    """
    first = _schedule(cur, idempotency_key="tap-2")
    assert first["invite_result"]["invited_contacts"] == []

    replay = _schedule(cur, idempotency_key="tap-2", invitees=[
        {"name": "Never Invited", "email": "ghost@outside.example"}])

    assert replay["public_id"] == first["public_id"]
    assert replay["invite_result"]["invited_contacts"] == [], (
        "the replay confirmed an invitation it did not send: "
        f"{replay['invite_result']['invited_contacts']}")
    assert [row["invitee_email"] for row in _invites(cur, first)] == [], (
        "and it wrote one")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "-p", "no:randomly"]))
