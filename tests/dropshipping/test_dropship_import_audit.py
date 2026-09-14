"""A product that priced and published itself must be able to say why. §36.

What this file is defending
---------------------------
"Import to Store" makes economic decisions on the merchant's behalf and then
shows a buyer the result. It picks a margin rule, measures it against a cost
basis, and publishes -- all from inputs resolved on the server, in the fraction
of a second after one tap. The listing that comes out records the *outcome*: a
price, a stock count, a status. It records none of the reasoning.

That gap is the whole section. A merchant who opens a product six weeks later and
finds it selling below cost asks who set the price, and "the importer did" is not
an answer they can act on. The answer they need is which rule applied, whether
they chose it or the platform defaulted it, and whether the margin was measured
against the goods or against the goods plus freight -- because 45% means two
different prices depending on that last one, and the listing cannot tell them
which it got.

So the tests here are not "an audit row exists". They are:

* **The row explains the price.** Rule, tier, freight figure, freight source and
  basis, all five, on the same row as the listing id. Four of the five are
  useless alone: a rule with no basis records a percentage of an unnamed
  quantity.
* **Unknown freight is recorded as unknown.** ``null``, not absent and not zero.
  The same rule §12 turns on, and it bites harder in a trail than in a
  calculation: a row that simply omits the field is ambiguous between "nobody
  declared one" and "this row predates the field", and only one of those means
  the margin was optimistic.
* **Refusals are recorded too.** The rows a merchant goes looking for are the
  ones where nothing appeared in their store. Those branches roll back, so the
  audit cannot ride the item's transaction, and the fact that it happens anyway
  is a property worth pinning.
* **Recording cannot be worse than not recording.** A success is atomic -- no
  published listing without its explanation. A failure is best-effort -- one
  unreachable product must not take a batch of twenty down with it, and the
  asymmetry is asserted in both directions rather than merely commented.
* **§27.** ``business_os_store_audit`` is read back by ``get_timeline`` behind
  ``store.read``, so every byte written here is merchant-facing. The payload is
  built from an allowlist, and the test that matters is the one proving a key the
  importer invents tomorrow does not reach the timeline on its own.

Runs alone -- see the header of ``test_dropship_import_pipeline``.
"""

import json
import os
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

_DB_HANDLE, _DB_PATH = tempfile.mkstemp(prefix="dropship-audit-", suffix=".db")
os.close(_DB_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    audit, gateway, import_cart, importer, pricing, store_policy)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OTHER_BUSINESS, OTHER_CONNECTION,
    OTHER_OWNER_ID, OTHER_STORE, OWNER_ID, STORE, _seed_connection, _seed_tenancy,
    cj_product)

# That import ran the pipeline module's header, which pointed DATABASE_URL at
# *its* temp file. db resolves the sqlite path per call, so without this every
# query below would run against a database this file never truncates.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

ITEM_COST = 820          # cj_product's "8.20", in cents
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
        _seed_connection(conn, CONNECTION, BUSINESS, STORE, OWNER_ID)
        _seed_connection(conn, OTHER_CONNECTION, OTHER_BUSINESS, OTHER_STORE,
                         OTHER_OWNER_ID)
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

def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def trail(business=BUSINESS):
    """Every import row this store has written, oldest first, payload parsed.

    Reads the table directly rather than through ``store_service.get_timeline``
    so the assertions below are about what was *stored*. ``get_timeline`` is
    covered separately, once, for the property that actually needs it: that these
    rows are merchant-readable and therefore governed by §27.
    """
    out = []
    for row in rows("SELECT * FROM business_os_store_audit WHERE business_id = ? "
                    "AND subject_type = ? ORDER BY id ASC",
                    (business, audit.SUBJECT)):
        row["after"] = json.loads(row["after_json"]) if row["after_json"] else None
        out.append(row)
    return out


def set_policy(business=BUSINESS, store=STORE, **fields):
    conn = db.connect()
    try:
        result = store_policy.set_policy(conn, business, store, **fields)
        conn.commit()
        return result
    finally:
        conn.close()


