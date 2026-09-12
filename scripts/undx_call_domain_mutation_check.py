#!/usr/bin/env python3
"""Prove `tests/test_undx_call_domain.py` fails when the rule it guards is broken.

§50: a protection test that has never been seen to fail is a comment with a test
runner attached. Two of these tests were rewritten from substring checks to AST
checks because the substring version fired on prose. That rewrite is exactly the
kind that can go vacuous quietly, so each mutation below names the assertion it
is supposed to trip and the run fails if that assertion stays green.

Never mutates the working tree. Builds a sandbox of symlinks to the real repo and
replaces one file with a mutated copy, so the worst outcome of an interrupted run
is a stale directory under /tmp.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TARGET = "services/undx_call_domain.py"
TESTS = "tests/test_undx_call_domain.py"

#: Each mutation is (label, old, new, test that must fail).
#:
#: `expect` is the point of the exercise. A mutation that fails *some* test proves
#: the suite is not empty; a mutation that fails *the named* test proves the
#: assertion written for it is the one doing the work.
MUTATIONS = [
    (
        "promote a provider in the preference table",
        '_PREFERENCE: dict[str, tuple[str, ...]] = {}',
        '_PREFERENCE: dict[str, tuple[str, ...]] = {"SCAM_SHIELD": ("openai",)}',
        "test_no_provider_name_reaches_the_module_as_data",
    ),
    (
        "name a provider in a docstring only (must stay GREEN)",
        '    """Provider names this domain would rather try first, possibly none.',
        '    """Provider names this domain would rather try first, e.g. openai.',
        None,
    ),
    (
        "add a rank table",
        '_KNOWN: frozenset[str] = frozenset(CALL_DOMAINS)',
        '_KNOWN: frozenset[str] = frozenset(CALL_DOMAINS)\n'
        'DOMAIN_RANK = {name: i for i, name in enumerate(CALL_DOMAINS)}',
        "test_domains_do_not_rank",
    ),
    (
        "add a rank function",
        'def is_known(call_domain: str | None) -> bool:',
        'def rank(call_domain: str | None) -> int:\n'
        '    return CALL_DOMAINS.index(normalise(call_domain))\n\n\n'
        'def is_known(call_domain: str | None) -> bool:',
        "test_domains_do_not_rank",
    ),
    (
        "read a domain's tuple position as an ordinal",
        '    return normalise(call_domain) in _KNOWN',
        '    return CALL_DOMAINS.index(normalise(call_domain)) >= 0',
        "test_domains_do_not_rank",
    ),
    (
        "map a domain to a number",
        '_PREFERENCE: dict[str, tuple[str, ...]] = {}',
        '_PREFERENCE: dict[str, tuple[str, ...]] = {"GENERAL": 3}',
        "test_domains_do_not_rank",
    ),
    (
        "import privacy the way every other module in this repo does",
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom services import undx_privacy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "import privacy relatively",
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom . import undx_privacy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "import privacy under an innocent alias",
        'from __future__ import annotations',
        'from __future__ import annotations\n\nimport services.undx_privacy as policy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "reach the lane table through the router",
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom undx_router import PROVIDERS',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "name a privacy class in prose",
        '#: Unordered on purpose: there is no ladder here.',
        '#: Unordered on purpose: unlike CONFIDENTIAL, there is no ladder here.',
        "test_the_module_names_no_privacy_class_anywhere",
    ),
    (
        "expose a permission question",
        'def readiness() -> dict[str, object]:',
        'def may_route(call_domain: str | None) -> bool:\n    return True\n\n\n'
        'def readiness() -> dict[str, object]:',
        "test_no_public_function_answers_a_permission_question",
    ),
    (
        "fold a typo into the default",
        '    return str(call_domain).strip().upper().replace("-", "_").replace(" ", "_")',
        '    cleaned = str(call_domain).strip().upper().replace("-", "_").replace(" ", "_")\n'
        '    return cleaned if cleaned in _KNOWN else DEFAULT_CALL_DOMAIN',
        "test_a_typo_is_visible_rather_than_silently_default",
    ),
]


def build_sandbox(root: pathlib.Path) -> pathlib.Path:
    """Symlink the repo, except the one file that gets mutated."""
    sandbox = root / "repo"
    sandbox.mkdir()
    for entry in REPO.iterdir():
        if entry.name in {"services", ".git"}:
            continue
        (sandbox / entry.name).symlink_to(entry)
    services = sandbox / "services"
    services.mkdir()
    for entry in (REPO / "services").iterdir():
        if entry.name == "undx_call_domain.py":
            continue
        (services / entry.name).symlink_to(entry)
    shutil.copy2(REPO / TARGET, services / "undx_call_domain.py")
    return sandbox


def main() -> int:
    failures = []
    for label, old, new, expect in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp))
            path = sandbox / TARGET
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                failures.append(f"{label}: anchor matched {source.count(old)}x, expected 1")
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")

            env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header", "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=300,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: prose is not supposed to trip anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            print(f"{'ok ' if not failures or failures[-1].split(':')[0] != label else 'BAD'} {label}: {verdict}")

    print()
    if failures:
        print(f"{len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print(f"All {len(MUTATIONS)} mutations behaved as specified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
