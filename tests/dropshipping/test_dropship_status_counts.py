"""The numbers a merchant is shown about their own catalogue must be measured. §15/§21.

What this file is defending
---------------------------
Every dropshipping surface that reports "12 imported · 3 need attention" is
making a claim about rows the merchant cannot see from that screen. The claim is
the only thing they have, so a wrong one is not a cosmetic defect: a merchant
told everything is published stops checking why nothing is selling.

``drafts.status_counts`` is the single place those numbers are computed, and the
properties asserted here are the ones that make it worth trusting:

* **A count is a measurement, not a restatement of a limit.** ``list_drafts``
  already carries a comment about the version of itself that returned ``len(rows)``
  after a LIMIT and told every merchant they had exactly one product. An
  aggregate built by paging would inherit that defect; this one is SQL.
* **``published`` requires both authorities to agree.** ``status`` and
  ``approval_status`` are separate columns and a buyer needs both. Counting
  ``status='published'`` alone reports a listing stuck in moderation as live.
* **Attention is not sync.** A listing selling below cost synced perfectly.
  Folding one into the other makes "the supplier is unreachable" and "the
  supplier raised their price" the same fact — the exact defect
  ``test_dropship_attention_state.py`` exists to prevent, arrived at from the
  reporting side instead.
* **The rollup is the worst state present, never the most common.** 99 synced
  products do not make a 100th error disappear.
* **An unrecognised sync state is not good news.** A value this module does not
  know is ranked worse than every value it does.
* **Another merchant's products are not counted.** The scope is asserted with a
  real second seller, not assumed from the WHERE clause being present.

Why this file runs alone
------------------------
``DATABASE_URL`` is bound to its own temp file at import, before ``services.db``
computes ``IS_POSTGRES``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_status_counts.py
"""

import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-counts-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer, revisions, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

from tests.dropshipping.test_dropship_revision_apply import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, OWNER_ID, PID, STORE, VID, FakeProvider,
    _seed_connection, _seed_tenancy, cj_product, import_one, publish, rows,
    set_store_policy, source_row)

# That import ran the applier suite's header, which pointed DATABASE_URL at *its*
# temp file. Restored, or every query below runs against a database this file
# never truncates.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


@pytest.fixture(autouse=True)
def database():
    open(_DB_PATH, "w").close()
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()
    if hasattr(gateway, "reset_schema_cache"):
        gateway.reset_schema_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        seed_production_listings(cur)
        cur.execute("DELETE FROM marketplace_listings")
        supplier_schema.ensure_supplier_schema(cur, force=True)
        _seed_tenancy(conn)
        connection_schema.ensure_schema(conn)
        _seed_connection(conn)
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    store_policy.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(importer.gateway, "read", fake)
    return fake


def counts():
    return drafts.status_counts(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)


def set_source(listing_id, **columns):
    """Write supplier-owned columns directly.

    The states this file needs — ERROR, DISCONNECTED, a stored attention list —
    are written by the worker and the reconciler on schedules a test cannot wait
    for. Writing the column is writing exactly what those paths write; the
    vocabulary itself is asserted against its owning module below rather than
    typed as a literal here.
    """
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = db.connect()
    try:
        conn.execute(f"UPDATE marketplace_product_sources SET {assignments} WHERE listing_id=?",
                     tuple(columns.values()) + (listing_id,))
        conn.commit()
    finally:
        conn.close()