def import_one(provider, pid="PID-1", *, selection=None, expect=None,
               **product_kwargs):
    provider.add(cj_product(pid, **product_kwargs))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid,
                         selected_variant_ids=selection or [f"{pid}-V1"],
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    entry = result["results"][0]
    assert entry["outcome"] == (expect or importer.PUBLISHED), entry
    return result, entry


def cart(provider, pid, **product_kwargs):
    """Queue one product without importing it."""
    provider.add(cj_product(pid, **product_kwargs))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid,
                         selected_variant_ids=[f"{pid}-V1"], context=CONTEXT)


# ---------------------------------------------------------------------------
# 1. The row exists, and it is about the right thing
# ---------------------------------------------------------------------------

def test_a_published_import_writes_exactly_one_row(provider):
    import_one(provider)
    assert len(trail()) == 1


def test_the_action_names_the_outcome(provider):
    import_one(provider)
    assert trail()[0]["action"] == "supplier.import.published"


def test_the_row_points_at_the_listing_it_created(provider):
    _, entry = import_one(provider)
    row = trail()[0]
    # `subject_ref` is what a later reader joins on to ask "what happened to this
    # product". A row that recorded the *cart item* id instead would be
    # unjoinable the moment the cart row was cleared -- which the importer does
    # three lines later, on every success.
    assert row["subject_ref"] == str(entry["listing_id"])


def test_the_row_names_the_person_who_tapped_import(provider):
    import_one(provider)
    # Not the seller_user_id and not the business: auto-publish is an action a
    # staff member took on a store, and "who did this" is the first question of
    # any audit trail.
    assert trail()[0]["actor"] == str(OWNER_ID)


def test_each_item_in_a_batch_gets_its_own_row(provider):
    cart(provider, "PID-1")
    cart(provider, "PID-2")
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    assert result["requested"] == 2
    # Two listings, two explanations. One row per *batch* would make the trail
    # unusable for the question it exists to answer, because the interesting
    # batches are the mixed ones.
    assert len(trail()) == 2
    assert {r["after"]["external_product_id"] for r in trail()} == {"PID-1", "PID-2"}


# ---------------------------------------------------------------------------
# 2. The row explains the price
# ---------------------------------------------------------------------------

def test_the_row_records_which_rule_priced_the_product(provider):
    set_policy(pricing_rule={"type": pricing.TARGET_MARGIN, "value": 60})
    import_one(provider)
    after = trail()[0]["after"]
    assert after["pricing_rule"]["type"] == pricing.TARGET_MARGIN
    assert after["pricing_rule"]["value"] == 60


def test_the_row_records_which_tier_supplied_the_rule(provider):
    # §8's three tiers. "The store chose 60%" and "nobody chose and the platform
    # defaulted to 45%" produce different prices for different reasons, and a
    # merchant disputing a price needs to know which happened.
    set_policy(pricing_rule={"type": pricing.TARGET_MARGIN, "value": 60})
    import_one(provider)
    assert trail()[0]["after"]["pricing_source"] == store_policy.SOURCE_STORE


def test_an_undeclared_rule_is_recorded_as_the_platform_default(provider):
    import_one(provider)
    after = trail()[0]["after"]
    assert after["pricing_source"] == store_policy.SOURCE_PLATFORM
    assert after["pricing_rule"]["type"] == pricing.TARGET_MARGIN


def test_the_row_records_the_freight_the_margin_was_measured_against(provider):
    set_policy(shipping_allowance_cents=FREIGHT)
    import_one(provider)
    after = trail()[0]["after"]
    assert after["shipping_allowance_cents"] == FREIGHT
    assert after["shipping_allowance_source"] == store_policy.SOURCE_STORE
    # The basis is the half that makes the percentage meaningful. Without it the
    # row says "60% of something".
    assert after["margin_basis"] == pricing.LANDED


