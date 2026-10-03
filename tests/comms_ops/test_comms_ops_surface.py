"""The owner can see whether calls and chats are happening -- and nothing else.

Two properties are being held at once here, and they pull in opposite
directions.

The first is that the page is *useful*: a call in progress appears, a call that
ended does not appear as if it were still running, a call that failed is counted
as a failure and not absorbed into a green number, and a provider that broke
reads broken. A dashboard that is merely safe is the bug this mission was opened
about -- the owner had one, and could not answer "are calls happening".

The second is that it is *not surveillance*. The page is built over
``comm_v2_messages``, which holds the text of every private message on the
platform, and over ``communication_calls``, which holds room names that are
live join handles. Neither may reach the browser. The privacy tests here assert
over the rendered HTML and the JSON feed rather than over the snapshot dict,
because the snapshot is an implementation detail and the response is the thing
an admin actually receives.

The third property is the quiet one, and it is why several tests look like they
are testing nothing: a *failed query must not read as zero activity*. "No calls
in the last hour" and "the calls table could not be read" are opposite
operational facts, and a dashboard that renders both as ``0`` is worse than no
dashboard, because it is confidently wrong. Every section is therefore allowed
to fail on its own, and a failed section says so where its numbers would be.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="comms_ops_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ.setdefault("FLASK_SECRET_KEY", "comms-ops-tests")

import bot  # noqa: E402
from services import db as db_service  # noqa: E402
from services import pulsesoc_comms_ops as comms_ops  # noqa: E402
from services import security_guard  # noqa: E402

PAGE = "/admin/communications"
FEED = "/admin/communications/snapshot.json"

#: Planted in a message body. If this string ever reaches a response, the page
#: has become a window into private conversations.
SECRET_BODY = "PRIVATE-MESSAGE-BODY-MUST-NEVER-REACH-AN-ADMIN-PAGE"

#: Planted in ``room_name``, which is a live join handle for an in-progress
#: call, and in a push token column. Both are credential-shaped.
SECRET_ROOM = "joinable-room-handle-7f3a9c"
SECRET_TOKEN = "ExponentPushToken[MUST-NEVER-RENDER]"

#: Planted in ``device_info_json`` on a quality report. The quality panel reads
#: that table, so the device fingerprint sitting in it is now reachable code.
SECRET_DEVICE = '{"model":"iPhone15,2","identifier":"DEVICE-FINGERPRINT-MUST-NOT-RENDER"}'


#: Emptied before every test. This list used to be written out inline and it
#: rotted the moment the snapshot learned to read a new table: the quality
#: reports table was absent, so rows planted by one test were still there for
#: the next one, and a test asserting "no reports" was reading six of them. The
#: guard in TestTheHarnessIsHonest keeps this in step with the module.
RESET_TABLES = (
    "communication_calls",
    "communication_call_participants",
    "communication_call_quality_reports",
    "comm_v2_messages",
    "comm_v2_conversations",
    "notification_delivery_jobs",
    "admin_audit_logs",
)


def _stamp(seconds_ago: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


class CommsOpsCase(unittest.TestCase):
    """Base: a fresh database, the real comms DDL, and one owner admin."""

    def setUp(self):
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        bot.app.config["TESTING"] = True
        self.client = bot.app.test_client()
        comms_ops.reset_snapshot_cache()

        conn = db_service.connect()
        cur = conn.cursor()
        # The comms schemas are created lazily by the live code paths, not by
        # init_db(), so a read-only snapshot cannot conjure them. Build them
        # with the same functions production uses -- a hand-written CREATE TABLE
        # here would let a column rename pass this suite and break the page.
        from pulse_communications_v2 import models as commv2_models
        from services import pulsesoc_communications_engine as engine
        from services import pulsesoc_notification_system as notif

        engine.ensure_schema(cur)
        commv2_models.ensure_schema(cur)
        conn.commit()
        notif.ensure_schema(conn)
        conn.commit()

        for table in RESET_TABLES:
            try:
                cur.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
        conn.close()

        self.owner_id = self._make_admin("owner")

    def _make_admin(self, role: str) -> int:
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO admin_users (full_name, email, password_hash, role, status, "
            "must_change_password, created_at) VALUES (?, ?, ?, ?, 'active', 0, ?)",
            (
                f"{role} tester",
                f"{role}-{secrets.token_hex(6)}@example.com",
                bot.generate_password_hash("OpsPass!12345"),
                role,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        cur.execute("SELECT id FROM admin_users ORDER BY id DESC LIMIT 1")
        admin_id = dict(cur.fetchone())["id"]
        conn.close()
        return admin_id

    def _sign_in(self, admin_id: int) -> None:
        with self.client.session_transaction() as sess:
            now = datetime.now().isoformat()
            sess["admin_user_id"] = admin_id
            sess["admin_session_issued_at"] = now
            sess["admin_session_last_seen"] = now

    def _sign_out(self) -> None:
        with self.client.session_transaction() as sess:
            sess.clear()

    def _page(self, admin_id: int | None = None):
        if admin_id is None:
            admin_id = self.owner_id
        self._sign_in(admin_id)
        comms_ops.reset_snapshot_cache()
        return self.client.get(PAGE)

    def _feed(self, admin_id: int | None = None):
        if admin_id is None:
            admin_id = self.owner_id
        self._sign_in(admin_id)
        comms_ops.reset_snapshot_cache()
        return self.client.get(FEED)

    # -- fixture writers ---------------------------------------------------

    def _call(self, public_id, status, *, started=120, answered=None, duration=0,
              end_reason="", call_type="audio", scope="direct", provider="agora",
              room=None):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO communication_calls (public_id, call_type, call_scope, provider, status, "
            "started_at, answered_at, created_at, end_reason, duration_seconds, created_by_user_id, "
            "room_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (public_id, call_type, scope, provider, status, _stamp(started),
             _stamp(answered) if answered is not None else None, _stamp(started),
             end_reason, duration, 1, room or SECRET_ROOM),
        )
        conn.commit()
        conn.close()

    def _conversation_with_messages(self, count=3, kind="direct"):
        conn = db_service.connect()
        cur = conn.cursor()
        public_id = f"conv-{secrets.token_hex(4)}"
        cur.execute(
            "INSERT INTO comm_v2_conversations (public_id, conversation_type, member_count, "
            "last_message_at, last_activity_at, status, created_at) VALUES (?, ?, 2, ?, ?, 'active', ?)",
            (public_id, kind, _stamp(60), _stamp(60), _stamp(5000)),
        )
        conn.commit()
        cur.execute("SELECT id FROM comm_v2_conversations WHERE public_id=?", (public_id,))
        conversation_id = dict(cur.fetchone())["id"]
        for index in range(count):
            cur.execute(
                "INSERT INTO comm_v2_messages (public_id, conversation_id, sender_user_id, body, "
                "message_type, delivery_status, created_at) VALUES (?, ?, 1, ?, 'text', 'sent', ?)",
                (f"msg-{secrets.token_hex(4)}", conversation_id, SECRET_BODY, _stamp(60 + index)),
            )
        conn.commit()
        conn.close()
        return conversation_id

    def _delivery(self, channel, status, reason="", provider="push_router", count=1):
        conn = db_service.connect()
        cur = conn.cursor()
        for _ in range(count):
            cur.execute(
                "INSERT INTO notification_delivery_jobs (notification_id, user_id, recipient_user_id, "
                "channel, provider, status, failure_reason, created_at, updated_at) "
                "VALUES (1, 1, 1, ?, ?, ?, ?, ?, ?)",
                (channel, provider, status, reason, _stamp(200), _stamp(200)),
            )
        conn.commit()
        conn.close()

    def _quality(self, call_id, *, latency=0, jitter=0, loss=0.0, score=0.0,
                 network="wifi", count=1):
        conn = db_service.connect()
        cur = conn.cursor()
        for _ in range(count):
            cur.execute(
                "INSERT INTO communication_call_quality_reports (call_id, user_id, latency_ms, "
                "jitter_ms, packet_loss, network_type, device_info_json, quality_score, created_at) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?)",
                (int(call_id), latency, jitter, loss, network, SECRET_DEVICE, score, _stamp(300)),
            )
        conn.commit()
        conn.close()

    def _call_id(self, public_id):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT id FROM communication_calls WHERE public_id=?", (public_id,))
        call_id = dict(cur.fetchone())["id"]
        conn.close()
        return call_id


class TestWhoMayLook(CommsOpsCase):
    """§21. Not "the URL starts with /admin"."""

    def test_anonymous_is_sent_to_the_login_page(self):
        self._sign_out()
        response = self.client.get(PAGE)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.headers.get("Location", ""))

    def test_anonymous_feed_is_401_not_a_login_redirect(self):
        self._sign_out()
        response = self.client.get(FEED)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json().get("ok"))

    def test_an_admin_without_system_view_is_refused_the_page(self):
        # pulse_moderator is a real role in ROLE_FALLBACK_PERMISSIONS and it
        # does not carry system.view. Being signed in is not authorisation.
        moderator = self._make_admin("pulse_moderator")
        response = self._page(moderator)
        self.assertEqual(response.status_code, 403)

    def test_an_admin_without_system_view_is_refused_the_feed(self):
        moderator = self._make_admin("pulse_moderator")
        response = self._feed(moderator)
        self.assertEqual(response.status_code, 403)

    def test_a_refusal_is_written_to_the_audit_log(self):
        # §23: which admin tried what, when.
        moderator = self._make_admin("pulse_moderator")
        self._page(moderator)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) AS n FROM admin_audit_logs WHERE admin_user_id=? AND action=?",
            (moderator, "admin_permission_denied"),
        )
        self.assertGreaterEqual(dict(cur.fetchone())["n"], 1)
        conn.close()

    def test_an_admin_with_system_view_gets_the_page(self):
        analyst = self._make_admin("analyst")  # carries system.view
        response = self._page(analyst)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Communications", response.get_data(as_text=True))

    def test_the_owner_gets_the_page(self):
        self.assertEqual(self._page().status_code, 200)


class TestCallsAreVisible(CommsOpsCase):
    """§6, §7, §9. The question the owner actually asked."""

    def test_a_call_in_progress_appears_in_the_active_list(self):
        self._call("call-live-0001", "connected", answered=110, duration=0)
        body = self._page().get_data(as_text=True)
        self.assertIn("call-liv", body)
        feed = self._feed().get_json()
        self.assertEqual(feed["calls"]["active_count"], 1)

    def test_an_ended_call_is_not_counted_as_active(self):
        self._call("call-done-0002", "ended", answered=890, duration=42, end_reason="native_hangup")
        feed = self._feed().get_json()
        self.assertEqual(feed["calls"]["active_count"], 0)
        self.assertEqual(feed["calls"]["active"], [])
        # But it is still counted as having happened.
        self.assertEqual(feed["calls"]["windows"]["24h"]["started"], 1)

    def test_a_failed_call_is_reported_as_a_failure_not_as_healthy(self):
        self._call("call-tokn-0003", "failed", end_reason="agora_token_failed")
        feed = self._feed().get_json()
        reasons = {row["reason"]: row for row in feed["calls"]["failures"]}
        self.assertIn("agora_token_failed", reasons)
        self.assertTrue(reasons["agora_token_failed"]["classified"])
        self.assertEqual(feed["calls"]["windows"]["24h"]["unsuccessful"], 1)
        states = {p["key"]: p["state"] for p in feed["providers"]["providers"]}
        self.assertEqual(states["agora"], "failed")
        self.assertNotEqual(feed["state"], "healthy")

    def test_a_deliberate_hangup_is_not_a_failure(self):
        # The distinction the whole failure panel rests on. If a normal hangup
        # counted, every healthy platform would read as broken.
        self._call("call-bye-0004", "ended", answered=890, duration=61, end_reason="native_hangup")
        feed = self._feed().get_json()
        self.assertEqual([r["reason"] for r in feed["calls"]["failures"]], [])
        self.assertEqual(feed["calls"]["windows"]["24h"]["unsuccessful"], 0)

    def test_an_unknown_end_reason_is_shown_unclassified_rather_than_guessed(self):
        self._call("call-odd-0005", "failed", end_reason="something_nobody_has_mapped")
        feed = self._feed().get_json()
        rows = {row["reason"]: row for row in feed["calls"]["failures"]}
        self.assertIn("something_nobody_has_mapped", rows)
        self.assertFalse(rows["something_nobody_has_mapped"]["classified"])

    def test_every_required_window_is_present_and_bounded(self):
        # §5. 15m/1h/24h, plus 7d. A call outside a window must not appear in it.
        self._call("call-old-0006", "ended", started=4000, answered=3990, duration=30,
                   end_reason="native_hangup")
        feed = self._feed().get_json()
        for label in ("15m", "1h", "24h"):
            self.assertIn(label, feed["calls"]["windows"])
        self.assertEqual(feed["calls"]["windows"]["15m"]["started"], 0)
        self.assertEqual(feed["calls"]["windows"]["24h"]["started"], 1)
        body = self._page().get_data(as_text=True)
        for label in ("15m", "1h", "24h", "7d"):
            self.assertIn(f">{label}<", body)

    def test_zero_activity_says_so_and_does_not_claim_a_recent_call(self):
        # §28's zero-activity state. "never" is the honest answer, and the page
        # has to name which statuses it counted so the number is checkable.
        body = self._page().get_data(as_text=True)
        self.assertIn("No call is in an active state right now", body)
        self.assertIn("never", body)
        for status in comms_ops.ACTIVE_CALL_STATUSES:
            self.assertIn(status, body)


class TestChatIsMetadataOnly(CommsOpsCase):
    """§10, §11, §12."""

    def test_conversation_and_message_counts_are_real(self):
        self._conversation_with_messages(count=4)
        feed = self._feed().get_json()
        self.assertEqual(feed["chat"]["windows"]["1h"]["messages"], 4)
        self.assertEqual(feed["chat"]["windows"]["1h"]["active_conversations"], 1)
        self.assertTrue(feed["chat"]["last_message_at"])

    def test_there_is_no_message_list_and_no_body_anywhere(self):
        self._conversation_with_messages(count=3)
        body = self._page().get_data(as_text=True)
        self.assertNotIn(SECRET_BODY, body)
        self.assertIn("no message bodies", body)
        self.assertNotIn(SECRET_BODY, self._feed().get_data(as_text=True))

    def test_unmeasurable_per_message_delivery_is_admitted_not_faked(self):
        # §14: a funnel stage the schema cannot answer gets a note, not a zero
        # that would read as "no messages failed".
        self._conversation_with_messages(count=2)
        feed = self._feed().get_json()
        self.assertFalse(feed["chat"]["message_delivery"]["measurable"])
        self.assertTrue(feed["chat"]["message_delivery"].get("note"))


class TestDeliveryOutcomes(CommsOpsCase):
    """§13 and the distinction a provider's health depends on."""

    def test_a_failed_send_surfaces_with_the_pipelines_own_reason(self):
        self._delivery("email", "config_missing", "BREVO_API_KEY missing", provider="brevo_email", count=2)
        feed = self._feed().get_json()
        self.assertEqual(feed["delivery"]["windows"]["24h"]["failed"], 2)
        self.assertIn("BREVO_API_KEY missing", [r["reason"] for r in feed["delivery"]["reasons"]])
        self.assertIn("BREVO_API_KEY missing", self._page().get_data(as_text=True))

    def test_an_absent_recipient_is_counted_separately_and_does_not_blame_the_provider(self):
        # A member with no device registered is a member-settings fact. Letting
        # it degrade the push provider would make the provider panel useless --
        # it would be amber on a perfectly healthy platform.
        self._delivery("push", "sent", count=5)
        self._delivery("push", "skipped_no_device", "No active push device or subscription.", count=4)
        feed = self._feed().get_json()
        window = feed["delivery"]["windows"]["24h"]
        self.assertEqual(window["unroutable"], 4)
        self.assertEqual(window["failed"], 0)
        push = {p["key"]: p for p in feed["providers"]["providers"]}["delivery_push"]
        self.assertEqual(push["state"], "healthy")
        body = self._page().get_data(as_text=True)
        self.assertIn("No recipient", body)


