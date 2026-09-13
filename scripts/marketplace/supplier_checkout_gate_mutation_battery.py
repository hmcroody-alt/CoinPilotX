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
because ``supplier_worker`` has a Procfile entry but runs dark behind two unset env
flags, and ``link_source`` never writes ``last_synced_at`` — so *every* drop-shipped
listing is unconfirmed and will stay that way until the reconciler is switched on
and works through them. A gate that demands freshness regardless takes the whole
catalogue off sale and calls it safety. The strictness is therefore derived from
the drain latch, and the latch mutations are the ones that matter most: dropping
``DRAIN_STALLED`` (a worker that ran once and died reads as healthy forever), or
widening ``RUNNING_STATES`` to include the states that mean nothing is re-reading.
The module's first draft actually had the first of those bugs, which is the best
available evidence that the mutation is plausible rather than invented.

The module's *second* shipped bug is the reason for the never-confirmed group, and
it is subtler than the first because reading the latch is what hid it. The latch
answers "is anything re-reading"; the gate then demanded evidence that is written
per listing. Those decouple within seconds of the worker starting — completion is
recorded on a tick that claimed no work, while confirmations land twenty jobs at a
time — so the whole catalogue sits in "reconciler up, this listing never confirmed"
for hours, and the gate refused it. Measured: 22 of 22 drop-shipped sources have a
NULL confirmation, all reading ``sync_state='SYNCED'``. The mutations here run in
both directions, because the fix has an over-application as well as a regression:
collapsing ``if not running or confirmation == CONFIRMATION_NEVER`` back to
``if not running`` restores the bug, and widening it to swallow the stale case
destroys the gate's remaining purpose. Both must be caught.

