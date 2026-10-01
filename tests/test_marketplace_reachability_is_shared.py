"""Every marketplace buyer surface asks ``marketplace_listing_lifecycle``, not a copy.

## The bug this closes

Six surfaces had hand-written copies of the publication predicate, all of them
some spelling of::

    WHERE status IN ('active','approved')     -- or status='active'

``marketplace_listings.status`` has never held either value. The publication
vocabulary is ``published``/``live``/``active``
(``marketplace_listing_lifecycle.PUBLIC_STATUSES``) and every writer in the tree
sets ``published``. Measured against production on 2026-09-30: 196 rows
``published``, 4 ``seller_deleted``, 2 ``review_ready``, zero ``active`` or
``approved`` ever. So each copy matched nothing, and the surface built on it had
been dead since the day it shipped:

* ``bot.py`` ``api_pulse_search`` -- the ``marketplace`` bucket of universal
  search returned nothing for every query ever typed. The dedicated
  ``api_pulse_marketplace_search`` does use the shared predicate, so marketplace
  search worked and only the universal-search bucket was dead.
* ``bot.py`` ``pulse_profile_page_for_user`` -- a seller's public profile showed
  no listings, on every profile, including the one seller with 196 live rows.
* ``undx_personal_intelligence_service.marketplace_search`` and
  ``marketplace_listing_summary`` -- the agent tools. The summary returned
  ``None``, so the agent told the asker a live listing does not exist.
* ``content_translation`` -- a *Python* mirror (``status in {"active","approved"}``)
  that computed ``public`` for the authorization gate. Always ``False``, so only
  a listing's own seller could ever translate it.
* ``pulse_ads_os`` promotable-content picker -- this one was ``IN
  ('active','review_ready')``, which is inverted rather than empty. It matched
  exactly the 2 unreviewed rows and none of the 196 live ones, so a seller was
  offered only the inventory they may not advertise.

## Why none of it was noticed

A dead eligibility clause returns an empty result set, and an empty result set is
also the correct answer when there is genuinely nothing to show. There is no
third outcome that says "this predicate matched nothing". No error, no log line,
no visual difference from intended behaviour. That is why this file leads with a
static guard rather than relying on someone noticing a blank surface.

## The two kinds of test here

``SharedReachabilityPredicateTestCase`` is the static guard, and it is the piece
that stops recurrence: it reads ``bot.py`` and every module under ``services/``
and fails if a drifted status literal appears anywhere near
``marketplace_listings``. It is the only test that covers all six surfaces at
once, including the two in ``bot.py`` whose page renderers are impractical to
drive here.

The rest are behavioural, seeding listings in the shapes the predicate decides
between and asserting which ones the surface returns. They exist because the
static guard only proves the *old* spelling is gone -- it cannot tell a shared
predicate from a differently-wrong new one. Note that three of the four service
seams swallow exceptions and return empty on failure
(``undx._read``, ``pulse_ads_os._inventory_ids``), so each one's positive
assertion is load-bearing twice over: it proves the predicate matches, and it
proves the query did not crash.

Runs against a temp sqlite file, so nothing here touches coinpilotx.db. Note
that sqlite cannot catch a Postgres dialect error in these queries; all six were
separately executed against production Postgres read-only before landing.

Run: python3 -m pytest tests/test_marketplace_reachability_is_shared.py
"""

import io
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import tokenize
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="mkt_reachability_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402

from services import content_translation  # noqa: E402
from services import pulse_ads_os  # noqa: E402
from services import undx_personal_intelligence_service as undx  # noqa: E402

SELLER = 95401
VIEWER = 95402
NOW = "2026-09-01T00:00:00"
TITLE = "Linen Duvet Cover Set"


# ---------------------------------------------------------------------------
# the static guard
# ---------------------------------------------------------------------------

#: The spellings the six drifted copies used, plus the orderings a reviewer
#: might write instead. ``status='active'`` is included even though ``active``
#: is a legitimate member of ``PUBLIC_STATUSES``: on its own it excludes
#: ``published``, which is the only value any writer sets, so a single-value
#: test against it is always the bug.
_DRIFTED_STATUS_LITERAL = re.compile(
    r"""status\s*(?:IN|in)\s*[\(\{]\s*['"]active['"]\s*,\s*['"](?:approved|review_ready)['"]\s*[\)\}]"""
    r"""|status\s*(?:IN|in)\s*[\(\{]\s*['"](?:approved|review_ready)['"]\s*,\s*['"]active['"]\s*[\)\}]"""
    r"""|status\s*=\s*['"]active['"]""",
    re.IGNORECASE,
)

