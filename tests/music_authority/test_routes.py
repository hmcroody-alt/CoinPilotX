"""Owner music takedown authority, exercised through the real Flask routes.

`test_policy.py` pins the decisions in isolation. This file pins the thing that
was actually broken: the route was *unreachable*. `/api/admin/pulse/music/<id>/
remove` resolves its caller with `admin_current_user()`, which reads
`session["admin_user_id"]` -- a key written in exactly one place, the web admin
login form. The owner on the phone is a `users` row authenticated by cookie or
bearer, so every attempt returned 401 before any permission was consulted.

So the authentication here is deliberately *not* mocked. Each test puts a real
value in the real session key and lets `account_user_id()` ->
`admin_user_by_account_user_id()` -> `resolve_actor()` run for itself. Two legs
are covered:

* `session["admin_user_id"]` -- the pre-existing web admin session.
* `session["account_user_id"]` -- the cookie leg of `account_user_id()`, which
  is what the native app and the website both authenticate with. This is the leg
  that did not previously reach an admin identity at all.

The bearer leg shares the same function and the same line (`account_user_id()`
returns `cookie_user_id or bearer_user_id or ...`), so the cookie leg proving the
link works proves the lookup; what it does not prove is bearer token minting,
which is `account_user_id`'s own business and has its own tests.

Four properties this file exists to defend:

1. Nothing the request *sends* selects an identity. A body carrying `role`,
   `is_owner` or `admin_user_id` is inert.
2. Permission comes from a named grant, not from holding some other permission.
   `pulse.moderate` -- which guards the old music review route -- grants no
   `music.*`.
3. A retry is not a second event. Repeating a takedown answers 200 with
   `changed: false` and writes no second audit row.
4. Purge is reachable only through schedule -> step-up -> confirm, and a legal
   hold stops it regardless of permission.

Run: .venv/bin/python3 -m pytest tests/music_authority/test_routes.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="music_authority_routes_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import music_authority  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

OWNER_ACCOUNT_ID = 9001
MODERATOR_ACCOUNT_ID = 9002
GRANTED_ACCOUNT_ID = 9003
PLAIN_ACCOUNT_ID = 9004
OWNER_PASSWORD = "Owner-Step-Up-P4ss!"

TRACK_AUDIO_URL = "https://cdn.example.test/pulse_music/track-one.mp3"


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_admin(email, role, account_user_id, password=None):
    """One `admin_users` row linked to an account id.

    `account_user_id` is the whole point: it is the stored column that bridges
    the two identity tables, and it is never read off a request.
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM admin_users WHERE email=?", (email,))
    now = datetime.now().isoformat()
    cur.execute(
        "INSERT INTO admin_users (email, password_hash, role, status, failed_login_count, "
        "must_change_password, account_user_id, created_at, updated_at) "
        "VALUES (?, ?, ?, 'active', 0, 0, ?, ?, ?)",
        (
            email,
            generate_password_hash(password) if password else "",
            role,
            account_user_id,
            now,
            now,
        ),
    )
    admin_id = cur.lastrowid
    conn.commit()
    conn.close()
    return admin_id


def _seed_track(title="Controlled Test Track", state="ACTIVE", legal_hold=0,
                audio_url=TRACK_AUDIO_URL):
    conn = _connect()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(timespec="seconds")
    legacy = music_authority.legacy_columns_for_state(state, now=now, actor_admin_id=0)
    cur.execute(
        "INSERT INTO pulse_audio_tracks (title, artist, uploader_user_id, audio_url, "
        "cover_art_url, safety_status, approved_by_admin, active, lifecycle_state, "
        "legal_hold, removed_at, created_at, updated_at) "
        "VALUES (?, 'Test Artist', ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            title,
            PLAIN_ACCOUNT_ID,
            audio_url,
            legacy["safety_status"],
            legacy["approved_by_admin"],
            legacy["active"],
            state,
            legal_hold,
            legacy["removed_at"],
            now,
            now,
        ),
    )
    track_id = cur.lastrowid
    conn.commit()
    conn.close()
    return track_id


