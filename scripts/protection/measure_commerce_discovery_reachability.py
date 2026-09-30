"""Regenerate the measured figures the reachability and retrieval chapters cite.

The report's defect 3 and 4 rows are numbers: a share of the catalogue that no
request could reach, and a share of placements a camera-only viewer was served.
Those numbers appear a dozen times across the report, the package docstrings and
two test files, and until this script nothing in the repository could produce
them again. The five ``prove_*`` harnesses prove the *tests* have teeth; none of
them prints a measurement. The figures survived only as prose. A report citing
unreproducible evidence is worse than one citing none, so this is the missing
half: it drives the real ``engine.serve`` on a 2,000-item catalogue and prints
the grid, in each of the four tree states the report compares.

**How the older states are reconstructed.** Not from git history — the pre-fix
code is not on this branch — but by re-applying, in a sandbox, the reverts the
harnesses already label "the true pre-fix" path:

* ``prove_commerce_discovery_signal_defects.py``'s ``revert()``, the whole
  pre-fix state of ``engine.py`` and ``ranking.py`` (unwindowed counts, lifetime
  trending, and the narrow interest profile). Imported, not copied: it is one
  implementation with its own anchor assertions, and a second copy here would be
  free to rot.
* ``prove_commerce_discovery_reachability.py``'s headline mutant, which makes
  ``rotation_offset`` ignore the span — the pre-span arithmetic exactly.
* ``prove_commerce_discovery_sources.py``'s headline mutant, which makes
  ``_sources`` return only the rotating source — the pre-multi-source retrieval
  exactly.

The three touch four different files, so they compose. The two mutants' find
strings are duplicated from those harnesses deliberately rather than imported
from their ``MUTANTS`` tables, because a table is a list of pairs whose meaning
depends on position: silently picking up a re-ordered entry would measure
something else under the right label. If either source drifts, the ``count != 1``
check below stops the run.

**What it cannot reconstruct**, and this is the limit to state plainly: the
cap, fatigue and value-tier fixes landed in the same package and have no combined
revert — their harnesses are lists of individual mutants, not a pre-fix restore.
So ``pre-audit`` here is the pre-audit tree in the four mechanisms that govern
these two measurements, not the tree as it stood. Two of the report's original
figures were taken on that fuller tree and do not reproduce; they were corrected
to the reproducible ones rather than kept, and the note in §6 says so.

**The measurement itself is not reimplemented either.** The probe module written
into the sandbox imports ``_enlarge``/``_reached`` from
``test_the_catalogue_is_reachable.py`` and ``_split_catalogue``/``_clicked``/
``_served_mix`` from ``test_retrieval_asks_several_questions.py``, and runs under
the real ``market`` fixture. The numbers printed here therefore come out of the
same code paths the assertions use — this script and the suite cannot drift
apart, because there is only one implementation of each.

Run it: ``python3 scripts/protection/measure_commerce_discovery_reachability.py``
Exit 0 means every figure the report cites reproduced within tolerance. It takes
about a minute: four states x 60 rotation epochs x four surfaces against a real
2,000-row catalogue.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Derived, never hardcoded — see the note in
# `prove_commerce_discovery_reachability.py`. `REPO` is only ever read: it is
# copied into a temporary directory and only the copy is mutated, so the working
# tree is never written and this can run alongside the other harnesses.
REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
HERE = Path(__file__).resolve().parent

ENGINE = "services/commerce_discovery/engine.py"
RANKING = "services/commerce_discovery/ranking.py"
EXPOSURE = "services/commerce_discovery/exposure.py"
POOL = "services/commerce_discovery/pool.py"

#: Snapshotted and restored around every state. `revert()` has no inverse, so the
#: unit of undo is the file's text, not the individual edit.
TOUCHED = (ENGINE, RANKING, EXPOSURE, POOL)

PROBE_REL = "tests/commerce_discovery/test_zz_measurement_probe.py"

#: ``prove_commerce_discovery_reachability.py``'s headline mutant, verbatim.
SPAN_MUTANT = (
    EXPOSURE,
    "    if span and int(span) > 0:\n"
    "        # Ceiling division: the last partial batch still deserves a slot, or the\n"
    "        # tail of the catalogue is exactly the part that stays unreachable.\n"
    "        slots = max(slots, -(-int(span) // max(1, batch)))",
    "    if False:\n        slots = slots",
)

#: ``prove_commerce_discovery_sources.py``'s headline mutant, verbatim.
SOURCES_MUTANT = (
    POOL,
    "    found.append(_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)))\n"
    "    return tuple(found)",
    "    return (_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)),)",
)

#: Oldest tree state first, so the printed grid reads like the history. The two
#: middle rows are what isolate the claims: each fix measured with only itself
#: missing, which is the comparison the report's before/after columns are making.
STATES = (
    ("pre-audit", "no signal, span or source fix", (SPAN_MUTANT, SOURCES_MUTANT), True),
    ("span-off", "only the span fix missing", (SPAN_MUTANT,), False),
    ("sources-off", "only multi-source missing", (SOURCES_MUTANT,), False),
    ("shipped", "HEAD", (), False),
)

#: What the report claims, keyed by state, and only the figures it actually
#: cites. Every one of these was measured by this script; none was carried over
#: from the prose. Where a figure here disagrees with an older draft of the
#: report, the older draft was wrong and was corrected — see §6's note.
EXPECTED = {
    "pre-audit": {
        "reachable_pct": 17.0,
        "dark_pct": 70.8,
        "shallowest_id": 1416,
        "camera_share_pct": 0.0,
        "placements": 960,
    },
    "sources-off": {"camera_share_pct": 11.9},
    "shipped": {
        "reachable_pct": 40.5,
        "dark_pct": 0.2,
        "shallowest_id": 6,
        "camera_share_pct": 48.4,
        "lift": 4.84,
    },
}

#: Percentage points, and also raw units for `shallowest_id` / `placements`.
#: The measurement is deterministic — fixed epoch, seeded catalogue,
#: digest-derived offsets — so this is slack for rounding, not for noise. A real
#: drift will be far larger than half a point.
TOLERANCE = 0.5

MARKER = "@@MEASURED@@"

PROBE = '''"""Not a test. A measurement, run as a test so that it gets the real fixtures.