#: How far from the literal a mention of the table counts as the same statement.
#: Wide enough to span a multi-line SQL literal with a JOIN and a projection,
#: narrow enough that an unrelated ``status='active'`` elsewhere in a 111k-line
#: file is not attributed to it.
_WINDOW_CHARS = 800


def _blank_comments(text: str) -> str:
    """``text`` with ``#`` comments overwritten by spaces, offsets preserved.

    A naive scan reports five hits on a tree that is already fixed, because each
    fix documents the predicate it replaced by quoting it. The repo has been
    bitten by exactly this before -- ``tests/protection/test_route_auth.py``
    reads a prose comment as a call -- so the guard tokenizes rather than
    grepping. String literals are deliberately kept: that is where the SQL is.
    """
    out = list(text)
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type != tokenize.COMMENT:
                continue
            start = line_starts[token.start[0] - 1] + token.start[1]
            end = min(line_starts[token.end[0] - 1] + token.end[1], len(out))
            for index in range(start, end):
                if out[index] != "\n":
                    out[index] = " "
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # An unparseable module is not this test's business to report; scanning
        # it raw only risks a false positive, never a missed one.
        return text
    return "".join(out)


def _scan_for_drift():
    sources = [REPO_ROOT / "bot.py"] + sorted((REPO_ROOT / "services").rglob("*.py"))
    findings = []
    for path in sources:
        text = _blank_comments(path.read_text(encoding="utf-8", errors="replace"))
        lowered = text.lower()
        for match in _DRIFTED_STATUS_LITERAL.finditer(text):
            window = lowered[
                max(0, match.start() - _WINDOW_CHARS) : match.end() + _WINDOW_CHARS
            ]
            if "marketplace_listings" in window:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)}"
                )
    return findings


class SharedReachabilityPredicateTestCase(unittest.TestCase):
    """No marketplace_listings query may decide reachability for itself."""

    def test_no_surface_hand_rolls_the_publication_status_list(self):
        """The guard against recurrence -- and the only test covering all six.

        A surface that copies the status list cannot be kept correct, because the
        vocabulary lives in ``marketplace_listing_lifecycle`` and a copy has no
        reason to be revisited when it changes. The six copies this closes drifted
        silently for their entire lives.
        """
        findings = _scan_for_drift()
        self.assertEqual(
            findings,
            [],
            "A marketplace_listings query is deciding publication status for itself.\n"
            "Use marketplace_listing_lifecycle.public_sql(alias, seller_alias) in SQL,\n"
            "or marketplace_listing_lifecycle.is_public(row) in Python.\n"
            "Note `is_public` fails closed on a column the row does not carry, so the\n"
            "projection must include status, approval_status, quantity, product_type or\n"
            "listing_type, and the seller's status.\n"
            "If you are documenting the old predicate, quote it in a `#` comment --\n"
            "those are stripped before this scan. Found:\n  "
            + "\n  ".join(findings),
        )

    def test_the_guard_detects_the_predicate_it_was_written_for(self):
        """Proof the scan can fail, without reverting source to find out.

        The guard's whole value is that it fails on reintroduction, and a scan
        that silently matched nothing would read identically green. This feeds it
        the real drifted clause and the real table name together.
        """
        reintroduced = (
            "cur.execute(\"SELECT id FROM marketplace_listings l \"\n"
            "            \"WHERE l.status IN ('active','approved')\")"
        )
        matches = list(_DRIFTED_STATUS_LITERAL.finditer(reintroduced))
        self.assertEqual(len(matches), 1)
        self.assertIn("marketplace_listings", reintroduced.lower())

    def test_the_guard_does_not_fire_on_the_shared_predicate(self):
        """``public_sql`` names ``active`` too; that must stay allowed.

        The shared predicate renders ``IN ('published','live','active')``. A guard
        that keyed on the word ``active`` rather than on a lone or wrongly-paired
        test against it would forbid the correct answer.
        """
        from services import marketplace_listing_lifecycle

        rendered = marketplace_listing_lifecycle.public_sql("l", "ms")
        self.assertIn("active", rendered)
        self.assertEqual(list(_DRIFTED_STATUS_LITERAL.finditer(rendered)), [])