def _track_row(track_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM pulse_audio_tracks WHERE id=?", (track_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _audit_rows(track_id):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM music_takedown_audit WHERE track_id=? ORDER BY action_id", (track_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


_FIXTURES = {}


def _seed_fixtures():
    """Seed the three admin identities once for the whole module.

    Deliberately not `setUpClass`: every class would re-run it against the same
    shared sqlite file, and the grant row below is uniquely keyed on
    (role_name, permission_key).
    """
    if _FIXTURES:
        return _FIXTURES
    bot.init_db()
    _FIXTURES["owner"] = _seed_admin(
        "music-owner@test.local", "owner", OWNER_ACCOUNT_ID, OWNER_PASSWORD
    )
    # `pulse_moderator` holds `pulse.moderate` and nothing from `music.*`.
    # Chosen over `owner` on purpose: `admin_has_permission` short-circuits
    # `return True` for role == "owner" before it looks at any permission, so an
    # owner-roled fixture here would pass without the grant check ever running
    # and the "no music permission" tests would be vacuous.
    _FIXTURES["moderator"] = _seed_admin(
        "music-moderator@test.local", "pulse_moderator", MODERATOR_ACCOUNT_ID
    )
    _FIXTURES["granted"] = _seed_admin(
        "music-granted@test.local", "analytics_viewer", GRANTED_ACCOUNT_ID
    )
    conn = _connect()
    conn.execute(
        "INSERT OR IGNORE INTO admin_role_permissions (role_name, permission_key) VALUES (?, ?)",
        ("analytics_viewer", "music.takedown"),
    )
    conn.commit()
    conn.close()
    return _FIXTURES


class MusicRouteTestCase(unittest.TestCase):
    """Shared harness: a real app, real sessions, no patched authentication."""

    def setUp(self):
        fixtures = _seed_fixtures()
        self.owner_admin_id = fixtures["owner"]
        self.moderator_admin_id = fixtures["moderator"]
        self.granted_admin_id = fixtures["granted"]
        bot.webhook_app.config["TESTING"] = True
        self.client = bot.webhook_app.test_client()
        # Step-up grants are per-admin, not per-track, so one left unconsumed by
        # an earlier test would silently authorize a purge here. The tests that
        # care about a grant existing create their own.
        conn = _connect()
        conn.execute("DELETE FROM music_owner_stepups")
        conn.commit()
        conn.close()
        self.track_id = _seed_track()

    def as_owner_session(self):
        """The pre-existing web admin session leg."""
        with self.client.session_transaction() as sess:
            sess["admin_user_id"] = self.owner_admin_id
            sess["admin_session_issued_at"] = datetime.now().isoformat()
            sess["admin_session_last_seen"] = datetime.now().isoformat()

    def as_account(self, account_user_id):
        """The native/website leg: a `users` id proven by the session cookie."""
        with self.client.session_transaction() as sess:
            sess.pop("admin_user_id", None)
            sess["account_user_id"] = account_user_id

    def post(self, path, payload=None):
        return self.client.post(
            path, data=json.dumps(payload or {}), content_type="application/json"
        )

    def body(self, response):
        return json.loads(response.data.decode("utf-8"))


class ReachabilityTests(MusicRouteTestCase):
    """The original defect: the owner could not reach the check at all."""

    def test_an_unauthenticated_caller_is_refused_before_any_state_changes(self):
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "OWNER_DECISION"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.body(response)["error_code"], "music_authority_required")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_an_account_authenticated_owner_reaches_the_route(self):
        """The whole mission in one assertion.

        Before the `admin_users.account_user_id` link existed this returned 401:
        the caller held a `users` identity and every `/api/admin/...` route only
        knew how to read `session["admin_user_id"]`.
        """
        self.as_account(OWNER_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "COPYRIGHT", "reason_note": "Label takedown notice."},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "TAKEN_DOWN")

    def test_the_web_admin_session_leg_still_works(self):
        """The existing login path is not regressed by adding a second one."""
        self.as_owner_session()
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "POLICY_VIOLATION"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "TAKEN_DOWN")

    def test_an_account_with_no_linked_admin_row_is_refused(self):
        """An ordinary member is not merely unauthorized -- it has no identity.

        401 rather than 403 is the correct answer: there is no admin actor to
        deny a permission to.
        """
        self.as_account(PLAIN_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "OWNER_DECISION"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_a_suspended_admin_link_stops_working(self):
        """`status='active'` is part of the lookup, not a display field."""
        conn = _connect()
        conn.execute("UPDATE admin_users SET status='suspended' WHERE id=?", (self.owner_admin_id,))
        conn.commit()
        conn.close()
        try:
            self.as_account(OWNER_ACCOUNT_ID)
            response = self.post(
                "/api/admin/music/tracks/%d/takedown" % self.track_id,
                {"reason_code": "OWNER_DECISION"},
            )
            self.assertEqual(response.status_code, 401)
        finally:
            conn = _connect()
            conn.execute("UPDATE admin_users SET status='active' WHERE id=?", (self.owner_admin_id,))
            conn.commit()
            conn.close()


class PermissionTests(MusicRouteTestCase):
    """Authority is a named grant, not a role name and not a nearby permission."""

    def test_pulse_moderate_does_not_imply_any_music_permission(self):
        """The old music review route is guarded by `pulse.moderate`.

        If holding it were enough, every Pulse moderator would silently have
        acquired the power to permanently destroy the music catalog the moment
        these endpoints shipped.
        """
        self.as_account(MODERATOR_ACCOUNT_ID)
        for path, payload in (
            ("takedown", {"reason_code": "COPYRIGHT"}),
            ("restore", {"reason_code": "OWNER_DECISION"}),
            ("schedule-purge", {"reason_code": "COPYRIGHT"}),
            ("cancel-purge", {"reason_code": "COPYRIGHT"}),
            ("purge", {"reason_code": "COPYRIGHT", "reason_note": "x", "confirm_track_id": str(self.track_id)}),
        ):
            with self.subTest(path=path):
                response = self.post(
                    "/api/admin/music/tracks/%d/%s" % (self.track_id, path), payload
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.body(response)["error_code"], "music_permission_denied")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_reading_the_catalog_is_gated_too(self):
        """`impact` and `audit` name who uploaded what and why it was removed."""
        self.as_account(MODERATOR_ACCOUNT_ID)
        for path in ("impact", "audit"):
            with self.subTest(path=path):
                response = self.client.get("/api/admin/music/tracks/%d/%s" % (self.track_id, path))
                self.assertEqual(response.status_code, 403)

    def test_a_deliberately_granted_permission_is_honoured(self):
        """The positive control for the two tests above.

        `analytics_viewer` has no `music.*` in its fallback set; the grant comes
        from a row in `admin_role_permissions`. Without this, "403 for everyone
        who is not the owner" would pass just as well if the grant lookup were
        broken and nothing but the owner short-circuit worked.
        """
        self.as_account(GRANTED_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "DUPLICATE"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "TAKEN_DOWN")

    def test_that_grant_does_not_leak_into_the_neighbouring_permissions(self):
        """`music.takedown` is not `music.purge`.

        The six permissions exist so the destructive one can be withheld from an
        account that is trusted to stop playback.
        """
        self.as_account(GRANTED_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id,
            {"reason_code": "DUPLICATE"},
        )
        self.assertEqual(response.status_code, 403)

    def test_the_request_body_cannot_nominate_an_identity(self):
        """The forgery attempt, spelled out.

        Every field a client could plausibly hope the server reads is sent at
        once, from an account with no admin link. If any of them were consulted
        this returns 200.
        """
        self.as_account(PLAIN_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {
                "reason_code": "OWNER_DECISION",
                "role": "owner",
                "is_owner": True,
                "admin_user_id": self.owner_admin_id,
                "account_user_id": OWNER_ACCOUNT_ID,
                "permissions": ["music.takedown", "music.purge"],
                "actor_role": "owner",
            },
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_a_moderator_cannot_escalate_by_naming_the_owner_in_the_body(self):
        """Same forgery, but from an account that *does* have an admin identity.

        This is the more dangerous shape: the caller is a real admin, so the
        lookup succeeds and only the permission check stands between them and
        the catalog.
        """
        self.as_account(MODERATOR_ACCOUNT_ID)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "OWNER_DECISION", "role": "owner", "admin_user_id": self.owner_admin_id},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")


class ReasonTests(MusicRouteTestCase):
    """A removal without a stated reason is not a decision anyone can review."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)

    def test_a_takedown_without_a_reason_code_is_refused(self):
        response = self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["error_code"], "music_reason_code_invalid")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_an_invented_reason_code_is_refused(self):
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "BECAUSE_I_SAID_SO"},
        )
        self.assertEqual(response.status_code, 400)

    def test_other_requires_a_note(self):
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "OTHER"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["error_code"], "music_reason_note_required")

    def test_the_reason_is_stored_on_the_track_and_in_the_trail(self):
        self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "LICENSING_EXPIRED", "reason_note": "Sync licence lapsed 2026-09-01."},
        )
        track = _track_row(self.track_id)
        self.assertEqual(track["takedown_reason_code"], "LICENSING_EXPIRED")
        self.assertEqual(track["takedown_reason_note"], "Sync licence lapsed 2026-09-01.")
        self.assertEqual(_audit_rows(self.track_id)[-1]["reason_code"], "LICENSING_EXPIRED")


class LifecycleTests(MusicRouteTestCase):
    """Takedown, restore, and the three-step road to destruction."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)

    def takedown(self, **extra):
        payload = {"reason_code": "COPYRIGHT"}
        payload.update(extra)
        return self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, payload)

    def test_takedown_writes_the_legacy_trio_as_well_as_the_new_state(self):
        """This is what makes the takedown *effective*.

        Around ten existing read paths -- search, trending, the artist page,
        reel attach, `music_service` -- filter on `active` / `safety_status` /
        `removed_at` and know nothing about `lifecycle_state`. Writing both in
        the same statement is what stops the track playing everywhere without
        editing every query, and it is why the two can never disagree.
        """
        self.takedown()
        track = _track_row(self.track_id)
        self.assertEqual(track["lifecycle_state"], "TAKEN_DOWN")
        self.assertEqual(track["active"], 0)
        self.assertEqual(track["approved_by_admin"], 0)
        self.assertEqual(track["safety_status"], "removed")
        self.assertTrue(track["removed_at"])
        self.assertEqual(track["removed_by_admin"], self.owner_admin_id)

    def test_restore_puts_every_one_of_those_columns_back(self):
        self.takedown()
        response = self.post(
            "/api/admin/music/tracks/%d/restore" % self.track_id, {"reason_code": "OWNER_DECISION"}
        )
        self.assertEqual(response.status_code, 200)
        track = _track_row(self.track_id)
        self.assertEqual(track["lifecycle_state"], "ACTIVE")
        self.assertEqual(track["active"], 1)
        self.assertEqual(track["approved_by_admin"], 1)
        self.assertEqual(track["safety_status"], "approved")
        self.assertEqual(track["removed_at"], "")

    def test_quarantine_is_reachable_and_is_a_stronger_state(self):
        response = self.takedown(quarantine="1", reason_code="MALWARE_OR_UNSAFE_FILE")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "QUARANTINED")

    def test_purge_is_not_reachable_directly_from_active(self):
        """Takedown and permanent deletion are different operations.

        A single mis-click must not be able to destroy an asset, so the only
        route into `PURGED` is from `PURGE_PENDING`.
        """
        self.grant_step_up()
        response = self.post(
            "/api/admin/music/tracks/%d/purge" % self.track_id,
            {"reason_code": "COPYRIGHT", "reason_note": "n/a", "confirm_track_id": str(self.track_id)},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.body(response)["error_code"], "music_transition_refused")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_schedule_then_cancel_returns_the_track_to_taken_down(self):
        self.takedown()
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGE_PENDING")
        self.assertTrue(_track_row(self.track_id)["purge_scheduled_at"])
        self.post(
            "/api/admin/music/tracks/%d/cancel-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        track = _track_row(self.track_id)
        self.assertEqual(track["lifecycle_state"], "TAKEN_DOWN")
        self.assertEqual(track["purge_scheduled_at"], "")

    def test_restore_rescues_a_track_from_purge_pending(self):
        """Scheduling destruction has to be undoable right up to the moment."""
        self.takedown()
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.post(
            "/api/admin/music/tracks/%d/restore" % self.track_id, {"reason_code": "OWNER_DECISION"}
        )
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_a_purged_track_cannot_be_restored(self):
        """`PURGED` is terminal because the bytes are gone.

        Answering 200 here would hand the owner back a row pointing at an object
        that no longer exists -- a track that looks restored and plays nothing.
        """
        self.purge_fully()
        response = self.post(
            "/api/admin/music/tracks/%d/restore" % self.track_id, {"reason_code": "OWNER_DECISION"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGED")

    def grant_step_up(self):
        response = self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        self.assertEqual(response.status_code, 200)
        return response

    def purge_fully(self):
        self.takedown()
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.grant_step_up()
        response = self.post(
            "/api/admin/music/tracks/%d/purge" % self.track_id,
            {
                "reason_code": "COPYRIGHT",
                "reason_note": "Confirmed infringing, label notice 44812.",
                "confirm_track_id": str(self.track_id),
            },
        )
        self.assertEqual(response.status_code, 200)
        return response


class IdempotencyTests(MusicRouteTestCase):
    """A retry is not a second event."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)

    def test_repeating_a_takedown_answers_changed_false_and_writes_no_second_row(self):
        """The client that never saw the first response.

        Answering 409 would make the phone show a failure for work that
        succeeded; writing a second audit row would put an event in the trail
        that did not happen.
        """
        first = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.assertTrue(self.body(first)["changed"])
        second = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.assertEqual(second.status_code, 200)
        self.assertFalse(self.body(second)["changed"])
        self.assertEqual(len(_audit_rows(self.track_id)), 1)

    def test_a_stale_expected_state_is_a_conflict_and_changes_nothing(self):
        """Two moderators, two screens, one of them out of date."""
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        response = self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id,
            {"reason_code": "COPYRIGHT", "expected_state": "ACTIVE"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.body(response)["error_code"], "music_state_conflict")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "TAKEN_DOWN")
        self.assertEqual(len(_audit_rows(self.track_id)), 1)

    def test_a_matching_expected_state_proceeds(self):
        """Positive control: the 409 above is about staleness, not about the
        field's presence."""
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        response = self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id,
            {"reason_code": "COPYRIGHT", "expected_state": "TAKEN_DOWN"},
        )
        self.assertEqual(response.status_code, 200)


class BulkTests(MusicRouteTestCase):
    """Bulk is the same code path as single, or the two will drift."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)
        self.second_id = _seed_track(title="Second Controlled Track")

    def test_a_bulk_takedown_removes_every_named_track(self):
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "COPYRIGHT", "track_ids": [self.second_id]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "TAKEN_DOWN")
        self.assertEqual(_track_row(self.second_id)["lifecycle_state"], "TAKEN_DOWN")

    def test_one_bad_id_rolls_the_whole_batch_back(self):
        """Half of a bulk takedown is the worst outcome available.

        The owner would have no way to tell which half applied, and the response
        that would have told them is the one that errored.
        """
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "COPYRIGHT", "track_ids": [self.second_id, 987654321]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")
        self.assertEqual(_track_row(self.second_id)["lifecycle_state"], "ACTIVE")
        self.assertEqual(_audit_rows(self.track_id), [])
        self.assertEqual(_audit_rows(self.second_id), [])

    def test_purge_refuses_to_act_in_bulk(self):
        """The one irreversible action is deliberately one track at a time."""
        for track_id in (self.track_id, self.second_id):
            self.post("/api/admin/music/tracks/%d/takedown" % track_id, {"reason_code": "COPYRIGHT"})
            self.post(
                "/api/admin/music/tracks/%d/schedule-purge" % track_id, {"reason_code": "COPYRIGHT"}
            )
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        response = self.post(
            "/api/admin/music/tracks/%d/purge" % self.track_id,
            {
                "reason_code": "COPYRIGHT",
                "reason_note": "batch attempt",
                "confirm_track_id": str(self.track_id),
                "track_ids": [self.second_id],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["error_code"], "music_purge_confirmation_required")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGE_PENDING")
        self.assertEqual(_track_row(self.second_id)["lifecycle_state"], "PURGE_PENDING")


class StepUpTests(MusicRouteTestCase):
    """Re-prove the password before destroying anything."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )

    def purge(self, **extra):
        payload = {
            "reason_code": "COPYRIGHT",
            "reason_note": "Label notice.",
            "confirm_track_id": str(self.track_id),
        }
        payload.update(extra)
        return self.post("/api/admin/music/tracks/%d/purge" % self.track_id, payload)

    def test_purge_without_a_step_up_is_refused(self):
        response = self.purge()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["error_code"], "music_step_up_required")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGE_PENDING")

    def test_a_wrong_password_grants_nothing(self):
        response = self.post("/api/admin/music/step-up", {"password": "not-the-password"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["error_code"], "music_step_up_failed")
        self.assertEqual(self.purge().status_code, 403)

    def test_an_expired_step_up_grants_nothing(self):
        """Five minutes is the point of the grant.

        Without the expiry check a single step-up taken once would authorize
        every purge that account ever makes.
        """
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        stale = (datetime.utcnow() - timedelta(seconds=60)).isoformat(timespec="seconds")
        conn = _connect()
        conn.execute(
            "UPDATE music_owner_stepups SET expires_at=? WHERE admin_user_id=?",
            (stale, self.owner_admin_id),
        )
        conn.commit()
        conn.close()
        response = self.purge()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["error_code"], "music_step_up_required")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGE_PENDING")

    def test_another_admins_step_up_does_not_authorize_this_one(self):
        """The grant is scoped to the admin who proved the password."""
        conn = _connect()
        now = datetime.utcnow()
        conn.execute(
            "INSERT INTO music_owner_stepups (admin_user_id, granted_at, expires_at, request_id, consumed_at) "
            "VALUES (?, ?, ?, 'other', '')",
            (
                self.moderator_admin_id,
                now.isoformat(timespec="seconds"),
                (now + timedelta(seconds=300)).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.purge().status_code, 403)

    def test_a_step_up_is_spent_by_the_purge_it_authorizes(self):
        """One proof, one destruction.

        A grant that survived its purge would leave a five-minute window in which
        any further purge needed no password at all.
        """
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        self.assertEqual(self.purge().status_code, 200)
        second_id = _seed_track(title="Second track for the spent grant")
        self.post("/api/admin/music/tracks/%d/takedown" % second_id, {"reason_code": "COPYRIGHT"})
        self.post("/api/admin/music/tracks/%d/schedule-purge" % second_id, {"reason_code": "COPYRIGHT"})
        response = self.post(
            "/api/admin/music/tracks/%d/purge" % second_id,
            {"reason_code": "COPYRIGHT", "reason_note": "n", "confirm_track_id": str(second_id)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.body(response)["error_code"], "music_step_up_required")

    def test_purge_requires_the_track_id_echoed_back(self):
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        response = self.purge(confirm_track_id=str(self.track_id + 1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["error_code"], "music_purge_confirmation_required")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "PURGE_PENDING")

    def test_purge_requires_a_note_even_when_the_code_would_not(self):
        """`COPYRIGHT` carries its own meaning; the irreversible act still wants
        a sentence a human wrote."""
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        response = self.purge(reason_note="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.body(response)["error_code"], "music_reason_note_required")


class LegalHoldTests(MusicRouteTestCase):
    """A hold outranks the owner's own permission."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)
        self.held_id = _seed_track(title="Held track", legal_hold=1)

    def test_a_held_track_can_still_be_taken_down(self):
        """A hold preserves evidence; it does not force the song to keep playing."""
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.held_id, {"reason_code": "COPYRIGHT"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(_track_row(self.held_id)["lifecycle_state"], "TAKEN_DOWN")

    def test_a_held_track_cannot_be_purged_even_with_a_valid_step_up(self):
        self.post("/api/admin/music/tracks/%d/takedown" % self.held_id, {"reason_code": "COPYRIGHT"})
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.held_id, {"reason_code": "COPYRIGHT"}
        )
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        response = self.post(
            "/api/admin/music/tracks/%d/purge" % self.held_id,
            {"reason_code": "COPYRIGHT", "reason_note": "n", "confirm_track_id": str(self.held_id)},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.body(response)["error_code"], "music_legal_hold")
        self.assertEqual(_track_row(self.held_id)["lifecycle_state"], "PURGE_PENDING")


class AuditTests(MusicRouteTestCase):
    """The trail has to outlive the asset and read as a sequence of decisions."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)

    def test_every_mission_field_is_populated_on_a_takedown(self):
        self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "COPYRIGHT", "reason_note": "Label notice 44812."},
        )
        row = _audit_rows(self.track_id)[-1]
        self.assertTrue(row["action_id"])
        self.assertEqual(row["track_id"], self.track_id)
        self.assertEqual(row["action"], "takedown")
        self.assertEqual(row["previous_state"], "ACTIVE")
        self.assertEqual(row["new_state"], "TAKEN_DOWN")
        self.assertEqual(row["actor_user_id"], self.owner_admin_id)
        self.assertEqual(row["actor_role"], "owner")
        self.assertEqual(row["reason_code"], "COPYRIGHT")
        self.assertEqual(row["reason_note"], "Label notice 44812.")
        self.assertIsNotNone(row["affected_reference_count"])
        self.assertTrue(row["request_id"])
        self.assertTrue(row["created_at"])
        self.assertIsNone(row["restored_at"])

    def test_a_supplied_request_id_is_carried_into_the_trail(self):
        """The client's own id, so a retry can be recognised as the same intent."""
        self.client.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            data=json.dumps({"reason_code": "COPYRIGHT"}),
            content_type="application/json",
            headers={"X-Request-Id": "owner-phone-7f3a"},
        )
        self.assertEqual(_audit_rows(self.track_id)[-1]["request_id"], "owner-phone-7f3a")

    def test_a_restore_links_back_to_the_takedown_it_undid(self):
        """The trail reads as a pair, not as two unrelated events."""
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        takedown_id = _audit_rows(self.track_id)[-1]["action_id"]
        self.post(
            "/api/admin/music/tracks/%d/restore" % self.track_id, {"reason_code": "OWNER_DECISION"}
        )
        rows = _audit_rows(self.track_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["action"], "restore")
        self.assertEqual(rows[1]["related_action_id"], takedown_id)
        self.assertTrue(rows[0]["restored_at"], "the takedown row should be stamped closed")

    def test_the_restore_stamp_is_the_only_mutation_of_an_existing_row(self):
        """Append-only, asserted rather than asserted-about.

        Everything else -- the action, who did it, why, what it changed -- is
        compared field by field before and after a later transition, so a writer
        that started editing history would fail here rather than in review.
        """
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        before = _audit_rows(self.track_id)[0]
        self.post(
            "/api/admin/music/tracks/%d/restore" % self.track_id, {"reason_code": "OWNER_DECISION"}
        )
        after = _audit_rows(self.track_id)[0]
        mutated = {key for key in before if before[key] != after[key]}
        self.assertEqual(mutated, {"restored_at"})

    def test_the_trail_survives_the_purge_that_destroyed_the_asset(self):
        """A purge that cascaded its own audit away would destroy the only
        record that it happened."""
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        self.post(
            "/api/admin/music/tracks/%d/schedule-purge" % self.track_id, {"reason_code": "COPYRIGHT"}
        )
        self.post("/api/admin/music/step-up", {"password": OWNER_PASSWORD})
        self.post(
            "/api/admin/music/tracks/%d/purge" % self.track_id,
            {
                "reason_code": "COPYRIGHT",
                "reason_note": "Confirmed infringing.",
                "confirm_track_id": str(self.track_id),
            },
        )
        rows = _audit_rows(self.track_id)
        self.assertEqual([row["action"] for row in rows], ["takedown", "schedule_purge", "purge"])
        track = _track_row(self.track_id)
        self.assertEqual(track["lifecycle_state"], "PURGED")
        self.assertEqual(track["audio_url"], "")
        self.assertTrue(track["purged_at"])

    def test_the_audit_endpoint_returns_the_same_trail(self):
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        response = self.client.get("/api/admin/music/tracks/%d/audit" % self.track_id)
        self.assertEqual(response.status_code, 200)
        entries = self.body(response)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], "takedown")

    def test_a_denied_attempt_is_recorded_against_the_admin_who_made_it(self):
        """A moderator probing the catalog should leave a trace."""
        conn = _connect()
        conn.execute("DELETE FROM admin_audit_logs")
        conn.commit()
        conn.close()
        self.as_account(MODERATOR_ACCOUNT_ID)
        self.post("/api/admin/music/tracks/%d/takedown" % self.track_id, {"reason_code": "COPYRIGHT"})
        conn = _connect()
        rows = conn.execute(
            "SELECT * FROM admin_audit_logs WHERE action='music_permission_denied'"
        ).fetchall()
        conn.close()
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["admin_user_id"], self.moderator_admin_id)


