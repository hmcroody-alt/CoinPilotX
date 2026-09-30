"""Weaken one predicate at a time; each must turn a specific test red.

Every mutant is arity-preserving on the SQL side — an earlier pass changed a
placeholder count instead of a predicate, which made the query raise, hit the
exception path, return zero, and kill the *positive* tests rather than the ones
about scope. `(x=? OR 1=1)` keeps the bind count and removes only the meaning.
"""
import pathlib, shutil, subprocess, sys, tempfile

# Derived, never hardcoded — see the note in
# `prove_commerce_discovery_reachability.py`. `REPO` is only read.
REPO = pathlib.Path(__file__).resolve().parents[2]
PYB = sys.executable
REL = "services/commerce_discovery/events.py"
TESTS = ["tests/commerce_discovery/test_value_is_reconciled.py"]

# This harness used to mutate `REPO/services/.../events.py` directly and put the
# only unmutated copy in a fixed `/tmp/events.bak`, restoring it in a `finally`.
# That works right up until the process is killed between the write and the
# restore — and then the working tree silently holds a mutant, which is the worst
# possible failure for a tool whose entire job is to distinguish real source from
# mutated source. The three later harnesses copy the repo into a temporary
# directory and mutate the copy instead; this one now does the same, so there is no
# restore step to fail and no window in which the real tree is wrong.
ORIG = (REPO / REL).read_text()

MUTANTS = {
    # --- settled money: the purchase tier ---
    "settled: no buyer scope": ('"WHERE buyer_user_id=? AND listing_id=? "', '"WHERE (buyer_user_id=? OR 1=1) AND listing_id=? "'),
    "settled: no listing scope": ('"WHERE buyer_user_id=? AND listing_id=? "', '"WHERE buyer_user_id=? AND (listing_id=? OR 1=1) "'),
    "settled: no status filter": (
        '"AND LOWER(COALESCE(status,\'\')) IN (\'paid\',\'fulfilled\',\'completed\') "', '""'),
    "settled: no buyer requirement": ("if not order_ref or not buyer_user_id:", "if not order_ref:"),
    # --- intent: the cart/checkout tier ---
    "intent: every verb is priced": ("if verb in _INTENT_VERBS:", "if True:"),
    "intent: cart not scoped to the buyer": (
        '"SELECT qty FROM marketplace_cart_items WHERE user_id=? AND listing_id=? LIMIT 1"',
        '"SELECT qty FROM marketplace_cart_items WHERE (user_id=? OR 1=1) AND listing_id=? LIMIT 1"'),
    "intent: claim preferred over the cart row": ("if quantity <= 0:", "if True:"),
    "intent: no clamp at all": ("quantity = max(1, min(quantity, ceiling))", "quantity = int(quantity)"),
    "intent: no floor": ("quantity = max(1, min(quantity, ceiling))", "quantity = min(quantity, ceiling)"),
    "intent: no ceiling": ("quantity = max(1, min(quantity, ceiling))", "quantity = max(1, quantity)"),
    "intent: unstocked means unbounded": (
        "ceiling = stock if stock > 0 else _MAX_CLAIMED_QUANTITY", "ceiling = stock if stock > 0 else 10**12"),
    # There is no *quiet* weakening of this one: removing the guard cannot invent
    # a price, it can only reach the unpack with None. So the mutant is the crash
    # shape, and the test's job is to prove the branch is taken at all rather than
    # to catch a plausible regression. Labelled as such in the test itself.
    "intent: the unpriced branch is removed": ("    if priced is None:\n", "    if False:\n"),
    "pre-fix: trust the caller": None,
}


def mutate(name, target):
    if name == "pre-fix: trust the caller":
        s = ORIG
        s = s.replace(
            "    buyer_user_id: Any = None,\n",
            '    value_minor: int = 0,\n    currency: str = "",\n    buyer_user_id: Any = None,\n')
        s = s.replace("""    value_minor, currency = _reconciled_value(
        cur,
        verb=verb,
        order_ref=order_ref,
        listing_id=int(row["listing_id"]),
        buyer_user_id=buyer_user_id,
        claimed_quantity=claimed_quantity,
        parse_price=parse_price,
    )
""", "")
        s = s.replace('row.get("session_id") or "", verb, value_minor,\n            currency or None,',
                      'row.get("session_id") or "", verb, max(0, int(value_minor or 0)),\n            (currency or "").upper()[:8] or None,')
        assert "value_minor: int = 0" in s and "_reconciled_value(\n        cur," not in s, "pre-fix rewrite failed"
        target.write_text(s)
        return
    a, b = MUTANTS[name]
    assert a in ORIG, f"anchor missing for {name}: {a}"
    target.write_text(ORIG.replace(a, b))


def sandbox(tmp):
    """A throwaway copy of the repo. Only the copy is ever mutated."""
    base = pathlib.Path(tmp) / "repo"
    base.mkdir()
    for path in ("services", "tests", "conftest.py", "pytest.ini", "setup.cfg", "tox.ini"):
        src = REPO / path
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, base / path,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, base / path)
    return base


def pytest_in(base):
    return subprocess.run([PYB, "-m", "pytest", *TESTS, "-q", "--no-header",
                           "-p", "no:cacheprovider"],
                          cwd=base, capture_output=True, text=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = sandbox(tmp)
        target = base / REL

        # A red baseline makes every result below meaningless — a mutant "killed" by
        # an already-failing test proves nothing about the mutant.
        baseline = pytest_in(base)
        if baseline.returncode != 0:
            print("BASELINE IS RED — nothing below means anything")
            print(baseline.stdout[-3000:])
            return 1
        print("baseline: green\n")

        unkilled = []
        for name in MUTANTS:
            mutate(name, target)
            r = pytest_in(base)
            killed = [l.split("::")[-1] for l in r.stdout.splitlines() if l.startswith("FAILED")]
            tail = [l for l in r.stdout.splitlines() if "passed" in l or "error" in l][-1:]
            print(f"\n--- {name}: {tail}")
            for k in killed:
                print(f"      killed by: {k}")
            if not killed:
                unkilled.append(name)
            target.write_text(ORIG)
        print("\nSURVIVED (no test noticed):", unkilled or "none")
        return 1 if unkilled else 0


if __name__ == "__main__":
    sys.exit(main())
