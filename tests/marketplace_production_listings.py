"""The six real ``marketplace_listings`` rows, as a fixture.

Why this exists
---------------
``marketplace_listings`` is the only populated product ledger in production, and
the reconciliation that moved the supplier gateway onto it is only safe if those
six rows keep working. Asserting that against invented rows would prove very
little, because the invented ones would inherit whatever assumptions the test
author already held. These are the real ones, read out of production on
2026-09-07:

===  ===============  ==============  ===============  ================
id   seller_user_id   status          approval_status  listing_type
===  ===============  ==============  ===============  ================
8    1                published       approved         physical
9    1                review_ready    review_ready     physical
10   1                draft           approved         digital
11   1                review_ready    review_ready     service
12   1                review_ready    review_ready     event
13   1                published       approved         booking
===  ===============  ==============  ===============  ================

Three details are load-bearing and are the reason this is copied rather than
paraphrased:

*Ids start at 8.* There is no listing 1 through 7. A fixture that seeds ids
1..6 lets a test pass while the code under test silently assumes listing ids are
dense or one-based, and lets an off-by-one in an ownership lookup go unnoticed
because id 1 happens to exist.

*Every row has the same owner.* One seller holds all six, so a tenant-isolation
test that only ever seeds one owner per fixture proves nothing here. Callers that
need a second tenant add one explicitly — see ``FOREIGN_SELLER_ID``.

*Money is prose.* ``price_label`` is TEXT (``'$5.00'``) and there is no
``price_cents`` column. Supplier cost is integer minor units; retail is a string.
That asymmetry is the live state of the ledger and a fixture that quietly gave
listings an integer price would hide every place the two representations meet.

``status`` and ``approval_status`` also disagree on real rows — listing 10 is
``draft``/``approved`` — so "is this visible" cannot be read off either column
alone. That is why publication is tested by
``marketplace_listing_lifecycle.is_public`` rather than by a status literal.
"""

from __future__ import annotations

#: The owner of all six production rows.
PRODUCTION_SELLER_ID = 1

#: A second tenant, used to prove cross-owner refusals. Deliberately not 2 —
#: an adjacent id makes an off-by-one look like a pass.
FOREIGN_SELLER_ID = 90210

#: (id, seller_user_id, title, status, approval_status, listing_type,
#:  product_type, delivery_type, price_label, category, quantity)
PRODUCTION_LISTINGS = (
    (8, PRODUCTION_SELLER_ID, "Sports item", "published", "approved",
     "physical", "physical", "shipping", "$5.00", "Sports", 5),
    (9, PRODUCTION_SELLER_ID, "Beauty item", "review_ready", "review_ready",
     "physical", "physical", "physical", "$10.00", "Beauty", 3),
    (10, PRODUCTION_SELLER_ID, "Course", "draft", "approved",
     "digital", "digital", "digital", "$15.99", "Education", 0),
    (11, PRODUCTION_SELLER_ID, "Coaching", "review_ready", "review_ready",
     "service", "service", "service", "$20.89", "Education", 0),
    (12, PRODUCTION_SELLER_ID, "Workshop", "review_ready", "review_ready",
     "event", "event", "event", "$99.99", "Education", 77),
    (13, PRODUCTION_SELLER_ID, "Session", "published", "approved",
     "booking", "booking", "booking", "$0.50", "Education", 0),
)

#: The physical, published row — the only shape a drop-shipped supplier order
#: can legitimately attach to.
PHYSICAL_PUBLISHED_ID = 8

#: Physical but not yet approved. Importing against it must still be allowed
#: (import is a draft-time act); *publishing* it must not become automatic.
PHYSICAL_REVIEW_ID = 9

#: Digital. A CJ drop-ship intent against this must be refused — there is
#: nothing to ship.
DIGITAL_ID = 10

#: An id that has never existed. Used to prove that "absent" and "not yours"
#: refuse identically.
ABSENT_ID = 7

_DDL = """
CREATE TABLE IF NOT EXISTS marketplace_listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_user_id INTEGER,
    title TEXT,
    description TEXT,
    category TEXT,
    price_label TEXT DEFAULT 'Request access',
    -- The storefront's sort key. Mirrors `bot.init_db()`, where it is added
    -- with no DEFAULT so that NULL keeps meaning "not yet derived" rather
    -- than "free". This fixture is hand-rolled and therefore free to drift
    -- from the real schema; when it does, the symptom is a supplier test
    -- failing with `no column named ...` for code that is actually correct.
    price_minor INTEGER,
    status TEXT DEFAULT 'active',
    created_at TEXT,
    updated_at TEXT,
    approval_status TEXT DEFAULT 'pending_review',
    currency TEXT DEFAULT 'USD',
    quantity INTEGER DEFAULT 0,
    delivery_type TEXT DEFAULT 'digital',
    product_type TEXT DEFAULT 'digital',
    listing_type TEXT DEFAULT '',
    listing_metadata_json TEXT DEFAULT '',
    published_at TEXT,
    cover_image_url TEXT
)
"""

