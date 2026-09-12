#!/usr/bin/env python3
"""Mutation battery for whether a never-sent supplier order can be ordered again (gap 18).

What this is aimed at
---------------------
Gap 17 made the drain observable. Measuring what a merchant can *do* about an
order the drain refused to send found the most expensive defect in this mission
so far, and it was one line:

*   "ALREADY PLACED" WAS DERIVED FROM A ROW EXISTING, NOT FROM A SEND.
    `list_obligations` read `if intent_id is not None` and appended
    `SUPPLIER_ORDER_ALREADY_PLACED` -- "You have already ordered this from your
    supplier". `BLOCKED` is what `dispatch` settles to when it *refuses* to
    send: no `provider_order_id`, never through `SENDING`. And `claim` selects
    only ``state IN ('READY','UNKNOWN','RECONCILE')``, so `BLOCKED` is terminal.
    A buyer had paid, nothing had been ordered, every surface said it was
    handled, and because nothing would ever pick the row up again it said so
    forever.

*   THE RETRY PATH WAS WORSE THAN THE REFUSAL, AND ONLY MEASUREMENT FOUND IT.
    Retrying with a fresh key gave `immutable_intent_conflict` -- a refusal,
    wrong but visible. Retrying with the *original* idempotency key returned
    ``{"duplicate": True}`` pointing at the dead intent. `duplicate: True`
    means "the order your request asked for exists", so the retry was a
    *success that placed nothing*: strictly the more expensive of the two
    answers, and the one a well-behaved caller retrying its own request would
    hit first.

The fix is a supersession seam: an intent proven never sent and past retrying
can be retired, and a fresh intent takes its order. Proven, not assumed --
`NEVER_SENT_STATES` contains only the two states `dispatch` can reach without
having written, and `RECOVERABLE_STATES` is narrower still, because `READY` is
never-sent but *live* and offering a retry on it would queue the same purchase
twice. Everything else fails closed.

What the mutations attack
-------------------------
Five groups, because the seam is a chain and every link is invisible from the
others:

*   the vocabulary -- that the two tuples mean what their names say, and that
    the predicate reads the evidence rather than the row's existence;
*   the obligation surface -- the original defect, in both of the places that
    derived the same claim, plus the JOIN that keeps a retired attempt from
    describing a live order;
*   the retirement gate in `create_intent` -- that a spent key still cannot
    mint a second order, that the evidence is re-checked inside the write and
    not only in the read, and that a lost race refuses;
*   the migration -- that the new shape reaches databases that already exist.
    `CREATE TABLE IF NOT EXISTS` is a no-op on every one of them and the suite
    runs on a fresh SQLite file every time, so a DDL edit alone proves a
    recovery path production does not have. This mission's recurring defect
    with a schema in the subject position;
*   the screen -- that the client's own count is taken off the server's verdict
    and not re-derived from an id being present, which is the same defect one
    language over.

Two mutations are listed as INVERTED: edits that *should* leave the suite green
because they change nothing a test is entitled to pin. As gap 17's battery
records, that classification is a claim about what the suite currently proves,
so it expires -- if an inverted mutation starts failing, the right response is
usually to reclassify it rather than to loosen the test.

Four checks, cheapest-first, stopping at the first failure so the report names
*which* defence caught each mutation.

    .venv/bin/python3 scripts/mutation_dropship_recovery_seam.py
    .venv/bin/python3 scripts/mutation_dropship_recovery_seam.py --only 3,7
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

FULFILLMENT = ROOT / "services/business_os/suppliers/fulfillment.py"
SCREEN = NATIVE / "src/screens/dropshipping/DropshippingOrdersScreen.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _obligations() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligations.py")


def _fulfillment() -> subprocess.CompletedProcess:
    return _pytest("tests/business_os/test_cj_fulfillment.py")


def _jest() -> subprocess.CompletedProcess:
    return subprocess.run(
        ["npx", "jest", "--runTestsByPath",
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
    ("obligations", _obligations),
    ("fulfillment", _fulfillment),
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
    # ----------------------------------------------------------- the vocabulary
    (
        "an unconfirmed send counts as proof of no send",
        FULFILLMENT,
        'NEVER_SENT_STATES = ("READY", "BLOCKED")',
        'NEVER_SENT_STATES = ("READY", "BLOCKED", "UNKNOWN")',
        "the one state whose own reason string is `absence_not_proven` treated "
        "as proof of absence. A supplier order that did go out, then went "
        "unconfirmed, becomes eligible for a second purchase of the same goods. "
        "This is the most expensive mutation in the file",
    ),
    (
        "a queued order is offered a retry",
        FULFILLMENT,
        'RECOVERABLE_STATES = ("BLOCKED",)',
        'RECOVERABLE_STATES = ("BLOCKED", "READY")',
        "never-sent read as sufficient when it is only necessary. `READY` is "
        "live -- `claim` will pick it up the moment a drain exists -- so "
        "retiring it and minting a replacement queues the same purchase twice",
    ),
    (
        "the predicate stops looking at the provider order id",
        FULFILLMENT,
        "    return state in RECOVERABLE_STATES and not provider_order_id",
        "    return state in RECOVERABLE_STATES",
        "`settle` writes the id with COALESCE and never clears it, so a row "
        "holding one has a real provider order behind it. Dropping the check "
        "lets the state win over the more expensive fact",
    ),
    (
        "the predicate stops looking at the state",
        FULFILLMENT,
        "    return state in RECOVERABLE_STATES and not provider_order_id",
        "    return not provider_order_id",
        "an intent with no outbox row at all -- which cannot testify either way "
        "-- reads as recoverable, as does one mid-SENDING. Fails open on "
        "exactly the rows the tuple exists to exclude",
    ),
    (
        "everything is recoverable",
        FULFILLMENT,
        "    return state in RECOVERABLE_STATES and not provider_order_id",
        "    return True",
        "the gate removed. Every order in the system can be bought a second "
        "time, including the ones already confirmed LINKED",
    ),
    # --------------------------------------------------- the obligation surface
    (
        "the already-placed blocker goes back to reading a row's existence",
        FULFILLMENT,
        "        if intent_id is not None and not _recoverable_intent(outbox_state, item.get(\"provider_order_id\")):",
        "        if intent_id is not None:",
        "gap 18's defect restored verbatim: a paid buyer, no supplier order, and "
        "a merchant told they have already ordered it -- permanently, because "
        "BLOCKED is terminal",
    ),
    (
        "the placed flag goes back to reading a row's existence",
        FULFILLMENT,
        '            "supplier_order_placed": intent_id is not None and not _recoverable_intent(\n'
        '                outbox_state, item.get("provider_order_id")),',
        '            "supplier_order_placed": intent_id is not None,',
        "the same wrong fact by the second of its two derivations. Worse than "
        "the blocker alone, because the payload now contradicts itself and the "
        "screen renders both halves",
    ),
    (
        "a retired attempt still describes the live order",
        FULFILLMENT,
        '            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "\n'
        '            "  AND i.superseded_at IS NULL "',
        '            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "',
        "an audit row about an attempt proven never sent is joined as though it "
        "described the order's present situation, so its dead state and dead "
        "error resurface on the obligation -- and after a second attempt the "
        "JOIN fans out",
    ),
    (
        "the liveness filter moves from the JOIN into the WHERE",
        FULFILLMENT,
        '            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "\n'
        '            "  AND i.superseded_at IS NULL "',
        '            "LEFT JOIN business_os_supplier_intents i ON i.order_id = CAST(o.id AS TEXT) "\n'
        '            "  AND (i.superseded_at IS NULL OR 1=0) "',
        "INVERTED. Written as an anchor for the WHERE-clause hazard the comment "
        "there warns about; the tautology keeps the semantics, so this is a "
        "no-op that documents the shape rather than a real mutation. The actual "
        "WHERE spelling cannot be produced by a single string swap here, and it "
        "is covered by the mutation above plus the empty-list assertions",
    ),
    # ------------------------------------------------------ the retirement gate
    (
        "replaying a spent key is called a duplicate again",
        FULFILLMENT,
        '                raise FulfillmentError("intent_superseded_use_new_key", 409)',
        "                pass",
        "the sharper half of gap 18 restored: the retry returns "
        "`duplicate: True` pointing at a dead intent, so a caller retrying its "
        "own request is told the order exists when nothing was ever sent. A "
        "success that places nothing is worse than a refusal",
    ),
    (
        "a spent key is allowed to mint a second order",
        FULFILLMENT,
        '        replayed = conn.execute("SELECT * FROM business_os_supplier_intents "\n'
        '                                "WHERE connection_id=? AND idempotency_key=?",',
        '        replayed = conn.execute("SELECT * FROM business_os_supplier_intents "\n'
        '                                "WHERE connection_id=? AND idempotency_key=? '
        'AND superseded_at IS NULL",',
        "the idempotency guarantee narrowed to live rows. A key is the caller's "
        "promise that this is the same request; a spent one minting a second "
        "supplier order is the double purchase the outbox exists to prevent",
    ),
    (
        "recovery is made impossible again",
        FULFILLMENT,
        '                            "WHERE i.connection_id=? AND i.order_id=? AND i.superseded_at IS NULL",',
        '                            "WHERE i.connection_id=? AND i.order_id=?",',
        "the retired attempt keeps holding its order, so the replacement is "
        "refused with `immutable_intent_conflict` and the seam is decorative",
    ),
    (
        "the write trusts the verdict the read produced",
        FULFILLMENT,
        '                "AND superseded_at IS NULL AND EXISTS (SELECT 1 FROM business_os_supplier_outbox o "\n'
        '                f"WHERE o.intent_id=business_os_supplier_intents.id AND o.state IN ({placeholders}) "\n'
        '                "AND o.provider_order_id IS NULL)",\n'
        "                (time.time(), live[\"id\"], *RECOVERABLE_STATES))",
        '                "AND superseded_at IS NULL",\n'
        "                (time.time(), live[\"id\"]))",
        "the guard collapses to the Python read. A dispatch that moved the row "
        "out of BLOCKED between the SELECT and the UPDATE loses nothing -- the "
        "retirement proceeds anyway and the in-flight order is bought twice",
    ),
    (
        "the write stops re-checking the provider order id",
        FULFILLMENT,
        '                "AND o.provider_order_id IS NULL)",',
        '                ")",',
        "half the evidence dropped from the statement that acts on it, leaving "
        "the id check only in the Python that has already gone stale",
    ),
    (
        "an already-retired intent can be retired again",
        FULFILLMENT,
        '                "UPDATE business_os_supplier_intents SET superseded_at=? WHERE id=? "\n'
        '                "AND superseded_at IS NULL AND EXISTS',
        '                "UPDATE business_os_supplier_intents SET superseded_at=? WHERE id=? "\n'
        '                "AND EXISTS',
        "two concurrent recoveries both believe they won, so one order acquires "
        "two live replacements and the canonical-order invariant is decided by "
        "the index rather than by the code",
    ),
    (
        "the lost race is not noticed",
        FULFILLMENT,
        "            if retired.rowcount != 1:\n"
        '                raise FulfillmentError("immutable_intent_conflict")',
        "            pass",
        "the conditional UPDATE still refuses, but nobody reads the answer: the "
        "replacement intent is created beside an intent that was never retired, "
        "which is the double purchase arriving through the back door",
    ),
    # ------------------------------------------------------------ the migration
    (
        "the reshape never runs",
        FULFILLMENT,
        "            _reshape_intents_for_supersession(conn)",
        "            pass",
        "the migration-invisibility hazard in full. `CREATE TABLE IF NOT "
        "EXISTS` is a no-op on every database that has run this code, so the "
        "whole seam exists only on fresh SQLite -- i.e. only in the test suite. "
        "Green tests, unmigrated production",
    ),
    (
        "the blocking index is left in place",
        FULFILLMENT,
        '        conn.execute("DROP INDEX IF EXISTS uq_supplier_canonical_order")',
        "        pass",
        "`uq_supplier_canonical_order` is unconditional on `order_id`, so the "
        "replacement intent collides with the retired one it replaces and every "
        "recovery fails on an existing database",
    ),
    (
        "the replacement index is unconditional too",
        FULFILLMENT,
        '                     "ON business_os_supplier_intents(order_id) WHERE superseded_at IS NULL")',
        '                     "ON business_os_supplier_intents(order_id)")',
        "the old constraint reintroduced under a new name. Nothing is gained by "
        "the reshape and the failure is subtler, because the name now suggests "
        "it is conditional",
    ),
    (
        "the canonical-order invariant is dropped rather than narrowed",
        FULFILLMENT,
        '        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_live_canonical_order "\n'
        '                     "ON business_os_supplier_intents(order_id) WHERE superseded_at IS NULL")',
        "        pass",
        "one customer order can hold two *live* intents, so the LEFT JOIN in "
        "`list_obligations` fans out and the merchant's screen shows one order "
        "twice with two different supplier states",
    ),
    (
        "existing rows are retired by the migration",
        FULFILLMENT,
        '                     "ADD COLUMN superseded_at DOUBLE PRECISION")',
        '                     "ADD COLUMN superseded_at DOUBLE PRECISION DEFAULT 0")',
        "a default makes 'still live' and 'retired at the epoch' the same row, "
        "so the reshape silently frees every order in the table for a second "
        "supplier purchase. The nullable column is the whole point",
    ),
    (
        "SQLite is left with the old inline uniqueness",
        FULFILLMENT,
        "        _drop_sqlite_order_uniqueness(conn)",
        "        pass",
        "an inline UNIQUE cannot be dropped by name, so without the rebuild "
        "every existing SQLite database -- dev and CI-adjacent -- refuses "
        "recovery while PostgreSQL allows it. Divergent behaviour by engine is "
        "the hardest kind of bug to reproduce",
    ),
    # --------------------------------------------------------------- the screen
    (
        "the client re-derives placed from an id being present",
        SCREEN,
        "  const awaiting = rows.filter((row) => !row.supplierOrderPlaced).length;",
        "  const awaiting = rows.filter((row) => !row.intentId).length;",
        "gap 18's defect one language over, and immune to every backend test "
        "here: the server correctly reports `supplierOrderPlaced: false` and "
        "the screen still omits the order from the count of orders needing one",
    ),
    (
        "the blocked count swallows the recoverable order",
        SCREEN,
        "  const blocked = rows.filter((row) => !row.supplierOrderPlaced && !row.canPlaceSupplierOrder).length;",
        "  const blocked = rows.filter((row) => !row.supplierOrderPlaced).length;",
        "an order that failed once but is orderable again is reported as one "
        "that 'could not be ordered as things stand', sending the merchant to "
        "look for a reason that is no longer there",
    ),
    # ------------------------------------------------------------- INVERTED (1)
    (
        "the refusal's HTTP status changes",
        FULFILLMENT,
        '                raise FulfillmentError("intent_superseded_use_new_key", 409)',
        '                raise FulfillmentError("intent_superseded_use_new_key")',
        "INVERTED, and honestly so rather than comfortably. The code string is "
        "pinned; the status is not, because no surface calls "
        "`fulfillment-intents` yet -- there is no 'place supplier order' button "
        "in any client. When one is built, 409 becomes a fact a caller reads "
        "and this mutation should be reclassified rather than the test loosened",
    ),
    # --------------------------------------------------------------- no-op (1)
    (
        "a comment is reworded",
        FULFILLMENT,
        "#: The single state from which a merchant may be offered a fresh attempt:",
        "#: The one state from which a merchant may be offered another attempt:",
        "CONTROL. A surviving no-op proves the battery is measuring the tests "
        "rather than measuring whether any edit at all breaks the build",
    ),
]

#: Mutations expected to survive. Keyed by name rather than index so inserting a
#: mutation above cannot silently reclassify one of these.
#:
#: As gap 16's battery learned, membership here is a claim about what the suite
#: currently proves, not a property of the edit -- it expires. One of its entries
#: had to be moved *out* mid-session when a newly written test began (correctly)
#: pinning it.
INVERTED_NAMES = {
    "the liveness filter moves from the JOIN into the WHERE",
    "the refusal's HTTP status changes",
}
CONTROL_NAMES = {"a comment is reworded"}

#: Mutations whose anchor is half of a pair. Applying the companion keeps the
#: mutation honest: it tests whether the *invariant* is pinned, not whether a
#: half-edit crashes.
COMPANIONS: dict[str, list[tuple[Path, str, str]]] = {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="comma-separated 1-based mutation numbers")
    args = parser.parse_args(argv)

    selected = list(enumerate(MUTATIONS, 1))
    if args.only:
        wanted = {int(part) for part in args.only.split(",")}
        selected = [item for item in selected if item[0] in wanted]

    baseline = first_failing()
    if baseline is not None:
        print(f"REFUSING TO RUN: the unmutated tree already fails {baseline!r}. "
              "A battery on a red tree measures nothing.")
        return 2

    print(f"baseline green; {len(selected)} mutation(s)\n")
    caught, survived, ignored = 0, [], 0

    for number, (name, path, old, new, meaning) in selected:
        edits = [(path, old, new)] + COMPANIONS.get(name, [])
        originals = {}
        try:
            for target, before, after in edits:
                source = originals.setdefault(target, target.read_text(encoding="utf-8"))
                current = target.read_text(encoding="utf-8")
                if before not in current:
                    print(f"{number:2}. SKIPPED  {name}\n    anchor not found in "
                          f"{target.name}; the code moved and this mutation needs "
                          "rewriting\n")
                    break
                target.write_text(current.replace(before, after, 1), encoding="utf-8")
            else:
                label = first_failing()
                expected_survivor = name in INVERTED_NAMES or name in CONTROL_NAMES
                if label is None and expected_survivor:
                    kind = "CONTROL" if name in CONTROL_NAMES else "INVERTED"
                    print(f"{number:2}. {kind}   {name}\n    survived, as intended\n")
                    ignored += 1
                elif label is None:
                    print(f"{number:2}. SURVIVED {name}\n    {meaning}\n")
                    survived.append((number, name, meaning))
                elif expected_survivor:
                    print(f"{number:2}. PINNED   {name}\n    caught by {label!r}, but "
                          "it was classified as a survivor. Read the classification "
                          "again before loosening anything: it may simply have "
                          "expired.\n")
                    survived.append((number, name, "misclassified as inverted"))
                else:
                    print(f"{number:2}. caught   {name}  [{label}]")
                    caught += 1
        finally:
            for target, source in originals.items():
                target.write_text(source, encoding="utf-8")

    print()
    if survived:
        print(f"{len(survived)} mutation(s) not caught:")
        for number, name, meaning in survived:
            print(f"  {number:2}. {name}\n      {meaning}")
        return 1
    print(f"All {caught} real mutations caught, {ignored} expected survivor(s) "
          "behaved as classified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
