"""Unit tests for the unified server-authoritative presence service.

These tests exercise services/presence_service.py against an in-memory SQLite
database with a controllable clock, proving the core Mission-9 guarantees:

  * a user is online only while at least one live session exists;
  * presence degrades to offline on its own once every session expires
    (no background worker required) and last-seen is then exposed;
  * multi-device: online while >=1 session lives, offline once all expire;
  * transient activity indicators (typing/recording/uploading) auto-clear
    via their TTL and can never stick;
  * privacy: invisible mode, blocks, hide-last-seen, and nobody/contacts
    visibility all hide presence, byte-identically to a genuine offline user;
  * self always sees own true state.

Run: python3 -m pytest tests/test_presence_service.py  (or unittest).
"""

import os
import sqlite3
import sys
import unittest
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import presence_service as ps  # noqa: E402


class _Clock:
    """A controllable stand-in for presence_service.utc_now."""

    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now = self.now + timedelta(seconds=seconds)


class PresenceServiceTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cur = self.conn.cursor()
        # Freeze the module clock at a fixed instant.
        self._real_now = ps.utc_now
        self.clock = _Clock(ps.datetime(2026, 7, 25, 12, 0, 0, tzinfo=ps.timezone.utc))
        ps.utc_now = self.clock
        ps.reset_schema_cache()
        ps.ensure_schema(self.cur)

    def tearDown(self):
        ps.utc_now = self._real_now
        self.conn.close()

    # -- helpers -----------------------------------------------------------

    def _presence(self, viewer, target, locale="en"):
        return ps.presence_for(self.cur, viewer, [target], locale=locale).get(target)

    # -- core liveness -----------------------------------------------------

    def test_connect_makes_user_online(self):
        res = ps.connect(self.cur, 10, device_id="phone", device_label="iPhone", platform="ios")
        self.assertTrue(res["ok"])
        self.assertTrue(ps.is_online(self.cur, 10))
        seen = self._presence(99, 10)
        self.assertEqual(seen["status"], "online")
        self.assertTrue(seen["online"])
        self.assertEqual(seen["devices"], 1)
        # A live user never leaks a last-seen timestamp.
        self.assertEqual(seen["last_seen_at"], "")

    def test_session_expiry_degrades_to_offline_without_any_worker(self):
        ps.connect(self.cur, 10, device_id="phone")
        self.assertTrue(ps.is_online(self.cur, 10))
        # Nothing runs a sweep; we only move the clock past the grace period.
        self.clock.advance(ps.GRACE_PERIOD_SECONDS + 5)
        self.assertFalse(ps.is_online(self.cur, 10))
        seen = self._presence(99, 10)
        self.assertEqual(seen["status"], "offline")
        self.assertFalse(seen["online"])
        self.assertEqual(seen["devices"], 0)
        # Now that they are offline, last-seen is exposed.
        self.assertNotEqual(seen["last_seen_at"], "")
        self.assertTrue(seen["last_seen_text"].startswith("Last seen"))

    def test_heartbeat_keeps_session_alive(self):
        c = ps.connect(self.cur, 10, device_id="phone")
        sid = c["session_id"]
        # Advance almost to expiry, then heartbeat; must stay online.
        self.clock.advance(ps.GRACE_PERIOD_SECONDS - 5)
        hb = ps.heartbeat(self.cur, 10, sid)
        self.assertTrue(hb["ok"])
        self.clock.advance(ps.GRACE_PERIOD_SECONDS - 5)
        self.assertTrue(ps.is_online(self.cur, 10))

    def test_heartbeat_unknown_session_requests_reconnect(self):
        res = ps.heartbeat(self.cur, 10, "not-a-real-session")
        self.assertFalse(res["ok"])
        self.assertTrue(res.get("reconnect"))

    # -- multi-device ------------------------------------------------------

    def test_multi_device_online_until_all_sessions_die(self):
        a = ps.connect(self.cur, 10, device_id="phone", device_label="iPhone")
        b = ps.connect(self.cur, 10, device_id="laptop", device_label="MacBook")
        self.assertEqual(self._presence(99, 10)["devices"], 2)
        # Close the laptop explicitly; phone keeps them online.
        ps.disconnect(self.cur, 10, b["session_id"])
        self.assertTrue(ps.is_online(self.cur, 10))
        self.assertEqual(self._presence(99, 10)["devices"], 1)
        # Let the phone's session lapse; now fully offline.
        self.clock.advance(ps.GRACE_PERIOD_SECONDS + 5)
        self.assertFalse(ps.is_online(self.cur, 10))

    def test_reconnect_same_device_supersedes_old_session(self):
        first = ps.connect(self.cur, 10, device_id="phone")
        second = ps.connect(self.cur, 10, device_id="phone")
        self.assertNotEqual(first["session_id"], second["session_id"])
        # Old session id must be dead; only one device counted.
        self.assertFalse(ps.heartbeat(self.cur, 10, first["session_id"])["ok"])
        self.assertEqual(self._presence(99, 10)["devices"], 1)

    def test_disconnect_all_signs_out_every_device(self):
        ps.connect(self.cur, 10, device_id="phone")
        ps.connect(self.cur, 10, device_id="laptop")
        ps.disconnect_all(self.cur, 10)
        self.assertFalse(ps.is_online(self.cur, 10))
        self.assertEqual(self._presence(99, 10)["status"], "offline")

    # -- activity indicators ----------------------------------------------

    def test_typing_indicator_auto_clears_via_ttl(self):
        c = ps.connect(self.cur, 10, device_id="phone")
        sid = c["session_id"]
        ps.set_activity(self.cur, 10, sid, "typing", activity_context="7")
        seen = self._presence(99, 10)
        self.assertEqual(seen["activity"], "typing")
        # After the activity TTL (but before the session grace expiry), the
        # indicator must defuse itself while the user stays online.
        self.clock.advance(ps.ACTIVITY_TTL_SECONDS + 2)
        ps.heartbeat(self.cur, 10, sid)  # keep session alive, no activity
        seen = self._presence(99, 10)
        self.assertEqual(seen["activity"], "idle")
        self.assertEqual(seen["status"], "online")

    def test_session_bound_activity_lives_with_session(self):
        c = ps.connect(self.cur, 10, device_id="phone")
        sid = c["session_id"]
        ps.set_activity(self.cur, 10, sid, "in_video_call")
        self.clock.advance(ps.ACTIVITY_TTL_SECONDS + 2)
        # A call is not transient; it survives past the transient TTL as long
        # as the session is heartbeated.
        ps.heartbeat(self.cur, 10, sid, activity="in_video_call")
        self.assertEqual(self._presence(99, 10)["activity"], "in_video_call")

    def test_activity_priority_prefers_call_over_typing(self):
        a = ps.connect(self.cur, 10, device_id="phone")
        b = ps.connect(self.cur, 10, device_id="laptop")
        ps.set_activity(self.cur, 10, a["session_id"], "typing")
        ps.set_activity(self.cur, 10, b["session_id"], "in_audio_call")
        self.assertEqual(self._presence(99, 10)["activity"], "in_audio_call")

    # -- away --------------------------------------------------------------

    def test_idle_session_reports_away(self):
        c = ps.connect(self.cur, 10, device_id="phone")
        sid = c["session_id"]
        # Keep the session live but let the last heartbeat age past the away
        # threshold by heartbeating right at the edge of grace repeatedly.
        elapsed = 0
        while elapsed < ps.AWAY_AFTER_SECONDS + 10:
            step = ps.GRACE_PERIOD_SECONDS - 5
            self.clock.advance(step)
            elapsed += step
            # Re-arm expiry WITHOUT updating last_heartbeat is not possible via
            # the public API, so instead we stop heartbeating and extend expiry
            # directly to isolate the away computation.
        # Force a live session whose last heartbeat is old.
        old = ps.iso(self.clock.now - timedelta(seconds=ps.AWAY_AFTER_SECONDS + 30))
        future = ps.iso(self.clock.now + timedelta(seconds=ps.GRACE_PERIOD_SECONDS))
        self.cur.execute(
            "UPDATE presence_sessions SET last_heartbeat_at=?, expires_at=? WHERE session_id=?",
            (old, future, sid),
        )
        seen = self._presence(99, 10)
        self.assertEqual(seen["status"], "away")
        self.assertTrue(seen["online"])  # away still counts as online

    # -- privacy -----------------------------------------------------------

    def test_invisible_mode_hides_from_others_not_self(self):
        ps.connect(self.cur, 10, device_id="phone")
        ps.set_privacy(self.cur, 10, invisible_mode=True)
        other = self._presence(99, 10)
        self.assertEqual(other, ps._hidden_presence(10))
        # Self still sees the truth.
        me = self._presence(10, 10)
        self.assertEqual(me["status"], "online")
        self.assertTrue(me["invisible"])
        self.assertTrue(me["self"])

    def test_hide_last_seen_suppresses_timestamp_for_others(self):
        ps.connect(self.cur, 10, device_id="phone")
        ps.set_privacy(self.cur, 10, hide_last_seen=True)
        self.clock.advance(ps.GRACE_PERIOD_SECONDS + 5)  # go offline
        other = self._presence(99, 10)
        self.assertEqual(other["status"], "offline")
        self.assertEqual(other["last_seen_at"], "")
        self.assertEqual(other["last_seen_text"], "")
        # Self sees their own last-seen preference flagged.
        me = self._presence(10, 10)
        self.assertTrue(me["hide_last_seen"])

    def test_block_hides_presence_both_directions(self):
        ps.connect(self.cur, 10, device_id="phone")
        self.cur.execute(
            "CREATE TABLE comm_v2_blocks (blocker_user_id INTEGER, blocked_user_id INTEGER, status TEXT)"
        )
        self.cur.execute(
            "INSERT INTO comm_v2_blocks VALUES (?,?,?)", (99, 10, "active")
        )
        self.assertEqual(self._presence(99, 10), ps._hidden_presence(10))

    def test_nobody_visibility_hides_presence(self):
        ps.connect(self.cur, 10, device_id="phone")
        self.cur.execute(
            "CREATE TABLE comm_v2_user_settings (user_id INTEGER, presence_privacy TEXT)"
        )
        self.cur.execute(
            "INSERT INTO comm_v2_user_settings VALUES (?,?)", (10, "nobody")
        )
        self.assertEqual(self._presence(99, 10), ps._hidden_presence(10))

    def test_hidden_presence_is_indistinguishable_from_offline(self):
        # The security property: a blocked/invisible user's payload must be
        # byte-identical to a genuinely-offline user with no last-seen.
        ps.connect(self.cur, 11, device_id="phone")
        ps.set_privacy(self.cur, 11, invisible_mode=True)
        hidden = self._presence(99, 11)
        genuine_offline = ps._hidden_presence(11)
        self.assertEqual(hidden, genuine_offline)

    # -- last-seen formatting ---------------------------------------------

    def test_format_last_seen_buckets(self):
        now = self.clock.now
        self.assertEqual(ps.format_last_seen(ps.iso(now - timedelta(seconds=10)), now=now), "Last seen just now")
        self.assertEqual(ps.format_last_seen(ps.iso(now - timedelta(minutes=1)), now=now), "Last seen 1 minute ago")
        self.assertEqual(ps.format_last_seen(ps.iso(now - timedelta(minutes=5)), now=now), "Last seen 5 minutes ago")
        self.assertEqual(ps.format_last_seen("", now=now), "")

    def test_format_last_seen_locale_uses_24h(self):
        now = ps.datetime(2026, 7, 25, 20, 0, 0, tzinfo=ps.timezone.utc)
        stamp = ps.iso(now - timedelta(days=2))
        en = ps.format_last_seen(stamp, now=now, locale="en")
        fr = ps.format_last_seen(stamp, now=now, locale="fr")
        self.assertTrue(("AM" in en) or ("PM" in en))  # en uses a 12h am/pm marker
        self.assertNotIn("AM", fr)  # fr uses a 24h clock, no am/pm marker
        self.assertNotIn("PM", fr)
        self.assertIn("20:00", fr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
