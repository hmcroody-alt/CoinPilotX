"""§9 — the rejected seller finds out why, on the listing, later.

The review pipeline could reject a product with a structured reason and file it
in the audit trail, and the merchant still had no way to read it. The decision
was written to ``moderation_category``, announced once through the notification
path, and then dropped:

  * neither seller ``SELECT`` named the moderation columns, and
  * every seller payload runs through the buyer serializer, which strips them
    on purpose (§43).

So a seller who missed the push — read it on another device, opened the app the
next week, cleared the notification by accident — saw the word ``rejected`` and
nothing else. Not a vague message: no message. "Send rejected items back to the
seller for correction" ended at "back to the seller", and the correction half
had nothing to work from.

The fix is deliberately narrow, because the reason it was missing is the same
reason it is dangerous to add carelessly. ``seller_verdict`` re-exposes the
structured code and the canonical sentence for it. It does **not** re-expose
``moderation_reason`` — the reviewer's free-text note — and does not read that
column at all. That note is typed on a screen showing supplier cost and unit
margin, which makes "paste the relevant line" a natural reviewer action and a
§43 leak the moment it happens.

The half worth reading twice is :func:`test_a_queued_listing_is_not_told_it_
needs_a_change`. ``seller_message`` falls back to the ``OTHER`` sentence for an
unrecognised code, and an empty code is unrecognised — so the naive version of
this feature tells every merchant with a product merely *waiting* in the queue
that it "needs a change before it can go live". That is a sentence they would
act on, by editing a product no reviewer has found fault with yet.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_seller_review_verdict.py
"""

import inspect
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="seller_verdict_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 90401
OTHER_SELLER = 90402
REVIEWER = 90499
NOW = "2026-09-01T00:00:00"

LIST_URL = "/api/pulse/marketplace/seller/listings"
PAUSE_URL = "/api/pulse/marketplace/seller/listings/{}/pause"

# The reviewer's note. Written on a screen that displays supplier cost, and
# carrying a number off it, because that is the shape of the leak -- not a
# malicious reviewer, an ordinary one explaining themselves.
INTERNAL_NOTE = "Margin is only 4% at CJ cost 18.40, not worth the support load."


# -- the decision function, with no database in sight -------------------------


class SellerVerdictTestCase(unittest.TestCase):
    def test_a_rejection_carries_the_code_and_the_sentence(self):
        verdict = rv.seller_verdict({
            "approval_status": lifecycle.REJECTED,
            "moderation_category": rv.INVALID_MEDIA,
            "review_version": 3, "reviewed_at": NOW})
        self.assertTrue(verdict["decided"])
        self.assertTrue(verdict["needs_action"])
        self.assertEqual(verdict["reason_code"], rv.INVALID_MEDIA)
        self.assertEqual(verdict["message"], rv.SELLER_MESSAGES[rv.INVALID_MEDIA])
        self.assertEqual(verdict["review_version"], 3)
        self.assertEqual(verdict["decided_at"], NOW)

    def test_a_queued_listing_is_not_told_it_needs_a_change(self):
        """The defect the obvious implementation ships with.

        ``seller_message("")`` returns the ``OTHER`` sentence -- "This listing
        needs a change before it can go live" -- because an empty code is an
        unrecognised code. Rendered against a listing that is merely waiting in
        the queue, that is a false alarm the merchant would act on by editing a
        product nobody has found fault with yet.
        """
        verdict = rv.seller_verdict({
            "approval_status": lifecycle.PENDING_REVIEW,
            "moderation_category": "", "review_version": 1})
        self.assertFalse(verdict["decided"])
        self.assertFalse(verdict["needs_action"])
        self.assertEqual(verdict["message"], "")
        self.assertEqual(verdict["reason_code"], "")
        self.assertEqual(verdict["decided_at"], "")

    def test_a_stale_code_is_not_rendered_against_a_pending_state(self):
        """§23 blanks the column when a material edit sends a listing back, but
        a code surviving some other path must not be shown as though it were
        this version's verdict. The state is what says whether there is a
        decision to report; the code only describes one."""
        verdict = rv.seller_verdict({
            "approval_status": lifecycle.PENDING_REVIEW,
            "moderation_category": rv.INVALID_PRICE, "review_version": 4})
        self.assertEqual(verdict["reason_code"], "")
        self.assertEqual(verdict["message"], "")

    def test_an_approval_reports_a_decision_with_nothing_to_fix(self):
        verdict = rv.seller_verdict({
            "approval_status": lifecycle.APPROVED,
            "moderation_category": "", "review_version": 2, "reviewed_at": NOW})
        self.assertTrue(verdict["decided"])
        self.assertFalse(verdict["needs_action"])
        self.assertEqual(verdict["message"], "")
        self.assertEqual(verdict["decided_at"], NOW)

    def test_restricted_is_a_state_the_seller_must_act_on(self):
        """The quiet one. ``restrict`` parks the moderation axis and leaves the
        merchant axis where the seller put it, so a restricted listing can read
        "live" on the seller's own release switch while no buyer can reach it.
        Saying nothing there looks exactly like nothing being wrong."""
        self.assertIn("restricted", rv.SELLER_MUST_ACT)
        verdict = rv.seller_verdict({
            "approval_status": "restricted",
            "moderation_category": rv.RESTRICTED_CATEGORY, "reviewed_at": NOW})
        self.assertTrue(verdict["needs_action"])
        self.assertEqual(verdict["message"], rv.SELLER_MESSAGES[rv.RESTRICTED_CATEGORY])

    def test_every_action_that_requires_a_reason_lands_in_a_state_the_seller_must_act_on(self):
        """Anchored on the transition table rather than on a list of names, so a
        fifth verdict added later is checked the day it is written. A reason the
        reviewer was *required* to give and the seller is never prompted to act
        on is a reason that was collected for nobody."""
        for action in rv.REASON_REQUIRED:
            _, approval = rv.TRANSITIONS[action]
            self.assertIn(approval, rv.SELLER_MUST_ACT, action)

    def test_every_state_the_seller_must_act_on_counts_as_decided(self):
        self.assertTrue(rv.SELLER_MUST_ACT.issubset(rv.SELLER_DECIDED))

    def test_a_missing_listing_is_an_empty_verdict_not_a_crash(self):
        for row in (None, {}):
            verdict = rv.seller_verdict(row)
            self.assertFalse(verdict["decided"])
            self.assertEqual(verdict["message"], "")

    def test_a_junk_revision_number_does_not_raise(self):
        self.assertEqual(rv.seller_verdict({"review_version": "n/a"})["review_version"], 0)

    def test_the_verdict_never_reads_the_reviewers_note(self):
        """Proven on source, not by hoping a fixture would have caught it.

        A later edit that reaches for ``moderation_reason`` -- to make the
        message "more specific", which is a genuinely tempting improvement --
        would pass every behavioural test in this file, because the column is
        blank in most of them. The column name simply must not appear.
        """
        code = rv.seller_verdict.__code__
        reachable = set(code.co_names) | {c for c in code.co_consts if isinstance(c, str)}
        self.assertNotIn("moderation_reason", reachable)
        self.assertNotIn("reviewed_by", reachable)
        # The docstring is excluded on purpose -- it *discusses* the column, and
        # a check that forbade the word outright would forbid explaining why.
        self.assertIn("moderation_reason", inspect.getsource(rv.seller_verdict))


