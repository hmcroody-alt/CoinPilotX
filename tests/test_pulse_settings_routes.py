"""Tests for the PulseSoc native settings backend.

Two layers are covered, because they fail in different ways.

The *normalizer* is pure and is tested directly. It is the server's half of a
pair — `mobile-native/src/settings/schema.ts` is the other half — and the
property that matters is totality: any input at all, including a document from
a future app version or a crafted one, must produce a complete, in-range
document. A normalizer that is merely "usually right" is indistinguishable from
a correct one in review and produces impossible states in production.

The *routes* are tested through a real Flask test client with `bot` replaced by
a fake. That is deliberate: importing the real `bot` module costs tens of
thousands of lines and a live config, and none of it is under test here. What is
under test is that each endpoint reads the caller's own rows and nobody else's,
returns the envelope the client parses, and fails in the direction the client
expects — which the fake exercises exactly as the real module would.

Run: python3 -m pytest tests/test_pulse_settings_routes.py
"""

import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

from services import pulse_settings_routes as settings_routes  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

class _KeepAliveConnection:
    """Proxy that swallows `close()`.

    `_with_db` closes the connection it opened, which for an in-memory database
    would discard the schema between requests. Everything else is delegated, so
    the transaction semantics under test (commit on success, rollback on error)
    are the real ones.
    """

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)


class _FakeBot:
    """The surface `pulse_settings_routes` actually uses from `bot`."""

    def __init__(self, conn):
        self.conn = conn
        self.current_user = None

    def db(self):
        return _KeepAliveConnection(self.conn)

    def api_account_user(self):
        return self.current_user

    def mobile_token_hash(self, token):
        import hashlib

        return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()

    def ensure_mobile_security_session_schema(self, cur):
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_security_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                device_hash TEXT,
                device_label TEXT,
                refresh_token_hash TEXT,
                access_token_hash TEXT,
                status TEXT DEFAULT 'active',
                platform TEXT,
                country TEXT,
                created_at TEXT,
                last_seen_at TEXT,
                revoked_at TEXT,
                revoked_reason TEXT
            )
            """
        )


class _RoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                display_name TEXT,
                email TEXT,
                avatar_url TEXT,
                preferred_language TEXT,
                profile_visibility TEXT
            )
            """
        )
        for user_id, username in ((1, "roody"), (2, "mara"), (3, "kito")):
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, email, preferred_language, profile_visibility)"
                " VALUES (?,?,?,?,?,?)",
                (user_id, username, username.title(), f"{username}@example.com", "en", "public"),
            )
        settings_routes.ensure_settings_schema(cur)
        self.conn.commit()

        self.bot = _FakeBot(self.conn)
        self._real_bot = settings_routes._bot
        settings_routes._bot = lambda: self.bot

        app = Flask(__name__)
        app.config["TESTING"] = True
        settings_routes.register(app)
        self.client = app.test_client()
        self.sign_in(1)

    def tearDown(self):
        settings_routes._bot = self._real_bot
        self.conn.close()

    def sign_in(self, user_id):
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        self.bot.current_user = dict(cur.fetchone())

    def sign_out(self):
        self.bot.current_user = None

    def body(self, response):
        return json.loads(response.data.decode("utf-8"))


# --------------------------------------------------------------------------
# Normalizer
# --------------------------------------------------------------------------

