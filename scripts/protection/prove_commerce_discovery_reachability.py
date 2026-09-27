"""Would the reachability tests notice if the span were weakened?

Same standard as the earlier chapters. Every mutant must be killed, and the
mutant that matters most is the last-but-one: it restores the pre-fix code
exactly. If that one did not kill a large fraction of the new file, the file
would be describing behaviour that was already true.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Derived, never hardcoded. `REPO` is only ever *read*: `main` copies it into a
# fresh temporary directory and mutates the copy, so the working tree is never
# touched and two of these harnesses can safely run at once. An absolute path would
# still be wrong, though — hardcoded, this file would keep proving whatever sits in
# one particular checkout no matter which one you ran it from, quietly reporting
# someone else's source as green. `parents[2]` is the checkout this script lives in;
# `sys.executable` is the interpreter used to run it.
REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
TESTS = ["tests/commerce_discovery/test_the_catalogue_is_reachable.py"]

POOL = "services/commerce_discovery/pool.py"
EXPOSURE = "services/commerce_discovery/exposure.py"
ENGINE = "services/commerce_discovery/engine.py"

MUTANTS = [
    (
        # THE HEADLINE MUTANT: `rotation_offset` ignores the span. This is the
        # pre-fix code exactly.
        "span: rotation_offset ignores it — the true pre-fix offset space",
        EXPOSURE,
        "    if span and int(span) > 0:\n"
        "        # Ceiling division: the last partial batch still deserves a slot, or the\n"
        "        # tail of the catalogue is exactly the part that stays unreachable.\n"
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "    if False:\n        slots = slots",
    ),
    (
        "span: the engine stops passing it, so nothing reaches the deep end",
        ENGINE,
        "        rotation_offset=exposure.rotation_offset(\n"
        "            policy.subject_ref, span=pool.catalogue_span(cur)\n        ),",
        "        rotation_offset=exposure.rotation_offset(policy.subject_ref),",
    ),
    (
        "span: floor division, so the oldest partial batch stays dark",
        EXPOSURE,
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "        slots = max(slots, int(span) // max(1, batch))",
    ),
    (
        "span: the configured floor is dropped, so a tiny shop collapses to one offset",
        EXPOSURE,
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "        slots = -(-int(span) // max(1, batch))",
    ),
    (
        "span: capped at the old four slots, so the fix is cosmetic",
        EXPOSURE,
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "        slots = min(config.rotation_slots(), max(slots, -(-int(span) // max(1, batch))))",
    ),
    (
        "span: halved, so the back half of the catalogue stays dark",
        EXPOSURE,
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "        slots = max(slots, -(-int(span) // max(1, batch * 2)))",
    ),
    (
        "span: a failed count is cached, blinding the deep end for a rotation period",
        POOL,
        "    except Exception:\n"
        "        LOGGER.warning(\"COMMERCE_DISCOVERY_SPAN_COUNT_FAILED\", exc_info=True)\n"
        "        return 0",
        "    except Exception:\n"
        "        LOGGER.warning(\"COMMERCE_DISCOVERY_SPAN_COUNT_FAILED\", exc_info=True)\n"
        "        _SPAN_CACHE[0] = subject_module.now_utc().timestamp() + 3600\n"
        "        _SPAN_CACHE[1] = 0\n        return 0",
    ),
    (
        "span: the cache never expires, so the count is taken once per process",
        POOL,
        "    ttl = max(60, config.rotation_period_seconds() or 0)",
        "    ttl = 10 ** 9",
    ),
    (
        "span: the cache is not used at all — a COUNT on every request",
        POOL,
        "    if _SPAN_CACHE[0] > now:\n        return int(_SPAN_CACHE[1])",
        "    if False:\n        return int(_SPAN_CACHE[1])",
    ),
    (
        "span: counted on the real wall clock, so a fake-clock test can never expire it",
        POOL,
        "    now = subject_module.now_utc().timestamp()",
        "    import time\n\n    now = time.time()",
    ),
    (
        "span: an under-estimate — the gate's rejects are subtracted from the count",
        POOL,
        "            f\"WHERE {eligibility.candidate_sql()}\"",
        "            f\"WHERE {eligibility.candidate_sql()} AND l.id > 60\"",
    ),
    (
        # `catalogue_span` returns 0 both for "the count failed" and for "the
        # catalogue is empty", and `rotation_offset` must read that as *unknown*.
        # Taking it literally collapses every viewer onto offset 0.
        #
        # Note: mutating only the `if` guard to `span is not None` is NOT a valid
        # mutant — `max(slots, 0)` absorbs it and behaviour is identical. The
        # mutant has to drop the floor as well to actually change anything.
        "span: a zero span is believed, collapsing every viewer onto offset 0",
        EXPOSURE,
        "    if span and int(span) > 0:\n"
        "        # Ceiling division: the last partial batch still deserves a slot, or the\n"
        "        # tail of the catalogue is exactly the part that stays unreachable.\n"
        "        slots = max(slots, -(-int(span) // max(1, batch)))",
        "    if span is not None:\n        slots = -(-int(span) // max(1, batch))",
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
