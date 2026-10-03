"""``/pulse/post/<id>`` was telling anonymous readers what we think of a post.

The byline read ``Type: text · Status: approved · Risk score: 45``.
``pulse_posts.risk_score`` is a scam-shield output -- an input to a moderation
decision, not a fact about the post -- and ``moderation_status`` is its review
state. This is the one social surface that answers an anonymous request with a
200, so both were being served to the open web; verified against production on
2026-10-01 with a Googlebot user-agent and no cookies, post 2516 returned
``Risk score: 45``.

The access gate was never wrong: it 404s anything not approved. The field
projection was. Deciding *whether* a reader may have this post is a different
question from deciding *which fields* they receive, and only the first one was
being asked.

WHAT THESE TESTS ARE FOR
------------------------
Not "the string is gone" -- a grep establishes that once and then rots.

The load-bearing property is that every seeded value is a *distinct* sentinel.
An earlier version of this check gave every numeric column the same placeholder
and was therefore blind to ``risk_score``, the one column it existed for: the
assertion passed because the value it was looking for was also the value of
four fields that were legitimately present. ``test_the_sentinels_are_distinguishable``
fails if that ever stops being true.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_privacy_p1_exposure.py
"""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="privacy_p1_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402


def pin_database():
    """Point DATABASE_URL at this file's temp sqlite, per test.

    ``services.db.connect()`` re-reads DATABASE_URL on every call and pytest
    imports every selected module during collection, so the last suite imported
    owns the database unless each one restates its own precondition.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def visible_text(html):
    """Roughly what a reader sees: no scripts, no styles, no tags.

    These assertions are about numbers, and a page carries plenty of incidental
    digits nobody reads -- ``rgba(255,255,255)`` contains ``55``, cache-busting
    tokens contain dates. Searching raw markup for a two-digit score reports a
    leak on a clean page.
    """
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", text)


SUBJECT = 96201          # the account being looked at
VIEWER = 96202           # some other logged-in account
RISKY_POST = 9960101

#: Deliberately unlike each other and unlike anything else the payload carries.
#: See the module docstring: identical placeholders are how the previous
#: version of this check managed to miss the field it was written for.
SUBJECT_EMAIL = "subject-7f31b2@privacy-fixture.invalid"
SUBJECT_FULL_NAME = "Rosalind Quartermaine-Okonjo"
SUBJECT_DISPLAY_NAME = "Subject Nine Six Two Oh One"
SUBJECT_USERNAME = "privacy_subject"
VIEWER_EMAIL = "viewer-4c08ad@privacy-fixture.invalid"
RISK = 45

NOW = "2026-09-01T00:00:00"


class PrivacyFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pin_database()
        # ``init_db()`` short-circuits on a process global once any suite has
        # run it, which would leave this file's temp database with no tables.
        bot.INIT_DB_COMPLETED = False
        bot.init_db()
        cls._real_require_account = bot.require_account
        cls._real_api_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    def setUp(self):
        pin_database()
        self.addCleanup(setattr, bot, "require_account", self._real_require_account)
        self.addCleanup(setattr, bot, "api_account_user", self._real_api_account_user)

    def logout(self):
        bot.require_account = lambda *args, **kwargs: None
        bot.api_account_user = lambda *args, **kwargs: None

    def login_as(self, user_id, username, email):
        who = {"user_id": user_id, "username": username, "email": email}
        bot.require_account = lambda *args, **kwargs: dict(who)
        bot.api_account_user = lambda *args, **kwargs: dict(who)

    def seed_accounts(self):
        conn = bot.db()
        cur = conn.cursor()
        for user_id in (SUBJECT, VIEWER):
            cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, full_name, email,"
            " login_enabled, password_hash, account_status, profile_visibility)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (SUBJECT, SUBJECT_USERNAME, SUBJECT_DISPLAY_NAME, SUBJECT_FULL_NAME,
             SUBJECT_EMAIL, 1, "not-a-real-hash", "active", "public"),
        )
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, full_name, email,"
            " login_enabled, password_hash, account_status, profile_visibility)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (VIEWER, "privacy_viewer", "Privacy Viewer", "Viewer Person",
             VIEWER_EMAIL, 1, "not-a-real-hash", "active", "public"),
        )
        conn.commit()
        conn.close()
        self.addCleanup(self.drop_accounts)

    def drop_accounts(self):
        conn = bot.db()
        for user_id in (SUBJECT, VIEWER):
            conn.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()


class SentinelHygiene(PrivacyFixture):
    """The precondition every assertion below silently depends on."""

    def test_the_sentinels_are_distinguishable(self):
        """If two sentinels collide, an absence assertion can pass on a leak.

        This is the failure that hid ``risk_score`` once already: every numeric
        column shared one placeholder, so "the score is not in the response"
        was satisfied by four fields that were supposed to be there.
        """
        values = [SUBJECT_EMAIL, SUBJECT_FULL_NAME, SUBJECT_DISPLAY_NAME,
                  SUBJECT_USERNAME, VIEWER_EMAIL]
        self.assertEqual(len(values), len(set(values)))
        for a in values:
            for b in values:
                if a is not b:
                    self.assertNotIn(a, b)
        # The score must not be findable inside any of the string sentinels
        # either, or the post-page assertion stops meaning anything.
        for value in values:
            self.assertNotIn(str(RISK), value)


class PostPageExposure(PrivacyFixture):
    """What ``/pulse/post/<id>`` serves to someone who is not logged in."""

    def seed_post(self):
        self.seed_accounts()
        conn = bot.db()
        cur = conn.cursor()
        body = "A real post about something, long enough to be a destination. " * 5
        cur.execute("DELETE FROM pulse_posts WHERE id=?", (RISKY_POST,))
        cur.execute(
            "INSERT INTO pulse_posts (id, user_id, post_type, body, title, visibility,"
            " moderation_status, deleted_at, status, risk_score, created_at, updated_at,"
            " engagement_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (RISKY_POST, SUBJECT, "text", body, "A post carrying a score", "public",
             "approved", None, "published", RISK, NOW, NOW, 0),
        )
        conn.commit()
        conn.close()
        self.addCleanup(self.drop_post)

    def drop_post(self):
        conn = bot.db()
        conn.execute("DELETE FROM pulse_posts WHERE id=?", (RISKY_POST,))
        conn.commit()
        conn.close()

    def test_the_post_page_renders_at_all(self):
        """The control. Every assertion below is an absence, and a 404 is an
        absence too."""
        self.seed_post()
        self.logout()
        page = self.client.get(f"/pulse/post/{RISKY_POST}").get_data(as_text=True)
        self.assertIn("A post carrying a score", page)

    def test_an_anonymous_reader_is_not_told_the_posts_risk_score(self):
        self.seed_post()
        self.logout()
        page = self.client.get(f"/pulse/post/{RISKY_POST}").get_data(as_text=True)
        self.assertNotIn("Risk score", page)
        self.assertNotIn("risk_score", page)
        # The number itself, against visible text -- the label could be renamed
        # and the value would still be published.
        self.assertNotRegex(visible_text(page), rf"\b{RISK}\b")

    def test_an_anonymous_reader_is_not_told_the_posts_moderation_state(self):
        """``moderation_status`` printed the constant "approved" in practice,
        because the route 404s anything else.

        That is a property of today's access gate rather than of this template,
        and the field would have started narrating review state the moment the
        gate loosened -- so it goes now, while the change is free.
        """
        self.seed_post()
        self.logout()
        page = self.client.get(f"/pulse/post/{RISKY_POST}").get_data(as_text=True)
        self.assertNotIn("Status: approved", page)
        self.assertNotIn("moderation_status", page)


if __name__ == "__main__":
    unittest.main()
