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

import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.commerce_discovery import engine, events, preferences, schema, subject

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
        status TEXT,
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
                created, created, 0, "published", "approved", 90, "",
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

    def history(self, listing_ids, *, impressions: int = 0, clicks: int = 0) -> None:
        """Give some listings a past, earned by *other* shoppers.

        Written under a different ``subject_ref`` on purpose. ``_listing_stats``
        counts these — which is what turns a listing from unproven into popular
        or trending, and therefore what makes the marketplace produce more than
        one shelf. The viewer's own exposure read filters on ``subject_ref``, so
        none of it counts as something *this* person has already been shown.
        """
        cur = self.conn.cursor()
        stamp = subject.iso(self.clock.now - timedelta(days=1))
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
    def serve(self, surface: str = "feed", *, limit=None, context=None, session_id="cs_test") -> list[dict]:
        return engine.serve(
            self.conn.cursor(),
            self.viewer_id,
            surface,
            context=context,
            session_id=session_id,
            limit=limit,
            parse_price=parse_price,
        )

    def render(self, placements, *, visible: bool = True) -> None:
        """Report placements as impressions, exactly as the client would."""
        cur = self.conn.cursor()
        for placement in placements:
            events.record_impression(
                cur,
                placement["placement_id"],
                placement["impression_token"],
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
    def purchase(self, listing_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_orders (buyer_user_id, listing_id, status, paid_at, created_at) "
            "VALUES (?,?,?,?,?)",
            (self.viewer_id, int(listing_id), "paid", subject.iso(self.clock.now), subject.iso(self.clock.now)),
        )
        self.conn.commit()

    def add_to_cart(self, listing_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_cart_items (user_id, listing_id, qty) VALUES (?,?,1)",
            (self.viewer_id, int(listing_id)),
        )
        self.conn.commit()

    def save(self, listing_id: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO marketplace_saved_products (user_id, listing_id, created_at) VALUES (?,?,?)",
            (self.viewer_id, int(listing_id), subject.iso(self.clock.now)),
        )
        self.conn.commit()

    def feedback(self, placement: dict, action: str) -> dict:
        return events.record_feedback(
            self.conn.cursor(),
            placement["placement_id"],
            placement["impression_token"],
            action=action,
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
    schema.ensure_schema(cur)
    conn.commit()

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
