"""A price that changed with nobody in the room must be able to say why. §23/§36.

What this file is defending
---------------------------
``test_dropship_import_audit.py`` covers the import half: a merchant taps once and
the server picks a price. This is the harder half. ``worker`` re-reads every
imported product on a 900-3600s cadence, and when a supplier moves their cost
``revisions`` moves ``marketplace_listings.price_label`` -- the number a stranger's
card is charged. No tap, no session, no merchant awake. The listing afterwards
holds the new price and no trace of the old one or of what chose it.

That is a worse gap than the import one. A merchant who finds an unexpected price
at import can at least remember importing. A merchant whose price changed at 3am
has nothing to reason from: the column holds one number, it has always held one
number, and the only other record is a ``supplier_snapshots`` row nobody surfaces.

So the properties here are:

* **The pair is the row.** ``$14.99 -> $21.50`` is the sentence; either half alone
  is unreadable. Both sides carry ``price_label`` and the bound variant's supplier
  cost.
* **The tier is named.** §8 gives three, and "your own policy said 45%" and
  "PulseSoc defaulted to 45%" are different answers to *why did this happen*. Both
  sources were being discarded before this; capturing them is most of the change
  in ``apply_supplier_read``.
* **Nobody gets blamed for it.** ``actor`` is the system, not the store owner.
  Filing an automatic overnight reprice under the merchant's own id would erase
  the single distinction the row exists to draw.
* **Restraint is a feature.** A read that changed nothing writes nothing. Ninety-six
  rows per listing per day is a timeline with nothing findable in it, so the row is
  earned by the buyer's price moving or by a margin needing a human -- not by a
  tick occurring.
* **The row and the price change are one transaction.** A trail that survived a
  rolled-back reprice would assert a change that never happened, which is worse
  than no trail.
* **§27.** Same table, same ``get_timeline``, same allowlist discipline as the
  import rows, and a separate vocabulary so a reprice row cannot start disclosing
  import fields by accident.

Why this file runs alone
------------------------
Same as its neighbours: ``DATABASE_URL`` is bound to its own temp file at import,
before ``services.db`` computes ``IS_POSTGRES``.

    .venv/bin/python3 -m pytest tests/dropshipping/test_dropship_reprice_audit.py
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-reprice-audit-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services import marketplace_variants as variants  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    audit, drafts, gateway, import_cart, importer, pricing, revisions, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# The applier's own end-to-end harness, reused rather than re-typed. Everything
# borrowed here resolves the database through `db.connect()` at call time, so it
# follows `DATABASE_URL` to this file's temp path; the one thing deliberately *not*
# borrowed is that module's `database` fixture, which truncates its own temp file.
from tests.dropshipping.test_dropship_revision_apply import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, OWNER_ID, PID, STORE, VID, FakeProvider,
    _seed_connection, _seed_tenancy, apply_read, bound_variant, cj_inventory,
    cj_product, counted_inventory, import_one, listing_row, publish, rows,
    set_store_policy, source_row)

# That import ran the applier suite's header, which pointed DATABASE_URL at *its*
# temp file. Restored here, or every query below would run against a database this
# file never truncates.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

ITEM_COST = 820          # cj_product's "8.20", in cents
RISEN_COST = 1640        # "16.40"
FREIGHT = 450


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def trail(subject=None):
    """Every audit row of one family, oldest first, with the JSON parsed."""
    subject = audit.REPRICE_SUBJECT if subject is None else subject
    out = []
    for row in rows("SELECT * FROM business_os_store_audit WHERE subject_type=? "
                    "ORDER BY id", (subject,)):
        row["before"] = json.loads(row["before_json"]) if row["before_json"] else None
        row["after"] = json.loads(row["after_json"]) if row["after_json"] else None
        out.append(row)
    return out


def one_row():
    entries = trail()
    assert len(entries) == 1, f"expected exactly one reprice row, got {entries}"
    return entries[0]


def live_listing(provider, **policy):
    """A published, bound, rule-priced listing -- the only thing §23 can move."""
    if policy:
        set_store_policy(**policy)
    listing_id = import_one(provider)
    publish(listing_id)
    return listing_id


def cost_rise(cost="16.40"):
    return cj_product(cost=cost, second_cost=cost)


def own_the_price(listing_id):
    """The merchant claims ``price_label``, exactly as the seller reprice route does."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        variants.mark_overridden(cur, listing_id=listing_id,
                                 seller_user_id=int(bound_variant(listing_id)["seller_user_id"]),
                                 fields=["price_label"])
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. The row exists, and it is the pair
# ---------------------------------------------------------------------------

