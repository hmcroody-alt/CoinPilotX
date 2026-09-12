#!/usr/bin/env python3
"""Mutation battery for Buy Now quantity (gap 8).

The defect was a single fact carried by two lanes, one of which guessed. The
cart sent the buyer's quantity; Buy Now sent none, and every consumer downstream
read the absence as one -- the charge, the shelf, the inventory hold, and
`marketplace_orders.quantity`, which is the column `fulfillment.create_intent`
compares a supplier line against.

What makes it worth a battery is *why* it survived: the only test covering the
route read its source text, and one of the strings it asserted on was
`"quantity=quantity-1"` -- the hardcoded defect itself. So every mutation below
is checked against suites that run the route rather than read it, and the two
that would previously have been caught by a literal are deliberately written to
leave the source looking plausible.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv/bin/python"
NATIVE = ROOT / "mobile-native"

BOT = ROOT / "bot.py"
API = NATIVE / "src/api/marketplace.ts"
CHECKOUT_SCREEN = NATIVE / "src/screens/MarketplaceCheckoutScreen.tsx"

QUANTITY = "tests/test_marketplace_buy_now_quantity.py"
CONTRACT = "tests/test_marketplace_buy_now_checkout_contract.py"
HANDOFF = "src/screens/__tests__/MarketplaceCheckoutQuantityHandoff.test.tsx"
BODY = "src/api/__tests__/marketplaceCheckoutQuantityBody.test.ts"
LANES = "src/screens/__tests__/MarketplaceCheckoutInformationOrder.test.ts"


def run_suite(suite: str) -> subprocess.CompletedProcess:
    """Dispatch on the suite's own path; the battery spans both runtimes.

    Matched on ``src/`` rather than on the extension: two of the native suites
    here are ``.ts``, not ``.tsx``, and an extension check would have handed them
    to pytest, which collects nothing and exits 0 — reporting every native
    mutation as a survivor for a reason that has nothing to do with the code.
    """
    if suite.startswith("src/"):
        return subprocess.run(["npx", "jest", "--runTestsByPath", suite, "--silent"],
                              cwd=NATIVE, capture_output=True, text=True)
    # One file per process: importing `bot` binds DATABASE_URL process-wide.
    return subprocess.run([str(PY), "-m", "pytest", suite, "-q", "-x"],
                          cwd=ROOT, capture_output=True, text=True)


# (name, file, old, new, suite, what a surviving mutant would mean)
MUTATIONS = [
    (
        "the route prices one unit again",
        BOT,
        "        buy_quantity = max(1, min(safe_int(payload.get(\"quantity\"), 1),\n"
        "                                  marketplace_cart_service.MAX_QTY_PER_LINE))",
        "        buy_quantity = 1",
        QUANTITY,
        "the original defect, exactly: a buyer shown $75.00 is charged $25.00, "
        "and the two units they paid for stay on the shelf to be sold again",
    ),
    (
        "the shelf comes down by one no matter what was sold",
        BOT,
        '                "UPDATE marketplace_listings SET quantity=quantity-?, updated_at=? WHERE id=? AND quantity>=?",\n'
        "                (buy_quantity, now, item_id, buy_quantity),",
        '                "UPDATE marketplace_listings SET quantity=quantity-1, updated_at=? WHERE id=? AND quantity>=1",\n'
        "                (now, item_id),",
        QUANTITY,
        "stock is oversold silently: three units are paid for, one leaves the "
        "count, and the other two are still discoverable and still buyable",
    ),
    (
        "the stock check tests one unit while the order takes several",
        BOT,
        "        if not marketplace_listing_lifecycle.inventory_available(item, buy_quantity):",
        "        if not marketplace_listing_lifecycle.inventory_available(item):",
        QUANTITY,
        "a shelf of two accepts an order for three -- the guard passes on the "
        "first unit and says nothing about the rest",
    ),
    (
        "the decrement's WHERE guard drops back to one unit",
        BOT,
        "WHERE id=? AND quantity>=?\",\n                (buy_quantity, now, item_id, buy_quantity),",
        "WHERE id=? AND quantity>=1\",\n                (buy_quantity, now, item_id),",
        QUANTITY,
        "the count goes negative under a race: the pre-flight refusal is "
        "advisory, this predicate is the one that holds at commit time",
    ),
    (
        "the hold covers one unit of a multi-unit order",
        BOT,
        "                (tx_id, int(buyer[\"user_id\"]), item_id, buy_quantity, now, now),",
        "                (tx_id, int(buyer[\"user_id\"]), item_id, 1, now, now),",
        QUANTITY,
        "a declined card returns one unit to a shelf that lost three; "
        "release_inventory_reservation reads this column and nothing else",
    ),
    (
        "the order row records a quantity the buyer did not order",
        BOT,
        "    quantity, unit_price_cents = marketplace_order_line(details, amount)",
        "    quantity, unit_price_cents = 1, amount",
        QUANTITY,
        "create_intent compares a supplier line against this column, so no "
        "multi-unit dropship order can ever be dispatched",
    ),
    (
        "the unit price is derived by division again",
        BOT,
        "            return quantity, unit",
        "            return quantity, int(amount_cents or 0) // quantity",
        QUANTITY,
        "the same fact computed twice: any order carrying shipping or tax "
        "records a unit price that was never any item's price",
    ),
    (
        "an absent quantity stops meaning one",
        BOT,
        "    quantity = max(1, safe_int(details.get(\"qty\"), 1))",
        "    quantity = safe_int(details.get(\"qty\"), 0)",
        QUANTITY,
        "every single-unit order already in the ledger -- the overwhelming "
        "majority -- projects as zero units, a worse bug than the one fixed",
    ),
    (
        "a malformed quote is trusted",
        BOT,
        "        if (type(quantity) is int and quantity > 0 and type(unit) is int and unit >= 0):",
        "        if quantity and unit is not None:",
        QUANTITY,
        'a quote carrying the string "3" writes a quantity no SQL comparison '
        "against an integer column will match",
    ),
    (
        "the clamp is dropped and the cart's ceiling stops applying",
        BOT,
        "        buy_quantity = max(1, min(safe_int(payload.get(\"quantity\"), 1),\n"
        "                                  marketplace_cart_service.MAX_QTY_PER_LINE))",
        "        buy_quantity = max(1, safe_int(payload.get(\"quantity\"), 1))",
        QUANTITY,
        "the same listing has two different maximums depending on which button "
        "the buyer pressed",
    ),
    (
        "the refusal is a 200 with a smaller order",
        BOT,
        "            conn.close()\n"
        "            return api_error(\"There are not that many left.\", 409,",
        "            buy_quantity = safe_int(item.get(\"quantity\"), 1) or 1\n"
        "        if False:\n"
        "            return api_error(\"There are not that many left.\", 409,",
        QUANTITY,
        "a buyer who asked for three is silently charged for two; quietly "
        "shipping short is the outcome the refusal exists to prevent",
    ),
    (
        "the quote is told a quantity of one",
        BOT,
        "            listing_id=item_id, seller_id=seller_user_id, quantity=buy_quantity,",
        "            listing_id=item_id, seller_id=seller_user_id, quantity=1,",
        QUANTITY,
        "the charge is right only if nothing reads the quote -- but the order "
        "projection reads it first, so the ledger disagrees with the receipt",
    ),
    (
        "the transaction stops carrying qty",
        BOT,
        '        transaction_details["qty"] = buy_quantity',
        '        transaction_details["qty"] = 1',
        QUANTITY,
        "the fallback path in marketplace_order_line -- the one every legacy row "
        "still uses -- is fed a lie by the current code",
    ),
    (
        "the contract test is pinned to route source text again",
        BOT,
        '                "UPDATE marketplace_listings SET quantity=quantity-?, updated_at=? WHERE id=? AND quantity>=?",',
        '                "UPDATE marketplace_listings SET quantity = quantity - ?, updated_at=? WHERE id=? AND quantity>=?",',
        CONTRACT,
        "(inverted) this must SURVIVE: reformatting SQL is not a behaviour "
        "change, and a contract file that fails on it is back to reading source",
    ),
    (
        "the client sends no quantity",
        API,
        "      quantity: Math.max(1, Math.floor(Number(quantity) || 1)),",
        "",
        BODY,
        "the server-side fix is unreachable from the app: the phone keeps "
        "sending a body with no quantity and every Buy Now order is one unit",
    ),
    (
        "the client sends the quantity in the wrong argument slot",
        CHECKOUT_SCREEN,
        "            details,\n"
        "            // The same number this screen already prints as \"x3\" and already\n"
        "            // multiplied into the amount above. It was displayed and never sent.\n"
        "            Number(params.quantity || 1)\n"
        "          );\n"
        "          url = result.handoff.checkoutUrl;\n"
        "          ids = [...result.handoff.transactionIds];\n"
        "          bootstrap = result.handoff.sheet;",
        "            details\n"
        "          );\n"
        "          url = result.handoff.checkoutUrl;\n"
        "          ids = [...result.handoff.transactionIds];\n"
        "          bootstrap = result.handoff.sheet;",
        LANES,
        "the card lane silently drops the quantity while the cash lane keeps "
        "it, so the bug returns for every buyer who pays by card",
    ),
    (
        "the client floors a chosen quantity to one",
        API,
        "      quantity: Math.max(1, Math.floor(Number(quantity) || 1)),",
        "      quantity: 1,",
        BODY,
        "the screen displays x3 and transmits 1 -- the exact shape of the "
        "original defect, moved one layer out",
    ),
    (
        "no-op control",
        BOT,
        "    quote = details.get(\"commercial_quote\")",
        "    quote = (details or {}).get(\"commercial_quote\") if details else details.get(\"commercial_quote\")",
        QUANTITY,
        "(control)",
    ),
]

#: Entry 14 is inverted on purpose: it reformats SQL without changing behaviour,
#: and the contract file must NOT notice. That file used to assert on the route's
#: source text, which is how the hardcoded `quantity=quantity-1` stayed green for
#: as long as it did. A battery that only rewards catching cannot tell a test
#: that measures behaviour from one that pins characters.
INVERTED = {14}

#: The last entry is a deliberate no-op: the ternary's two branches are the same
#: expression for every dict. If the battery reports it caught, the battery is
#: measuring noise -- a flaky suite, a stale `__pycache__`, or an anchor that
#: changed more than it looked like it did.
NO_OP_INDEX = len(MUTATIONS)


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
            result = run_suite(suite)
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

        if index in INVERTED:
            if caught:
                print(f"{index:2}. PINNED   {name}")
                survivors.append((name, meaning))
            else:
                print(f"{index:2}. survived {name} (inverted, correctly ignored)")
            continue

        if caught:
            print(f"{index:2}. caught   {name}")
        else:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))

    real = len(MUTATIONS) - 1
    print()
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing below can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {real} mutations gave the wrong answer:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {real - len(INVERTED)} real mutations caught, "
          f"{len(INVERTED)} inverted mutation correctly ignored, "
          "no-op control survived.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
