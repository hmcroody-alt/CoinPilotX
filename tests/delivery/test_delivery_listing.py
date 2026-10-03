"""Who ships this listing is read, never inferred — and a draft is never quoted.

What this file is defending
---------------------------
Five failures, none of which raises anything:

* **A warehouse window for a parcel the seller posts themselves.** A listing can
  be imported from CJ and stocked in the merchant's own garage. It has a CJ
  source row, CJ ids and a real weight, so CJ will price a shipment it will never
  make. Reading ``provider`` without ``fulfillment_mode`` produces a confident,
  wrong, CJ-shaped promise for a parcel going out by local post.
* **Freight quoted for a download.** A digital listing with a stale supplier
  binding must not get a transit time. The supplier dispatcher already refuses a
  non-physical order, so the estimate and the order have to agree about it.
* **Drafts and rejected listings quoted, and CJ's cache warmed by them.** Nothing
  else in this package reads ``marketplace_listings``, so without this module the
  engine would spend a supplier call per unpublished product and hand out a
  correct estimate for something nobody can buy.
* **A partial connection triple used to authorize.** Two of three coordinates
  select a different merchant's connection, or none, and either way the failure
  is a credential problem wearing a catalogue problem's clothes.
* **A seller's free-text ``estimated_delivery`` leaking back in.** It is a
  hard-coded duration in a database column, printed verbatim by the storefront
  today under the same label a computed window would use.

Two of these are *precedence* decisions rather than parse decisions, so each is
asserted with the competing fact present and pointing the other way: the digital
test has a live CJ dropship source row, and the stocked test has everything a
freight quote needs. A test with only the deciding fact present would pass
against an implementation that read the wrong column.

The tests drive real SQL against a temp SQLite file, with the supplier tables
built from the production DDL constants and the two marketplace tables built from
the columns ``public_sql`` actually names — pinned by a test, so a change to that
predicate fails here instead of silently matching nothing.
"""

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_DB = tempfile.NamedTemporaryFile(prefix="pulsesoc-delivery-listing-", suffix=".db",
                                  delete=False)
_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB.name}"

from services import db, marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.delivery import listing, quote, variant_facts  # noqa: E402

LISTING = 7001
OTHER_LISTING = 7002
SELLER = 55
PID = "PID-1"
VID = "VID-1"

CONNECTION = "conn-abc"
BUSINESS = "biz-1"
STORE = "store-1"

#: The three coordinates a supplier adapter is built from, spelled here as the
#: column names rather than borrowed from the module, so a renamed column is a
#: failure and not a silently-renamed expectation.
CONNECTION_COLUMNS = ("supplier_connection_id", "business_id", "store_id")

#: Minimal, but not arbitrary: every column here is one ``public_sql`` names or
#: ``effective_listing_type`` reads, plus ``estimated_delivery`` so a test can
#: prove it is never read. Pinned by
#: ``test_the_fixture_covers_every_column_the_visibility_predicate_names``.
_LISTINGS_DDL = """
CREATE TABLE marketplace_listings (
    id INTEGER PRIMARY KEY,
    seller_user_id INTEGER,
    status TEXT,
    approval_status TEXT,
    listing_type TEXT,
    product_type TEXT,
    quantity INTEGER,
    estimated_delivery TEXT,
    -- Named by `public_sql`, so the column-coverage test below requires it.
    -- Left NULL by every insert: NULL is "no publication decision recorded",
    -- which both halves of the predicate read as not-held, so every delivery
    -- verdict in this file keeps the meaning it was written with.
    commerce_publication_enabled INTEGER
)
"""

_SELLERS_DDL = """
CREATE TABLE marketplace_sellers (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE,
    display_name TEXT,
    status TEXT
)
"""


@pytest.fixture(autouse=True)
def schema():
    conn = db.connect()
    try:
        conn.execute("DROP TABLE IF EXISTS marketplace_listings")
        conn.execute("DROP TABLE IF EXISTS marketplace_sellers")
        conn.execute(f"DROP TABLE IF EXISTS {supplier_schema.SOURCE_TABLE}")
        conn.execute(_LISTINGS_DDL)
        conn.execute(_SELLERS_DDL)
        conn.execute(supplier_schema.SOURCE_TABLE_DDL)
        conn.commit()
    finally:
        conn.close()
    yield


def seller(*, status="approved", name="Real Store"):
    conn = db.connect()
    try:
        conn.execute("INSERT OR REPLACE INTO marketplace_sellers "
                     "(user_id, display_name, status) VALUES (?,?,?)",
                     (SELLER, name, status))
        conn.commit()
    finally:
        conn.close()


