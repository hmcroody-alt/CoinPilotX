"""Would the new tests notice if the cap were weakened? Break it and see.

Same standard as the earlier chapters: every mutant must be killed, and killed by
a test whose *name* describes the thing that was broken. A mutant that survives
is a claim the suite does not actually make.

The last mutant is the important one — it restores the pre-fix code exactly. If
that one did not kill a large fraction of the file, the file would be describing
behaviour that was already true and the whole chapter would be theatre.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Derived, never hardcoded — see the note in
# `prove_commerce_discovery_reachability.py`. `REPO` is copied into a temporary
# directory and only the copy is mutated; the path is derived so that the harness
# proves the checkout it lives in rather than one fixed tree.
REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
TESTS = (
    "tests/commerce_discovery/test_exposure_is_capped.py"
    " tests/commerce_discovery/test_repetition_is_observed.py"
    " tests/commerce_discovery/test_fatigue_escalates.py"
).split()

METRICS = "services/commerce_discovery/metrics.py"
ENGINE = "services/commerce_discovery/engine.py"

POOL = "services/commerce_discovery/pool.py"
ROUTER = "services/commerce_discovery/router.py"

# (label, file, find, replace) — `find` must appear exactly once.
MUTANTS = [
    (
        # Only `_reject`'s check. Notice how few tests this kills: the SQL
        # exclusion still removes at-cap rows, so end-to-end behaviour barely
        # moves. That is worth knowing — the "optimisation" does enforcement work
        # in the common case, which is exactly why `_reject` must hold too, and
        # why the suite isolates each layer instead of only measuring outcomes.
        "cap: only _reject's drop is removed (the SQL layer covers for it)",
        POOL,
        'if product_cap > 0 and state.product_seen(listing_id) >= product_cap:\n        return "product_cap"',
        'if False:\n        return "product_cap"',
    ),
    (
        "cap: BOTH layers removed — the true pre-fix state of the pipeline",
        POOL,
        # Applied as two edits via a tuple of pairs; see `apply` below.
        (
            ('if product_cap > 0 and state.product_seen(listing_id) >= product_cap:\n        return "product_cap"',
             'if False:\n        return "product_cap"'),
            ("at_cap = product_cap > 0 and state.product_seen(key) >= product_cap",
             "at_cap = False"),
        ),
        None,
    ),
    (
        "cap: off-by-one, so the cap is exceeded by exactly one",
        POOL,
        "state.product_seen(listing_id) >= product_cap",
        "state.product_seen(listing_id) > product_cap",
    ),
    (
        "cap: reported as a cooldown, hiding exhaustion as congestion",
        POOL,
        'return "product_cap"',
        'return "product_cooldown"',
    ),
    (
        # There is no separate cross-surface mechanism to break, because
        # cross-surface fatigue is a consequence of `product_counts` being keyed
        # on the listing alone. So the mutant re-keys the count by surface at the
        # source: count only what happened in the feed. If the cross-surface
        # cases in the suite were vacuous, this would survive.
        "exposure: the exposure count becomes per-surface, killing cross-surface fatigue",
        "services/commerce_discovery/exposure.py",
        "        if age <= product_window:\n            product_counts[listing_id] = product_counts.get(listing_id, 0) + 1",
        "        if age <= product_window and surface == \"feed\":\n            product_counts[listing_id] = product_counts.get(listing_id, 0) + 1",
    ),
    (
        "cap: demoted to a penalty — an at-cap row is kept",
        POOL,
        'if product_cap > 0 and state.product_seen(listing_id) >= product_cap:\n        return "product_cap"',
        'if product_cap > 0 and state.product_seen(listing_id) >= product_cap:\n        pass',
    ),
    (
        "cap: _reject's enforcing argument is made optional and defaults to off",
        POOL,
        "    product_cap: int,\n    seen_ids: set,",
        "    product_cap: int = 0,\n    seen_ids: set = None,",
    ),
    (
        "cap: build no longer normalises, so a bad config disables it",
        POOL,
        "product_cap = config.product_cap() if product_cap is None else max(1, int(product_cap))",
        "product_cap = 0 if product_cap is None else int(product_cap)",
    ),
    (
        "cap: joins the relaxation ladder, so scarcity buys extra sightings",
        POOL,
        'if product_cap > 0 and state.product_seen(listing_id) >= product_cap:',
        'if product_cap > 0 and state.product_seen(listing_id) >= product_cap * 4:',
    ),
    (
        # EXPECTED SURVIVOR. `_hard_exclusions` is documented in the module as
        # "an optimisation, never the enforcement", and a behaviour suite that
        # killed this would be asserting the optimisation instead of the rule.
        # Its survival is the *proof* of the module's own claim — and
        # `test_the_sql_exclusion_is_an_optimisation_and_not_the_enforcement`
        # makes the claim directly by disabling the whole function.
        "EXPECTED SURVIVOR — cap: the SQL exclusion stops excluding at-cap rows",
        POOL,
        "at_cap = product_cap > 0 and state.product_seen(key) >= product_cap",
        "at_cap = False",
    ),
    (
        "router: every surface gets the permissive shopping allowance",
        ROUTER,
        '_PRODUCT_CAP_FACTORS.get(key, _PRODUCT_CAP_FACTORS["feed"])',
        "2.0",
    ),
    (
        "router: an unknown surface gets the shopping allowance, not the feed's",
        ROUTER,
        '_PRODUCT_CAP_FACTORS.get(key, _PRODUCT_CAP_FACTORS["feed"])',
        '_PRODUCT_CAP_FACTORS.get(key, 2.0)',
    ),
    (
        "router: the minimum=1 floor is dropped, so cap=1 becomes a kill switch",
        ROUTER,
        "            _PRODUCT_CAP_FACTORS.get(key, _PRODUCT_CAP_FACTORS[\"feed\"]),\n            minimum=1,",
        "            _PRODUCT_CAP_FACTORS.get(key, _PRODUCT_CAP_FACTORS[\"feed\"]),\n            minimum=0,",
    ),
    (
        "metrics: the cap-breach alert is removed",
        METRICS,
        "if product_cap > 0 and report.top_product_count > product_cap:",
        "if False:",
    ),
    (
        "metrics: the breach alert moves below the sample gate, hiding small breaches",
        METRICS,
        "    if product_cap > 0 and report.top_product_count > product_cap:\n"
        "        found.append(Alert(PRODUCT_CAP_EXCEEDED, float(report.top_product_count), float(product_cap)))\n"
        "\n    if report.impressions < config.repetition_min_sample():\n        return tuple(found)",
        "    if report.impressions < config.repetition_min_sample():\n        return tuple(found)\n"
        "\n    if product_cap > 0 and report.top_product_count > product_cap:\n"
        "        found.append(Alert(PRODUCT_CAP_EXCEEDED, float(report.top_product_count), float(product_cap)))",
    ),
    (
        "metrics: off-by-one, so a count one over the cap is called healthy",
        METRICS,
        "report.top_product_count > product_cap",
        "report.top_product_count > product_cap + 1",
    ),
    (
        "metrics: alerts at the cap, so every exhausted viewer is an incident",
        METRICS,
        "report.top_product_count > product_cap",
        "report.top_product_count >= product_cap",
    ),
    (
        "metrics: the alert defaults on, firing for callers that named no surface",
        METRICS,
        "def alerts(report: RepetitionReport, *, product_cap: int = 0)",
        "def alerts(report: RepetitionReport, *, product_cap: int = 3)",
    ),
    (
        "engine: the surface cap never reaches the observer, so the alert is dead",
        ENGINE,
        "state, surface=surface, subject_ref=policy.subject_ref, product_cap=branch.product_cap",
        "state, surface=surface, subject_ref=policy.subject_ref",
    ),
    # ---- escalating fatigue cooldowns -------------------------------------
    (
        # THE HEADLINE MUTANT for this chapter: `_escalated` returns the
        # cooldown unchanged, which is the pre-fix behaviour exactly. If this
        # does not kill a large fraction of the new file, the file is describing
        # spacing that was already flat and the chapter is theatre.
        "escalation: flat again — the true pre-fix spacing rule",
        POOL,
        "    return int(cooldown) * max(1, int(seen))",
        "    return int(cooldown)",
    ),
    (
        # EXPECTED SURVIVOR, and for the same reason as the SQL cap exclusion
        # above. A flat SQL check is *shorter* than the escalated one, so this
        # drift makes the optimisation exclude FEWER rows than the enforcement
        # will reject. `_reject` still holds them. The cost is wasted rows per
        # batch; the outcome is identical. A behaviour suite that killed this
        # would be pinning the optimisation.
        #
        # The opposite drift is not harmless, and the mutant below proves the
        # suite catches it.
        "EXPECTED SURVIVOR — escalation: the SQL layer stays flat, so it under-excludes",
        POOL,
        "            cooling = product_cooldown > 0 and state.seconds_since_product(key) < _escalated(\n"
        "                product_cooldown, state.product_seen(key)\n            )",
        "            cooling = product_cooldown > 0 and state.seconds_since_product(key) < product_cooldown",
    ),
    (
        # The dangerous drift: the SQL layer escalates HARDER than the
        # enforcement, so it removes inventory `_reject` would have served. That
        # is silent — pages just get thinner — which is why it needs a test of
        # its own rather than an outcome measurement.
        "escalation: the SQL layer over-excludes, removing rows _reject would serve",
        POOL,
        "                product_cooldown, state.product_seen(key)\n            )",
        "                product_cooldown, state.product_seen(key) + 2\n            )",
    ),
    (
        "escalation: only the SQL layer escalates; the enforcement stays flat",
        POOL,
        "    if product_cooldown > 0 and state.seconds_since_product(listing_id) < _escalated(\n"
        "        product_cooldown, state.product_seen(listing_id)\n    ):",
        "    if product_cooldown > 0 and state.seconds_since_product(listing_id) < product_cooldown:",
    ),
    (
        # The floor that keeps `seen == 0` from disabling the check entirely.
        "escalation: the seen=0 floor is dropped, so an unseen product skips spacing",
        POOL,
        "    return int(cooldown) * max(1, int(seen))",
        "    return int(cooldown) * int(seen)",
    ),
    (
        # Off-by-one in the multiplier: the first repeat is delayed too, so the
        # configured cooldown no longer names a real gap.
        "escalation: the first repeat is delayed too, so the config names nothing",
        POOL,
        "    return int(cooldown) * max(1, int(seen))",
        "    return int(cooldown) * max(1, int(seen) + 1)",
    ),
    (
        # Exponential instead of linear. This is the design `_escalated`'s
        # docstring argues against: the allowance stops fitting inside the
        # window, so the effective cap silently drops below the configured one.
        "escalation: exponential backoff, so the allowance leaves the window",
        POOL,
        "    return int(cooldown) * max(1, int(seen))",
        "    return int(cooldown) * (2 ** max(0, int(seen) - 1))",
    ),
    (
        # Escalation put OUTSIDE the relaxation ladder, which is the cap's
        # treatment applied to a spacing rule. A thin catalogue goes blank.
        "escalation: escalates on the unrelaxed base, ignoring the ladder",
        POOL,
        "    return int(cooldown) * max(1, int(seen))",
        "    return max(int(cooldown), config.product_cooldown_seconds()) * max(1, int(seen))",
    ),
    (
        "router: the cap ignores the global config",
        ROUTER,
        "        product_cap=_scaled(\n            config.product_cap(),",
        "        product_cap=_scaled(\n            3,",
    ),
]


def run(root: Path) -> tuple[bool, str]:
    proc = subprocess.run(
        [PY, "-m", "pytest", *TESTS, "-q", "-p", "no:randomly", "--tb=no"],
        cwd=root, capture_output=True, text=True,
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr)


def failures(output: str) -> list[str]:
    return sorted(set(re.findall(r"^FAILED \S+::(\S+)", output, re.M)))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "repo"
        # Only what the tests import. Copying the whole worktree would drag in
        # node_modules.
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

        ok, out = run(base)
        if not ok:
            print("BASELINE IS RED — nothing below means anything")
            print(out[-4000:])
            return 1
        found = re.search(r"(\d+) passed", out)
        print(f"baseline: green ({found.group(1) if found else '?'} passed)\n")

        survivors = []
        for label, rel, find, replace in MUTANTS:
            target = base / rel
            original = target.read_text()
            # A mutant is either one (find, replace) or a tuple of them.
            edits = find if isinstance(find, tuple) else ((find, replace),)
            bad = [f"{n}x" for n, _ in ((original.count(f), f) for f, _ in edits) if n != 1]
            if bad:
                print(f"  BAD MUTANT  {label}\n              patterns matched {bad}, expected 1 each")
                survivors.append(label + "  (bad mutant)")
                continue
            mutated = original
            for f, r in edits:
                mutated = mutated.replace(f, r)
            target.write_text(mutated)
            try:
                passed, output = run(base)
            finally:
                target.write_text(original)
            expected = label.startswith("EXPECTED SURVIVOR")
            if passed:
                if expected:
                    print(f"  survived as designed  {label}")
                else:
                    print(f"  SURVIVED    {label}")
                    survivors.append(label)
            elif expected:
                print(f"  UNEXPECTEDLY KILLED  {label}\n"
                      f"              a test is now pinning an optimisation: {failures(output)}")
                survivors.append(label + "  (should have survived)")
            else:
                killers = failures(output)
                shown = ", ".join(killers[:3]) + (f" (+{len(killers) - 3} more)" if len(killers) > 3 else "")
                print(f"  killed      {label}\n              by {len(killers)}: {shown}")

        print()
        if survivors:
            print(f"{len(survivors)} SURVIVED of {len(MUTANTS)}:")
            for label in survivors:
                print(f"  - {label}")
            return 1
        # Counts the designed survivors separately. Printing "all N mutants killed"
        # when two of them deliberately survived is a sentence that gets quoted
        # later and is not true; the distinction between "nothing escaped" and
        # "nothing escaped unexpectedly" is the whole point of the EXPECTED
        # SURVIVOR labels.
        by_design = sum(1 for m in MUTANTS if m[0].startswith("EXPECTED SURVIVOR"))
        if by_design:
            print(f"all {len(MUTANTS)} mutants handled: "
                  f"{len(MUTANTS) - by_design} killed, "
                  f"{by_design} surviving as designed")
        else:
            print(f"all {len(MUTANTS)} mutants killed")
        return 0


if __name__ == "__main__":
    sys.exit(main())
