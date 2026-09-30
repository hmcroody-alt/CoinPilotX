"""`scripts/pulse_home_os_audit.py` runs here, or it runs nowhere.

That audit is the only check on the route-scoped /pulse Home OS presentation: it
asserts the Home shell declares cache-busted versions of `pulse_home_os.css`,
`pulse_environment_engine.js` and `pulse_radio.js`, that the rendered page loads
the versions the shell declares, and that ~70 further CSS/JS selectors, Pulse
Radio controls and workflow links are still wired. Nothing invoked it. Not this
suite, not a workflow, not a docs step.

So it exited 1 for months and that cost nothing. Five of its assertions had
rotted: three `?v=` literals it pinned by hand, which every legitimate
cache-buster bump invalidated, and a `@keyframes pulseCityVehicle` rule that
4916856d8 deliberately deleted when it froze the decorative background
animations into static layers. The script was reporting a Home OS regression
that did not exist, to no one -- and a red that nobody reads is
indistinguishable from a green that measures nothing.

1890dbcd7 repaired those assertions: the tokens are now derived from bot.py and
the keyframe check is inverted to guard the perf decision. That fixed the
expectations but not the reason nobody noticed, which is this file's job. An
audit invoked by nothing rots again the same week and the next person to read it
learns the same non-fact.

Two details of this wrapper are load-bearing.

  * The audit INSERTs a `users` row, so it gets a throwaway sqlite database.
    `import bot` runs `init_db()` at module scope, so the target is chosen
    before any of the audit's own code executes -- there is no later point at
    which an override would still help, which is why the DSN is set in the
    environment here rather than anywhere inside the script.

  * It runs on `sys.executable`, the interpreter already running this suite. The
    system python3 has neither flask nor requests, and an audit that cannot
    import its own subject exits non-zero for a reason that is not a regression.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "pulse_home_os_audit.py"

# A floor, not a target. The audit's own `require()` prints one line per check,
# so a `main()` that returns 0 after an early exit -- or after someone deletes
# the assertion lists -- is caught here rather than read as a pass. The count was
# 77 when this was wired in; the number only needs to be large enough that
# gutting the audit cannot pass.
MINIMUM_CHECKS = 70


def _run_audit() -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        environment = dict(os.environ)
        environment["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'pulse_home_os_audit.db'}"
        return subprocess.run(
            [sys.executable, str(AUDIT)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=900,
        )


def test_the_pulse_home_os_audit_passes():
    assert AUDIT.is_file(), (
        f"{AUDIT.relative_to(ROOT)} is gone. If it moved, point this wrapper at "
        "the new path; the audit is the only coverage the /pulse Home OS shell "
        "has, and deleting the wrapper with it restores the state this file "
        "exists to end."
    )
    result = _run_audit()
    if result.returncode != 0:
        failed = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
        raise AssertionError(
            "scripts/pulse_home_os_audit.py failed:\n"
            + "\n".join(failed or ["(no FAIL lines; see output below)"])
            + "\n\nBefore changing the audit to match the page, decide which side "
            "is wrong. A cache-buster bump is no longer supposed to break it -- "
            "the `?v=` tokens are derived from bot.py, not pinned -- so a failure "
            "here is either a real regression in the Home OS shell or an "
            "expectation the product deliberately moved past.\n"
            f"\nexit={result.returncode}\n{result.stdout[-4000:]}\n{result.stderr[-2000:]}"
        )
    passed = sum(1 for line in result.stdout.splitlines() if line.startswith("PASS"))
    assert passed >= MINIMUM_CHECKS, (
        f"the audit exited 0 having run only {passed} checks, below the floor of "
        f"{MINIMUM_CHECKS}. Exit 0 with nothing measured is the failure this "
        "directory exists to prevent; if the audit legitimately shrank, move the "
        "floor in the same commit and say why."
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
