"""Who is allowed to read a profile, asked of every surface that answers.

``/pulse/@<handle>`` authenticated and then rendered. That was its whole gate.
It never read ``users.profile_visibility``, never consulted ``blocked_users``,
and never looked at ``account_status`` -- so any logged-in account could open a
private profile, or one that had blocked them, and receive the name, bio,
badges, stats and marketplace strip with working Follow and Message, while
``/api/pulse/profile/<key>`` refused the identical request. One subsystem, two
answers, and the stricter one was not the one a browser reached.

The API's own gate was a hand-rolled copy of the same decision and had drifted
in both directions: it never denied on a block either, and it refused an
accepted friend reading a private profile that ``profile_viewer_permissions``
allows. The profile-scoped feed lane (``?profile=<handle>``) filtered blocks
per post but knew nothing about profile visibility, so a private account's
public-visibility posts rendered to whoever asked.

WHAT THESE TESTS ARE FOR
------------------------
Not "the 403 happens" on its own. Three properties, each of which has already
failed in this codebase:

*One authority.* ``profile_access`` is the only thing that decides, and
``viewer_permissions`` is a projection of it. A surface that re-derives "is
this viewer blocked" beside the flag dict is free to disagree, and the
disagreement is invisible until it is a leak. So the same audience is asked of
the page, the API and the feed, and they must agree.

*Distinct sentinels.* The bio, the post body, the listing title, the email and
the legal name share no substrings. An earlier privacy suite in this repo gave
every field the same placeholder and was therefore blind to the one column it
existed for. ``test_the_sentinels_are_distinguishable`` fails if that stops
being true here.

*Deny is not disclosure.* A viewer who has been blocked and a viewer looking at
a suspended account are shown the same page. ``account_status`` reaches another
user as an availability outcome, never as a reason (privacy architecture §F1),
and "you have been blocked" is a disclosure the owner never asked us to make.
``test_blocked_and_restricted_are_indistinguishable`` compares the two bodies
directly rather than trusting that each one separately looks fine.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db.

Run: python3 -m pytest tests/test_profile_audience_matrix.py
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="profile_audience_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import profile_viewer_permissions  # noqa: E402


def pin_database():
    """Point DATABASE_URL at this file's temp sqlite, per test.

    ``services.db.connect()`` re-reads DATABASE_URL on every call and pytest
    imports every selected module during collection, so the last suite imported
    owns the database unless each one restates its own precondition.
    """
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def visible_text(html):
    """Roughly what a reader sees: no scripts, no styles, no tags.

    The profile page inlines a stylesheet and a few kilobytes of JavaScript that
    mention `bio` and `blocked` for unrelated reasons, so searching raw markup
    for either word reports a leak on a clean page.
    """
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", text)


SUBJECT = 96301          # the account being looked at
STRANGER = 96302         # logged in, no relationship to the subject
FOLLOWER = 96303         # follows the subject, not an accepted friend
FRIEND = 96304           # accepted friendship with the subject
BLOCKED_BY = 96305       # the subject has blocked this account
BLOCKER = 96306          # this account has blocked the subject

ALL_USERS = (SUBJECT, STRANGER, FOLLOWER, FRIEND, BLOCKED_BY, BLOCKER)

PUBLIC_POST = 9963001
PRIVATE_POST = 9963002
SUBJECT_RISKY_POST = 9963003
STRANGER_RISKY_POST = 9963004

#: Deliberately unlike each other and unlike anything else the page carries.
#: See the module docstring: identical placeholders are how a sibling suite
#: managed to miss the field it was written for.
SUBJECT_USERNAME = "estuarine_ferries"
SUBJECT_DISPLAY_NAME = "Marisol Vantongeren"
SUBJECT_FULL_NAME = "Marisol Henrike Vantongeren-Achterberg"
SUBJECT_EMAIL = "subject-3b9d71@audience-fixture.invalid"
SUBJECT_BIO = "Bibliographer of tidal ferry timetables since the Polperro refit"
PUBLIC_POST_BODY = "Dredging moved the jetty berth by eleven metres this winter"
PRIVATE_POST_BODY = "Draft notes nobody but me should be reading at this point"
# The platform-wide rail has no viewer, so its sentinels are a pair: one post
# only the subject may read, and one a stranger genuinely may. The second is the
# positive control -- `safe_intelligence_panel` turns any SQL error into an empty
# payload, so without it every absence below would pass on a broken rail.
SUBJECT_RISKY_BODY = "Private ledger of the wire transfer scam I am still tracing"
STRANGER_RISKY_BODY = "Public warning about the dockside deposit scam doing rounds"
RISK_SCORE_SENTINEL = 77

NOW = "2026-09-01T00:00:00"


class AudienceFixture(unittest.TestCase):
    """One subject, six audiences, and the ability to re-shape the subject."""

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
        self.seed()

    # -- identity -----------------------------------------------------------

    def logout(self):
        bot.require_account = lambda *args, **kwargs: None
        bot.api_account_user = lambda *args, **kwargs: None

    def login_as(self, user_id):
        who = {"user_id": user_id, "username": f"audience_{user_id}",
               "email": f"audience-{user_id}@audience-fixture.invalid"}
        bot.require_account = lambda *args, **kwargs: dict(who)
        bot.api_account_user = lambda *args, **kwargs: dict(who)

    # -- fixture ------------------------------------------------------------

    def seed(self):
        conn = bot.db()
        cur = conn.cursor()
        self._wipe(cur)
        cur.execute(
            "INSERT INTO users (user_id, username, display_name, full_name, email, bio,"
            " login_enabled, password_hash, account_status, profile_visibility)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (SUBJECT, SUBJECT_USERNAME, SUBJECT_DISPLAY_NAME, SUBJECT_FULL_NAME,
             SUBJECT_EMAIL, SUBJECT_BIO, 1, "not-a-real-hash", "active", "public"),
        )
        for user_id in ALL_USERS[1:]:
            cur.execute(
                "INSERT INTO users (user_id, username, display_name, full_name, email,"
                " login_enabled, password_hash, account_status, profile_visibility)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, f"audience_{user_id}", f"Audience {user_id}",
                 f"Audience Person {user_id}",
                 f"audience-{user_id}@audience-fixture.invalid",
                 1, "not-a-real-hash", "active", "public"),
            )
        for post_id, author, body, visibility, risk in (
            (PUBLIC_POST, SUBJECT, PUBLIC_POST_BODY, "public", 0),
            (PRIVATE_POST, SUBJECT, PRIVATE_POST_BODY, "private", 0),
            (SUBJECT_RISKY_POST, SUBJECT, SUBJECT_RISKY_BODY, "private", RISK_SCORE_SENTINEL),
            (STRANGER_RISKY_POST, STRANGER, STRANGER_RISKY_BODY, "public", RISK_SCORE_SENTINEL),
        ):
            cur.execute(
                "INSERT INTO pulse_posts (id, user_id, post_type, body, title, visibility,"
                " moderation_status, deleted_at, status, risk_score, created_at, updated_at,"
                " engagement_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, author, "text", body, "", visibility, "approved", None,
                 "published", risk, NOW, NOW, 0),
            )
        cur.execute(
            "INSERT INTO pulse_follows (follower_user_id, followed_user_id, created_at)"
            " VALUES (?,?,?)", (FOLLOWER, SUBJECT, NOW),
        )
        # `_friends` reads both tables the app writes to, so an accepted
        # friendship is seeded through the one `pulse_friend_graph` prefers.
        cur.execute(
            "INSERT INTO pulse_friendships (user_id, friend_user_id, created_at)"
            " VALUES (?,?,?)", (FRIEND, SUBJECT, NOW),
        )
        cur.execute(
            "INSERT INTO pulse_friendships (user_id, friend_user_id, created_at)"
            " VALUES (?,?,?)", (SUBJECT, FRIEND, NOW),
        )
        cur.execute(
            "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at)"
            " VALUES (?,?,?)", (SUBJECT, BLOCKED_BY, NOW),
        )
        cur.execute(
            "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at)"
            " VALUES (?,?,?)", (BLOCKER, SUBJECT, NOW),
        )
        conn.commit()
        conn.close()
        self.addCleanup(self.drop)

    def _wipe(self, cur):
        for user_id in ALL_USERS:
            cur.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            cur.execute("DELETE FROM pulse_posts WHERE user_id=?", (user_id,))
            cur.execute(
                "DELETE FROM pulse_follows WHERE follower_user_id=? OR followed_user_id=?",
                (user_id, user_id),
            )
            cur.execute(
                "DELETE FROM pulse_friendships WHERE user_id=? OR friend_user_id=?",
                (user_id, user_id),
            )
            cur.execute(
                "DELETE FROM blocked_users WHERE blocker_user_id=? OR blocked_user_id=?",
                (user_id, user_id),
            )

    def drop(self):
        conn = bot.db()
        self._wipe(conn.cursor())
        conn.commit()
        conn.close()

    def set_subject(self, **columns):
        """Re-shape the subject's own row: visibility, account_status."""
        conn = bot.db()
        assignments = ", ".join(f"{name}=?" for name in columns)
        conn.execute(
            f"UPDATE users SET {assignments} WHERE user_id=?",
            (*columns.values(), SUBJECT),
        )
        conn.commit()
        conn.close()

    # -- surfaces -----------------------------------------------------------

    def page(self, viewer_user_id):
        self.login_as(viewer_user_id)
        return self.client.get(f"/pulse/@{SUBJECT_USERNAME}")

    def api_profile(self, viewer_user_id):
        self.login_as(viewer_user_id)
        return self.client.get(f"/api/pulse/profile/{SUBJECT_USERNAME}")

    def api_profile_posts(self, viewer_user_id):
        self.login_as(viewer_user_id)
        return self.client.get(f"/api/pulse/profile/{SUBJECT_USERNAME}/posts")

    def feed_lane(self, viewer_user_id):
        self.login_as(viewer_user_id)
        return self.client.get(
            f"/api/pulse/feed?profile={SUBJECT_USERNAME}&feed=for_you"
        )

    def rail(self, viewer_user_id):
        """The platform-wide `intelligence` panel, which every feed call carries."""
        self.login_as(viewer_user_id)
        response = self.client.get("/api/pulse/feed?feed=for_you")
        self.assertEqual(response.status_code, 200)
        return response.get_json().get("intelligence") or {}


