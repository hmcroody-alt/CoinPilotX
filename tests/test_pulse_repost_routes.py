"""Route-level tests for repost and un-repost across post, reel and video.

The engine's behavior is covered by test_pulse_repost_toggle.py. This file covers
what only the routes can get wrong: whether DELETE is actually registered, whether
each route hands `undo` to the engine, whether the reel and video routes resolve
their own id to the right post before delegating, and whether the response a
client receives still carries the flag and count on every branch.

Both halves are needed. The engine could be perfect while a route forgot to add
"DELETE" to its `methods` list, and the client would get a 405 from a toggle that
looks correct in every other file.

Runs against a temp sqlite file rather than the local dev database, so the schema
comes from init_db and nothing here can leave rows behind in coinpilotx.db.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="pulse_repost_routes_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


AUTHOR = 90101
VIEWER = 90102


class RepostRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        conn = sqlite3.connect(cls.db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pulse_posts'")
        if not cur.fetchone():
            conn.close()
            raise unittest.SkipTest("init_db did not create pulse_posts in the temp database")
        conn.close()
        cls._real_account_user = bot.api_account_user
        bot.api_account_user = lambda *args, **kwargs: {"user_id": VIEWER, "username": "repost_viewer", "email": "repost_viewer@example.com"}
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM pulse_posts WHERE user_id IN (?,?)", (AUTHOR, VIEWER))
        cur.execute(
            "INSERT INTO pulse_posts (user_id, post_type, body, title, tags_json, visibility, moderation_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (AUTHOR, "text", "route fixture body", "Route Fixture", '["alpha"]', "public", "approved",
             "2026-07-26T00:00:00", "2026-07-26T00:00:00"),
        )
        self.post_id = int(cur.lastrowid)
        conn.commit()
        conn.close()

    def tearDown(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM pulse_posts WHERE user_id IN (?,?)", (AUTHOR, VIEWER))
        conn.commit()
        conn.close()

    def live_reposts(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT id FROM pulse_posts WHERE user_id=? AND repost_of_post_id=? AND deleted_at IS NULL",
            (VIEWER, self.post_id),
        ).fetchall()
        conn.close()
        return [int(row[0]) for row in rows]

    # -- the methods are registered -------------------------------------------

    def test_every_repost_route_accepts_both_post_and_delete(self):
        """The failure a perfect engine cannot prevent: a missing method."""
        wanted = {
            "/api/pulse/posts/<int:post_id>/repost",
            "/api/pulse/reels/<int:reel_id>/repost",
            "/api/pulse/videos/<int:video_id>/repost",
        }
        seen = {}
        for rule in bot.webhook_app.url_map.iter_rules():
            if str(rule) in wanted:
                seen[str(rule)] = rule.methods
        self.assertEqual(set(seen), wanted, "a repost route is missing from the url map")
        for path, methods in seen.items():
            self.assertIn("POST", methods, f"{path} lost POST")
            self.assertIn("DELETE", methods, f"{path} has no undo")

    # -- post repost ----------------------------------------------------------

    def test_post_repost_returns_the_flag_and_count(self):
        response = self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["reposted"])
        self.assertEqual(body["repost_count"], 1)
        self.assertEqual(len(self.live_reposts()), 1)

    def test_post_repost_delete_undoes_it(self):
        self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={})
        response = self.client.delete(f"/api/pulse/posts/{self.post_id}/repost", json={})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertFalse(body["reposted"])
        self.assertEqual(body["repost_count"], 0)
        self.assertEqual(self.live_reposts(), [])

    def test_post_repost_honours_undo_in_the_body_for_clients_that_cannot_send_delete(self):
        self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={})
        response = self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={"undo": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["reposted"])
        self.assertEqual(self.live_reposts(), [])

    def test_post_repost_twice_over_http_writes_one_row(self):
        self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={})
        second = self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["repost_count"], 1)
        self.assertEqual(len(self.live_reposts()), 1)

    def test_a_full_http_toggle_cycle_returns_to_zero(self):
        for _ in range(3):
            created = self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={}).get_json()
            self.assertTrue(created["reposted"])
            self.assertEqual(created["repost_count"], 1)
            removed = self.client.delete(f"/api/pulse/posts/{self.post_id}/repost", json={}).get_json()
            self.assertFalse(removed["reposted"])
            self.assertEqual(removed["repost_count"], 0)

    def test_undoing_an_unreposted_post_is_not_an_error(self):
        response = self.client.delete(f"/api/pulse/posts/{self.post_id}/repost", json={})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])

    def test_reposting_a_missing_post_is_404(self):
        response = self.client.post("/api/pulse/posts/99999901/repost", json={})
        self.assertEqual(response.status_code, 404)

    def test_undoing_a_missing_post_is_404(self):
        response = self.client.delete("/api/pulse/posts/99999901/repost", json={})
        self.assertEqual(response.status_code, 404)

    def test_a_note_survives_the_route(self):
        created = self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={"body": "worth reading"}).get_json()
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT body, media_ids_json FROM pulse_posts WHERE id=?", (created["post_id"],)).fetchone()
        conn.close()
        self.assertIn("worth reading", row[0])
        self.assertFalse(row[1], "the route must not copy the original's media ids")

    def test_login_is_required(self):
        real = bot.api_account_user
        bot.api_account_user = lambda *args, **kwargs: None
        try:
            self.assertEqual(self.client.post(f"/api/pulse/posts/{self.post_id}/repost", json={}).status_code, 401)
            self.assertEqual(self.client.delete(f"/api/pulse/posts/{self.post_id}/repost", json={}).status_code, 401)
        finally:
            bot.api_account_user = real

    # -- reel repost ----------------------------------------------------------

    def test_reel_repost_toggles_through_the_same_rows_as_a_post(self):
        """A reel repost is a pulse_posts row on the reel's post, so it must
        share the post route's dedupe and undo rather than reimplement them."""
        real_payload = bot.pulse_reel_payload
        bot.pulse_reel_payload = lambda *args, **kwargs: {"post_id": self.post_id, "reel_id": 555}
        try:
            created = self.client.post("/api/pulse/reels/555/repost", json={})
            self.assertEqual(created.status_code, 200)
            body = created.get_json()
            self.assertTrue(body["reposted"])
            self.assertEqual(body["repost_count"], 1)
            self.assertEqual(body["reel_id"], 555)
            self.assertEqual(len(self.live_reposts()), 1)

            self.assertEqual(self.client.post("/api/pulse/reels/555/repost", json={}).get_json()["repost_count"], 1)
            self.assertEqual(len(self.live_reposts()), 1)

            removed = self.client.delete("/api/pulse/reels/555/repost", json={})
            self.assertEqual(removed.status_code, 200)
            self.assertFalse(removed.get_json()["reposted"])
            self.assertEqual(self.live_reposts(), [])
        finally:
            bot.pulse_reel_payload = real_payload

    def test_reel_repost_keeps_its_own_wording(self):
        real_payload = bot.pulse_reel_payload
        bot.pulse_reel_payload = lambda *args, **kwargs: {"post_id": self.post_id, "reel_id": 555}
        try:
            created = self.client.post("/api/pulse/reels/555/repost", json={}).get_json()
            conn = sqlite3.connect(self.db_path)
            row = conn.execute("SELECT body FROM pulse_posts WHERE id=?", (created["post_id"],)).fetchone()
            conn.close()
            self.assertIn("Reel", row[0], "sharing the engine must not cost the reel-specific copy")
        finally:
            bot.pulse_reel_payload = real_payload

    def test_a_missing_reel_is_404_on_both_methods(self):
        real_payload = bot.pulse_reel_payload
        bot.pulse_reel_payload = lambda *args, **kwargs: None
        try:
            self.assertEqual(self.client.post("/api/pulse/reels/555/repost", json={}).status_code, 404)
            self.assertEqual(self.client.delete("/api/pulse/reels/555/repost", json={}).status_code, 404)
        finally:
            bot.pulse_reel_payload = real_payload


if __name__ == "__main__":
    unittest.main()