def test_unknown_freight_is_recorded_as_unknown_not_as_free(provider):
    import_one(provider)
    after = trail()[0]["after"]
    # Present and null. Not absent -- a missing key cannot be told apart from a
    # row written before the field existed -- and emphatically not 0, which is
    # the affirmative claim that the supplier ships for nothing.
    assert "shipping_allowance_cents" in after
    assert after["shipping_allowance_cents"] is None
    assert after["shipping_allowance_cents"] is not False  # 0 == False in Python
    assert after["margin_basis"] == pricing.ITEM


def test_declared_zero_freight_survives_into_the_trail(provider):
    # The other half of §12's rule. A merchant whose supplier bundles freight
    # into the item price said something, and the trail must not round it back
    # to "nobody said".
    set_policy(shipping_allowance_cents=0)
    import_one(provider)
    after = trail()[0]["after"]
    assert after["shipping_allowance_cents"] == 0
    assert after["shipping_allowance_source"] == store_policy.SOURCE_STORE
    assert after["margin_basis"] == pricing.LANDED


def test_the_basis_in_the_trail_matches_the_basis_in_the_response(provider):
    # One computation, reported twice. These were separate expressions until the
    # audit needed the value, and two copies of
    # `LANDED if shipping is not None else ITEM` is two things to keep in step.
    set_policy(shipping_allowance_cents=FREIGHT)
    result, _ = import_one(provider)
    assert trail()[0]["after"]["margin_basis"] == result["margin_basis"]


def test_the_row_records_the_cost_the_margin_started_from(provider):
    import_one(provider)
    after = trail()[0]["after"]
    assert after["cost_low_cents"] == ITEM_COST
    # Recorded because the supplier's price moves. Six weeks later the binding
    # holds today's cost, and only the trail holds the one the price was set
    # from.
    assert after["snapshot_id"] == "snap-PID-1"


def test_the_row_records_the_price_the_buyer_sees(provider):
    _, entry = import_one(provider)
    after = trail()[0]["after"]
    assert after["price_label"] == entry["price_label"]
    assert after["price_label"]


# ---------------------------------------------------------------------------
# 3. The row records who decided to show it
# ---------------------------------------------------------------------------

def test_the_row_records_that_the_store_asked_for_auto_publish(provider):
    import_one(provider)
    after = trail()[0]["after"]
    # The setting and the result, both. They agree here and disagree in the
    # NEEDS_ATTENTION case below, and the disagreement is the record.
    assert after["auto_publish"] is True
    assert after["published"] is True
    assert after["status"] == "PUBLISHED"


def test_a_store_that_asked_for_drafts_is_recorded_as_having_asked(provider):
    set_policy(auto_publish=False)
    import_one(provider, expect=importer.IMPORTED)
    after = trail()[0]["after"]
    assert after["auto_publish"] is False
    assert after["published"] is False
    # The distinction that matters six weeks later: nothing refused this listing.
    # The merchant asked to look at it first.
    assert trail()[0]["action"] == "supplier.import.imported"
    assert "problems" not in after


def test_a_refused_publish_records_the_setting_and_the_refusal_together(provider):
    set_policy(pricing_rule={"type": pricing.MANUAL_PRICE})
    import_one(provider, expect=importer.NEEDS_ATTENTION)
    row = trail()[0]
    assert row["action"] == "supplier.import.needs_attention"
    # Auto-publish was on and the product is not published. That pair is the
    # whole content of a needs-attention, and either half alone reads as
    # something else.
    assert row["after"]["auto_publish"] is True
    assert row["after"]["published"] is False
    # `drafts`' own codes, so the trail and the merchant's screen name the
    # refusal identically.
    assert row["after"]["problems"]


def test_a_listing_still_awaiting_marketplace_moderation_says_so(provider):
    import_one(provider)
    # §19/§20: published to the merchant's store is not placement across
    # PulseSoc, and a trail that recorded only "published" would later be read as
    # a claim the product was discoverable.
    assert trail()[0]["after"]["awaiting_moderation"] is True


# ---------------------------------------------------------------------------
# 4. Refusals are recorded too
# ---------------------------------------------------------------------------