class ImpactTests(MusicRouteTestCase):
    """Look before you remove."""

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)

    def test_impact_counts_references_across_all_three_tables(self):
        """`pulse_content_music.audio_track_id` is TEXT while
        `pulse_audio_tracks.id` is INTEGER.

        Without a CAST on both sides that join silently counts zero on Postgres,
        and the owner is told a takedown is harmless when it is not.
        """
        conn = _connect()
        conn.execute(
            "INSERT INTO pulse_reel_audio (reel_id, audio_track_id, created_at) VALUES (1, ?, '')",
            (self.track_id,),
        )
        conn.execute(
            "INSERT INTO pulse_content_music (content_type, content_id, audio_track_id, created_at) "
            "VALUES ('video', 5, ?, '')",
            (str(self.track_id),),
        )
        conn.commit()
        conn.close()
        response = self.client.get("/api/admin/music/tracks/%d/impact" % self.track_id)
        self.assertEqual(response.status_code, 200)
        references = self.body(response)["references"]
        self.assertEqual(references["reels"], 1)
        self.assertEqual(references["content"], 1)
        self.assertEqual(references["total"], 2)

    def test_impact_states_the_cdn_caveat_rather_than_implying_it(self):
        """The bucket is public and the object was marked immutable for a year.

        A takedown stops the server handing the url out; it does not reach a
        client that already has it. Saying so in the response is what lets the
        owner choose quarantine for a copyright or malware case.
        """
        body = self.body(self.client.get("/api/admin/music/tracks/%d/impact" % self.track_id))
        self.assertTrue(body["cached_copies_remain_until_purge"])

    def test_impact_offers_the_reason_codes_so_the_client_need_not_hardcode_them(self):
        body = self.body(self.client.get("/api/admin/music/tracks/%d/impact" % self.track_id))
        self.assertEqual(body["reason_codes"], list(music_authority.REASON_CODES))

    def test_impact_on_an_unknown_track_is_a_clean_404(self):
        response = self.client.get("/api/admin/music/tracks/987654321/impact")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.body(response)["error_code"], "music_track_not_found")