class NormalizePreferencesTest(unittest.TestCase):
    def test_defaults_are_a_fixed_point(self):
        # If normalizing the defaults changed them, the defaults would be
        # invalid by the server's own rules and every fresh account would
        # immediately disagree with what it was given.
        defaults = settings_routes.default_preferences()
        self.assertEqual(settings_routes.normalize_preferences(defaults), defaults)

    def test_is_total_over_junk_input(self):
        expected = settings_routes.default_preferences()
        for junk in (None, [], "settings", 7, {"appearance": "dark"}, {"privacy": ["public"]}):
            self.assertEqual(settings_routes.normalize_preferences(junk), expected, junk)

    def test_rejects_out_of_range_values_without_dropping_the_key(self):
        result = settings_routes.normalize_preferences(
            {
                "appearance": {"theme": "midnight", "fontScale": 9000},
                "storage": {"cacheLimitMb": -5, "mediaQuality": "ultra"},
                "privacy": {"lastSeen": "friends-of-friends"},
                "notifications": {"quietHoursStart": "25:00", "quietHoursEnd": "7:5"},
            }
        )
        self.assertEqual(result["appearance"]["theme"], "system")
        self.assertEqual(result["appearance"]["fontScale"], settings_routes.FONT_SCALE_MAX)
        self.assertEqual(result["storage"]["cacheLimitMb"], settings_routes.CACHE_LIMIT_MIN_MB)
        self.assertEqual(result["storage"]["mediaQuality"], "auto")
        self.assertEqual(result["privacy"]["lastSeen"], "followers")
        self.assertEqual(result["notifications"]["quietHoursStart"], "22:00")
        self.assertEqual(result["notifications"]["quietHoursEnd"], "07:00")

    def test_font_scale_snaps_to_the_step_the_slider_uses(self):
        # An unsnapped value round-trips as a slider thumb that sits between
        # detents and drifts a little further on every save.
        result = settings_routes.normalize_preferences({"appearance": {"fontScale": 1.13}})
        self.assertEqual(result["appearance"]["fontScale"], 1.15)

    def test_accepts_the_wire_forms_a_client_may_send_for_booleans(self):
        result = settings_routes.normalize_preferences(
            {"privacy": {"onlineStatus": "false", "readReceipts": 0, "searchableByPhone": "yes"}}
        )
        self.assertIs(result["privacy"]["onlineStatus"], False)
        self.assertIs(result["privacy"]["readReceipts"], False)
        self.assertIs(result["privacy"]["searchableByPhone"], True)

    def test_every_notification_category_is_always_present(self):
        result = settings_routes.normalize_preferences({"notifications": {"categories": {"likes": {"push": False}}}})
        self.assertEqual(set(result["notifications"]["categories"]), set(settings_routes.NOTIFICATION_CATEGORIES))
        self.assertIs(result["notifications"]["categories"]["likes"]["push"], False)
        # An unnamed category keeps its default rather than being reset to all-off.
        self.assertIs(result["notifications"]["categories"]["security"]["email"], True)

    def test_unknown_categories_and_groups_are_discarded(self):
        result = settings_routes.normalize_preferences(
            {"notifications": {"categories": {"telepathy": {"push": True}}}, "cryptomining": {"enabled": True}}
        )
        self.assertNotIn("telepathy", result["notifications"]["categories"])
        self.assertNotIn("cryptomining", result)

    def test_language_tags_are_validated_and_deduplicated(self):
        result = settings_routes.normalize_preferences(
            {"language": {"appLanguage": "PT-BR", "contentLanguages": ["en", "EN", "!!", "fr"]}}
        )
        self.assertEqual(result["language"]["appLanguage"], "pt-br")
        self.assertEqual(result["language"]["contentLanguages"], ["en", "fr"])

    def test_empty_content_languages_fall_back_rather_than_leaving_none(self):
        # An empty list would mean "translate nothing and show nothing", which
        # is not a state any control in the app can produce or undo.
        result = settings_routes.normalize_preferences({"language": {"contentLanguages": []}})
        self.assertEqual(result["language"]["contentLanguages"], ["en"])