class SentinelHygiene(AudienceFixture):
    """The precondition every assertion below silently depends on."""

    def test_the_sentinels_are_distinguishable(self):
        values = [SUBJECT_USERNAME, SUBJECT_DISPLAY_NAME, SUBJECT_EMAIL,
                  SUBJECT_BIO, PUBLIC_POST_BODY, PRIVATE_POST_BODY]
        self.assertEqual(len(values), len(set(values)))
        for a in values:
            for b in values:
                if a is not b:
                    self.assertNotIn(a, b)
        # `full_name` deliberately *contains* the display name -- it is the
        # longer legal form of it -- so it is checked one-directionally.
        self.assertNotIn(SUBJECT_FULL_NAME, SUBJECT_DISPLAY_NAME)


class PublicProfile(AudienceFixture):
    """The control. Every denial below is an absence, and a broken page is an
    absence too."""

    def test_a_stranger_can_read_a_public_profile(self):
        response = self.page(STRANGER)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn(SUBJECT_DISPLAY_NAME, body)
        self.assertIn(SUBJECT_BIO, body)

    def test_the_owner_can_read_their_own_profile(self):
        response = self.page(SUBJECT)
        self.assertEqual(response.status_code, 200)
        self.assertIn(SUBJECT_BIO, response.get_data(as_text=True))

    def test_a_logged_out_reader_is_sent_to_login(self):
        self.logout()
        response = self.client.get(f"/pulse/@{SUBJECT_USERNAME}")
        self.assertIn(response.status_code, (301, 302, 303, 307, 308))
        self.assertIn("/login", response.headers.get("Location", ""))