class TestProviderHealthComesFromTraffic(CommsOpsCase):
    """§13's hard rule: not green because an environment variable exists."""

    def test_no_provider_is_green_without_observed_traffic(self):
        feed = self._feed().get_json()
        states = {p["key"]: p["state"] for p in feed["providers"]["providers"]}
        self.assertNotIn("healthy", set(states.values()))
        self.assertIn("UNKNOWN", self._page().get_data(as_text=True))

    def test_an_uninstrumented_provider_reads_unknown_rather_than_being_omitted(self):
        # Mux has no stored per-stream outcome. Silence is reported as silence:
        # dropping the row would read as "nothing to worry about".
        feed = self._feed().get_json()
        states = {p["key"]: p["state"] for p in feed["providers"]["providers"]}
        self.assertEqual(states.get("mux"), "unknown")

    def test_a_provider_whose_every_real_attempt_failed_is_not_green(self):
        # The question this panel exists to answer is "is push broken?", so the
        # case where it is entirely broken is the one assertion the panel cannot
        # do without. Tested alongside a healthy channel so the verdict is shown
        # to be per-provider rather than a page-wide mood.
        self._delivery("push", "failed", "APNs 410 BadDeviceToken", count=6)
        self._delivery("email", "sent", count=3)
        states = {p["key"]: p["state"] for p in self._feed().get_json()["providers"]["providers"]}
        self.assertEqual(states["delivery_push"], "failed")
        self.assertEqual(states["delivery_email"], "healthy")
        self.assertIn("FAILED", self._page().get_data(as_text=True))

    def test_a_provider_failing_some_of_the_time_is_degraded_not_failed(self):
        # The boundary between the two verdicts, so neither collapses into the
        # other: a partial outage must not read as total, nor total as partial.
        self._delivery("push", "failed", "APNs 410 BadDeviceToken", count=2)
        self._delivery("push", "sent", count=8)
        states = {p["key"]: p["state"] for p in self._feed().get_json()["providers"]["providers"]}
        self.assertEqual(states["delivery_push"], "degraded")

    def test_the_page_states_that_configuration_is_never_health(self):
        self.assertIn("Configuration presence is never reported as health",
                      self._page().get_data(as_text=True))