class MergePreferencesTest(unittest.TestCase):
    def test_patch_touches_only_the_groups_it_names(self):
        stored = settings_routes.normalize_preferences({"privacy": {"onlineStatus": False}})
        merged = settings_routes.merge_preferences(stored, {"appearance": {"theme": "dark"}})
        self.assertEqual(merged["appearance"]["theme"], "dark")
        self.assertIs(merged["privacy"]["onlineStatus"], False)

    def test_partial_group_keeps_sibling_keys(self):
        # This is what protects an older app build: it PATCHes the four keys it
        # knows about and must not clear the ones added after it shipped.
        stored = settings_routes.normalize_preferences({"appearance": {"theme": "dark", "compactDensity": True}})
        merged = settings_routes.merge_preferences(stored, {"appearance": {"fontScale": 1.2}})
        self.assertEqual(merged["appearance"]["theme"], "dark")
        self.assertIs(merged["appearance"]["compactDensity"], True)
        self.assertEqual(merged["appearance"]["fontScale"], 1.2)

    def test_category_patch_merges_channel_by_channel(self):
        stored = settings_routes.default_preferences()
        merged = settings_routes.merge_preferences(stored, {"notifications": {"categories": {"security": {"push": False}}}})
        self.assertIs(merged["notifications"]["categories"]["security"]["push"], False)
        self.assertIs(merged["notifications"]["categories"]["security"]["email"], True)
        self.assertIs(merged["notifications"]["categories"]["likes"]["push"], True)


# --------------------------------------------------------------------------
# Preference endpoints
# --------------------------------------------------------------------------