def set_listing(listing_id, **columns):
    assignments = ", ".join(f"{name}=?" for name in columns)
    conn = db.connect()
    try:
        conn.execute(f"UPDATE marketplace_listings SET {assignments} WHERE id=?",
                     tuple(columns.values()) + (listing_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The stage is real
# ---------------------------------------------------------------------------

def test_an_empty_connection_counts_nothing_and_claims_no_sync_state(provider):
    """Guards every test below: if the import silently produced nothing, a
    suite asserting counts would read zeros and call them correct."""
    result = counts()

    assert result["imported"] == 0
    # Not "SYNCED". There is nothing to be synced, and a green word over an
    # empty catalogue is the smallest version of the false all-clear this whole
    # module exists to avoid.
    assert result["sync_state"] is None
    assert result["attention_products"] == 0


def test_the_fixture_really_imports_and_is_counted(provider):
    listing_id = import_one(provider)
    assert source_row(listing_id)["listing_id"] == listing_id

    assert counts()["imported"] == 1


def test_the_platform_default_publishes_an_import_immediately(provider):
    """Records what the default actually is, because it is surprising.

    ``store_policy`` ships ``auto_publish`` defaulting to True, so a product
    imported from a supplier goes live without the merchant reading it. That is
    a product decision this file does not make, but it is one every number here
    is relative to: a merchant's "published" count moving on its own is the
    policy working, not the counter lying. Pinned so that changing the default
    is a deliberate act that turns this red rather than a silent shift in what
    every dropshipping screen reports.
    """
    listing_id = import_one(provider)
    row = rows("SELECT status, approval_status FROM marketplace_listings WHERE id=?",
               (listing_id,))[0]
    assert row["status"] == "published"


def test_a_fresh_unpublished_import_is_a_draft_though_its_approval_column_says_pending(provider):
    """The bucket that is easiest to get wrong, and the order that prevents it.

    ``importer`` inserts every listing as ``status='draft'`` with
    ``approval_status='pending_review'`` in the same statement. Read the
    approval column first and a draft the merchant has not finished writing is
    reported as sitting with a moderator -- for *every* unpublished import, so
    the draft count would be zero for every merchant and the review queue would
    appear full of products nobody had submitted.

    ``lifecycle.MERCHANT_RELEASED_STATUSES`` excludes ``draft`` for exactly this
    reason, and this asserts the same precedence from the counting side. The
    row's own columns are asserted first, so this fails loudly if the importer
    stops seeding the pair rather than passing vacuously.
    """
    set_store_policy(auto_publish=False)
    listing_id = import_one(provider)
    row = rows("SELECT status, approval_status FROM marketplace_listings WHERE id=?",
               (listing_id,))[0]
    assert row["status"] == "draft"
    assert row["approval_status"] == "pending_review"

    result = counts()
    assert result["draft"] == 1
    assert result["awaiting_review"] == 0
    assert result["published"] == 0


# ---------------------------------------------------------------------------
# The count is a measurement
# ---------------------------------------------------------------------------

def test_imported_counts_every_product_not_one_page(provider):
    """The defect `list_drafts` carries a comment about, from the aggregate side.

    Three is above no page size in this codebase, so this cannot catch a limit
    of 50 on its own — what it catches is a rewrite that reaches the number by
    paging at all, because the number then tracks the page rather than the
    catalogue. The assertion is that `imported` equals the rows that exist.
    """
    for offset in range(3):
        import_one(provider, pid=str(int(PID) + offset))

    existing = rows("SELECT COUNT(*) AS n FROM marketplace_product_sources "
                    "WHERE supplier_connection_id=?", (CONNECTION,))[0]["n"]
    assert existing == 3
    assert counts()["imported"] == 3


# ---------------------------------------------------------------------------
# §15 published requires both authorities
# ---------------------------------------------------------------------------

def test_a_published_listing_awaiting_moderation_is_not_counted_as_published(provider):
    """The one error the merchant cannot detect from this screen.

    `marketplace_listing_lifecycle` gates buyer visibility on status *and*
    approval. A count that reads only `status` reports this listing as live, the
    merchant sees "1 published", and nothing sells — with no surface anywhere
    saying why. It is counted as awaiting review, which is what it is.
    """
    listing_id = import_one(provider)
    publish(listing_id)
    set_listing(listing_id, status="published", approval_status="pending_review")

    result = counts()
    assert result["published"] == 0
    assert result["awaiting_review"] == 1
    # The raw histogram still carries what the buckets were built from, so the
    # discrepancy is recoverable by a reader who suspects the bucketing.
    assert result["by_status"] == {"published/pending_review": 1}


def test_a_published_listing_with_no_approval_recorded_is_not_counted_as_published(provider):
    """The case that actually isolates the approval requirement.

    The pending-review test above looks like it proves ``published`` reads both
    columns, and it does not: ``pending_review`` is caught by the awaiting
    branch first, so that test still passes if the approval check is deleted
    from the published branch entirely. This was found by deleting it.

    An empty approval column is a listing for which no decision was ever
    recorded, which is not approval. It is not awaiting either -- nobody is
    looking at it -- so it lands in ``other``: not live, not a draft, and not
    claiming to know which.
    """
    listing_id = import_one(provider)
    set_listing(listing_id, status="published", approval_status="")

    result = counts()
    assert result["published"] == 0
    assert result["awaiting_review"] == 0
    assert result["other"] == 1


def test_a_published_and_approved_listing_is_counted_as_published(provider):
    listing_id = import_one(provider)
    publish(listing_id)
    set_listing(listing_id, status="published", approval_status="approved")

    result = counts()
    assert result["published"] == 1
    assert result["awaiting_review"] == 0
    assert result["draft"] == 0


def test_a_rejected_listing_is_blocked_not_draft_and_not_published(provider):
    listing_id = import_one(provider)
    set_listing(listing_id, status="rejected", approval_status="rejected")

    result = counts()
    assert result["blocked"] == 1
    assert result["published"] == 0
    assert result["draft"] == 0


def test_a_status_this_module_has_never_heard_of_is_not_published_or_draft(provider):
    """A status added later must not land in a bucket by accident.

    `other` is the honest home for it: not live, not a draft, and not claiming
    to know which. The histogram carries the real value so the next reader can
    see what arrived.
    """
    listing_id = import_one(provider)
    set_listing(listing_id, status="quarantined_pending_appeal", approval_status="")

    result = counts()
    assert result["other"] == 1
    assert result["published"] == 0
    assert result["draft"] == 0
    assert result["blocked"] == 0
    assert result["by_status"] == {"quarantined_pending_appeal": 1}


# ---------------------------------------------------------------------------
# §21 the sync rollup is the worst state, never the most common
# ---------------------------------------------------------------------------

def test_one_errored_product_among_many_synced_makes_the_rollup_error(provider):
    healthy = [import_one(provider, pid=str(int(PID) + n)) for n in range(3)]
    for listing_id in healthy:
        set_source(listing_id, sync_state=supplier_schema.SYNC_SYNCED)
    broken = import_one(provider, pid=str(int(PID) + 9))
    set_source(broken, sync_state=supplier_schema.SYNC_ERROR)

    result = counts()
    assert result["sync"][supplier_schema.SYNC_SYNCED] == 3
    assert result["sync"][supplier_schema.SYNC_ERROR] == 1
    # Three quarters healthy is not "healthy".
    assert result["sync_state"] == supplier_schema.SYNC_ERROR


def test_all_synced_rolls_up_to_synced(provider):
    """The rollup must be able to say yes, or the worst-wins rule above is
    indistinguishable from a function that never reports good news."""
    listing_id = import_one(provider)
    set_source(listing_id, sync_state=supplier_schema.SYNC_SYNCED)

    assert counts()["sync_state"] == supplier_schema.SYNC_SYNCED


def test_an_unrecognised_sync_state_outranks_every_known_one(provider):
    """A value this module cannot vouch for is ranked worse than every value it
    can — including ERROR, because at least ERROR is a state we defined."""
    listing_id = import_one(provider)
    set_source(listing_id, sync_state="MIGRATING_TO_V3")
    other = import_one(provider, pid=str(int(PID) + 1))
    set_source(other, sync_state=supplier_schema.SYNC_ERROR)

    result = counts()
    assert result["sync"]["UNKNOWN"] == 1
    assert result["sync_state"] == "UNKNOWN"


def test_every_schema_sync_state_has_a_rank(provider):
    """Pins the severity table against the module that defines the vocabulary.

    A seventh state added to `marketplace_supplier_schema` without a rank here
    would fall through to UNKNOWN — which fails safe, but silently, and the
    merchant would see "Unknown" for a state we had just finished naming.
    """
    for state in supplier_schema.SYNC_STATES:
        assert state in drafts._SYNC_SEVERITY, state


# ---------------------------------------------------------------------------
# §21 attention is not sync
# ---------------------------------------------------------------------------

def test_a_product_selling_below_cost_is_flagged_while_still_counting_as_synced(provider):
    """SYNCED + SELLING_BELOW_COST is a real and common state.

    The read succeeded and the answer was bad news. If this reported ERROR the
    merchant would go looking for a connection problem that does not exist; if
    it reported no attention the product would keep losing money quietly.
    """
    listing_id = import_one(provider)
    set_source(listing_id, sync_state=supplier_schema.SYNC_SYNCED,
               attention_json=f'["{revisions.SELLING_BELOW_COST}"]')

    result = counts()
    assert result["sync_state"] == supplier_schema.SYNC_SYNCED
    assert result["attention_products"] == 1
    assert result["attention"][revisions.SELLING_BELOW_COST] == 1
    assert result["cost_attention"] == 1
    assert result["stock_attention"] == 0


def test_cost_and_stock_attention_split_the_way_their_owning_module_splits_them(provider):
    """The split is read from `revisions`, not re-declared here.

    A caller showing separate "pricing" and "inventory" health needs these two
    numbers to mean what that module means, including after a seventh reason is
    added to one of the tuples.
    """
    listing_id = import_one(provider)
    set_source(listing_id, attention_json=f'["{revisions.SUPPLIER_OUT_OF_STOCK}"]')
    priced = import_one(provider, pid=str(int(PID) + 1))
    set_source(priced, attention_json=f'["{revisions.MARGIN_LOST}"]')

    result = counts()
    assert result["stock_attention"] == 1
    assert result["cost_attention"] == 1
    assert result["attention_products"] == 2
    assert sum(result["attention"][r] for r in revisions.COST_REASONS) == result["cost_attention"]
    assert sum(result["attention"][r] for r in revisions.STOCK_REASONS) == result["stock_attention"]


def test_one_product_with_two_reasons_is_one_product(provider):
    """`attention_products` counts products and `attention` counts reasons.

    A merchant reading "2 products need attention" over a catalogue of one would
    go looking for a product that does not exist.
    """
    listing_id = import_one(provider)
    set_source(listing_id,
               attention_json=f'["{revisions.MARGIN_LOST}", "{revisions.SUPPLIER_OUT_OF_STOCK}"]')

    result = counts()
    assert result["attention_products"] == 1
    assert result["attention"][revisions.MARGIN_LOST] == 1
    assert result["attention"][revisions.SUPPLIER_OUT_OF_STOCK] == 1


def test_an_empty_attention_list_is_not_a_flagged_product(provider):
    """`[]` is what a read that found nothing wrong writes, and it is the
    common case. Treating a non-NULL column as a flag would report every
    healthy product as needing attention."""
    listing_id = import_one(provider)
    set_source(listing_id, attention_json="[]")

    assert counts()["attention_products"] == 0


def test_an_unparseable_attention_blob_flags_the_product_without_inventing_a_reason(provider):
    """JSON is not a guarantee the database makes.

    The row is counted as needing a human — something is stored there and we
    cannot read it — but no specific reason is claimed, because none was read.
    """
    listing_id = import_one(provider)
    set_source(listing_id, attention_json="{not json")

    result = counts()
    assert result["attention_products"] == 1
    assert sum(result["attention"].values()) == 0


def test_every_attention_reason_is_countable(provider):
    """Pins the LIKE clauses against the reason vocabulary.

    The counts are produced by matching quoted tokens in stored JSON. A reason
    whose stored form differed from its constant would count zero forever, and
    zero is indistinguishable from "nothing is wrong" on every screen that reads
    this. So each reason is stored and then found.
    """
    for offset, reason in enumerate(revisions.ATTENTION_REASONS):
        listing_id = import_one(provider, pid=str(int(PID) + 100 + offset))
        set_source(listing_id, attention_json=f'["{reason}"]')

    result = counts()
    assert result["attention_products"] == len(revisions.ATTENTION_REASONS)
    for reason in revisions.ATTENTION_REASONS:
        assert result["attention"][reason] == 1, reason


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_another_sellers_products_on_the_same_connection_are_not_counted(provider):
    """Asserted with a real foreign row, not inferred from the WHERE clause.

    A count is a disclosure. This one is scoped by seller, and the only way to
    know the scoping works is to put a row outside it and watch the number stay
    put.
    """
    mine = import_one(provider)
    conn = db.connect()
    try:
        conn.execute("UPDATE marketplace_product_sources SET seller_user_id=? WHERE listing_id=?",
                     (int(OWNER_ID) + 1, mine))
        conn.commit()
    finally:
        conn.close()

    assert counts()["imported"] == 0