class PrivateProfile(AudienceFixture):
    """``profile_visibility='private'`` -- the setting the page never read."""

    def setUp(self):
        super().setUp()
        self.set_subject(profile_visibility="private")

    def test_a_stranger_is_refused(self):
        response = self.page(STRANGER)
        self.assertEqual(response.status_code, 403)

    def test_a_stranger_is_told_whose_door_it_is_and_nothing_else(self):
        """The name is the point of the private shell; the content is not.

        Hiding the name too would leave a viewer unable to tell whether they
        mistyped a handle. Everything below the name stays shut.
        """
        body = self.page(STRANGER).get_data(as_text=True)
        self.assertIn(SUBJECT_DISPLAY_NAME, body)
        self.assertIn("private", visible_text(body).lower())
        for secret in (SUBJECT_BIO, SUBJECT_EMAIL, SUBJECT_FULL_NAME,
                       PUBLIC_POST_BODY, PRIVATE_POST_BODY):
            self.assertNotIn(secret, body)

    def test_a_follower_is_still_refused(self):
        """Following is not acceptance. Only an accepted friendship opens a
        private profile, which is what ``profile_access`` encodes."""
        self.assertEqual(self.page(FOLLOWER).status_code, 403)

    def test_an_accepted_friend_may_read_it(self):
        response = self.page(FRIEND)
        self.assertEqual(response.status_code, 200)
        self.assertIn(SUBJECT_BIO, response.get_data(as_text=True))

    def test_the_owner_may_read_their_own_private_profile(self):
        self.assertEqual(self.page(SUBJECT).status_code, 200)

    def test_the_api_refuses_a_stranger_too(self):
        response = self.api_profile(STRANGER)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SUBJECT_BIO, response.get_data(as_text=True))

    def test_the_api_lets_an_accepted_friend_in(self):
        """The API's hand-rolled gate refused every non-owner, so it was
        stricter than the resolver rather than merely different -- a friend who
        could read the profile in a browser could not read it in the app."""
        self.assertEqual(self.api_profile(FRIEND).status_code, 200)

    def test_the_posts_endpoint_refuses_a_stranger(self):
        response = self.api_profile_posts(STRANGER)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(PUBLIC_POST_BODY, response.get_data(as_text=True))

    def test_the_feed_lane_returns_no_posts_to_a_stranger(self):
        """``?profile=`` filtered blocks per post but not profile visibility,
        so the profile page could 403 while its own embedded feed served the
        same person's posts."""
        response = self.feed_lane(STRANGER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json().get("posts"), [])
        # Scoped to `posts` on purpose. The same response also carries the
        # global `intelligence` panel, which is not viewer-scoped at all and
        # republishes this subject's name and post title regardless of the lane
        # -- a separate defect with a wider blast radius, covered by
        # `IntelligencePanelExposure` below rather than smuggled in here.
        posts = response.get_json().get("posts") or []
        self.assertFalse(any(PUBLIC_POST_BODY in (p.get("body") or "") for p in posts))

    def test_the_feed_lane_still_serves_the_owner(self):
        """The deny must be scoped to the audience, not to the lane."""
        posts = self.feed_lane(SUBJECT).get_json().get("posts") or []
        self.assertTrue(any(PUBLIC_POST_BODY in (p.get("body") or "") for p in posts))