class PreferenceEndpointTest(_RoutesTestCase):
    def test_get_returns_defaults_at_revision_zero_for_a_new_account(self):
        payload = self.body(self.client.get(settings_routes.API_PREFIX))
        self.assertEqual(payload["preferences"], settings_routes.default_preferences())
        self.assertEqual(payload["revision"], 0)
        self.assertIsNone(payload["updated_at"])

    def test_patch_persists_and_advances_the_revision(self):
        first = self.body(
            self.client.patch(settings_routes.API_PREFIX, json={"preferences": {"appearance": {"theme": "dark"}}, "revision": 0})
        )
        self.assertEqual(first["preferences"]["appearance"]["theme"], "dark")
        self.assertEqual(first["revision"], 1)
        self.assertIsNotNone(first["updated_at"])

        # Persistence is the whole point: a fresh read must agree.
        reread = self.body(self.client.get(settings_routes.API_PREFIX))
        self.assertEqual(reread["preferences"]["appearance"]["theme"], "dark")
        self.assertEqual(reread["revision"], 1)

        second = self.body(
            self.client.patch(settings_routes.API_PREFIX, json={"preferences": {"privacy": {"onlineStatus": False}}, "revision": 1})
        )
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["preferences"]["appearance"]["theme"], "dark")
        self.assertIs(second["preferences"]["privacy"]["onlineStatus"], False)

    def test_patch_sanitises_hostile_values_rather_than_storing_them(self):
        payload = self.body(
            self.client.patch(
                settings_routes.API_PREFIX,
                json={"preferences": {"privacy": {"accountVisibility": "'; DROP TABLE users; --"}}, "revision": 0},
            )
        )
        self.assertEqual(payload["preferences"]["privacy"]["accountVisibility"], "public")
        cur = self.conn.cursor()
        stored = json.loads(settings_routes._read_setting(cur, 1, settings_routes.PREFERENCES_KEY))
        self.assertEqual(stored["privacy"]["accountVisibility"], "public")

    def test_a_stale_revision_still_applies_the_groups_it_names(self):
        # Two devices, one offline for a while. Losing the second device's theme
        # change because its revision counter lagged would be a worse outcome
        # than last-write-wins on the group it actually edited.
        self.client.patch(settings_routes.API_PREFIX, json={"preferences": {"privacy": {"readReceipts": False}}, "revision": 0})
        payload = self.body(
            self.client.patch(settings_routes.API_PREFIX, json={"preferences": {"appearance": {"theme": "light"}}, "revision": 0})
        )
        self.assertEqual(payload["preferences"]["appearance"]["theme"], "light")
        self.assertIs(payload["preferences"]["privacy"]["readReceipts"], False)
        self.assertEqual(payload["revision"], 2)

    def test_empty_and_unknown_patches_are_rejected_as_permanent(self):
        for patch in ({}, {"cryptomining": {"enabled": True}}):
            response = self.client.patch(settings_routes.API_PREFIX, json={"preferences": patch, "revision": 0})
            # The client treats 4xx as permanent and stops retrying, which is
            # correct here: neither request will ever become valid.
            self.assertEqual(response.status_code, 400, patch)

    def test_preferences_are_per_account(self):
        self.client.patch(settings_routes.API_PREFIX, json={"preferences": {"appearance": {"theme": "dark"}}, "revision": 0})
        self.sign_in(2)
        payload = self.body(self.client.get(settings_routes.API_PREFIX))
        self.assertEqual(payload["preferences"]["appearance"]["theme"], "system")

    def test_language_and_visibility_are_projected_onto_the_user_row(self):
        # These two are read by the web app and the email templates. A setting
        # that saves but is not applied outside the native app is not saved.
        self.client.patch(
            settings_routes.API_PREFIX,
            json={
                "preferences": {"language": {"appLanguage": "fr"}, "privacy": {"accountVisibility": "private"}},
                "revision": 0,
            },
        )
        cur = self.conn.cursor()
        cur.execute("SELECT preferred_language, profile_visibility FROM users WHERE user_id=1")
        row = dict(cur.fetchone())
        self.assertEqual(row["preferred_language"], "fr")
        self.assertEqual(row["profile_visibility"], "private")

    def test_a_corrupt_stored_document_reads_as_defaults_instead_of_failing(self):
        cur = self.conn.cursor()
        settings_routes._write_setting(cur, 1, settings_routes.PREFERENCES_KEY, "{not json")
        self.conn.commit()
        payload = self.body(self.client.get(settings_routes.API_PREFIX))
        self.assertEqual(payload["preferences"], settings_routes.default_preferences())

    def test_every_endpoint_requires_a_session(self):
        self.sign_out()
        for method, path in (
            ("get", settings_routes.API_PREFIX),
            ("patch", settings_routes.API_PREFIX),
            ("get", f"{settings_routes.API_PREFIX}/blocked"),
            ("post", f"{settings_routes.API_PREFIX}/blocked"),
            ("delete", f"{settings_routes.API_PREFIX}/blocked"),
            ("get", f"{settings_routes.API_PREFIX}/muted"),
            ("post", f"{settings_routes.API_PREFIX}/muted"),
            ("delete", f"{settings_routes.API_PREFIX}/muted"),
            ("get", f"{settings_routes.API_PREFIX}/sessions"),
            ("post", f"{settings_routes.API_PREFIX}/sessions/revoke"),
            ("post", f"{settings_routes.API_PREFIX}/data-export"),
            ("post", f"{settings_routes.API_PREFIX}/delete-account"),
        ):
            response = getattr(self.client, method)(path, json={})
            self.assertEqual(response.status_code, 401, f"{method} {path}")

    def test_responses_are_not_cacheable(self):
        # A cached settings response shown to the next account on a shared
        # proxy, or a stale one shown after a save, are both worse than a round
        # trip.
        response = self.client.get(settings_routes.API_PREFIX)
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_privacy_snapshot_is_what_the_rest_of_the_backend_reads(self):
        self.client.patch(
            settings_routes.API_PREFIX,
            json={"preferences": {"privacy": {"lastSeen": "nobody", "allowDirectMessages": "followers"}}, "revision": 0},
        )
        cur = self.conn.cursor()
        snapshot = settings_routes.privacy_snapshot(cur, 1)
        self.assertEqual(snapshot["lastSeen"], "nobody")
        self.assertEqual(snapshot["allowDirectMessages"], "followers")
        # A user who has never opened Settings still gets an answer, not a KeyError.
        self.assertEqual(settings_routes.privacy_snapshot(cur, 3)["lastSeen"], "followers")


# --------------------------------------------------------------------------
# Blocking and muting
# --------------------------------------------------------------------------

