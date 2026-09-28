#!/usr/bin/env python3
"""Prove the price-label suite would notice each way a price can go wrong again.

``tests/web_parity/test_marketplace_price_label_rendering.py`` exists because a
listing with no price was rendered on the web as the words "Request access", and
because the tests that should have caught it asserted on helpers rather than on
served bytes. It has since been rewritten twice to follow the markup it reads --
once when search stopped being a JavaScript twin of the card, and once when the
product page moved to the storefront engine and stopped emitting the pill
paragraph the file had been keyed to.

That second rewrite is why this script exists. A suite whose reader has outlived
its markup does not fail loudly; it reports the *renderer* as broken while the
renderer is correct, and the obvious way to make it green again is to loosen the
assertion until the reader's answer stops mattering. The file is now green against
a correct implementation, which says nothing about whether it would still be green
against a wrong one.

So each mutation below is one of the specific shapes this bug family actually
takes, written into the renderer, with the suite expected to go red:

* the retired phrase substituted back in, on either surface;
* an empty price element rendered instead of none -- the distinction the whole
  file turns on, and the one a loosened reader loses first;
* a priced listing's price dropped, which is what every "no price" assertion
  passes for if the controls are not load-bearing;
* the two surfaces disagreeing by a stray separator;
* and the grid card dropping something that describes the *listing* along with
  the price, which is the claim that had to be re-cut when the card grew an
  add-to-cart row and is therefore the one most worth re-proving.

Deliberately not claimed: killing a mutant proves the shape is *observed*, not
that the behaviour pinned is the one the product wants. The add-to-cart row's
absence from an unpriced card is asserted as correct by
``tests/test_storefront_add_to_cart.py`` and its reason is the cart route's 400
``ITEM_UNAVAILABLE``; no mutation here can establish that, and none tries.

Usage:  python3 scripts/protection/marketplace_price_label_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

FRONT = "services/marketplace_storefront.py"
SUITE = "tests/web_parity/test_marketplace_price_label_rendering.py"

# The product page's price block, anchored on the guard rather than on the markup
# so a mutation reads as a decision about *whether* to print a price.
PAGE_GUARD = "    if price_display:"

# The grid card's, the same way. `product_card` is the only place in this module
# that branches on `price.known`.
CARD_GUARD = "    if price.known:"

# The two whole blocks, for the mutations that have to change both the guard and
# the markup inside it. `PriceView` is a frozen dataclass, so forcing a substituted
# price through the existing branch is not available as a one-line edit.
PAGE_BLOCK = (
    PAGE_GUARD + "\n"
    '        note = ""\n'
    "        if price.is_range and chosen_variant is None:\n"
    '            note = \'<span class="mkt-price-note">Price varies by option</span>\'\n'
    "        price_block = (\n"
    "            f'<div class=\"mkt-price\"><span class=\"mkt-price-value\" data-mkt-price>'\n"
    '            f"{esc(price_display)}</span>{note}</div>"\n'
    "        )"
)

CARD_BLOCK = (
    CARD_GUARD + "\n"
    '        range_class = " is-range" if price.is_range else ""\n'
    "        price_block = f'<p class=\"mkt-card-price{range_class}\">{esc(price.display)}</p>'"
)

MUTATIONS = [
    # --- the product page ---------------------------------------------------
    dict(
        name="page-substitutes-the-retired-phrase",
        control=(
            "The product page prints no price element when the seller set no "
            "price. Falling back to prose is the original defect, and 'Request "
            "access' is a phrase a seller may legitimately type -- so downstream "
            "nothing can tell an invented one from a real one."
        ),
        old=PAGE_BLOCK,
        new=PAGE_BLOCK.replace(PAGE_GUARD, "    if True:").replace(
            '{esc(price_display)}', '{esc(price_display) or "Request access"}'),
    ),
    dict(
        name="page-renders-an-empty-price-element",
        control=(
            "An absent price is an absent element. An element present and holding "
            "nothing is not 'no price' -- it is a price the seller set to nothing, "
            "and it is the shape a bare `or` fallback produces for the whitespace "
            "row. This is the distinction the suite's three-way reader exists for, "
            "and the first thing a loosened reader stops seeing."
        ),
        old=PAGE_GUARD,
        new="    if True:",
    ),
    dict(
        name="priced-page-drops-its-price",
        control=(
            "The control half. Every 'no price element' assertion in the suite "
            "passes against a page that renders no price for anybody, so the "
            "priced page must be held to showing one."
        ),
        old=PAGE_GUARD,
        new="    if False:",
    ),
    dict(
        name="page-and-grid-disagree-by-a-separator",
        control=(
            "The two surfaces must arrive at the same price text, compared "
            "unstripped. A member who browses to a listing and a member who opens "
            "its shared link are looking at one number; a separator left inside one "
            "surface's price element is how the two came to be fixed separately in "
            "the first place."
        ),
        old='f"{esc(price_display)}</span>{note}</div>"',
        new='f"{esc(price_display)} </span>{note}</div>"',
    ),
    # --- the grid card ------------------------------------------------------
    dict(
        name="card-substitutes-the-retired-phrase",
        control=(
            "The same defect on the surface it shipped on. The card builds its HTML "
            "from the row rather than through the API, so the serializer being "
            "correct does not cover it."
        ),
        old=CARD_BLOCK,
        new=CARD_BLOCK.replace(CARD_GUARD, "    if True:").replace(
            '{esc(price.display)}', '{esc(price.display) or "Request access"}'),
    ),
    dict(
        name="card-renders-an-empty-price-element",
        control=(
            "As on the page: an empty `<p class=\"mkt-card-price\">` is a price set "
            "to nothing, not the absence of one, and the card's own stylesheet "
            "comment says the reserve belongs on the title's margin rather than on "
            "an empty paragraph."
        ),
        old=CARD_GUARD,
        new="    if True:",
    ),
    dict(
        name="priced-card-drops-its-price",
        control=(
            "The grid's control, matching the page's. Without it, deleting the "
            "price element outright satisfies every unpriced-card assertion."
        ),
        old=CARD_GUARD,
        new="    if False:",
    ),
    dict(
        name="card-drops-the-seller-line-with-the-price",
        control=(
            "Dropping the price must not drop anything that describes the listing. "
            "This is the claim that had to be re-cut when the card grew an "
            "add-to-cart row -- the row is correctly withheld from an unpriced "
            "listing, so it is subtracted -- and re-cutting it is exactly how a "
            "comparison gets loosened until it no longer notices the seller's name "
            "vanishing from an unpriced card."
        ),
        old='    meta_block = f\'<div class="mkt-card-meta">{seller_block}</div>\' if seller_block else ""',
        new='    meta_block = f\'<div class="mkt-card-meta">{seller_block}</div>\' if seller_block and price.known else ""',
    ),
    dict(
        name="card-drops-its-photograph-with-the-price",
        control=(
            "The same claim one element further out, because the fingerprint the "
            "suite compares is a flat list and a mutation inside the media box tests "
            "a different part of it than one in the body. An unpriced listing is "
            "still a product with a picture."
        ),
        old="    box = media_box(first, alt=title, eager=eager, inner_html=badges_html(badges))",
        new="    box = media_box(first, alt=title, eager=eager, inner_html=badges_html(badges)) if price.known else \"\"",
    ),
]


def run_suite(verbose):
    """Red if the suite fails.

    One process, one file. This suite boots the app in a child process of its own
    and binds ``DATABASE_URL`` there, so it neither needs nor tolerates being
    batched with the rest of the marketplace tests.

    Exit 5 is "no tests ran", which is not a pass: a mutation that made the module
    unimportable would otherwise read as killed for the wrong reason, and so would
    a typo in ``SUITE``.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-q", "-p", "no:warnings"],
        cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    if verbose:
        print(f"      exit {proc.returncode} | {tail}")
    return proc.returncode == 0, tail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--only", default="", help="run one mutation by name")
    args = parser.parse_args()

    mutations = [m for m in MUTATIONS if not args.only or m["name"] == args.only]
    if args.only and not mutations:
        print(f"no mutation named {args.only!r}")
        return 2

    print("Baseline: the suite must be green before a mutation means anything.\n")
    green, tail = run_suite(args.verbose)
    if not green:
        print(f"ABORT: {SUITE} is already failing ({tail}). Fix that first -- a red\n"
              f"       baseline makes every mutation look killed.")
        return 2
    print(f"  green ({tail}).\n")

    path = ROOT / FRONT
    survivors = []
    for mutation in mutations:
        original = path.read_text()
        occurrences = original.count(mutation["old"])
        if occurrences == 0:
            print(f"  DRIFTED   {mutation['name']}")
            print(f"            anchor absent from {FRONT}. The control may have moved")
            print(f"            or been rewritten; re-point this entry before it can")
            print(f"            claim anything.")
            survivors.append(mutation["name"])
            continue
        if occurrences != 1:
            print(f"  AMBIGUOUS {mutation['name']}: anchor appears {occurrences} times.")
            survivors.append(mutation["name"])
            continue
        path.write_text(original.replace(mutation["old"], mutation["new"]))
        try:
            still_green, tail = run_suite(args.verbose)
        finally:
            path.write_text(original)
            assert path.read_text() == original, (
                f"FAILED TO RESTORE {FRONT} -- the working tree is dirty")
        if still_green:
            print(f"  SURVIVED  {mutation['name']}")
            print(f"            {mutation['control']}")
            print(f"            Writing this in changed no test result ({tail}).")
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
