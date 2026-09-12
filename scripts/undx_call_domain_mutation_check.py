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
ROUTER = "undx_router.py"
TESTS = "tests/test_undx_call_domain.py"

#: Each mutation is (label, file, old, new, test that must fail).
#:
#: `expect` is the point of the exercise. A mutation that fails *some* test proves
#: the suite is not empty; a mutation that fails *the named* test proves the
#: assertion written for it is the one doing the work.
#:
#: The rule spans two files, so the mutations do too. Keeping the vocabulary out of
#: `undx_call_domain` is worth nothing if `undx_router` reads a preference and
#: concatenates it onto the admitted list, so the `ROUTER` mutations below break the
#: reorder-only property directly.
MUTATIONS = [
    (
        "promote a provider in the preference table",
        TARGET,
        '_PREFERENCE: dict[str, tuple[str, ...]] = {}',
        '_PREFERENCE: dict[str, tuple[str, ...]] = {"SCAM_SHIELD": ("openai",)}',
        "test_no_provider_name_reaches_the_module_as_data",
    ),
    (
        "name a provider in a docstring only (must stay GREEN)",
        TARGET,
        '    """Provider names this domain would rather try first, possibly none.',
        '    """Provider names this domain would rather try first, e.g. openai.',
        None,
    ),
    (
        "add a rank table",
        TARGET,
        '_KNOWN: frozenset[str] = frozenset(CALL_DOMAINS)',
        '_KNOWN: frozenset[str] = frozenset(CALL_DOMAINS)\n'
        'DOMAIN_RANK = {name: i for i, name in enumerate(CALL_DOMAINS)}',
        "test_domains_do_not_rank",
    ),
    (
        "add a rank function",
        TARGET,
        'def is_known(call_domain: str | None) -> bool:',
        'def rank(call_domain: str | None) -> int:\n'
        '    return CALL_DOMAINS.index(normalise(call_domain))\n\n\n'
        'def is_known(call_domain: str | None) -> bool:',
        "test_domains_do_not_rank",
    ),
    (
        "read a domain's tuple position as an ordinal",
        TARGET,
        '    return normalise(call_domain) in _KNOWN',
        '    return CALL_DOMAINS.index(normalise(call_domain)) >= 0',
        "test_domains_do_not_rank",
    ),
    (
        "map a domain to a number",
        TARGET,
        '_PREFERENCE: dict[str, tuple[str, ...]] = {}',
        '_PREFERENCE: dict[str, tuple[str, ...]] = {"GENERAL": 3}',
        "test_domains_do_not_rank",
    ),
    (
        "import privacy the way every other module in this repo does",
        TARGET,
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom services import undx_privacy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "import privacy relatively",
        TARGET,
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom . import undx_privacy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "import privacy under an innocent alias",
        TARGET,
        'from __future__ import annotations',
        'from __future__ import annotations\n\nimport services.undx_privacy as policy',
        "test_the_module_does_not_import_privacy",
    ),
    (
        "reach the lane table through the router (blocked by the import graph, not the test)",
        TARGET,
        'from __future__ import annotations',
        'from __future__ import annotations\n\nfrom undx_router import PROVIDERS',
        # Not the test name, and that is the finding. `undx_router` imports
        # `undx_call_domain`, so importing the router back is a cycle Python refuses
        # before any test runs. The ban is now enforced twice, and the stronger of
        # the two is the dependency direction. The test still earns its place: it
        # covers `undx_privacy` (three mutations above), and it is what would catch
        # this if the router ever stopped importing the domain module.
        "ImportError",
    ),
    (
        "name a privacy class in prose",
        TARGET,
        '#: Unordered on purpose: there is no ladder here.',
        '#: Unordered on purpose: unlike CONFIDENTIAL, there is no ladder here.',
        "test_the_module_names_no_privacy_class_anywhere",
    ),
    (
        "expose a permission question",
        TARGET,
        'def readiness() -> dict[str, object]:',
        'def may_route(call_domain: str | None) -> bool:\n    return True\n\n\n'
        'def readiness() -> dict[str, object]:',
        "test_no_public_function_answers_a_permission_question",
    ),
    (
        "fold a typo into the default",
        TARGET,
        '    return str(call_domain).strip().upper().replace("-", "_").replace(" ", "_")',
        '    cleaned = str(call_domain).strip().upper().replace("-", "_").replace(" ", "_")\n'
        '    return cleaned if cleaned in _KNOWN else DEFAULT_CALL_DOMAIN',
        "test_a_typo_is_visible_rather_than_silently_default",
    ),
    (
        "let a preference add a provider instead of moving one",
        ROUTER,
        '    front = [provider for provider in preferred if provider in ordered]',
        '    front = list(preferred)',
        "test_a_preference_cannot_add_a_provider_that_privacy_refused",
    ),
    (
        "let a preference replace the admitted list outright",
        ROUTER,
        '    front = [provider for provider in preferred if provider in ordered]\n'
        '    return front + [provider for provider in ordered if provider not in front]',
        '    return list(preferred)',
        "test_the_result_is_always_a_permutation_of_what_was_admitted",
    ),
    (
        "keep a preferred provider in both positions",
        ROUTER,
        '    return front + [provider for provider in ordered if provider not in front]',
        '    return front + list(ordered)',
        "test_the_result_is_always_a_permutation_of_what_was_admitted",
    ),
    (
        "stop consulting the declared domain in the structured path",
        ROUTER,
        '            else [default_provider()]\n'
        '    ordered = _domain_ordered(ordered, call_domain)\n',
        '            else [default_provider()]\n',
        "test_the_domain_is_consulted_after_the_privacy_ceiling",
    ),
    (
        "stop consulting the declared domain in the mission path",
        ROUTER,
        '    ordered = provider_priority(classification) if router_enabled() else ["openai"]\n'
        '    ordered = _domain_ordered(ordered, call_domain)\n',
        '    ordered = provider_priority(classification) if router_enabled() else ["openai"]\n',
        "test_the_domain_is_consulted_after_the_privacy_ceiling",
    ),
    (
        # Re-anchored. The original anchor ended at `) -> dict[str, Any]:`, which assumed
        # `call_domain` was the last parameter of `route_structured_request`. A later phase
        # added `history`, `require_json` and `json_schema` after it, so the anchor matched
        # 0x and this mutation silently stopped being run — reported as a harness problem
        # rather than as a pass, which is the only reason it was noticed. An anchor pinned
        # to an exact adjacency has a shelf life of one edit to its neighbourhood; this one
        # holds the two lines it is actually about plus the next, and no closing paren.
        "drop the declared domain from the structured signature",
        ROUTER,
        '    privacy_class: str | None = None,\n    call_domain: str | None = None,\n    history: Any = None,',
        '    privacy_class: str | None = None,\n    history: Any = None,',
        "test_both_router_entry_points_accept_a_declared_domain",
    ),
]