class TestNothingPrivateIsRendered(CommsOpsCase):
    """§22, asserted over the response rather than over the snapshot."""

    def setUp(self):
        super().setUp()
        self._call("call-live-9001", "connected", answered=110)
        self._conversation_with_messages(count=3)
        self._delivery("push", "sent", count=2)
        self._delivery("email", "failed", "SMTP 421 from relay", provider="brevo_email")
        # room_name is UNIQUE, so a second call needs its own handle. Still
        # credential-shaped, and still denied by the assertions below.
        self._call("call-rated-9002", "ended", end_reason="completed", duration=90,
                   room=SECRET_ROOM + "-b")
        self._quality(self._call_id("call-rated-9002"), latency=180, jitter=40, score=55.0)

    def _both_responses(self):
        return (self._page().get_data(as_text=True), self._feed().get_data(as_text=True))

    def test_no_message_body_reaches_either_response(self):
        for payload in self._both_responses():
            self.assertNotIn(SECRET_BODY, payload)

    def test_no_room_name_reaches_either_response(self):
        # room_name is a joinable handle for a call in progress, which makes it
        # an access credential and not merely an identifier.
        for payload in self._both_responses():
            self.assertNotIn(SECRET_ROOM, payload)

    def test_no_credential_shaped_value_reaches_either_response(self):
        # Token-shaped names, not provider names. "APNs" on its own appears in
        # the page's own caveat that per-transport push delivery is not
        # instrumented -- naming a provider in prose is the opposite of leaking
        # its credentials, and a needle that cannot tell the two apart would
        # have to be satisfied by deleting the honest sentence.
        needles = ("password", "secret_key", "bearer ", "p256dh", "exponentpushtoken",
                   "apns_token", "apns_key", "device_token", "push_token",
                   "subscription_json", "stream_key", SECRET_TOKEN.lower())
        for payload in self._both_responses():
            lowered = payload.lower()
            for needle in needles:
                self.assertNotIn(needle, lowered, f"response leaked {needle!r}")

    def test_the_needles_above_can_actually_fire(self):
        # The control for the test before it. A privacy assertion over a whole
        # HTML document is the easiest kind of test to pass vacuously, so prove
        # the comparison is live rather than trusting that it is.
        page, feed = self._both_responses()
        self.assertIn("push", page.lower())
        self.assertIn("push", feed.lower())
        self.assertNotIn("push_token", page.lower())

    def test_no_device_fingerprint_reaches_either_response(self):
        # The quality panel reads communication_call_quality_reports, which also
        # holds device_info_json. Reading a table makes everything in it one
        # SELECT * away from the page, so the fingerprint is planted and denied.
        for payload in self._both_responses():
            self.assertNotIn("DEVICE-FINGERPRINT-MUST-NOT-RENDER", payload)
            self.assertNotIn("iPhone15,2", payload)

    def test_quality_is_attributed_to_a_call_and_never_to_a_person(self):
        # Latency describes a network path. The reports carry user_id, so the
        # assertion is that the panel counts calls and declines to count people.
        quality = self._feed().get_json()["calls"]["quality"]
        self.assertIn("calls", quality)
        self.assertNotIn("user_id", quality)
        self.assertNotIn("users", quality)

    def test_a_session_identifier_is_shortened_rather_than_reusable(self):
        feed = self._feed().get_json()
        session = feed["calls"]["active"][0]["session"]
        self.assertNotEqual(session, "call-live-9001")
        self.assertLess(len(session), len("call-live-9001"))

    def test_participants_are_a_number_not_a_roster(self):
        entry = self._feed().get_json()["calls"]["active"][0]
        self.assertIsInstance(entry["participants"], int)
        self.assertNotIn("user_id", entry)
        self.assertNotIn("participant_ids", entry)

    def test_the_private_column_inventory_is_never_a_snapshot_key(self):
        # A column added to a SELECT * would otherwise arrive here unnoticed.
        blob = self._feed().get_data(as_text=True).lower()
        ambiguous = {"auth", "title", "reason", "description", "url"}
        for column in comms_ops.PRIVATE_COLUMNS:
            if column in ambiguous:
                continue
            self.assertNotIn(column, blob, f"private column {column!r} appears in the feed")


