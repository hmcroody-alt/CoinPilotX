"""A test file that no workflow runs is documentation with a misleading name.

When PR #26 merged, its nine checks were green and not one of them had loaded
the five regression tests it added. The measurement behind issue #28:

    tests/**/test_*.py                              736
      tests/protection/        (run by the suite)    51
      tests/pulse_control_plane/ (capability-drift)   4
      named in crypto-alert-persistence.yml          10
                                                ---------
      executed by some workflow                      65

The issue counted 733/62; the tree has since grown by three files and
tests/protection/ by three, so the gap is unchanged. route-contract.yml and
web-build.yml name one file each as well, but both already live in
tests/protection/, so they add nothing to the union.

The other 671 were run by nothing. That is worse than a plain gap, because the
green tick is actively misleading: the natural reading of "9 checks passed" on
a PR that adds tests is that the tests passed.

``tests/protection/_runner.py`` already documents this failure one level down --
a suite nobody runs "consumes the attention that would otherwise go to real
verification" -- and fixed it *inside* ``tests/protection/``. Nothing fixed it
outside. This file is that same property for the tree as a whole.

THE SHAPE
---------
Every discovered test file must be in exactly one of three states:

  executed  -- some workflow runs it. Computed from the workflows, never
               asserted by hand, so the claim cannot go stale.
  quarantined -- listed in the manifest with a reason. Honest, visible debt.
  neither   -- a failure. This is the state a newly added file lands in, which
               is the whole point: a new test cannot join the unrun pile in
               silence.

WHY THE EXECUTED SET IS COMPUTED, NOT LISTED
--------------------------------------------
A hand-written "these are covered" list is a second thing to keep in sync, and
it fails in the direction that hides the problem. So the executed set is
derived from ``.github/workflows/*.yml``, with two details that are easy to get
wrong and both of which flip the answer:

1. Only ``run:`` bodies execute anything. A ``paths:`` filter is a *trigger*.
   ``crypto-alert-persistence.yml`` lists ``tests/test_crypto_alert*`` under
   ``paths:`` and names ten explicit files under ``run:``. Counting the glob
   would credit files nothing runs -- and it would do so for exactly the
   directory where glob and reality diverge.

2. Execution is sometimes indirect. ``realtime-audio.yml`` runs
   ``scripts/protection/run_protection_suite.py``, which *discovers*
   ``tests/protection/test_*.py``. That indirection is followed by reading the
   glob out of the runner rather than hardcoding the directory, so moving the
   suite directory cannot silently unwatch it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
MANIFEST = ROOT / "config" / "ci_test_manifest.json"

# A path token that looks like a test file or a tests/ directory.
_TEST_PATH = re.compile(r"(?<![\w/.-])tests/[\w./*-]+")
# `run:` opens a script body; everything more-indented than it belongs to it.
_RUN_KEY = re.compile(r"^(\s*)(?:-\s+)?run:\s*(\S.*)?$")
# The suite runner's own glob, read rather than assumed.
_SUITE_GLOB = re.compile(r"SUITE_DIR\s*=\s*ROOT\s*/\s*[\"']([^\"']+)[\"']")


def discovered_test_files() -> set[str]:
    """Every ``tests/**/test_*.py``, as repo-relative posix paths."""
    return {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "tests").rglob("test_*.py")
    }


def _run_bodies(text: str) -> list[str]:
    """The script bodies of every ``run:`` step, and nothing else.

    Deliberately a small indentation walk rather than a YAML parse: the thing
    being extracted is shell text, and the distinction that matters -- ``run:``
    versus ``paths:`` -- is structural, not semantic.
    """
    bodies: list[str] = []
    current: list[str] | None = None
    base = 0
    for line in text.splitlines():
        match = _RUN_KEY.match(line)
        if match:
            if current is not None:
                bodies.append("\n".join(current))
            base = len(match.group(1))
            inline = match.group(2) or ""
            current = [inline] if inline not in ("", "|", ">", "|-", ">-") else []
            continue
        if current is None:
            continue
        if not line.strip():
            current.append(line)
            continue
        if len(line) - len(line.lstrip()) <= base:
            bodies.append("\n".join(current))
            current = None
        else:
            current.append(line)
    if current is not None:
        bodies.append("\n".join(current))
    return bodies


def _expand(token: str) -> set[str]:
    """Turn one path token from a run body into the test files it executes."""
    target = ROOT / token
    if target.is_file():
        return {token} if target.name.startswith("test_") else set()
    if target.is_dir():
        return {
            path.relative_to(ROOT).as_posix()
            for path in target.rglob("test_*.py")
        }
    return set()


def _suite_runner_coverage() -> set[str]:
    """Files ``run_protection_suite.py`` discovers, by reading its own glob.

    Two files in ``tests/protection/`` define no ``test_*`` function at all --
    they are script-style, with ``expect()`` and a ``main()``. pytest collects
    nothing from them. The suite runner executes them as subprocesses and fails
    when one exits 0 having run zero checks, so they are genuinely covered, and
    a gate that only understood pytest collection would call them unrun.
    """
    runner = ROOT / "scripts" / "protection" / "run_protection_suite.py"
    if not runner.is_file():
        return set()
    match = _SUITE_GLOB.search(runner.read_text(encoding="utf-8"))
    if not match:
        return set()
    return _expand(match.group(1))


def executed_by_ci() -> dict[str, set[str]]:
    """Map workflow filename -> the test files its ``run:`` steps execute."""
    coverage: dict[str, set[str]] = {}
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        files: set[str] = set()
        for body in _run_bodies(text):
            for token in _TEST_PATH.findall(body):
                files |= _expand(token.rstrip("/") if token.endswith("/") else token)
            if "run_protection_suite.py" in body:
                files |= _suite_runner_coverage()
            if MANIFEST.name in body:
                files |= set(_manifest()["run"])
        if files:
            coverage[workflow.name] = files
    return coverage


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_inputs_exist():
    """Every other assertion is "these account for each other".

    Delete an input and they account for each other trivially, which is the
    failure mode this directory exists to prevent.
    """
    assert MANIFEST.is_file(), f"{MANIFEST.relative_to(ROOT)} is missing"
    assert WORKFLOWS.is_dir()
    discovered = discovered_test_files()
    assert len(discovered) > 600, (
        f"only {len(discovered)} test files discovered; this gate was written "
        "when there were 736. A collapse here means the walk broke, not that "
        "the tests were deleted."
    )


def test_every_test_file_is_either_run_or_quarantined():
    """The gate. A new test file lands in neither bucket, and that fails."""
    manifest = _manifest()
    discovered = discovered_test_files()
    executed = set().union(*executed_by_ci().values())
    quarantined = set(manifest["quarantined"])

    unaccounted = sorted(discovered - executed - quarantined)
    assert not unaccounted, (
        f"{len(unaccounted)} test file(s) are run by no workflow and not "
        f"quarantined: {unaccounted[:10]}"
        + ("..." if len(unaccounted) > 10 else "")
        + ". Add them to the 'run' list in config/ci_test_manifest.json so CI "
        "executes them, or to 'quarantined' with a reason. A test file that "
        "runs nowhere is documentation with a misleading name."
    )


def test_nothing_is_both_quarantined_and_run():
    manifest = _manifest()
    overlap = sorted(set(manifest["run"]) & set(manifest["quarantined"]))
    assert not overlap, (
        f"quarantined but also scheduled to run: {overlap}. Quarantine is the "
        "list that has to shrink; a file in both makes its size a lie."
    )


def test_every_quarantine_entry_gives_a_reason():
    """"Fails" is not a reason. The next person needs to know what to do."""
    manifest = _manifest()
    thin = sorted(
        path for path, reason in manifest["quarantined"].items()
        if len(str(reason).strip()) < 25
    )
    assert not thin, (
        f"quarantine entries with no usable reason: {thin}. An entry nobody "
        "can act on never leaves the list."
    )


def test_the_quarantine_only_shrinks():
    """A ceiling, so the honest debt cannot quietly become bigger honest debt.

    The number is deliberately the count at the moment the list was written. It
    is meant to be edited downward when files are fixed, and a change upward
    should be a visible, argued decision rather than a side effect.
    """
    manifest = _manifest()
    ceiling = manifest["quarantine_ceiling"]
    actual = len(manifest["quarantined"])
    assert actual <= ceiling, (
        f"{actual} quarantined files, ceiling is {ceiling}. Fix the file, or "
        "raise the ceiling in the same commit that explains why."
    )


def test_every_manifest_path_still_exists():
    """A stale entry is how a list stops describing the repo."""
    manifest = _manifest()
    discovered = discovered_test_files()
    listed = set(manifest["run"]) | set(manifest["quarantined"])
    missing = sorted(listed - discovered)
    assert not missing, (
        f"manifest names files that do not exist: {missing}. Renamed or "
        "deleted files leave entries that make the counts wrong in the "
        "flattering direction."
    )


def test_a_paths_trigger_is_not_execution():
    """The trap that would silently make this gate vacuous.

    ``crypto-alert-persistence.yml`` lists ``tests/test_crypto_alert*`` under
    ``paths:`` -- a trigger -- and names ten explicit files under ``run:``. A
    scanner that read the whole file would credit the glob and call every
    matching file covered. Here the glob and the explicit list happen to
    overlap, so the error would be invisible in *this* repo today and would
    surface the first time someone added a ``tests/foo*`` trigger.
    """
    sample = (
        "on:\n"
        "  pull_request:\n"
        "    paths:\n"
        "      - 'tests/quarantined_by_trigger_only.py'\n"
        "jobs:\n"
        "  j:\n"
        "    steps:\n"
        "      - run: python -m pytest tests/really_executed.py\n"
    )
    bodies = "\n".join(_run_bodies(sample))
    assert "tests/really_executed.py" in bodies
    assert "quarantined_by_trigger_only" not in bodies


def test_the_indirect_runner_is_followed():
    """tests/protection/ is executed via a script, not named in a workflow.

    If this ever returns nothing, 49 files silently move from "executed" to
    "unaccounted" and the gate would demand they all be quarantined -- which is
    loud, and correct, but the cause would be this function, not the tests.
    """
    covered = _suite_runner_coverage()
    assert len(covered) > 20, (
        f"run_protection_suite.py resolved to {len(covered)} files. Its "
        "SUITE_DIR glob is how tests/protection/ earns its coverage; if the "
        "runner changed shape, teach _suite_runner_coverage the new one."
    )
    assert Path(__file__).relative_to(ROOT).as_posix() in covered, (
        "this file is in tests/protection/ and the resolver cannot see it, "
        "which means it cannot see any of them"
    )


def test_the_gate_can_actually_fail():
    """Pin the accounting itself, so a refactor cannot make it vacuous."""
    discovered = {"tests/a.py", "tests/b.py", "tests/c.py"}
    executed = {"tests/a.py"}
    quarantined = {"tests/b.py"}
    assert sorted(discovered - executed - quarantined) == ["tests/c.py"]
    # And the shape that must NOT be reported: fully accounted for.
    assert not (discovered - executed - {"tests/b.py", "tests/c.py"})


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
