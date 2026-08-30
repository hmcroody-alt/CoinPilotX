"""Prove the worker imports from the tree Railway will actually check out.

Stage 34 states the rule it is defending against in one line: *do not accept a dirty
local tree PASS with a clean Railway checkout FAIL.* The two differ in exactly one way
that matters here — the local tree contains untracked files, and the deployed checkout
does not. A module that was written but never ``git add``ed imports perfectly on the
machine that wrote it and does not exist on the machine that runs it, and the symptom in
production is a route pack that 404s or a worker that crashes on boot, hours later, with
a ``ModuleNotFoundError`` naming a file the author is looking at.

So this script builds the file set the *commit* would contain — tracked files at their
working-tree content, plus anything explicitly staged — materialises exactly that into a
temporary directory, and imports the worker there.

**Two independent failure modes, checked in one pass.** The tree is clean *and* the web
stack is denied. ``bot``, ``stripe`` and ``flask`` are made unimportable by a meta-path
finder, reproducing the constraint the Railway worker service runs under: it has the
repository and it has no reason to have the web application's dependency set. The denier
self-checks that it works before anything is trusted, because a blocker that silently
does nothing turns every assertion downstream into a vacuous pass.

Usage::

    python3 scripts/undx_clean_checkout_import_proof.py
    python3 scripts/undx_clean_checkout_import_proof.py --module services.undx_run_health

Exit codes: 0 proof passed, 1 a module failed to import, 2 the denier did not work,
3 a forbidden module was reached anyway, 4 the repository could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: What the Railway worker service must not need. See the module docstring in
#: ``tests/undx_agent/test_worker_substrate.py`` for why these three specifically.
FORBIDDEN = ("bot", "stripe", "flask")

#: The import surface of the worker process. The entrypoint first, then each module it
#: reaches that a route pack also reaches — those are the ones where an untracked file
#: hurts twice.
DEFAULT_MODULES = (
    "undx_worker",
    "services.undx_worker_runtime",
    "services.undx_agent_runs",
    "services.undx_mission_runtime",
    "services.undx_run_health",
)


def _git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", REPO, *args], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def committed_file_set() -> tuple[set[str], set[str]]:
    """(files that would be in the commit, untracked files that would not be).

    ``git ls-files`` is the tracked set. ``--cached`` catches anything already staged,
    which is how a newly added file joins the commit. Everything in ``--others`` and in
    neither of those is the gap this script exists to find.
    """
    tracked = set(_git("ls-files"))
    staged = set(_git("diff", "--cached", "--name-only"))
    others = set(_git("ls-files", "--others", "--exclude-standard"))
    included = tracked | staged
    return included, {path for path in others if path not in included}


def materialise(paths: set[str], destination: str) -> int:
    """Copy the commit's file set into an empty directory, preserving layout.

    Copied from the working tree rather than exported from ``HEAD`` on purpose: the
    commit being prepared includes uncommitted edits to tracked files, and a proof run
    against ``HEAD`` would be a proof about the previous release.
    """
    copied = 0
    for relative in sorted(paths):
        source = os.path.join(REPO, relative)
        if not os.path.isfile(source):
            # A staged deletion. Absent from the commit, so absent here.
            continue
        target = os.path.join(destination, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied


def _program(tree: str, modules: tuple[str, ...]) -> str:
    return textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {tree!r})

        class Denied:
            names = {FORBIDDEN!r}

            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in self.names:
                    raise ImportError("DENIED:" + name)
                return None

        sys.meta_path.insert(0, Denied())

        for forbidden in {FORBIDDEN!r}:
            try:
                __import__(forbidden)
            except ImportError:
                pass
            else:
                print("BLOCKER_INEFFECTIVE:" + forbidden)
                raise SystemExit(2)

        imported = []
        for name in {modules!r}:
            try:
                __import__(name)
            except BaseException as exc:
                print(json.dumps({{"module": name, "error": type(exc).__name__,
                                   "detail": str(exc)[:400]}}))
                raise SystemExit(1)
            imported.append(name)

        for forbidden in {FORBIDDEN!r}:
            if forbidden in sys.modules:
                print("LEAKED:" + forbidden)
                raise SystemExit(3)

        print(json.dumps({{"imported": imported}}))
        """
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", action="append", default=[],
                        help="Extra module to import in the clean tree.")
    parser.add_argument("--keep", action="store_true",
                        help="Leave the temporary tree in place for inspection.")
    args = parser.parse_args()

    modules = DEFAULT_MODULES + tuple(args.module)

    try:
        included, excluded = committed_file_set()
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL could not read the repository: {exc}")
        return 4

    tree = tempfile.mkdtemp(prefix="undx_clean_checkout_")
    try:
        copied = materialise(included, tree)
        print(f"tree={tree} files={copied} untracked_excluded={len(excluded)}")
        # Printed whether or not the import succeeds. An untracked Python file is worth
        # seeing even on a pass, because the next module to import it will not be so
        # lucky.
        stragglers = sorted(path for path in excluded if path.endswith(".py"))
        for path in stragglers:
            print(f"  UNTRACKED (absent from the deployed checkout): {path}")

        result = subprocess.run(
            [sys.executable, "-c", _program(tree, modules)],
            capture_output=True, text=True, timeout=300, cwd=tree,
        )
        output = (result.stdout or "").strip()
        if result.returncode == 0:
            names = json.loads(output.splitlines()[-1])["imported"]
            print(f"PASS imported {len(names)} modules from a clean tree "
                  f"with {', '.join(FORBIDDEN)} denied")
            for name in names:
                print(f"  ok {name}")
            return 0
        print(f"FAIL exit={result.returncode}")
        print(output or (result.stderr or "").strip()[:2000])
        return result.returncode
    finally:
        if args.keep:
            print(f"kept {tree}")
        else:
            shutil.rmtree(tree, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