class TestQualityReportsOnlyWhatWasMeasured(CommsOpsCase):
    """§9's "network state if real", and the production shape that defines it.

    Production holds 542 quality reports, of which 452 carry latency, jitter and
    score all exactly zero: a row written by the client rather than a network
    observed. An average over all of them returns 2.5 ms, which would advertise
    a faster-than-physics platform. These tests pin the distinction, because the
    cheap implementation -- one AVG over the column -- passes every other test
    in this file.
    """

    def _quality_payload(self):
        return self._feed().get_json()["calls"]["quality"]

    def test_placeholder_rows_alone_report_no_figure_at_all(self):
        self._call("call-q-1", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-1"), latency=0, jitter=0, score=0.0, count=12)
        quality = self._quality_payload()
        self.assertFalse(quality["measurable"])
        self.assertEqual(quality["reports"], 12)
        self.assertEqual(quality["measured"], 0)
        # The failure mode being denied: a zero-latency verdict.
        self.assertNotIn("worst_latency_ms", quality)

    def test_a_placeholder_does_not_drag_a_real_measurement_toward_zero(self):
        self._call("call-q-2", "ended", end_reason="completed", duration=60)
        call_id = self._call_id("call-q-2")
        self._quality(call_id, latency=0, jitter=0, score=0.0, count=9)
        self._quality(call_id, latency=240, jitter=60, loss=0.04, score=31.0)
        quality = self._quality_payload()
        self.assertTrue(quality["measurable"])
        self.assertEqual(quality["reports"], 10)
        self.assertEqual(quality["measured"], 1)
        self.assertEqual(quality["unmeasured"], 9)
        self.assertEqual(quality["worst_latency_ms"], 240)
        self.assertEqual(quality["worst_jitter_ms"], 60)
        self.assertAlmostEqual(quality["worst_packet_loss"], 0.04)
        self.assertAlmostEqual(quality["worst_quality_score"], 31.0)

    def test_an_unscored_measurement_is_not_the_worst_possible_score(self):
        # A row that timed the network but did not rate the call must not be
        # read as a rating of zero. MIN over the measured set would do exactly
        # that, which is the same arithmetic-but-false mistake as the average.
        self._call("call-q-3", "ended", end_reason="completed", duration=60)
        call_id = self._call_id("call-q-3")
        self._quality(call_id, latency=90, jitter=10, score=0.0)
        self._quality(call_id, latency=70, jitter=8, score=88.0)
        quality = self._quality_payload()
        self.assertEqual(quality["measured"], 2)
        self.assertAlmostEqual(quality["worst_quality_score"], 88.0)

    def test_an_unrated_call_reports_an_absent_score_not_a_zero(self):
        self._call("call-q-4", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-4"), latency=150, jitter=20, score=0.0)
        quality = self._quality_payload()
        self.assertTrue(quality["measurable"])
        self.assertEqual(quality["worst_latency_ms"], 150)
        self.assertIsNone(quality["worst_quality_score"])

    def test_the_panel_says_it_describes_finished_calls(self):
        # Production has no quality report against any call still in progress
        # (428 ended, 9 missed, 5 declined, 2 expired). An operator reading a
        # latency figure next to a list of live calls would reasonably assume it
        # described them, so the payload has to disclaim it.
        self._call("call-q-5", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-5"), latency=100, jitter=12, score=70.0)
        self.assertIn("after a call ends", self._quality_payload()["note"])

    def test_the_page_says_not_measured_rather_than_zero_milliseconds(self):
        # The rendered half of the same property. A panel that prints "0 ms"
        # for an unmeasured network is the quietest possible lie on this page:
        # a perfect score, shown precisely when nothing is known.
        self._call("call-q-6", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-6"), latency=0, jitter=0, score=0.0, count=4)
        body = self._page().get_data(as_text=True)
        self.assertIn("No network measurement available", body)
        self.assertNotIn("0 ms", body)

    def test_the_page_shows_a_measured_figure_when_there_is_one(self):
        # The control for the test above: prove the panel can render a number,
        # so that "no 0 ms anywhere" is not passing because the panel is mute.
        self._call("call-q-7", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-7"), latency=310, jitter=44, loss=0.02, score=25.0)
        body = self._page().get_data(as_text=True)
        self.assertIn("310 ms", body)
        self.assertIn("44 ms", body)
        self.assertIn("2.00%", body)
        self.assertIn("25 / 100", body)
        self.assertNotIn("No network measurement available", body)

    def test_an_unrated_but_measured_call_renders_as_not_rated(self):
        self._call("call-q-8", "ended", end_reason="completed", duration=60)
        self._quality(self._call_id("call-q-8"), latency=120, jitter=15, score=0.0)
        body = self._page().get_data(as_text=True)
        self.assertIn("120 ms", body)
        self.assertIn("not rated", body)
        # The specific wrong rendering: an absent score as the worst score.
        self.assertNotIn("0 / 100", body)

    def test_no_reports_at_all_is_not_an_error(self):
        quality = self._quality_payload()
        self.assertFalse(quality["measurable"])
        self.assertEqual(quality["reports"], 0)
        self.assertEqual(self._feed().get_json()["calls"]["state"], "ready")


class TestAFailedQueryIsNotZero(CommsOpsCase):
    """§27. The property that makes every other number trustworthy."""

    def _hide(self, table):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute(f"ALTER TABLE {table} RENAME TO {table}_hidden_by_test")
        conn.commit()
        conn.close()
        self.addCleanup(self._restore, table)

    def _restore(self, table):
        try:
            conn = db_service.connect()
            cur = conn.cursor()
            cur.execute(f"ALTER TABLE {table}_hidden_by_test RENAME TO {table}")
            conn.commit()
            conn.close()
        except Exception:
            pass

    def test_a_broken_section_does_not_blank_the_working_ones(self):
        self._conversation_with_messages(count=3)
        self._hide("communication_calls")
        feed = self._feed().get_json()
        self.assertEqual(feed["calls"]["state"], "error")
        self.assertEqual(feed["chat"]["state"], "ready")
        self.assertEqual(feed["chat"]["windows"]["24h"]["messages"], 3)

    def test_a_broken_section_is_not_reported_healthy(self):
        self._hide("communication_calls")
        feed = self._feed().get_json()
        self.assertNotEqual(feed["state"], "healthy")
        self.assertIn("calls", feed["section_errors"])

    def test_a_broken_section_renders_as_unavailable_not_as_no_activity(self):
        self._hide("communication_calls")
        body = self._page().get_data(as_text=True)
        self.assertIn("Calls unavailable", body)
        self.assertIn("treat this as unknown, not as zero activity", body)
        self.assertNotIn("No call is in an active state right now", body)

    def test_a_broken_section_reports_no_number_rather_than_a_zero(self):
        self._conversation_with_messages(count=3)
        self._hide("communication_calls")
        head = comms_ops.dashboard_headline(comms_ops.comms_ops_snapshot())
        self.assertIsNone(head["active_calls"])
        self.assertIsNone(head["last_call_at"])
        self.assertEqual(head["messages_24h"], 3)

    def test_the_page_still_answers_when_every_section_is_broken(self):
        for table in ("communication_calls", "comm_v2_messages", "comm_v2_conversations",
                      "notification_delivery_jobs"):
            self._hide(table)
        response = self._page()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("unavailable", body.lower())

    def test_the_dashboard_survives_a_comms_snapshot_that_raises(self):
        # The dashboard predates this section. A failure here must not take out
        # the page the owner opens first.
        original = comms_ops.cached_comms_ops_snapshot

        def explode(*args, **kwargs):
            raise RuntimeError("snapshot is down")

        comms_ops.cached_comms_ops_snapshot = explode
        try:
            self._sign_in(self.owner_id)
            response = self.client.get("/admin/dashboard")
            self.assertEqual(response.status_code, 200)
            body = response.get_data(as_text=True)
            self.assertIn("Communications metrics unavailable", body)
            self.assertIn("not a report of zero activity", body)
        finally:
            comms_ops.cached_comms_ops_snapshot = original


class TestTheOwnerCanFindIt(CommsOpsCase):
    """§3, §4, §31. Discoverability was half the original defect."""

    def test_the_dashboard_carries_a_communications_section_and_a_way_in(self):
        self._call("call-live-7001", "connected", answered=110)
        self._conversation_with_messages(count=2)
        self._sign_in(self.owner_id)
        comms_ops.reset_snapshot_cache()
        body = self.client.get("/admin/dashboard").get_data(as_text=True)
        self.assertIn(">Communications</h2>", body)
        self.assertIn("Open Communications Center", body)
        self.assertIn(PAGE, body)
        self.assertIn("Active calls now", body)

    def test_the_dashboard_shows_the_real_counts(self):
        self._conversation_with_messages(count=5)
        self._sign_in(self.owner_id)
        comms_ops.reset_snapshot_cache()
        body = self.client.get("/admin/dashboard").get_data(as_text=True)
        self.assertIn("Messages &middot; 24h", body)
        self.assertRegex(body, r"Messages &middot; 24h</div><span class='metric'>5<")

    def test_calls_is_filed_under_communications_in_the_navigation(self):
        body = self._page().get_data(as_text=True)
        self.assertIn("Comms Ops", body)
        self.assertIn("/admin/calls", body)

    def test_the_page_links_onward_to_the_existing_detail_surfaces(self):
        # The mission is not to rebuild /admin/calls, it is to make it findable.
        body = self._page().get_data(as_text=True)
        self.assertIn("/admin/calls", body)
        self.assertIn("/admin/notification-delivery", body)

    def test_the_page_names_its_own_sources(self):
        # §32's data-source requirement, on the page rather than only in a PR.
        body = self._page().get_data(as_text=True)
        for table in ("communication_calls", "comm_v2_messages", "notification_delivery_jobs"):
            self.assertIn(table, body)


class TestTheFeedIsCheapAndBounded(CommsOpsCase):
    """§18, §19."""

    def test_the_feed_is_json_and_not_cacheable_by_a_proxy(self):
        response = self._feed()
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/json", response.headers.get("Content-Type", ""))
        # The admin response pipeline appends max-age=0 of its own, so the
        # assertion is that no-store is present, not that it is the whole header.
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_a_second_request_inside_the_window_is_served_from_cache(self):
        self._sign_in(self.owner_id)
        comms_ops.reset_snapshot_cache()
        first = self.client.get(FEED).get_json()
        second = self.client.get(FEED).get_json()
        self.assertEqual(first["generated_at"], second["generated_at"])
        self.assertIn("cache_age_seconds", second)

    def test_the_active_call_list_is_capped(self):
        self.assertLessEqual(comms_ops.MAX_ACTIVE_CALLS, 100)
        for index in range(comms_ops.MAX_ACTIVE_CALLS + 5):
            self._call(f"call-many-{index:04d}", "connected", answered=110,
                       room=f"room-{index}")
        feed = self._feed().get_json()
        self.assertLessEqual(len(feed["calls"]["active"]), comms_ops.MAX_ACTIVE_CALLS)
        # The count is the truth even though the list is truncated, so the cap
        # cannot be mistaken for the real number of calls in progress.
        self.assertEqual(feed["calls"]["active_count"], comms_ops.MAX_ACTIVE_CALLS + 5)

    def test_the_page_reports_what_it_cost(self):
        body = self._page().get_data(as_text=True)
        self.assertIn("Snapshot cost", body)

    def test_the_polling_script_is_served_with_a_cache_token(self):
        body = self._page().get_data(as_text=True)
        self.assertIn("/static/js/admin_comms_ops.js?v=", body)
        asset = self.client.get("/static/js/admin_comms_ops.js")
        self.assertEqual(asset.status_code, 200)


class TestHighActivityStillRenders(CommsOpsCase):
    """§28's high-activity state."""

    def test_hundreds_of_rows_do_not_change_the_shape_of_the_page(self):
        for index in range(120):
            self._call(f"call-bulk-{index:04d}", "ended", started=300 + index, answered=299 + index,
                       duration=30, end_reason="native_hangup", room=f"room-bulk-{index}")
        for _ in range(5):
            self._conversation_with_messages(count=20)
        self._delivery("push", "sent", count=200)
        self._delivery("email", "failed", "SMTP 421 from relay", provider="brevo_email", count=7)

        response = self._page()
        self.assertEqual(response.status_code, 200)
        feed = self._feed().get_json()
        self.assertEqual(feed["calls"]["windows"]["24h"]["started"], 120)
        self.assertEqual(feed["chat"]["windows"]["24h"]["messages"], 100)
        self.assertEqual(feed["delivery"]["windows"]["24h"]["total"], 207)
        self.assertEqual(feed["delivery"]["windows"]["24h"]["failed"], 7)
        self.assertLessEqual(len(feed["calls"]["failures"]), comms_ops.MAX_FAILURE_ROWS)

    def test_rendered_numbers_are_grouped_for_a_human(self):
        self._delivery("push", "sent", count=1200)
        body = self._page().get_data(as_text=True)
        self.assertIn("1,200", body)


class TestOutputIsEscaped(CommsOpsCase):
    """§21's output-escaping clause, on the one field a provider controls."""

    def test_a_hostile_failure_reason_is_not_rendered_as_markup(self):
        payload = "<script>window.__pwned=1</script>"
        self._delivery("email", "failed", payload, provider="brevo_email")
        body = self._page().get_data(as_text=True)
        self.assertNotIn(payload, body)
        # Asserted on the cell, not on the whole document: the admin shell ships
        # its own <script> tags, so "no script tag anywhere" would be a needle
        # that matches the page's own markup rather than the attack. The cell is
        # inert text -- the sanitiser drops the element and keeps the words, so
        # the operator can still read what the provider said.
        self.assertIn("<td>window.__pwned=1</td>", body)

    def test_a_hostile_end_reason_is_not_rendered_as_markup(self):
        self._call("call-xss-0001", "failed", end_reason="<img src=x onerror=alert(1)>")
        body = self._page().get_data(as_text=True)
        self.assertNotIn("<img src=x", body)
        self.assertNotIn("onerror=alert", body)


class TestLookupIsNotUserSearch(CommsOpsCase):
    """§16 and §21. The only input on this surface, so the only attack surface.

    A search box on an admin page is where an operations tool becomes a people
    finder, and the slide is gradual: accept an email "just to be helpful",
    accept a row id "because admins have one anyway", and the box now answers
    questions about who exists. These tests pin the refusals as behaviour rather
    than as intent.
    """

    LOOKUP = "/admin/communications/lookup.json"

    def setUp(self):
        super().setUp()
        self._call("call-lookup-abcdef12", "connected", answered=100)
        self.conversation_id = self._conversation_with_messages(count=4)
        security_guard.BUCKETS.clear()

    def _lookup(self, term, admin_id=None):
        self._sign_in(self.owner_id if admin_id is None else admin_id)
        return self.client.get(f"{self.LOOKUP}?q={term}")

    # -- authorisation ----------------------------------------------------

    def test_anonymous_cannot_look_anything_up(self):
        self._sign_out()
        response = self.client.get(f"{self.LOOKUP}?q=call-lookup-abcdef12")
        self.assertIn(response.status_code, (302, 401, 403))
        self.assertNotIn("abcdef12", response.get_data(as_text=True))

    def test_an_admin_without_the_permission_cannot_look_anything_up(self):
        # The same negative role the page uses. A lookup authorised by "is an
        # admin" rather than by a permission is the §29 mutation made real.
        moderator = self._make_admin("pulse_moderator")
        response = self._lookup("call-lookup-abcdef12", admin_id=moderator)
        self.assertIn(response.status_code, (302, 401, 403))
        self.assertNotIn("abcdef12", response.get_data(as_text=True))

    def test_the_permitted_admin_can(self):
        # The control. Without it the three tests above would pass against an
        # endpoint that refuses everyone, including the operator it is for.
        response = self._lookup("call-lookup-abcdef12")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"], "found")

    # -- what it refuses to search ----------------------------------------

    def test_an_email_address_is_refused_rather_than_searched(self):
        response = self._lookup("someone%40example.com")
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["state"], "refused")
        # The refusal has to be the same whether or not that person exists, or
        # the error message itself becomes the enumeration oracle.
        self.assertIn("discover who has an account", payload["reason"])

    def test_a_row_number_is_refused_by_the_numeric_rule_not_by_its_length(self):
        # Asserted on a number long enough to satisfy the identifier grammar.
        # A short one is refused for being four characters, which would let the
        # numeric rule be deleted without this test noticing -- the guard would
        # be untested precisely where production ids eventually live.
        response = self._lookup("104729")
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["state"], "refused")
        self.assertIn("enumerable", payload["reason"])

    def test_a_sweep_of_row_numbers_finds_nothing_at_any_id(self):
        # The property the test above protects, asserted by actually trying it,
        # across both length regimes so neither rule carries the whole weight.
        for candidate in list(range(1, 15)) + list(range(1000, 1010)):
            payload = self._lookup(str(candidate)).get_json()
            self.assertEqual(payload["state"], "refused", f"id {candidate} was searched")

    def test_the_numeric_rule_is_what_refuses_a_long_number(self):
        # The control for the two tests above, checked against the classifier
        # rather than the route: proof that the length rule is not quietly
        # doing all the work.
        self.assertIn("enumerable", comms_ops.lookup_term_refusal("104729"))
        # Non-numeric, so this one reaches the length rule. A numeric short
        # term would be caught by the rule above and prove nothing about which
        # of the two is doing the refusing.
        self.assertIn("4-64 characters", comms_ops.lookup_term_refusal("ab"))
        self.assertEqual(comms_ops.lookup_term_refusal("call-lookup-abcdef12"), "")

    def test_a_name_shaped_term_matches_nothing_it_was_not_given(self):
        # Free text that happens to satisfy the grammar still only matches ids.
        payload = self._lookup("cherie").get_json()
        self.assertEqual(payload["state"], "not_found")
        self.assertEqual(payload["calls"], [])
        self.assertEqual(payload["conversations"], [])

    def test_a_wildcard_is_not_a_wildcard(self):
        # Also the regression guard for the compat layer's percent escaping:
        # a LIKE pattern written here would be mangled on PostgreSQL only.
        for term in ("%25", "_____", "a%25b", "*"):
            payload = self._lookup(term).get_json()
            self.assertIn(payload["state"], ("refused", "not_found"))
            self.assertEqual(payload.get("calls") or [], [])

    def test_a_quote_does_not_reach_the_query(self):
        payload = self._lookup("call%27%3B--").get_json()
        self.assertEqual(payload["state"], "refused")

    # -- what it returns ---------------------------------------------------

    def test_the_visible_short_handle_is_enough_to_find_the_call(self):
        # The page prints eight characters and an ellipsis. A lookup that only
        # accepted the full id would be a box an operator cannot type into from
        # what is in front of them.
        payload = self._lookup("call-loo").get_json()
        self.assertEqual(payload["state"], "found")
        self.assertEqual(len(payload["calls"]), 1)
        self.assertEqual(payload["calls"][0]["status"], "connected")

    def test_a_found_call_carries_no_room_handle_and_no_roster(self):
        payload = self._lookup("call-lookup-abcdef12").get_json()
        entry = payload["calls"][0]
        self.assertNotIn("room", entry)
        self.assertNotIn("room_name", entry)
        self.assertIsInstance(entry["participants"], int)
        self.assertNotIn(SECRET_ROOM, json.dumps(payload))

    def test_a_found_conversation_carries_no_message_body(self):
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT public_id FROM comm_v2_conversations WHERE id=?", (self.conversation_id,))
        public_id = dict(cur.fetchone())["public_id"]
        conn.close()
        payload = self._lookup(public_id).get_json()
        self.assertEqual(payload["state"], "found")
        self.assertEqual(len(payload["conversations"]), 1)
        self.assertNotIn(SECRET_BODY, json.dumps(payload))

    def test_an_unknown_id_says_unknown_rather_than_rendering_empty(self):
        payload = self._lookup("zzzz-no-such-session").get_json()
        self.assertEqual(payload["state"], "not_found")

    # -- rate limiting and audit ------------------------------------------

    def test_lookups_are_rate_limited_per_admin(self):
        # §21. The grammar bounds what can be swept; this bounds how fast.
        last = None
        for _ in range(40):
            last = self._lookup("call-lookup-abcdef12")
            if last.status_code == 429:
                break
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.get_json()["state"], "rate_limited")

    def test_a_lookup_is_written_to_the_admin_audit_log(self):
        self._lookup("call-lookup-abcdef12")
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT action, target_id FROM admin_audit_logs WHERE action=?",
                    ("comms_ops_lookup",))
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        self.assertTrue(rows, "a session lookup left no audit trail")
        self.assertEqual(rows[0]["target_id"], "call-lookup-abcdef12")

    def test_a_refused_term_is_not_recorded_as_a_lookup(self):
        # Nothing was looked up, so recording one would make the audit log
        # claim an operator saw a session they were never shown.
        self._lookup("someone%40example.com")
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM admin_audit_logs WHERE action=?",
                    ("comms_ops_lookup",))
        count = dict(cur.fetchone())["n"]
        conn.close()
        self.assertEqual(count, 0)

    # -- the page -----------------------------------------------------------

    def test_the_page_offers_the_box_and_explains_the_refusals(self):
        body = self._page().get_data(as_text=True)
        self.assertIn("data-comms-lookup", body)
        self.assertIn("/admin/communications/lookup.json", body)
        self.assertIn("discover who has an account", body)


