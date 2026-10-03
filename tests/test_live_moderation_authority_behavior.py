"""The live-ban authority, end to end: writer, readers, and who may use it.

``pulse_live_moderation`` shipped with six authorization readers and no way to
write a row. Every one of those checks ran, consulted an empty table, and said
"not banned" — correctly, because nothing could ever have banned anybody. The
table had zero rows in production and the enforcement looked complete in code
review.

So the property this file exists to prove is not "the ban query works". It is
that a decision made by an authorized moderator is *observed by the readers
that already existed*. Tests that only assert the service's own round trip
would have passed on the day the authority did not exist, which makes them
worth very little. Every behavioural test here therefore ends at one of the six
shipped call sites, through ``pulse_live_user_is_blocked``.

Three things are asserted that are easy to get wrong in the other direction:

* **A live ban is not a social block.** ``pulse_live_viewer_authorized``
  reports ``live_blocked`` and ``social_blocked`` separately. Writing
  ``blocked_users`` from a ban would silently upgrade "get out of my stream"
  into "I never want to hear from this person again" — a privacy decision the
  moderator did not make. A test asserts the ban leaves that table empty.
* **Reading fails closed.** A read error must deny the protected action. The
  sentinel mission found a profile-block check that returned ALLOW on this
  exact path.
* **Scope is one Live.** A ban on Live A must not follow the target to Live B,
  and moderating Live A must not confer authority over Live B.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB = tempfile.mkstemp(suffix=".db", prefix="live_moderation_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"

import bot  # noqa: E402
from services import live_moderation, live_participants  # noqa: E402

HOST = 97001
COHOST = 97002
GUEST = 97003
VIEWER = 97004
OTHER = 97005
NOW = "2026-10-03T00:00:00"

LIVE_A = 9701
LIVE_B = 9702

BAN_URL = "/api/pulse/live/{live}/viewers/{target}/{action}"
STATE_URL = "/api/pulse/live/{live}/moderation"


def _conn():
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    return conn


def setUpModule():
    bot.init_db()
    conn = _conn()
    for user_id, name in (
        (HOST, "host"), (COHOST, "cohost"), (GUEST, "guest"),
        (VIEWER, "viewer"), (OTHER, "other"),
    ):
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, email, created_at) VALUES (?,?,?,?)",
            (user_id, f"lm_{name}", f"lm_{name}@example.com", NOW),
        )
    for live_id in (LIVE_A, LIVE_B):
        conn.execute(
            "INSERT OR IGNORE INTO pulse_live_sessions "
            "(id, user_id, status, audience, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (live_id, HOST, "live", "public", NOW, NOW),
        )
    # The co-host's authority comes from this row, and only on Live A. That is
    # the whole mechanism preventing cross-live moderation, so Live B is left
    # deliberately without one.
    conn.execute(
        "INSERT OR IGNORE INTO pulse_live_guests "
        "(live_id, user_id, guest_role, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (LIVE_A, COHOST, live_participants.ROLE_COHOST, "active", NOW, NOW),
    )
    conn.execute(
        "INSERT OR IGNORE INTO pulse_live_guests "
        "(live_id, user_id, guest_role, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (LIVE_A, GUEST, live_participants.ROLE_GUEST, "active", NOW, NOW),
    )
    conn.commit()
    conn.close()
    bot.webhook_app.config["TESTING"] = True


def tearDownModule():
    try:
        os.unlink(_DB)
    except OSError:
        pass


def _clear_bans():
    conn = _conn()
    conn.execute("DELETE FROM pulse_live_moderation")
    conn.commit()
    conn.close()


def _rows(live_id=LIVE_A, target=VIEWER):
    conn = _conn()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pulse_live_moderation WHERE live_id=? AND target_user_id=? ORDER BY id",
        (live_id, target),
    )]
    conn.close()
    return rows


def _as(user_id, username="actor"):
    """Sign the test client in as a user, the way the route sees it."""
    return mock.patch.object(
        bot, "api_account_user",
        lambda *a, **k: {"user_id": user_id, "username": username,
                         "email": f"{username}@example.com"},
    )


def _no_admin():
    """Nobody here is a platform admin unless a test says so.

    ``pulse_live_moderation_actor_role`` treats an admin as a host, so leaving
    this unpatched would let a stray admin session make every "a guest may not
    ban" test pass for the wrong reason.
    """
    return mock.patch.object(bot, "admin_current_user", lambda *a, **k: None)


def _post(client, actor, target, action, live=LIVE_A, body=None):
    with _as(actor), _no_admin():
        return client.post(
            BAN_URL.format(live=live, target=target, action=action),
            json=body or {},
        )


# =========================================================================
# 1. The service's own state rule
# =========================================================================

class BanStateRuleCase(unittest.TestCase):
    """Current state is EXISTS, not a count. The two must not drift."""

    def setUp(self):
        _clear_bans()
        self.conn = _conn()
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_an_unknown_pair_is_not_banned(self):
        self.assertFalse(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))

    def test_a_ban_is_observable(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertTrue(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))

    def test_re_banning_writes_nothing_and_says_so(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        again = live_moderation.ban(self.cur, LIVE_A, COHOST, VIEWER)
        self.assertEqual(again["status"], "already_active")
        self.conn.commit()
        self.assertEqual(len(_rows()), 1)

    def test_unban_clears_every_active_row(self):
        """The property that makes EXISTS safe under a race.

        Two moderators banning concurrently can both land a row. If unban
        cleared only one, the viewer would stay banned after a moderator was
        told they had been let back in — and no amount of unbanning from the UI
        would ever fix it, because each press clears one row and the UI reports
        success.
        """
        for _ in range(3):
            self.cur.execute(
                "INSERT INTO pulse_live_moderation "
                "(live_id, moderator_user_id, target_user_id, action, reason, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (LIVE_A, HOST, VIEWER, "ban", "", "active", NOW, NOW),
            )
        self.conn.commit()
        self.assertEqual(len(_rows()), 3)
        result = live_moderation.unban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertEqual(result["status"], "unbanned")
        self.assertFalse(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))

    def test_unbanning_someone_who_is_not_banned_is_not_an_error(self):
        result = live_moderation.unban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertEqual(result, {"status": "not_banned", "cleared": 0})

    def test_reversal_keeps_the_history(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        live_moderation.unban(self.cur, LIVE_A, HOST, VIEWER)
        self.conn.commit()
        rows = _rows()
        self.assertEqual(len(rows), 1, "unban must reverse, never delete")
        self.assertEqual(rows[0]["status"], live_moderation.STATUS_REVERSED)

    def test_a_reversed_row_is_not_a_ban(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        live_moderation.unban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertFalse(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))

    def test_a_target_can_be_re_banned_after_an_unban(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        live_moderation.unban(self.cur, LIVE_A, HOST, VIEWER)
        result = live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertEqual(result["status"], "banned")
        self.assertTrue(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))

    def test_every_legacy_ban_spelling_is_still_read_as_a_ban(self):
        """The shipped predicate has accepted four spellings since before this
        module existed. Narrowing the reader to the one spelling we write would
        silently un-ban anything written by hand or by a future caller."""
        for spelling in live_moderation.BAN_ACTIONS:
            self.conn.commit()
            _clear_bans()
            self.cur.execute(
                "INSERT INTO pulse_live_moderation "
                "(live_id, moderator_user_id, target_user_id, action, reason, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (LIVE_A, HOST, VIEWER, spelling, "", "active", NOW, NOW),
            )
            self.assertTrue(
                live_moderation.is_banned(self.cur, LIVE_A, VIEWER),
                f"the reader stopped recognising {spelling!r} as a ban",
            )


# =========================================================================
# 2. Scope: one ban, one Live, one target
# =========================================================================

class BanScopeCase(unittest.TestCase):
    def setUp(self):
        _clear_bans()
        self.conn = _conn()
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def test_a_ban_does_not_follow_the_target_to_another_live(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertTrue(live_moderation.is_banned(self.cur, LIVE_A, VIEWER))
        self.assertFalse(live_moderation.is_banned(self.cur, LIVE_B, VIEWER))

    def test_banning_one_viewer_does_not_ban_another(self):
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        self.assertFalse(live_moderation.is_banned(self.cur, LIVE_A, OTHER))

    def test_a_ban_never_writes_a_social_block(self):
        """`pulse_live_viewer_authorized` reports live_blocked and
        social_blocked separately and neither implies the other. Upgrading one
        to the other would be a privacy decision nobody made."""
        live_moderation.ban(self.cur, LIVE_A, HOST, VIEWER)
        self.conn.commit()
        conn = _conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM blocked_users WHERE blocked_user_id=?", (VIEWER,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)


# =========================================================================
# 3. Reading cannot quietly answer "not banned"
# =========================================================================

class FailClosedReadCase(unittest.TestCase):
    class _Exploding:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("database is locked")

    class _MissingTable:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("no such table: pulse_live_moderation")

    def test_a_read_error_denies_the_protected_action(self):
        with self.assertLogs("services.live_moderation", level="WARNING") as logs:
            self.assertTrue(live_moderation.is_banned(self._Exploding(), LIVE_A, VIEWER))
        self.assertIn("LIVE_MODERATION_READ_FAILED", "\n".join(logs.output))

    def test_the_bot_level_reader_inherits_fail_closed(self):
        """The six enforcement sites call this name, not the service directly.
        If the delegation were ever unwound, this is what notices."""
        self.assertTrue(bot.pulse_live_user_is_blocked(self._Exploding(), LIVE_A, VIEWER))

    def test_an_absent_table_is_reported_loudly_and_distinctly(self):
        """Argued, not assumed: init_db() creates this table unconditionally on
        every boot and production has it, so failing closed here would deny all
        live access in a partial harness while buying no production safety. The
        guarantee that the table keeps existing is enforced statically instead,
        by tests/protection/test_live_moderation_authority.py."""
        with self.assertLogs("services.live_moderation", level="ERROR") as logs:
            self.assertFalse(live_moderation.is_banned(self._MissingTable(), LIVE_A, VIEWER))
        self.assertIn("LIVE_MODERATION_TABLE_MISSING", "\n".join(logs.output))


# =========================================================================
# 4. Who may ban whom (pure authorization)
# =========================================================================

class AuthorizationMatrixCase(unittest.TestCase):
    def _decide(self, role, actor, target):
        return live_moderation.authorize(
            role,
            actor_user_id=actor,
            target_user_id=target,
            host_user_id=HOST,
            can_moderate=live_participants.can_moderate,
        )

    def test_a_host_may_ban_a_viewer(self):
        self.assertEqual(self._decide(live_participants.ROLE_HOST, HOST, VIEWER), (True, ""))

    def test_a_cohost_may_ban_a_viewer(self):
        self.assertEqual(self._decide(live_participants.ROLE_COHOST, COHOST, VIEWER), (True, ""))

    def test_a_guest_may_not_ban(self):
        self.assertEqual(
            self._decide(live_participants.ROLE_GUEST, GUEST, VIEWER),
            (False, "not_a_moderator"),
        )

    def test_an_audience_member_may_not_ban(self):
        self.assertEqual(
            self._decide(live_participants.ROLE_AUDIENCE, VIEWER, OTHER),
            (False, "not_a_moderator"),
        )

    def test_a_cohost_may_not_ban_the_host(self):
        """A co-host runs the room. They do not get to evict its owner."""
        self.assertEqual(
            self._decide(live_participants.ROLE_COHOST, COHOST, HOST),
            (False, "cannot_moderate_host"),
        )

    def test_nobody_may_ban_themselves(self):
        self.assertEqual(
            self._decide(live_participants.ROLE_COHOST, COHOST, COHOST),
            (False, "cannot_moderate_self"),
        )

    def test_a_missing_identity_is_refused_before_any_permission_question(self):
        self.assertEqual(
            self._decide(live_participants.ROLE_HOST, HOST, 0),
            (False, "invalid_identity"),
        )

    def test_an_unknown_role_string_does_not_grant_authority(self):
        """normalize_role() defaults to audience rather than raising, so a role
        this code has never heard of must land on the deny side."""
        self.assertEqual(
            self._decide("supreme_overlord", VIEWER, OTHER),
            (False, "not_a_moderator"),
        )


# =========================================================================
# 5. The route, and the six readers observing it
# =========================================================================

class ModerationRouteCase(unittest.TestCase):
    def setUp(self):
        _clear_bans()
        self.client = bot.webhook_app.test_client()

    def _is_blocked(self, live=LIVE_A, target=VIEWER):
        """Ask the question the way all six enforcement sites ask it."""
        conn = _conn()
        try:
            return bot.pulse_live_user_is_blocked(conn.cursor(), live, target)
        finally:
            conn.close()

    def _gate(self, viewer=VIEWER, live=LIVE_A):
        """Run the canonical audience gate the way a token mint would.

        It consults ``admin_current_user()``, which needs a request context and
        would otherwise short-circuit the whole gate to "host" — so the context
        is real and the admin answer is pinned to None.
        """
        conn = _conn()
        cur = conn.cursor()
        row = dict(cur.execute(
            "SELECT * FROM pulse_live_sessions WHERE id=?", (live,)).fetchone())
        try:
            with bot.webhook_app.test_request_context("/"), _no_admin():
                return bot.pulse_live_viewer_authorized(cur, row, viewer)
        finally:
            conn.close()

    def test_a_host_ban_is_observed_by_the_shipped_reader(self):
        response = _post(self.client, HOST, VIEWER, "ban")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["banned"])
        self.assertTrue(self._is_blocked())

    def test_the_canonical_audience_gate_reports_live_blocked(self):
        """The gate is the shared entry point for Live tokens and Feed entry,
        and it distinguishes a live ban from a social block. A ban must land on
        the live_blocked branch, or the two controls have been conflated."""
        _post(self.client, HOST, VIEWER, "ban")
        allowed, reason = self._gate()
        self.assertFalse(allowed)
        self.assertEqual(reason, "live_blocked")

    def test_unban_restores_access_through_the_same_gate(self):
        _post(self.client, HOST, VIEWER, "ban")
        self.assertTrue(self._is_blocked())
        response = _post(self.client, HOST, VIEWER, "unban")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["banned"])
        self.assertFalse(self._is_blocked())
        allowed, _ = self._gate()
        self.assertTrue(allowed, "unban must restore access, not just clear a row")

    def test_a_cohost_may_ban(self):
        response = _post(self.client, COHOST, VIEWER, "ban")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(self._is_blocked())

    def test_a_guest_is_refused_and_writes_nothing(self):
        response = _post(self.client, GUEST, VIEWER, "ban")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(_rows(), [])
        self.assertFalse(self._is_blocked())

    def test_an_ordinary_viewer_is_refused(self):
        response = _post(self.client, VIEWER, OTHER, "ban")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._is_blocked(target=OTHER))

    def test_moderating_one_live_confers_no_authority_over_another(self):
        """The co-host's row is on Live A only. If the role were resolved from
        anything other than their participant row on the live being moderated,
        this would succeed — which is the cross-live IDOR this design exists to
        prevent."""
        response = _post(self.client, COHOST, VIEWER, "ban", live=LIVE_B)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._is_blocked(live=LIVE_B))

    def test_a_cohost_cannot_ban_the_host_through_the_route(self):
        response = _post(self.client, COHOST, HOST, "ban")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._is_blocked(target=HOST))

    def test_a_host_cannot_lock_themselves_out(self):
        response = _post(self.client, HOST, HOST, "ban")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self._is_blocked(target=HOST))

    def test_an_unknown_action_is_refused(self):
        response = _post(self.client, HOST, VIEWER, "obliterate")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(_rows(), [])

    def test_a_repeated_ban_is_idempotent_over_the_wire(self):
        _post(self.client, HOST, VIEWER, "ban")
        response = _post(self.client, HOST, VIEWER, "ban")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "already_active")
        self.assertEqual(len(_rows()), 1)

    def test_an_anonymous_caller_is_refused(self):
        with mock.patch.object(bot, "api_account_user", lambda *a, **k: None):
            response = self.client.post(
                BAN_URL.format(live=LIVE_A, target=VIEWER, action="ban"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_rows(), [])

    def test_an_unauthorized_caller_learns_nothing_about_the_target(self):
        """§19: the denial must be about the rule, not the account. A guest
        probing user ids must not be able to tell an existing account from a
        nonexistent one by the response."""
        real = _post(self.client, GUEST, VIEWER, "ban")
        fake = _post(self.client, GUEST, 90999999, "ban")
        self.assertEqual(real.status_code, fake.status_code)
        # trace_id is per-request by design and carries no information about
        # the target, so it is the one field allowed to differ.
        self.assertEqual(
            {k: v for k, v in real.get_json().items() if k != "trace_id"},
            {k: v for k, v in fake.get_json().items() if k != "trace_id"},
        )

    def test_the_moderator_note_is_bounded_and_single_line(self):
        _post(self.client, HOST, VIEWER, "ban",
              body={"reason": "spam\r\nSECOND LINE" + "x" * 600})
        stored = _rows()[0]["reason"]
        self.assertLessEqual(len(stored), live_moderation.REASON_MAX)
        self.assertNotIn("\n", stored)
        self.assertNotIn("\r", stored)

    def test_the_response_never_carries_the_private_note(self):
        response = _post(self.client, HOST, VIEWER, "ban",
                         body={"reason": "known troll account"})
        self.assertNotIn("known troll account", response.get_data(as_text=True))

    def test_what_the_banned_account_is_told_names_nobody(self):
        message = live_moderation.public_denial_message().lower()
        for leak in ("ban", "moderat", "host", "reason", "report"):
            self.assertNotIn(leak, message, f"the viewer-facing message leaks {leak!r}")


# =========================================================================
# 6. The moderator-only read
# =========================================================================

class ModerationStateReadCase(unittest.TestCase):
    def setUp(self):
        _clear_bans()
        self.client = bot.webhook_app.test_client()

    def _get(self, actor, live=LIVE_A):
        with _as(actor), _no_admin():
            return self.client.get(STATE_URL.format(live=live))

    def test_a_host_sees_the_current_bans(self):
        _post(self.client, HOST, VIEWER, "ban", body={"reason": "spam"})
        payload = self._get(HOST).get_json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["bans"][0]["target_user_id"], VIEWER)

    def test_a_cohost_sees_them_too(self):
        _post(self.client, HOST, VIEWER, "ban")
        self.assertEqual(self._get(COHOST).status_code, 200)

    def test_a_guest_cannot_read_the_moderation_state(self):
        """The rows carry `reason`, a private trust & safety note. The read is
        gated by the same authority as the write for that reason alone."""
        _post(self.client, HOST, VIEWER, "ban", body={"reason": "private note"})
        response = self._get(GUEST)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private note", response.get_data(as_text=True))

    def test_the_banned_viewer_cannot_read_their_own_ban_record(self):
        _post(self.client, HOST, VIEWER, "ban", body={"reason": "private note"})
        response = self._get(VIEWER)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("private note", response.get_data(as_text=True))

    def test_a_reversed_ban_leaves_the_list(self):
        _post(self.client, HOST, VIEWER, "ban")
        _post(self.client, HOST, VIEWER, "unban")
        self.assertEqual(self._get(HOST).get_json()["count"], 0)


if __name__ == "__main__":
    unittest.main()
