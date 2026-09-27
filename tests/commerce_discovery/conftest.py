"""A real marketplace, small enough to fit in memory and drive for an hour.

The repetition failure is invisible to a unit test. Every individual response
this engine produces is defensible — the products are eligible, the scores clear
the floor, the per-response diversity caps hold. The defect only exists across
*many* responses, and only when each one can see what the ones before it did. So
the fixture here is not a stub: it is a SQLite database with the real schema, the
real eligibility SQL running against it, and an impression log that the next
request genuinely reads back.

Three things make that possible:

**A controllable clock.** Cooldowns are the core mechanism and they are measured
in hours. ``subject.now_utc`` is monkeypatched to a fake clock so a test can
walk a viewer through fifty minutes of scrolling in a few milliseconds, and so
that "six hours later" is a test case rather than an aspiration.

**Injected preferences.** ``preferences.viewer_policy`` falls back to importing
``services.pulse_settings_routes``, which imports ``bot`` — 111k lines, to answer
a question about one dict. The loader is injectable for exactly this reason, and
the fixture injects it while leaving every other line of the real policy
resolution (consent, suppressions, frequency) running for real.

**A closed loop.** :meth:`SimulatedMarketplace.render` writes the impression
events through ``events.record_impression``, the same path the mobile client
uses. Nothing about exposure is simulated — the engine's memory of what it
showed is the rows it actually wrote.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.commerce_discovery import (
    engine, events, pool, preferences, schema, subject, tagging,
)

#: Deliberately not "now". A fixed start makes a failure reproducible, and an
#: hour that is not the hour the suite happens to run in catches code that
#: compares against a real wall clock by accident.
EPOCH = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)

CATEGORIES = ("shoes", "bags", "watches", "jackets", "lamps")


_MARKETPLACE_DDL = (
    """
    CREATE TABLE users (
        user_id INTEGER PRIMARY KEY,
        username TEXT
    )
    """,
    """
    CREATE TABLE marketplace_sellers (
        user_id INTEGER PRIMARY KEY,
        status TEXT,
        display_name TEXT,
        business_name TEXT,
        verification_status TEXT,
        risk_score INTEGER DEFAULT 0,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE marketplace_listings (
        id INTEGER PRIMARY KEY,
        seller_user_id INTEGER,
        title TEXT,
        short_description TEXT,
        description TEXT,
        category TEXT,
        subcategory TEXT,
        price_label TEXT,
        currency TEXT,
        quantity INTEGER DEFAULT 0,
        product_type TEXT,
        listing_type TEXT,
        listing_metadata_json TEXT,
        cover_image_url TEXT,
        gallery_json TEXT,
        video_url TEXT,
        created_at TEXT,
        updated_at TEXT,
        featured INTEGER DEFAULT 0,
        delivery_type TEXT,
        status TEXT,
        approval_status TEXT,
        safety_score INTEGER,
        moderation_reason TEXT
    )
    """,
    """
    CREATE TABLE marketplace_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        buyer_user_id INTEGER,
        seller_user_id INTEGER,
        listing_id INTEGER,
        quantity INTEGER DEFAULT 1,
        unit_price_cents INTEGER DEFAULT 0,
        -- The money columns are here because they are in production
        -- (`bot.init_db`, `CREATE TABLE IF NOT EXISTS marketplace_orders`) and
        -- because value reconciliation reads them. A fixture missing them would
        -- send `_reconciled_value` down its exception path on every call, where
        -- it fails soft to zero — so the tests would pass while proving that the
        -- lookup never works.
        amount_cents INTEGER DEFAULT 0,
        currency TEXT DEFAULT 'USD',
        status TEXT,
        provider_payment_id TEXT,
        paid_at TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE marketplace_saved_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        listing_id INTEGER,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE marketplace_cart_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        listing_id INTEGER,
        qty INTEGER DEFAULT 1
    )
    """,
    # The platform's social follow graph, shaped as `bot.init_db` creates it. It
    # lives in this marketplace fixture because a seller is a user, so the shop's
    # "from sellers you follow" claim is answered by the social graph and not by
    # any commerce table. Present here so the read is *exercised* rather than
    # failing soft to an empty set — a fail-soft path that no test ever leaves is
    # indistinguishable from a read that does not work.
    """
    CREATE TABLE pulse_follows (
        follower_user_id INTEGER,
        followed_user_id INTEGER,
        followed_public_player_id TEXT,
        created_at TEXT,
        PRIMARY KEY (follower_user_id, followed_user_id)
    )
    """,
    # Creator product tags. Owned by `bot.init_db`, not by
    # `schema.ensure_schema` — the writer is the post composer, so the discovery
    # package does not create it. Which means this fixture is a hand-copy of
    # production DDL and free to drift from it, the exact failure mode that has
    # bitten this repo before. `test_a_creator_can_tag_their_own_products.py`
    # closes it: one test parses `bot.py`'s own CREATE TABLE and asserts the
    # column names match this one, so a column added in production and forgotten
    # here fails rather than silently making every tagging test a test of an
    # imaginary table.
    """
    CREATE TABLE pulse_content_products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content_type TEXT,
        content_id INTEGER,
        listing_id INTEGER,
        attached_by_user_id INTEGER,
        seller_user_id INTEGER,
        authority TEXT DEFAULT 'owner',
        created_at TEXT,
        UNIQUE(content_type, content_id, listing_id)
    )
    """,
)


_PRICE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_price(label, currency="USD"):
    """Stand-in for ``bot.parse_price``: "$34.00" -> ``(3400, "USD")``.

    Returns zero for anything without a number, which is what makes the
    "Request access" listings in the fixture fail :func:`eligibility.gate` the
    same way they would in production.
    """
    match = _PRICE.search(str(label or ""))
    if not match:
        return 0, currency or "USD"
    return int(round(float(match.group(1)) * 100)), (currency or "USD")


class Clock:
    """A wall clock the test moves by hand."""

    def __init__(self, start: datetime = EPOCH):
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


class SimulatedMarketplace:
    """One viewer, one catalogue, and the loop between them.

    ``seed`` builds the catalogue. ``serve`` asks the engine for placements.
    ``render`` reports them back as impressions, which is what gives the *next*
    ``serve`` something to avoid. ``browse`` is the two composed, which is what
    an actual scroll looks like.
    """

    def __init__(self, conn, clock: Clock, viewer_id: int = 9001):
        self.conn = conn
        self.clock = clock
        self.viewer_id = viewer_id
        #: Every impression, in the order it happened. The input to
        #: ``metrics.summarize``.
        self.log: list[dict] = []
        self._categories: dict[int, str] = {}
        self._next_id = 1

    # -- setup ---------------------------------------------------------------
    def seed(self, *, products: int = 100, sellers: int = 10, categories=CATEGORIES) -> None:
        cur = self.conn.cursor()
        created = subject.iso(self.clock.now - timedelta(days=30))
        for index in range(sellers):
            seller_id = 1001 + index
            cur.execute(
                "INSERT INTO users (user_id, username) VALUES (?,?)",
                (seller_id, f"seller{index}"),
            )
            cur.execute(
                "INSERT INTO marketplace_sellers "
                "(user_id, status, display_name, business_name, verification_status, risk_score, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (seller_id, "approved", f"Store {index}", f"Store {index} Ltd", "verified", 0, created),
            )
        cur.execute("INSERT INTO users (user_id, username) VALUES (?,?)", (self.viewer_id, "shopper"))

        for index in range(products):
            self._insert_listing(cur, index + 1, 1001 + (index % sellers), categories[index % len(categories)], created)
        self._next_id = products + 1
        self.conn.commit()

    def _insert_listing(self, cur, listing_id: int, seller_id: int, category: str, created: str) -> None:
        self._categories[listing_id] = category
        cur.execute(
            "INSERT INTO marketplace_listings "
            "(id, seller_user_id, title, short_description, description, category, subcategory, "
            " price_label, currency, quantity, product_type, listing_type, cover_image_url, "
            " created_at, updated_at, featured, status, approval_status, safety_score, moderation_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                listing_id, seller_id, f"Product {listing_id}",
                f"A very good {category[:-1]}", f"Full description of product {listing_id}",
                category, "", f"${20 + listing_id}.00", "USD", 25,
                "physical", "physical", f"https://cdn.example/{listing_id}.jpg",
                # safety_score holds a *risk* score — 0 is what the scorer writes
                # for a listing it found nothing wrong with. See eligibility.py.
                created, created, 0, "published", "approved", 0, "",
            ),
        )

    def restock(self, seller_index: int, count: int, *, categories=CATEGORIES) -> list[int]:
        """Give one seller a much larger catalogue than everybody else."""
        cur = self.conn.cursor()
        created = subject.iso(self.clock.now - timedelta(days=30))
        added = []
        for offset in range(count):
            listing_id = self._next_id + offset
            self._insert_listing(cur, listing_id, 1001 + seller_index, categories[offset % len(categories)], created)
            added.append(listing_id)
        self._next_id += count
        self.conn.commit()
        return added

    def category_of(self, listing_id) -> str:
        return self._categories.get(int(listing_id), "")

    def history(
        self,
        listing_ids,
        *,
        impressions: int = 0,
        clicks: int = 0,
        age_days: float = 1.0,
    ) -> None:
        """Give some listings a past, earned by *other* shoppers.

        Written under a different ``subject_ref`` on purpose. ``_listing_stats``
        counts these — which is what turns a listing from unproven into popular
        or trending, and therefore what makes the marketplace produce more than
        one shelf. The viewer's own exposure read filters on ``subject_ref``, so
        none of it counts as something *this* person has already been shown.

        ``age_days`` is how long ago it happened, and it matters: engagement is
        counted over a window, so history written at ``age_days=1`` is current
        evidence and the same history at ``age_days=30`` is a listing's
        biography. A fixture that can only write recent history cannot tell a
        windowed read from an unwindowed one, which is how the unwindowed read
        survived this long.
        """
        cur = self.conn.cursor()
        stamp = subject.iso(self.clock.now - timedelta(days=age_days))
        for listing_id in listing_ids:
            listing_id = int(listing_id)
            seller_id = 1001 + ((listing_id - 1) % 10)
            for index in range(impressions):
                key = f"seed-impr:{listing_id}:{index}"
                cur.execute(
                    "INSERT INTO commerce_discovery_impression_events "
                    "(event_id, placement_id, subject_ref, surface, slot, listing_id, "
                    " seller_user_id, promotion_class, ranking_version, visible, "
                    " self_view, event_at, dedup_key, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, f"seed-pl:{listing_id}:{index}", "crowd", "feed", 0, listing_id,
                     seller_id, "organic", "seed", 1, 0, stamp, key, stamp),
                )
            for index in range(clicks):
                key = f"seed-click:{listing_id}:{index}"
                cur.execute(
                    "INSERT INTO commerce_discovery_engagement_events "
                    "(event_id, placement_id, subject_ref, surface, listing_id, "
                    " seller_user_id, promotion_class, ranking_version, action, "
                    " event_at, dedup_key, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, f"seed-pl:{listing_id}:{index}", "crowd", "feed", listing_id,
                     seller_id, "organic", "seed", "click", stamp, key, stamp),
                )
        self.conn.commit()

    # -- the loop ------------------------------------------------------------
    def serve(
        self,
        surface: str = "feed",
        *,
        limit=None,
        context=None,
        session_id="cs_test",
        exclude_listing_ids=(),
        content_post_id=0,
    ) -> list[dict]:
        return engine.serve(
            self.conn.cursor(),
            self.viewer_id,
            surface,
            conn=self.conn,
            context=context,
            session_id=session_id,
            limit=limit,
            parse_price=parse_price,
            exclude_listing_ids=exclude_listing_ids,
            content_post_id=content_post_id,
        )

    def render(self, placements, *, visible: bool = True) -> None:
        """Report placements as impressions, exactly as the client would."""
        cur = self.conn.cursor()
        for placement in placements:
            events.record_impression(
                cur,
                placement["placement_id"],
                placement["impression_token"],
                conn=self.conn,
                viewer_user_id=self.viewer_id,
                visible=visible,
                view_duration_ms=1200,
            )
            listing_id = int(placement["product"].get("listing_id") or placement["product"].get("id") or 0)
            self.log.append({
                "listing_id": listing_id,
                "seller_user_id": int(placement["product"].get("seller_user_id") or 0),
                "category": self.category_of(listing_id),
                "surface": placement["surface"],
                "reason": placement.get("reason") or "",
            })
        self.conn.commit()

    def mark(self) -> int:
        """A cursor into the log, so a test can ask "and what happened after?"."""
        return len(self.log)

    def since(self, mark: int) -> list[dict]:
        return list(self.log[mark:])

    def browse(self, surface: str = "feed", *, steps: int = 50, seconds: float = 30.0, **kwargs) -> list[dict]:
        """Serve, render, advance — ``steps`` times.

        Returns a *copy* of the impressions this call produced, not the live log.
        Handing back ``self.log`` made every ``before = browse(); after =
        browse()[len(before):]`` read empty, because ``before`` had grown too.
        """
        start = len(self.log)
        for _ in range(steps):
            self.render(self.serve(surface, **kwargs))
            self.clock.advance(seconds)
        return list(self.log[start:])

    # -- signals the brief asks the router to respect ------------------------
    def purchase(
        self,
        listing_id: int,
        *,
        amount_cents: int = 0,
        currency: str = "USD",
        status: str = "paid",
        provider_payment_id: str = "",
        buyer_user_id: int | None = None,
    ) -> int:
        """Place an order, and return its id — which is what an engagement event
        cites as ``order_ref`` when it claims a sale."""
        cur = self.conn.cursor()
        stamp = subject.iso(self.clock.now)
        cur.execute(
            "INSERT INTO marketplace_orders "
            "(buyer_user_id, listing_id, amount_cents, currency, status, "
            " provider_payment_id, paid_at, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                int(self.viewer_id if buyer_user_id is None else buyer_user_id),
                int(listing_id), int(amount_cents), currency, status,
                provider_payment_id or None, stamp, stamp,
            ),
        )
        order_id = int(cur.lastrowid)
        self.conn.commit()
        return order_id

    def add_to_cart(self, listing_id: int, qty: int = 1) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_cart_items (user_id, listing_id, qty) VALUES (?,?,?)",
            (self.viewer_id, int(listing_id), int(qty)),
        )
        self.conn.commit()

    def save(self, listing_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_saved_products (user_id, listing_id, created_at) VALUES (?,?,?)",
            (self.viewer_id, int(listing_id), subject.iso(self.clock.now)),
        )
        self.conn.commit()

    def seller_of(self, listing_id) -> int:
        """Who owns a listing *now*, read back from the table.

        Read rather than computed from ``1001 + (id-1) % 10`` on purpose: the
        formula is only true until :meth:`transfer` runs, and a helper that keeps
        answering the old owner would make the stale-authorisation tests pass by
        agreeing with the bug.
        """
        cur = self.conn.cursor()
        cur.execute("SELECT seller_user_id FROM marketplace_listings WHERE id=?", (int(listing_id),))
        row = cur.fetchone()
        return int((row or {"seller_user_id": 0})["seller_user_id"] or 0)

    def tag(self, listing_id, *, post_id: int, content_type: str = "post", user_id=None) -> dict:
        """Attach a product to content, as the composer would.

        ``user_id`` defaults to the listing's *current* owner, so the happy path is
        one argument. A test about refusals passes it explicitly.
        """
        actor = self.seller_of(listing_id) if user_id is None else int(user_id)
        result = tagging.attach(
            self.conn.cursor(),
            content_type=content_type,
            content_id=post_id,
            listing_id=listing_id,
            user_id=actor,
        )
        self.conn.commit()
        return result

    def transfer(self, listing_id, new_seller_user_id: int) -> None:
        """Sell a listing to a different seller.

        The thing `tagging.tagged_listing_ids`' JOIN exists to notice: the tag row
        still names the old owner, who is the person that actually granted it.
        """
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE marketplace_listings SET seller_user_id=? WHERE id=?",
            (int(new_seller_user_id), int(listing_id)),
        )
        self.conn.commit()

    def follow_seller(self, seller_user_id: int) -> None:
        """Follow a seller socially, which is the only thing that licenses the
        "from sellers you follow" claim."""
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO pulse_follows "
            "(follower_user_id, followed_user_id, created_at) VALUES (?,?,?)",
            (self.viewer_id, int(seller_user_id), subject.iso(self.clock.now)),
        )
        self.conn.commit()

    def engage(
        self,
        placement: dict,
        action: str = "click",
        *,
        order_ref: str = "",
        quantity=None,
        parse_price=parse_price,
    ) -> dict:
        """A positive outcome on a placement — click, product_view, purchase.

        Distinct from :meth:`feedback`, which is the *negative* path ("not
        interested", "hide seller") and rejects these verbs. Both exist because
        the two write to different tables and mean opposite things; naming only
        one of them ``feedback`` is what makes the confusion possible.

        ``parse_price`` defaults to the fixture's parser rather than to ``None``
        so the *priced* branch is the one tests take by default. Leaving it out
        would have left every intent event on the no-parser short circuit, worth
        zero for a reason that has nothing to do with what is being tested —
        a fail-soft path no test ever leaves. Pass ``parse_price=None``
        explicitly to exercise that branch.
        """
        return events.record_engagement(
            self.conn.cursor(),
            placement["placement_id"],
            placement["impression_token"],
            action,
            conn=self.conn,
            buyer_user_id=self.viewer_id,
            order_ref=order_ref,
            claimed_quantity=quantity,
            parse_price=parse_price,
        )

    def feedback(self, placement: dict, action: str) -> dict:
        return events.record_feedback(
            self.conn.cursor(),
            placement["placement_id"],
            placement["impression_token"],
            action,
            conn=self.conn,
        )


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(subject, "now_utc", fake)
    return fake


@pytest.fixture
def market(clock, monkeypatch) -> SimulatedMarketplace:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    for statement in _MARKETPLACE_DDL:
        cur.execute(statement)

    # The DDL guard caches success for the whole process, so the second test in
    # this file would otherwise run against a fresh database it believes it has
    # already migrated — "no such table" in whichever test happens to run second.
    schema.ensure_schema.reset()
    schema.ensure_schema(conn)
    conn.commit()

    # Same class of hazard, one module over. `pool.catalogue_span` caches the
    # eligible catalogue size per process for a rotation period, so without this
    # the second test in a file inherits the first test's catalogue size and its
    # rotation offsets are computed for a marketplace that no longer exists. It
    # was caught by a reachability probe reporting an unchanged number after the
    # span was wired up — the span was correct and stale.
    pool.reset_span_cache()

    def stub_preferences(_cur, _user_id):
        return {"commerce": {}}, None, None

    real_policy = preferences.viewer_policy
    monkeypatch.setattr(
        preferences,
        "viewer_policy",
        lambda cursor, user_id, **kwargs: real_policy(
            cursor, user_id, load_preferences=stub_preferences
        ),
    )

    # The feed's per-session cap is six placements an hour, which is the right
    # production number and makes a hundred-request scroll test measure the cap
    # rather than the rotation. Raised here so the thing under test is the thing
    # being tested; the cap has its own test.
    monkeypatch.setenv("COMMERCE_DISCOVERY_FEED_MAX_PER_SESSION", "100000")
    monkeypatch.setenv("COMMERCE_DISCOVERY_REELS_MAX_PER_SESSION", "100000")
    monkeypatch.setenv("COMMERCE_DISCOVERY_MESSENGER_MAX_PER_SESSION", "100000")

    market = SimulatedMarketplace(conn, clock)
    market.seed()
    try:
        yield market
    finally:
        conn.close()
        schema.ensure_schema.reset()


# --------------------------------------------------------------------------- #
# The suite's blind spot, closed.
#
# `engine.serve` wraps `_serve` in `except Exception: return []`. That fail-safe
# is correct and must stay — a post has to render when commerce breaks. But it
# means a crash and a decision are the same value to a caller, and *most of the
# assertions in this package are about that value*. Measured 2026-09-27 by
# raising `TypeError` on `_serve`'s first line: 110 tests went red and **558
# stayed green**. Some of those legitimately never call `serve`. The rest are
# every negative assertion in the package — opted-out viewer, cap reached,
# surface suppressed, pool empty — and each one passes just as well when the
# engine is a smoking hole, because `[] == []`.
#
# So the guard below reads the signal that already exists. `serve`'s fail-safe
# logs `COMMERCE_DISCOVERY_SERVE_FAILED` with the traceback; if that line was
# emitted during a test that otherwise passed, the test did not measure what it
# says it measured, and the report is flipped to a failure naming the exception.
#
# Deliberately not a production change. The alternative — a strict mode that
# re-raises under test — makes the tested code path differ from the shipped one,
# which is the class of mistake §18.6 was about.
#
# A test that *wants* a swallowed failure marks itself `commerce_serve_may_fail`.
# --------------------------------------------------------------------------- #

#: The prefix `engine.serve`'s fail-safe logs. Kept as a literal rather than
#: imported because there is nothing to import: it is a log message, and the
#: point of this guard is to notice if it starts being emitted, not to agree with
#: the engine about its spelling. `test_the_engine_still_logs_this_prefix` pins
#: the two together so a rename cannot quietly disarm the guard.
SERVE_FAILED_PREFIX = "COMMERCE_DISCOVERY_SERVE_FAILED"

#: `commerce_discovery_routes.commerce_discovery_serve` has a *second* fail-safe
#: wrapping the engine's, and it is the more dangerous of the two: it returns
#: ``{"ok": True, "placements": []}`` with **HTTP 200**, so a crashed route is
#: indistinguishable from "no products for you" to a test, to the client, and to
#: a dashboard.
#:
#: This is not covered by the prefix above, twice over, and both traps are worth
#: stating because each looks like it should work:
#:
#: 1. ``COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`` does not *start with*
#:    ``COMMERCE_DISCOVERY_SERVE_FAILED`` — the words diverge right after
#:    ``SERVE_``. A `startswith` on the engine's prefix misses it.
#: 2. `logging` propagates records to *ancestors*. ``commerce_discovery_routes``
#:    is a sibling of ``commerce_discovery.engine``, not a descendant, so a
#:    handler on the engine's logger never sees it however the message is spelled.
#:
#: Measured 2026-09-27 by raising `TypeError` on the route handler's first line:
#: **37 tests stayed green**, and they are the suitability tests — the ones that
#: certify PulseSoc does not put a shopping card next to a bereavement post.
#: Those tests already carry a second assertion against exactly this class of
#: mistake, `assert not serve.called`, and the crash satisfies it *more*
#: thoroughly than a real refusal does. A stronger assertion in the same
#: direction is still the same direction.
ROUTE_FAILED_PREFIX = "COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED"

#: Both fail-safes, and the loggers to watch them on. A tuple rather than two
#: code paths so that adding the next fail-safe is one line and cannot forget the
#: report, the marker, or the teardown.
WATCHED_FAILSAFES = (
    ("services.commerce_discovery.engine", SERVE_FAILED_PREFIX),
    ("services.commerce_discovery_routes", ROUTE_FAILED_PREFIX),
)

_RECORDER_KEY = pytest.StashKey["_SwallowedServeFailures"]()


class _SwallowedServeFailures(logging.Handler):
    """Collects every watched fail-safe's log records for one test.

    One handler instance is attached to each logger in `WATCHED_FAILSAFES`, but it
    filters on *all* the prefixes rather than only the one belonging to the logger
    it is attached to. That is deliberate: if a fail-safe is ever moved between
    modules the guard keeps working, and the alternative — a per-logger prefix —
    would silently stop watching the moved one.
    """

    #: Matched against the *start* of the message. Longest first is not needed
    #: (they are checked with `any`), but note that `SERVE_FAILED` is **not** a
    #: prefix of `SERVE_ROUTE_FAILED`, which is why both must be listed.
    PREFIXES = tuple(prefix for _logger, prefix in WATCHED_FAILSAFES)

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a broken record is not our business
            return
        if not any(message.startswith(prefix) for prefix in self.PREFIXES):
            return
        # One instance is attached to several loggers, and a record can reach it
        # more than once if those loggers are ever nested. Identity-dedupe rather
        # than trusting the hierarchy to stay flat.
        if any(seen is record for seen in self.records):
            return
        self.records.append(record)

    def detail(self) -> str:
        formatter = logging.Formatter()
        chunks = []
        for record in self.records:
            chunks.append(record.getMessage())
            if record.exc_info:
                chunks.append(formatter.formatException(record.exc_info))
        return "\n".join(chunks)


def swallowed_failure_report(item, report) -> str:
    """The text to fail ``report`` with, or ``""`` to let it stand.

    Split out of the hook so it can be tested as a function. Three conditions,
    each of which exists to stop the guard being noise:

    * only the ``call`` phase — a setup or teardown report has its own story.
    * only a report that otherwise **passed**. A test that already failed does
      not need a second opinion, and flipping it would double every failure in a
      run where the engine is genuinely broken.
    * only without the opt-out marker.
    """
    if getattr(report, "when", "") != "call" or not getattr(report, "passed", False):
        return ""
    if item.get_closest_marker("commerce_serve_may_fail") is not None:
        return ""
    recorder = item.stash.get(_RECORDER_KEY, None)
    if recorder is None or not recorder.records:
        return ""
    # Name the module that actually swallowed it. "engine.serve failed" sent
    # someone to read the wrong file the first time the route fail-safe fired.
    where = sorted({record.name for record in recorder.records}) or ["a fail-safe"]
    return (
        f"This test passed, but {', '.join(where)} swallowed an exception while it "
        "ran, so whatever it asserted about the result was asserted about a "
        "fail-safe's empty list rather than about a decision anything made.\n\n"
        "Note that an empty-result assertion is not enough to catch this, and "
        "neither is a stronger assertion in the same direction: `assert not "
        "serve.called` is satisfied by a crash before retrieval too.\n\n"
        "Fix the exception. If the failure is the point of the test, mark it "
        "`@pytest.mark.commerce_serve_may_fail`.\n\n"
        + recorder.detail()
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "commerce_serve_may_fail: this test expects `engine.serve` to swallow an "
        "exception; do not treat the fail-safe log line as a defect.",
    )


@pytest.fixture(autouse=True)
def swallowed_serve_failures(request):
    """Watch every fail-safe logger for the whole test, and hand the recorder to
    the report hook through the item's stash.

    Attached by logger *name* rather than by importing the modules.
    ``commerce_discovery_routes`` imports `bot` at call time, and a conftest that
    imported it at collection would make every test in the package pay for 111k
    lines to answer a question about a log record. `logging.getLogger` on a name
    that has not been imported yet is legal and returns the same object the module
    will use when it is.

    Yielded as well as stashed so a test can assert on it directly."""
    recorder = _SwallowedServeFailures()
    request.node.stash[_RECORDER_KEY] = recorder
    loggers = [logging.getLogger(name) for name, _prefix in WATCHED_FAILSAFES]
    for logger in loggers:
        logger.addHandler(recorder)
    try:
        yield recorder
    finally:
        for logger in loggers:
            logger.removeHandler(recorder)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    detail = swallowed_failure_report(item, report)
    if detail:
        report.outcome = "failed"
        report.longrepr = detail
    return report
