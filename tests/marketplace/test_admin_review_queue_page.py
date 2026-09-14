"""The review queue page itself — can a reviewer actually reach the backlog?

The endpoint tests in ``test_admin_review_batch_route.py`` prove a batch decides
and writes correctly. They say nothing about whether a human can ever assemble
that batch, and that is the half of §38 the mission is actually about: *"there
is no clear backend/admin review queue where an authorized reviewer can see
pending products"*. A queue that renders is not the same as a queue that is
complete.

What only a rendered page can get wrong:

  * **the backlog past the first screen.** The previous query was a bare
    ``ORDER BY ... LIMIT 100``. Listing 101 was counted in the pending badge,
    was decidable by the endpoint, and was reachable by no control on the page.
    Nothing failed. The badge said 340 and the reviewer cleared 100 of them
    forever;
  * **the badge and the table disagreeing.** They are two SQL statements. If
    they are built from two predicates, "23 pending" over a table of nineteen
    rows is a bug no assertion about either one alone can see;
  * **a runtime NameError in the page body.** ``py_compile`` passes on a
    reference to a constant that does not exist; the page 500s on load. This
    file loads it, which is the only thing that catches that;
  * **the blocker preview being a guess.** §13 requires the bulk bar to say
    "eligible vs blocked" *before* the reviewer commits. The only honest source
    for that is the same ``block_reason`` the endpoint will call, rendered into
    the row — so the assertion here is that the page's ``data-block`` matches
    what the endpoint would decide, not merely that some attribute is present.

Run standalone (this file binds its own ``DATABASE_URL`` before importing
``bot``, so it cannot share a pytest process with another marketplace file)::

    ./.venv/bin/python3 -m pytest tests/marketplace/test_admin_review_queue_page.py
"""

import os
import re
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="review_queue_page_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 78201
REVIEWER = 78202
NOW = "2026-09-01T00:00:00"

PAGE = "/admin/marketplace-command"

#: ``value='12' data-block="SELF_REVIEW"`` — the id the reviewer would submit
#: and the verdict the page is predicting for it, read straight out of the
#: markup rather than out of a template variable.
TICK = re.compile(r"class='review-tick' value='(\d+)' data-block=\"([^\"]*)\"")


class AdminReviewQueuePageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = _DB_PATH
        bot.init_db()
        rv.ensure_schema()
        cls._real_require_admin_page = bot.require_admin_page
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    @classmethod
    def tearDownClass(cls):
        bot.require_admin_page = cls._real_require_admin_page

    def setUp(self):
        self.sign_in(REVIEWER)
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM marketplace_listings")
            cur.execute("DELETE FROM marketplace_sellers WHERE user_id IN (?,?)",
                        (SELLER, REVIEWER))
            for user_id, store in ((SELLER, "Lamp Co"), (REVIEWER, "Reviewer's Own Store")):
                cur.execute("INSERT OR IGNORE INTO users (user_id, username, display_name)"
                            " VALUES (?,?,?)", (user_id, f"user{user_id}", store))
                cur.execute("INSERT INTO marketplace_sellers"
                            " (user_id, display_name, status, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (user_id, store, "approved", NOW, NOW))
            conn.commit()
        finally:
            conn.close()

    # -- harness ---------------------------------------------------------------

    def sign_in(self, admin_id):
        def _require_admin_page(permission="users.view"):
            return {"id": admin_id, "username": f"admin{admin_id}",
                    "email": f"admin{admin_id}@example.com", "role": "owner"}, None
        bot.require_admin_page = _require_admin_page

    def insert_listing(self, seller_user_id=SELLER, **overrides):
        row = {
            "seller_user_id": seller_user_id,
            "title": "Brass desk lamp",
            "description": "A weighted brass lamp with a linen shade.",
            "category": "Home",
            "price_label": "$24.00",
            "cover_image_url": "https://cdn.example/lamp.jpg",
            "status": lifecycle.PENDING_REVIEW,
            "approval_status": lifecycle.PENDING_REVIEW,
            "listing_type": "physical",
            "product_type": "physical",
            "quantity": 12,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(f"INSERT INTO marketplace_listings ({cols}) VALUES ({marks})",
                    tuple(row.values()))
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id

    def load(self, **params):
        response = self.client.get(PAGE, query_string=params)
        self.assertEqual(response.status_code, 200,
                         response.get_data(as_text=True)[:2000])
        return response.get_data(as_text=True)

    def ticks(self, html):
        """``{listing_id: predicted block reason}`` for the rendered page."""
        return {int(listing_id): block for listing_id, block in TICK.findall(html)}

    def chip(self, html, label):
        match = re.search(r">" + re.escape(label) + r" <b>(\d+)</b></a>", html)
        self.assertIsNotNone(match, f"no {label!r} chip on the page")
        return int(match.group(1))

    # -- the page exists at all ------------------------------------------------

    def test_the_review_page_renders_instead_of_five_hundreding(self):
        """A referenced-but-undefined script constant compiles and then 500s.

        This is not a hypothetical: the bulk bar's behaviour lives in a module
        constant that the body concatenates, and nothing short of rendering the
        page proves it is defined.
        """
        html = self.load()
        self.assertIn("Listing Review Queue", html)
        self.assertIn("id='review-bulk'", html)
        self.assertIn("/api/admin/marketplace/review/batch", html)

    def test_every_bulk_control_the_script_drives_is_actually_on_the_page(self):
        """§31. The script and the markup are written in two different places;
        a renamed id leaves a button that does nothing and looks fine."""
        html = self.load()
        for handle in ("id='review-all'", "id='review-selected'", "id='review-eligible'",
                       "id='review-reason'", "id='review-note'", "id='review-clear'",
                       "id='review-outcome'"):
            self.assertIn(handle, html, handle)
        for action in rv.ACTIONS:
            self.assertIn(f"data-review-action='{action}'", html, action)

    def test_the_reason_menu_offers_the_sentence_the_seller_will_read(self):
        """§20/§36. A reviewer choosing between ``MISLEADING_DESCRIPTION`` and
        ``CATEGORY_MISMATCH`` is decoding constants; choosing between the two
        sentences the seller is about to receive is reading."""
        html = self.load()
        for code in rv.REASON_CODES:
            self.assertIn(f"<option value='{code}'>", html, code)
        self.assertIn("This product is not permitted on PulseSoc.", html)

    # -- §38/§42: nothing is stranded -----------------------------------------

    def test_a_pending_listing_past_the_first_page_is_still_reachable(self):
        """The bug this replaced: counted in the badge, decidable by the
        endpoint, reachable by nobody."""
        ids = [self.insert_listing(title=f"Lamp {index:02d}") for index in range(31)]
        seen = set()
        page = 1
        while True:
            html = self.load(page=page, page_size=10)
            found = self.ticks(html)
            seen.update(found)
            if "Next</a>" not in html:
                break
            page += 1
            self.assertLess(page, 12, "pager never terminated")
        self.assertEqual(seen, set(ids))
        self.assertEqual(page, 4)

    def test_the_badge_and_the_table_are_built_from_one_predicate(self):
        """Two SQL statements, one truth. A badge that counts differently to the
        table under it is the version of this page that looks correct."""
        for index in range(7):
            self.insert_listing(title=f"Lamp {index}")
        for index in range(3):
            self.insert_listing(title=f"Gone {index}", status=lifecycle.REJECTED,
                                approval_status=lifecycle.REJECTED)
        html = self.load(page_size=50)
        self.assertEqual(self.chip(html, "Needs review"), 7)
        self.assertEqual(len(self.ticks(html)), 7)
        self.assertEqual(self.chip(html, "Rejected"), 3)
        self.assertEqual(self.chip(html, "All listings"), 10)

    def test_a_page_past_the_end_lands_on_a_page_that_has_rows(self):
        """A reviewer who clears the last four listings on page 7 must not be
        told the queue is empty when thirty listings remain."""
        ids = [self.insert_listing(title=f"Lamp {index}") for index in range(5)]
        html = self.load(page=99, page_size=2)
        self.assertTrue(self.ticks(html), "landed on an empty page")
        self.assertIn("Page 3 of 3", html)
        self.assertLessEqual(set(self.ticks(html)), set(ids))

    def test_an_empty_filter_says_so_and_offers_the_way_out(self):
        self.insert_listing()
        html = self.load(filter="rejected")
        self.assertEqual(self.ticks(html), {})
        self.assertIn("No listings match this filter", html)
        self.assertIn("filter=all", html)

    # -- §26-§28: the controls do what they say --------------------------------

    def test_the_default_view_is_the_work_queue_not_the_catalogue(self):
        pending = self.insert_listing(title="Needs a decision")
        self.insert_listing(title="Long since live", status=lifecycle.PUBLISHED,
                            approval_status=lifecycle.APPROVED)
        html = self.load()
        self.assertEqual(set(self.ticks(html)), {pending})

    def test_a_typo_in_the_filter_shows_the_work_queue_not_everything(self):
        """Failing open here means a reviewer mistakes the whole catalogue for
        their backlog and works rows nobody asked them to touch."""
        pending = self.insert_listing()
        self.insert_listing(status=lifecycle.PUBLISHED, approval_status=lifecycle.APPROVED)
        html = self.load(filter="pendign")
        self.assertEqual(set(self.ticks(html)), {pending})

    def test_a_pasted_listing_id_finds_that_one_row(self):
        first = self.insert_listing(title="Lamp one")
        wanted = self.insert_listing(title="Lamp two")
        html = self.load(q=str(wanted))
        self.assertEqual(set(self.ticks(html)), {wanted})
        self.assertNotIn(f"value='{first}' data-block", html)

    def test_a_text_search_matches_the_title(self):
        wanted = self.insert_listing(title="Cross Border Jeans Ripped Mid Waist")
        self.insert_listing(title="Brass desk lamp")
        html = self.load(q="jeans")
        self.assertEqual(set(self.ticks(html)), {wanted})

    def test_the_sort_control_reorders_the_queue(self):
        older = self.insert_listing(title="Older", created_at="2026-01-01T00:00:00")
        newer = self.insert_listing(title="Newer", created_at="2026-08-01T00:00:00")
        self.assertEqual(list(self.ticks(self.load(sort="oldest"))), [older, newer])
        self.assertEqual(list(self.ticks(self.load(sort="newest"))), [newer, older])

    def test_paging_does_not_quietly_drop_the_search_and_the_sort(self):
        """The version of "the filters don't work" that looks like the filters
        working: page 2 of a search is page 2 of everything."""
        for index in range(4):
            self.insert_listing(title=f"Jeans {index}")
        self.insert_listing(title="Brass desk lamp")
        html = self.load(q="jeans", sort="newest", page_size=2)
        next_link = re.search(r"href='([^']*page=2[^']*)'>Next</a>", html)
        self.assertIsNotNone(next_link, "no Next link on a four-row search")
        self.assertIn("q=jeans", next_link.group(1))
        self.assertIn("sort=newest", next_link.group(1))
        self.assertEqual(len(self.ticks(self.load(
            q="jeans", sort="newest", page_size=2, page=2))), 2)

    # -- §13: the preview is the server's own verdict --------------------------

    def test_the_row_predicts_exactly_what_the_endpoint_would_decide(self):
        """Not "an attribute is present" — the same function, the same answer.

        A preview computed any other way is a second opinion, and the reviewer
        finds out it was wrong only after committing twenty-five decisions.
        """
        ordinary = self.insert_listing()
        own = self.insert_listing(seller_user_id=REVIEWER, title="Reviewer's own lamp")
        prohibited = self.insert_listing(title="Case of whisky", category="Alcohol")

        html = self.load(page_size=50)
        rendered = self.ticks(html)

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = {int(row["id"]): dict(row) for row in
                conn.execute("SELECT * FROM marketplace_listings").fetchall()}
        conn.close()

        for listing_id, predicted in rendered.items():
            expected = rv.block_reason(rows[listing_id], rv.APPROVE,
                                       reviewer_id=REVIEWER) or ""
            self.assertEqual(predicted, expected, f"listing {listing_id}")

        self.assertEqual(rendered[ordinary], "")
        self.assertEqual(rendered[own], rv.SELF_REVIEW)
        self.assertEqual(rendered[prohibited], rv.PROHIBITED)

    def test_a_blocked_row_says_why_in_words_next_to_the_tick(self):
        """An unexplained disabled-looking row is indistinguishable from a bug.
        The reviewer needs the reason at the row, not after the batch."""
        self.insert_listing(seller_user_id=REVIEWER)
        html = self.load()
        self.assertIn(rv.BLOCK_NOTES[rv.SELF_REVIEW], html)

    # -- §30: decide one, land on the next -------------------------------------

    def test_every_row_carries_its_own_decide_and_next_controls(self):
        first = self.insert_listing(title="Lamp one")
        second = self.insert_listing(title="Lamp two")
        html = self.load()
        for listing_id in (first, second):
            for verb in (rv.APPROVE, rv.REQUEST_CHANGES, rv.REJECT):
                self.assertIn(f"data-quick='{verb}' data-listing='{listing_id}'", html,
                              f"{verb} on {listing_id}")

    def test_approve_and_next_goes_through_the_same_endpoint_as_the_bulk_bar(self):
        """§21 one authority. A second single-decision route is how the two
        drift: one grows a guard, the other keeps the old behaviour, and which
        one ran depends on which button the reviewer happened to click."""
        self.insert_listing()
        html = self.load()
        self.assertEqual(html.count("'/api/admin/marketplace/review/batch'"), 2)
        self.assertIn("listing_ids: [listingId]", html)

    def test_the_approve_button_is_dead_on_a_row_that_cannot_be_approved(self):
        """§31. A control that looks live and answers 403 is worse than one that
        is visibly unavailable — the reviewer learns nothing either way, but the
        first costs them a decision they thought they made."""
        own = self.insert_listing(seller_user_id=REVIEWER)
        html = self.load()
        self.assertIn(f"data-quick='approve' data-listing='{own}' disabled", html)

    def test_a_prohibited_product_stays_rejectable_from_the_row(self):
        """§34 cuts one way only. If the block covered every action, the
        listings a reviewer most needs to clear would be the ones they cannot."""
        banned = self.insert_listing(title="Case of whisky", category="Weapons")
        html = self.load()
        self.assertIn(f"data-quick='approve' data-listing='{banned}' disabled", html)
        self.assertIn(f"data-quick='reject' data-listing='{banned}'>", html)
        self.assertNotIn(f"data-quick='reject' data-listing='{banned}' disabled", html)

    def test_a_selection_cannot_exceed_what_one_batch_will_accept(self):
        """Select-all ticks the page. If a page can hold more rows than
        ``MAX_BATCH``, select-all builds a request the server refuses whole —
        and the reviewer's only clue is that nothing happened."""
        html = self.load(page_size=10_000)
        match = re.search(r"Page 1 of \d+ · \d+ listing", html)
        self.assertIsNotNone(match)
        self.assertLessEqual(rv.normalize_query({"page_size": 10_000})["page_size"],
                             rv.MAX_BATCH)


if __name__ == "__main__":
    unittest.main()