def listing_row(*, listing_id=LISTING, status="published", approval="approved",
                listing_type="physical", product_type="physical", quantity=5,
                estimated_delivery=None):
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO marketplace_listings "
            "(id, seller_user_id, status, approval_status, listing_type, "
            " product_type, quantity, estimated_delivery) VALUES (?,?,?,?,?,?,?,?)",
            (listing_id, SELLER, status, approval, listing_type, product_type,
             quantity, estimated_delivery))
        conn.commit()
    finally:
        conn.close()


def source_row(*, listing_id=LISTING, provider="cj", mode=supplier_schema.MODE_DROPSHIP,
               connection=CONNECTION, business=BUSINESS, store=STORE):
    conn = db.connect()
    try:
        conn.execute(
            f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
            f"(listing_id, seller_user_id, provider, provider_product_id, "
            f" fulfillment_mode, supplier_connection_id, business_id, store_id, "
            f" provider_variant_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (listing_id, SELLER, provider, PID, mode, connection, business, store, VID))
        conn.commit()
    finally:
        conn.close()


def published(**kwargs):
    """A listing a buyer can see, with an approved seller behind it."""
    seller()
    listing_row(**kwargs)


def ask(ref=str(LISTING)):
    return listing.declaration(ref)


# ---------------------------------------------------------------------------
# The declaration: provider AND mode, never one of them
# ---------------------------------------------------------------------------

def test_a_cj_dropship_listing_is_declared_supplier_fulfilled():
    published()
    source_row()
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SUPPLIER
    assert got["supplier"] == {"connection_id": CONNECTION, "business_id": BUSINESS,
                               "store_id": STORE}


def test_a_cj_listing_the_merchant_stocks_is_not_supplier_fulfilled():
    """The failure this whole module exists for. Everything a freight quote needs
    is present -- CJ provider, CJ ids, a live connection -- and the merchant has
    declared that they hold the stock. Reading ``provider`` alone quotes a
    warehouse-to-door window for a parcel going out by local post.

    The same pair gates the live dispatch path: ``marketplace_supplier_checkout``
    answers ``merchant_stocked`` for exactly this row, so an estimate that said
    SUPPLIER here would be promising a shipment the dispatcher refuses to book.
    """
    published()
    source_row(mode=supplier_schema.MODE_STOCKED)
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SELLER
    assert got["supplier"] is None


def test_a_supplier_this_deployment_cannot_quote_is_not_supplier_fulfilled():
    """``PROVIDERS`` lists printful and printify. Neither appears in a line of
    code in this repository, and this package binds exactly one adapter, so
    answering SUPPLIER would hand a Printful product to the CJ provider."""
    published()
    source_row(provider="printful")
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SELLER
    assert got["supplier"] is None


def test_a_manual_variant_is_not_supplier_fulfilled():
    published()
    source_row(provider=supplier_schema.PROVIDER_MANUAL)
    assert ask()["fulfillment"] == quote.FULFILLMENT_SELLER


def test_a_listing_with_no_source_row_is_seller_fulfilled():
    """Not ``None``: nobody declared a supplier, and that is itself a declaration
    -- the seller posts it. ``None`` is reserved for a listing a buyer cannot see,
    so that the two cannot be confused one layer up."""
    published()
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SELLER
    assert got["supplier"] is None


@pytest.mark.parametrize("provider,mode", [("CJ", "DROPSHIP"), ("cj", "dropship"),
                                           ("Cj", " DropShip "), (" cj ", "DROPSHIP")])
def test_the_declaration_is_read_case_and_space_insensitively(provider, mode):
    """These columns are written by several callers, at least one of which stores
    the mode lowercase. A case-sensitive read here would reclassify a live
    dropship listing as seller-shipped on the strength of a capital letter."""
    published()
    source_row(provider=provider, mode=mode)
    assert ask()["fulfillment"] == quote.FULFILLMENT_SUPPLIER


# ``provider`` is NOT NULL in the production DDL, so a null provider is a row that
# cannot exist and is not worth a case. ``fulfillment_mode`` has a default but no
# NOT NULL, so a null mode is reachable -- and it is the dangerous one, because a
# row written before that column existed has it.
@pytest.mark.parametrize("provider,mode", [("cj", None), ("", "DROPSHIP"), ("cj", ""),
                                           ("  ", "DROPSHIP"), ("cj", "   ")])
def test_half_a_declaration_is_not_a_declaration(provider, mode):
    published()
    source_row(provider=provider, mode=mode)
    assert ask()["fulfillment"] == quote.FULFILLMENT_SELLER


def _legacy_source_table(*columns):
    """Rebuild the source table without some of the columns added later.

    Reachable, not hypothetical: ``fulfillment_mode``, ``business_id`` and
    ``store_id`` all arrive through ``add_columns_if_missing``, so a database that
    has not been through the current ``init_db`` has rows without them. It matters
    here and not for the listings query because that one projects its two columns
    by name -- a missing one fails in ``execute`` -- while the source row is read
    with ``SELECT *``, which happily returns a row that has no such key.
    """
    conn = db.connect()
    try:
        kept = [c for c in ("listing_id", "seller_user_id", "provider",
                            "provider_product_id", "provider_variant_id",
                            "fulfillment_mode", "supplier_connection_id",
                            "business_id", "store_id") if c not in columns]
        conn.execute(f"DROP TABLE {supplier_schema.SOURCE_TABLE}")
        conn.execute(f"CREATE TABLE {supplier_schema.SOURCE_TABLE} ("
                     "id INTEGER PRIMARY KEY, "
                     + ", ".join(f"{c} TEXT" for c in kept) + ")")
        conn.execute(f"INSERT INTO {supplier_schema.SOURCE_TABLE} "
                     f"({', '.join(kept)}) VALUES "
                     f"({', '.join('?' for _ in kept)})",
                     tuple({"listing_id": LISTING, "seller_user_id": SELLER,
                            "provider": "cj", "provider_product_id": PID,
                            "provider_variant_id": VID,
                            "fulfillment_mode": supplier_schema.MODE_DROPSHIP,
                            "supplier_connection_id": CONNECTION,
                            "business_id": BUSINESS, "store_id": STORE}[c]
                           for c in kept))
        conn.commit()
    finally:
        conn.close()


def test_a_source_row_predating_the_mode_column_is_not_supplier_fulfilled():
    """No column is no declaration, and the safe reading of no declaration is
    that the seller ships it. The unsafe reading is the tempting one: the row
    names CJ, carries CJ ids, and the column's own DDL default is ``DROPSHIP``, so
    "assume the default" looks like faithfulness to the schema. It is not -- the
    default applies to rows written after the column existed, and these rows were
    not."""
    published()
    _legacy_source_table("fulfillment_mode")
    assert ask()["fulfillment"] == quote.FULFILLMENT_SELLER


@pytest.mark.parametrize("absent", ["supplier_connection_id", "business_id",
                                    "store_id"])
def test_a_source_row_predating_a_connection_column_yields_no_coordinates(absent):
    """An absent column and an unset one are the same fact, and both must produce
    no triple rather than a two-thirds one. Asserted because the declaration is
    still ``SUPPLIER``: the merchant did declare it, and it is the coordinates
    that are missing -- see ``_supplier``'s own docstring."""
    published()
    _legacy_source_table(absent)
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SUPPLIER
    assert got["supplier"] is None


def test_only_this_listings_source_row_is_read():
    """A dropped predicate would read a neighbouring listing's binding, which is
    how one product's supplier ends up quoting another product's parcel."""
    published()
    listing_row(listing_id=OTHER_LISTING)
    source_row(listing_id=OTHER_LISTING)
    assert ask()["fulfillment"] == quote.FULFILLMENT_SELLER
    assert ask(str(OTHER_LISTING))["fulfillment"] == quote.FULFILLMENT_SUPPLIER


# ---------------------------------------------------------------------------
# Nothing ships beats anyone ships
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["digital", "service", "event", "booking"])
def test_a_listing_that_posts_nothing_gets_no_delivery_declaration(kind):
    """Asserted with a live CJ dropship source row present and pointing the other
    way. Without it the test would pass against an implementation that consulted
    the source row first, which is the precedence that quotes freight for a
    download."""
    published(listing_type=kind, product_type=kind, quantity=0)
    source_row()
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_DIGITAL
    assert got["supplier"] is None