class BlockedViewer(AudienceFixture):
    """A block closes the profile in both directions."""

    def test_an_account_the_subject_blocked_is_refused(self):
        self.assertEqual(self.page(BLOCKED_BY).status_code, 403)

    def test_an_account_that_blocked_the_subject_is_refused(self):
        """Checking only "did the owner block me" would let a viewer keep
        reading someone they themselves blocked, which is not what a block
        means to either party."""
        self.assertEqual(self.page(BLOCKER).status_code, 403)

    def test_a_blocked_viewer_is_not_shown_the_profile_content(self):
        body = self.page(BLOCKED_BY).get_data(as_text=True)
        for secret in (SUBJECT_BIO, SUBJECT_EMAIL, SUBJECT_FULL_NAME,
                       PUBLIC_POST_BODY):
            self.assertNotIn(secret, body)

    def test_a_blocked_viewer_is_not_told_they_were_blocked(self):
        """The deny is not the place to narrate the relationship."""
        text = visible_text(self.page(BLOCKED_BY).get_data(as_text=True)).lower()
        self.assertNotIn("blocked", text)
        self.assertNotIn("block", text.replace("blocked", ""))

    def test_a_blocked_viewer_keeps_a_route_to_report(self):
        """Every deny branch keeps ``can_report`` open on purpose: a viewer who
        needs to escalate must not be left with nowhere to go."""
        body = self.page(BLOCKED_BY).get_data(as_text=True)
        self.assertIn("/pulse/safety", body)

    def test_the_api_refuses_a_blocked_viewer(self):
        """The API's own gate never looked at ``blocked_users`` at all."""
        response = self.api_profile(BLOCKED_BY)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(SUBJECT_BIO, response.get_data(as_text=True))

    def test_the_feed_lane_refuses_a_blocked_viewer(self):
        response = self.feed_lane(BLOCKED_BY)
        self.assertEqual(response.get_json().get("posts"), [])