class ReferenceCountFailureTests(MusicRouteTestCase):
    """A count that cannot be read must not arrive as the number 0.

    `music_reference_counts` used to wrap each COUNT in `except Exception:
    value = 0`. The owner-facing blast radius is the number a person reads
    immediately before an irreversible purge, so the failure mode was: the query
    breaks, the panel says "0 attached", and the owner reads that as permission
    to proceed while the content is still out there.

    The failure injected here is a *query* failure on a table that exists -- a
    column that is not there -- rather than a dropped table. That is the shape
    the real hazard takes (the `CAST` comparison spans a TEXT/INTEGER mismatch,
    and on Postgres the first failed statement aborts the transaction), and it
    keeps the test honest about what is being simulated.

    Each test seeds a real reference first, so a reported 0 is demonstrably a
    lie rather than merely unproven.
    """

    def setUp(self):
        super().setUp()
        self.as_account(OWNER_ACCOUNT_ID)
        self._seed_one_real_reel_reference()

    def _seed_one_real_reel_reference(self):
        conn = _connect()
        conn.execute("DELETE FROM pulse_reel_audio WHERE audio_track_id=?", (self.track_id,))
        conn.execute(
            "INSERT INTO pulse_reel_audio (reel_id, audio_track_id, created_at) VALUES (1, ?, '')",
            (self.track_id,),
        )
        conn.commit()
        conn.close()

    def _broken_reference_tables(self):
        """The real tables, with the reels count aimed at a column that is absent."""
        return (
            ("pulse_reel_audio", "column_that_does_not_exist", "reels"),
            ("pulse_content_music", "audio_track_id", "content"),
            ("pulse_status_music", "audio_track_id", "statuses"),
        )

    def test_the_injected_failure_really_breaks_the_query(self):
        """Control: without this, every assertion below could pass vacuously."""
        conn = _connect()
        cur = conn.cursor()
        with patch.object(bot, "MUSIC_REFERENCE_TABLES", self._broken_reference_tables()):
            with self.assertRaises(bot.MusicReferenceCountUnavailable):
                bot.music_reference_counts(cur, self.track_id)
        conn.close()

    def test_a_genuine_zero_is_still_reported_as_zero(self):
        """The positive control: only *failure* is special, not emptiness."""
        conn = _connect()
        conn.execute("DELETE FROM pulse_reel_audio WHERE audio_track_id=?", (self.track_id,))
        conn.commit()
        conn.close()
        response = self.client.get("/api/admin/music/tracks/%d/impact" % self.track_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.body(response)["references"]["total"], 0)

    def test_a_failed_reference_query_does_not_reach_the_owner_as_zero(self):
        with patch.object(bot, "MUSIC_REFERENCE_TABLES", self._broken_reference_tables()):
            response = self.client.get("/api/admin/music/tracks/%d/impact" % self.track_id)
        self.assertNotEqual(
            response.status_code,
            200,
            "a broken count answered 200 -- the owner is being shown a number nobody read",
        )
        body = self.body(response)
        self.assertEqual(body.get("error_code"), "music_reference_count_unavailable")
        # The refusal must not smuggle the fabricated number in beside itself.
        self.assertNotIn("references", body)

    def test_a_failed_reference_query_does_not_record_a_takedown_as_harmless(self):
        """`affected_reference_count` lands in an immutable trail.

        A takedown recorded as touching 0 items, when the count behind that 0
        failed, is a permanent false record of how much was affected.
        """
        with patch.object(bot, "MUSIC_REFERENCE_TABLES", self._broken_reference_tables()):
            response = self.post(
                "/api/admin/music/tracks/%d/takedown" % self.track_id,
                {"reason_code": "OWNER_DECISION"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.body(response)["error_code"], "music_reference_count_unavailable")
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")
        self.assertEqual(
            _audit_rows(self.track_id),
            [],
            "the transition rolled forward on a blast radius nobody could read",
        )


class CapabilityTests(MusicRouteTestCase):
    """`/api/admin/music/capabilities` -- the one music route a normal user may call.

    It exists so the phone never decides for itself whether to draw a takedown
    button. The alternative the app would otherwise reach for is the profile's
    role string, which is a security decision made in a place the server does
    not control.

    Two properties have to hold together and pull in opposite directions:

    * it must answer **200** to an ordinary signed-in user, saying "you hold
      nothing" -- otherwise the client cannot tell "not an owner" apart from a
      dropped connection, and a real owner silently loses the surface whenever
      the network blips;
    * it must grant **nothing**, so that a client which ignores the answer and
      calls a mutation anyway is refused exactly as before.

    The second is the one worth distrusting, so it is asserted by calling a real
    mutation immediately after a maximally-encouraging capability response.
    """

    def test_the_owner_is_told_they_hold_every_music_permission(self):
        self.as_account(OWNER_ACCOUNT_ID)
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertTrue(body["music_authority"])
        for permission in sorted(music_authority.MUSIC_PERMISSIONS):
            self.assertTrue(body["permissions"][permission], permission)

    def test_an_ordinary_user_gets_a_200_saying_they_hold_nothing(self):
        """Not a 403. The client needs an answer it can render, not an error."""
        self.as_account(PLAIN_ACCOUNT_ID)
        response = self.client.get("/api/admin/music/capabilities")
        self.assertEqual(response.status_code, 200)
        body = self.body(response)
        self.assertFalse(body["music_authority"])
        self.assertEqual(set(body["permissions"].values()), {False})

    def test_an_unauthenticated_caller_is_still_refused(self):
        """The 200-for-everyone rule stops at "signed in"."""
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.get("/api/admin/music/capabilities")
        self.assertEqual(response.status_code, 401)

    def test_a_partial_grant_reports_exactly_what_it_is(self):
        """One granted permission, five not -- reported per permission, not per role.

        The `analytics_viewer` fixture holds `music.takedown` alone. A response
        that collapsed this to a single "is owner" boolean would either hide the
        grant or promote the account to full authority in the UI.
        """
        self.as_account(GRANTED_ACCOUNT_ID)
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertTrue(body["music_authority"])
        self.assertTrue(body["permissions"]["music.takedown"])
        self.assertFalse(body["permissions"]["music.purge"])
        self.assertFalse(body["permissions"]["music.restore"])

    def test_pulse_moderate_alone_reports_no_music_authority(self):
        """The old music review route's permission grants nothing here."""
        self.as_account(MODERATOR_ACCOUNT_ID)
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertFalse(body["music_authority"])
        self.assertEqual(set(body["permissions"].values()), {False})

    def test_every_permission_is_named_even_the_false_ones(self):
        """A missing key and a false key must not be the same wire answer.

        A client reads both as "no", so an endpoint that omitted the denials
        would look identical to one built against a stale, shorter permission
        list -- hiding a real grant with no error anywhere.
        """
        self.as_account(PLAIN_ACCOUNT_ID)
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertEqual(set(body["permissions"]), set(music_authority.MUSIC_PERMISSIONS))

    def test_the_capability_answer_grants_nothing(self):
        """The property that makes it safe to serve this to everyone.

        Asking, being told "no", and then calling the mutation anyway is exactly
        what a tampered client does. The refusal must come from the mutation's
        own check, not from the client having believed the capability response.
        """
        self.as_account(PLAIN_ACCOUNT_ID)
        self.assertEqual(self.client.get("/api/admin/music/capabilities").status_code, 200)
        response = self.post(
            "/api/admin/music/tracks/%d/takedown" % self.track_id,
            {"reason_code": "OWNER_DECISION"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(_track_row(self.track_id)["lifecycle_state"], "ACTIVE")

    def test_a_body_claiming_authority_does_not_change_the_answer(self):
        """Nothing the request sends selects an identity -- including here."""
        self.as_account(PLAIN_ACCOUNT_ID)
        response = self.client.get(
            "/api/admin/music/capabilities?role=owner&is_owner=1&admin_user_id=%d"
            % self.owner_admin_id
        )
        self.assertFalse(self.body(response)["music_authority"])

    def test_the_web_admin_session_leg_works_too(self):
        self.as_owner_session()
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertTrue(body["music_authority"])

    def test_the_client_is_given_the_vocabulary_it_would_otherwise_hardcode(self):
        """States and reason codes ship with the capability answer.

        A client with its own copy of these lists drifts the moment one is added
        server-side, and the drift shows up as a takedown the owner cannot file
        rather than as an error.
        """
        self.as_account(OWNER_ACCOUNT_ID)
        body = self.body(self.client.get("/api/admin/music/capabilities"))
        self.assertEqual(body["reason_codes"], list(music_authority.REASON_CODES))
        self.assertEqual(body["states"], list(music_authority.LIFECYCLE_STATES))
        self.assertEqual(body["step_up_ttl_seconds"], music_authority.STEP_UP_TTL_SECONDS)


if __name__ == "__main__":
    unittest.main()
