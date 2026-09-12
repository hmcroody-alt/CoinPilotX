#!/usr/bin/env python3
"""Mutation battery for the order timeline's fulfilment strips (gap 12).

Gap 11 made the settled `fulfillment_kind` reachable by the orders dashboard.
This is what the client then did with it: `OrderTimelineVariant` was
`"shipping" | "pickup"` -- two strips for eleven kinds -- so every order that was
not handed over in person was described as a parcel.

`scripts/probe_order_timeline_kinds.py` published one listing per lane and bought
each through the real checkout route. Four of the nine came back on the parcel
strip: a digital download, a remote service, an online event and a remote
booking. Their buyers were told "Being packed", then "On its way", then
"Delivered". Their sellers were offered "Mark packed" and a "Mark shipped"
disabled with "Add a tracking number before marking this order shipped" -- a
precondition a downloadable file can never meet.

Two root causes, and the mutations below are aimed at both:

*   ONE DERIVATION ANSWERING TWO QUESTIONS. `escrowPresentable` was
    `flag && variant === "pickup"`, fusing "which strip describes this order"
    with "do these two people meet in person". Those had the same answer only
    while there were two strips. Several mutations re-fuse them, in both
    directions: escrow following the strip again, and the in-person kinds being
    dragged back onto the pickup strip so that it can.
*   A SECOND, COARSER COPY OF A VOCABULARY THAT ALREADY EXISTED.
    `marketplaceFulfillment` holds `MarketplaceFulfillmentKind`,
    `isScheduledKind` and `isInPersonKind`, and tells the buyer at checkout that
    a digital purchase is "Delivered to your PulseSoc account" -- one screen
    before the orders list said "Being packed". Mutations that collapse a kind
    back onto the parcel strip test whether anything notices the drift.

Two suites, because a derivation is not delivered until something renders it.
`ordersDashboard.test.ts` proves the model picks the right strip;
`OrderTimeline.test.tsx` proves the component draws the strip the model picked --
it used to choose its own with a second `variant === "pickup" ? … : …`, which is
how a two-step digital strip gets drawn against a four-step reached index.

This battery is native-only on purpose. The server side of the lane was fixed and
mutation-tested in gap 11 (`scripts/mutation_order_lane.py`); nothing about gap 12
changes what the backend sends.

One mutation is inverted: renaming a parameter changes the source text and
nothing else, and nothing should notice. One is a no-op control.

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

DASH = NATIVE / "src/api/ordersDashboard.ts"
KINDS = NATIVE / "src/api/marketplaceFulfillment.ts"
STRIP = NATIVE / "src/components/orders/OrderTimeline.tsx"

# Both suites run for every mutation. The split between "the model chose the
# right strip" and "the component drew it" is exactly the seam this gap lived in,
# so a battery that ran only one of them could not tell the two apart.
SUITES = [
    "src/api/__tests__/ordersDashboard.test.ts",
    "src/components/orders/__tests__/OrderTimeline.test.tsx",
]


def run_suites() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "jest", "--runTestsByPath", *SUITES, "--silent"],
        cwd=NATIVE, capture_output=True, text=True,
    )


# (name, file, old, new, what a surviving mutant would mean)
MUTATIONS = [
    # ------------------------------------------------- the strip for each kind
    (
        "a digital sale goes back on the parcel strip",
        DASH,
        '  digital: "digital",\n',
        '  digital: "shipping",\n',
        "the defect verbatim: someone who bought a file is told it is being "
        "packed and then that it is on its way, and the seller is asked for a "
        "tracking number for a download",
    ),
    (
        "a remote service goes back on the parcel strip",
        DASH,
        '  service_remote: "scheduled",\n',
        '  service_remote: "shipping",\n',
        "a video consultation is described as a parcel in transit, and its "
        "seller is offered a packing step for an appointment",
    ),
    (
        "an online event goes back on the parcel strip",
        DASH,
        '  event_online: "scheduled",\n',
        '  event_online: "shipping",\n',
        "a ticket to a livestream is 'Being packed' -- the exact wording the "
        "checkout screen had already replaced with 'Joined online'",
    ),
    (
        "an in-person service is dragged back onto the pickup strip",
        DASH,
        '  service_in_person: "scheduled",\n',
        '  service_in_person: "pickup",\n',
        "the old fusion restored: a haircut can only be given stranger-safety "
        "advice by also being described to the buyer as an item awaiting "
        "collection, which is what made the two questions look like one",
    ),
    (
        "an unrecognised kind is guessed as a collection",
        DASH,
        '  return kind ? TIMELINE_VARIANT_BY_KIND[kind] : "shipping";',
        '  return kind ? TIMELINE_VARIANT_BY_KIND[kind] : "pickup";',
        "a legacy row with no resolvable lane unlocks the pickup strip, so the "
        "surface promises a handoff for an order that recorded none",
    ),
    (
        "the kind is no longer validated before it is looked up",
        DASH,
        "  return KNOWN_KINDS.has(kind) ? (kind as MarketplaceFulfillmentKind) : null;",
        "  return kind ? (kind as MarketplaceFulfillmentKind) : null;",
        "any string on the wire is treated as a kind, so a lane word from the "
        "listing vocabulary ('local', 'meetup') indexes the map to undefined "
        "and the variant becomes a value no strip answers to",
    ),
    (
        "the buyer path reads the field the server does not send, again",
        DASH,
        "  const variant = timelineVariantOf(order.fulfillment_kind);\n"
        "  const status = normalizeStatus(order.status_group",
        "  const variant = timelineVariantOf((order as BuyerOrder & { delivery_type?: string }).delivery_type);\n"
        "  const status = normalizeStatus(order.status_group",
        "gap 11's defect restored underneath gap 12's fix: the argument is "
        "always undefined, so every strip is shipping again and the eleven-way "
        "map is dead code that typechecks",
    ),
    # ------------------------------------------- escrow refused its own question
    (
        "escrow follows the strip again on the buyer side",
        DASH,
        "    escrowPresentable: ordersEscrowIsLive() && orderIsInPerson(order.fulfillment_kind),\n"
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    returnWindowClosesAt:",
        '    escrowPresentable: ordersEscrowIsLive() && variant === "pickup",\n'
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    returnWindowClosesAt:",
        "the two questions are fused back together, and the three in-person "
        "kinds that now live on the scheduled strip silently lose the safety "
        "panel -- a regression invisible to anyone reading the diff for 'escrow'",
    ),
    (
        "escrow follows the strip again on the seller side",
        DASH,
        "    escrowPresentable: ordersEscrowIsLive() && orderIsInPerson(order.fulfillment_kind),\n"
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    raw: { seller: order }",
        '    escrowPresentable: ordersEscrowIsLive() && variant === "pickup",\n'
        "    awaitingCash: isAwaitingCash(order.status),\n"
        "    raw: { seller: order }",
        "the same fusion on one end only, so buyer and seller disagree about "
        "whether the same order involves meeting someone",
    ),
    (
        "the in-person question stops being asked at all",
        DASH,
        "  const kind = asFulfillmentKind(fulfillmentKind);\n  return kind ? isInPersonKind(kind) : false;",
        "  return true;",
        "the handoff safety panel is presented on a parcel order and on a "
        "download, asserting an escrow hold against an order with no handoff",
    ),
    (
        "a remote booking is counted as meeting someone",
        KINDS,
        '    kind === "pickup" ||\n    kind === "service_in_person" ||',
        '    kind === "pickup" ||\n    kind === "booking_remote" ||\n    kind === "service_in_person" ||',
        "advice about meeting a stranger safely is given to someone joining a "
        "video call, which teaches the buyer the panel means nothing",
    ),
    # ------------------------------------------------ what the buyer actually reads
    (
        "the component picks its own steps again",
        STRIP,
        "  const steps = stepsForVariant(variant);",
        '  const steps = variant === "pickup" ? stepsForVariant("pickup") : stepsForVariant("shipping");',
        "the second copy of the strip choice is back: the model says digital "
        "and the component draws four parcel dots, so the two-step reached "
        "index points at the wrong label -- and the derivation tests all pass",
    ),
    (
        "a digital order never reaches its delivered step",
        DASH,
        '      return indexOfKey(steps, "available");',
        '      return indexOfKey(steps, "paid");',
        "the file is attached to the order and the buyer is told only that the "
        "order was placed, so a completed purchase looks unfulfilled forever",
    ),
    (
        "a provisional middle is invented for a digital sale",
        DASH,
        '  { key: "available", sellerLabel: "Delivered", buyerLabel: "Delivered to your account", mock: false }\n];',
        '  { key: "preparing", sellerLabel: "Preparing", buyerLabel: "Ready to download", mock: true },\n'
        '  { key: "available", sellerLabel: "Delivered", buyerLabel: "Delivered to your account", mock: false }\n];',
        "a step the listing validator forbids -- `_validate_digital` refuses any "
        "delivery mode but automatic -- and a label promising a download control "
        "that does not exist on any screen",
    ),
    (
        "the appointment step stops being marked provisional",
        DASH,
        '  { key: "scheduled", sellerLabel: "Scheduled", buyerLabel: "Scheduled", mock: true },',
        '  { key: "scheduled", sellerLabel: "Scheduled", buyerLabel: "Scheduled", mock: false },',
        "the surface claims an appointment is scheduled when the live payload "
        "carries no such state -- provisional progress presented as fact, which "
        "is the one thing the Preview tag exists to prevent",
    ),
    # ---------------------------------------------- what the seller is offered
    (
        "the digital seller falls back into the shipping branch",
        DASH,
        '    } else if (order.variant === "shipping") {',
        "    } else {",
        "the seller of a download is offered 'Mark packed' and a 'Mark shipped' "
        "whose only enabling condition is a tracking number a file can never "
        "have -- a control with no reachable path to being usable",
    ),
    (
        "an appointment is treated as a handoff",
        DASH,
        '    if (order.variant === "pickup") {\n'
        '      actions.push(previewOrDisabled("confirm_handoff", "Confirm handoff", live));',
        '    if (order.variant === "pickup" || order.variant === "scheduled") {\n'
        '      actions.push(previewOrDisabled("confirm_handoff", "Confirm handoff", live));',
        "a remote consultation asks the seller to confirm they handed something "
        "over, and the completion action becomes unreachable code",
    ),
    (
        "the completion action outlives the order",
        DASH,
        '      if (order.status !== "delivered") {\n'
        '        actions.push(previewOrDisabled("mark_completed", "Mark completed", live));\n'
        "      }",
        '      actions.push(previewOrDisabled("mark_completed", "Mark completed", live));',
        "a finished appointment still offers 'Mark completed', so the seller "
        "cannot tell from the card whether they already did",
    ),
    # -------------------------------------------------------- the gap ledger
    (
        "the gap count is asserted instead of measured",
        DASH,
        "export const ORDERS_MOCK_DATA_GAP_COUNT = ORDERS_MOCK_DATA_GAPS.length;",
        "export const ORDERS_MOCK_DATA_GAP_COUNT = 7;",
        "the ledger reports a number it did not count, so a gap can be added or "
        "closed without the pinned figure moving -- the same defect as a harness "
        "reporting a result it never measured",
    ),
    # ------------------------------------------------------------- inverted
    (
        "the lane parameter is renamed",
        DASH,
        "export function timelineVariantOf(fulfillmentKind?: string): OrderTimelineVariant {\n"
        "  const kind = asFulfillmentKind(fulfillmentKind);",
        "export function timelineVariantOf(value?: string): OrderTimelineVariant {\n"
        "  const kind = asFulfillmentKind(value);",
        "a pure rename was reported as a behaviour change, which means some "
        "assertion is reading the source text rather than the behaviour",
    ),
    # -------------------------------------------------------------- control
    (
        "a comment is reworded",
        DASH,
        "/** The payload's `fulfillment_kind` as a kind, or null if it is not one. */",
        "/** The payload's `fulfillment_kind` as a kind, or null when it is not one. */",
        "",
    ),
]

# Indices (1-based) that must NOT be caught.
INVERTED = {len(MUTATIONS) - 1}
NO_OP_INDEX = len(MUTATIONS)


def main() -> int:
    survivors = []
    control_failed = False

    for index, (name, path, old, new, meaning) in enumerate(MUTATIONS, 1):
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR    {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        path.write_text(original.replace(old, new, 1))
        try:
            result = run_suites()
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