# Verbatim from `bot.init_db()`. Here rather than in the suites that need it
# because `marketplace_listings` alone cannot answer whether a listing is
# reachable: `lifecycle.public_sql` gates on the seller's status and store name,
# so every buyer-side predicate joins this table. A fixture with the listings and
# not the seller is not a smaller production -- it is one where those queries
# raise, and the suites that caught this were failing on a missing table rather
# than on anything they were written to assert.
_SELLERS_DDL = """
CREATE TABLE IF NOT EXISTS marketplace_sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    display_name TEXT,
    bio TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    updated_at TEXT
)
"""

_ORDERS_DDL = """
CREATE TABLE IF NOT EXISTS marketplace_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_transaction_id INTEGER UNIQUE,
    buyer_user_id INTEGER NOT NULL,
    seller_user_id INTEGER NOT NULL,
    listing_id INTEGER NOT NULL,
    quantity INTEGER DEFAULT 1,
    unit_price_cents INTEGER DEFAULT 0,
    amount_cents INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    status TEXT DEFAULT 'pending_payment',
    payment_provider TEXT DEFAULT 'stripe',
    provider_payment_id TEXT,
    created_at TEXT,
    paid_at TEXT,
    updated_at TEXT
)
"""


def seed_production_listings(cur, *, owner=PRODUCTION_SELLER_ID, extra_owner=None):
    """Create ``marketplace_listings`` and insert the six real rows.

    ``extra_owner`` adds one more listing owned by a different seller, so a
    cross-tenant refusal has something real to refuse rather than only an absent
    id. Returns that listing's id, or None.

    ``owner`` re-homes all six rows onto a different seller. Suites that reach the
    ledger through Business OS cannot choose their seller id — it is
    ``business_os_business.owner_user_id``, fixed by their own fixture — so
    without this they would have to hand-roll listings and lose the three
    production shapes above. What is load-bearing here is the *shape*: ids 8..13,
    one owner for all six, price as prose. Which integer that owner is, is not.

    Idempotent: re-running against the same cursor leaves the six rows alone.
    """
    owner = int(owner)
    cur.execute(_DDL)
    _seed_seller(cur, owner)
    cur.execute("SELECT COUNT(*) FROM marketplace_listings")
    row = cur.fetchone()
    existing = int(row[0] if not isinstance(row, dict) else list(row.values())[0])
    if existing == 0:
        for (listing_id, _seller, title, status, approval, listing_type,
             product_type, delivery, price_label, category, quantity) in PRODUCTION_LISTINGS:
            cur.execute(
                "INSERT INTO marketplace_listings (id, seller_user_id, title, category, "
                "price_label, status, approval_status, listing_type, product_type, "
                "delivery_type, currency, quantity, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (listing_id, owner, title, category, price_label, status, approval,
                 listing_type, product_type, delivery, "USD", quantity,
                 "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    if extra_owner is None:
        return None
    _seed_seller(cur, int(extra_owner))
    foreign_id = 99
    cur.execute(
        "INSERT INTO marketplace_listings (id, seller_user_id, title, category, "
        "price_label, status, approval_status, listing_type, product_type, "
        "delivery_type, currency, quantity, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (foreign_id, int(extra_owner), "Someone else's item", "Sports", "$1.00",
         "published", "approved", "physical", "physical", "shipping", "USD", 1,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
    return foreign_id


def _seed_seller(cur, user_id):
    """Give a listings owner the approved, named seller record production gives it.

    Approved and named, not pending and blank. These six rows model a catalogue a
    buyer can reach, and in production they are reachable *because* their owner is
    an approved seller with a store name -- two of the four rules
    :data:`lifecycle.PUBLICATION_RULES` checks. Seeding the table empty would make
    every publication count answer zero for a reason that has nothing to do with
    the listings, which is the failure mode where a test passes by asserting the
    right number for the wrong reason.

    A suite that wants the unhappy path updates this row; the six listings already
    cover unpublished and unapproved on the listing side.
    """
    cur.execute(_SELLERS_DDL)
    cur.execute(
        "INSERT OR IGNORE INTO marketplace_sellers "
        "(user_id, display_name, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (int(user_id), "Production Test Store", "approved",
         "2026-01-01T00:00:00", "2026-01-01T00:00:00"))


def seed_orders_table(cur):
    """Create ``marketplace_orders``. Separate because most suites do not need it."""
    cur.execute(_ORDERS_DDL)