# ---------------------------------------------------------------------------
# behavioural: shared fixtures
# ---------------------------------------------------------------------------


class _ListingFixture(unittest.TestCase):
    """A seller and listings in the shapes the predicate decides between."""

    @classmethod
    def setUpClass(cls):
        # `init_db()` returns early on a process global, so a second suite in the
        # same pytest process would find this file's database empty.
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        bot.INIT_DB_COMPLETED = False
        bot.init_db()

    def setUp(self):
        # On SQLite `services.db.connect()` re-reads DATABASE_URL per call, and
        # pytest imports every selected module before running anything, so the
        # last suite imported owns the database unless each one re-pins it.
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM marketplace_listings WHERE seller_user_id=?", (SELLER,))
        cur.execute("DELETE FROM marketplace_sellers WHERE user_id=?", (SELLER,))
        for user_id, username in ((SELLER, "reach_seller"), (VIEWER, "reach_viewer")):
            cur.execute(
                "INSERT OR IGNORE INTO users (user_id, username, display_name) VALUES (?,?,?)",
                (user_id, username, username),
            )
        conn.commit()
        conn.close()

    def seed_seller(self, *, display_name="M&W Store", status="approved"):
        conn = sqlite3.connect(_DB_PATH)
        conn.execute(
            "INSERT INTO marketplace_sellers (user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (SELLER, display_name, status, NOW, NOW),
        )
        conn.commit()
        conn.close()

    def seed_listing(self, *, status="published", approval_status="approved",
                     quantity=12, product_type="physical", title=TITLE):
        conn = sqlite3.connect(_DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(seller_user_id, title, description, short_description, category, price_label,"
            " currency, quantity, product_type, listing_type, status, approval_status,"
            " cover_image_url, safety_score, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (SELLER, title, "A washed European linen duvet cover.", "Washed linen duvet set",
             "Home", "$465.74", "USD", quantity, product_type, product_type, status,
             approval_status, "https://cdn.example/bed.jpg", 7, NOW, NOW),
        )
        listing_id = int(cur.lastrowid)
        conn.commit()
        conn.close()
        return listing_id


# ---------------------------------------------------------------------------
# behavioural: the UNDX agent tools
# ---------------------------------------------------------------------------


class UndxMarketplaceToolsTestCase(_ListingFixture):
    """``marketplace.search`` and ``marketplace.listing.summary``."""

    def test_search_returns_a_published_listing(self):
        """The regression: this shape is the 196 real rows and returned nothing.

        ``undx._read`` catches every exception and returns ``[]``, so an empty
        result proved nothing about the query. This assertion is the only thing
        separating "the predicate matches" from "the statement raised".
        """
        self.seed_seller()
        self.seed_listing()
        facts = undx.marketplace_search(VIEWER, "linen")
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["title"], TITLE)

    def test_search_withholds_a_listing_still_in_review(self):
        self.seed_seller()
        self.seed_listing(status="review_ready", approval_status="review_ready")
        self.assertEqual(undx.marketplace_search(VIEWER, "linen"), [])

    def test_search_withholds_a_listing_whose_seller_is_not_approved(self):
        """A condition the old ``status='active'`` did not ask at all."""
        self.seed_seller(status="suspended")
        self.seed_listing()
        self.assertEqual(undx.marketplace_search(VIEWER, "linen"), [])

    def test_summary_describes_a_published_listing(self):
        """The loudest of the six: the agent reported a live listing as absent.

        ``marketplace_listing_summary`` returns ``None`` on an empty read, and the
        caller renders that as "no such listing" rather than as a failed lookup.
        """
        self.seed_seller()
        listing_id = self.seed_listing()
        fact = undx.marketplace_listing_summary(VIEWER, listing_id)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["title"], TITLE)

    def test_summary_withholds_a_listing_awaiting_moderation(self):
        """Publishing is the merchant's act; approving is the moderator's."""
        self.seed_seller()
        listing_id = self.seed_listing(approval_status="review_ready")
        self.assertIsNone(undx.marketplace_listing_summary(VIEWER, listing_id))

    def test_summary_payload_gains_no_seller_columns(self):
        """The join is for the WHERE clause only.

        ``_fact`` hands the whole row to the agent as ``data``, so a convenience
        column added to the projection becomes part of what the model may repeat
        to a user. The seller's moderation status is not the asker's business.
        """
        self.seed_seller()
        listing_id = self.seed_listing()
        fact = undx.marketplace_listing_summary(VIEWER, listing_id)
        self.assertNotIn("seller_status", fact["data"])
        self.assertNotIn("display_name", fact["data"])