def test_the_listing_type_column_outranks_the_legacy_product_type():
    """``effective_listing_type``'s rule, not a second copy of it: ``listing_type``
    arrived after ``product_type``, so a row carrying both means the newer column
    was written deliberately."""
    published(listing_type="physical", product_type="digital")
    source_row()
    assert ask()["fulfillment"] == quote.FULFILLMENT_SUPPLIER


def test_a_legacy_row_with_only_product_type_still_classifies():
    published(listing_type=None, product_type="digital", quantity=0)
    assert ask()["fulfillment"] == quote.FULFILLMENT_DIGITAL


def test_a_row_with_neither_type_column_is_treated_as_physical():
    """``effective_listing_type`` falls through to ``physical``, and this module
    must not substitute a safer-looking guess: a physical listing declared
    dropship is quotable, and calling it digital would suppress every estimate on
    a legacy catalogue."""
    published(listing_type=None, product_type=None)
    source_row()
    assert ask()["fulfillment"] == quote.FULFILLMENT_SUPPLIER


# ---------------------------------------------------------------------------
# Visibility, asked of the canonical predicate
# ---------------------------------------------------------------------------

def test_a_published_approved_listing_with_a_named_seller_is_visible():
    published()
    assert ask()["visible"] is True


@pytest.mark.parametrize("status", ["draft", "paused", "removed", "", None])
def test_a_listing_the_merchant_has_not_released_is_not_visible(status):
    published(status=status)
    source_row()
    assert ask() == {"visible": False, "fulfillment": None, "supplier": None}


