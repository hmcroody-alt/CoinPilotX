#!/usr/bin/env python3
"""Anti-vacuity battery for the "what does this listing actually sell" invariants.

Why this battery exists
-----------------------
The defect it guards was not a missing check. It was a claim nobody measured at
the surface where it lands::

    sellable = sum(1 for v in rows if variants.availability(v) == variants.AVAILABLE)
    "UPDATE marketplace_listings SET status='published', quantity=?, ..."
    return {..., "sellable_variants": sellable}

One integer, two meanings, one line apart. ``quantity`` is a *unit* ledger — the
cart decrements it per unit reserved and ``lifecycle.inventory_available``
answers "may this buyer take N" by comparing N against it — and publish was
seeding it with a *count of variants*. Production listing 14 is the measurement:
one variant, 132 units in CJ's warehouse, ``quantity = 1``.

Beside it, a second fact nobody measured: ``provider_variant_id`` was NULL on
every imported listing, and ``fulfillment.create_intent`` routes every line
through ``gateway.get_product_binding``, which refuses outright when it is NULL.
So every published dropship product in production was one a buyer could pay for
and nothing could ship.

Both are the same shape, and both survive any test that only asserts the code
returns what the code computes. So the invariants are:

* a dropshipped listing sells exactly the supplier variant named by
  ``provider_variant_id``, and everything — price, availability, stock — reads
  that variant rather than the set,
* the shelf's number is units, the merchant's number is variants, and they are
  no longer the same number,
* a dropship listing that names nothing cannot be published, and the ordinary
  import path satisfies that guard without the merchant doing anything extra.

Each mutation below is a plausible "simplification" of one of those — the edit a
reasonable engineer makes while tidying, which puts the defect back. A mutation
that survives is reported as a hole in the tests, not as a pass.

Usage::

    .venv/bin/python scripts/marketplace/dropship_binding_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay of
symlinks with the one mutated file materialised as a real copy — the same
mechanism as ``supplier_variant_mutation_battery``, imported from it rather than
restated so the two batteries cannot drift into different definitions of
"applied".
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supplier_variant_mutation_battery import _apply, _overlay  # noqa: E402

#: Each mutation is paired with the suite that is supposed to defend against it.
#: Pairing is the stricter claim: it says *this* suite covers *this* invariant, so
#: a mutation caught only by some unrelated file's incidental breakage is still a
#: hole.
PUBLISH_SUITE = "tests/dropshipping/test_dropship_draft_publish.py"
IMPORT_SUITE = "tests/dropshipping/test_dropship_import_pipeline.py"

DRAFTS = "services/business_os/suppliers/drafts.py"
IMPORTER = "services/business_os/suppliers/importer.py"


#: (name, file, old, new). ``old`` must appear exactly once in the file.
UNITS_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the shelf goes back to carrying a count of variants",
        DRAFTS,
        "            (units, label, cover, _iso(), _iso(), listing_id, int(seller_user_id)))",
        "            (sellable, label, cover, _iso(), _iso(), listing_id, int(seller_user_id)))",
    ),
    (
        "an unavailable variant still contributes its warehouse count",
        DRAFTS,
        "    if variant.get(\"availability\") != variants.AVAILABLE:\n        return 0",
        "    if False:\n        return 0",
    ),
    (
        "in stock with no count is sold as unlimited",
        DRAFTS,
        "    quantity = variant.get(\"stock_quantity\")\n    if quantity is None:\n        return 1",
        "    quantity = variant.get(\"stock_quantity\")\n    if quantity is None:\n        return 1_000_000",
    ),
    (
        "in stock with no count is treated as sold out",
        DRAFTS,
        "    quantity = variant.get(\"stock_quantity\")\n    if quantity is None:\n        return 1",
        "    quantity = variant.get(\"stock_quantity\")\n    if quantity is None:\n        return 0",
    ),
    (
        "the merchant's variant count is quietly replaced by the unit count",
        DRAFTS,
        "        \"sellable_variants\": sellable,",
        "        \"sellable_variants\": units,",
    ),
    (
        "publish stops reading stock_quantity at all, so every listing offers one",
        DRAFTS,
        "            \"stock_quantity\": v.get(\"stock_quantity\"),\n",
        "",
    ),
]

BINDING_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the offered set ignores the binding again",
        DRAFTS,
        "    sold = _sold_variant(priced, source)\n    if sold is not None:\n        return [sold]\n",
        "",
    ),
    (
        "an unbound listing quietly sells its first variant",
        DRAFTS,
        "    bound = str((source or {}).get(\"provider_variant_id\") or \"\").strip()\n"
        "    if not bound:\n        return None",
        "    bound = str((source or {}).get(\"provider_variant_id\") or \"\").strip()\n"
        "    if not bound:\n        return priced[0] if priced else None",
    ),
    (
        "a binding that names a variant the listing lacks reads as bound anyway",
        DRAFTS,
        "        if str(variant.get(\"provider_variant_id\") or \"\").strip() == bound:\n"
        "            return variant\n    return None",
        "        if str(variant.get(\"provider_variant_id\") or \"\").strip() == bound:\n"
        "            return variant\n    return priced[0] if priced else None",
    ),
    (
        "publication no longer requires anything to be bound",
        DRAFTS,
        "    if str(source.get(\"fulfillment_mode\") or \"\").upper() == supplier_schema.MODE_DROPSHIP \\\n"
        "            and priced and sold is None:\n"
        "        problems.append(SUPPLIER_VARIANT_UNBOUND)",
        "    if False:\n        problems.append(SUPPLIER_VARIANT_UNBOUND)",
    ),
    (
        "the binding requirement is applied to self-fulfilled inventory too",
        DRAFTS,
        "    if str(source.get(\"fulfillment_mode\") or \"\").upper() == supplier_schema.MODE_DROPSHIP \\\n"
        "            and priced and sold is None:",
        "    if priced and sold is None:",
    ),
    (
        "an available sibling answers for an unknown bound variant",
        DRAFTS,
        "    inventory_pool = [sold] if sold is not None else priced",
        "    inventory_pool = priced",
    ),
    (
        "the live reprice path forgets the binding, so a sibling can block it",
        DRAFTS,
        "    offered = _offered(priced, variants.source_for(cur, listing_id))",
        "    offered = _offered(priced)",
    ),
]

IMPORT_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "import binds nothing, as it did before",
        IMPORTER,
        "        provider_variant_id=(chosen[0].get(\"external_variant_id\")\n"
        "                             if len(chosen) == 1 else None),",
        "        provider_variant_id=None,",
    ),
    (
        "import guesses a binding when the merchant chose several variants",
        IMPORTER,
        "        provider_variant_id=(chosen[0].get(\"external_variant_id\")\n"
        "                             if len(chosen) == 1 else None),",
        "        provider_variant_id=chosen[0].get(\"external_variant_id\"),",
    ),
]


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    # These suites set their own temp sqlite DATABASE_URL in their module header.
    # An inherited one would be overwritten anyway, but dropping it keeps the two
    # batteries' environments identical and stops a stray PostgreSQL URL from
    # failing every mutation for the same irrelevant reason — which reads as a
    # clean sweep and proves nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (label, suite, mutations). Order is presentation only; each group is independent.
GROUPS: list[tuple[str, str, list[tuple]]] = [
    ("Units are not a count of variants", PUBLISH_SUITE, UNITS_MUTATIONS),
    ("A listing sells the variant it names", PUBLISH_SUITE, BINDING_MUTATIONS),
    ("Import records what the merchant chose", IMPORT_SUITE, IMPORT_MUTATIONS),
]


def main() -> int:
    suites = []
    for _, default, mutations in GROUPS:
        for suite in [default] + [m[4] for m in mutations if len(m) > 4]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="bind_mut_base_"))
    try:
        for suite in suites:
            if _run(base, suite) != 0:
                print(f"  FAIL — {suite} is already red; mutation results "
                      f"judged by it would be noise.")
                return 1
            print(f"  PASS  {suite}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()

    survived: list[str] = []
    unapplied: list[str] = []
    total = 0
    for label, default, mutations in GROUPS:
        print(f"{label} ({len(mutations)} mutations, judged by {default}):")
        for mutation in mutations:
            name, rel_path, old, new = mutation[:4]
            suite = mutation[4] if len(mutation) > 4 else default
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="bind_mut_"))
            try:
                if not _apply(root, rel_path, old, new):
                    unapplied.append(f"{label}: {name}")
                    print(f"  ERROR     {name} — anchor not found exactly once")
                    continue
                if _run(root, suite) == 0:
                    survived.append(f"{label}: {name}")
                    print(f"  SURVIVED  {name}")
                else:
                    print(f"  CAUGHT    {name}")
            finally:
                shutil.rmtree(root, ignore_errors=True)
        print()

    print("=" * 68)
    if unapplied:
        print(f"{len(unapplied)} mutation(s) could not be applied "
              f"— a bug in this script, not a pass:")
        for name in unapplied:
            print(f"  - {name}")
    if survived:
        print(f"{len(survived)} mutation(s) SURVIVED — the suite has a hole there:")
        for name in survived:
            print(f"  - {name}")
    if survived or unapplied:
        return 1
    print(f"PASS — all {total} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