# ---------------------------------------------------------------------------
# behavioural: the promotable-inventory picker
# ---------------------------------------------------------------------------


class PromotableListingInventoryTestCase(_ListingFixture):
    """``pulse_ads_os._inventory_ids(..., kind='listing')``."""

    def inventory(self):
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            return pulse_ads_os._inventory_ids(conn, SELLER, "listing", 25)
        finally:
            conn.close()

    def test_a_published_listing_is_offered_for_promotion(self):
        self.seed_seller()
        listing_id = self.seed_listing()
        self.assertEqual([row["id"] for row in self.inventory()], [listing_id])

    def test_an_unreviewed_listing_is_not_offered_for_promotion(self):
        """The inversion, stated directly.

        The old clause was ``IN ('active','review_ready')``. Production holds no
        ``active`` row, so ``review_ready`` was the *only* thing it ever matched:
        a seller was shown exactly the inventory they may not advertise. Both
        halves matter, so this withholding case is as much the regression as the
        one above.
        """
        self.seed_seller()
        self.seed_listing(status="review_ready", approval_status="review_ready")
        self.assertEqual(self.inventory(), [])

    def test_a_listing_with_no_stock_is_not_offered_for_promotion(self):
        """Promotable means reachable; spend must have somewhere to land."""
        self.seed_seller()
        self.seed_listing(quantity=0)
        self.assertEqual(self.inventory(), [])


# ---------------------------------------------------------------------------
# behavioural: translation authorization
# ---------------------------------------------------------------------------


class ListingTranslationAuthorizationTestCase(_ListingFixture):
    """``content_translation.resolve_authorized_content`` for a listing."""

    def resolve(self, user_id, listing_id):
        return content_translation.resolve_authorized_content(user_id, "marketplace", listing_id)

    def test_any_member_may_translate_a_published_listing(self):
        """The regression: the Python mirror made this ``False`` for everyone.

        A shopper reading a shop in another language got
        ``content_unavailable`` 404, which reads as a deleted listing.
        """
        self.seed_seller()
        listing_id = self.seed_listing()
        resolved = self.resolve(VIEWER, listing_id)
        self.assertIn(TITLE, resolved["text"])

    def test_a_stranger_may_not_translate_an_unpublished_listing(self):
        self.seed_seller()
        listing_id = self.seed_listing(status="review_ready", approval_status="review_ready")
        with self.assertRaises(content_translation.TranslationError) as caught:
            self.resolve(VIEWER, listing_id)
        self.assertEqual(caught.exception.code, "content_unavailable")

    def test_a_seller_may_still_translate_their_own_unpublished_listing(self):
        """Why the gate is ``is_public`` in Python and not ``public_sql`` in SQL.

        The row must be fetched whether or not it is published, because the owner
        is allowed to see it. Filtering in the WHERE clause would have made this
        case unreachable -- the correct-looking fix that breaks the one path the
        broken code accidentally kept working.
        """
        self.seed_seller()
        listing_id = self.seed_listing(status="review_ready", approval_status="review_ready")
        resolved = self.resolve(SELLER, listing_id)
        self.assertIn(TITLE, resolved["text"])

    def test_the_projection_carries_every_column_the_gate_needs(self):
        """``is_public`` fails closed, so a thin projection denies everything.

        This is the trap that would have reproduced the original bug through a
        different mechanism: calling the shared helper on the old five-column row
        returns ``False`` for every listing, exactly as the hand-written mirror
        did, and no test of the published case would have caught the difference
        between "not public" and "cannot tell".
        """
        from services import marketplace_listing_lifecycle

        self.seed_seller()
        listing_id = self.seed_listing()
        thin = {"id": listing_id, "status": "published", "approval_status": "approved"}
        self.assertFalse(marketplace_listing_lifecycle.is_public(thin))
        # The real surface answers True on the same listing, which can only be
        # because its projection is wider than `thin`.
        self.assertIn(TITLE, self.resolve(VIEWER, listing_id)["text"])


if __name__ == "__main__":
    unittest.main()