def test_a_supplier_price_move_leaves_exactly_one_row(provider):
    listing_id = live_listing(provider)

    apply_read("product", cost_rise())

    row = one_row()
    assert row["action"] == audit.REPRICE_APPLIED
    assert str(row["subject_ref"]) == str(listing_id), \
        "the row is about the listing, which is the thing whose price moved"
    assert str(row["business_id"]) == BUSINESS


def test_the_row_says_what_the_buyer_was_charged_before_and_after(provider):
    """Either half alone is unreadable, so both are asserted against reality.

    ``after`` is compared against the *listing's* current label rather than a
    literal, because the point is that the trail agrees with the column a buyer's
    checkout reads -- a row quoting a number nobody is charged is worse than none.
    """
    listing_id = live_listing(provider)
    label_before = listing_row(listing_id)["price_label"]

    apply_read("product", cost_rise())

    row = one_row()
    label_after = listing_row(listing_id)["price_label"]
    assert row["before"]["price_label"] == label_before
    assert row["after"]["price_label"] == label_after
    assert label_after != label_before, "sanity: this test needs the price to have moved"


def test_the_row_says_what_the_supplier_charged_before_and_after(provider):
    """The cause, beside the effect.

    Without the cost pair the row says a price moved and offers no reason, which is
    the state the listing was already in.
    """
    listing_id = live_listing(provider)

    apply_read("product", cost_rise())

    row = one_row()
    assert row["before"]["supplier_cost_cents"] == ITEM_COST
    assert row["after"]["supplier_cost_cents"] == RISEN_COST
    assert source_row(listing_id)["supplier_cost_cents"] == RISEN_COST


def test_the_row_identifies_the_product_it_is_about(provider):
    """A merchant reading the timeline needs to know which product, not which row id."""
    live_listing(provider)

    apply_read("product", cost_rise())

    row = one_row()
    assert row["after"]["provider"] == "cj"
    assert str(row["after"]["external_product_id"]) == PID


# ---------------------------------------------------------------------------
# 2. Why it moved -- §8's three tiers
# ---------------------------------------------------------------------------

def test_the_row_names_the_platform_default_when_the_merchant_set_no_policy(provider):
    """"PulseSoc chose 45%" and "you chose 45%" are different answers.

    The rule alone cannot tell them apart, and it is the answer a merchant who
    never opened the pricing screen actually needs.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert after["pricing_source"] == store_policy.SOURCE_PLATFORM
    assert after["pricing_rule"] == store_policy.PLATFORM_DEFAULT_RULE


def test_the_row_names_the_store_policy_when_the_merchant_set_one(provider):
    rule = {"type": pricing.TARGET_MARGIN, "value": 60.0}
    live_listing(provider, pricing_rule=rule)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert after["pricing_source"] == store_policy.SOURCE_STORE
    assert after["pricing_rule"]["type"] == pricing.TARGET_MARGIN
    assert float(after["pricing_rule"]["value"]) == 60.0


def test_the_row_names_the_freight_the_margin_was_measured_against(provider):
    """A 45% rule means two different prices depending on the basis.

    So the rule without the basis records a percentage of an unnamed quantity.
    """
    live_listing(provider, shipping_allowance_cents=FREIGHT)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert after["shipping_allowance_cents"] == FREIGHT
    assert after["shipping_allowance_source"] == store_policy.SOURCE_STORE
    assert after["margin_basis"] == pricing.LANDED


def test_undeclared_freight_is_recorded_as_unknown_rather_than_zero(provider):
    """§12's rule, in the trail. ``null`` is a fact; absent and ``0`` are not it.

    An omitted key is ambiguous between "nobody declared freight" and "this row
    predates the field", and only one of those means the margin was optimistic.
    Zero is a *different* claim -- freight is inside the item price -- and must
    never be written for an absence.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert "shipping_allowance_cents" in after, "the explicit null must survive"
    assert after["shipping_allowance_cents"] is None
    assert after["margin_basis"] == pricing.ITEM


