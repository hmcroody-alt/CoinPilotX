"""A person decides what buyers see. §12-§16.

What this file is defending
---------------------------
Production measurement, not supposition: all 196 supplier-backed listings are
already ``status='published'`` and ``approval_status='approved'``, including the
153 with no variant bound. What keeps those 153 off sale is ``quantity=0`` and
``price_minor=0`` -- and both are written by ``revisions.apply_supplier_read``
from the supplier feed, on a worker cadence, with nobody awake.

So the sequence §12 forbids is not hypothetical and needs no bug to occur. Bind a
variant, wait for the next sync tick, and a product is on sale that no human ever
chose to sell. The binding is the latch. ``commerce_publication_enabled`` is the
lock, and :mod:`publication` is the only thing that turns it.

The tests are therefore not "a column can be set to 0". They are:

* **The latch test.** Hold, then bind, then let the feed fill quantity and price
  -- the exact automated sequence above, run end to end -- and assert the product
  is *still* invisible. If one test in this file has to survive, it is that one:
  every other property here is a detail of a mechanism whose entire purpose is
  that assertion.
* **A hold costs nothing.** §1 requires the listing, its provenance, pricing,
  variants, ownership and approval history preserved. ``status='paused'`` could
  not do that -- a round trip through pause NULLs ``published_at``. So the test
  is a before/after comparison of every column, not a check that the product
  disappeared.
* **Release is a gate, not a setter.** An unbound product cannot be released, and
  the refusal carries the publish gate's own problem code rather than a second
  vocabulary invented here.
* **Release is not publish.** Clearing a hold on a draft leaves a draft. The
  control is a veto, and a veto that could promote something would be a second
  publisher -- the thing ``_publish_core`` exists to prevent.
* **Idempotence is visible.** Holding twice records one decision, and says so in
  its return value rather than leaving a caller to count audit rows.
* **§27.** Publication rows land in ``business_os_store_audit``, which
  ``get_timeline`` serves to merchants, so the payload is allowlisted and a key
  invented by a future caller must not reach it.

Runs alone -- see the header of ``test_dropship_import_pipeline``.
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-publication-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    audit, drafts, gateway, import_cart, importer, publication, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from services.business_os.suppliers.errors import SupplierError  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product, set_store_policy)

# That import ran the pipeline module's header, which pointed DATABASE_URL at
# *its* temp file. See the same note in `test_dropship_import_audit`.
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
        _seed_connection(conn, CONNECTION, BUSINESS, STORE, OWNER_ID)
        _seed_connection(conn, OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE,
                         OTHER_OWNER_ID)
        conn.commit()
    finally:
        conn.close()
    import_cart.ensure_schema()
    gateway.ensure_schema()
    store_policy.ensure_schema()
    audit.ensure_schema()
    yield
    supplier_schema.reset_schema_cache()
    import_cart.reset_schema_cache()


@pytest.fixture()
def provider(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(importer.gateway, "read", fake)
    monkeypatch.setattr(gateway, "read", fake)
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def listing(listing_id):
    """The listing joined to its seller, which is the shape the predicate needs.

    ``lifecycle.is_public`` reads seller status and store name off the mapping --
    a suspended seller takes every one of their listings off sale without any
    listing row changing -- so a bare ``SELECT * FROM marketplace_listings``
    would be answering a different question than production asks.
    """
    found = rows(
        "SELECT l.*, COALESCE(ms.status,'missing') AS seller_status, "
        "COALESCE(ms.display_name,'') AS seller_store_name "
        "FROM marketplace_listings l "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
        "WHERE l.id=?", (int(listing_id),))
    assert found, f"listing {listing_id} vanished"
    return found[0]


def trail(business=BUSINESS):
    """Every publication row this store has written, oldest first."""
    out = []
    for row in rows("SELECT * FROM business_os_store_audit WHERE business_id=? "
                    "AND subject_type=? ORDER BY id ASC",
                    (business, audit.PUBLICATION_SUBJECT)):
        row["before"] = json.loads(row["before_json"]) if row["before_json"] else None
        row["after"] = json.loads(row["after_json"]) if row["after_json"] else None
        out.append(row)
    return out


def _clear_publication_decision(listing_id):
    """Put the control back to NULL: "no publication decision recorded".

    The importer now writes a hold at creation when it cannot determine a binding
    (`publication.hold_at_creation`), which is the right behaviour for every
    *future* import and the wrong starting state for the tests below. The 196
    supplier-backed rows this mission is about were imported before the column
    existed, so they carry NULL -- and NULL is the state the hold has to be able
    to move a row *out of*. Starting those tests from an already-held row would
    make `hold()` a no-op and every assertion about the writer would pass without
    the writer doing anything.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings "
                    "SET commerce_publication_enabled=NULL WHERE id=?",
                    (int(listing_id),))
        conn.commit()
    finally:
        conn.close()


