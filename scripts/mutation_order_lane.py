#!/usr/bin/env python3
"""Mutation battery for the order's delivery lane (gap 11).

Gap 9/10 fixed how a *listing* declares its lane. This is the *order* side, and
it is a different mistake with the same shape: the timeline that tells a buyer
whether their item is travelling or waiting to be collected was chosen by
reading `order.delivery_type`, a field no order endpoint has ever served.

`pulse_buyer_order_response` names its listing columns explicitly and
`delivery_type` is not among them; nothing adds a top-level one. So the argument
was always `undefined`, `variantOf`'s pickup branch was unreachable, every order
rendered the shipping strip, and `escrowPresentable` -- which is
`variant === "pickup"` -- was permanently false, making the escrow safety panel
unreachable UI. The seller's copy had its own version of the same bug:
`variantOf(String(order.item_type || ""))` fed a row kind into a parameter named
`deliveryType`, and `item_type` is "marketplace_product" on every row.

The fix adds no seventh derivation. Checkout already freezes the *settled* kind
onto the transaction via `marketplace_fulfillment.snapshot`, and the serializer
has been parsing that metadata all along without reading the key. So the
serializers serve it and the client folds down from it.

Three things the mutations below are aimed at, because each is a way the fix
could be undone while every suite stayed green:

*   Serving the kind but deriving it freshly from the listing. That looks
    equivalent and is not: the frozen value is post-`resolve_choice`, so a
    listing that offered both lanes carries the lane the buyer actually picked.
*   Widening what counts as a lane -- echoing an unvalidated string from
    metadata, or letting the client read `delivery_type` again as a fallback.
    Either restores the dead branch under a new name.
*   Collapsing the in-person kinds, which silently re-hides the escrow panel.

One mutation is inverted: renaming the client's local parameter changes the
source text without changing behaviour, and nothing should notice.

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
BOT = ROOT / "bot.py"
TS_DASH = NATIVE / "src/api/ordersDashboard.ts"

ORDER = "tests/test_marketplace_order_lane.py"
DASH = "src/api/__tests__/ordersDashboard.test.ts"


def run_suite(suite: str) -> subprocess.CompletedProcess:
    """Dispatch on the suite's own path; the battery spans both runtimes.

    Matched on ``src/`` rather than on the extension: the native suite here is
    ``.ts``, and an extension check would hand it to pytest, which collects
    nothing and exits 0 -- reporting the mutation as a survivor for a reason
    that has nothing to do with the code.
    """
    if suite.startswith("src/"):
        return subprocess.run(["npx", "jest", "--runTestsByPath", suite, "--silent"],
                              cwd=NATIVE, capture_output=True, text=True)
    # One file per process: importing `bot` binds DATABASE_URL process-wide.
    return subprocess.run([str(PY), "-m", "pytest", suite, "-q", "-x"],
                          cwd=ROOT, capture_output=True, text=True)


# (name, file, old, new, suite, what a surviving mutant would mean)
MUTATIONS = [
    # ---------------------------------------------------------------- server
    (
        "the buyer serializer stops serving the lane",
        BOT,
        '        "fulfillment_kind": fulfillment_kind,\n',
        "",
        ORDER,
        "the defect restored exactly: the order payload carries no lane at all, "
        "so the client has nothing to read and every timeline is shipping",
    ),
    (
        "the seller list stops serving the lane",
        BOT,
        '        order["fulfillment_kind"] = marketplace_fulfillment.order_kind(order_metadata)',
        '        order["fulfillment_kind"] = ""',
        ORDER,
        "the seller cannot tell a parcel from a collection, so they post an "
        "item the buyer is on their way to pick up",
    ),
    (
        "the order re-derives its lane from the listing instead of its own record",
        RULE,
        '    frozen = metadata.get("fulfillment") if isinstance(metadata, dict) else None\n'
        "    if isinstance(frozen, dict):\n",
        "    frozen = None\n"
        "    if isinstance(frozen, dict):\n",
        ORDER,
        "the settled choice is thrown away and the listing is asked again: an "
        "order placed on a both-lane listing loses which lane the buyer picked, "
        "and a seller editing the listing afterwards rewrites completed orders",
    ),
    (
        "an unvalidated kind is echoed onto the wire",
        RULE,
        "        if kind in KINDS:\n            return kind\n",
        "        if kind:\n            return kind\n",
        ORDER,
        "any string in the transaction metadata becomes a lane, so a corrupt or "
        "half-written snapshot is served to the client as a fact rather than "
        "falling through to the listing",
    ),
    (
        "the legacy fallback answers shipping instead of admitting it has nothing",
        RULE,
        '            meta,\n        )\n    return ""',
        '            meta,\n        )\n    return "shipping"',
        ORDER,
        "an order with neither a snapshot nor a listing is served a lane it "
        "never recorded -- a guess promoted to a server fact, which is exactly "
        "how the client came to trust a field that meant nothing",
    ),
    (
        "the raw metadata column is passed through unparsed",
        RULE,
        '        meta = listing.get("listing_metadata")\n'
        "        if not isinstance(meta, dict):\n",
        '        meta = listing.get("listing_metadata")\n'
        "        if False:\n",
        ORDER,
        "`resolve_kind` ignores a metadata string silently, so the seller's "
        "entire `delivery_options` declaration is dropped and every legacy "
        "pickup order reads as shipping",
    ),
    (
        "the frozen kind is matched without normalising it",
        RULE,
        '        kind = str(frozen.get("kind") or "").strip().lower()',
        '        kind = str(frozen.get("kind") or "")',
        ORDER,
        "a kind written with different case or padding stops matching KINDS and "
        "falls through to the listing, so the buyer's settled choice is lost on "
        "exactly the rows a migration is most likely to produce",
    ),
    # ---------------------------------------------------------------- client
    (
        "the client reads the field the server does not send, again",
        TS_DASH,
        "  const variant = variantOf(order.fulfillment_kind);\n  const status = normalizeStatus(order.status_group",
        "  const variant = variantOf((order as BuyerOrder & { delivery_type?: string }).delivery_type);\n"
        "  const status = normalizeStatus(order.status_group",
        DASH,
        "the original defect: an argument that is always undefined, so the "
        "pickup branch is unreachable and the escrow panel can never render",
    ),
    (
        "the seller path goes back to passing the row kind as a lane",
        TS_DASH,
        "  const variant = variantOf(order.fulfillment_kind);\n  const status = normalizeStatus(order.status);",
        '  const variant = variantOf(String(order.item_type || ""));\n  const status = normalizeStatus(order.status);',
        DASH,
        "`item_type` is 'marketplace_product' on every row, so the seller's "
        "timeline is shipping-only by construction and the two perspectives "
        "disagree about the same order",
    ),
    (
        "the client keeps the column as a fallback",
        TS_DASH,
        "function variantOf(fulfillmentKind?: string): OrderTimelineVariant {\n"
        '  return IN_PERSON_KINDS.has(String(fulfillmentKind || "").toLowerCase()) ? "pickup" : "shipping";',
        "function variantOf(fulfillmentKind?: string): OrderTimelineVariant {\n"
        '  const d = String(fulfillmentKind || "").toLowerCase();\n'
        '  if (d === "pickup" || d === "local") return "pickup";\n'
        '  return IN_PERSON_KINDS.has(d) ? "pickup" : "shipping";',
        DASH,
        "'local' is a lane word from the listing vocabulary, not a fulfilment "
        "kind. Accepting it means the client is still speaking the old "
        "language, and a future payload carrying it would be honoured silently",
    ),
    (
        "pickup is dropped from the in-person set",
        TS_DASH,
        '  "pickup",\n  "service_in_person",',
        '  "service_in_person",',
        DASH,
        "the single most common in-person order renders as a parcel and the "
        "escrow panel stays unreachable -- the whole defect, minus the excuse",
    ),
    (
        "the in-person service and booking kinds collapse to shipping",
        TS_DASH,
        '  "service_in_person",\n  "booking_in_person",\n  "event_in_person"\n',
        '  "event_in_person"\n',
        DASH,
        "a buyer meeting someone for a haircut or a lesson is told their order "
        "is on its way, and is never shown the safety guidance for meeting a "
        "stranger",
    ),
    (
        "an undecided kind is allowed to render as pickup",
        TS_DASH,
        '  "pickup",\n  "service_in_person",\n  "booking_in_person",\n  "event_in_person"\n',
        '  "pickup",\n  "shipping_or_pickup",\n  "service_in_person",\n'
        '  "booking_in_person",\n  "event_in_person"\n',
        DASH,
        "a lane nobody chose unlocks the escrow presentation -- the one thing "
        "the original comment was right to refuse to guess",
    ),
    (
        "escrow stops depending on the lane",
        TS_DASH,
        "    escrowPresentable: ordersEscrowIsLive() && variant === \"pickup\",\n"
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    returnWindowClosesAt:",
        "    escrowPresentable: ordersEscrowIsLive(),\n"
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    returnWindowClosesAt:",
        DASH,
        "the handoff safety panel is shown to a buyer whose item is in the "
        "post, asserting an escrow hold on an order with no handoff",
    ),
    (
        "the two perspectives are allowed to answer differently",
        TS_DASH,
        "  // This passed `item_type` into a parameter named `deliveryType`.",
        '  if (String(order.status || "") === "paid") return unifyBuyerOrder(order as never);\n'
        "  // This passed `item_type` into a parameter named `deliveryType`.",
        DASH,
        "not a lane bug but a shape bug -- it proves the cross-view assertion "
        "is comparing two real derivations rather than one function called "
        "twice",
    ),
    # ---------------------------------------------------------------- inverted
    (
        "the client's parameter is renamed",
        TS_DASH,
        "function variantOf(fulfillmentKind?: string): OrderTimelineVariant {\n"
        '  return IN_PERSON_KINDS.has(String(fulfillmentKind || "").toLowerCase()) ? "pickup" : "shipping";',
        "function variantOf(kind?: string): OrderTimelineVariant {\n"
        '  return IN_PERSON_KINDS.has(String(kind || "").toLowerCase()) ? "pickup" : "shipping";',
        DASH,
        "a pure rename was reported as a behaviour change, which means some "
        "assertion is reading the source text rather than the behaviour",
    ),
    # ---------------------------------------------------------------- control
    (
        "a comment is reworded",
        RULE,
        "    The inverse of :func:`snapshot`, and the only function any order serializer\n"
        "    should ask.",
        "    The inverse of :func:`snapshot`, and the one function any order serializer\n"
        "    should ask.",
        ORDER,
        "",
    ),
]

# Indices (1-based) that must NOT be caught.
INVERTED = {16}
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