def test_a_declared_zero_freight_is_not_normalised_into_an_unknown(provider):
    """Zero is a merchant declaring freight is inside the item price."""
    live_listing(provider, shipping_allowance_cents=0)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert after["shipping_allowance_cents"] == 0
    assert after["margin_basis"] == pricing.LANDED, \
        "a declared zero is a declaration, so the basis is landed"


def test_the_row_reports_the_margin_the_new_price_actually_lands_on(provider):
    """A rule-held reprice can still be a bad price.

    Recording only that the rule applied would file a loss as a success, so the
    verdict is stored on its own terms.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    assert after["margin_state"] in pricing.MARGIN_STATES
    assert after["variants_written"] >= 1


# ---------------------------------------------------------------------------
# 3. Nobody was present
# ---------------------------------------------------------------------------

def test_the_actor_is_the_system_and_not_the_store_owner(provider):
    """The one distinction this row exists to draw.

    ``actor`` holds a user id everywhere else in this table, and putting the owner's
    id here would make an automatic 3am reprice indistinguishable from the merchant
    having done it themselves -- which is precisely the question they will be
    asking.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    row = one_row()
    assert row["actor"] == audit.SYSTEM_ACTOR
    assert row["actor"] != OWNER_ID


# ---------------------------------------------------------------------------
# 4. Restraint: a tick is not an event
# ---------------------------------------------------------------------------

def test_a_read_that_changed_nothing_writes_nothing(provider):
    """The property that makes the trail readable at all.

    ``worker`` re-reads every imported product every 900-3600 seconds. A row per
    read is ninety-six rows per listing per day in the same timeline a merchant
    opens to find out what happened to their store, and at that volume the rows
    that matter are unfindable.
    """
    live_listing(provider)

    out = apply_read("product", cj_product())

    assert out["listings"] == 1, "sanity: the read did reach the listing"
    assert trail() == []


def test_the_same_price_twice_writes_one_row_not_two(provider):
    """The second tick after a rise is a no-op, and no-ops are silent."""
    live_listing(provider)

    apply_read("product", cost_rise())
    apply_read("product", cost_rise())

    assert len(trail()) == 1


def test_an_inventory_read_writes_no_reprice_row(provider):
    """Stock moving is not pricing moving.

    Deliberately unaudited for now: a stock figure flickers on a cadence a price
    does not, and the rows worth keeping are buyer-visible threshold crossings
    rather than every count. Filing them under the reprice verb would make the
    price history unreadable to get there.
    """
    live_listing(provider)

    apply_read("inventory", counted_inventory(7))

    assert trail() == []


def test_a_cost_nudge_the_merchant_absorbed_harmlessly_is_not_news(provider):
    """§44 plus restraint, together.

    The merchant owns ``price_label``, so the cost rise is recorded on the variant
    and the price stands. Nothing a buyer sees moved and no margin needs a human,
    so there is nothing to tell anybody.
    """
    listing_id = live_listing(provider)
    own_the_price(listing_id)

    apply_read("product", cj_product(cost="8.60", second_cost="8.60"))

    assert bound_variant(listing_id)["cost_cents"] == 860, "the cost is still a fact"
    assert trail() == []