def test_a_provider_failure_is_recorded_even_though_the_item_rolled_back(provider):
    provider.add(cj_product("PID-1"))
    provider.fail["PID-1"] = "cj_upstream_timeout"
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id="PID-1",
                         selected_variant_ids=["PID-1-V1"], context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    assert result["results"][0]["outcome"] == importer.PROVIDER_UNAVAILABLE

    row = trail()[0]
    assert row["action"] == "supplier.import.provider_unavailable"
    # No listing, so nothing to point at -- and the product is still identifiable
    # because `external_product_id` is on every row, not only the ones that
    # produced a listing.
    assert row["subject_ref"] is None
    assert row["after"]["external_product_id"] == "PID-1"
    # The provider's own refusal code, so "why did this keep failing" is
    # answerable from the trail alone.
    assert row["after"]["detail"] == "cj_upstream_timeout"


def test_a_failed_item_still_records_the_settings_that_were_in_force(provider):
    # Not obviously necessary, and it is: "every import failed that week" is
    # usually a question about what changed in the store's settings, and a
    # failure row with no settings on it cannot answer it.
    set_policy(shipping_allowance_cents=FREIGHT,
               pricing_rule={"type": pricing.TARGET_MARGIN, "value": 60})
    provider.fail["PID-1"] = "cj_upstream_timeout"
    provider.add(cj_product("PID-1"))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id="PID-1",
                         selected_variant_ids=["PID-1-V1"], context=CONTEXT)
    importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    after = trail()[0]["after"]
    assert after["shipping_allowance_cents"] == FREIGHT
    assert after["pricing_rule"]["value"] == 60


def test_re_importing_a_product_records_the_refusal_to_duplicate(provider):
    import_one(provider)
    # §16: the second tap must not create a second listing, and the merchant who
    # taps twice and sees nothing change is owed a record of why.
    cart(provider, "PID-1")
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    assert result["results"][0]["outcome"] == importer.ALREADY_EXISTS
    assert [r["action"] for r in trail()] == ["supplier.import.published",
                                              "supplier.import.already_exists"]


def test_a_failure_beside_a_success_records_both(provider):
    cart(provider, "PID-1")
    cart(provider, "PID-2")
    provider.fail["PID-2"] = "cj_upstream_timeout"
    importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    # The partial-success shape the importer is built around, reflected in the
    # trail. One row per item, whichever way each went.
    assert {r["action"] for r in trail()} == {"supplier.import.published",
                                              "supplier.import.provider_unavailable"}


# ---------------------------------------------------------------------------
# 5. Recording cannot be worse than not recording
# ---------------------------------------------------------------------------

def test_a_published_listing_never_exists_without_its_explanation(provider, monkeypatch):
    # The atomicity claim, and the reason `record_import` takes the caller's
    # connection instead of opening its own. If the explanation cannot be
    # written, the product does not go live carrying a price nothing accounts
    # for.
    def explode(*args, **kwargs):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr(importer.audit, "record_import", explode)
    provider.add(cj_product("PID-1"))
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id="PID-1",
                         selected_variant_ids=["PID-1-V1"], context=CONTEXT)

    with pytest.raises(RuntimeError):
        importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                 context=CONTEXT)
    # Rolled back with the audit row. Not "a listing with no trail".
    assert rows("SELECT id FROM marketplace_listings "
                "WHERE seller_user_id = ?", (int(OWNER_ID),)) == []