@pytest.mark.parametrize("approval", ["pending_review", "rejected", "", None])
def test_a_listing_without_an_approval_is_not_visible(approval):
    published(approval=approval)
    source_row()
    assert ask()["visible"] is False


@pytest.mark.parametrize("seller_status", ["pending", "suspended", "", None])
def test_a_listing_behind_an_unapproved_seller_is_not_visible(seller_status):
    seller(status=seller_status)
    listing_row()
    source_row()
    assert ask()["visible"] is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_a_listing_whose_seller_has_no_store_name_is_not_visible(name):
    """The store-name invariant is part of ``public_sql``. Reproduced here not
    because delivery cares about store names but because asking the canonical
    predicate means inheriting all of it, and a test per rule is what proves the
    predicate was asked rather than partially copied."""
    seller(name=name)
    listing_row()
    source_row()
    assert ask()["visible"] is False


def test_a_physical_listing_with_no_stock_is_not_visible():
    published(quantity=0)
    source_row()
    assert ask()["visible"] is False


def test_a_listing_with_no_seller_row_at_all_is_not_visible():
    listing_row()
    source_row()
    assert ask()["visible"] is False


def test_a_listing_that_does_not_exist_answers_exactly_like_an_invisible_one():
    """Deliberately indistinguishable. This runs on an unauthenticated surface, and
    an answer that differed would let a caller enumerate which listing ids exist
    as drafts."""
    published(status="draft")
    draft = ask()
    missing = ask("999999")
    assert draft == missing == {"visible": False, "fulfillment": None, "supplier": None}


def test_an_invisible_listing_declares_no_fulfillment():
    """So a caller that ignores ``visible`` and passes ``fulfillment`` straight to
    ``quote_delivery`` gets ``fulfillment_undeclared`` -- no estimate -- rather
    than an estimate for a listing nobody may buy."""
    published(status="draft")
    got = ask()
    assert got["fulfillment"] is None
    answered = quote.quote_delivery(
        fulfillment=got["fulfillment"], destination={"country": "US"},
        now=None, handling=None, buffer_days=None)
    assert answered["buyer"]["reason"] == quote.REASON_FULFILLMENT_UNDECLARED
    assert answered["buyer"]["earliest"] is None


def test_the_fixture_covers_every_column_the_visibility_predicate_names():
    """A drift guard on the harness, not on the module.

    ``public_sql`` is interpolated into this module's SQL, so if it comes to read
    a sixth column that this file's minimal tables do not have, every visibility
    test above fails with an OperationalError and the diagnosis is a broken
    fixture rather than a changed contract. Naming the columns here makes the
    failure say which one appeared.
    """
    predicate = lifecycle.public_sql("l", "ms")
    named = set(re.findall(r"\b(?:l|ms)\.(\w+)", predicate))
    conn = db.connect()
    try:
        have = set()
        for table in ("marketplace_listings", "marketplace_sellers"):
            cur = conn.cursor()
            cur.execute(f"SELECT * FROM {table} LIMIT 0")
            have.update(column[0] for column in cur.description)
            cur.close()
    finally:
        conn.close()
    assert named, "the predicate named no columns -- this test has stopped reading it"
    assert named <= have, f"the fixture is missing {sorted(named - have)}"