# ---------------------------------------------------------------------------
# 5. The half that is not a reprice
# ---------------------------------------------------------------------------

def test_a_margin_collapse_on_a_price_the_merchant_owns_is_filed_as_attention(provider):
    """The most important row in this file.

    §44 says a merchant who set their own price keeps it, which means the one thing
    PulseSoc can do about a supplier quadrupling their cost is *say so*. If that
    went unrecorded, the listing would sell below cost with no event anywhere
    naming the moment it started.
    """
    listing_id = live_listing(provider)
    own_the_price(listing_id)
    label = listing_row(listing_id)["price_label"]

    apply_read("product", cost_rise("400.00"))

    row = one_row()
    assert row["action"] == audit.REPRICE_ATTENTION
    assert revisions.SELLING_BELOW_COST in row["after"]["attention"]
    assert row["after"]["margin_state"] == pricing.NEGATIVE_MARGIN
    assert row["before"]["price_label"] == label
    assert row["after"]["price_label"] == label, \
        "an attention row still carries the price, or a reader cannot tell whether " \
        "the cost rose or the price fell"
    assert listing_row(listing_id)["price_label"] == label, "§44: the price stands"


def test_a_reprice_the_rule_could_not_hold_is_filed_with_its_reason(provider):
    """§8/§11: a rule computing past the checkout ceiling does not get written.

    The old price stands and the merchant is told we could not hold their rule --
    and that refusal is exactly the kind of thing that is invisible without a row,
    because the listing looks untouched.
    """
    listing_id = live_listing(provider)
    label = listing_row(listing_id)["price_label"]

    apply_read("product", cost_rise("9000000.00"))

    row = one_row()
    assert row["action"] == audit.REPRICE_ATTENTION
    assert revisions.REPRICE_IMPOSSIBLE in row["after"]["attention"]
    assert listing_row(listing_id)["price_label"] == label


# ---------------------------------------------------------------------------
# 6. Recording cannot be worse than not recording
# ---------------------------------------------------------------------------

def test_a_price_cannot_move_without_its_explanation_landing_with_it(provider,
                                                                    monkeypatch):
    """Atomicity, asserted by breaking the audit write.

    The reprice UPDATE and the trail row are in one transaction, so a trail that
    could not be written takes the price change down with it. That is the right way
    round: the listing keeps a price the merchant already knows about, and the
    worker's next read tries again. The alternative -- commit the price, skip the
    row -- is a silent unexplained change to what a stranger's card is charged.
    """
    listing_id = live_listing(provider)
    label = listing_row(listing_id)["price_label"]

    def explode(*args, **kwargs):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr(revisions.audit, "record_reprice", explode)

    with pytest.raises(RuntimeError):
        apply_read("product", cost_rise())

    assert listing_row(listing_id)["price_label"] == label, \
        "the buyer's price must not move without the row that explains it"
    assert bound_variant(listing_id)["cost_cents"] == ITEM_COST


def test_the_worker_establishes_the_trail_table_it_writes_to(provider):
    """``business_os_store_audit`` belongs to the store subsystem.

    Its DDL is reached through a route pack registered inside an ``except
    Exception`` block, and a worker process may never have touched it at all. The
    table is dropped here after the import to prove the *revision* path establishes
    it rather than inheriting it from the importer.
    """
    live_listing(provider)
    conn = db.connect()
    try:
        conn.execute("DROP TABLE business_os_store_audit")
        conn.commit()
    finally:
        conn.close()

    apply_read("product", cost_rise())

    assert len(trail()) == 1


# ---------------------------------------------------------------------------
# 7. §27 -- the timeline is merchant-facing
# ---------------------------------------------------------------------------