class RelationshipEndpointTest(_RoutesTestCase):
    def block(self, user_id):
        return self.client.post(f"{settings_routes.API_PREFIX}/blocked", json={"user_id": user_id})

    def mute(self, user_id):
        return self.client.post(f"{settings_routes.API_PREFIX}/muted", json={"user_id": user_id})

    def test_block_then_list_then_unblock(self):
        self.assertEqual(self.block(2).status_code, 200)
        payload = self.body(self.client.get(f"{settings_routes.API_PREFIX}/blocked"))
        self.assertEqual([entry["id"] for entry in payload["users"]], [2])
        entry = payload["users"][0]
        # The client's parser reads these snake_case keys specifically.
        self.assertEqual(entry["username"], "mara")
        self.assertEqual(entry["display_name"], "Mara")
        self.assertIsNotNone(entry["created_at"])

        self.assertEqual(self.client.delete(f"{settings_routes.API_PREFIX}/blocked", json={"user_id": 2}).status_code, 200)
        self.assertEqual(self.body(self.client.get(f"{settings_routes.API_PREFIX}/blocked"))["users"], [])

    def test_blocking_is_idempotent(self):
        # The control is a toggle that can retry on a flaky connection; a second
        # POST must be a success, not a conflict.
        self.assertEqual(self.body(self.block(2))["state"], "added")
        self.assertEqual(self.body(self.block(2))["state"], "exists")
        self.assertEqual(len(self.body(self.client.get(f"{settings_routes.API_PREFIX}/blocked"))["users"]), 1)

    def test_unblocking_someone_who_was_never_blocked_succeeds(self):
        self.assertEqual(self.client.delete(f"{settings_routes.API_PREFIX}/blocked", json={"user_id": 3}).status_code, 200)

    def test_block_and_mute_are_independent_relations(self):
        self.block(2)
        self.mute(3)
        self.assertEqual([entry["id"] for entry in self.body(self.client.get(f"{settings_routes.API_PREFIX}/blocked"))["users"]], [2])
        self.assertEqual([entry["id"] for entry in self.body(self.client.get(f"{settings_routes.API_PREFIX}/muted"))["users"]], [3])

    def test_lists_are_scoped_to_the_caller(self):
        self.block(2)
        self.sign_in(3)
        self.assertEqual(self.body(self.client.get(f"{settings_routes.API_PREFIX}/blocked"))["users"], [])

    def test_you_cannot_block_yourself_or_a_missing_account(self):
        self.assertEqual(self.block(1).status_code, 400)
        self.assertEqual(self.block(9999).status_code, 404)
        self.assertEqual(self.block(0).status_code, 400)
        self.assertEqual(self.client.post(f"{settings_routes.API_PREFIX}/blocked", json={}).status_code, 400)

    def test_enforcement_helpers_see_a_block_from_either_side(self):
        self.block(2)
        cur = self.conn.cursor()
        self.assertTrue(settings_routes.is_blocked(cur, 1, 2))
        # Symmetric: the blocked user must not see the blocker either, or the
        # block only hides one direction of a two-way surface.
        self.assertTrue(settings_routes.is_blocked(cur, 2, 1))
        self.assertFalse(settings_routes.is_blocked(cur, 1, 3))
        self.mute(3)
        self.assertTrue(settings_routes.is_muted(cur, 1, 3))
        self.assertFalse(settings_routes.is_muted(cur, 3, 1))


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