def import_one(provider, pid="PID-1", *, selection=None, as_legacy=True,
               **product_kwargs):
    """Import one product and return its listing id, in production's shape.

    The default ``cj_product`` has two variants and the default selection takes
    both, which is deliberately the shape of the 152: ``importer._sole_orderable``
    finds no single orderable variant and writes NULL, so the listing arrives
    unbound. Importing the *easy* shape here would make every refusal below pass
    for the wrong reason.

    ``as_legacy`` finishes that reproduction by clearing the at-creation hold --
    see :func:`_clear_publication_decision`. Pass ``False`` to observe what the
    importer actually wrote, which is what the two tests about the at-creation
    hold do.
    """
    provider.add(cj_product(pid, **product_kwargs))
    variants = [v["vid"] for v in cj_product(pid, **product_kwargs)["variants"]]
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid,
                         selected_variant_ids=selection or variants,
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    entry = result["results"][0]
    assert entry.get("listing_id"), entry
    listing_id = int(entry["listing_id"])
    if as_legacy:
        _clear_publication_decision(listing_id)
    return listing_id


def hold(listing_id, **kwargs):
    return publication.hold(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            context=CONTEXT, **kwargs)


def release(listing_id, **kwargs):
    return publication.release(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                               context=CONTEXT, **kwargs)