def build_sandbox(root: pathlib.Path, target: str) -> pathlib.Path:
    """Symlink the repo, except the one file that gets mutated.

    Every directory on the path to the target becomes real; everything beside it at each
    level stays a symlink. Nothing under the repo is opened for writing at any point, and
    the assertion at the end is what makes that sentence true rather than intended.

    **It was not true before.** The first version handled exactly two shapes — a
    root-level module, and `package/module.py` — and the `else` branch assumed depth two
    by building `parts[0]/parts[-1]`. Handed
    `services/command_center_worker/ai_messaging.py`, it made `services/` real, found no
    entry named `ai_messaging.py` beside it, and so symlinked
    `services/command_center_worker` straight back at the repo. The caller then wrote to
    `sandbox/services/command_center_worker/ai_messaging.py`, which resolved *through* that
    symlink, and `write_text` truncated and rewrote the real file. Six harnesses share this
    function and all six happened to target depth one or two, so the bound held by luck
    while the docstring asserted it unconditionally.

    The cost was an hour of uncommitted work: each iteration wrote the repo, and the next
    iteration read what the previous one had written, so the mutations accumulated into the
    file under test. Worth stating plainly because it is the same mistake this mission keeps
    finding in the code it is migrating — a claim made in prose that nothing evaluates. A
    docstring is not a guarantee. The `raise` below is.
    """
    sandbox = root / "repo"
    sandbox.mkdir()
    parts = pathlib.Path(target).parts
    if not parts:
        raise ValueError("build_sandbox needs a target path")

    # Walk the path one component at a time. At each level the component leading to the
    # target is skipped and everything else is symlinked, so only the chain of directories
    # above the mutated file is ever a real directory.
    for depth, name in enumerate(parts[:-1]):
        source_dir = REPO.joinpath(*parts[:depth])
        sandbox_dir = sandbox.joinpath(*parts[:depth])
        for entry in source_dir.iterdir():
            if entry.name == name or (depth == 0 and entry.name == ".git"):
                continue
            (sandbox_dir / entry.name).symlink_to(entry)
        (sandbox_dir / name).mkdir()

    leaf_source = REPO.joinpath(*parts[:-1])
    leaf_sandbox = sandbox.joinpath(*parts[:-1])
    for entry in leaf_source.iterdir():
        if entry.name == parts[-1] or (len(parts) == 1 and entry.name == ".git"):
            continue
        (leaf_sandbox / entry.name).symlink_to(entry)
    shutil.copy2(REPO / target, leaf_sandbox / parts[-1])

    # The guarantee, evaluated. A mutated path that resolves outside the sandbox — because
    # some component of it turned out to be a symlink back at the repo — must stop the run
    # rather than overwrite a real file. This is cheap and it is the only thing standing
    # between a new target shape and destroyed uncommitted work.
    written = sandbox / target
    if written.is_symlink() or not str(written.resolve()).startswith(str(sandbox.resolve())):
        raise RuntimeError(
            f"sandbox for {target!r} resolves to {written.resolve()}, outside the sandbox; "
            "refusing to mutate the real repository")
    return sandbox


def main() -> int:
    failures = []
    for label, target, old, new, expect in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp), target)
            path = sandbox / target
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
