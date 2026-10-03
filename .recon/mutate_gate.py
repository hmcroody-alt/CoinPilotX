"""§28. Break each new check on purpose and confirm the suite notices.

A green suite is not evidence until it has been shown to go red. Every mutation
below is a plausible mistake -- a flipped comparison, a convenient fallback, an
ignored argument -- and each one must be caught by at least one named test. A
mutation that survives means the check it breaks is unprotected, whatever the
suite reports.

Safety: the real source file is restored in a ``finally`` and its SHA-256 is
compared against the pre-run digest before this script will report success. The
whole battery runs in one process so there is no window in which the working
tree is left mutated.
"""
import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path("/Users/hmcherie/Desktop/cpx-catalog")
PYTHON = "/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python"

#: The two files the CATALOG SAFETY GATE added checks to, each with the suite
#: that is supposed to be defending it. Keyed rather than single because the
#: decider and its reader fail differently and independently: the gate can refuse
#: correctly while the reporting layer files the refusal under a name that sends
#: nobody to look at it, and a battery that only mutated the gate would call that
#: covered.
TARGETS = {
    "gate": (ROOT / "services" / "marketplace_supplier_checkout.py",
             "tests/marketplace/test_supplier_checkout_gate.py"),
    "reader": (ROOT / "services" / "marketplace_drain_observability.py",
               "tests/marketplace/test_drain_observability.py"),
}

# (target, name, old, new, the test that must catch it)
#
# The last field is a test *name*, not a description. A mutation that turns the
# suite red by tripping some unrelated assertion has not been caught by anything
# deliberate -- it has been caught by luck, and the luck can change. Naming the
# intended guard is what makes this battery evidence about the tests rather than
# evidence about the suite.
GATE_MUTATIONS = [
    ("unbound falls back to the first variant",
     "    bound = str((source or {}).get(\"provider_variant_id\") or \"\").strip()\n"
     "    if not bound:\n        return None",
     "    bound = str((source or {}).get(\"provider_variant_id\") or \"\").strip()\n"
     "    if not bound:\n        return dict(rows[0]) if rows else None",
     "test_an_unbound_listing_is_refused_before_the_charge"),

    # The guard deleted outright. The code below then dereferences a None bound
    # variant, so this mutation is caught as an error rather than a wrong answer
    # -- which is still the suite noticing, and is what deleting the guard would
    # actually do in production.
    ("the unbound guard is removed",
     "    if bound is None:\n        # Nothing to ship",
     "    if False:\n        # Nothing to ship",
     "test_an_unbound_listing_is_refused_before_the_charge"),

    ("break-even is refused as a loss",
     "if charged is not None and basis_cost is not None and charged < basis_cost:",
     "if charged is not None and basis_cost is not None and charged <= basis_cost:",
     "test_break_even_is_not_a_loss"),

    ("the loss comparison is inverted",
     "if charged is not None and basis_cost is not None and charged < basis_cost:",
     "if charged is not None and basis_cost is not None and charged > basis_cost:",
     "test_selling_below_landed_cost_is_refused"),

    ("the loss check is removed",
     "    if charged is not None and basis_cost is not None and charged < basis_cost:\n"
     "        # The last tier-1 refusal",
     "    if False:\n        # The last tier-1 refusal",
     "test_selling_below_landed_cost_is_refused"),

    ("the lane's price is ignored for the stored one",
     "    charged = price_minor if price_minor is not None else stored",
     "    charged = stored",
     "test_the_price_the_lane_is_actually_charging_wins_over_the_stored_one"),

    ("an unknown cost is read as free",
     "    _, basis_cost = pricing.basis(bound.get(\"cost_cents\"),\n"
     "                                  _shipping_allowance(cur, source))",
     "    _, basis_cost = pricing.basis(bound.get(\"cost_cents\") or 0,\n"
     "                                  _shipping_allowance(cur, source))",
     "test_an_unknown_supplier_cost_does_not_invent_a_loss"),

    ("freight is dropped from landed cost",
     "    _, basis_cost = pricing.basis(bound.get(\"cost_cents\"),\n"
     "                                  _shipping_allowance(cur, source))",
     "    _, basis_cost = pricing.basis(bound.get(\"cost_cents\"), None)",
     "test_freight_is_part_of_the_cost_being_compared"),

    # §5's central distinction, as a mutation: the gate starts enforcing the
    # store's 68% TARGET_MARGIN instead of refusing only a loss. Production has
    # 112 variants above cost and below target, so this is the mutation that
    # would quietly take profitable listings off sale.
    ("the seller's target is enforced instead of the loss",
     "    _, basis_cost = pricing.basis(bound.get(\"cost_cents\"),\n"
     "                                  _shipping_allowance(cur, source))",
     "    _, _landed = pricing.basis(bound.get(\"cost_cents\"),\n"
     "                                  _shipping_allowance(cur, source))\n"
     "    basis_cost = None if _landed is None else int(_landed / 0.32)",
     "test_a_profitable_sale_below_the_sellers_target_still_sells"),

    ("stock is read across every variant again",
     "    if str(bound.get(\"stock_state\") or \"\").strip().upper() == "
     "supplier_schema.STOCK_OUT_OF_STOCK:",
     "    if rows and all(str(r.get(\"stock_state\") or \"\").strip().upper() == "
     "supplier_schema.STOCK_OUT_OF_STOCK for r in rows):",
     "test_a_sibling_in_stock_does_not_rescue_a_sold_out_bound_variant"),
]