class TestAFailureReasonIsNotACredentialViewer(CommsOpsCase):
    """§22. The one field on this surface that carries provider free text.

    ``notification_delivery_jobs.failure_reason`` is written as
    ``str(result.get("message") or result.get("error") or ...)[:1000]`` -- the
    channel adapter's own output, bounded only by length. It is shown because
    "410 BadDeviceToken" is the single most actionable string on the delivery
    panel. That makes it the one place where a privacy boundary depends on what
    a third party chose to put in an error message, so the boundary is enforced
    here instead of assumed.

    Production holds only five short hand-written reasons today, so these tests
    pin a latent leak rather than a live one. Latent here means one adapter
    change away, and the whole surface would be downgraded silently: the page
    would keep working and start printing credentials.
    """

    def test_an_actionable_reason_survives_untouched(self):
        # Listed first because it is the one that matters most. A redaction that
        # ate the useful text would pass every test below while making the panel
        # worthless, and "redact everything" is the easy wrong answer here.
        for reason in ("410 BadDeviceToken",
                       "Brevo email is not configured.",
                       "No active push device or subscription.",
                       "Verified SMS opt-in phone number is missing.",
                       "One or more push tokens are invalid.",
                       "Push delivery failed."):
            self.assertEqual(comms_ops.safe_failure_reason(reason), reason)

    def test_an_expo_token_is_redacted_by_name(self):
        # Bracketed, so the length rule alone would leave fragments of it.
        cleaned = comms_ops.safe_failure_reason(
            f"Invalid push token {SECRET_TOKEN} for device")
        self.assertNotIn("MUST-NEVER-RENDER", cleaned)
        self.assertIn("[redacted]", cleaned)
        # The sentence around it survives, or an operator cannot tell which
        # failure they are looking at.
        self.assertIn("Invalid push token", cleaned)

    def test_a_bare_opaque_run_is_redacted_by_shape_not_by_a_known_prefix(self):
        # The case that matters: a provider nobody has integrated yet. An
        # allowlist of known credential prefixes fails exactly here.
        secret = "c" * 64
        cleaned = comms_ops.safe_failure_reason(f"FCM rejected registration {secret}")
        self.assertNotIn(secret, cleaned)
        self.assertIn("FCM rejected registration", cleaned)

    def test_a_jwt_is_redacted(self):
        jwt = "eyJhbGciOiJFUzI1NiJ9.eyJpc3MiOiJURUFNSUQxMjMifQ.c2lnbmF0dXJl"
        cleaned = comms_ops.safe_failure_reason(f"APNs auth failed: {jwt}")
        self.assertNotIn("eyJhbGciOiJFUzI1NiJ9", cleaned)
        self.assertIn("APNs auth failed", cleaned)

    def test_the_reason_is_length_capped(self):
        self.assertLessEqual(
            len(comms_ops.safe_failure_reason("word " * 400)), comms_ops.REASON_MAX_CHARS)

    def test_the_snapshot_and_the_feed_both_redact(self):
        # The page and the JSON endpoint are two renderers of one dict, so the
        # redaction lives in the dict. Asserted on both, because a fix applied
        # only in the HTML would leave the polling feed leaking.
        self._delivery("push", "failed", reason=f"token {SECRET_TOKEN} rejected", count=2)
        comms_ops.reset_snapshot_cache()
        snapshot = comms_ops.comms_ops_snapshot()
        reasons = (snapshot["delivery"] or {}).get("reasons") or []
        self.assertTrue(reasons, "the fixture should have produced a failure reason row")
        self.assertNotIn("MUST-NEVER-RENDER", json.dumps(reasons))
        self.assertNotIn("MUST-NEVER-RENDER", self._page().get_data(as_text=True))
        self.assertNotIn("MUST-NEVER-RENDER", self._feed().get_data(as_text=True))

    def test_reasons_differing_only_in_their_token_fold_into_one_row(self):
        # Without the fold, an adapter echoing a distinct token per failure
        # fills the twelve-row panel with rows that all read "[redacted]" and
        # pushes the one informative reason off the bottom -- the panel degrades
        # worst in precisely the case the redaction exists for.
        for index in range(20):
            self._delivery("push", "failed", reason=f"rejected ExponentPushToken[{index:0>50}]")
        self._delivery("email", "config_missing", reason="Brevo email is not configured.", count=2)
        comms_ops.reset_snapshot_cache()
        reasons = comms_ops.comms_ops_snapshot()["delivery"]["reasons"]
        redacted = [r for r in reasons if "[redacted]" in r["reason"]]
        self.assertEqual(len(redacted), 1, f"expected one folded row, got {reasons}")
        self.assertEqual(redacted[0]["count"], 20)
        # And the informative one is still present rather than crowded out.
        self.assertTrue(any("Brevo" in r["reason"] for r in reasons), reasons)


