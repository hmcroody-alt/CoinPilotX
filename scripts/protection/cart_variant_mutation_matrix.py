#!/usr/bin/env python3
"""Prove each control in the variant→cart chain is load-bearing by removing it.

A green suite says the tests pass. It does not say the tests would notice if the
control they are named after were deleted. On this chain the difference is money:
the failure mode is a buyer who picked Medium being shipped a Large, or being
billed the listing's price instead of the variant's, and neither of those raises
anything. They just quietly happen.

So this script takes each control the chain depends on, edits it out of the
source, runs the suites that claim to cover it, and asserts they go red. A
mutation that survives is reported as SURVIVED and fails this script's exit code,
because a control nothing detects the absence of is a comment with a syntax
highlighter.

Two things this deliberately does not claim:

* It is not a general mutation sweep. Each entry is a specific control, mutated
  into something a person could plausibly write on a bad day -- a dropped scope
  check, a price read from the wrong row -- not a random operator flip.
* Killing a mutant proves the control is *observed*, not that it is *correct*. A
  test pinning the wrong behaviour still goes red when that behaviour is removed.

One control is deliberately absent, and its absence is the point: the
``UPDATE ... SET variant_id=0 WHERE variant_id IS NULL`` backfill in
``ensure_cart_schema`` cannot be killed, because ``ADD COLUMN variant_id INTEGER
DEFAULT 0`` already fills existing rows with ``0`` on both SQLite and PostgreSQL
(PG 11+ does not rewrite but does report the default). The backfill is a belt for
a column that arrived from some older path without a default. Listing it here
would produce a permanent SURVIVED and train a reader to ignore the output, so it
is named here instead.

Usage:  python3 scripts/protection/cart_variant_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

SCHEMA = "services/marketplace_cart_schema.py"
ROUTES = "services/marketplace_cart_routes.py"
WEB = "services/marketplace_web.py"
FRONT = "services/marketplace_storefront.py"

VARIANTS_SUITE = "tests/test_marketplace_cart_variants.py"
PANEL_SUITE = "tests/test_storefront_add_to_cart.py"

# Suites are listed narrowly on purpose. Naming every marketplace test would let
# an unrelated assertion take credit for killing a mutant, which is the same
# blind spot one layer up.
MUTATIONS = [
    # --- the schema ---------------------------------------------------------
    dict(
        name="legacy-two-column-unique-left-in-place",
        control=(
            "ensure_cart_schema retires UNIQUE(user_id, listing_id). That key is "
            "precisely the rule forbidding a second variant of one listing, so a "
            "migration that adds the column and leaves the key still refuses the "
            "buyer's second size -- while looking, column-wise, entirely fixed."
        ),
        path=SCHEMA,
        old="        elif sqlite_has_legacy_unique(_sqlite_table_sql(cur)):",
        new="        elif False:",
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="legacy-detector-also-matches-the-fixed-shape",
        control=(
            "sqlite_has_legacy_unique matches the two-column key only. Matching the "
            "three-column replacement too would rebuild the cart table on every "
            "call -- which is a table drop per request, and green the whole way."
        ),
        path=SCHEMA,
        old="        if columns == tuple(sorted(LEGACY_UNIQUE_COLUMNS)):",
        new="        if columns[:2] == tuple(sorted(LEGACY_UNIQUE_COLUMNS)):",
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="replacement-key-narrowed-back-to-two-columns",
        control=(
            "The replacement unique index spans all three columns. Two would "
            "reinstate the original bug behind a new index name."
        ),
        path=SCHEMA,
        old=f'    f"ON {{CART_TABLE}} (user_id, listing_id, variant_id)"',
        new=f'    f"ON {{CART_TABLE}} (user_id, listing_id)"',
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="sqlite-rebuild-renumbers-the-line-ids",
        control=(
            "The rebuild copies row ids explicitly. cart_update, cart_remove and "
            "the checkout cart-clear all address lines by id, so renumbering them "
            "invalidates every line id a client is holding -- the buyer's next "
            "remove 404s, or hits a line that is now somebody else's."
        ),
        path=SCHEMA,
        old='        ("id", "user_id", "listing_id", "variant_id", "qty",',
        new='        ("user_id", "listing_id", "variant_id", "qty",',
        suites=[VARIANTS_SUITE],
    ),
    # --- the add -----------------------------------------------------------
    dict(
        name="unconfigured-add-accepted",
        control=(
            "cart_add refuses an add that names no variant on a listing that needs "
            "one. This refusal is what makes opening the client-side gate safe: a "
            "stale cached page or an older app build posts a bare listing_id, and "
            "without this it books the unnamed, mis-priced line the whole gate "
            "exists to prevent."
        ),
        path=ROUTES,
        old="        if _listing_needs_variant(cur, listing_id) and not variant:",
        new="        if False:",
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="variant-lookup-not-scoped-to-the-listing",
        control=(
            "_load_variant matches on listing_id as well as id, so a variant id "
            "from another seller's product cannot attach its price to this line. "
            "Dropping the scope is price manipulation through a client field."
        ),
        path=ROUTES,
        old="    for row in _active_variants(cur, listing_id):\n"
            "        if int(row.get(\"id\") or 0) == int(variant_id):",
        new="    for row in _all_variants_unscoped(cur):\n"
            "        if int(row.get(\"id\") or 0) == int(variant_id):",
        extra=(
            "\n\ndef _all_variants_unscoped(cur):\n"
            "    cur.execute(\"SELECT * FROM marketplace_listing_variants \"\n"
            "                \"WHERE LOWER(COALESCE(status,'active'))='active'\")\n"
            "    return [dict(r) for r in cur.fetchall()]\n"
        ),
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="sold-out-variant-accepted-at-add-time",
        control=(
            "A variant whose own stock is gone is refused even when the listing "
            "reports plenty. Reading only the listing is how a buyer is shown 'In "
            "stock' and then refused at the till."
        ),
        path=ROUTES,
        old='        if variant and not _variant_available(variant):\n'
            '            return _error("That option is out of stock.", 409, code="OUT_OF_STOCK")',
        new='        if False:\n'
            '            return _error("That option is out of stock.", 409, code="OUT_OF_STOCK")',
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="line-priced-from-the-listing-not-the-variant",
        control=(
            "_line_price_minor prices a variant line from that row's price_cents. "
            "price_label is a string typed against the listing as a whole; for a "
            "shirt sold at two prices it is at best one of them. This is the "
            "mutation that bills the buyer the wrong number."
        ),
        path=ROUTES,
        old='    if variant and variant.get("price_cents") is not None:',
        new="    if False:",
        suites=[VARIANTS_SUITE],
    ),
    # --- the read ----------------------------------------------------------
    dict(
        name="retired-variant-line-reported-as-ordinary",
        control=(
            "A line naming a variant that no longer resolves reports `removed`. "
            "Without it the line reads `available` and the buyer checks out a "
            "combination that no longer exists."
        ),
        path=ROUTES,
        old='    if int(line.get("variant_id") or 0) and not variant:',
        new="    if False:",
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="variant-stock-ignored-on-a-held-line",
        control=(
            "A held line whose variant sold out reports `sold`. Checked in "
            "addition to the listing's own inventory, never instead of it."
        ),
        path=ROUTES,
        old="    # listing without any.\n"
            "    if variant and not _variant_available(variant):\n"
            '        return "sold"',
        new="    # listing without any.\n"
            "    if False:\n"
            '        return "sold"',
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="listing-stock-ignored-once-a-variant-has-some",
        control=(
            "The listing's own inventory still decides. Checkout reserves against "
            "and decrements the listing quantity, so a variant with stock must not "
            "be buyable out of a listing without any."
        ),
        path=ROUTES,
        old='    if not listing_lifecycle.inventory_available(listing, int(line.get("qty") or 1)):\n'
            '        return "sold"',
        new='    if False:\n'
            '        return "sold"',
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="variant-rebuilt-from-the-raw-row",
        control=(
            "_serialize_lines rebuilds the variant dict from the v.-prefixed "
            "aliases. The row also carries the *listing's* currency and status "
            "under those bare names, so handing the whole row to the pricing and "
            "stock helpers prices a variant line in the listing's currency and "
            "judges the variant's stock from the listing's status."
        ),
        path=ROUTES,
        old='        price_now, currency_now = (_line_price_minor(bot, listing, variant)',
        new='        price_now, currency_now = (_line_price_minor(bot, listing, row if variant else None)',
        suites=[VARIANTS_SUITE],
    ),
    # --- the order ---------------------------------------------------------
    dict(
        name="order-forgets-what-was-chosen",
        control=(
            "The chosen variant is recorded on seller_transactions. A cart that "
            "records the size and an order that does not is a seller reading "
            "'Linen Shirt x1' off a packing slip and guessing."
        ),
        path=ROUTES,
        old='                         if l.get("variant_id") else {}),',
        new="                         if False else {}),",
        suites=[VARIANTS_SUITE],
    ),
    dict(
        name="stripe-metadata-drops-the-variantless-lines",
        control=(
            "variant_ids carries a 0 sentinel per line so it stays the same length "
            "as its sibling lists and the nth entry is the nth line. Dropping the "
            "empty entries re-aligns every field after it."
        ),
        path=ROUTES,
        old='"variant_ids": ",".join(str(l.get("variant_id") or 0) for l in lines),',
        new='"variant_ids": ",".join(str(l["variant_id"]) for l in lines if l.get("variant_id")),',
        suites=[VARIANTS_SUITE],
    ),
    # --- the gate ----------------------------------------------------------
    dict(
        name="chosen-variant-never-accepted",
        control=(
            "cart_affordance accepts a resolved choice, which is what ends the trip "
            "the grid's 'Choose options' link starts. Refusing it always leaves the "
            "buyer at the end of a journey the storefront invited them on with no "
            "way to finish it -- the original defect."
        ),
        path=WEB,
        old="        chosen = _accepted_choice(chosen_variant, variants)",
        new="        chosen = None",
        suites=[PANEL_SUITE],
    ),
    dict(
        name="unresolvable-choice-accepted-anyway",
        control=(
            "_accepted_choice refuses a variant that is not in the set the decision "
            "was made against. `?opt_size=XXL` on a shirt sold in M and L resolves "
            "to nothing, and nothing is not a selection."
        ),
        path=WEB,
        old="    known = {int((v or {}).get(\"id\") or 0) for v in variants}\n"
            "    return variant_id if variant_id in known else None",
        new="    return variant_id",
        suites=[PANEL_SUITE],
    ),
    dict(
        name="sold-out-choice-still-offers-the-button",
        control=(
            "An unavailable variant is not an acceptable choice. Offering 'Add to "
            "cart' on the one combination that is sold out is worse than offering "
            "nothing, because it reads as though the page checked."
        ),
        path=WEB,
        old='    if variant_id <= 0 or not getattr(chosen, "available", False):',
        new="    if variant_id <= 0:",
        suites=[PANEL_SUITE],
    ),
    dict(
        name="button-omitted-instead-of-disabled",
        control=(
            "While the picker is incomplete the button is rendered disabled, not "
            "omitted. Omitting it leaves the scripted page with no element for the "
            "resolver to enable, so the buyer can never reach an add at all -- the "
            "shape that makes this the subtle half of the fix."
        ),
        path=FRONT,
        old="        if affordance is not None or needs_choice:",
        new="        if affordance is not None:",
        suites=[PANEL_SUITE],
    ),
    dict(
        name="posted-variant-hardcoded-to-zero",
        control=(
            "The panel emits the resolved variant id into data-mkt-variant, which "
            "the script posts. Emitting 0 regardless would send every buyer the "
            "same unnamed line while the page showed them their choice."
        ),
        path=FRONT,
        old="            variant_attr = int(affordance.variant_id) if affordance is not None else 0",
        new="            variant_attr = 0",
        suites=[PANEL_SUITE],
    ),
]


def run_suites(suites, verbose):
    """Red if any suite fails. One process per file -- these suites bind
    DATABASE_URL and import bot at module scope, so batching them produces
    failures that belong to the batching."""
    for suite in suites:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "-x", "-p", "no:warnings"],
            cwd=ROOT, capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail}")
        # Exit 5 is "no tests ran", which is not a pass. A mutation that made a
        # module unimportable would otherwise read as killed for the wrong reason
        # -- and so would a typo in this file's suite paths.
        if proc.returncode != 0:
            return False, suite, tail
    return True, None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", default="", help="run one mutation by name")
    args = parser.parse_args()

    mutations = [m for m in MUTATIONS if not args.only or m["name"] == args.only]
    if args.only and not mutations:
        print(f"no mutation named {args.only!r}")
        return 2

    print("Baseline: suites must be green before a mutation means anything.\n")
    baseline = sorted({suite for m in mutations for suite in m["suites"]})
    green, suite, tail = run_suites(baseline, args.verbose)
    if not green:
        print(f"ABORT: {suite} is already failing ({tail}). Fix that first -- a red\n"
              f"       baseline makes every mutation look killed.")
        return 2
    print(f"  {len(baseline)} suites green.\n")

    survivors = []
    for mutation in mutations:
        path = ROOT / mutation["path"]
        original = path.read_text()
        occurrences = original.count(mutation["old"])
        if occurrences == 0:
            print(f"  DRIFTED   {mutation['name']}")
            print(f"            anchor absent from {mutation['path']}. The control may")
            print(f"            have moved or been rewritten; re-point this entry before")
            print(f"            it can claim anything.")
            survivors.append(mutation["name"])
            continue
        if occurrences != 1:
            print(f"  AMBIGUOUS {mutation['name']}: anchor appears {occurrences} times.")
            survivors.append(mutation["name"])
            continue
        mutated = original.replace(mutation["old"], mutation["new"])
        if mutation.get("extra"):
            mutated += mutation["extra"]
        path.write_text(mutated)
        try:
            still_green, _, _ = run_suites(mutation["suites"], args.verbose)
        finally:
            path.write_text(original)
            assert path.read_text() == original, (
                f"FAILED TO RESTORE {mutation['path']} -- the working tree is dirty")
        if still_green:
            print(f"  SURVIVED  {mutation['name']}")
            print(f"            {mutation['control']}")
            print(f"            Removing it changed no test result. Nothing observes this.")
            survivors.append(mutation["name"])
        else:
            print(f"  killed    {mutation['name']}")

    print()
    if survivors:
        print(f"FAIL: {len(survivors)} of {len(mutations)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    print(f"PASS: all {len(mutations)} mutations killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