# The reader's half. These mutations do not change a single checkout decision --
# every one of them leaves the gate refusing exactly the same six listings. They
# change only what the refusal is *called*, which is the entire failure mode
# this reporting layer can have: the fix nobody does because the screen said
# somebody else's system was busy.
READER_MUTATIONS = [
    # The tempting simplification. Both new reasons are affirmative facts about
    # the item, so folding them into the existing affirmative bucket looks like
    # tidying up -- and it silently merges two counts that need an operator with
    # three that clear themselves.
    ("the unbound refusal is folded into the affirmative bucket",
     "    if reason == gate.REASON_UNBOUND:",
     "    if False:",
     "test_a_listing_bound_to_no_supplier_variant_cannot_be_fulfilled"),

    ("the loss refusal is folded into the affirmative bucket",
     "    if reason == gate.REASON_NEGATIVE_MARGIN:\n"
     "        return SUPPLIER_NEGATIVE_MARGIN",
     "    if reason == gate.REASON_NEGATIVE_MARGIN:\n"
     "        return AFFIRMATIVE_NEGATIVE_BLOCK",
     "test_a_sale_below_landed_cost_is_a_decision_pending_refusal"),

    # The two reasons swapped. Nothing crashes, the totals are right, and every
    # alert points at the wrong remedy.
    ("the two decision-pending reasons are transposed",
     "    if reason == gate.REASON_UNBOUND:\n"
     "        # Read off the reason",
     "    if reason == gate.REASON_NEGATIVE_MARGIN:\n"
     "        # Read off the reason",
     "test_a_listing_bound_to_no_supplier_variant_cannot_be_fulfilled"),

    # Dropped from REFUSING_STATES: the states still classify correctly, but the
    # refusal count and the refusal rate stop including them, so a dashboard
    # reports zero sales lost while six listings are being turned away.
    ("the new refusals stop counting as refusals",
     "REFUSING_STATES = (SUPPLIER_FULFILLMENT_IMPOSSIBLE, SUPPLIER_NEGATIVE_MARGIN,\n"
     "                   AFFIRMATIVE_NEGATIVE_BLOCK, SUPPLIER_UNCONFIRMED)",
     "REFUSING_STATES = (AFFIRMATIVE_NEGATIVE_BLOCK, SUPPLIER_UNCONFIRMED)",
     "test_the_state_vocabulary_is_partitioned_not_overlapping"),

    # The §22 sentinel widened to any refusal. True in production's steady state
    # from the day the gate ships, which is how a P0 becomes wallpaper -- the
    # exact mistake this module already made once with DRAIN_BEHIND.
    ("the P0 sentinel is widened to any refusal",
     '        "fulfillment_impossible": sustained(\n'
     "            observations, _in(SUPPLIER_FULFILLMENT_IMPOSSIBLE), required=required),",
     '        "fulfillment_impossible": sustained(\n'
     "            observations, _refusing, required=required),",
     "test_an_unfulfillable_buyable_listing_raises_its_own_sentinel"),

    # A sentinel that fires on one observation, pre-empting the rebinding that
    # would have fixed it.
    ("the sentinels lose the sustained window",
     '        "negative_margin": sustained(\n'
     "            observations, _in(SUPPLIER_NEGATIVE_MARGIN), required=required),",
     '        "negative_margin": any(\n'
     "            _in(SUPPLIER_NEGATIVE_MARGIN)(s) for s in observations),",
     "test_the_sentinels_need_the_same_sustained_window_as_everything_else"),
]

