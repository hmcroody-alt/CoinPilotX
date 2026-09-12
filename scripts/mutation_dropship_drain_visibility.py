#!/usr/bin/env python3
"""Mutation battery for whether anything drains the supplier outbox (gap 17).

What this is aimed at
---------------------
Gap 16 made a blocked supplier order explain itself. Measuring what happens to
an order that is *not* blocked found something larger, and it is not a wording
bug:

*   NOTHING SENDS QUEUED SUPPLIER ORDERS, AND THE SCREEN SAID OTHERWISE.
    `worker.run_once` is the only caller of `fulfillment.claim`/`dispatch`. Its
    only entry point is `supplier_worker.py`, and that is absent from the
    `Procfile`. So every intent this platform has ever created sits at `READY`
    forever, while `SUPPLIER_ORDER_STATE_COPY` renders `READY` as "Queued to
    send to your supplier" -- a promise about a process that does not exist.

*   THE PROMISE WAS UNFALSIFIABLE, WHICH IS WHY IT SURVIVED. `run_once`
    returned its counts to its caller and persisted nothing about itself. "A
    drain ran" was not a fact in the database, so no read path could contradict
    the copy and no test could either. This is the same shape as gap 16's clock:
    not a wrong answer, but a question nothing could ask. A rule nothing can
    execute has never been true or false; a claim nothing can measure has never
    been right or wrong.

The fix is therefore not new wording. It is a latch: `run_once` records the
start and the completion of each tick, `drain_status` reads them, the merchant
payload carries the answer, and the screen says the one sentence that
contradicts the row above it. Every mutation below attacks that chain.

What the mutations attack
-------------------------
Four groups, because the chain is only as good as its weakest link and three of
the links are invisible from the other two:

*   the latch -- that both moments are recorded, in that order, and that a
    deployment which has never drained cannot be confused with one that just
    did;
*   the classification -- that staleness is judged on the completion rather
    than the start (a worker crash-looping keeps `started_at` fresh forever),
    its boundary, and its size;
*   the wire -- that the fact crosses from Python to the envelope to the
    screen, since no compiler spans any of those joins;
*   the copy -- that every unhealthy state says queued orders are not going
    out, that the healthy one stays silent, and that no identifier reaches the
    merchant.

Two mutations are listed as INVERTED: edits that *should* leave the suite green
because they change nothing a test is entitled to pin. As gap 16's battery
records, that classification is a claim about what the suite currently proves,
so it expires -- if an inverted mutation starts failing, the right response is
usually to reclassify it rather than to loosen the test.

Four checks, cheapest-first, stopping at the first failure so the report names
*which* defence caught each mutation.

    .venv/bin/python3 scripts/mutation_dropship_drain_visibility.py
    .venv/bin/python3 scripts/mutation_dropship_drain_visibility.py --only 3,7
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "mobile-native"
PY = ROOT / ".venv/bin/python3"

FULFILLMENT = ROOT / "services/business_os/suppliers/fulfillment.py"
WORKER = ROOT / "services/business_os/suppliers/worker.py"
API = NATIVE / "src/api/dropshipping.ts"
SCREEN = NATIVE / "src/screens/dropshipping/DropshippingOrdersScreen.tsx"


def _pytest(rel: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(PY), "-m", "pytest", rel, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True,
    )


def _copy_pin() -> subprocess.CompletedProcess:
    return _pytest("tests/dropshipping/test_supplier_obligation_copy.py")


def _worker() -> subprocess.CompletedProcess:
    return _pytest("tests/business_os/test_cj_worker.py")


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
    ("copy-pin", _copy_pin),
    ("worker", _worker),
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
    # ---------------------------------------------------------------- the latch
    (
        "the worker records nothing at all",
        WORKER,
        "    fulfillment.record_drain_tick(now=now)\n",
        "",
        "the original defect restored in full: the drain becomes unobservable "
        "again, every deployment reports NO_DRAIN_HAS_EVER_RUN whatever it is "
        "doing, and the banner fires permanently on healthy platforms",
    ),
    (
        "the worker never records a completion",
        WORKER,
        "    fulfillment.record_drain_tick(now=now, completed=True)\n",
        "",
        "no deployment can ever reach DRAINING, so a working platform tells "
        "every merchant its queue is broken -- and a banner that is always on "
        "is a banner nobody reads",
    ),
    (
        "the start and the completion are recorded at the same moment",
        WORKER,
        "    fulfillment.record_drain_tick(now=now)\n",
        "    fulfillment.record_drain_tick(now=now, completed=True)\n",
        "the two-timestamp design collapsed to one. A worker that dies inside "
        "every tick would report DRAINING forever -- a live process draining "
        "nothing, indefinitely, with no notice. This is the most expensive "
        "mutation in the file",
    ),
    (
        "the completion is recorded before the work",
        WORKER,
        "    fulfillment.record_drain_tick(now=now)\n    _seed_jobs(limit, now)",
        "    fulfillment.record_drain_tick(now=now)\n"
        "    fulfillment.record_drain_tick(now=now, completed=True)\n    _seed_jobs(limit, now)",
        "same failure by a different route: a tick counts as finished before it "
        "has done anything, so crashing and succeeding look identical",
    ),
    (
        "a missing latch row is read as a fresh tick",
        FULFILLMENT,
        '    started = row["started_at"] if row else None',
        "    started = row[\"started_at\"] if row else now",
        "'never drained' becomes 'just drained'. This is what a column default "
        "would have done too, which is why the schema leaves both nullable",
    ),
    # ------------------------------------------------------- the classification
    (
        "staleness is judged on the start instead of the completion",
        FULFILLMENT,
        "    elif now - completed > DRAIN_STALL_SECONDS:",
        "    elif now - started > DRAIN_STALL_SECONDS:",
        "a worker crash-looping refreshes started_at forever, so the worst "
        "failure mode -- running, draining nothing, for days -- is the one that "
        "never raises a notice",
    ),
    (
        "a deployment that has never drained is called healthy",
        FULFILLMENT,
        '        state = "NO_DRAIN_HAS_EVER_RUN"',
        '        state = "DRAINING"',
        "exactly this repo's production state reported as fine, which is the "
        "defect gap 17 exists to close",
    ),
    (
        "a worker that never finishes a tick is called healthy",
        FULFILLMENT,
        '        state = "TICKING_BUT_NOT_COMPLETING"',
        '        state = "DRAINING"',
        "an incident on a deployed worker is reported as a moving queue",
    ),
    (
        "a worker that never finishes a tick is called absent",
        FULFILLMENT,
        '        state = "TICKING_BUT_NOT_COMPLETING"',
        '        state = "NO_DRAIN_HAS_EVER_RUN"',
        "the two states merged the other way: whoever reads the notice goes "
        "looking for a missing process that is in fact running and broken",
    ),
    (
        "the stall window is widened tenfold",
        FULFILLMENT,
        "DRAIN_STALL_SECONDS = 7200",
        "DRAIN_STALL_SECONDS = 72000",
        "a queue can go twenty hours undrained while every row still promises a "
        "send. Caught only by an absolute pin -- every other test here is "
        "written relative to this constant and moves with it",
    ),
    (
        "the stall window is closed entirely",
        FULFILLMENT,
        "DRAIN_STALL_SECONDS = 7200",
        "DRAIN_STALL_SECONDS = 0",
        "every healthy deployment is reported as stalled the instant its tick "
        "finishes",
    ),
    (
        "the stall boundary excludes the boundary itself",
        FULFILLMENT,
        "    elif now - completed > DRAIN_STALL_SECONDS:",
        "    elif now - completed >= DRAIN_STALL_SECONDS:",
        "an off-by-one at the threshold. Small, but it is the kind of edit a "
        "test suite should not be indifferent to, since the whole constant is a "
        "claim about a boundary",
    ),
    # ----------------------------------------------------------------- the wire
    (
        "the drain is dropped from the merchant envelope",
        FULFILLMENT,
        '            "drain": drain_status()}',
        "            }",
        "the server measures the drain and tells nobody. Mobile falls back to "
        "silence, which on screen is indistinguishable from healthy",
    ),
    (
        "mobile stops reading the drain the server reports",
        API,
        "    drainState: drain ? textOrNull(drain.state) : null",
        "    drainState: null",
        "the same silence from the other end, and the join no compiler spans",
    ),
    (
        "the screen stops rendering the notice",
        SCREEN,
        "            {supplierDrainNotice(drainState) ? (",
        "            {false ? (",
        "every link in the chain works and the merchant still reads a promise "
        "nothing will keep",
    ),
    (
        "a failed request leaves the last drain answer on screen",
        SCREEN,
        "        setDrainState(null);\n        setState(stateForError(error));",
        "        setState(stateForError(error));",
        "error-and-stale-data: the banner from a previous load survives a failed "
        "refresh, so a merchant reads a drain verdict that is no longer sourced",
    ),
    # ----------------------------------------------------------------- the copy
    (
        "a healthy drain also raises the banner",
        API,
        "  DRAINING: null,",
        '  DRAINING: "Queued orders are not being sent",',
        "a banner on every screen forever, which trains merchants to ignore the "
        "one case where it is the only true thing on the page",
    ),
    (
        "the state this deployment is in says nothing",
        API,
        '  NO_DRAIN_HAS_EVER_RUN:\n    "Supplier ordering is not running on this account yet, '
        'so queued orders are not being sent. Contact support before promising a dispatch date",',
        "  NO_DRAIN_HAS_EVER_RUN: null,",
        "the defect restored precisely: the one state production is actually in "
        "is the one with no answer",
    ),
    (
        "the notice stops saying orders are not going out",
        API,
        '    "Supplier ordering is failing on this account, so queued orders are not being sent. Contact support",',
        '    "Supplier ordering is having some trouble on this account. Contact support",',
        "reassuring drift. The row above still says 'Queued to send to your "
        "supplier' and now nothing contradicts it, so the merchant waits",
    ),
    (
        "an unrecognised drain state is rendered as its identifier",
        API,
        "  return SUPPLIER_DRAIN_NOTICE[state as SupplierDrainState] ?? null;",
        "  return SUPPLIER_DRAIN_NOTICE[state as SupplierDrainState] ?? state;",
        "NO_DRAIN_HAS_EVER_RUN on a merchant's screen -- the same class of "
        "defect as the preflight_blocked that gap 16 removed, reintroduced one "
        "gap later in a new place",
    ),
    (
        "mobile forgets a state the backend can report",
        API,
        '  "TICKING_BUT_NOT_COMPLETING",\n',
        "",
        "the cross-language drift this whole test file exists for. Caught by "
        "tsc as well as the pin, which is the point of the total Record",
    ),
    # ------------------------------------------------------------- INVERTED (2)
    (
        "the latch's single row is keyed by a different word",
        FULFILLMENT,
        '            ("worker", now, now))',
        '            ("drain", now, now))',
        "INVERTED. `scope` is a private single-row latch key, written and read "
        "in one module. Nothing outside chooses it and no merchant sees it, so "
        "a test that pinned the string would be pinning an implementation "
        "detail. NOTE: the read in `drain_status` filters on 'worker', so this "
        "mutation alone breaks the pair -- it is listed with its companion "
        "below for that reason",
    ),
    (
        "a notice's wording changes without changing its meaning",
        API,
        '    "Queued orders have not been sent for some time. Contact support before promising a dispatch date"',
        '    "Your queued orders have not been sent for some time. Contact support before you promise a dispatch date"',
        "INVERTED. Still names the same fact, still avoids promising a time, "
        "still contains 'have not been sent'. Copy has to stay editable; a test "
        "that pins exact prose makes every wording improvement a test failure "
        "and teaches the next person to delete the test",
    ),
    # --------------------------------------------------------------- no-op (1)
    (
        "a comment is reworded",
        FULFILLMENT,
        "#: What is known about the process that turns a queued supplier order into a",
        "#: What this deployment knows about the process that turns a queued order into a",
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
    "the latch's single row is keyed by a different word",
    "a notice's wording changes without changing its meaning",
}
CONTROL_NAMES = {"a comment is reworded"}

#: `scope` is written in one place and read in another, so mutating only the
#: writer breaks a pair that no single edit was meant to split. Applying the
#: companion keeps the mutation honest: it tests whether the *name* is pinned,
#: not whether a half-rename crashes.
COMPANIONS = {
    "the latch's single row is keyed by a different word": [
        (FULFILLMENT, "WHERE scope='worker'", "WHERE scope='drain'"),
    ],
}


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
