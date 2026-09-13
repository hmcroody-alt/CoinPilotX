#!/usr/bin/env python3
"""Anti-vacuity battery for the transaction-time supplier gate. §22, §38-§43.

``tests/marketplace/test_supplier_checkout_gate.py`` went green on its second run.
That is when a suite deserves the least trust, and this one guards a decision made
microseconds before a buyer is charged — the cost of a vacuous test here is either
money taken for something nobody can ship, or a store-wide refusal of every
drop-shipped checkout. So each invariant is broken on purpose and the suite is
required to notice.

Why these particular mutations
------------------------------
The gate fails in two opposite directions and both are one line wide.

**Too permissive.** Every refusal it can make is a refusal it can be talked out
of: read UNKNOWN stock as buyable, count an archived variant as orderable, take
``any`` orderable variant instead of ``all`` sold out, trust a timestamp without
checking whether the last read actually succeeded, trust a timestamp from the
future. Each of those looks like a tidy-up and each one silently reopens the
window §22 exists to close.

**Too strict.** This is the direction that bites hardest in this deployment,
because ``supplier_worker`` is not in the Procfile and ``link_source`` never writes
``last_synced_at`` — so *every* drop-shipped listing is unconfirmed and always will
be until the reconciler ships. A gate that demands freshness regardless takes the
whole catalogue off sale and calls it safety. The strictness is therefore derived
from the drain latch, and the latch mutations are the ones that matter most:
dropping ``DRAIN_STALLED`` (a worker that ran once and died reads as healthy
forever), or widening ``RUNNING_STATES`` to include the states that mean nothing is
re-reading. The module's first draft actually had the first of those bugs, which is
the best available evidence that the mutation is plausible rather than invented.

Then the fail-open group. ``reconciliation_evidence`` swallows every exception by
design, because a deployment that never initialised the supplier subsystem has
legitimately never drained. The price of that tolerance is that a renamed column
reads the same way, so the suite has to pin the names structurally; the mutation
here renames one and requires that to be caught.

A mutation that survives is reported as a hole in the tests, not as a pass. An
anchor that cannot be applied is reported as an error in *this* script — never as a
silent skip, because a mutation that quietly fails to apply is indistinguishable
from one the suite caught.

Usage::

    .venv/bin/python3 scripts/marketplace/supplier_checkout_gate_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay
directory of symlinks, with the one mutated file materialised as a real copy.
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

SUITE = "tests/marketplace/test_supplier_checkout_gate.py"

GATE = "services/marketplace_supplier_checkout.py"
FULFILLMENT = "services/business_os/suppliers/fulfillment.py"
WORKER = "services/business_os/suppliers/worker.py"

REAL_DIRS = {"services", "services/business_os", "services/business_os/suppliers",
             "tests", "tests/marketplace", "scripts", "scripts/marketplace"}


#: (name, file, old, new). ``old`` must appear exactly once in the file.
PERMISSIVE_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "unknown stock is read as sold out (refuses the whole catalogue)",
        GATE,
        "    if states and all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):",
        "    if states and all(state != supplier_schema.STOCK_IN_STOCK for state in states):",
    ),
    (
        "one sold-out variant sells out the whole listing",
        GATE,
        "    if states and all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):",
        "    if states and any(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):",
    ),
    (
        "a listing with no variants yet is reported sold out",
        GATE,
        "    if states and all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):",
        "    if all(state == supplier_schema.STOCK_OUT_OF_STOCK for state in states):",
    ),
    (
        "an archived variant still counts as orderable",
        GATE,
        "    return [dict(row) for row in rows\n"
        "            if str(row.get(\"status\") or \"active\").strip().lower() == \"active\"]",
        "    return [dict(row) for row in rows]",
    ),
    (
        "a sell-out only refuses when the reconciler happens to be running",
        GATE,
        "    rows = _orderable(variants.variants_for(cur, int(source[\"listing_id\"])))",
        "    if not bool((evidence or {}).get(\"running\")):\n"
        "        return _allow(unverified=True, evidence_state=_state_of(evidence))\n"
        "    rows = _orderable(variants.variants_for(cur, int(source[\"listing_id\"])))",
    ),
    (
        "a recent read that failed is trusted anyway",
        GATE,
        "    if sync_state in (supplier_schema.SYNC_STALE, supplier_schema.SYNC_ERROR,\n"
        "                      supplier_schema.SYNC_DISCONNECTED, supplier_schema.SYNC_REMOVED):",
        "    if False:",
    ),
    (
        "a confirmation dated in the future reads as fresh",
        GATE,
        "    if age is None or age > CONFIRMATION_MAX_AGE_SECONDS or age < -CLOCK_SKEW_TOLERANCE_SECONDS:",
        "    if age is None or age > CONFIRMATION_MAX_AGE_SECONDS:",
    ),
    (
        "a missing confirmation is treated as a fresh one",
        GATE,
        "    if age is None or age > CONFIRMATION_MAX_AGE_SECONDS",
        "    if age is not None and age > CONFIRMATION_MAX_AGE_SECONDS",
    ),
    (
        "a supplier-less listing is reported as checked and satisfied",
        GATE,
        "def _not_applicable(reason: str) -> dict:\n    return {**_base(NOT_APPLICABLE), \"reason\": reason}",
        "def _not_applicable(reason: str) -> dict:\n    return {**_base(DECISION_ALLOW), \"reason\": reason}",
    ),
    (
        # Deliberately mutated in `_age_seconds` rather than in `_parse`. The
        # obvious version — have `_parse` fall back to `datetime.now()` — is
        # defeated by the clock-skew guard instead of by the suite's intent, and
        # only on days when the real clock is far enough from the frozen `NOW` to
        # trip it. A mutation whose fate depends on the wall clock proves nothing.
        "an unreadable timestamp is treated as a perfectly fresh one",
        GATE,
        "    parsed = _parse(stamp)\n    if parsed is None:\n        return None",
        "    parsed = _parse(stamp)\n    if parsed is None:\n        return 0.0",
    ),
]

STRICTNESS_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "a worker that ran once and died reads as healthy forever",
        GATE,
        "    elif _epoch(now) - float(completed) > DRAIN_STALL_SECONDS:",
        "    elif False:",
    ),
    (
        "a stalled reconciler is treated as one that is still re-reading",
        GATE,
        "RUNNING_STATES = (\"DRAINING\",)",
        "RUNNING_STATES = (\"DRAINING\", \"DRAIN_STALLED\")",
    ),
    (
        "a reconciler that has never completed a tick is treated as running",
        GATE,
        "RUNNING_STATES = (\"DRAINING\",)",
        "RUNNING_STATES = (\"DRAINING\", \"TICKING_BUT_NOT_COMPLETING\")",
    ),
    (
        "freshness is demanded of a reconciler that was never deployed",
        GATE,
        "    if not running:\n        # See the module docstring",
        "    if False:\n        # See the module docstring",
    ),
    (
        "the confirmation window stops tracking the reconciler's cadence",
        GATE,
        "CONFIRMATION_MAX_AGE_SECONDS = 2700",
        "CONFIRMATION_MAX_AGE_SECONDS = 27000",
    ),
    (
        "the stall window is restated here instead of imported (two authorities)",
        GATE,
        "from services.business_os.suppliers.fulfillment import DRAIN_STALL_SECONDS",
        "DRAIN_STALL_SECONDS = 7200",
    ),
    (
        "a merchant who stocks their own goods is refused on a supplier's word",
        GATE,
        "    if mode != supplier_schema.MODE_DROPSHIP:",
        "    if False:",
    ),
    (
        "a malformed listing reference turns a checkout into a 500",
        GATE,
        "    except variants.VariantRejected:",
        "    except _NeverRaised:",
    ),
]

FAIL_OPEN_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the latch column is renamed and the gate silently stops checking",
        GATE,
        "        cur.execute(\"SELECT started_at, completed_at FROM \"",
        "        cur.execute(\"SELECT started_at, finished_at FROM \"",
    ),
    (
        "a missing latch table becomes a 500 in checkout",
        GATE,
        "    except Exception:\n        return {\"state\": \"NO_DRAIN_HAS_EVER_RUN\", \"running\": False, \"completed_at\": None}",
        "    except _NeverRaised:\n        return {\"state\": \"NO_DRAIN_HAS_EVER_RUN\", \"running\": False, \"completed_at\": None}",
    ),
    (
        "the audit annotates every sale, burying the unverified ones",
        GATE,
        "    if decision.get(\"decision\") != DECISION_ALLOW or not decision.get(\"unverified\"):\n        return {}",
        "    if False:\n        return {}",
    ),
    (
        "a refusal names the merchant's supplier to the buyer",
        GATE,
        "    REASON_SOLD_OUT: \"This item just went out of stock. You have not been charged.\"",
        "    REASON_SOLD_OUT: \"Our supplier CJ reports this item is out of stock.\"",
    ),
    (
        "an answer stops carrying every key",
        GATE,
        "    return {\"decision\": decision, \"reason\": \"\", \"message\": \"\", \"code\": \"\",\n"
        "            \"unverified\": False, \"evidence_state\": \"\", \"sync_state\": \"\",\n"
        "            \"confirmation_age_seconds\": None}",
        "    return {\"decision\": decision, \"reason\": \"\"}",
    ),
]

#: Mutations to the modules the gate *derives* its numbers from. These prove the
#: derivation is pinned rather than coincidental: if somebody retunes the
#: reconciler's inventory cadence, the gate's window has to move with it or the
#: suite has to say so. Without these, `CONFIRMATION_MAX_AGE_SECONDS` is a magic
#: number with a comment claiming it is derived.
DERIVATION_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "the reconciler's inventory cadence changes and the window does not follow",
        WORKER,
        "\"inventory\": 900",
        "\"inventory\": 300",
    ),
]

#: Mutations that are *supposed* to survive, and are run to prove it.
#:
#: ``DRAIN_STALL_SECONDS`` is imported from ``fulfillment`` rather than restated, so
#: retuning it there moves this gate with it and nothing breaks. A suite going red
#: here would mean the gate had acquired its own second copy of the number — which
#: is the §21 violation the import exists to prevent. So the pass condition is
#: inverted, and stated rather than left as an absence: an "expected survivor" that
#: nobody wrote down is indistinguishable from a hole somebody forgot about.
#:
#: Contrast the cadence mutation above, which is expected to be *caught*.
#: ``CONFIRMATION_MAX_AGE_SECONDS`` is a literal that cannot follow
#: ``CADENCE["inventory"]`` automatically, so the only thing that can keep the two
#: honest is a test asserting the arithmetic. The two entries together are the
#: difference between a number that is derived and a number that is merely
#: documented as derived.
FOLLOWED_MUTATIONS: list[tuple[str, str, str, str]] = [
    (
        "fulfillment retunes the stall window and the gate follows it",
        FULFILLMENT,
        "DRAIN_STALL_SECONDS = 7200",
        "DRAIN_STALL_SECONDS = 60",
    ),
]


def _overlay(root: str, real_dirs: set[str] | None = None) -> str:
    """A symlink mirror of the repo, deep only where we need to write."""
    real_dirs = REAL_DIRS if real_dirs is None else real_dirs
    for base, dirs, files in os.walk(REPO):
        rel = os.path.relpath(base, REPO)
        rel = "" if rel == "." else rel.replace(os.sep, "/")
        if rel and rel not in real_dirs:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs
                   if d not in {".git", "node_modules", "__pycache__", ".venv"}]
        target = os.path.join(root, rel) if rel else root
        os.makedirs(target, exist_ok=True)
        for name in files:
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
        for name in dirs:
            child = f"{rel}/{name}" if rel else name
            if child in real_dirs:
                continue
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                os.symlink(os.path.join(base, name), dst)
    return root


def _apply(root: str, rel_path: str, old, new) -> bool:
    """Materialise one mutated copy of ``rel_path``. False if any anchor misses."""
    edits = old if isinstance(old, list) else [(old, new)]
    source = os.path.join(REPO, rel_path)
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    for anchor, _ in edits:
        if text.count(anchor) != 1:
            return False
    for anchor, replacement in edits:
        text = text.replace(anchor, replacement, 1)
    dst = os.path.join(root, rel_path)
    if os.path.islink(dst):
        os.unlink(dst)
    with open(dst, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    # The suite builds its own in-memory SQLite. An inherited DATABASE_URL would
    # point services.db at PostgreSQL and every mutation would "fail" for the same
    # irrelevant reason, which reads as a clean sweep and proves nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (label, suite, mutations, expect_survival).
GROUPS: list[tuple[str, str, list[tuple], bool]] = [
    ("Refusals it can be talked out of", SUITE, PERMISSIVE_MUTATIONS, False),
    ("Strictness it was never entitled to", SUITE, STRICTNESS_MUTATIONS, False),
    ("Failing open without saying so", SUITE, FAIL_OPEN_MUTATIONS, False),
    ("Numbers that must stay derived", SUITE, DERIVATION_MUTATIONS, False),
    ("Numbers that follow their owner (expected to survive)", SUITE,
     FOLLOWED_MUTATIONS, True),
]


def main() -> int:
    suites = []
    for _, default, mutations, _expect in GROUPS:
        for suite in [default] + [m[4] for m in mutations if len(m) > 4]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="gate_mut_base_"))
    try:
        for suite in suites:
            if _run(base, suite) != 0:
                print(f"  FAIL — {suite} is already red; mutation results "
                      f"judged by it would be noise.")
                return 1
            print(f"  PASS  {suite}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()

    survived: list[str] = []
    wrongly_caught: list[str] = []
    unapplied: list[str] = []
    total = 0
    for label, default, mutations, expect_survival in GROUPS:
        print(f"{label} ({len(mutations)} mutations, judged by {default}):")
        for mutation in mutations:
            name, rel_path, old, new = mutation[:4]
            suite = mutation[4] if len(mutation) > 4 else default
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="gate_mut_"))
            try:
                if not _apply(root, rel_path, old, new):
                    unapplied.append(f"{label}: {name}")
                    print(f"  ERROR     {name} — anchor not found exactly once")
                    continue
                lived = _run(root, suite) == 0
                if expect_survival:
                    # Inverted: the suite going red here would mean the gate had
                    # grown its own copy of a number it is supposed to import.
                    if lived:
                        print(f"  FOLLOWED  {name}")
                    else:
                        wrongly_caught.append(f"{label}: {name}")
                        print(f"  DIVERGED  {name} — the gate no longer follows "
                              f"its owner")
                elif lived:
                    survived.append(f"{label}: {name}")
                    print(f"  SURVIVED  {name}")
                else:
                    print(f"  CAUGHT    {name}")
            finally:
                shutil.rmtree(root, ignore_errors=True)
        print()

    print("=" * 68)
    if unapplied:
        print(f"{len(unapplied)} mutation(s) could not be applied "
              f"— a bug in this script, not a pass:")
        for name in unapplied:
            print(f"  - {name}")
    if survived:
        print(f"{len(survived)} mutation(s) SURVIVED — the suite has a hole there:")
        for name in survived:
            print(f"  - {name}")
    if wrongly_caught:
        print(f"{len(wrongly_caught)} mutation(s) were caught that should have been "
              f"followed — a second authority for a borrowed constant:")
        for name in wrongly_caught:
            print(f"  - {name}")
    if survived or unapplied or wrongly_caught:
        return 1
    print(f"PASS — all {total} mutations behaved as specified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