# -- and it reaches the merchant's own screen ---------------------------------


class SellerVerdictRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_account_user = bot.api_account_user
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()
        bot.api_account_user = lambda *a, **k: {"user_id": SELLER, "username": "seller"}

    @classmethod
    def tearDownClass(cls):
        bot.api_account_user = cls._real_account_user

    def setUp(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            for uid in (SELLER, OTHER_SELLER):
                cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (uid,))
                cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (uid,))
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (uid, f"seller{uid}", "Lamp Co"))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (uid, "Lamp Co", "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    def insert_listing(self, **overrides):
        row = {
            "seller_user_id": SELLER,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "currency": "USD",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.REJECTED,
            "approval_status": lifecycle.REJECTED,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 5,
            "review_version": 3,
            "reviewed_by": REVIEWER,
            "reviewed_at": NOW,
            "moderation_reason": INTERNAL_NOTE,
            "moderation_category": rv.INVALID_MEDIA,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({', '.join(row)}) "
                    f"VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def listed(self, listing_id):
        payload = self.client.get(LIST_URL).get_json()
        self.assertTrue(payload.get("ok"), payload)
        for item in payload["items"]:
            if int(item.get("id") or 0) == listing_id:
                return item
        self.fail(f"listing {listing_id} missing from the seller's own list")

    # -- the headline -----------------------------------------------------------

    def test_the_seller_can_read_why_their_listing_was_rejected(self):
        """The whole point. Not "a notification was sent" -- the reason is on the
        listing, reachable whenever the merchant next opens it."""
        item = self.listed(self.insert_listing())
        self.assertEqual(item["review"]["reason_code"], rv.INVALID_MEDIA)
        self.assertEqual(item["review"]["message"],
                         rv.SELLER_MESSAGES[rv.INVALID_MEDIA])
        self.assertTrue(item["review"]["needs_action"])

    def test_the_single_listing_view_says_the_same_thing_as_the_list(self):
        """Two routes, one serializer. The list saying "replace the images" and
        the detail view saying nothing is the stale-blocker bug that put
        `readiness` in the shared serializer in the first place."""
        listing_id = self.insert_listing(status=lifecycle.PUBLISHED,
                                         approval_status=lifecycle.APPROVED)
        from_list = self.listed(listing_id)["review"]
        from_detail = self.client.post(PAUSE_URL.format(listing_id)).get_json()["listing"]["review"]
        self.assertEqual(from_list["reason_code"], from_detail["reason_code"])
        self.assertEqual(from_list["message"], from_detail["message"])

    # -- and only that ----------------------------------------------------------

    def test_the_reviewers_note_does_not_reach_the_seller(self):
        """§43. The note quotes a supplier cost and a margin. It is in the row
        the serializer is handed -- the column is populated in this fixture on
        purpose -- so this is a real containment check and not a check that the
        column happens to be empty."""
        item = self.listed(self.insert_listing())
        body = repr(item)
        self.assertNotIn("moderation_reason", item)
        self.assertNotIn(INTERNAL_NOTE, body)
        self.assertNotIn("18.40", body)

    def test_which_admin_decided_it_does_not_reach_the_seller(self):
        """§43. Platform staff identity on a merchant payload.

        Held by two barriers — no seller ``SELECT`` names the column, and the
        strip list would remove it if one did — so this asserts the outcome
        rather than either mechanism. Deliberately not paired with a mutation
        guard below: removing the strip entry does not leak it, which is the
        honest reading and was not the one this file shipped with first.
        """
        item = self.listed(self.insert_listing())
        self.assertNotIn("reviewed_by", item)
        self.assertNotIn(str(REVIEWER), repr(item))

    def test_the_risk_score_still_does_not_reach_the_seller(self):
        """Widening the SELECT is exactly how a strip list gets bypassed, so the
        fields that were already reviewer-only are re-checked here rather than
        assumed to have survived the change."""
        item = self.listed(self.insert_listing(safety_score=91))
        for field in ("safety_score", "safety_flags_json", "moderation_category",
                      "review_version"):
            self.assertNotIn(field, item, field)

    def test_the_verdict_is_scoped_to_the_listings_own_merchant(self):
        """The route filters on `seller_user_id`, and this is the assertion that
        keeps it that way: a rejection reason is the merchant's business and
        nobody else's."""
        mine = self.insert_listing()
        theirs = self.insert_listing(seller_user_id=OTHER_SELLER)
        ids = {int(i["id"]) for i in self.client.get(LIST_URL).get_json()["items"]}
        self.assertIn(mine, ids)
        self.assertNotIn(theirs, ids)

    # -- the states that are not rejections -------------------------------------

    def test_a_pending_listing_carries_no_verdict(self):
        item = self.listed(self.insert_listing(
            status=lifecycle.PENDING_REVIEW, approval_status=lifecycle.PENDING_REVIEW,
            moderation_reason="", moderation_category="",
            reviewed_by=None, reviewed_at=None))
        self.assertFalse(item["review"]["decided"])
        self.assertEqual(item["review"]["message"], "")

    def test_a_live_listing_is_not_told_to_fix_anything(self):
        item = self.listed(self.insert_listing(
            status=lifecycle.PUBLISHED, approval_status=lifecycle.APPROVED,
            moderation_reason="", moderation_category=""))
        self.assertTrue(item["review"]["decided"])
        self.assertFalse(item["review"]["needs_action"])
        self.assertEqual(item["review"]["message"], "")

# -- and the barriers are load-bearing ----------------------------------------


class SellerVerdictMutationGuardTestCase(SellerVerdictRouteTestCase):
    """§45. Break each barrier on purpose and require the containment tests above
    to go red.

    Worth doing here specifically because two of those tests could pass for the
    wrong reason. ``moderation_reason`` is blocked twice over — the seller
    ``SELECT`` does not name it *and* the strip list would remove it — so a test
    asserting it is absent proves containment without proving which barrier is
    holding. ``reviewed_by`` is the one that changed: this work started selecting
    it to build the verdict, so the strip list is now the only thing between an
    admin's user id and a merchant's payload. That barrier is new, and a new
    barrier nobody has watched fail is a barrier nobody knows is there.
    """

    def assert_mutation_leaks(self, name, field, value, probe):
        """``probe`` must pass now and fail with ``field`` removed from the strip
        list. The first half is the half that matters: a probe that cannot pass
        reads as "the mutation was caught" for every mutation ever applied.
        """
        probe()
        previous = bot.MARKETPLACE_REVIEWER_ONLY_FIELDS
        bot.MARKETPLACE_REVIEWER_ONLY_FIELDS = tuple(
            f for f in previous if f != field)
        try:
            probe()
        except AssertionError:
            return
        finally:
            bot.MARKETPLACE_REVIEWER_ONLY_FIELDS = previous
        self.fail(f"MUTATION SURVIVED: {name}. {field!r} was allowed through and "
                  f"every assertion still passed — nothing is guarding it.")

    def test_dropping_the_moderation_category_from_the_strip_list_is_caught(self):
        """The column this change started selecting. Leaked raw it ships as
        ``INVALID_MEDIA`` beside the verdict's own readable sentence — a constant
        rendered at a merchant, which is the §20 failure pointed the other way.
        """
        self.assert_mutation_leaks(
            "§43 raw moderation column on a merchant payload",
            "moderation_category", rv.INVALID_MEDIA,
            self.test_the_risk_score_still_does_not_reach_the_seller)

    def test_dropping_the_risk_score_from_the_strip_list_is_caught(self):
        """The field that actually shipped once. It holds the reviewer's *risk*
        number despite its name, so a seller reading "Safety 91" reads it exactly
        backwards."""
        self.assert_mutation_leaks(
            "§43 risk score on a merchant payload", "safety_score", 91,
            self.test_the_risk_score_still_does_not_reach_the_seller)


if __name__ == "__main__":
    unittest.main()
