"""Would `tests/test_search_truth.py` notice if the sentinel went blind?

`scripts/search_os/search_truth_sentinel.py` reports zero faults across every
product URL in production. That sentence is worth exactly as much as the test
suite's ability to fail, and a comparison engine is unusually good at failing
silently: every one of its checks degrades to "no contradiction found" when the
thing it reads stops being read. A regex that stops matching, a branch that
stops being taken, and a healthy page produce the same clean verdict.

So each mutation below breaks one specific comparison in the way it would
plausibly break on its own -- a threshold off by one, a branch never entered, a
contradiction recorded as an absence -- and the suite has to go red. A mutant
that survives is a comparison the sentinel is only *assumed* to be making.

HOW THIS AVOIDS THE USUAL WAYS A HARNESS LIES
---------------------------------------------
* No restore step. The repository is copied into a `TemporaryDirectory` and the
  copy is mutated, so an interrupted run cannot leave a mutation in real source
  and cannot revert uncommitted work. A sibling harness in this repo wrote 45
  mutations into live files by restoring instead of sandboxing.
* An anchor that does not match is a HARNESS error, reported separately and
  loudly. It is not a kill: nothing was mutated, so the red suite -- or green
  one -- says nothing about the test.
* The baseline is run first, in the sandbox, and a red baseline aborts. A mutant
  "killed" by an already-failing suite proves nothing.
* `sys.executable` runs the tests, so the harness cannot silently use an
  interpreter without pytest and read the resulting failure as a kill.
* Mutants expected to survive are declared as such, with a reason, and counted
  apart from the kills. "All N killed" must never be printable while something
  survived by design.

    python3 scripts/protection/mutate_search_truth.py
    python3 scripts/protection/mutate_search_truth.py --only AGGREGATE_OFFER_IGNORED
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ENGINE = "services/search_truth.py"
SUITE = "tests/test_search_truth.py"

#: Only what the suite needs to run. Copying the whole checkout would drag in
#: `node_modules`, the virtualenv and the database file for no benefit.
#:
#: `seo` is here because `marketplace_seo` imports `seo.schema` at module scope;
#: the first run of this harness omitted it and the sandbox could not collect
#: the suite at all. That showed up as COULD_NOT_RUN rather than as ten kills,
#: which is the whole reason that outcome is kept separate.
COPY = (
    "services",
    "seo",
    "tests/__init__.py",
    "tests/conftest.py",
    SUITE,
    "tests/fixtures/search_truth",
)


@dataclass(frozen=True)
class Mutation:
    name: str
    #: What this breaks, in the terms of the thing we would lose in production.
    breaks: str
    anchor: str
    replacement: str
    path: str = ENGINE
    #: Set when a mutant is expected to live. Counted separately, never as a win.
    survives_because: str = ""


MUTATIONS = (
    Mutation(
        "AGGREGATE_OFFER_IGNORED",
        "ranged products stop being price-checked at all -- the exact subset "
        "where marketplace_seo documents real drift",
        'elif "lowPrice" in offer or "highPrice" in offer:',
        "elif False:",
    ),
    Mutation(
        "SURFACE_DISAGREEMENT_TOLERATED",
        "a page quoting two different prices in two of its own tags passes",
        "if len(set(stated.values())) > 1:",
        "if len(set(stated.values())) > 2:",
    ),
    Mutation(
        "PROSE_CONTRADICTION_UNREPORTED",
        "a description stating both the real price and a stale one passes",
        "if len(claims) > 1:",
        "if len(claims) > 2:",
    ),
    Mutation(
        "PROSE_CONTRADICTION_READS_AS_ABSENCE",
        "the surface that holds two amounts is recorded as holding none, which "
        "is how this defect hid before the fault code existed",
        'shown = ", ".join(sorted(_show(_money_key(c)) for c in claims))',
        'shown = "no price"',
    ),
    Mutation(
        "STOCK_LABEL_MATCHED_AS_SUBSTRING",
        '"Currently unavailable" reads as in stock, because "available" is a '
        'substring of "unavailable"',
        'return _STOCK_LABELS.get(" ".join(str(label).split()).strip().lower())',
        'return marketplace_seo.IN_STOCK if "available" in str(label).lower() '
        "else _STOCK_LABELS.get(str(label).strip().lower())",
    ),
    Mutation(
        "CANONICAL_DRIFT_IGNORED",
        "a page declaring someone else's canonical passes",
        "elif canonical != expected_canonical:",
        "elif False:",
    ),
    Mutation(
        "IDENTITY_DRIFT_IGNORED",
        "a page whose Product.sku names a different listing passes",
        "elif sku != expected_sku:",
        "elif False:",
    ),
    Mutation(
        "UNKNOWN_READS_AS_KNOWN",
        "an unreadable surface counts as a surface that agreed",
        "return self.value is not None",
        "return True",
    ),
    Mutation(
        "ROW_BLAME_COLLAPSED",
        "a drifting price_label is reported against the page template as well "
        "as the row, sending someone to debug a correct renderer",
        "elif marketplace_seo.price_label_contradicts_variants(listing):",
        "elif False:",
    ),
    Mutation(
        "CURRENCY_DROPPED_FROM_COMPARISON",
        "two surfaces stating the same number in different currencies compare "
        "as equal",
        'return (Decimal(claim.low), Decimal(claim.high), (claim.currency or "").upper())',
        'return (Decimal(claim.low), Decimal(claim.high), "")',
    ),
)


@dataclass
class Result:
    name: str
    outcome: str  # KILLED | SURVIVED | SURVIVED_BY_DESIGN | HARNESS_ERROR
    detail: str = ""
    failures: tuple = field(default_factory=tuple)


def sandbox(into):
    for rel in COPY:
        src = REPO / rel
        dst = into / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def run_suite(root):
    """``(ok, tail)``. ``ok`` only when pytest itself ran and everything passed."""

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    out = (proc.stdout + proc.stderr).strip()
    # A collection error exits non-zero too. Treating that as a kill is the
    # failure mode this harness most needs to avoid, so the distinction is made
    # on the summary line rather than on the exit code alone.
    if "no tests ran" in out or "error" in out.splitlines()[-1].lower():
        return False, "COULD_NOT_RUN: " + out[-800:]
    return proc.returncode == 0, out[-800:]


def failing_tests(output):
    return tuple(
        line.split("::")[-1].split()[0]
        for line in output.splitlines()
        if line.startswith("FAILED")
    )


def apply_mutation(root, mutation):
    target = root / mutation.path
    source = target.read_text()
    hits = source.count(mutation.anchor)
    if hits != 1:
        return f"anchor matched {hits}x, expected exactly 1"
    target.write_text(source.replace(mutation.anchor, mutation.replacement))
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", dest="names")
    args = parser.parse_args(argv)

    selected = [m for m in MUTATIONS if not args.names or m.name in args.names]
    if args.names:
        unknown = set(args.names) - {m.name for m in MUTATIONS}
        if unknown:
            print(f"unknown mutation(s): {sorted(unknown)}")
            return 2

    with tempfile.TemporaryDirectory(prefix="search-truth-baseline-") as tmp:
        root = Path(tmp)
        sandbox(root)
        ok, out = run_suite(root)
        if not ok:
            print("BASELINE IS NOT GREEN -- every result below would be meaningless.\n")
            print(out)
            return 2
        print(f"baseline  GREEN  ({out.splitlines()[-1].strip()})\n")

    results = []
    for mutation in selected:
        with tempfile.TemporaryDirectory(prefix="search-truth-mutant-") as tmp:
            root = Path(tmp)
            sandbox(root)
            problem = apply_mutation(root, mutation)
            if problem:
                results.append(Result(mutation.name, "HARNESS_ERROR", problem))
                print(f"  HARNESS  {mutation.name}: {problem}")
                continue
            ok, out = run_suite(root)
            if out.startswith("COULD_NOT_RUN"):
                results.append(Result(mutation.name, "HARNESS_ERROR", out))
                print(f"  HARNESS  {mutation.name}: suite did not run")
                continue
            if ok:
                outcome = "SURVIVED_BY_DESIGN" if mutation.survives_because else "SURVIVED"
                results.append(Result(mutation.name, outcome, mutation.survives_because))
                print(f"  {outcome:18s} {mutation.name}")
            else:
                caught = failing_tests(out)
                results.append(Result(mutation.name, "KILLED", "", caught))
                print(f"  KILLED   {mutation.name}  ({len(caught)} test(s))")

    def count(outcome):
        return [r for r in results if r.outcome == outcome]

    killed, survived = count("KILLED"), count("SURVIVED")
    by_design, broken = count("SURVIVED_BY_DESIGN"), count("HARNESS_ERROR")

    print(f"\nkilled {len(killed)}/{len(selected)}")
    if by_design:
        print(f"survived by design {len(by_design)} (declared, not a pass)")
    if broken:
        print(f"\nHARNESS ERRORS -- these prove nothing either way:")
        for r in broken:
            print(f"  {r.name}: {r.detail}")
    if survived:
        print("\nSURVIVED -- the suite does not test these comparisons:")
        for r in survived:
            print(f"  {r.name}")
            print(f"    breaking it means: {dict((m.name, m.breaks) for m in MUTATIONS)[r.name]}")
    for r in killed:
        print(f"\n{r.name} caught by:")
        for name in r.failures:
            print(f"  {name}")

    return 1 if survived or broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