class UnavailableAndRestricted(AudienceFixture):
    """``account_status`` must reach a viewer as an outcome, not a reason."""

    def test_a_deleted_account_is_gone_rather_than_refused(self):
        self.set_subject(account_status="deleted")
        self.assertEqual(self.page(STRANGER).status_code, 410)

    def test_a_suspended_account_is_refused(self):
        self.set_subject(account_status="suspended")
        self.assertEqual(self.page(STRANGER).status_code, 403)

    def test_a_suspended_account_does_not_leak_its_content(self):
        self.set_subject(account_status="suspended")
        body = self.page(STRANGER).get_data(as_text=True)
        for secret in (SUBJECT_BIO, SUBJECT_EMAIL, PUBLIC_POST_BODY):
            self.assertNotIn(secret, body)

    def test_a_suspended_account_does_not_narrate_its_moderation_state(self):
        self.set_subject(account_status="suspended")
        text = visible_text(self.page(STRANGER).get_data(as_text=True)).lower()
        for reason in ("suspended", "restricted", "banned", "moderation"):
            self.assertNotIn(reason, text)

    def test_blocked_and_restricted_are_indistinguishable(self):
        """Compared directly rather than separately.

        Each page could pass its own absence assertions while still differing
        from the other in a way that tells a viewer which of the two happened.

        One viewer sees both reasons, which matters: the shell renders the
        *viewer's* own id, so comparing two different viewers' pages would
        differ on a byte that is not a disclosure about the subject and would
        have to be filtered back out by the assertion.
        """
        self.set_subject(account_status="suspended")
        restricted = self.page(STRANGER)
        self.set_subject(account_status="active")
        conn = bot.db()
        conn.execute(
            "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at)"
            " VALUES (?,?,?)", (SUBJECT, STRANGER, NOW),
        )
        conn.commit()
        conn.close()
        blocked = self.page(STRANGER)
        self.assertEqual(blocked.status_code, restricted.status_code)
        self.assertEqual(
            visible_text(blocked.get_data(as_text=True)).split(),
            visible_text(restricted.get_data(as_text=True)).split(),
        )

    def test_the_owner_of_a_suspended_account_still_sees_their_profile(self):
        """A moderation outcome is addressed by the surfaces that exist for it,
        not by hiding someone's own profile from them."""
        self.set_subject(account_status="suspended")
        self.assertEqual(self.page(SUBJECT).status_code, 200)


