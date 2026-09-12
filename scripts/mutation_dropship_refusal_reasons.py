#!/usr/bin/env python3
"""Mutation battery for why a supplier order did not go out (gap 16).

What this is aimed at
---------------------
Gap 15 let a merchant place the supplier order a paid sale already owes.
Measuring what then *happens* to that order -- claim it, dispatch it, read the
outbox and the obligation back -- found four defects, and only the first one
looks like a bug if you read the code:

*   `dispatch` ACCEPTED A CLOCK AND THEN READ THE WALL ONE. The function takes
    `now`; the line comparing the frozen `quoted_at` against it called
    `datetime.now(timezone.utc)`. The snapshot is pinned by `snapshot_hash`, so
    `quoted_at` cannot be edited, and the clock was the only other input -- which
    means the 300-second reapproval rule could be executed only by letting 300
    real seconds pass. Nothing ever did. Both sides of the rule standing between
    a merchant and a price CJ has since changed had never run.

*   A DOZEN CAUSES WERE STORED AS ONE WORD. Every non-retryable preflight
    failure became `preflight_blocked` before it reached the database. At least
    six of them are things a merchant can fix -- re-quote, re-approve a cost,
    re-bind a shop, re-import a product -- and all six arrived indistinguishable
    from the ones nobody can fix.

*   THAT WORD WAS RENDERED TO THE MERCHANT. `DropshippingOrdersScreen` printed
    `row.lastError` verbatim, under a comment calling it "the supplier's own
    refusal text ... the words their supplier used". Neither half is true: no
    provider string can reach that column (`suppliers/errors.py` is the module
    that guarantees it) and the value is an identifier from a Python file. And
    the jest test covering the line was named "shows a supplier's refusal in the
    supplier's own words" and asserted `getByText("preflight_blocked")` -- the
    false belief, pinned, green.

*   `BLOCKED` SAID "Your supplier refused this order". Every cause in
    `PREFLIGHT_REASONS` is raised before `_sending`, and the handler turns
    anything already sent into `UNKNOWN` first, so `BLOCKED` means the supplier
    was never contacted and has no opinion. The copy sent merchants to argue
    with their supplier about a message the supplier never sent.

What the mutations attack
-------------------------
Three groups, because a fix that is right in one place and absent in the next
leaves the merchant exactly where they were:

*   the freshness window -- its clock, both of its bounds, and its size;
*   the mapping from internal code to stored reason -- its closedness (a
    provider code must not be able to get in), its keys (a key nothing raises
    is an entry that can never match, which is this repo's recurring defect),
    and its distinctions;
*   the rendering -- that the reason crosses the language boundary, has words,
    and that those words neither leak the identifier nor claim a refusal that
    did not happen.

Five checks, cheapest-first, stopping at the first failure so the report says
*which* defence caught each mutation. The two `tests/dropshipping/` files cannot
share a pytest process -- each binds `DATABASE_URL` at import against its own
temp database -- which is why they are separate checks rather than one.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

FULFILLMENT = ROOT / "services/business_os/suppliers/fulfillment.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/DropshippingOrdersScreen.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _copy_pin() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligation_copy.py")


def _dispatch() -> subprocess.CompletedProcess:
    return _pytest("tests/business_os/test_cj_fulfillment.py")


def _obligations() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligations.py")


def _jest() -> subprocess.CompletedProcess:
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


CHECKS = [
    ("copy-pin", _copy_pin),
    ("dispatch", _dispatch),
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
    # ---------------------------------------------------------------- the clock
    (
        "dispatch reads the wall clock instead of the one it was given",
        FULFILLMENT,
        "        age = (datetime.fromtimestamp(now, timezone.utc) - quoted_at).total_seconds()",
        "        age = (datetime.now(timezone.utc) - quoted_at).total_seconds()",
        "the original defect restored: the freshness rule becomes unexecutable "
        "by any test, because the snapshot is hash-pinned and the clock was the "
        "only remaining input. Nothing would fail -- which is the whole problem",
    ),
    (
        "the window is one-sided",
        FULFILLMENT,
        "        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:",
        "        if age > QUOTE_MAX_AGE_SECONDS:",
        "a worker whose clock is behind sees every quote as future-dated and "
        "spends against all of them, on the strength of two machines disagreeing",
    ),
    (
        "the window accepts only stale quotes",
        FULFILLMENT,
        "        if not 0 <= age <= QUOTE_MAX_AGE_SECONDS:",
        "        if 0 <= age <= QUOTE_MAX_AGE_SECONDS:",
        "the condition inverted -- every fresh quote refused and every stale one "
        "sent. A guard that always fires and one that never fires are the same "
        "defect, and one test proves neither",
    ),
    (
        "the freshness window is widened tenfold",
        FULFILLMENT,
        "QUOTE_MAX_AGE_SECONDS = 300",
        "QUOTE_MAX_AGE_SECONDS = 3000",
        "a merchant can spend against a freight price CJ set fifty minutes ago",
    ),
    (
        "the freshness window is closed entirely",
        FULFILLMENT,
        "QUOTE_MAX_AGE_SECONDS = 300",
        "QUOTE_MAX_AGE_SECONDS = 0",
        "no supplier order can ever be sent -- every quote is stale the instant "
        "it is frozen, and the merchant is told to re-quote forever",
    ),
    # -------------------------------------------------------------- the mapping
    (
        "every refusal is flattened into one word again",
        FULFILLMENT,
        '                      else PREFLIGHT_REASONS.get(str(getattr(exc, "code", "")), "preflight_blocked"))',
        '                      else "preflight_blocked")',
        "the second defect restored: six fixable problems and six unfixable ones "
        "arrive identical, and the merchant is told to contact support about all "
        "twelve",
    ),
    (
        "the exception's own code is stored verbatim",
        FULFILLMENT,
        '                      else PREFLIGHT_REASONS.get(str(getattr(exc, "code", "")), "preflight_blocked"))',
        '                      else str(getattr(exc, "code", "")))',
        "the leak the flattening was there to prevent: a provider-originated "
        "`SupplierError` code becomes persisted state and is rendered to the "
        "merchant. The closed dict is the whole difference between keeping the "
        "cause and copying the provider",
    ),
    (
        "an expired quote and a changed cost become the same message",
        FULFILLMENT,
        '    "supplier_cost_reapproval_required": "supplier_cost_changed",',
        '    "supplier_cost_reapproval_required": "supplier_quote_expired",',
        "a merchant told the quote expired re-quotes, finds the same cost, and "
        "learns nothing -- the number changed and nothing said so",
    ),
    (
        "a mapping key names a code nothing raises",
        FULFILLMENT,
        '    "inventory_quantity_not_verified": "supplier_stock_unconfirmed",',
        '    "inventory_quantity_unverified": "supplier_stock_unconfirmed",',
        "an entry that can never match. The cause silently falls back to "
        "`preflight_blocked` while the map still appears to handle it -- the "
        "unfireable guard, one layer up, and nothing raises so nothing fails",
    ),
    (
        "the declared reason set omits one the code writes",
        FULFILLMENT,
        '    "connection_unavailable",',
        "",
        "`OUTBOX_REASONS` stops being the set the writers write, so mobile is "
        "checked against a list that is missing a value it will really receive "
        "-- and receives it as a raw identifier",
    ),
    (
        "a stored reason is declared but never mapped",
        FULFILLMENT,
        '    "order_not_eligible": "order_no_longer_eligible",',
        "",
        "a sale cancelled after the order was queued is reported as an "
        "unexplained failure rather than as the cancellation it is",
    ),
    (
        "the handler stops asking whether the write already happened",
        FULFILLMENT,
        '        if sent or intent["state"] in {"UNKNOWN", "RECONCILE"} or getattr(exc, "ambiguous_write", False):',
        '        if intent["state"] in {"UNKNOWN", "RECONCILE"} or getattr(exc, "ambiguous_write", False):',
        "a failure on the way back from a successful create is reported as "
        "BLOCKED -- whose copy now says nothing was sent -- and the merchant "
        "orders the same goods a second time",
    ),
    # ------------------------------------------------------------ the rendering
    (
        "the screen prints the identifier again",
        SCREEN,
        "        <Text style={styles.rowWarning}>{supplierOrderReasonCopy(row.lastError)}</Text>",
        "        <Text style={styles.rowWarning}>{row.lastError}</Text>",
        "the third defect restored, and the one a merchant actually saw: "
        "`supplier_quote_expired` on a phone",
    ),
    (
        "BLOCKED blames the supplier again",
        API,
        '  BLOCKED: "Not sent — needs your attention"',
        '  BLOCKED: "Your supplier refused this order"',
        "the fourth defect restored: a merchant is sent to argue with their "
        "supplier about an order the supplier was never told about",
    ),
    (
        "a reason's copy is just the code",
        API,
        '  supplier_stock_unconfirmed: "Your supplier has not confirmed stock for this order",',
        '  supplier_stock_unconfirmed: "supplier_stock_unconfirmed",',
        "the copy map exists and is total and still shows an identifier -- "
        "totality was never the property that mattered",
    ),
    (
        "a reason's copy leaks an identifier into the prose",
        API,
        '  supplier_item_changed: "This product no longer matches what your supplier lists. Import it again",',
        '  supplier_item_changed: "This product failed product_binding_mismatch. Import it again",',
        "an internal code inside a sentence, which is how "
        "`SUPPLIER_VARIANT_UNBOUND` reached a merchant in the first place",
    ),
    (
        "the reason copy stops being total over the reason type",
        API,
        "export const SUPPLIER_ORDER_REASON_COPY: Record<SupplierOrderReason, string> = {",
        "export const SUPPLIER_ORDER_REASON_COPY: Record<string, string> = {",
        "the compile error that catches a reason added without words is gone, "
        "so the next reason ships unexplained and the build stays green",
    ),
    (
        "a reason is dropped from the mobile list",
        API,
        '  "supplier_stock_unconfirmed",\n',
        "",
        "a reason the backend writes that mobile has never heard of, which "
        "reaches the fallback and tells the merchant nothing actionable",
    ),
    (
        "an unconfirmed send reads like a failure",
        API,
        '  awaiting_create_readback: "Sent to your supplier — waiting for them to confirm it",',
        '  awaiting_create_readback: "This could not be sent to your supplier",',
        "the one mistake in this subsystem that costs real money: a merchant "
        "told an unconfirmed order failed places it again",
    ),
    # ----------------------------------------------------- behaviour-preserving
    (
        "the handler's local is renamed",
        FULFILLMENT,
        "        safe_error = (\"readback_required\" if state == \"UNKNOWN\"",
        "        chosen_reason = (\"readback_required\" if state == \"UNKNOWN\"",
        "nothing -- a local rename. Listed so the battery is seen to distinguish "
        "a behaviour change from an edit",
    ),
    (
        "a reason's wording changes without changing its meaning",
        API,
        '  supplier_quote_expired: "The shipping quote expired before this was sent. Get a new quote and approve it",',
        '  supplier_quote_expired: "The shipping quote expired before this was sent. Request a new quote and approve it",',
        "nothing -- the tests match on the part of the sentence that carries the "
        "meaning, not on the whole string. A battery that reported this as "
        "caught would be measuring copy churn, not copy correctness",
    ),
    (
        "create_intent's window is spelled as a literal",
        FULFILLMENT,
        "        if age < 0 or age > QUOTE_MAX_AGE_SECONDS or not freight.is_finite()",
        "        if age < 0 or age > 300 or not freight.is_finite()",
        "the two halves of one window can drift apart. It was written into the "
        "inverted set -- behaviour-preserving today, because the constant is "
        "300 -- and the run that followed reported it PINNED, by a test written "
        "an hour earlier for an unrelated reason. That test asserts both "
        "enforcement sites read the shared constant, so the mutation now "
        "removes a real guarantee and belongs here. Worth recording: a "
        "mutation's classification is a property of the current test suite, not "
        "of the edit, and an inverted set is a claim that expires",
    ),
    (
        "a no-op control",
        FULFILLMENT,
        "#: Why this deployment did not send a supplier order, keyed by the internal",
        "#: Why this deployment did not send a supplier order -- keyed by the internal",
        "the battery is reporting mutations it did not make; nothing else in "
        "this run can be trusted",
    ),
]

_NAMES = [name for name, *_ in MUTATIONS]
assert len(_NAMES) == len(set(_NAMES)), "mutation names must be unique -- they key the sets below"

# Keyed by name, not by index. An index-keyed set silently re-labels every
# mutation after any insertion, and the failure is a real defect reported as
# "inverted, correctly ignored".
#
# `create_intent's window is spelled as a literal` started in here and was moved
# out: see its `meaning` above. Membership is a claim about what the suite
# currently proves, and that claim can be invalidated by a test written for
# something else -- so the battery reporting a mutation as PINNED is not always
# a defect in the mutation.
INVERTED_NAMES = {
    "the handler's local is renamed",
    "a reason's wording changes without changing its meaning",
}
NO_OP_NAME = "a no-op control"

# `safe_error` is named twice: the assignment and the `settle` call. A rename
# that moves only the assignment is a NameError, and a NameError is "caught" for
# the wrong reason -- the battery would report a pure rename as pinned and the
# finding would be its own mutation. Gap 15's battery did exactly that once.
RENAME_NAME = "the handler's local is renamed"
RENAME_COMPANIONS = (
    ("        settle(intent, state, now=now, delay=delay, error=safe_error)",
     "        settle(intent, state, now=now, delay=delay, error=chosen_reason)"),
)


def _bare_safe_error_references(source: str) -> int:
    """How many times `dispatch`'s code still names the old local.

    Counting the name inside the function -- rather than trusting the companion
    list to be complete -- is the difference between measuring that a rename is
    behaviour-preserving and asserting it. Comments are stripped first: they
    discuss the local by name in prose, and prose does not raise `NameError`.
    """
    body = source.split("def dispatch(", 1)[-1].split("\ndef ", 1)[0]
    code = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    return code.count("safe_error")


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
            aborted = False
            for before, after in RENAME_COMPANIONS:
                if mutated.count(before) != 1:
                    print(f"{index:2}. ERROR    {name}\n        rename companion anchor missing")
                    survivors.append((name, "rename companion anchor missing"))
                    aborted = True
                    break
                mutated = mutated.replace(before, after, 1)
            if aborted:
                continue
            leftover = _bare_safe_error_references(mutated)
            if leftover:
                print(f"{index:2}. ERROR    {name}\n        rename is partial: "
                      f"{leftover} reference(s) to `safe_error` survive inside "
                      "`dispatch`, so this would be a NameError rather than a "
                      "behaviour-preserving rename")
                survivors.append((name, "rename is partial"))
                continue
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
    print(f"All {real} real mutations caught, {ran_inverted} inverted mutations "
          "correctly ignored, no-op control survived.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