def make_sellable(listing_id, *, vid="PID-1-V2"):
    """Put a listing in the state the supplier feed puts it in: bound, stocked, priced.

    ``vid=None`` leaves the binding alone, for the one case that needs the feed's
    effect *without* the bind: an unbound listing is what the at-creation hold is
    protecting, and binding it there would remove the condition under test.

    This is `revisions.apply_supplier_read`'s effect reproduced as a direct write
    rather than by running the worker, and the distinction is worth stating: the
    point is not that the worker does this, it is that *anything* doing this must
    not be able to publish. A test that drove the worker would prove the same
    property about one caller; written this way it is a property of the predicate.

    It also seeds the seller, which the import pipeline's fixture does not. Two of
    the four `PUBLICATION_RULES` ask about the *owner* rather than the listing --
    an approved status and a store name -- and in production these listings are
    reachable because their owner satisfies both. Without it every assertion below
    would be measuring `seller_approved`, and a hold would look like it was
    working while the row was already invisible for an unrelated reason.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO marketplace_sellers "
            "(user_id, display_name, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (int(OWNER_ID), "Publication Test Store", "approved",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"))
        cur.execute(
            "UPDATE marketplace_listings SET quantity=?, price_label=?, "
            "status=?, approval_status=? WHERE id=?",
            (25, "$24.00", lifecycle.PUBLISHED, lifecycle.APPROVED, int(listing_id)))
        if vid is not None:
            cur.execute(
                "UPDATE marketplace_product_sources SET provider_variant_id=? "
                "WHERE listing_id=?", (vid, int(listing_id)))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The property this module exists for
# ---------------------------------------------------------------------------

def test_binding_a_held_product_and_syncing_it_does_not_put_it_on_sale(provider):
    """§12, run as the sequence it forbids rather than asserted about.

    Hold, bind, then let the feed fill quantity and price. Every automated step
    that stood between the 152 and a buyer has now run. The product must still be
    unreachable, and the only thing making it so is the hold -- which is why the
    last two assertions check that lifting the hold *does* release it. A test that
    only proved the product was invisible could be passing because the fixture
    never made it sellable in the first place.
    """
    listing_id = import_one(provider)
    assert hold(listing_id, reason="awaiting variant decision")["changed"] is True

    make_sellable(listing_id)
    row = listing(listing_id)

    # Everything the automated pipeline controls now says "sell me".
    assert row["status"] == lifecycle.PUBLISHED
    assert row["approval_status"] == lifecycle.APPROVED
    assert int(row["quantity"]) == 25
    assert row["price_label"] == "$24.00"

    # And the buyer still cannot reach it.
    assert lifecycle.is_public(row) is False
    assert lifecycle.is_purchasable(row) is False
    assert lifecycle.publication_blocker(row) == "publication_enabled"

    # The hold is the only thing doing that. Proven by removing it: this is the
    # same row, one integer different, and now it sells.
    assert release(listing_id)["released"] is True
    freed = listing(listing_id)
    assert lifecycle.is_public(freed) is True
    assert lifecycle.is_purchasable(freed) is True


def test_the_hold_is_enforced_in_sql_and_not_only_in_python(provider):
    """The predicate production actually runs is the SQL one.

    `lifecycle.is_public` is the Python twin; every storefront, discovery and feed
    query runs `public_sql` against the database. The row-for-row agreement test
    in `test_marketplace_listing_lifecycle` pins that the two match on a synthetic
    catalogue -- this one pins it on a listing the real importer produced, because
    the two halves read the column by different mechanisms (a mapping key and a
    `COALESCE`) and a fixture is not evidence about a real row.
    """
    listing_id = import_one(provider)
    make_sellable(listing_id)

    def visible_ids():
        return {
            r["id"] for r in rows(
                "SELECT l.id FROM marketplace_listings l "
                "LEFT JOIN marketplace_sellers ms ON ms.user_id = l.seller_user_id "
                "WHERE " + lifecycle.public_sql("l", "ms"))
        }

    assert listing_id in visible_ids()
    hold(listing_id)
    assert listing_id not in visible_ids()
    release(listing_id)
    assert listing_id in visible_ids()


# ---------------------------------------------------------------------------
# Closing the latch at source, so there is no next 152
# ---------------------------------------------------------------------------

def test_an_import_that_cannot_bind_a_variant_arrives_held(provider):
    """The hold is written by the import, not waited for from an operator.

    Everything above protects rows that already exist. This protects the ones
    that do not yet, and it is a separate property because the path that created
    the 152 does not run `drafts` at all: `autopublish` correctly refused to
    publish these (unbound), which sends them to moderator review, and
    `listing_review` maps APPROVE to `(published, approved)` without consulting a
    single supplier fact. Approval is the merchant's answer to "may this be
    sold"; it was never an answer to "is this shippable".

    So the assertion is deliberately made against a row that has been *approved
    in the ordinary way* and then fed. Without the at-creation hold this is the
    152 being manufactured again, one import at a time.
    """
    listing_id = import_one(provider, as_legacy=False)
    fresh = listing(listing_id)
    assert int(fresh["commerce_publication_enabled"]) == publication.HELD
    # Held because it is unbound -- the two facts come from one reading, so this
    # pins that they cannot disagree.
    assert rows("SELECT provider_variant_id FROM marketplace_product_sources "
                "WHERE listing_id=?", (listing_id,))[0]["provider_variant_id"] is None

    # Now run the path that published the 152: approve it, then let the feed in.
    make_sellable(listing_id, vid=None)
    row = listing(listing_id)
    assert row["status"] == lifecycle.PUBLISHED
    assert row["approval_status"] == lifecycle.APPROVED
    assert lifecycle.is_purchasable(row) is False
    assert lifecycle.publication_blocker(row) == "publication_enabled"

    # No trail row: nobody decided anything. The at-creation hold is the absence
    # of a decision, and recording it as one would put an actor on the timeline
    # who never acted.
    assert trail() == []


def test_an_import_that_names_one_variant_is_never_held(provider):
    """The hold is for undetermined bindings only, never a blanket brake.

    A selection naming one orderable variant *is* the merchant choosing the
    product, and holding it back would overrule that import the same way forcing
    it to draft would overrule the store's `auto_publish` setting.

    Run twice, because two different writers are involved and asserting "not
    held" once would not say which. With `auto_publish` off the importer is the
    only writer and leaves NULL: it is allowed to withhold and never to grant.
    With it on, `drafts._publish_core` runs in the same request and records
    RELEASED -- the merchant tapped Import & publish, the gate accepted, and that
    is the decision the column is for.
    """
    set_store_policy(auto_publish=False)
    drafted = import_one(provider, pid="PID-SOLO", selection=["PID-SOLO-V1"],
                         as_legacy=False)
    row = listing(drafted)
    assert row["status"] == lifecycle.DRAFT
    assert row["commerce_publication_enabled"] is None
    assert rows("SELECT provider_variant_id FROM marketplace_product_sources "
                "WHERE listing_id=?", (drafted,))[0][
                    "provider_variant_id"] == "PID-SOLO-V1"

    set_store_policy(auto_publish=True)
    published = import_one(provider, pid="PID-DUO", selection=["PID-DUO-V1"],
                           as_legacy=False)
    row = listing(published)
    assert row["status"] == lifecycle.PUBLISHED
    assert int(row["commerce_publication_enabled"]) == publication.RELEASED

    # Neither path wrote a hold, which is the property under test. Stated
    # separately so a future default flip cannot make both halves above vacuous.
    for listing_id in (drafted, published):
        assert listing(listing_id)["commerce_publication_enabled"] != publication.HELD


def test_publishing_a_held_import_is_the_decision_that_lifts_the_hold(provider):
    """The at-creation hold must not become a trap for the merchant who answers it.

    This is the sequence §15 prescribes, run through the merchant's own Publish
    rather than through `publication.release`: an import that could not be bound
    arrives held, the merchant picks the variant, and presses Publish. If the
    hold survived that, they would be looking at a `published` row no buyer can
    see -- the same lie in the opposite direction from the one this whole control
    exists to prevent.

    `_publish_core` is allowed to write RELEASED precisely because it has just
    cleared `_validate`, so the product it is showing buyers is bound and
    fulfillable. That is what makes this different from moderator APPROVE, which
    publishes on no supplier fact at all and must never touch the column.
    """
    set_store_policy(auto_publish=False)
    listing_id = import_one(provider, as_legacy=False)
    assert int(listing(listing_id)["commerce_publication_enabled"]) == publication.HELD

    # The merchant answers the question the import could not: this variant.
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_product_sources SET provider_variant_id=? "
                    "WHERE listing_id=?", ("PID-1-V2", int(listing_id)))
        conn.commit()
    finally:
        conn.close()

    result = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                            context=CONTEXT)
    assert result.get("status") == "published", result

    row = listing(listing_id)
    assert int(row["commerce_publication_enabled"]) == publication.RELEASED
    assert lifecycle.publication_blocker(row) != "publication_enabled"


# ---------------------------------------------------------------------------
# §1: a hold must cost the listing nothing
# ---------------------------------------------------------------------------

def test_a_hold_changes_exactly_one_column(provider):
    """§1 names what must survive; this asserts it by diffing every column.

    Written as a whole-row comparison rather than a list of the fields §1
    mentions, because the fields it mentions are examples. `status='paused'` was
    rejected as the mechanism precisely because a round trip through it NULLs
    `published_at`, and the way to be sure this mechanism has no equivalent
    surprise is to let the test fail on any column at all.
    """
    listing_id = import_one(provider)
    make_sellable(listing_id)
    before = listing(listing_id)

    hold(listing_id, reason="operator review")
    after = listing(listing_id)

    assert set(before) == set(after)
    moved = {k for k in before if before[k] != after[k]}
    assert moved == {"commerce_publication_enabled"}, moved
    assert before["commerce_publication_enabled"] is None
    assert int(after["commerce_publication_enabled"]) == publication.HELD


def test_a_hold_and_release_round_trip_restores_the_row(provider):
    """Reversible in fact, not just in name -- the property `paused` lacks.

    `/resume` does not return a paused listing to `published`; it sends it back to
    review, and the moderator approval that follows NULLs `published_at`. So the
    comparison that matters is not "release undoes hold" but "the row after a
    round trip is the row before it", with only the decision itself recorded.
    """
    listing_id = import_one(provider)
    make_sellable(listing_id)
    before = listing(listing_id)

    hold(listing_id)
    release(listing_id)
    after = listing(listing_id)

    moved = {k for k in before if before[k] != after[k]}
    assert moved == {"commerce_publication_enabled"}, moved
    # Not back to NULL: "a person released this" and "nobody has decided" are
    # different facts and the column keeps them apart. Both read as not-held, so
    # the row sells either way -- which is what makes the diff above the whole
    # claim rather than a claim about visibility.
    assert int(after["commerce_publication_enabled"]) == publication.RELEASED
    assert lifecycle.is_purchasable(after) is True


# ---------------------------------------------------------------------------
# Release is a gate
# ---------------------------------------------------------------------------

def test_an_unbound_product_cannot_be_released(provider):
    """The refusal that stops §12 being reintroduced by the release path itself.

    An unbound dropship listing is the 152's exact state. Releasing one would
    make a product buyable that nobody can ship -- listing 35, which is what this
    mission started from. The refusal carries `drafts`' own problem code rather
    than a code invented here, so the merchant screen explaining it has one
    vocabulary to render.
    """
    listing_id = import_one(provider)
    # Stocked and priced, so that nothing *except* the binding is missing and the
    # refusal below cannot be attributed to anything else.
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET quantity=?, price_label=? "
                    "WHERE id=?", (25, "$24.00", listing_id))
        conn.commit()
    finally:
        conn.close()
    hold(listing_id)

    result = release(listing_id)
    assert result["released"] is False
    assert result["changed"] is False
    assert result["refusal"] == publication.NOT_READY
    assert drafts.SUPPLIER_VARIANT_UNBOUND in result["problems"]

    # And the refusal wrote nothing -- not the column, not a trail row.
    assert int(listing(listing_id)["commerce_publication_enabled"]) == publication.HELD
    assert [r["action"] for r in trail()] == [audit.PUBLICATION_HELD]


def test_a_refused_release_does_not_consume_the_hold(provider):
    """Refusals are reportable, so a bulk release can refuse 4 and grant 148.

    `release` returns rather than raises for exactly this reason, and the property
    worth pinning is that a refused product is left in the state it was in: still
    held, still releasable once the decision is made. A refusal that cleared the
    hold would publish the product it was refusing.
    """
    listing_id = import_one(provider)
    hold(listing_id)
    for _ in range(3):
        assert release(listing_id)["released"] is False
    assert int(listing(listing_id)["commerce_publication_enabled"]) == publication.HELD

    make_sellable(listing_id)
    assert release(listing_id)["released"] is True


def test_releasing_a_draft_does_not_publish_it(provider):
    """A veto lifted is not a promotion. `publication` never writes `status`.

    The listing here is a draft, so releasing the hold leaves it a draft and no
    buyer can see it. This is the boundary §16 draws: APPROVAL is "may this be
    sold", PUBLICATION CONTROL is "has a person chosen to show it", and a control
    that promoted a draft would be a second publisher competing with
    `_publish_core` -- which is the one thing `drafts` is organised to prevent.
    """
    listing_id = import_one(provider)
    make_sellable(listing_id)
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE marketplace_listings SET status=? WHERE id=?",
                    (lifecycle.DRAFT, listing_id))
        conn.commit()
    finally:
        conn.close()

    hold(listing_id)
    # The gate is about fulfilment, not about `status`, so it accepts this row --
    # which is what makes the assertion below meaningful rather than vacuous.
    assert release(listing_id)["released"] is True

    row = listing(listing_id)
    assert row["status"] == lifecycle.DRAFT
    assert lifecycle.is_public(row) is False
    assert lifecycle.publication_blocker(row) == "released"


# ---------------------------------------------------------------------------
# Idempotence, ownership, and the trail
# ---------------------------------------------------------------------------

def test_holding_twice_records_one_decision(provider):
    listing_id = import_one(provider)
    first = hold(listing_id, reason="first")
    second = hold(listing_id, reason="second")

    assert first["changed"] is True
    assert second["changed"] is False
    # One row, and it is the first call's reason. A trail with a row per retry is
    # a trail with no findable rows -- the same argument `record_reprice` makes
    # for not auditing every sync tick.
    assert [r["action"] for r in trail()] == [audit.PUBLICATION_HELD]
    assert trail()[0]["after"]["reason"] == "first"


def test_another_merchant_cannot_hold_your_product(provider):
    """Tenant isolation, and an absent id refuses identically to a foreign one.

    Both 404, copying `drafts._owned_listing`: distinguishing them would turn this
    into an oracle for which listing ids exist.
    """
    listing_id = import_one(provider)
    for target in (listing_id, 10 ** 9):
        with pytest.raises(SupplierError) as caught:
            publication.hold(OTHER_BUSINESS, OTHER_STORE, OTHER_OWNER_ID,
                             OTHER_CONNECTION, target, context=CONTEXT)
        assert caught.value.http_status == 404
    assert listing(listing_id)["commerce_publication_enabled"] is None


def test_a_manual_listing_is_not_this_modules_business(provider):
    """No supplier source row, no hold.

    Refused rather than quietly allowed, because a hold here would be this module
    taking authority over the hand-authored catalogue, whose publication story is
    not the one it documents. The seeded production listings are exactly that
    shape -- real rows, no `marketplace_product_sources`.
    """
    conn = db.connect()
    try:
        cur = conn.cursor()
        seed_production_listings(cur, owner=OWNER_ID)
        conn.commit()
    finally:
        conn.close()
    manual = rows("SELECT l.id FROM marketplace_listings l "
                  "LEFT JOIN marketplace_product_sources s ON s.listing_id = l.id "
                  "WHERE s.listing_id IS NULL ORDER BY l.id LIMIT 1")
    assert manual, "the fixture produced no manual listing"

    with pytest.raises(SupplierError) as caught:
        hold(manual[0]["id"])
    assert caught.value.http_status == 404


def test_the_trail_records_the_transition_and_who_made_it(provider):
    listing_id = import_one(provider)
    make_sellable(listing_id)
    hold(listing_id, reason="pulled for review")
    release(listing_id, reason="variant confirmed")

    entries = trail()
    assert [r["action"] for r in entries] == [audit.PUBLICATION_HELD,
                                              audit.PUBLICATION_RELEASED]
    held, freed = entries

    # The pair is the row. Either half alone cannot distinguish a first-ever hold
    # from one reversing a previous release.
    assert held["before"]["commerce_publication_enabled"] is None
    assert held["after"]["commerce_publication_enabled"] == publication.HELD
    assert freed["before"]["commerce_publication_enabled"] == publication.HELD
    assert freed["after"]["commerce_publication_enabled"] == publication.RELEASED

    assert held["after"]["reason"] == "pulled for review"
    assert freed["after"]["reason"] == "variant confirmed"
    # A real actor on both. There is no system actor for this family: a
    # publication decision nobody made is the thing §12 forbids.
    assert str(held["actor"]) == str(OWNER_ID)
    assert str(freed["actor"]) == str(OWNER_ID)
    # The gate's verdict at the moment of release, so a product that breaks later
    # does not make the release look negligent in hindsight.
    assert freed["after"]["publishable"] is True
    assert freed["after"]["problems"] == []
    # Provenance, so a publication row is readable without joining back to a
    # listing that has since moved on.
    assert freed["after"]["provider_variant_id"] == "PID-1-V2"


def test_an_unlisted_fact_cannot_reach_the_merchant_timeline(provider, monkeypatch):
    """§27. The payload is an allowlist, and this is the test that proves it.

    `business_os_store_audit` is served to merchants by `get_timeline`, so a key a
    future caller starts attaching -- a vault pointer, a raw provider response --
    would otherwise be published into their timeline by a change nobody thought
    was about audit. Asserted by attaching one and finding it absent, rather than
    by reading the allowlist back, which would only prove the list equals itself.
    """
    listing_id = import_one(provider)

    original = publication._listing_facts

    def leaky(row):
        payload = original(row)
        payload["cj_access_token"] = "secret-should-never-be-disclosed"
        return payload

    monkeypatch.setattr(publication, "_listing_facts", leaky)
    hold(listing_id)

    entry = trail()[0]
    assert "cj_access_token" not in (entry["after"] or {})
    assert "cj_access_token" not in (entry["before"] or {})
    assert "secret-should-never-be-disclosed" not in json.dumps(entry["after"])
    # And the row still carries what it is for, so the filter is not simply
    # dropping everything.
    assert entry["after"]["commerce_publication_enabled"] == publication.HELD