class ViewerAwareCounts(AudienceFixture):
    """A count is a disclosure too."""

    def test_a_stranger_is_not_told_how_many_posts_are_hidden_from_them(self):
        """The header counted every non-deleted row while the Posts tab applied
        visibility, so the difference between the two numbers was a readout of
        how much the subject was withholding."""
        own = bot.pulse_feed_engine.count_user_posts(SUBJECT, viewer_user_id=SUBJECT)
        seen = bot.pulse_feed_engine.count_user_posts(SUBJECT, viewer_user_id=STRANGER)
        # The subject authored three: one public and two private.
        self.assertEqual(own, 3)
        self.assertEqual(seen, 1)
        body = visible_text(self.page(STRANGER).get_data(as_text=True))
        self.assertRegex(body, r"\b1 posts\b")
        self.assertNotRegex(body, r"\b2 posts\b")


class OneAuthority(AudienceFixture):
    """The page, the API and the resolver must not be able to disagree."""

    AUDIENCES = (SUBJECT, STRANGER, FOLLOWER, FRIEND, BLOCKED_BY, BLOCKER)

    def resolver_state(self, viewer_user_id):
        conn = bot.db()
        cur = conn.cursor()
        try:
            return profile_viewer_permissions.profile_access(
                cur, SUBJECT, viewer_user_id
            )[0]
        finally:
            conn.close()

    def test_viewer_permissions_is_a_projection_of_profile_access(self):
        """Two entry points, one precedence chain. If these ever diverge, a
        caller reading flags and a caller reading state disagree about the same
        viewer."""
        conn = bot.db()
        cur = conn.cursor()
        try:
            for viewer in self.AUDIENCES:
                self.assertEqual(
                    profile_viewer_permissions.viewer_permissions(cur, SUBJECT, viewer),
                    profile_viewer_permissions.profile_access(cur, SUBJECT, viewer)[1],
                )
        finally:
            conn.close()

    def test_a_closed_state_opens_no_content_flag(self):
        """``CLOSED_STATES`` is the set callers branch on. A state in it that
        left a content flag open would render a profile the resolver meant to
        close."""
        content_flags = (
            "can_view_public_profile",
            "can_view_follower_content",
            "can_view_friend_content",
            *profile_viewer_permissions.PUBLIC_CONTENT_FLAGS,
        )
        conn = bot.db()
        cur = conn.cursor()
        try:
            for visibility in ("public", "private"):
                self.set_subject(profile_visibility=visibility)
                for status in ("active", "suspended", "deleted"):
                    self.set_subject(account_status=status)
                    for viewer in self.AUDIENCES:
                        state, permissions = profile_viewer_permissions.profile_access(
                            cur, SUBJECT, viewer
                        )
                        if state not in profile_viewer_permissions.CLOSED_STATES:
                            continue
                        for flag in content_flags:
                            with self.subTest(visibility=visibility, status=status,
                                              viewer=viewer, flag=flag):
                                self.assertFalse(permissions[flag])
        finally:
            conn.close()

    def test_the_page_and_the_api_agree_on_every_audience(self):
        """The defect was exactly this: a 403 from the API and a 200 from the
        page for the same viewer and the same subject."""
        for visibility in ("public", "private"):
            for status in ("active", "suspended", "deleted"):
                self.set_subject(profile_visibility=visibility, account_status=status)
                for viewer in self.AUDIENCES:
                    closed = self.resolver_state(viewer) in profile_viewer_permissions.CLOSED_STATES
                    page = self.page(viewer).status_code
                    api = self.api_profile(viewer).status_code
                    with self.subTest(visibility=visibility, status=status, viewer=viewer):
                        self.assertEqual(page >= 400, closed)
                        self.assertEqual(api >= 400, closed)
                        self.assertEqual(page, api)