Written into a sandbox by ``scripts/protection/measure_commerce_discovery_reachability.py``
and deleted with it. Deliberately not committed as a test: it asserts nothing, it
takes a quarter of a minute, and a green suite must not depend on a number that
is allowed to move when a ranking weight is retuned.
"""
import json

from test_the_catalogue_is_reachable import LARGE, SURFACES, _enlarge, _reached
from test_retrieval_asks_several_questions import (
    DEEP,
    _clicked,
    _served_mix,
    _split_catalogue,
)

MARKER = "@@MEASURED@@"
EPOCHS = 60


def _emit(metric, payload):
    # Leading newline because `-q` writes a progress dot for the preceding test
    # without a line break, and a marker that is not at the start of its line is
    # a marker the parent process will not see.
    print("\\n" + MARKER + json.dumps(dict(payload, metric=metric)))


def test_measure_reachability(market, clock):
    _enlarge(market, LARGE)
    reached = _reached(market, clock, epochs=EPOCHS)
    # `dark` is the run of ids below the shallowest row ever fetched. The
    # candidate ordering ends in `id DESC`, so an unreachable region is always a
    # prefix of the ids -- which is why the report can quote a single number.
    dark = (min(reached) - 1) if reached else LARGE
    _emit("reachability", {
        "catalogue": LARGE,
        "epochs": EPOCHS,
        "surfaces": len(SURFACES),
        "distinct_reached": len(reached),
        "reachable_pct": round(100.0 * len(reached) / LARGE, 1),
        "shallowest_id": min(reached) if reached else 0,
        "deepest_id": max(reached) if reached else 0,
        "dark_rows": dark,
        "dark_pct": round(100.0 * dark / LARGE, 1),
        # The ordering position of the shallowest row reached, which is the form
        # the package docstring quotes ("nothing past row ~585").
        "deepest_ordering_row": (LARGE - min(reached) + 1) if reached else 0,
    })


def test_measure_affinity(market, clock):
    _enlarge(market, LARGE)
    _split_catalogue(market)
    market.render(market.serve("feed"))  # establishes the subject_ref
    _clicked(market, range(1, 21))
    got = _served_mix(market, epochs=EPOCHS)
    total = sum(got.values())
    share = (got["cameras"] / total) if total else 0.0
    catalogue_share = DEEP / LARGE
    _emit("affinity", {
        "placements": total,
        "cameras": got["cameras"],
        "widgets": got["widgets"],
        "camera_share_pct": round(100.0 * share, 1),
        "catalogue_share_pct": round(100.0 * catalogue_share, 1),
        "lift": round(share / catalogue_share, 2),
    })
'''


def load_signal_revert():
    """``revert()`` from the signal-defect harness, imported rather than copied."""
    path = HERE / "prove_commerce_discovery_signal_defects.py"
    spec = importlib.util.spec_from_file_location("_cd_signal_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.revert


def sandbox(tmp: Path) -> Path:
    base = tmp / "repo"
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
    (base / PROBE_REL).write_text(PROBE)
    return base


def measure(base: Path) -> tuple[dict, str]:
    """Run the probe and collect its emitted JSON, keyed by metric name."""
    proc = subprocess.run(
        [PY, "-m", "pytest", PROBE_REL, "-q", "-s", "-p", "no:randomly",
         "-p", "no:cacheprovider", "--tb=short"],
        cwd=base, capture_output=True, text=True,
    )
    output = proc.stdout + proc.stderr
    found: dict[str, dict] = {}
    for line in output.splitlines():
        if MARKER not in line:
            continue
        payload = json.loads(line[line.index(MARKER) + len(MARKER):])
        found[payload.pop("metric")] = payload
    if proc.returncode != 0 or len(found) != 2:
        return {}, output
    return found, output


def apply_state(base: Path, mutants, signals, signal_revert) -> None:
    for rel, find, replace in mutants:
        target = base / rel
        original = target.read_text()
        count = original.count(find)
        if count != 1:
            which = "reachability" if rel == EXPOSURE else "sources"
            raise SystemExit(
                f"PATTERN DRIFT in {rel}: the mutant's find string matched "
                f"{count}x, expected 1. The source has changed since this script "
                "was written, so the older tree states cannot be reconstructed "
                "and none of the report's figures can be checked. Re-derive the "
                f"mutant from prove_commerce_discovery_{which}.py."
            )
        target.write_text(original.replace(find, replace))
    if signals:
        # Raises AssertionError on its own anchors if `engine.py`/`ranking.py`
        # have moved, which is the same loud failure as the count check above.
        signal_revert(base)


def main() -> int:
    signal_revert = load_signal_revert()
    with tempfile.TemporaryDirectory() as tmp:
        base = sandbox(Path(tmp))
        pristine = {rel: (base / rel).read_text() for rel in TOUCHED}

        results: dict[str, dict] = {}
        for state, note, mutants, signals in STATES:
            print(f"measuring {state:<12} ({note}) ...", flush=True)
            try:
                apply_state(base, mutants, signals, signal_revert)
                found, output = measure(base)
            finally:
                for rel, text in pristine.items():
                    (base / rel).write_text(text)
            if not found:
                print(f"  PROBE FAILED in state {state!r} — nothing below means anything")
                print(output[-4000:])
                return 1
            results[state] = {**found["reachability"], **found["affinity"]}

        head = results["shipped"]
        print(f"\n{head['catalogue']} listings, {head['epochs']} rotation epochs, "
              f"{head['surfaces']} surfaces, through engine.serve. Cameras are "
              f"{head['catalogue_share_pct']:.0f}% of the catalogue and sit at its "
              f"deep end.\n")
        print(f"  {'state':<12}  {'reachable':>9}  {'dark':>6}  {'shallowest':>10}"
              f"  {'placements':>10}  {'cameras':>8}  {'lift':>6}")
        for state, _note, _mutants, _signals in STATES:
            row = results[state]
            print(f"  {state:<12}  {row['reachable_pct']:>8.1f}%  "
                  f"{row['dark_pct']:>5.1f}%  {row['shallowest_id']:>10}"
                  f"  {row['placements']:>10}  {row['camera_share_pct']:>7.1f}%"
                  f"  {row['lift']:>5.2f}x")
        pre = results["pre-audit"]
        print(f"\n  pre-audit reached {pre['distinct_reached']} distinct listings and "
              f"nothing past ordering row {pre['deepest_ordering_row']}; "
              f"shipped reached {head['distinct_reached']}")

        drift = []
        for state, claims in EXPECTED.items():
            for key, claimed in claims.items():
                actual = results[state][key]
                if abs(actual - claimed) > TOLERANCE:
                    drift.append(f"{state}/{key}: report says {claimed}, measured {actual}")

        print()
        if drift:
            print(f"{len(drift)} FIGURE(S) IN THE REPORT NO LONGER REPRODUCE:")
            for line in drift:
                print(f"  - {line}")
            print("\nThe measured column is the evidence and the report is a claim "
                  "about it, so correct the report — unless the drift is a real "
                  "behaviour change, in which case the suite will be red too.")
            return 1
        checked = sum(len(claims) for claims in EXPECTED.values())
        print(f"all {checked} figures cited in the report reproduced "
              f"(within {TOLERANCE} of the stated value)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