# ---------------------------------------------------------------------------
# The connection triple: all three, or none
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", CONNECTION_COLUMNS)
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_an_incomplete_connection_triple_yields_no_adapter_coordinates(missing, blank):
    """Still SUPPLIER, and no coordinates. The listing really is supplier-fulfilled
    and we really cannot reach the supplier, and both halves of that have to
    survive: reclassifying it would tell the buyer the seller arranges delivery,
    and inventing a partial triple would authorize against whichever connection
    two columns happen to match."""
    published()
    columns = {"connection": CONNECTION, "business": BUSINESS, "store": STORE}
    columns[{"supplier_connection_id": "connection", "business_id": "business",
             "store_id": "store"}[missing]] = blank
    source_row(**columns)
    got = ask()
    assert got["fulfillment"] == quote.FULFILLMENT_SUPPLIER
    assert got["supplier"] is None


def test_the_adapter_coordinates_hold_nothing_but_the_triple():
    """The source row also carries the seller's user id and CJ's wholesale cost.
    This dict is handed to a credential lookup, and anything extra in it is a
    field some future caller reads and forwards."""
    published()
    source_row()
    assert set(ask()["supplier"]) == {"connection_id", "business_id", "store_id"}


def test_the_declaration_carries_no_seller_identity_or_cost():
    published()
    source_row()
    got = ask()
    assert str(SELLER) not in repr(got)
    assert PID not in repr(got)
    assert VID not in repr(got)


# ---------------------------------------------------------------------------
# What is deliberately not read
# ---------------------------------------------------------------------------

def test_the_sellers_free_text_delivery_estimate_is_never_read():
    """A hard-coded duration in a database column, printed verbatim by
    ``marketplace_storefront.fulfilment_html`` under the label "Estimated
    delivery". Empty on every production row today, which is the only reason
    there is no conflict yet."""
    published(estimated_delivery="Ships in 2-3 days, arrives within a week")
    source_row()
    got = ask()
    assert "2-3" not in repr(got)
    assert "week" not in repr(got)
    assert set(got) == {"visible", "fulfillment", "supplier"}


def test_the_module_does_not_read_the_free_text_column_at_all():
    """Asserted against the source, because the test above passes as long as the
    value is not *returned* -- and a module that read the column and used it to
    pick a branch would leave no trace in the result."""
    source = open(listing.__file__, encoding="utf-8").read()
    body = source.split('"""', 2)[-1]
    assert "estimated_delivery" not in body


# ---------------------------------------------------------------------------
# The record's shape, and the reference grammar
# ---------------------------------------------------------------------------

def test_the_record_always_has_the_same_keys():
    published()
    source_row()
    with_source = ask()
    published(listing_id=OTHER_LISTING)
    assert set(with_source) == set(ask(str(OTHER_LISTING))) == {
        "visible", "fulfillment", "supplier"}
    assert set(ask("999999")) == {"visible", "fulfillment", "supplier"}


@pytest.mark.parametrize("scenario", ["supplier", "seller", "digital", "absent"])
def test_every_fulfillment_a_declaration_can_report_is_a_declared_one(scenario):
    if scenario == "absent":
        assert ask("999999")["fulfillment"] is None
        return
    if scenario == "digital":
        published(listing_type="digital", product_type="digital", quantity=0)
    else:
        published()
    if scenario == "supplier":
        source_row()
    assert ask()["fulfillment"] in quote.FULFILLMENT_TYPES


@pytest.mark.parametrize("ref", [None, "", "   ", "abc", "0", "-1", ":blue", 7001, 1.0])
def test_a_reference_that_names_no_listing_is_a_programmer_error(ref):
    """Raised rather than answered, and raised by ``variant_facts.parse_ref`` rather
    than by a second parser here. One grammar across the domain means a reference
    cannot be accepted at this layer and rejected at the next."""
    published()
    with pytest.raises(variant_facts.VariantRefInvalid):
        listing.declaration(ref)


def test_a_reference_naming_a_variant_still_resolves_the_listing():
    """Fulfillment and visibility are the listing's facts, not the variant's, so
    the variant key is parsed and then correctly ignored."""
    published()
    source_row()
    assert ask(f"{LISTING}:blue") == ask(str(LISTING))


def test_the_connection_is_closed_even_when_the_listing_is_absent():
    """A product page that 404s must not leak a pooled connection. The pool is 8+8
    with a three-second timeout, so a leak per unpublished product is an outage."""
    opened = []

    def connect():
        conn = db.connect()
        opened.append(conn)
        return conn

    listing.declaration("999999", connect=connect)
    assert len(opened) == 1
    with pytest.raises(Exception):
        opened[0].execute("SELECT 1")
