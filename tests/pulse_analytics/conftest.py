"""A real database with two sellers in it, because the risk here is a leak.

The whole authorisation surface of this package is a ``WHERE`` clause. A test
that hands the read layer a fake cursor and asserts on the SQL string would pass
against a query that returns the wrong rows, so the fixture is a real SQLite
database with the real ``commerce_discovery`` schema and events written through
``events.record_impression`` — the same path the client uses.

Two sellers exist in every fixture and both have traffic. A single-seller
fixture cannot fail a cross-seller isolation test: with nobody else's rows in
the table, a query that forgot to filter returns the correct answer by accident.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from services.commerce_discovery import schema, subject

EPOCH = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)

SELLER_A = 101
SELLER_B = 202

#: Listing ids, kept far apart so a mix-up in a failure message is obvious.
A_LISTINGS = (1001, 1002)
B_LISTINGS = (2001,)

_LISTINGS_DDL = """
CREATE TABLE marketplace_listings (
    id INTEGER PRIMARY KEY,
    seller_user_id INTEGER,
    status TEXT,
    approval_status TEXT,
    quantity INTEGER,
    product_type TEXT,
    price_label TEXT,
    category TEXT
)
"""

#: Column names and types copied from `bot.init_db()`. `created_at` is TEXT
#: holding ISO-8601 in two shapes, which is the reason `read.orders` windows in
#: Python rather than in SQL.
_ORDERS_DDL = """
CREATE TABLE seller_transactions (
    id INTEGER PRIMARY KEY,
    seller_user_id INTEGER,
    status TEXT,
    amount_cents INTEGER,
    currency TEXT,
    item_id INTEGER,
    item_type TEXT,
    stripe_payment_intent_id TEXT,
    metadata_json TEXT,
    created_at TEXT
)
"""


@pytest.fixture
def clock(monkeypatch):
    """A fake clock, so "six days ago" is a test case and not a sleep."""

    state = {"now": EPOCH}

    def _now():
        return state["now"]

    monkeypatch.setattr(subject, "now_utc", _now)
    return state


@pytest.fixture
def conn(clock):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    # `ensure_schema` caches success for the life of the process. Without the
    # reset the second test in the file gets a fresh in-memory database and a
    # guard that believes the tables are already there.
    schema.ensure_schema.reset()
    schema.ensure_schema(connection)
    cur = connection.cursor()
    cur.execute(_LISTINGS_DDL)
    cur.execute(_ORDERS_DDL)
    for listing_id in A_LISTINGS:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, status, approval_status) "
            "VALUES (?,?,'active','approved')",
            (listing_id, SELLER_A),
        )
    for listing_id in B_LISTINGS:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, status, approval_status) "
            "VALUES (?,?,'active','approved')",
            (listing_id, SELLER_B),
        )
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def cur(conn):
    return conn.cursor()


def write_impression(
    cur,
    *,
    listing_id: int,
    seller_user_id: int,
    subject_ref: str = "viewer-hash-1",
    surface: str = "marketplace",
    self_view: int = 0,
    minutes_ago: int = 1,
    event_id: str = "",
) -> str:
    """An impression row written directly, so a test can control every column.

    Direct rather than through ``record_impression`` on purpose: these tests
    need to place rows at chosen times and with chosen ``self_view`` values,
    which the recording path derives rather than accepts.
    """
    at = subject.iso(subject.now_utc() - timedelta(minutes=minutes_ago))
    event_id = event_id or f"impr-{listing_id}-{minutes_ago}-{self_view}-{subject_ref}"
    cur.execute(
        "INSERT INTO commerce_discovery_impression_events "
        "(event_id, placement_id, subject_ref, surface, slot, listing_id, seller_user_id, "
        " promotion_class, reason_code, ranking_version, session_id, visible, "
        " view_duration_ms, self_view, request_meta_json, event_at, dedup_key, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id, f"plc-{event_id}", subject_ref, surface, 0, listing_id, seller_user_id,
            "organic", "rank", "v1", "sess-1", 1, 1200, self_view, "{}", at, event_id, at,
        ),
    )
    return event_id


def write_order(
    cur,
    *,
    item_id: int,
    seller_user_id: int,
    status: str = "succeeded",
    amount_cents: int = 1000,
    minutes_ago: int = 1,
    created_at: str = "",
    order_id: int = 0,
) -> int:
    """An order row. ``created_at`` overridable so a test can write a bad one."""
    at = created_at or subject.iso(subject.now_utc() - timedelta(minutes=minutes_ago))
    order_id = order_id or (abs(hash((item_id, seller_user_id, status, minutes_ago))) % 10**8)
    cur.execute(
        "INSERT INTO seller_transactions "
        "(id, seller_user_id, status, amount_cents, currency, item_id, item_type, "
        " stripe_payment_intent_id, metadata_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            order_id, seller_user_id, status, amount_cents, "USD", item_id, "listing",
            "pi_test", "{}", at,
        ),
    )
    return order_id


def write_engagement(
    cur,
    *,
    listing_id: int,
    seller_user_id: int,
    action: str,
    subject_ref: str = "viewer-hash-1",
    surface: str = "marketplace",
    minutes_ago: int = 1,
    event_id: str = "",
) -> str:
    at = subject.iso(subject.now_utc() - timedelta(minutes=minutes_ago))
    event_id = event_id or f"eng-{listing_id}-{action}-{minutes_ago}-{subject_ref}"
    cur.execute(
        "INSERT INTO commerce_discovery_engagement_events "
        "(event_id, placement_id, impression_event_id, subject_ref, surface, listing_id, "
        " seller_user_id, promotion_class, reason_code, ranking_version, session_id, action, "
        " value_minor, currency, order_ref, request_meta_json, event_at, dedup_key, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id, f"plc-{event_id}", None, subject_ref, surface, listing_id, seller_user_id,
            "organic", "rank", "v1", "sess-1", action, 0, "USD", None, "{}", at, event_id, at,
        ),
    )
    return event_id