class IntelligencePanelExposure(AudienceFixture):
    """The `intelligence` rail ships on every feed response and has no viewer.

    It is therefore built for the anonymous audience, and anything it names or
    quotes must be something a stranger may already see. It was not: a private
    account appeared in `active_creators` with a count of its private posts, a
    private post's body appeared under `scam_warnings`, and `risk_score` -- the
    internal moderation signal 078329545 took off the wire elsewhere -- was
    published beside it.
    """

    def test_the_rail_is_actually_populated(self):
        """The positive control. `safe_intelligence_panel` converts any SQL
        error into an empty payload, so a broken rail would satisfy every
        absence assertion in this class."""
        panel = self.rail(STRANGER)
        titles = [item["title"] for item in panel.get("scam_warnings") or []]
        self.assertIn(STRANGER_RISKY_BODY[:80], titles)
        self.assertGreaterEqual(len(panel.get("active_creators") or []), 1)

    def test_the_rail_never_publishes_a_risk_score(self):
        panel = self.rail(STRANGER)
        for item in panel.get("scam_warnings") or []:
            self.assertNotIn("risk_score", item)
        self.assertNotIn(str(RISK_SCORE_SENTINEL), json.dumps(panel))

    def test_the_rail_does_not_name_a_private_account(self):
        self.set_subject(profile_visibility="private")
        panel = self.rail(STRANGER)
        names = [item["name"] for item in panel.get("active_creators") or []]
        self.assertNotIn(SUBJECT_DISPLAY_NAME, names)
        self.assertNotIn(SUBJECT_DISPLAY_NAME, json.dumps(panel))

    def test_the_rail_does_not_name_a_suspended_account(self):
        """`discovery_visible_sql` already covered this; the private case above
        is what `public_author_sql` adds. Both are asserted so a later
        simplification of one predicate into the other has to break a test."""
        self.set_subject(account_status="suspended")
        self.assertNotIn(SUBJECT_DISPLAY_NAME, json.dumps(self.rail(STRANGER)))

    def test_the_rail_does_not_quote_a_private_post(self):
        panel = json.dumps(self.rail(STRANGER))
        self.assertNotIn(PRIVATE_POST_BODY, panel)
        self.assertNotIn(SUBJECT_RISKY_BODY, panel)

    def test_a_private_accounts_post_count_is_not_published(self):
        """The subject has two public-visibility posts' worth of nothing to a
        stranger once private, so neither the name nor the tally may appear."""
        self.set_subject(profile_visibility="private")
        creators = self.rail(STRANGER).get("active_creators") or []
        self.assertEqual(
            [item for item in creators if item["name"] == SUBJECT_DISPLAY_NAME], []
        )

    def test_a_public_accounts_published_count_is_only_its_public_posts(self):
        """The subject is public here, so the name is fair to publish. The
        tally beside it is a different question: it was `COUNT(*)` over every
        approved row, which for this account means 1 public and 2 private, and
        publishing 3 tells a stranger exactly how much they cannot see."""
        creators = self.rail(STRANGER).get("active_creators") or []
        mine = [item for item in creators if item["name"] == SUBJECT_DISPLAY_NAME]
        self.assertEqual(len(mine), 1, creators)
        self.assertEqual(mine[0]["posts"], 1)

    def test_posts_today_counts_only_what_a_stranger_can_reach(self):
        """A tally of every row is a readout of what is being withheld -- the
        same defect as the profile header's raw `COUNT(*)`.

        The shared fixture is dated `NOW` on purpose, so nothing in it is ever
        "today" and this is the one assertion that has to seed its own rows.
        Three go in: one a stranger may read, one private, and one public but
        privately authored, so both halves of the predicate are load-bearing.
        """
        today = datetime.utcnow().date().isoformat() + "T12:00:00"
        conn = bot.db()
        cur = conn.cursor()
        for post_id, author, body, visibility in (
            (9963101, STRANGER, "Today public from a public author", "public"),
            (9963102, STRANGER, "Today private from a public author", "private"),
            (9963103, SUBJECT, "Today public from a private author", "public"),
        ):
            cur.execute(
                "INSERT INTO pulse_posts (id, user_id, post_type, body, title, visibility,"
                " moderation_status, deleted_at, status, risk_score, created_at, updated_at,"
                " engagement_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (post_id, author, "text", body, "", visibility, "approved", None,
                 "published", 0, today, today, 0),
            )
        conn.commit()
        conn.close()
        self.set_subject(profile_visibility="private")
        self.assertEqual(self.rail(STRANGER).get("posts_today"), 1)


if __name__ == "__main__":
    unittest.main()