MUTATIONS = ([("gate",) + m for m in GATE_MUTATIONS]
             + [("reader",) + m for m in READER_MUTATIONS])


def run_suite(suite):
    # Deliberately *not* `-x`. Stopping at the first failure only tells you that
    # something went red, and the first test to run is not usually the test
    # written for the mutation. The battery's real question is whether the
    # specific guard holds, so it needs the whole failure set.
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", suite, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)


def failures(output):
    names = []
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            names.append(line.split("::")[-1].split(" ")[0])
    return names


# Every target's pristine text and digest, read once up front. Restoring from
# memory rather than from git means this cannot be defeated by an unrelated
# staged change, and the digest is compared before the script will claim success.
ORIGINAL = {key: path.read_text() for key, (path, _) in TARGETS.items()}
DIGEST = {key: hashlib.sha256(text.encode()).hexdigest()
          for key, text in ORIGINAL.items()}

for key, (path, suite) in TARGETS.items():
    print("baseline %-7s" % key, end=" ", flush=True)
    code, output = run_suite(suite)
    if code != 0:
        print("SUITE ALREADY RED -- fix that before mutating\n")
        print(output[-3000:])
        sys.exit(1)
    print(output.strip().splitlines()[-1])
print()

survivors = []
unguarded = []
try:
    for target, name, old, new, protects in MUTATIONS:
        path, suite = TARGETS[target]
        original = ORIGINAL[target]
        label = "[%s] %s" % (target, name)
        if old not in original:
            print("  !! PATTERN MISSING  %-56s (%s)" % (label, protects))
            survivors.append((label, "pattern not found -- this battery is stale"))
            continue
        assert original.count(old) == 1, f"{label}: pattern appears {original.count(old)}x"
        path.write_text(original.replace(old, new, 1))
        try:
            code, output = run_suite(suite)
        finally:
            # Restored per mutation, not just in the outer finally, so the next
            # mutation is applied to pristine text rather than to a stack of them.
            path.write_text(original)
        red = failures(output)
        if code == 0:
            print("  SURVIVED  %-56s (%s)" % (label, protects))
            survivors.append((label, protects))
        elif protects in red:
            print("  caught    %-56s by its own guard" % label)
        else:
            # Red, so the mutation does not ship -- but the test written for it
            # is not the one that noticed, which means that test is not actually
            # pinning what its name claims.
            print("  caught    %-56s by %s" % (label, ", ".join(red[:2])))
            print("            !! but %s stayed green" % protects)
            unguarded.append((label, protects))
finally:
    print()
    bad = False
    for key, (path, _) in TARGETS.items():
        path.write_text(ORIGINAL[key])
        restored = hashlib.sha256(path.read_text().encode()).hexdigest()
        if restored != DIGEST[key]:
            print("!! %s NOT RESTORED -- expected %s got %s"
                  % (path.name, DIGEST[key], restored))
            bad = True
        else:
            print("restored %-7s sha256 matches: %s" % (key, DIGEST[key][:16]))
    if bad:
        sys.exit(2)

print()
if survivors:
    print("%s MUTATION(S) SURVIVED -- those checks are unprotected:" % len(survivors))
    for name, protects in survivors:
        print("  - %s  (%s)" % (name, protects))
if unguarded:
    print("%s MUTATION(S) caught only incidentally -- the named test is not "
          "pinning what it claims:" % len(unguarded))
    for name, protects in unguarded:
        print("  - %s  (expected %s)" % (name, protects))
if survivors or unguarded:
    sys.exit(1)
print("all %s mutations caught by the test written for them" % len(MUTATIONS))
