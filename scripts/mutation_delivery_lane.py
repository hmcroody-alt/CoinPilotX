#!/usr/bin/env python3
"""Mutation battery for the delivery lane (gaps 9 and 10).

One fact -- "how is this listing fulfilled" -- was derived six times, in two
languages, from a column that has never contained the answer.
``marketplace_listings.delivery_type`` is ``TEXT DEFAULT 'digital'`` and every
writer stores the *product type* in it: the publish route's INSERT lists
``delivery_type, product_type`` against ``product_type, product_type``, and the
CJ importer hardcodes ``'physical','physical'``. The seller's actual choice
lives in ``listing_metadata.delivery_options``.

What makes this worth a battery rather than a diff is why it survived for so
long. Every test of the rule built its own listing dict and put a lane word --
``pickup``, ``shipping``, ``both`` -- into ``delivery_type``. No row in the
table has ever looked like that. The suites were green on inputs production
cannot produce, so the mutations below are checked against suites that publish
through the real route and read the row back (``test_marketplace_delivery_lane``)
or that assert the surfaces against *each other* rather than each against its own
expectation (``marketplaceDeliveryLaneAgreement``).

Two mutations are therefore aimed at the fixtures rather than at the logic: one
reintroduces a divergence between the client's word list and the server's, which
only a test that can read both files catches, and one is inverted -- a rename
that changes the source text without changing behaviour, which the source-reading
parity test must NOT notice.

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

RULE = ROOT / "services/marketplace_fulfillment.py"
CART = ROOT / "services/marketplace_cart_routes.py"
TS_RULE = NATIVE / "src/api/marketplaceFulfillment.ts"
TS_SCREEN = NATIVE / "src/api/marketplaceScreen.ts"
TS_BUYER = NATIVE / "src/api/marketplaceBuyerPresentation.ts"

LANE = "tests/test_marketplace_delivery_lane.py"
RULE_PY = "tests/test_marketplace_fulfillment.py"
AGREE = "src/api/__tests__/marketplaceDeliveryLaneAgreement.test.ts"
RULE_TS = "src/api/__tests__/marketplaceFulfillment.test.ts"
BUYER_TS = "src/screens/__tests__/MarketplaceBuyerExperience.test.ts"


def run_suite(suite: str) -> subprocess.CompletedProcess:
    """Dispatch on the suite's own path; the battery spans both runtimes.

    Matched on ``src/`` rather than on the extension, because every native suite
    in this battery is ``.ts`` rather than ``.tsx`` and an extension check would
    hand them to pytest, which collects nothing and exits 0 -- reporting every
    native mutation as a survivor for a reason that has nothing to do with the
    code.
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
        "the server reads the column first again",
        RULE,
        '    lane = _LANE_WORDS.get(str(meta.get("delivery_options") or "").strip().lower(), "")\n'
        "    if lane:\n"
        "        return lane\n"
        '    if str(listing_type or "").strip().lower():\n'
        '        return ""\n'
        '    return _LANE_WORDS.get(str(delivery_type or "").strip().lower(), "")',
        '    option = (str(delivery_type or "").strip().lower()\n'
        '              or str(meta.get("delivery_options") or "").strip().lower())\n'
        '    return _LANE_WORDS.get(option, "")',
        LANE,
        "the original defect restored in one line: the column is always "
        "populated, so the seller's own answer is never reached and every "
        "physical listing -- pickup-only included -- checks out as shipping",
    ),
    (
        "a typed row starts consulting the column again",
        RULE,
        '    if str(listing_type or "").strip().lower():\n'
        '        return ""\n'
        '    return _LANE_WORDS.get(str(delivery_type or "").strip().lower(), "")',
        '    return _LANE_WORDS.get(str(delivery_type or "").strip().lower(), "")',
        RULE_PY,
        "the column is back in the answer for rows that have a real declaration, "
        "so the DDL default 'digital' can turn a parcel into a download",
    ),
    (
        "an offer of both is settled without asking the buyer",
        RULE,
        '    if lane == "both":\n        return "shipping_or_pickup"',
        '    if lane == "both":\n        return "shipping"',
        LANE,
        "the lane chooser never renders and the buyer who wanted to collect in "
        "person is put on shipping -- the outcome resolve_choice exists to stop",
    ),
    (
        "pickup-only is answered as shipping",
        RULE,
        '    if lane == "pickup":\n        return "pickup"\n    return "shipping"',
        '    return "shipping"',
        LANE,
        "a seller who chose local pickup only has their buyers asked for a "
        "delivery address for an item nobody is going to post",
    ),
    (
        "the column's 'digital' default makes a parcel a download",
        RULE,
        '    "digital": "digital", "download": "digital",',
        '    "digital": "digital", "download": "digital", "physical": "digital",',
        # Deliberately NOT `LANE`. That file publishes through the real route, and
        # the route always writes a product type into `listing_type`, so the
        # column is never consulted there and this mutation is invisible to it.
        # The one assertion that can see it is the unit-level
        # `delivery_lane("physical", {}, "") == ""` -- which is the single most
        # load-bearing line in the rule, and would be unguarded if this entry
        # were pointed at the integration file for looking more end-to-end.
        RULE_PY,
        "a stockless kind for a physical item: no address collected, no "
        "inventory held, and the same unit sold repeatedly",
    ),
    (
        "the server drops a legacy spelling",
        RULE,
        '    "both": "both", "pickup_or_shipping": "both", "shipping_or_pickup": "both",',
        '    "both": "both", "shipping_or_pickup": "both",',
        RULE_PY,
        "rows written before the spelling was normalised silently lose their "
        "lane, and the two languages stop folding the same words",
    ),
    (
        "the cart line reads the column again",
        CART,
        # The whole body, not a line prepended to it. A column check *added* in
        # front of this function is a no-op on every row the publish route
        # writes -- the column reads `physical`, which matches no lane word --
        # so the first draft of this entry survived while changing nothing. The
        # defect was never an extra branch; it was this function answering from
        # the column instead of from the kind.
        "    kind = _fulfillment_kind(listing)\n"
        '    if kind == "digital":\n'
        '        return "digital"\n'
        '    if kind == "pickup":\n'
        '        return "pickup"\n'
        '    if kind == "shipping_or_pickup":\n'
        '        return "both"\n'
        '    return "shipping"',
        '    column = (listing.get("delivery_type") or "").strip().lower()\n'
        '    if column in ("both", "pickup_or_shipping", "shipping_or_pickup"):\n'
        '        return "both"\n'
        '    if column in ("pickup", "local", "meetup"):\n'
        '        return "pickup"\n'
        '    if column in ("digital", "download"):\n'
        '        return "digital"\n'
        '    return "shipping"',
        LANE,
        "the cart line and the checkout derive the same fact separately again, "
        "so a cart can display 'Local pickup' on a line that checks out shipped",
    ),
    (
        "the cart collapses an open choice",
        CART,
        '    if kind == "shipping_or_pickup":\n        return "both"\n    return "shipping"',
        '    return "shipping"',
        LANE,
        "the cart takes the pickup option away from a buyer whose listing page "
        "had just offered it",
    ),
    (
        "the client reads the column first again",
        TS_RULE,
        "  const declared = LANE_WORDS[String(metadata.delivery_options || \"\").trim().toLowerCase()];\n"
        "  if (declared) return declared;\n"
        "  if (String(listing.listing_type || listing.product_type || \"\").trim()) return \"\";\n"
        "  return LANE_WORDS[String(listing.delivery_type || \"\").trim().toLowerCase()] || \"\";",
        "  const column = LANE_WORDS[String(listing.delivery_type || \"\").trim().toLowerCase()];\n"
        "  if (String(listing.delivery_type || \"\").trim()) return column || \"\";\n"
        "  return LANE_WORDS[String(metadata.delivery_options || \"\").trim().toLowerCase()] || \"\";",
        AGREE,
        "the phone and the server answer differently on the same row: the "
        "backend honours pickup and the app asks for a shipping address",
    ),
    (
        "the client lets a typed row read the column",
        TS_RULE,
        "  if (String(listing.listing_type || listing.product_type || \"\").trim()) return \"\";\n"
        "  return LANE_WORDS[String(listing.delivery_type || \"\").trim().toLowerCase()] || \"\";",
        "  return LANE_WORDS[String(listing.delivery_type || \"\").trim().toLowerCase()] || \"\";",
        RULE_TS,
        "the product type is read as a lane on the client, which is the half of "
        "the defect that lived in this file",
    ),
    (
        "the client settles an open choice by itself",
        TS_RULE,
        '  if (lane === "both") return "shipping_or_pickup";',
        '  if (lane === "both") return "shipping";',
        AGREE,
        "`shipping_or_pickup` becomes unreachable on the client, so "
        "`mustChooseLane` is dead and the chooser is unreachable UI",
    ),
    (
        "the client's word list diverges from the server's",
        TS_RULE,
        "  meetup: \"pickup\",\n",
        "",
        RULE_PY,
        "the two languages fold different words onto different lanes, which no "
        "single-language suite can see -- only a test that reads both files",
    ),
    (
        "every physical grid card loses its buy button again",
        TS_SCREEN,
        '  const declared = String(listing.listing_type || listing.product_type || "").trim();\n'
        '  if (!declared && !deliveryLane(listing)) return "unknown";',
        '  const column = String(listing.delivery_type || "").toLowerCase();\n'
        '  if (!column.includes("ship") && !column.includes("pickup") && !column.includes("digital")) {\n'
        '    return "unknown";\n'
        "  }",
        AGREE,
        "the measured damage, exactly: `physical` contains none of those "
        "substrings, so every card in the marketplace falls to unknown and "
        "`gridCardAction` returns null -- no Add to cart, no Make offer",
    ),
    (
        "the grid card stops distinguishing pickup",
        TS_SCREEN,
        '  if (kind === "pickup") return "local";\n  return "platform";',
        '  return "platform";',
        AGREE,
        "a pickup-only listing offers Add to cart, entering a checkout that "
        "will ask for a delivery address it never needed",
    ),
    (
        "the guard stops being reachable",
        TS_SCREEN,
        '  if (!declared && !deliveryLane(listing)) return "unknown";',
        "",
        AGREE,
        "`unknown` becomes an enumeration member no input can produce -- a row "
        "that declared nothing at all is offered for sale as a shipped item",
    ),
    (
        "the detail page derives the lane a second time",
        TS_BUYER,
        '  const kind = resolveFulfillmentKind(listing);\n'
        '  if (kind === "digital") return "digital";\n'
        '  if (kind === "pickup") return "pickup";\n'
        '  if (kind === "shipping_or_pickup") return "both";\n'
        '  return "shipping";',
        '  const column = String(listing.delivery_type || "").trim().toLowerCase();\n'
        '  if (column === "pickup") return "pickup";\n'
        '  if (column === "digital") return "digital";\n'
        '  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;\n'
        '  const option = String(metadata.delivery_options || "").trim().toLowerCase();\n'
        '  if (option === "pickup") return "pickup";\n'
        '  if (option === "both") return "both";\n'
        '  return "shipping";',
        AGREE,
        "two fields carrying one fact can differ again -- `handleBuyNow` sends "
        "both of them to checkout two lines apart and the wrong one wins",
    ),
    (
        "the sentence stops matching the lane",
        TS_BUYER,
        '  if (kind === "pickup") return "Local pickup";',
        '  if (kind === "pickup") return "Shipping";',
        AGREE,
        "the buyer reads one lane on the product page and is checked out on "
        "another -- the drift the copy and the kind are asserted against each "
        "other to prevent",
    ),
    (
        "an undeclared row is described as shipped",
        TS_BUYER,
        '    if (!declared && !String(metadata.delivery_options || "").trim()) {\n'
        '      return "Delivery details shown at checkout";\n'
        "    }\n",
        "",
        BUYER_TS,
        "a row that declared nothing is given a lane no seller chose, which is "
        "the same invention in the opposite direction",
    ),
    (
        "the client's lane helper is renamed",
        TS_RULE,
        "  const declared = LANE_WORDS[String(metadata.delivery_options || \"\").trim().toLowerCase()];\n"
        "  if (declared) return declared;",
        "  const declaredLane = LANE_WORDS[String(metadata.delivery_options || \"\").trim().toLowerCase()];\n"
        "  if (declaredLane) return declaredLane;",
        RULE_PY,
        "(inverted) this must SURVIVE: the cross-language test reads this "
        "function's source to pin the *order* the fields are consulted in, and "
        "a test that fails on a local rename is pinning characters instead",
    ),
    (
        "no-op control",
        RULE,
        "    meta = metadata if isinstance(metadata, dict) else {}\n"
        '    kind = str(listing_type or "").strip().lower()',
        "    meta = metadata if isinstance(metadata, dict) else dict()\n"
        '    kind = str(listing_type or "").strip().lower()',
        LANE,
        "(control)",
    ),
]

#: Entry 19 is inverted on purpose. `test_the_client_reads_the_sellers_declaration_first_too`
#: reads `deliveryLane`'s source text, because field *order* is the entire defect
#: and order is not observable from the outside once the fix is in. A source-
#: reading assertion is exactly the kind that pinned this bug in the first place,
#: so it has to be shown to be scoped to what it claims: renaming a local must
#: not fail it.
INVERTED = {19}

#: The last entry is a deliberate no-op: `{}` and `dict()` are the same object.
#: If the battery reports it caught, the battery is measuring noise -- a flaky
#: suite, a stale `__pycache__`, or an anchor that changed more than it looked
#: like it did.
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
