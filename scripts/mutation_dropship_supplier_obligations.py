#!/usr/bin/env python3
"""Mutation battery for the supplier obligation list (gap 14).

What this is aimed at
---------------------
A buyer could pay for a published, variant-bound dropship listing and nothing
on the supplier side recorded that the merchant now owed a purchase. Measured,
not inferred: `scripts/probe_dropship_paid_order_fulfillment.py` publishes a
dropship listing through the real importer, writes the paid `marketplace_orders`
row exactly as `pulse_upsert_marketplace_order` projects one, and then finds
`business_os_supplier_intents` and `business_os_supplier_outbox` at zero rows
with no function in the fulfilment module able to answer "which of my orders
needs a supplier order placed?".

Three root causes, and the mutations below are aimed at all three:

*   A WRITER REACHABLE ONLY FROM PYTEST. `create_intent` is the sole writer of
    `business_os_supplier_intents`; its one production call site had zero
    callers in `mobile-native/src`, `templates/` or `static/`; the dispatch
    worker is not in the Procfile. Measured off its own bytecode, that function
    demands a shipping quote under 300 seconds old and an
    `expected_supplier_cost_cents` equal to it exactly -- it is a merchant
    *approval* action, so calling it from a payment webhook was never the fix.
    The obligation is derived on read instead. The first group of mutations
    attacks that derivation: the cast the join needs, the four filters that
    make the list correct rather than merely non-empty, the two contradictions
    the first draft of it contained, and the alias that keeps a supplier status
    off the payment provider's field name.
*   A SECOND COPY OF AN ENUMERATION WITH NO COMPILER SPANNING IT. The outbox's
    six state names live in SQL literals across three Python modules and again
    as `SUPPLIER_ORDER_STATES` in TypeScript. This is the seam that printed
    `SUPPLIER_VARIANT_UNBOUND` on a merchant's screen at gap 13. The second
    group attacks both halves of the defence: the total `Record` the compiler
    can walk, and `test_supplier_obligation_copy.py`, which crosses the
    boundary the compiler cannot.
*   A GAP NOTE THAT UNDERSTATED ITS GAP AND NAMED A TABLE THAT DOES NOT EXIST.
    `DROPSHIPPING_DATA_GAPS` claimed the layer "can create and read a single
    intent by id" and only lacked an enumeration -- when in fact nothing
    reachable created one -- and it named
    `business_os_supplier_fulfillment_intents`, which does not exist. The real
    tables are `business_os_supplier_intents` and
    `business_os_supplier_outbox`. The name had also been copied into the stage
    table of `CJ_IMPORT_TO_CHECKOUT_ARCHITECTURE_MAP.md`, where it sat wrong for
    two days -- a fiction spreads by being quoted. The third group attacks the
    screen that now
    renders the real list, the sandbox promise it must take from the server
    rather than from a build flag, and the gap prose it must take from the list
    rather than write itself.

Four checks, because the defences are in four places
----------------------------------------------------
*   `tests/dropshipping/test_supplier_obligation_copy.py` -- the Python state
    literals and the TypeScript list name the same things, and every state has
    words. Reads the TS side as *data*: a literal array and an object's keys.
*   `tests/dropshipping/test_supplier_obligations.py` -- a paid dropship sale
    appears, the four filters hold, and the SQL casts the order id. Two of its
    tests read data rather than behaviour on purpose, because the SQLite
    failure mode for the cast is a silently empty join that no behavioural test
    can see.
*   `DropshippingScreens.test.tsx` + `dropshipping.test.ts` -- the merchant can
    actually see the backlog. A list the server serves and a backend test pins
    is not delivered until something renders it.
*   `npx tsc --noEmit` -- `SUPPLIER_ORDER_STATE_COPY` is total over
    `SupplierOrderState`, so a state added to the union without copy fails the
    build.

The two Python files run in separate processes: each binds `DATABASE_URL` at
import against its own temp database, and `tests/dropshipping/` cannot share a
pytest process at all.

Checks run cheapest-first and stop at the first failure, so the report says
*which* defence caught each mutation -- useful, because several of these are
caught by only one of the four.

Three mutations are inverted, and a mutation battery is only honest if it says
why rather than quietly dropping them. All three change the source text and
nothing a test can observe:

*   a pure rename of a local;
*   moving the `supplier_order_placed` read to a client-side re-derivation of
    `intent_id`. The server writes `supplier_order_placed = intent_id is
    not None`, so no response it can produce distinguishes the two. Catching it
    would need a fixture with `intent_id` set and `supplier_order_placed`
    false -- a row the backend cannot emit, and a fixture describing an
    impossible row is not a test of the system. The reason to keep the field
    read is that the server's rule may stop being that identity; that is an
    argument about a future, not a claim about today, so it is a comment there
    and an inverted mutation here;
*   reordering the `!connectionId` guard after the scope phases. Damaging
    only if `useDropshippingScope` ever reaches `ready` on its first render,
    which it cannot: it always starts in `loading` and resolves a microtask
    later, so the guard has already run. Recorded in a comment on the screen
    for the same reason.

One mutation is a no-op control.

A mutation that survives means the assertion holding it up does not.

Read-only against the repo: every mutation is written, tested, and reverted from
an in-memory copy of the original file, including on failure.

    .venv/bin/python3 scripts/mutation_dropship_supplier_obligations.py
    .venv/bin/python3 scripts/mutation_dropship_supplier_obligations.py --only 1,8
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

FULFILLMENT = ROOT / "services/business_os/suppliers/fulfillment.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/DropshippingOrdersScreen.tsx"
SYNC = NATIVE / "src/screens/dropshipping/DropshippingSyncScreen.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _copy_pin() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligation_copy.py")


def _obligations() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligations.py")


def _jest() -> subprocess.CompletedProcess:
    # Both suites in one process: the api contract and the screen that renders
    # it fail for different reasons, and running them together costs one startup
    # rather than two.
    return subprocess.run(
        ["npx", "jest", "--runTestsByPath",
         "src/api/__tests__/dropshipping.test.ts",
         "src/screens/dropshipping/__tests__/DropshippingScreens.test.tsx",
         "--silent"],
        cwd=NATIVE, capture_output=True, text=True,
    )


def _typecheck() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"],
        cwd=NATIVE, capture_output=True, text=True,
    )


# Cheapest and most specific first; the report names whichever one fires.
CHECKS = [
    ("copy-pin", _copy_pin),
    ("obligations", _obligations),
    ("jest", _jest),
    ("tsc", _typecheck),
]


def first_failing() -> str | None:
    for label, run in CHECKS:
        if run().returncode != 0:
            return label
    return None


# (name, file, old, new, what a surviving mutant would mean)
MUTATIONS = [
    # ------------------------------------------- the derivation on the server
    (
        "the order id join compares integer to text",
        FULFILLMENT,
        "\"LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) \"",
        "\"LEFT JOIN business_os_supplier_intents i ON i.order_id = o.id \"",
        "the join raises on PostgreSQL and silently matches nothing on SQLite, "
        "so every placed supplier order reads as unplaced and the merchant is "
        "invited to buy the same goods twice -- with no exception, no empty "
        "list, and nothing for a behavioural test to see",
    ),
    (
        "hand-fulfilled listings become supplier obligations",
        FULFILLMENT,
        "\"AND s.fulfillment_mode = ? AND LOWER(o.status) = 'paid' \"",
        "\"AND LOWER(o.status) = 'paid' \"",
        "every sale of a self-stocked product appears as owing a supplier "
        "purchase, so the backlog the merchant is meant to work through is "
        "mostly noise and the real dropship rows are lost in it",
    ),
    (
        "unpaid orders become supplier obligations",
        FULFILLMENT,
        "\"AND s.fulfillment_mode = ? AND LOWER(o.status) = 'paid' \"",
        "\"AND s.fulfillment_mode = ? \"",
        "abandoned checkouts are presented as goods to buy. The merchant spends "
        "real money with the supplier for a sale that was never completed, "
        "which is the one error on this screen that cannot be undone",
    ),
    (
        "the supplier connection scope is dropped",
        FULFILLMENT,
        "\"WHERE s.supplier_connection_id = ? AND s.business_id = ? AND s.store_id = ? \"",
        "\"WHERE s.business_id = ? AND s.store_id = ? \"",
        "a tenancy leak carrying supplier cost: one merchant reads another's "
        "paid sales and what those sales cost them wholesale",
    ),
    (
        "non-physical listings become supplier obligations",
        FULFILLMENT,
        '        if listing_type != "physical":\n            continue\n',
        "",
        "downloads and bookings are listed as needing a parcel bought from a "
        "supplier, and `create_intent` would refuse each one -- a backlog of "
        "rows whose only possible action is an error",
    ),
    (
        "an intent with no outbox row reads as never placed",
        FULFILLMENT,
        '            "state": outbox_state or ("UNKNOWN" if intent_id else AWAITING_SUPPLIER_ORDER),',
        '            "state": outbox_state or AWAITING_SUPPLIER_ORDER,',
        "the payload contradicts itself -- `supplier_order_placed: true` beside "
        "\"No supplier order yet\" -- and the half the merchant reads is the one "
        "that tells them to order it again",
    ),
    (
        "the raw outbox column is popped only when an intent exists",
        FULFILLMENT,
        "        outbox_state = item.pop(\"outbox_state\")\n        intent_id = item.get(\"intent_id\")",
        "        intent_id = item.get(\"intent_id\")\n        outbox_state = (item.pop(\"outbox_state\")\n                        if intent_id else item.get(\"outbox_state\"))",
        "on the unplaced path a null `outbox_state` rides along beside `state`: "
        "two spellings of one fact, with a caller free to read the null one. "
        "This was the first draft of the function",
    ),
    (
        "the supplier status ships under the payment provider's field name",
        FULFILLMENT,
        '            "b.state AS outbox_state, b.provider_order_id, "\n'
        '            "b.provider_status AS supplier_order_status, "',
        '            "b.state AS outbox_state, b.provider_order_id, b.provider_status, "',
        "`provider_status` is Stripe's subscription status on this platform, and "
        "the mobile entitlement drift guard lists it among the raw membership "
        "fields no unlisted file may hold. Shipping CJ's order status under that "
        "name forces either an exemption for `api/dropshipping.ts` or a weaker "
        "guard, for nothing but a name collision",
    ),
    # ------------------------ the enumeration with no compiler spanning it
    (
        "an unconfirmed order is described as never placed",
        API,
        '  UNKNOWN: "Unconfirmed — do not re-order",',
        '  UNKNOWN: "No supplier order yet",',
        "the distinction the whole state vocabulary exists for is erased. "
        "UNKNOWN means a write to the supplier could not be confirmed, so a "
        "purchase may already exist; describing it as not placed is an "
        "instruction to place a duplicate",
    ),
    (
        "a state the backend writes is dropped from the mobile list",
        API,
        '  "RECONCILE",\n  "LINKED",\n  "BLOCKED"\n] as const;',
        '  "LINKED",\n  "BLOCKED"\n] as const;',
        "gap 13's defect at a new seam: `webhooks.py` sets RECONCILE, so the "
        "merchant reads generic copy over a row whose supplier order is being "
        "re-checked",
    ),
    (
        "the copy table is widened back to Record<string, …>",
        API,
        "export const SUPPLIER_ORDER_STATE_COPY: Record<SupplierOrderState, string> = {",
        "export const SUPPLIER_ORDER_STATE_COPY: Record<string, string> = {",
        "the compile error that catches a state added without copy is gone, so "
        "the table can silently fall behind the union -- which is exactly how "
        "three publish codes came to have no copy",
    ),
    (
        "an unrecognised state falls through to nothing",
        API,
        'export function supplierOrderStateCopy(state: string): string {\n'
        "  return (\n"
        "    SUPPLIER_ORDER_STATE_COPY[state as SupplierOrderState] ||\n"
        '    "Your supplier order is in a state this app does not recognise yet"\n'
        "  );\n"
        "}",
        'export function supplierOrderStateCopy(state: string): string {\n'
        "  return SUPPLIER_ORDER_STATE_COPY[state as SupplierOrderState] || state;\n"
        "}",
        "a server ahead of this build puts a raw identifier on the merchant's "
        "screen -- the gap 13 failure verbatim, moved to a surface where the "
        "identifier sits beside money owed",
    ),
    (
        "whether the order was placed is re-derived on the client",
        API,
        "    supplierOrderPlaced: raw.supplier_order_placed === true,",
        "    supplierOrderPlaced: intentId !== null,",
        "INVERTED. Behaviour-preserving over every payload the server can "
        "produce, because the server's rule *is* `intent_id is not None`. "
        "Catching it would need a fixture with an intent id and "
        "supplier_order_placed false, which the backend cannot emit",
    ),
    # --------------------------------------------- the screen the merchant sees
    (
        "the sandbox promise is hardcoded instead of read from the response",
        SCREEN,
        "            {isSandbox === true ? (",
        "            {true ? (",
        "the most reassuring sentence in the feature -- nothing is sent to your "
        "supplier -- keeps being shown after production fulfilment is switched "
        "on. Nothing in the build changes when that happens, so nothing else "
        "would catch it",
    ),
    (
        "the screen writes its own gap prose beside the mapped entry",
        SCREEN,
        "                body={gap.needs}",
        '                body="This is coming soon."',
        "the enumeration copied twice with prose as the second copy: the screen "
        "can claim a gap has closed while the list says it is open, and can keep "
        "claiming one after it closes",
    ),
    (
        "the sync screen writes its own gap prose too",
        SYNC,
        "                body={gap.needs}",
        '                body="This is coming soon."',
        "the same drift on the second surface that renders the gap list. Fixing "
        "one copy of a defect and leaving the other is how a ledger of "
        "recurring defects gets seventeen entries",
    ),
    (
        "a missing connection is checked after the scope phases",
        SCREEN,
        "    if (!connectionId) setState(\"EMPTY\");\n"
        "    else if (scopeStatus.status.phase === \"ready\") load().catch(() => undefined);",
        "    if (scopeStatus.status.phase === \"ready\") load().catch(() => undefined);\n"
        "    else if (!connectionId) setState(\"EMPTY\");",
        "INVERTED. A ready scope with no connection would call `load`, which "
        "returns without clearing LOADING -- a skeleton for ever. Unreachable "
        "while `useDropshippingScope` starts in `loading` and reaches `ready` "
        "a microtask later, so no test can catch the reordering today",
    ),
    (
        "a failed refresh leaves the previous backlog on screen",
        SCREEN,
        "        setRows([]);\n        setIsSandbox(null);\n        setState(stateForError(error));",
        "        setState(stateForError(error));",
        "error and empty co-render in their most expensive form: a list of money "
        "owed, shown under a failed request, that the merchant may already have "
        "handled -- or that has grown without them being told",
    ),
    # ------------------------------------------------- inverted, and the control
    (
        "the clamped limit local is renamed",
        FULFILLMENT,
        "        capped = min(max(int(limit), 1), 200)",
        "        clamped = min(max(int(limit), 1), 200)",
        "a pure rename is pinned by something reading the source text rather "
        "than the behaviour",
    ),
    (
        "a comment is added above the query",
        FULFILLMENT,
        '        raise FulfillmentError("invalid_limit", 400) from None\n\n    ensure_schema()',
        '        raise FulfillmentError("invalid_limit", 400) from None\n\n'
        "    # no-op control\n    ensure_schema()",
        "the battery is reporting something other than the mutation",
    ),
]

# Behaviour-preserving by construction, each for a reason written beside it and
# in the docstring above.
#
# Keyed by name rather than by position. Inserting a mutation renumbers every
# one after it, and a set of indices would then quietly relabel a real mutation
# as inverted -- which is to say, would stop demanding that anything catch it.
# That is this battery's own version of the defect it exists to find.
INVERTED_NAMES = {
    "the clamped limit local is renamed",
    "whether the order was placed is re-derived on the client",
    "a missing connection is checked after the scope phases",
}
# The rename is inverted only if it is *complete*; a half-done rename would be a
# NameError and would be "caught" for the wrong reason.
RENAME_NAME = "the clamped limit local is renamed"
RENAME_COMPANION = (
    '(connection_id, business_id, store_id, "DROPSHIP", capped)).fetchall()',
    '(connection_id, business_id, store_id, "DROPSHIP", clamped)).fetchall()',
)
NO_OP_NAME = "a comment is added above the query"

_NAMES = [name for name, *_ in MUTATIONS]
assert len(_NAMES) == len(set(_NAMES)), "two mutations share a name"
for _declared in INVERTED_NAMES | {RENAME_NAME, NO_OP_NAME}:
    assert _declared in _NAMES, f"no mutation named {_declared!r}"


def main(argv: list[str]) -> int:
    only: set[int] = set()
    if argv and argv[0] == "--only" and len(argv) > 1:
        only = {int(part) for part in argv[1].split(",") if part.strip()}

    survivors: list[tuple[str, str]] = []
    control_failed = False

    for index, (name, path, old, new, meaning) in enumerate(MUTATIONS, 1):
        if only and index not in only:
            continue
        original = path.read_text()
        if original.count(old) != 1:
            print(f"{index:2}. ERROR    {name}\n        anchor matched "
                  f"{original.count(old)} times in {path.name}")
            survivors.append((name, "anchor did not match exactly once"))
            continue
        mutated = original.replace(old, new, 1)
        if name == RENAME_NAME:
            before, after = RENAME_COMPANION
            if mutated.count(before) != 1:
                print(f"{index:2}. ERROR    {name}\n        rename companion anchor missing")
                survivors.append((name, "rename companion anchor missing"))
                continue
            mutated = mutated.replace(before, after, 1)
        path.write_text(mutated)
        try:
            caught_by = first_failing()
        finally:
            path.write_text(original)

        caught = caught_by is not None

        if name == NO_OP_NAME:
            if caught:
                print(f"{index:2}. CONTROL FAILED {name} (reported by {caught_by})")
                control_failed = True
            else:
                print(f"{index:2}. control  {name} (no-op, correctly survived)")
            continue

        if name in INVERTED_NAMES:
            if caught:
                print(f"{index:2}. PINNED   {name} (by {caught_by})")
                survivors.append((name, meaning))
            else:
                print(f"{index:2}. survived {name} (inverted, correctly ignored)")
            continue

        if caught:
            print(f"{index:2}. caught   {name}  [{caught_by}]")
        else:
            print(f"{index:2}. SURVIVED {name}")
            survivors.append((name, meaning))

    ran = only or set(range(1, len(MUTATIONS) + 1))
    ran_names = {_NAMES[index - 1] for index in ran}
    real = len(ran_names - INVERTED_NAMES - {NO_OP_NAME})
    ran_inverted = len(ran_names & INVERTED_NAMES)
    print()
    if only:
        print(f"Filtered run: {sorted(only)} of {len(MUTATIONS)}. Not a full battery.")
    if control_failed:
        print("The no-op control was reported as caught. The battery is measuring")
        print("something other than the mutation; nothing above can be trusted.")
        return 1
    if survivors:
        print(f"{len(survivors)} of {real} mutations gave the wrong answer:")
        for name, meaning in survivors:
            print(f"  - {name}\n      would mean: {meaning}")
        return 1
    print(f"All {real} real mutations caught, "
          f"{ran_inverted} inverted mutations correctly ignored, "
          f"{'no-op control survived' if NO_OP_NAME in ran_names else 'control not run'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
