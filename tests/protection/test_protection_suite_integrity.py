"""The protection suite must actually run, and CI must actually run the suite.

This file guards the guards. It exists because two independent holes let
protection checks report success without executing:

  * `scripts/protection/run_protection_suite.py` named three files explicitly.
    Eight other suites in this directory - covering admin action accountability,
    metric truthfulness, the environment contract, LiveKit token grants and the
    real-time audio architecture boundary - were run by nothing at all.

  * `.github/workflows/realtime-audio.yml` ran the LiveKit suites with
    `python3 -m unittest tests.protection.test_livestream_audio_token_grants`.
    That module defines module-level functions and no `TestCase`, so unittest
    collected nothing and printed "Ran 0 tests ... OK". The job that guards who
    is allowed to publish a microphone track was green while measuring nothing.

Both failures share a shape: a green signal that no change to the system could
ever turn red. That is the same defect class as a dashboard metric that renders
a confident zero, and it is worth a test of its own.
"""

import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[2]
SUITE_DIR = ROOT / "tests" / "protection"
RUNNER = ROOT / "scripts" / "protection" / "run_protection_suite.py"
WORKFLOW = ROOT / ".github" / "workflows" / "realtime-audio.yml"


def _suite_files():
    return sorted(SUITE_DIR.glob("test_*.py"))


def test_every_protection_suite_is_executable_on_its_own():
    """No `__main__` guard means `python3 <file>` exits 0 having done nothing."""
    silent = [
        path.name for path in _suite_files()
        if '__main__' not in path.read_text(encoding="utf-8")
    ]
    assert not silent, (
        "These protection suites cannot be executed as scripts, so the runner "
        f"would record zero checks for them: {silent}. Add the _runner.py guard."
    )


def test_every_protection_suite_reports_how_many_checks_it_ran():
    """A count is what lets the runner distinguish 'passed' from 'did nothing'."""
    quiet = []
    for path in _suite_files():
        text = path.read_text(encoding="utf-8")
        if "PROTECTION_TESTS_RUN" in text or "run_module_tests" in text or "unittest.main()" in text:
            continue
        quiet.append(path.name)
    assert not quiet, (
        "These suites report no executed-check count, so a silent no-op would "
        f"look identical to a pass: {quiet}"
    )


def test_the_runner_discovers_suites_rather_than_listing_them():
    source = RUNNER.read_text(encoding="utf-8")
    assert 'glob("test_*.py")' in source, (
        "The runner must discover suites. A hand-maintained list is how eight "
        "suites came to be run by nothing; adding a protection test should be "
        "sufficient to have it enforced."
    )
    assert "count == 0" in source, (
        "The runner must treat a suite that exits 0 having run zero checks as a "
        "failure. Without that, `python3 -m unittest` on a file with no TestCase "
        "reports OK forever."
    )


def test_ci_runs_the_runner_and_not_a_hand_listed_unittest_invocation():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "scripts/protection/run_protection_suite.py" in workflow, (
        "The audio workflow must invoke the protection runner."
    )
    # The old invocation collected nothing from two of its three modules.
    live = [
        line for line in workflow.splitlines()
        if "python3 -m unittest" in line and not line.strip().startswith("#")
    ]
    assert not live, (
        "An uncommented `python3 -m unittest` step is back. unittest collects "
        "only TestCase subclasses; most suites here are module-level functions, "
        f"so the step passes while running nothing: {live}"
    )


def test_the_backend_protection_job_is_not_gated_on_audio_path_changes():
    """The suite now covers CSRF, audit rows and env; it must run on every PR."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    start = workflow.index("\n  backend:")
    block = workflow[start : workflow.index("\n  native-build:", start)]
    condition = re.search(r"^\s{4}if:.*$", block, re.M)
    assert condition is None, (
        "The backend protection job is conditional again: "
        f"{condition.group(0).strip() if condition else ''}. A pull request that "
        "removes an admin audit row or a CSRF check touches no audio path, so a "
        "detect-gated job would skip exactly the change it should catch."
    )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
