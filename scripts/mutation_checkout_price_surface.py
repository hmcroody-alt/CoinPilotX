#!/usr/bin/env python3
"""Mutation battery for the checkout price surface (gap 6).

Gap 6 was a comment that enumerated, in prose, the surfaces that print nothing
rather than prose for a listing with no price. The enumeration was accurate and
incomplete: the checkout screen was a seventh surface nobody had listed, and it
filled its amount slot with `params.priceLabel || "Shown at checkout"`.

The fix replaces the prose with two scans and a rendering suite. This battery
exists because a scan is exactly the kind of guard that fails open -- a broken
comment stripper or an empty file list makes every phrase vanish and every test
pass. So each mutation below puts one invented price back, or removes one guard,
or blinds one detector, and then runs the suite that is supposed to notice.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"

CHECKOUT = NATIVE / "src/screens/MarketplaceCheckoutScreen.tsx"
CONTROLS = NATIVE / "src/screens/marketplace/CheckoutControls.tsx"
SCAN_SUITE_FILE = NATIVE / "src/screens/__tests__/MarketplacePriceLabelRendering.test.tsx"
EN_CATALOG = NATIVE / "src/i18n/catalogs/en/extended.json"

SCAN_SUITE = "src/screens/__tests__/MarketplacePriceLabelRendering.test.tsx"
RENDER_SUITE = "src/screens/__tests__/MarketplaceCheckoutAmountRendering.test.tsx"


def jest(suite: str) -> subprocess.CompletedProcess:
    return subprocess.run(["npx", "jest", "--runTestsByPath", suite, "--silent"],
                          cwd=NATIVE, capture_output=True, text=True)


# (name, file, old, new, suite, what a surviving mutant would mean)
MUTATIONS = [
    (
        "the amount slot invents a sentence again",
        CHECKOUT,
        "      : \"\",\n    [params.currency, params.subtotalMinor]",
        "      : \"Shown at checkout\",\n    [params.currency, params.subtotalMinor]",
        RENDER_SUITE,
        "the exact defect gap 6 was opened for is back: a promise naming this "
        "screen, printed under 'Item total' and again under 'Amount paid'",
    ),
    (
        "the tree scan no longer sees the phrase in any source file",
        CHECKOUT,
        "      : \"\",\n    [params.currency, params.subtotalMinor]",
        "      : \"Shown at checkout\",\n    [params.currency, params.subtotalMinor]",
        SCAN_SUITE,
        "the scan that replaced the prose enumeration cannot find an invented "
        "price even when one is shipping -- the enumeration was at least honest",
    ),
    (
        "Item total prints an empty value instead of being omitted",
        CHECKOUT,
        "{knowsFinalAmount ? <SummaryRow label=\"Item total\" value={amount} /> : null}",
        "<SummaryRow label=\"Item total\" value={amount} />",
        RENDER_SUITE,
        "a labelled row over nothing, which reads as a total of zero rather than "
        "as a total nobody knows",
    ),
    (
        "the Total row returns without its guard",
        CHECKOUT,
        "        {knowsFinalAmount ? (\n"
        "          <SummaryRow label={paymentMethod === \"cash\" ? \"Total due to seller\" : \"Total to pay\"} value={amount} strong />\n"
        "        ) : null}",
        "        <SummaryRow label={paymentMethod === \"cash\" ? \"Total due to seller\" : \"Total to pay\"} value={amount} strong />",
        RENDER_SUITE,
        "'Total due to seller' with nothing after it, in the heaviest type on "
        "the screen",
    ),
    (
        "the receipt's Amount paid row returns without its guard",
        CHECKOUT,
        "          {knowsFinalAmount ? (\n"
        "            <SummaryRow label={paymentMethod === \"cash\" ? \"Amount due to seller\" : \"Amount paid\"} value={amount} strong />\n"
        "          ) : null}",
        "          <SummaryRow label={paymentMethod === \"cash\" ? \"Amount due to seller\" : \"Amount paid\"} value={amount} strong />",
        RENDER_SUITE,
        "the confirmation view states what was paid and leaves the figure blank "
        "-- after the money moved",
    ),
    (
        "the CTA states an amount it does not have",
        CHECKOUT,
        "            ? knowsFinalAmount ? `Confirm cash order · ${amount}` : \"Confirm cash order\"",
        "            ? `Confirm cash order · ${amount}`",
        RENDER_SUITE,
        "the button the buyer commits with ends in a dangling separator and no "
        "number",
    ),
    (
        "the cash lane stops explaining a total it removed",
        CHECKOUT,
        "            {knowsFinalAmount\n"
        "              ? \"No card or Stripe charge will start. Pay the seller directly when you pick up or meet in person.\"\n"
        "              : \"No card or Stripe charge will start. The amount isn't set here — agree it with the seller when you pick up or meet in person.\"}",
        "            {\"No card or Stripe charge will start. Pay the seller directly when you pick up or meet in person.\"}",
        RENDER_SUITE,
        "on the only payment lane that is not paused, an order with no subtotal "
        "says nothing whatsoever about the amount",
    ),
    (
        "the summary card prints an empty price line",
        CONTROLS,
        "{price ? <Text testID=\"checkout-summary-price\" style={styles.summaryPrice}>{price}</Text> : null}",
        "<Text testID=\"checkout-summary-price\" style={styles.summaryPrice}>{price}</Text>",
        RENDER_SUITE,
        "an empty price element keeps its margins and its weight, so the card "
        "still has a price-shaped hole where a price would be",
    ),
    (
        "a fallback string returns to the English catalog",
        EN_CATALOG,
        "  \"commerce\": {",
        "  \"commerce\": {\n    \"priceFallbackDemo\": \"Price at checkout\",",
        SCAN_SUITE,
        "the eleven-language `priceFallback` key can come back, and ten of its "
        "translations are invisible to anyone searching for the English",
    ),
    (
        "the comment stripper eats the code with the comments",
        SCAN_SUITE_FILE,
        "  function withoutComments(source: string) {\n    return source",
        "  function withoutComments(source: string) {\n    return \"\" || source",
        SCAN_SUITE,
        "nothing -- this mutation is a no-op and must NOT be reported as caught; "
        "it is here to prove the next one is real",
    ),
    (
        "the comment stripper returns nothing at all",
        SCAN_SUITE_FILE,
        "  function withoutComments(source: string) {\n    return source",
        "  function withoutComments(source: string) {\n    return \"\" && source",
        SCAN_SUITE,
        "the scan reads an empty string for every file, finds no banned phrase "
        "anywhere, and reports success while guarding nothing",
    ),
    (
        "the scan walks no files",
        SCAN_SUITE_FILE,
        "  const files = sourceFiles(SRC);",
        "  const files: string[] = [];",
        SCAN_SUITE,
        "the phrase scan passes against an empty file list",
    ),
]

# Mutation 10 is a deliberate no-op control: `"" || source` is `source`. If the
# battery reports it caught, the battery is measuring noise -- a flaky suite, a
# stale cache, or an anchor that changed more than it looked like it did.
NO_OP_INDEX = 10


def main() -> int:
    survivors = []
    control_failed = False

    for index, (name, path, old, new, suite, meaning) in enumerate(MUTATIONS, 1):
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR    {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            result = jest(suite)
        finally:
            path.write_text(original)

        caught = result.returncode != 0
        if index == NO_OP_INDEX:
            if caught:
                print(f"{index:2}. CONTROL FAILED {name}")
                control_failed = True
            else:
                print(f"{index:2}. control  {name} (no-op, correctly survived)")
            continue

        if caught:
            print(f"{index:2}. caught   {name}")
        else:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))

    print()
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing below can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS) - 1} real mutations survived:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {len(MUTATIONS) - 1} real mutations caught; the no-op control survived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