def test_a_broken_trail_does_not_turn_one_dead_item_into_a_dead_batch(provider,
                                                                     monkeypatch):
    # The other half of the asymmetry. This path runs *inside* an except block,
    # after the item is already lost, so letting it raise would discard the
    # successes beside it -- exactly the all-or-nothing shape the importer
    # exists to avoid.
    real = importer.audit.store_service._audit

    def explode_on_failures_only(conn, **kwargs):
        # Breaks the failure-path write and leaves the success-path write alone.
        # Patching `_audit` outright would break both, and the batch would die on
        # PID-1's atomic write before ever reaching the branch under test -- which
        # is how the first version of this test passed for the wrong reason.
        if kwargs.get("action", "").endswith("provider_unavailable"):
            raise RuntimeError("audit table is on fire")
        return real(conn, **kwargs)

    monkeypatch.setattr(importer.audit.store_service, "_audit",
                        explode_on_failures_only)
    cart(provider, "PID-1")
    cart(provider, "PID-2")
    provider.fail["PID-2"] = "cj_upstream_timeout"

    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION,
                                      context=CONTEXT)
    outcomes = {r["external_product_id"]: r["outcome"] for r in result["results"]}
    # The failed item reports its real outcome rather than the audit's exception.
    assert outcomes["PID-2"] == importer.PROVIDER_UNAVAILABLE
    # And its nineteen hypothetical neighbours survive.
    assert outcomes["PID-1"] == importer.PUBLISHED
    # The success row still landed: the swallow is scoped to the branch that
    # needs it, not applied to the whole path.
    assert [r["action"] for r in trail()] == ["supplier.import.published"]


def test_the_swallow_reports_that_it_swallowed(monkeypatch):
    # An exception handler nobody can see the effect of is how a permanently
    # broken audit trail looks from the outside: green tests, no rows, nobody
    # notices for a year. So the swallow returns a value.
    def explode(*args, **kwargs):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr(audit.store_service, "_audit", explode)
    audit.ensure_schema()
    assert audit.record_import_safely(
        business_id=BUSINESS, actor_user_id=OWNER_ID,
        outcome=importer.PROVIDER_UNAVAILABLE,
        facts={"external_product_id": "PID-1"}) is False


def test_a_row_that_lands_reports_that_it_landed():
    # The other half, so `is False` above is a real signal and not the only thing
    # this function can ever return.
    audit.ensure_schema()
    assert audit.record_import_safely(
        business_id=BUSINESS, actor_user_id=OWNER_ID,
        outcome=importer.PROVIDER_UNAVAILABLE,
        facts={"external_product_id": "PID-1"}) is True
    assert len(trail()) == 1


def test_the_import_establishes_the_table_it_writes_to(provider):
    # `business_os_store_audit` belongs to the Store subsystem and is created by
    # a route pack that boots inside an `except Exception` block, so "the table
    # exists" is a fact this path would otherwise inherit from something allowed
    # to fail quietly. The fixture never creates it; the import must.
    assert rows("SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='business_os_store_audit'") == []
    import_one(provider)
    assert len(trail()) == 1


# ---------------------------------------------------------------------------
# 6. §27 — everything here is merchant-facing
# ---------------------------------------------------------------------------

def test_the_payload_carries_no_credential_or_account_material(provider):
    set_policy(shipping_allowance_cents=FREIGHT)
    import_one(provider)
    blob = json.dumps(trail()[0]["after"])
    # The three things the connection row holds that a timeline reader must
    # never be handed: the vault pointer, and the CJ account and shop ids that
    # identify the merchant's supplier-side identity.
    for secret in (f"cred-{CONNECTION}", f"acct-{CONNECTION}", f"shop-{CONNECTION}"):
        assert secret not in blob


def test_a_new_importer_fact_does_not_reach_the_timeline_on_its_own(provider,
                                                                    monkeypatch):
    # The allowlist's actual job, and the only way to test it is to try to smuggle
    # something through. `_import_one`'s payload is spread into the audit facts,
    # so a future key holding a credential reference would be published into a
    # merchant-readable timeline by a change nobody thought was about audit.
    real = importer._import_one

    def leaky(*args, **kwargs):
        outcome, payload = real(*args, **kwargs)
        return outcome, {**payload, "credential_reference": "cred-LEAKED",
                         "raw_provider_response": {"token": "sk_live_xyz"}}

    monkeypatch.setattr(importer, "_import_one", leaky)
    import_one(provider)
    after = trail()[0]["after"]
    assert "credential_reference" not in after
    assert "raw_provider_response" not in after
    # And the row is still a real row, not an empty one -- a filter that dropped
    # everything would pass the two assertions above.
    assert after["listing_id"]
    assert after["pricing_rule"]


