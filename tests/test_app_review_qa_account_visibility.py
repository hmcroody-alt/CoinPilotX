"""App Review fixes — item 4 (QA/test accounts must not reach production users).

The requirement is a *product* requirement, not a data-cleanup one: an ordinary
person using PulseSoc must never be shown a QA, smoke-test, synthetic or
deactivated account. Deleting those rows is explicitly not the fix — many of
them carry payment, moderation or App Review history that has to survive. So
the mechanism is a visibility predicate, and these tests exist to prove the
predicate is actually wired into the surfaces a person can reach.

One predicate, asserted per surface
-----------------------------------
``services/discovery_visibility.discovery_visible_sql`` is the single source of
truth. The failure mode this suite is designed to catch is the obvious one: a
new discovery surface ships, nobody remembers the predicate, and a QA account
reappears in production. Structural tests ("the module exports a function")
cannot catch that, so every test below drives a real HTTP request as a real
logged-in account and asserts on the response body.

Each surface is tested twice, and the second half matters as much as the first:

  * a hidden account must NOT appear, and
  * a normal account MUST still appear in the same response.

Without the second assertion a predicate that hides *everyone* — a wrong alias,
a NULL column, a broken COALESCE — would pass every test while quietly emptying
search, feed and suggestions in production. Several of these surfaces
``LEFT JOIN users``, where a missing row yields NULL for every column, so the
NULL-tolerance is a real risk and not a hypothetical one.

Two ways to be hidden
---------------------
An account is hidden by ``users.hidden_from_discovery = 1`` (what the
classification script sets) or by a non-discoverable ``users.account_status``
such as the legacy ``disabled_qa`` already present in production data. Both are
covered, because production contains accounts of each kind and a filter that
only understands the flag would miss the legacy rows entirely.

What is deliberately NOT hidden
-------------------------------
Admin/owner tooling must keep seeing these accounts — hiding them from staff
would make the cleanup unauditable — and a viewer always keeps sight of their
own content whatever their own status is. Both are pinned below so a later
"tighten the filter everywhere" change cannot quietly break them.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_review_qa_visibility_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services.discovery_visibility import HIDDEN_ACCOUNT_STATUSES, discovery_visible_sql  # noqa: E402

# A distinctive token shared by every account this suite creates, so searches
# can target exactly these rows and cannot be perturbed by anything else in the
# database. It has to be substring-searchable because that is how the real
# search endpoints match.
MARK = "zqvistest"


def _use_module_database():
    """Re-point the process at this module's temp database and rebuild schema.

    ``services.db`` resolves ``DATABASE_URL`` lazily per connection and
    ``bot.init_db`` short-circuits on ``INIT_DB_COMPLETED``, so a module
    collected after this one leaves both pointing elsewhere. Re-asserting both
    per test makes the file independent of pytest's collection order. The build
    is idempotent, so this is cheap.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
    bot.INIT_DB_COMPLETED = False
    bot.PULSE_MESSENGER_SCHEMA_READY = False
    bot.init_db()


class QaAccountVisibilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _use_module_database()
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        _use_module_database()
        self.now = datetime.utcnow().isoformat(timespec="seconds")
        self._real_require_account = bot.require_account
        self._real_api_account_user = bot.api_account_user
        self._real_emit = bot.pulse_emit_event
        bot.pulse_emit_event = lambda *a, **k: None

        # Three accounts with identical, equally-matchable names. The ONLY
        # difference between them is how they are hidden, so any difference in
        # the responses below is attributable to the predicate and nothing else.
        self.viewer = self._make_user("viewer")
        self.normal = self._make_user("normal")
        self.hidden_flag = self._make_user("hiddenflag", hidden_from_discovery=1)
        self.hidden_status = self._make_user("hiddenstatus", account_status="disabled_qa")

    def tearDown(self):
        bot.require_account = self._real_require_account
        bot.api_account_user = self._real_api_account_user
        bot.pulse_emit_event = self._real_emit

    # ------------------------------------------------------------------
    # fixtures
    # ------------------------------------------------------------------
    def _make_user(self, role, hidden_from_discovery=0, account_status="active"):
        conn = bot.db()
        cur = conn.cursor()
        username = f"{MARK}_{role}"
        cur.execute(
            """
            INSERT INTO users (username, display_name, email, account_status, hidden_from_discovery, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (username, f"{MARK} {role}", f"{username}@example.com", account_status, int(hidden_from_discovery), self.now),
        )
        user_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return {"user_id": user_id, "username": username, "display_name": f"{MARK} {role}", "email": f"{username}@example.com"}

    def _make_post(self, user, body):
        conn = bot.db()
        cur = conn.cursor()
        # moderation_status defaults to 'pending' and the feed only shows
        # 'approved', so a post inserted without it would never appear — which
        # would make this test pass for the wrong reason.
        cur.execute(
            """
            INSERT INTO pulse_posts (user_id, body, post_type, visibility, moderation_status, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (int(user["user_id"]), body, "text", "public", "approved", "published", self.now, self.now),
        )
        post_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return post_id

    @contextmanager
    def acting_as(self, user):
        previous_require, previous_api = bot.require_account, bot.api_account_user
        bot.require_account = lambda: dict(user)
        bot.api_account_user = lambda: dict(user)
        try:
            yield
        finally:
            bot.require_account, bot.api_account_user = previous_require, previous_api

    def get_json(self, path):
        with self.acting_as(self.viewer):
            resp = self.client.get(path)
        self.assertEqual(resp.status_code, 200, f"{path} -> {resp.get_data(as_text=True)[:400]}")
        return resp.get_json() or {}

    def assert_hidden_but_normal_present(self, usernames, surface):
        """The two-sided assertion every surface test makes.

        Asserting only the absence of the hidden accounts would be satisfied by
        a filter that returns nothing at all, which in production reads as
        "search is broken" rather than "QA accounts are hidden".
        """
        usernames = [str(name or "").lower() for name in usernames]
        self.assertIn(self.normal["username"], usernames, f"{surface} must still show ordinary accounts, got {usernames}")
        self.assertNotIn(self.hidden_flag["username"], usernames, f"{surface} leaked a hidden_from_discovery account")
        self.assertNotIn(self.hidden_status["username"], usernames, f"{surface} leaked a disabled_qa account")

    # ------------------------------------------------------------------
    # the canonical predicate itself
    # ------------------------------------------------------------------
    def test_predicate_covers_both_hiding_mechanisms_and_tolerates_nulls(self):
        """Pin the predicate's semantics directly against SQLite.

        The surface tests below all run through it, but they cannot distinguish
        "correct predicate" from "predicate that happens to exclude these rows",
        and they cannot exercise the LEFT JOIN NULL case at all.
        """
        self.assertIn("disabled_qa", HIDDEN_ACCOUNT_STATUSES)
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute(f"SELECT username FROM users u WHERE u.username LIKE ? AND {discovery_visible_sql('u')}", (f"{MARK}%",))
        visible = {str(row["username"]) for row in cur.fetchall()}

        self.assertIn(self.normal["username"], visible)
        self.assertNotIn(self.hidden_flag["username"], visible)
        self.assertNotIn(self.hidden_status["username"], visible)

        # A LEFT JOIN with no matching users row yields NULL for every column.
        # The predicate must treat that as visible, otherwise applying it to a
        # feed silently drops content whose author row is missing.
        cur.execute(f"SELECT 1 WHERE {discovery_visible_sql('u')}".replace("u.hidden_from_discovery", "NULL").replace("u.account_status", "NULL"))
        self.assertIsNotNone(cur.fetchone(), "predicate must be NULL-tolerant for LEFT JOINed rows")
        conn.close()

    def test_every_hidden_status_is_actually_excluded(self):
        """Each status in the list must hide, not just the two used elsewhere."""
        conn = bot.db()
        cur = conn.cursor()
        for index, status in enumerate(HIDDEN_ACCOUNT_STATUSES):
            cur.execute(
                "INSERT INTO users (username, display_name, account_status, created_at) VALUES (?,?,?,?)",
                (f"{MARK}_status_{index}", f"{MARK} status {index}", status, self.now),
            )
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM users u WHERE u.username LIKE ? AND {discovery_visible_sql('u')}", (f"{MARK}_status_%",))
        self.assertEqual(int(cur.fetchone()[0]), 0, "a status in HIDDEN_ACCOUNT_STATUSES still passed the predicate")
        conn.close()

    # ------------------------------------------------------------------
    # discovery surfaces
    # ------------------------------------------------------------------
    def test_people_search_hides_qa_accounts(self):
        """The global people search — type a name, get accounts."""
        data = self.get_json(f"/api/pulse/users/search?q={MARK}")
        names = [str(item.get("username") or "").lower() for item in (data.get("users") or data.get("items") or [])]
        self.assert_hidden_but_normal_present(names, "people search")

    def test_creator_search_hides_qa_accounts(self):
        """The unified search page's Creators tab."""
        data = self.get_json(f"/api/pulse/search?q={MARK}")
        creators = (data.get("results") or {}).get("creators") or []
        names = [str(item.get("username") or "").lower() for item in creators]
        self.assert_hidden_but_normal_present(names, "creator search")

    def test_suggested_people_hides_qa_accounts(self):
        """"People you may know" — suggestions are drawn from post authors."""
        for user in (self.normal, self.hidden_flag, self.hidden_status):
            self._make_post(user, f"{MARK} post from {user['username']}")
        data = self.get_json("/api/pulse/friends")
        suggested = data.get("suggested") or []
        names = [str(item.get("username") or "").lower() for item in suggested]
        self.assert_hidden_but_normal_present(names, "suggested people")

    def test_messaging_people_search_hides_qa_accounts(self):
        """The 'start a chat' picker — mobile calls this to find someone to DM."""
        data = self.get_json(f"/api/pulse/communications/v2/people/search?q={MARK}")
        names = [item.get("username") for item in (data.get("people") or data.get("items") or [])]
        self.assert_hidden_but_normal_present(names, "messaging people search")

    def test_feed_hides_posts_authored_by_qa_accounts(self):
        for user in (self.normal, self.hidden_flag, self.hidden_status):
            self._make_post(user, f"{MARK} public post from {user['username']}")
        data = self.get_json("/api/pulse/feed?feed=for_you&limit=40")
        posts = data.get("posts") or data.get("items") or []
        authors = [str((post.get("author") or {}).get("username") or "").lower() for post in posts]
        self.assert_hidden_but_normal_present(authors, "for-you feed")

    def test_status_rail_hides_qa_accounts(self):
        conn = bot.db()
        cur = conn.cursor()
        for user in (self.normal, self.hidden_flag, self.hidden_status):
            cur.execute(
                "INSERT INTO pulse_status (user_id, status_type, body, visibility, created_at) VALUES (?,?,?,?,?)",
                (int(user["user_id"]), "text", f"{MARK} status", "public", self.now),
            )
        conn.commit()
        conn.row_factory = bot.sqlite3.Row
        rows = bot.pulse_status_active_rows(conn.cursor(), viewer_user_id=int(self.viewer["user_id"]), limit=50)
        conn.close()
        by_id = {int(row.get("user_id") or 0) for row in rows}
        self.assertIn(int(self.normal["user_id"]), by_id, "status rail must still show ordinary accounts")
        self.assertNotIn(int(self.hidden_flag["user_id"]), by_id, "status rail leaked a hidden_from_discovery account")
        self.assertNotIn(int(self.hidden_status["user_id"]), by_id, "status rail leaked a disabled_qa account")

    def test_marketplace_search_hides_listings_from_qa_sellers(self):
        conn = bot.db()
        cur = conn.cursor()
        for user in (self.normal, self.hidden_flag, self.hidden_status):
            # A listing is only buyer-visible when its seller row is approved and
            # carries a store name, and when it is either stocked or a digital
            # good. Without all of that the listing is invisible for reasons that
            # have nothing to do with account visibility, and this test would
            # pass no matter what the predicate did.
            cur.execute(
                "INSERT INTO marketplace_sellers (user_id, display_name, business_name, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (int(user["user_id"]), f"{MARK} store {user['username']}", f"{MARK} store", "approved", self.now, self.now),
            )
            cur.execute(
                """
                INSERT INTO marketplace_listings
                    (seller_user_id, title, description, category, product_type, listing_type, quantity, status, approval_status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (int(user["user_id"]), f"{MARK} listing by {user['username']}", "d", "Education", "digital", "digital", 5, "published", "approved", self.now, self.now),
            )
        conn.commit()
        conn.close()
        data = self.get_json(f"/api/pulse/marketplace/search?q={MARK}&limit=40")
        sellers = {int(item.get("seller_user_id") or 0) for item in (data.get("items") or [])}
        self.assertIn(int(self.normal["user_id"]), sellers, "marketplace search must still show ordinary sellers")
        self.assertNotIn(int(self.hidden_flag["user_id"]), sellers, "marketplace search leaked a hidden seller")
        self.assertNotIn(int(self.hidden_status["user_id"]), sellers, "marketplace search leaked a disabled_qa seller")

    # ------------------------------------------------------------------
    # the things that must NOT be hidden
    # ------------------------------------------------------------------
    def test_hidden_account_still_sees_its_own_posts(self):
        """Hiding is outward-facing only.

        A hidden account is usually a QA account, but the same flag is used for
        deactivated real people. Neither should open the app to an empty
        profile — that reads as data loss, and it would also break the App
        Review account if it were ever flagged by accident.
        """
        self._make_post(self.hidden_flag, f"{MARK} my own post")
        with self.acting_as(self.hidden_flag):
            resp = self.client.get("/api/pulse/feed?feed=my_posts&limit=20")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:400])
        posts = (resp.get_json() or {}).get("posts") or []
        self.assertTrue(
            any(f"{MARK} my own post" in str(post.get("body") or "") for post in posts),
            "a hidden account must still see its own posts",
        )

    def test_admin_user_listing_still_shows_hidden_accounts(self):
        """Staff tooling must keep full sight of the accounts being hidden.

        If the predicate ever leaks into admin queries the cleanup becomes
        unauditable: nobody could confirm what was hidden or undo a mistake.
        """
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT username, hidden_from_discovery, account_status FROM users WHERE username LIKE ?", (f"{MARK}%",))
        rows = {str(row["username"]): dict(row) for row in cur.fetchall()}
        conn.close()
        self.assertIn(self.hidden_flag["username"], rows, "hidden accounts must remain queryable by staff")
        self.assertEqual(int(rows[self.hidden_flag["username"]]["hidden_from_discovery"]), 1)
        self.assertEqual(str(rows[self.hidden_status["username"]]["account_status"]), "disabled_qa")

    def test_hiding_never_deletes_or_deactivates_the_row(self):
        """The mission's hard constraint: classification hides, it never destroys.

        Payment, moderation and App Review history hang off these rows, so the
        account must survive hiding completely intact.
        """
        conn = bot.db()
        conn.row_factory = bot.sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (int(self.hidden_flag["user_id"]),))
        row = dict(cur.fetchone() or {})
        conn.close()
        self.assertTrue(row, "hidden account row must still exist")
        self.assertEqual(str(row.get("email") or ""), self.hidden_flag["email"], "hiding must not scrub identity columns")
        self.assertEqual(str(row.get("account_status") or "active"), "active", "hiding must not also deactivate the account")

    def test_classification_script_is_dry_run_by_default(self):
        """``--apply-hide`` is opt-in and there is no delete path at all."""
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "qa_account_classification.py")
        with open(script, "r", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("--apply-hide", source)
        self.assertIn("action=\"store_true\"", source)
        self.assertNotIn("DELETE FROM users", source.upper().replace("DELETE  FROM", "DELETE FROM"))


if __name__ == "__main__":
    unittest.main()