class TestPerUserDiagnostics(CommsOpsCase):
    """§17 and §22. The panel on the account page, and what it must not become.

    This is the most dangerous panel on the surface, because here the admin has
    already named a person. A number next to their account looks like support
    context, and "spoke with" would look like support context too -- and that
    one is a social graph. The tests below pin the counts as present and the
    peers, threads and bodies as absent.
    """

    MEMBER = 1          # the user id every fixture writer in this file plants

    def setUp(self):
        super().setUp()
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE user_id=?", (self.MEMBER,))
        cur.execute("INSERT INTO users (user_id, email, full_name) VALUES (?, ?, ?)",
                    (self.MEMBER, "member@example.com", "Member Under Support"))
        conn.commit()
        conn.close()

    def _detail(self, admin_id=None):
        self._sign_in(self.owner_id if admin_id is None else admin_id)
        return self.client.get(f"/admin/users/{self.MEMBER}")

    # -- authorisation ------------------------------------------------------

    def test_anonymous_sees_no_account_page_and_so_no_diagnostics(self):
        self._sign_out()
        response = self.client.get(f"/admin/users/{self.MEMBER}")
        self.assertIn(response.status_code, (302, 401, 403))
        self.assertNotIn("Notification delivery", response.get_data(as_text=True))

    def test_the_panel_is_rendered_for_an_admin_who_may_see_the_account(self):
        # The control for every negative in this class. §17 asks for the block
        # to be *in* the admin user view, so its absence is a failure too.
        self._call("call-diag-aaaa1111", "connected", answered=100, duration=42)
        response = self._detail()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("<h2>Communications</h2>", body)
        self.assertIn("Notification delivery", body)

    # -- it answers the support question ------------------------------------

    def test_a_members_calls_and_their_outcomes_are_counted(self):
        self._call("call-diag-aaaa1111", "connected", answered=100, duration=42)
        self._call("call-diag-bbbb2222", "failed", end_reason="network_error",
                   room=SECRET_ROOM + "-diag-b")
        diag = comms_ops.comms_user_diagnostics(self.MEMBER)
        placed = diag["sections"]["calls_placed"]
        self.assertEqual(placed["state"], "ready")
        self.assertEqual(placed["7d"], 2)
        self.assertEqual(placed["answered"], 1)
        self.assertEqual(placed["connected"], 1)
        statuses = {row["status"]: row["count"]
                    for row in diag["sections"]["calls_placed_outcomes"]["by_status"]}
        self.assertEqual(statuses, {"connected": 1, "failed": 1})

    def test_the_page_names_the_channel_that_is_failing_for_this_member(self):
        # "Why am I not getting notifications?" is the complaint §17 exists to
        # answer, so the answer has to be legible on the page, not just in the
        # dict behind it.
        self._delivery("push", "failed", reason="410 BadDeviceToken", count=3)
        self._delivery("email", "sent", count=2)
        body = self._detail().get_data(as_text=True)
        self.assertIn("Failing: push", body)
        self.assertIn("410 BadDeviceToken", body)

    def test_an_unroutable_attempt_is_not_counted_as_a_provider_failure(self):
        # No registered device is the member's own state, not a push outage.
        # Folded together, an admin would chase a provider that is working.
        self._delivery("push", "skipped_no_device", count=4)
        diag = comms_ops.comms_user_diagnostics(self.MEMBER)
        channels = {c["channel"]: c for c in diag["sections"]["delivery"]["channels"]}
        self.assertEqual(channels["push"]["unroutable"], 4)
        self.assertEqual(channels["push"]["failed"], 0)
        self.assertEqual(diag["sections"]["delivery"]["failing"], [])

    def test_messages_are_counted_and_threads_are_a_number(self):
        self._conversation_with_messages(count=3)
        self._conversation_with_messages(count=2)
        messages = comms_ops.comms_user_diagnostics(self.MEMBER)["sections"]["messages_sent"]
        self.assertEqual(messages["7d"], 5)
        # Two threads as a count. A list here is the thing that would let an
        # admin walk from a person to the conversations they could then read.
        self.assertEqual(messages["threads"], 2)

    # -- §22. what the panel must never carry -------------------------------

    def test_no_message_body_reaches_the_account_page(self):
        self._conversation_with_messages(count=4)
        self.assertNotIn(SECRET_BODY, self._detail().get_data(as_text=True))

    def test_no_room_handle_reaches_the_account_page(self):
        # A joinable handle beside a named person is worse than beside a
        # session id: it is an invitation into that person's call.
        self._call("call-diag-aaaa1111", "connected", answered=100)
        self.assertNotIn(SECRET_ROOM, self._detail().get_data(as_text=True))

    def test_no_push_token_or_device_fingerprint_reaches_the_account_page(self):
        self._call("call-diag-aaaa1111", "connected", answered=100)
        self._quality(self._call_id("call-diag-aaaa1111"), latency=90, score=4.0)
        self._delivery("push", "failed", reason=SECRET_TOKEN)
        body = self._detail().get_data(as_text=True)
        self.assertNotIn("DEVICE-FINGERPRINT-MUST-NOT-RENDER", body)
        # The reason column is deliberately shown, so a token placed *in* it by
        # the pipeline must still not be: this asserts the panel does not become
        # a credential viewer by way of a field it is allowed to print. This
        # test failed when it was first written, which is why
        # safe_failure_reason() exists.
        self.assertNotIn("MUST-NEVER-RENDER", body)
        self.assertIn("[redacted]", body)

    def test_no_peer_identity_is_returned_for_a_call_this_member_joined(self):
        # The social-graph test. The participants table holds everyone else on
        # the call and the join is one line away, so this asserts on the whole
        # payload rather than on one field.
        self._call("call-diag-cccc3333", "connected", answered=100,
                   room=SECRET_ROOM + "-diag-c")
        conn = db_service.connect()
        cur = conn.cursor()
        call_id = self._call_id("call-diag-cccc3333")
        for peer in (self.MEMBER, 4242):
            cur.execute(
                "INSERT INTO communication_call_participants (call_id, user_id, role, status, "
                "joined_at, created_at) VALUES (?, ?, 'participant', 'joined', ?, ?)",
                (call_id, peer, _stamp(90), _stamp(90)),
            )
        conn.commit()
        conn.close()

        diag = comms_ops.comms_user_diagnostics(self.MEMBER)
        self.assertEqual(diag["sections"]["calls_joined"]["7d"], 1)
        self.assertNotIn("4242", json.dumps(diag))
        self.assertNotIn("4242", self._detail().get_data(as_text=True))

    def test_no_conversation_identifier_is_returned(self):
        # A conversation id here would hand the §16 lookup a route from a named
        # person to their threads, which is the one path this surface must not
        # have. Asserted against the lookup's own grammar, not just the string:
        # any id-shaped value in this payload is the beginning of that path.
        conversation_id = self._conversation_with_messages(count=2)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("SELECT public_id FROM comm_v2_conversations WHERE id=?", (conversation_id,))
        public_id = dict(cur.fetchone())["public_id"]
        conn.close()
        payload = json.dumps(comms_ops.comms_user_diagnostics(self.MEMBER))
        self.assertNotIn(public_id, payload)
        self.assertNotIn(public_id, self._detail().get_data(as_text=True))

    # -- §27. a broken section says so --------------------------------------

    def test_a_failed_section_says_unavailable_and_never_zero(self):
        # The §29 shape, at the per-user level: a query that raised must not
        # render as "this member has no activity", because that is the answer
        # that sends an admin to tell them their phone is fine.
        self._delivery("push", "failed", reason="410 BadDeviceToken", count=2)
        conn = db_service.connect()
        cur = conn.cursor()
        cur.execute("DROP TABLE notification_delivery_jobs")
        conn.commit()
        conn.close()
        try:
            diag = comms_ops.comms_user_diagnostics(self.MEMBER)
            self.assertEqual(diag["state"], "degraded")
            self.assertEqual(diag["sections"]["delivery"]["state"], "error")
            self.assertNotIn("attempted", diag["sections"]["delivery"])
            body = self._detail().get_data(as_text=True)
            self.assertIn("Not a report of no activity", body)
            # The rest of the account page still renders. One unreadable table
            # must not take down the page an admin opened to help someone.
            self.assertIn("<h2>Communications</h2>", body)
        finally:
            conn = db_service.connect()
            from services import pulsesoc_notification_system as notif
            notif.ensure_schema(conn)
            conn.commit()
            conn.close()

    def test_a_member_with_no_activity_reads_as_no_activity_not_as_an_error(self):
        # The control for the test above. Both states have to be reachable or
        # "unavailable" becomes the only thing the panel can ever say.
        diag = comms_ops.comms_user_diagnostics(self.MEMBER)
        self.assertEqual(diag["state"], "ready")
        self.assertEqual(diag["section_errors"], {})
        self.assertEqual(diag["sections"]["calls_placed"]["7d"], 0)
        body = self._detail().get_data(as_text=True)
        self.assertNotIn("Not a report of no activity", body)
        self.assertIn("No notifications attempted", body)

    def test_the_panel_names_its_sources_and_its_exclusions(self):
        # §32 asks for the data sources to be stated. On this panel it is also
        # the privacy claim, written where the operator reading it can see it.
        body = self._detail().get_data(as_text=True)
        self.assertIn("notification_delivery_jobs", body)
        self.assertIn("who this member communicated with", body)