def test_only_allowlisted_keys_are_ever_written(provider):
    set_policy(shipping_allowance_cents=FREIGHT)
    import_one(provider)
    assert set(trail()[0]["after"]) <= set(audit._ALLOWED)


def test_the_trail_is_readable_by_the_merchant_it_is_about(provider):
    # Stated once, because it is the premise every §27 assertion above rests on.
    # If these rows were internal-only the allowlist would be belt-and-braces; a
    # `store.read`-gated reader makes it the actual control.
    from services.business_os.store import service as store_service
    consts = store_service.get_timeline.__code__.co_consts
    assert any(isinstance(c, str) and "business_os_store_audit" in c for c in consts)
    assert any(c == "store.read" for c in consts)


# ---------------------------------------------------------------------------
# 7. The action vocabulary stays mechanical
# ---------------------------------------------------------------------------

def test_every_outcome_has_an_action():
    # Derived, not mapped. A mapping table would be correct on the day it was
    # written and one outcome behind forever after; this asserts the derivation
    # covers the vocabulary as it stands and will cover the next addition too.
    for outcome in importer.OUTCOMES:
        assert audit.action_for(outcome) == f"supplier.import.{outcome.lower()}"


def test_actions_are_distinct_per_outcome():
    assert len({audit.action_for(o) for o in importer.OUTCOMES}) == len(importer.OUTCOMES)


@pytest.mark.parametrize("bad", [None, "", "  ", "lowercase", "has space",
                                 "Semi.Colon", 7, "DROP TABLE"])
def test_a_malformed_outcome_cannot_invent_an_action_name(bad):
    # The action lands in an indexed column operators filter on. Deriving it from
    # a value rather than a fixed mapping means a garbage outcome would otherwise
    # quietly create a category nobody knows to search for.
    assert audit.action_for(bad) == "supplier.import.unknown"


def test_every_action_stays_inside_the_import_namespace():
    for outcome in list(importer.OUTCOMES) + ["", None, "nonsense"]:
        assert audit.action_for(outcome).startswith(audit.ACTION_PREFIX)


# ---------------------------------------------------------------------------
# 8. _facts, on its own
# ---------------------------------------------------------------------------

def test_facts_keeps_an_explicit_null():
    # The §12 rule, at the level of the serializer. `{k: v for ... if v is not
    # None}` is the one-line version of this bug and it erases the difference
    # between "no freight declared" and "field did not exist".
    assert audit._facts({"shipping_allowance_cents": None}) == {
        "shipping_allowance_cents": None}


def test_facts_keeps_a_zero():
    assert audit._facts({"shipping_allowance_cents": 0}) == {
        "shipping_allowance_cents": 0}


def test_facts_keeps_a_false():
    # `published: False` is the entire content of a needs-attention row.
    assert audit._facts({"published": False}) == {"published": False}


def test_facts_omits_a_key_nobody_supplied():
    # Distinct from the two above: absent stays absent, so a row does not sprout
    # twenty-odd nulls for facts that were never in play. Equality with a
    # one-key dict is the assertion -- `_ALLOWED` has the rest.
    assert audit._facts({"listing_id": 7}) == {"listing_id": 7}


def test_facts_drops_a_key_that_is_not_on_the_allowlist():
    # The §27 control, stated as a unit fact so it does not only exist inside an
    # end-to-end test. Anything not named in `_ALLOWED` is not disclosed, whether
    # or not anyone realises it is sensitive.
    assert audit._facts({"credential_reference": "cred-x",
                         "listing_id": 7}) == {"listing_id": 7}


def test_facts_merges_in_order_with_the_last_source_winning():
    assert audit._facts({"status": "DRAFT"}, {"status": "PUBLISHED"}) == {
        "status": "PUBLISHED"}


def test_facts_ignores_a_source_that_is_not_a_dict():
    assert audit._facts(None, {"listing_id": 1}, "nope") == {"listing_id": 1}
