"""Would the multi-source tests notice if retrieval went back to one question?

Same standard as the earlier chapters. Every mutant must be killed, and the one
that matters most is the headline: it restores the pre-sources code exactly, by
making `_sources` return the single rotating source it used to. If that mutant
did not kill a large fraction of the new file, the file would be describing
behaviour that was already true — which is the trap the whole package fell into,
since all 415 pre-existing tests passed unchanged both before and after the
sources were added.

The second family of mutants is about restraint rather than capability. A
targeted source that widens instead of narrowing, or that skips the frequency
controls, or that takes the whole pool, is a worse outcome than no sources at
all: it would put personalisation ahead of the anti-repetition guarantee this
engine exists to provide.
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
TESTS = ["tests/commerce_discovery/test_retrieval_asks_several_questions.py"]

POOL = "services/commerce_discovery/pool.py"
ENGINE = "services/commerce_discovery/engine.py"
METRICS = "services/commerce_discovery/metrics.py"

MUTANTS = [
    (
        # THE HEADLINE MUTANT: `_sources` returns only the rotating source. This is
        # the pre-sources code path exactly — one viewer-blind ordering, which
        # measured 14.1% cameras for a viewer whose every click was a camera.
        "sources: only rotation is ever asked — the true pre-fix retrieval",
        POOL,
        "    found.append(_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)))\n"
        "    return tuple(found)",
        "    return (_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)),)",
    ),
    (
        "sources: the engine stops passing interests, so affinity never activates",
        ENGINE,
        "        interests=profile.get(\"viewed_categories\", ()),",
        "        interests=(),",
    ),
    (
        "sources: the engine stops passing follows, so the followed source is dead",
        ENGINE,
        "        followed_sellers=tuple(profile.get(\"followed_sellers\", ()) or ()),",
        "        followed_sellers=(),",
    ),
    (
        # The reason the profile read moved above `pool.build`. Reading it after
        # retrieval leaves it feeding the ranker only, and a ranker can only
        # reorder rows retrieval already returned.
        "sources: the profile is read but the pool is built before it exists",
        ENGINE,
        "        interests=profile.get(\"viewed_categories\", ()),",
        "        interests=({} or {}).get(\"viewed_categories\", ()),",
    ),
    (
        "sources: the affinity clause widens instead of narrowing",
        POOL,
        "            clause=f\"AND LOWER(TRIM(COALESCE(l.category,''))) IN ({marks})\",",
        "            clause=f\"AND (1=1 OR LOWER(TRIM(COALESCE(l.category,''))) IN ({marks}))\",",
    ),
    (
        "sources: affinity takes the whole pool, crowding out every other question",
        POOL,
        "            share=0.35,",
        "            share=1.0,",
    ),
    (
        # The quota arithmetic. Without the share, every source is handed the full
        # target and the first one to answer fills the pool.
        "sources: shares are ignored, so the first source fills the pool",
        POOL,
        "        if source.share > 0:\n"
        "            quota = min(target, max(1, int(target * source.share)))\n"
        "        else:\n"
        "            quota = target",
        "        quota = target",
    ),
    (
        # The whole point of a *candidate* set. A source must not be able to skip
        # the exposure controls on the way in.
        "sources: a targeted row skips the reject pass entirely",
        POOL,
        "                row[\"candidate_source\"] = source.name",
        "                row[\"candidate_source\"] = source.name\n"
        "                accepted.append(row)\n"
        "                contributed[source.name] = contributed.get(source.name, 0) + 1\n"
        "                continue",
    ),
    (
        # EXPECTED SURVIVOR, and a load-bearing one. `_hard_exclusions` is an
        # optimisation — it removes rows in SQL that `_reject` would remove in
        # Python anyway — and the module says so: under-exclusion is cost-only,
        # over-exclusion silently destroys inventory. So dropping it for the
        # targeted sources must change the query *cost* and not the accepted rows.
        # A test that failed here would mean correctness had migrated into the
        # optimisation, which is the dangerous direction.
        "EXPECTED SURVIVOR: the hard exclusions are dropped for targeted questions",
        POOL,
        "                    cur, viewer_user_id, offset, batch_size, hard_excluded,",
        "                    cur, viewer_user_id, offset, batch_size,\n"
        "                    hard_excluded if source.name == SOURCE_ROTATION else (),",
    ),
    (
        # Circularity. The viewer's own clicks made those exact listings the
        # busiest rows in the window, so "trending" partly meant "you clicked it"
        # and became a fourth route back to what the viewer just engaged with.
        "trending: the viewer's own events are counted, so trending means 'you clicked it'",
        POOL,
        "            + (\"AND COALESCE(subject_ref,'')<>? \" if own else \"\")",
        "            + \"\"",
    ),
    (
        "trending: the exclusion is inverted, so trending means *only* your own clicks",
        POOL,
        "            + (\"AND COALESCE(subject_ref,'')<>? \" if own else \"\")",
        "            + (\"AND COALESCE(subject_ref,'')=? \" if own else \"\")",
    ),
    (
        "trending: the window is ignored, so a month-old fad trends forever",
        POOL,
        "        \"WHERE action IN ('click','product_view','add_to_cart') AND event_at>? \"",
        "        \"WHERE action IN ('click','product_view','add_to_cart') AND event_at>'' \"",
    ),
    (
        "trending: a failed query raises instead of removing the source",
        POOL,
        "    except Exception:\n"
        "        LOGGER.debug(\"COMMERCE_DISCOVERY_TRENDING_UNAVAILABLE\", exc_info=True)\n"
        "        return ()",
        "    except Exception:\n"
        "        raise",
    ),
    (
        "sources: the trending IN clause is unbounded in the other direction — id 0 admitted",
        POOL,
        "            clause=f\"AND l.id IN ({marks})\",",
        "            clause=f\"AND (l.id IN ({marks}) OR 1=1)\",",
    ),
    (
        # Provenance. A label that is not a fact is worse than no label, because an
        # operator will believe it while debugging.
        "provenance: every row is labelled rotation regardless of which question found it",
        POOL,
        "                row[\"candidate_source\"] = source.name",
        "                row[\"candidate_source\"] = SOURCE_ROTATION",
    ),
    (
        "provenance: the counts are not kept, so the mix is always empty",
        POOL,
        "                contributed[source.name] = contributed.get(source.name, 0) + 1",
        "                pass",
    ),
    (
        # Absent-vs-zero. A source that ran and found nothing is a different
        # operational fact from a source the viewer cannot drive at all: the first
        # is an inventory question, the second a cold-start question.
        "provenance: a source asked and found nothing is absent rather than zero",
        POOL,
        "        contributed.setdefault(source.name, 0)",
        "        pass",
    ),
    (
        "provenance: the mix is not reported when the pool came back empty",
        ENGINE,
        "    metrics.observe_sources(built.sources, surface=surface)",
        "    pass",
    ),
    (
        "provenance: an empty mix is logged as a line, so 'no sources' reads as a source",
        METRICS,
        "    if not mix:\n        return \"\"",
        "    if False:\n        return \"\"",
    ),
    (
        # The opt-out has to reach the *fetch*, not merely the scoring. A viewer who
        # asked not to be profiled and is still profiled at retrieval has not opted
        # out of anything that matters.
        "privacy: the opt-out no longer gates the profile read that feeds retrieval",
        ENGINE,
        "    profile = (\n"
        "        _interest_profile(cur, user_id, subject_ref=policy.subject_ref)\n"
        "        if policy.personalized\n"
        "        else {}\n    )",
        "    profile = _interest_profile(cur, user_id, subject_ref=policy.subject_ref)",
    ),
    (
        # EXPECTED SURVIVOR. Three extra questions must not multiply the query
        # bill, and two guards hold that: a per-source budget, and an absolute
        # ceiling on the outer loop. The ceiling is the binding one — removing the
        # per-source budget entirely still leaves every source capped by it, so
        # behaviour is unchanged and no test can or should fail. The companion
        # mutant below removes the *ceiling*, which is killed. Between the two,
        # each guard is covered; neither alone is a valid mutant.
        "EXPECTED SURVIVOR: the per-source budget is removed, ceiling still binds",
        POOL,
        "        budget = max_batches if source.name == SOURCE_ROTATION else targeted_batches",
        "        budget = 10 ** 6",
    ),
    (
        "cost: the absolute ceiling is removed and every budget is the full one",
        POOL,
        "        while spent < budget and batches < max_batches + 3 * targeted_batches:",
        "        while spent < (max_batches if True else budget):",
    ),
    (
        # Bounding the IN clauses. A viewer with a long history must not be able to
        # turn one retrieval question into a several-hundred-parameter statement.
        "cost: source terms are unbounded, so a long history becomes a huge statement",
        POOL,
        "        if len(out) >= MAX_SOURCE_TERMS:\n            break",
        "        if False:\n            break",
    ),
    (
        # Note: making `_sources` return *nothing* for a viewer with no history is
        # NOT a valid mutant — `_scan`'s `sources or (rotation,)` fallback absorbs
        # it and behaviour is identical. The guarantee is deliberately held twice.
        # So the mutant probes the reachable half instead: rotation is dropped as
        # soon as the viewer has any history at all, which is the version where a
        # viewer who has clicked once is only ever shown targeted inventory again.
        "sources: rotation is dropped for any viewer who has a targeted source",
        POOL,
        "    found.append(_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)))\n"
        "    return tuple(found)",
        "    if found:\n"
        "        return tuple(found)\n"
        "    found.append(_Source(name=SOURCE_ROTATION, rotates=bool(rotation_offset)))\n"
        "    return tuple(found)",
    ),
    (
        "sources: subject_ref is not taken off the policy, so the exclusion never applies",
        POOL,
        "        subject_ref=getattr(policy, \"subject_ref\", \"\") or \"\",",
        "        subject_ref=\"\",",
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
            count = original.count(find)
            if count != 1:
                print(f"  BAD MUTANT  {label}\n              pattern matched {count}x, expected 1")
                survivors.append(label + "  (bad mutant)")
                continue
            target.write_text(original.replace(find, replace))
            try:
                passed, output = run(base)
            finally:
                target.write_text(original)
            expected = label.startswith("EXPECTED SURVIVOR")
            if passed and expected:
                print(f"  survived as designed  {label}")
            elif passed:
                print(f"  SURVIVED    {label}")
                survivors.append(label)
            elif expected:
                print(f"  UNEXPECTEDLY KILLED  {label}\n              by {failures(output)}")
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