The ordering group exists for the same reason in miniature. Widening the
absent-evidence case is only defensible while affirmative bad evidence still
refuses through it, so ``FAILED_SYNC_STATES`` is checked above the unverified
allow. Every production row carrying a failed state also carries a timestamp,
which makes the wrong order invisible to any realistic fixture — hence a mutation
that reinstates the dependency rather than deleting the check.

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
        "    if sync_state in FAILED_SYNC_STATES:",
        "    if False:",
    ),
    (
        # The ordering bug specifically, rather than the check being absent. These
        # two mutations look alike and are not: the one above deletes the failed-read
        # refusal outright, which almost any test notices; this one leaves it in
        # place and makes it conditional on the listing having a timestamp, which is
        # exactly what the earlier arrangement did by putting it below the
        # never-confirmed allow. Every production row that carries a failed state
        # also carries a timestamp, so this mutation is invisible to a suite that
        # only exercises realistic rows — and it is the one that would sell a
        # REMOVED product.
        "a failed read only refuses when it happens to carry a timestamp",
        GATE,
        "    if sync_state in FAILED_SYNC_STATES:",
        "    if sync_state in FAILED_SYNC_STATES and confirmation == CONFIRMATION_KNOWN:",
    ),
    (
        # PENDING is the column DEFAULT, so this refuses every freshly imported
        # listing. It belongs in the permissive group by construction and in the
        # strictness group by effect; it is here because the mutation is "somebody
        # tightened the failed set", and the point is that tightening it is not
        # free.
        "the default sync state joins the failed set and refuses every import",
        GATE,
        "FAILED_SYNC_STATES = (supplier_schema.SYNC_STALE, supplier_schema.SYNC_ERROR,",
        "FAILED_SYNC_STATES = (supplier_schema.SYNC_PENDING, supplier_schema.SYNC_STALE,\n"
        "                      supplier_schema.SYNC_ERROR,",
    ),
    (
        "a confirmation dated in the future reads as fresh",
        GATE,
        "    if age > CONFIRMATION_MAX_AGE_SECONDS or age < -CLOCK_SKEW_TOLERANCE_SECONDS:",
        "    if age > CONFIRMATION_MAX_AGE_SECONDS:",
    ),
    (
        "a supplier-less listing is reported as checked and satisfied",
        GATE,
        "def _not_applicable(reason: str) -> dict:\n    return {**_base(NOT_APPLICABLE), \"reason\": reason}",
        "def _not_applicable(reason: str) -> dict:\n    return {**_base(DECISION_ALLOW), \"reason\": reason}",
    ),
    (
        # Deliberately mutated in `_confirmation` rather than in `_parse`. The
        # obvious version — have `_parse` fall back to `datetime.now()` — is
        # defeated by the clock-skew guard instead of by the suite's intent, and
        # only on days when the real clock is far enough from the frozen `NOW` to
        # trip it. A mutation whose fate depends on the wall clock proves nothing.
        #
        # This anchor moved when `_age_seconds` was replaced. The old mutation put
        # `return 0.0` where the None came from; the equivalent now is to hand back
        # a KNOWN state with a zero age, because the state string is what the
        # decision branches on.
        "an unreadable timestamp is treated as a perfectly fresh one",
        GATE,
        "    parsed = _parse(text)\n    if parsed is None:\n        return CONFIRMATION_UNREADABLE, None",
        "    parsed = _parse(text)\n    if parsed is None:\n        return CONFIRMATION_KNOWN, 0.0",
    ),
    (
        # The other half of the same seam. Above, corrupt evidence reads as fresh;
        # here it reads as *absent*, which routes it into the widened allow. This is
        # the specific way the never-confirmed fix could be over-applied — "None is
        # None, why are there two of them" is a plausible simplification and it
        # sells against a column nobody can read.
        "a corrupt confirmation is filed as one that was never written",
        GATE,
        "    parsed = _parse(text)\n    if parsed is None:\n        return CONFIRMATION_UNREADABLE, None",
        "    parsed = _parse(text)\n    if parsed is None:\n        return CONFIRMATION_NEVER, None",
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
        "    if not running or confirmation == CONFIRMATION_NEVER:",
        "    if False:",
    ),
    (
        # The regression this module was rewritten to prevent, expressed as the one
        # edit that would cause it. Anybody simplifying that condition back to
        # `if not running:` is re-introducing it, and the measured cost was every
        # drop-shipped checkout on the deployment: 22 of 22 sources carry a NULL
        # `last_synced_at`, and the drain latch flips to DRAINING within seconds of
        # the worker's first tick.
        #
        # It survives here only if the suite has no test for a running reconciler
        # meeting a listing it has not reached yet — which is the exact test the
        # first draft got backwards, so this is the mutation with the strongest
        # claim to being plausible rather than invented.
        "a listing the running reconciler has not reached yet is refused",
        GATE,
        "    if not running or confirmation == CONFIRMATION_NEVER:",
        "    if not running:",
    ),
    (
        # The inverse over-correction: never-confirmed no longer refuses, so the
        # tempting next simplification is to stop demanding freshness at all. This
        # keeps the widened allow and deletes the strict half, which is what makes
        # it dangerous — the suite's happy paths all still pass.
        "the widened allow swallows the went-stale case too",
        GATE,
        "    if not running or confirmation == CONFIRMATION_NEVER:",
        "    if not running or confirmation != CONFIRMATION_KNOWN or age > 0:",
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
        "            \"confirmation\": \"\", \"confirmation_age_seconds\": None}",
        "    return {\"decision\": decision, \"reason\": \"\"}",
    ),
    (
        # Widening the allow was traded against marking what it lets through, and
        # this is the mutation that collects the widening without paying for it.
        # Both unverified cases still write an annotation, so a test that only
        # asserts "an annotation exists" stays green while the two incidents — a
        # listing merely queued, and a reconciler that is not running at all —
        # become indistinguishable in the audit.
        "the audit stops recording which kind of absence it was",
        GATE,
        "        \"confirmation\": decision.get(\"confirmation\") or \"\",\n",
        "",
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


#: The three lanes, and the structural suite that judges their wiring.
#:
#: The gate mutations above ask "does the gate decide correctly". These ask the
#: question no unit test of the gate can reach: does anybody still call it, in the
#: right place, through the one entry point. A §22 gate nothing calls reads as done.
WIRING_SUITE = "tests/marketplace/test_supplier_checkout_wiring.py"

CART = "services/marketplace_cart_routes.py"
OFFERS = "services/marketplace_offers_routes.py"
BOT = "bot.py"

#: Each lane's gate block, verbatim. Relocating one of these below the lane's first
#: ``INSERT INTO seller_transactions`` is the defect the ordering assertion exists for:
#: the refusal still reaches the buyer with the same sentence, and the only trace is a
#: ``created`` transaction row nobody is looking at.
CART_GATE = '''        screened = supplier_checkout.screen(
            cur, [l["listing_id"] for l in lines], now=now)
        if screened["refusal"]:
            refusal = screened["refusal"]
            return _error(refusal["message"], 409,
                          code=supplier_checkout.refusal_code(refusal),
                          listing_id=screened["refused_listing_id"])
'''

OFFERS_GATE = '''        screened = supplier_checkout.screen(cur, [listing_id], now=now)
        if screened["refusal"]:
            refusal = screened["refusal"]
            return _error(refusal["message"], 409,
                          code=supplier_checkout.refusal_code(refusal))
'''

BOT_GATE = '''    if item_type == "marketplace_product":
        from services import marketplace_supplier_checkout
        supplier_screened = marketplace_supplier_checkout.screen(cur, [item_id], now=now)
        if supplier_screened["refusal"]:
            supplier_refusal = supplier_screened["refusal"]
            conn.close()
            return api_error(
                supplier_refusal["message"], 409,
                error_code=marketplace_supplier_checkout.refusal_code(supplier_refusal))
        transaction_details.update(
            marketplace_supplier_checkout.audit_for(supplier_screened, item_id))
'''

#: Unique lines that sit *after* each lane's first transaction INSERT.
CART_AFTER = "            tx_ids.append(int(cur.lastrowid))\n"
OFFERS_AFTER = "        tx_id = int(cur.lastrowid)\n"
BOT_AFTER = ('    pulse_emit_payment_checkout_event(cur, tx_event, "payment_pending", '
             'status=initial_status, actor_user_id=buyer["user_id"])\n')

#: One mutation whose only job is to prove this harness reads the mutated tree.
#:
#: Every wiring mutation below is judged by a suite that opens source files by path.
#: If those paths resolved back through the overlay's symlinks to the real repo, the
#: suite would read the unmutated lanes and *every* wiring mutation would survive —
#: a uniform sweep of holes that is indistinguishable, in the report, from a suite
#: that genuinely checks nothing. Deleting a lane's gate outright is the most
#: obviously-catchable defect available. If this one survives, nothing else in the
#: wiring group means anything, so it is run first and reported separately.
CANARY_MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the cart lane drops §22 altogether",
        CART, CART_GATE, "", WIRING_SUITE,
    ),
]