class SessionEndpointTest(_RoutesTestCase):
    def setUp(self):
        super().setUp()
        cur = self.conn.cursor()
        self.bot.ensure_mobile_security_session_schema(cur)
        rows = (
            (1, "iPhone 15", "ios", "SE", "active", self.bot.mobile_token_hash("token-a"), "2026-07-25T10:00:00+00:00"),
            (1, "Pixel 8", "android", "NG", "active", self.bot.mobile_token_hash("token-b"), "2026-07-24T10:00:00+00:00"),
            (1, "Old iPad", "ios", "SE", "revoked", self.bot.mobile_token_hash("token-c"), "2026-01-01T10:00:00+00:00"),
            (2, "Someone else", "ios", "SE", "active", self.bot.mobile_token_hash("token-d"), "2026-07-25T10:00:00+00:00"),
        )
        for user_id, label, platform, country, status, token_hash, seen in rows:
            cur.execute(
                "INSERT INTO mobile_security_sessions"
                " (user_id, device_label, platform, country, status, access_token_hash, created_at, last_seen_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (user_id, label, platform, country, status, token_hash, seen, seen),
            )
        self.conn.commit()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_lists_only_the_callers_active_sessions_most_recent_first(self):
        payload = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions", headers=self.auth("token-a")))
        self.assertEqual([entry["device_name"] for entry in payload["sessions"]], ["iPhone 15", "Pixel 8"])
        self.assertEqual([entry["current"] for entry in payload["sessions"]], [True, False])
        self.assertEqual(payload["sessions"][0]["platform"], "ios")
        self.assertEqual(payload["sessions"][0]["location"], "SE")

    def test_no_bearer_token_marks_nothing_current(self):
        # A web caller's session is not in this table, so claiming one of the
        # phones is "this device" would be a lie the user acts on.
        payload = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions"))
        self.assertEqual([entry["current"] for entry in payload["sessions"]], [False, False])

    def test_the_stored_ip_hash_is_never_returned(self):
        payload = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions", headers=self.auth("token-a")))
        self.assertTrue(all(entry["ip_address"] is None for entry in payload["sessions"]))

    def test_revoking_a_session_removes_it_from_the_list(self):
        listed = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions", headers=self.auth("token-a")))
        target = next(entry for entry in listed["sessions"] if entry["device_name"] == "Pixel 8")
        response = self.client.post(f"{settings_routes.API_PREFIX}/sessions/revoke", json={"session_id": target["id"]})
        self.assertEqual(response.status_code, 200)
        remaining = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions", headers=self.auth("token-a")))
        self.assertEqual([entry["device_name"] for entry in remaining["sessions"]], ["iPhone 15"])

    def test_you_cannot_revoke_another_accounts_session(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM mobile_security_sessions WHERE user_id=2 LIMIT 1")
        foreign_id = dict(cur.fetchone())["id"]
        # Without the user_id scope on the UPDATE, knowing any session id would
        # be enough to sign that account out.
        response = self.client.post(f"{settings_routes.API_PREFIX}/sessions/revoke", json={"session_id": str(foreign_id)})
        self.assertEqual(response.status_code, 404)
        cur.execute("SELECT status FROM mobile_security_sessions WHERE id=?", (foreign_id,))
        self.assertEqual(dict(cur.fetchone())["status"], "active")

    def test_revoking_an_already_revoked_session_is_a_404_not_a_500(self):
        response = self.client.post(f"{settings_routes.API_PREFIX}/sessions/revoke", json={"session_id": "424242"})
        self.assertEqual(response.status_code, 404)

    def test_a_missing_session_id_is_rejected(self):
        self.assertEqual(self.client.post(f"{settings_routes.API_PREFIX}/sessions/revoke", json={}).status_code, 400)

    def test_sign_out_everywhere_else_keeps_the_calling_device(self):
        response = self.client.post(
            f"{settings_routes.API_PREFIX}/sessions/revoke", json={"all": True}, headers=self.auth("token-a")
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.body(response)["revoked"], 1)
        remaining = self.body(self.client.get(f"{settings_routes.API_PREFIX}/sessions", headers=self.auth("token-a")))
        self.assertEqual([entry["device_name"] for entry in remaining["sessions"]], ["iPhone 15"])
        # And it must not reach across accounts.
        cur = self.conn.cursor()
        cur.execute("SELECT status FROM mobile_security_sessions WHERE user_id=2")
        self.assertEqual(dict(cur.fetchone())["status"], "active")


# --------------------------------------------------------------------------
# Export and deletion
# --------------------------------------------------------------------------

class DataRequestEndpointTest(_RoutesTestCase):
    def test_export_records_a_pending_request_and_is_not_duplicated(self):
        first = self.body(self.client.post(f"{settings_routes.API_PREFIX}/data-export", json={"source": "native_settings"}))
        self.assertEqual(first["status"], "pending")
        self.assertTrue(first["reference"].startswith("exp_"))
        self.assertIn("roody@example.com", first["message"])

        second = self.body(self.client.post(f"{settings_routes.API_PREFIX}/data-export", json={}))
        self.assertEqual(second["reference"], first["reference"])
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM pulse_account_data_requests WHERE user_id=1 AND request_type='export'")
        self.assertEqual(dict(cur.fetchone())["total"], 1)

    def test_deletion_requires_the_typed_confirmation(self):
        for payload in ({}, {"confirmation": "delete"}, {"confirmation": "yes"}):
            response = self.client.post(f"{settings_routes.API_PREFIX}/delete-account", json=payload)
            self.assertEqual(response.status_code, 400, payload)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM pulse_account_data_requests WHERE request_type='deletion'")
        self.assertEqual(dict(cur.fetchone())["total"], 0)

    def test_deletion_is_scheduled_with_a_grace_period(self):
        payload = self.body(self.client.post(f"{settings_routes.API_PREFIX}/delete-account", json={"confirmation": "DELETE"}))
        self.assertTrue(payload["reference"].startswith("del_"))
        self.assertIsNotNone(payload["scheduled_for"])
        cur = self.conn.cursor()
        cur.execute("SELECT status, scheduled_for FROM pulse_account_data_requests WHERE user_id=1 AND request_type='deletion'")
        row = dict(cur.fetchone())
        self.assertEqual(row["status"], "pending")
        self.assertGreater(row["scheduled_for"], settings_routes._now())

    def test_signing_back_in_cancels_a_pending_deletion(self):
        # The screen tells the user this in as many words, so it has to be true.
        self.client.post(f"{settings_routes.API_PREFIX}/delete-account", json={"confirmation": "DELETE"})
        cur = self.conn.cursor()
        self.assertEqual(settings_routes.cancel_pending_deletion(cur, 1), 1)
        self.conn.commit()
        cur.execute("SELECT status, cancelled_at FROM pulse_account_data_requests WHERE user_id=1")
        row = dict(cur.fetchone())
        self.assertEqual(row["status"], "cancelled")
        self.assertIsNotNone(row["cancelled_at"])
        # And cancelling again is a no-op rather than an error.
        self.assertEqual(settings_routes.cancel_pending_deletion(cur, 1), 0)

    def test_a_cancelled_request_does_not_block_a_new_one(self):
        self.client.post(f"{settings_routes.API_PREFIX}/delete-account", json={"confirmation": "DELETE"})
        cur = self.conn.cursor()
        settings_routes.cancel_pending_deletion(cur, 1)
        self.conn.commit()
        payload = self.body(self.client.post(f"{settings_routes.API_PREFIX}/delete-account", json={"confirmation": "DELETE"}))
        self.assertEqual(payload["status"], "pending")
        cur.execute("SELECT COUNT(*) AS total FROM pulse_account_data_requests WHERE user_id=1 AND status='pending'")
        self.assertEqual(dict(cur.fetchone())["total"], 1)

    def test_requests_are_per_account(self):
        self.client.post(f"{settings_routes.API_PREFIX}/data-export", json={})
        self.sign_in(2)
        payload = self.body(self.client.post(f"{settings_routes.API_PREFIX}/data-export", json={}))
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM pulse_account_data_requests WHERE request_type='export'")
        self.assertEqual(dict(cur.fetchone())["total"], 2)
        self.assertIn("mara@example.com", payload["message"])


if __name__ == "__main__":
    unittest.main()
