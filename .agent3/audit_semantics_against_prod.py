"""Read-only: run services.catalog_semantics over real production rows.

A model that only ever sees fixtures is a model that agrees with its author. This
drives it over the whole supplier-backed catalogue and reports the distribution of
semantic states, then prints the briefing's named edge cases in full: the widest
variant matrix, the weakest title, the held row, the out-of-stock row, and the one
product whose option split produced three axes.

It also asserts the two properties that cannot be checked from a fixture, because
they are statements about the whole catalogue rather than about one row:
no SKU anywhere classifies as a GTIN, and no supplier product key collides.
"""
import collections
import json
import os
import sys

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.catalog_semantics import (  # noqa: E402
    UNKNOWN,
    IdentifierClass,
    product_identity,
    public_projection,
    semantic_state,
)

conn = psycopg2.connect(os.environ["DATABASE_PUBLIC_URL"])
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()


def rows(sql, params=()):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


listings = {r["id"]: r for r in rows("SELECT * FROM marketplace_listings")}
sources = {}
for r in rows("SELECT * FROM marketplace_product_sources"):
    sources[r["listing_id"]] = r
variants = collections.defaultdict(list)
for r in rows("SELECT * FROM marketplace_listing_variants"):
    variants[r["listing_id"]].append(r)

# Per-variant images, recovered from the immutable snapshot by matching the
# supplier's own option string against the stored options_json. This is the
# RECOVERABLE_FROM_SNAPSHOT path, exercised against real payloads.
snap_images = collections.defaultdict(dict)
cur.execute(
    """SELECT DISTINCT ON (s.resource_id) s.resource_id, s.payload_json
       FROM supplier_snapshots s WHERE s.kind='product'
       ORDER BY s.resource_id, s.created_at DESC"""
)
by_resource = {}
for rid, pj in cur.fetchall():
    try:
        by_resource[str(rid)] = json.loads(pj or "{}")
    except ValueError:
        continue

for lid, src in sources.items():
    payload = by_resource.get(str(src.get("provider_product_id")))
    if not payload:
        continue
    supplier_by_options = {}
    for v in payload.get("variants") or []:
        opts = str(v.get("options") or "").strip()
        if opts and v.get("image"):
            supplier_by_options[opts] = v["image"]
    for v in variants.get(lid, ()):
        try:
            parsed = json.loads(v.get("options_json") or "[]")
        except ValueError:
            continue
        joined = "-".join(str(p.get("value") or "") for p in parsed if isinstance(p, dict))
        if joined in supplier_by_options:
            snap_images[lid][str(v.get("variant_key") or "")] = supplier_by_options[joined]

identities = {}
for lid, listing in listings.items():
    identities[lid] = product_identity(
        listing,
        variants.get(lid, []),
        sources.get(lid),
        variant_images=snap_images.get(lid),
    )

print("=== SEMANTIC STATE over the whole catalogue (%d listings) ===" % len(identities))
states = collections.Counter(semantic_state(i) for i in identities.values())
for state, n in states.most_common():
    print("  %-24s %d" % (state, n))

print("\n=== CATALOGUE-WIDE INVARIANTS ===")
classes = collections.Counter()
gtins = []
for i in identities.values():
    for v in i.variants:
        classes[v.sku_class] += 1
        if v.sku_class == IdentifierClass.GTIN:
            gtins.append((i.listing_id, v.sku.value))
print("  sku_class distribution:", dict(classes))
print("  variants claiming a GTIN:", len(gtins), gtins[:5])
assert not gtins, "a production SKU was classified as a GTIN -- re-check the check digit"

keys = collections.Counter(
    i.supplier_product_key.value for i in identities.values() if i.supplier_product_key.known
)
collisions = {k: n for k, n in keys.items() if n > 1}
print("  distinct supplier product keys:", len(keys), "collisions:", collisions or "none")
assert not collisions, "supplier product key collision"

groups = collections.Counter(i.group_id for i in identities.values())
print("  distinct group_ids:", len(groups), "reused:", {k: n for k, n in groups.items() if n > 1} or "none")

stable = [v.stable_id for i in identities.values() for v in i.variants]
print("  variant stable_ids:", len(stable), "distinct:", len(set(stable)))
assert len(stable) == len(set(stable)), "variant stable_id is not unique"

brands = {i.brand.value for i in identities.values()}
print("  distinct brand values across the catalogue:", brands)
assert brands == {UNKNOWN}, "a brand value appeared"

named_axes = [
    (i.listing_id, a.name.value)
    for i in identities.values()
    for v in i.variants
    for a in v.axes
    if a.name.known
]
print("  axes with a semantic name:", len(named_axes), named_axes[:5])

recovered = sum(1 for i in identities.values() for v in i.variants if v.image.known)
total_variants = sum(len(i.variants) for i in identities.values())
print("  variant images recovered from snapshots: %d/%d" % (recovered, total_variants))

print("\n=== PRIVACY: does any projection carry a cost? ===")
leaks = []
for i in identities.values():
    blob = public_projection(i)
    for v, row in zip(i.variants, blob["variants"]):
        for key in row:
            if "cost" in key or "margin" in key:
                leaks.append((i.listing_id, key))
print("  cost/margin-shaped keys in projections:", leaks or "none")
assert not leaks

print("\n=== NAMED EDGE CASES ===")


def show(label, lid):
    if lid not in identities:
        print("\n  -- %s: listing %s not present" % (label, lid))
        return
    i = identities[lid]
    listing = listings[lid]
    print("\n  -- %s (listing %s) --" % (label, lid))
    print("     status=%r approval=%r quantity=%r price_label=%r" % (
        listing.get("status"), listing.get("approval_status"),
        listing.get("quantity"), listing.get("price_label")))
    print("     semantic_state:", semantic_state(i))
    print("     group_id:", i.group_id)
    blob = public_projection(i)
    blob["variants"] = blob["variants"][:3]
    print("     projection:", json.dumps(blob, indent=6, default=str)[:1800])


widest = max(identities.values(), key=lambda i: len(i.variants))
show("WIDEST VARIANT MATRIX (%d variants)" % len(widest.variants), widest.listing_id)

three_axis = next(
    (i for i in identities.values() if any(len(v.axes) >= 3 for v in i.variants)), None
)
if three_axis:
    show("THREE-AXIS OPTION SPLIT", three_axis.listing_id)

weakest = min(
    (i for i in identities.values() if i.title.known),
    key=lambda i: len(str(i.title.value)),
)
show("SHORTEST TITLE (%d chars)" % len(str(weakest.title.value)), weakest.listing_id)

held = next(
    (
        i for i in identities.values()
        if str(listings[i.listing_id].get("status") or "").lower() == "published"
        and str(listings[i.listing_id].get("approval_status") or "").lower() == "approved"
        and not (listings[i.listing_id].get("quantity") or 0) > 0
    ),
    None,
)
if held:
    show("HELD OUT OF SEARCH BY QUANTITY ALONE", held.listing_id)

buyable = next(
    (i for i in identities.values() if (listings[i.listing_id].get("quantity") or 0) > 0), None
)
if buyable:
    show("BUYABLE", buyable.listing_id)

unbound = next((i for i in identities.values() if not i.supplier_product_key.known), None)
if unbound:
    show("NOT SUPPLIER-BOUND", unbound.listing_id)

no_variants = next((i for i in identities.values() if not i.variants), None)
if no_variants:
    show("NO VARIANTS", no_variants.listing_id)

conn.close()
print("\nall catalogue-wide invariants held.")