class TestTheHarnessIsHonest(unittest.TestCase):
    """Tests about the tests. Both properties below have already failed once.

    A suite whose fixtures leak is worse than no suite: it reports green while
    asserting something other than what it says. Both of these are cheap static
    checks that would have caught the leak the moment the snapshot grew a new
    table, rather than three test classes later.
    """

    def test_every_table_the_snapshot_reads_is_emptied_between_tests(self):
        # Derived from the module rather than maintained beside it, because a
        # hand-kept list is exactly what went stale. If the snapshot learns to
        # read a table, this fails until the reset list learns about it too.
        source = pathlib.Path(comms_ops.__file__).read_text(encoding="utf-8")
        read = set(re.findall(r"FROM\s+([a-z_][a-z0-9_]*)", source))
        # Tables the snapshot reads only for liveness of the schema itself, and
        # the admin tables owned by other fixtures, are not comms state.
        ignore = {"information_schema", "sqlite_master", "pg_catalog", "users", "admin_users"}
        missing = sorted(t for t in read - ignore - set(RESET_TABLES)
                         if t.startswith(("comm", "notification")))
        self.assertEqual(missing, [], f"snapshot reads {missing} but tests never empty them")

    def test_the_derivation_above_actually_finds_tables(self):
        # The control. A regex that silently matched nothing would make the test
        # above pass forever, which is the failure mode it exists to prevent.
        source = pathlib.Path(comms_ops.__file__).read_text(encoding="utf-8")
        read = set(re.findall(r"FROM\s+([a-z_][a-z0-9_]*)", source))
        self.assertIn("communication_calls", read)
        self.assertIn("communication_call_quality_reports", read)


if __name__ == "__main__":
    unittest.main()