def test_the_reprice_payload_carries_no_credential_or_account_material(provider):
    """``get_timeline`` hands these bytes to a merchant behind ``store.read``.

    The fixture's connection carries recognisable markers, so a payload that ever
    started spreading the supplier connection row would say so here.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    blob = json.dumps(trail())
    for marker in (f"cred-{CONNECTION}", f"acct-{CONNECTION}", f"shop-{CONNECTION}"):
        assert marker not in blob, f"{marker} reached the merchant timeline"


def test_a_new_revision_fact_does_not_reach_the_timeline_on_its_own(provider,
                                                                   monkeypatch):
    """The allowlist, tested as the disclosure control it is.

    ``_apply_cost``'s result is free to grow, and a future key holding a raw
    provider response or a credential reference would otherwise be published into
    the timeline by a change nobody thought was about audit. The legitimate keys
    are asserted present in the same breath, so a filter that dropped everything
    could not pass this.
    """
    real = revisions._apply_cost

    def with_extra_facts(*args, **kwargs):
        result = real(*args, **kwargs)
        if result.get("audit"):
            result["audit"]["after"]["credential_reference"] = "cred-leaked"
            result["audit"]["after"]["raw_provider_response"] = {"secret": True}
            result["audit"]["before"]["credential_reference"] = "cred-leaked"
        return result

    monkeypatch.setattr(revisions, "_apply_cost", with_extra_facts)
    live_listing(provider)

    apply_read("product", cost_rise())

    row = one_row()
    for side in ("before", "after"):
        assert "credential_reference" not in row[side]
    assert "raw_provider_response" not in row["after"]
    assert row["after"]["pricing_rule"], "the legitimate facts must still be there"
    assert row["before"]["price_label"]


# ---------------------------------------------------------------------------
# 8. One timeline, two families
# ---------------------------------------------------------------------------

def test_a_reprice_is_filed_apart_from_the_import_that_created_the_listing(provider):
    """Two questions, two answers.

    An import row says why a price was *chosen*; a reprice row says why it
    *changed*. A merchant looking for the second does not want to page through a
    catalogue's worth of the first, and a single subject type would make "show me
    every price change" unanswerable.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    assert len(trail(audit.SUBJECT)) == 1, "the import row is still filed as an import"
    assert len(trail(audit.REPRICE_SUBJECT)) == 1
    assert trail(audit.SUBJECT)[0]["action"].startswith(audit.ACTION_PREFIX)
    assert trail(audit.REPRICE_SUBJECT)[0]["action"] == audit.REPRICE_APPLIED


def test_the_two_families_spell_the_pricing_facts_identically(provider):
    """So "the rule that priced it" and "the rule that repriced it" are comparable.

    Two spellings of the same five facts would give a reader two vocabularies for
    one question, and would let the import path and the revision path drift into
    disagreeing about what a rule even is.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    shared = ("pricing_rule", "pricing_source", "shipping_allowance_cents",
              "shipping_allowance_source", "margin_basis")
    imported = trail(audit.SUBJECT)[0]["after"]
    repriced = trail(audit.REPRICE_SUBJECT)[0]["after"]
    for key in shared:
        assert key in imported, f"import row lost {key}"
        assert key in repriced, f"reprice row lost {key}"
    assert imported["pricing_rule"] == repriced["pricing_rule"]
    assert imported["margin_basis"] == repriced["margin_basis"]


def test_the_reprice_vocabulary_excludes_the_import_only_facts(provider):
    """The two allowlists are two descriptions, not one union.

    ``auto_publish`` is an import-time decision and means nothing on a listing that
    is already live; carrying it here would invite a reader to think a reprice
    consulted it.
    """
    live_listing(provider)

    apply_read("product", cost_rise())

    after = one_row()["after"]
    for key in ("auto_publish", "marketplace_autolist", "published", "problems",
                "snapshot_id"):
        assert key not in after, f"{key} belongs to the import row, not this one"