WIRING_MUTATIONS: list[tuple[str, str, object, object, str]] = [
    (
        "the cart gate slides below the transaction INSERT",
        CART, [(CART_GATE, ""), (CART_AFTER, CART_AFTER + CART_GATE)], None,
        WIRING_SUITE,
    ),
    (
        "the offers gate slides below the transaction INSERT",
        OFFERS, [(OFFERS_GATE, ""), (OFFERS_AFTER, OFFERS_AFTER + OFFERS_GATE)], None,
        WIRING_SUITE,
    ),
    (
        "the buy-now gate slides below the transaction INSERT",
        BOT, [(BOT_GATE, ""), (BOT_AFTER, BOT_AFTER + BOT_GATE)], None,
        WIRING_SUITE,
    ),
    (
        "the cart lane grows its own copy of the gate's decision loop",
        CART,
        '''        screened = supplier_checkout.screen(
            cur, [l["listing_id"] for l in lines], now=now)''',
        '''        evidence = supplier_checkout.reconciliation_evidence(cur, now=now)
        screened = {"refusal": None, "refused_listing_id": None, "decisions": {
            l["listing_id"]: supplier_checkout.evaluate(
                cur, listing_id=l["listing_id"], evidence=evidence, now=now)
            for l in lines}}''',
        WIRING_SUITE,
    ),
    (
        "the cart stops annotating the sale it could not confirm",
        CART, '                      **supplier_checkout.audit_for(screened, l["listing_id"]),\n',
        "", WIRING_SUITE,
    ),
    (
        "the offers lane stops annotating the sale it could not confirm",
        OFFERS, "                  **supplier_checkout.audit_for(screened, listing_id),\n",
        "", WIRING_SUITE,
    ),
    (
        "the buy-now lane stops annotating the sale it could not confirm",
        BOT, """        transaction_details.update(
            marketplace_supplier_checkout.audit_for(supplier_screened, item_id))
""", "", WIRING_SUITE,
    ),
    (
        "the cart sends the internal reason as the client-facing code",
        CART, "code=supplier_checkout.refusal_code(refusal),",
        'code=refusal["reason"],', WIRING_SUITE,
    ),
    (
        "the offers lane sends the internal reason as the client-facing code",
        OFFERS, "                          code=supplier_checkout.refusal_code(refusal))",
        '                          code=refusal["reason"])', WIRING_SUITE,
    ),
    (
        "the buy-now lane sends the internal reason as the client-facing code",
        BOT, "                error_code=marketplace_supplier_checkout.refusal_code(supplier_refusal))",
        '                supplier_refusal["reason"])', WIRING_SUITE,
    ),
    (
        "the buy-now gate loses its marketplace_product scope and screens courses",
        BOT, '''    if item_type == "marketplace_product":
        from services import marketplace_supplier_checkout''',
        """    if True:
        from services import marketplace_supplier_checkout""",
        WIRING_SUITE,
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
    ("Harness self-check — this must be caught or nothing below counts",
     WIRING_SUITE, CANARY_MUTATIONS, False),
    ("A gate nobody calls, or calls in the wrong place", WIRING_SUITE,
     WIRING_MUTATIONS, False),
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
