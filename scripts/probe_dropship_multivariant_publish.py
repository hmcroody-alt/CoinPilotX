"""Can a merchant publish what the import screen imports by default?

Why this probe exists
---------------------
`drafts._validate` refuses to publish a dropship listing whose source row names
no supplier variant (`SUPPLIER_VARIANT_UNBOUND`). The comment defending that
refusal says it is fair to demand "because `importer` now binds at import when
the merchant's selection names one variant, so the ordinary path satisfies it
without the merchant doing anything."

That sentence is a claim about the *screen*, made in the backend, and never
measured from the screen. `SupplierProductScreen.defaultSelection` selects every
in-stock variant. So the question this probe answers is not "does the guard
work" -- it does -- but "can anything reachable satisfy it".

It runs the real importer, the real pricing pass and the real publish evaluator
against a fake provider. Nothing is asserted; every line is printed, because the
point is to read what the system does rather than to confirm what I expect.

    .venv/bin/python3 scripts/probe_dropship_multivariant_publish.py
"""

import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

_HANDLE, _DB = tempfile.mkstemp(prefix="probe-multivariant-", suffix=".db")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["BUSINESS_OS_SUPPLIERS_CJ"] = "1"
os.environ["CJ_ENVIRONMENT_MODE"] = "SANDBOX"

from services import db  # noqa: E402
from services import marketplace_supplier_schema as supplier_schema  # noqa: E402
from services.business_os.suppliers import (  # noqa: E402
    drafts, gateway, import_cart, importer)
from services.business_os.suppliers import schema as connection_schema  # noqa: E402
from tests.marketplace_production_listings import seed_production_listings  # noqa: E402

# The pipeline suite already owns a fake CJ provider and a seeded tenancy. Reuse
# them: a probe that builds its own fixtures is measuring its own fixtures.
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
from tests.dropshipping.test_dropship_import_pipeline import (  # noqa: E402
    BUSINESS, CONNECTION, CONTEXT, FakeProvider, OWNER_ID, STORE,
    _seed_connection, _seed_tenancy, cj_product)

os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"


def reset():
    open(_DB, "w").close()
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
        connection_schema.ensure_schema(cur)
        import_cart.ensure_schema(cur)
        _seed_tenancy(cur)
        _seed_connection(cur, CONNECTION, BUSINESS, STORE, OWNER_ID)
        conn.commit()
    finally:
        conn.close()


def rows(sql, args=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def run(label, *, variant_count, selection_mode):
    """Import one product and ask the real evaluator whether it can be published.

    ``selection_mode`` mirrors what the screen sends: "screen-default" is every
    in-stock variant, which is what `defaultSelection` produces; "one" is a
    merchant who deselected down to a single variant by hand.
    """
    reset()
    provider = FakeProvider()
    gateway_read = gateway.read
    gateway.read = provider

    pid = "PROBE-1"
    variants_ = [
        {"vid": f"{pid}-V{i}", "variantKey": f"Black-{i}", "variantSellPrice": "8.20",
         "variantQuantity": 40, "variantSku": f"{pid}-SKU-{i}", "pid": pid}
        for i in range(1, variant_count + 1)
    ]
    provider.add(cj_product(pid, variants_=variants_))

    selection = [f"{pid}-V1"] if selection_mode == "one" else [v["vid"] for v in variants_]
    import_cart.add_item(BUSINESS, STORE, OWNER_ID, CONNECTION,
                         external_product_id=pid, selected_variant_ids=selection,
                         context=CONTEXT)
    result = importer.import_selected(BUSINESS, STORE, OWNER_ID, CONNECTION, context=CONTEXT)
    entry = result["results"][0]
    listing_id = entry["listing_id"]

    # Price every variant, so that MISSING_PRICE cannot be the thing we measure.
    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)
    drafts.update_draft(
        BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
        fields={"price_cents": {str(v["variant_id"]): 2000 for v in draft["variants"]}},
        context=CONTEXT)
    draft = drafts.get_draft(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id, context=CONTEXT)

    source = rows("SELECT provider_variant_id FROM marketplace_product_sources "
                  "WHERE listing_id=?", (listing_id,))
    per_variant = rows("SELECT provider_variant_id FROM marketplace_listing_variants "
                       "WHERE listing_id=? ORDER BY position", (listing_id,))

    print(f"\n--- {label} ---")
    print(f"  selection sent                 : {selection}")
    print(f"  listing variants written       : "
          f"{[r['provider_variant_id'] for r in per_variant]}")
    print(f"  source row provider_variant_id : {source[0]['provider_variant_id']!r}")
    print(f"  publishable                    : {draft['validation']['publishable']}")
    print(f"  problems                       : {draft['validation']['problems']}")

    published = None
    try:
        published = drafts.publish(BUSINESS, STORE, OWNER_ID, CONNECTION, listing_id,
                                   context=CONTEXT)
        print(f"  publish()                      : OK {json.dumps(published)[:120]}")
    except Exception as exc:  # noqa: BLE001 - the refusal is the measurement
        print(f"  publish()                      : {type(exc).__name__}: {exc}")

    gateway.read = gateway_read
    return draft["validation"]


if __name__ == "__main__":
    print("What the import screen sends, and what the publish gate does with it.")
    one = run("one variant (merchant deselected by hand)",
              variant_count=2, selection_mode="one")
    two = run("two variants (SupplierProductScreen default)",
              variant_count=2, selection_mode="screen-default")
    four = run("four variants (a t-shirt in four sizes, screen default)",
               variant_count=4, selection_mode="screen-default")

    print("\n=== reading ===")
    print(f"  single-variant import publishable : {one['publishable']}")
    print(f"  default   import publishable      : {two['publishable']}")
    print(f"  four-size import publishable      : {four['publishable']}")
    print("\n  Whether SUPPLIER_VARIANT_UNBOUND has merchant-readable copy, and")
    print("  whether any screen can call bind-product, are grep questions -- see")
    print("  PUBLISH_PROBLEMS / PROBLEM_COPY in mobile-native.")
    os.unlink(_DB)
