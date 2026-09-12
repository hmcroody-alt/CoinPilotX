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
EVIDENCE = "services/undx_routing_evidence.py"
TESTS = "tests/test_undx_call_domain.py"
EVIDENCE_TESTS = "tests/test_undx_routing_evidence.py"

#: Each mutation is (label, file, old, new, test that must fail[, test file]).
#:
#: The sixth element defaults to `TESTS`. It exists because the gate ladder is now one
#: function consumed by three callers, and a mutation to it can be caught by assertions
#: living in either suite. Which suite is asked matters: a mutation that kills the AST
#: order test proves the *source shape* is pinned, and one that kills the evidence test
#: proves the *predicted chain* is pinned. Those are different claims and the first does
#: not imply the second, so the conditional-privacy mutation below appears twice, once
#: per suite, rather than once with whichever `expect` happened to be convenient.
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
        # Renamed with the test. The privacy ceiling is no longer applied inline in
        # either entry point — it moved into `_gate` — so "after the privacy ceiling"
        # named a line that is not there any more. A stale `expect` is the failure this
        # harness exists to prevent, one level up: the mutation still kills the suite,
        # the named assertion is simply not the one that caught it, and the run would
        # have reported `died, but not on ...` rather than a pass. Worth stating because
        # the cheaper-looking fix — deleting the `expect` — converts a proof that a
        # specific assertion works into a proof that the file is non-empty.
        "test_the_domain_is_consulted_before_the_gate_ladder",
    ),
    (
        "stop consulting the declared domain in the mission path",
        ROUTER,
        '    ordered = provider_priority(classification) if router_enabled() else ["openai"]\n'
        '    ordered = _domain_ordered(ordered, call_domain)\n',
        '    ordered = provider_priority(classification) if router_enabled() else ["openai"]\n',
        "test_the_domain_is_consulted_before_the_gate_ladder",
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
    # ------------------------------------------------------------------ the gate ladder
    #
    # `_gate` was extracted because the ladder had been transcribed four times: twice to
    # run it and twice to predict it. The five mutations below are the three ways the
    # predicting copies had actually drifted, plus the order property of the extracted
    # function itself. Each one restores a real former state of this repo rather than an
    # imagined one, which is why they are worth carrying: they are the regressions that
    # already happened once.
    (
        # The exact guard `explain` used to carry. It reads as tolerance for a missing
        # value and is the opposite — an omitted class normalises to CONFIDENTIAL, so
        # skipping the call is what discards the default ceiling. This is the mutation
        # direction the pre-existing suite could not catch: both of its privacy tests
        # passed a truthy class, so a gate that only fires when a class is named looked
        # identical to one that always fires.
        "make the privacy gate conditional on the caller naming a class",
        ROUTER,
        '    refusal = _privacy_refusal(provider, privacy_class)\n',
        '    refusal = _privacy_refusal(provider, privacy_class) if privacy_class else ""\n',
        "test_the_gate_ladder_never_makes_the_privacy_check_conditional",
    ),
    (
        # Same mutation, asked of the other suite. The AST test above pins the source
        # shape; this pins the consequence — that a request declaring nothing is still
        # told Perplexity is unreachable and OpenAI is the first choice.
        "make the privacy gate conditional (as the evidence surface sees it)",
        ROUTER,
        '    refusal = _privacy_refusal(provider, privacy_class)\n',
        '    refusal = _privacy_refusal(provider, privacy_class) if privacy_class else ""\n',
        "test_an_omitted_privacy_class_still_applies_the_default_ceiling",
        EVIDENCE_TESTS,
    ),
    (
        # Read a credential before asking whether the content may be sent at all. The
        # order is the security property, not the presence of the checks: a provider
        # which must not see this content should not be consulted about whether it could
        # have. Anchored on the privacy line, which is unique, rather than on
        # `config = PROVIDERS[provider]`, which is not.
        "read the API key before the privacy class",
        ROUTER,
        '    config = PROVIDERS[provider]\n'
        '    refusal = _privacy_refusal(provider, privacy_class)\n',
        '    config = PROVIDERS[provider]\n'
        '    if not _api_key(provider):\n'
        '        return {"provider": config.label, "status": "not_configured",\n'
        '                "detail": "no API key is set for this provider"}\n'
        '    refusal = _privacy_refusal(provider, privacy_class)\n',
        "test_the_gate_ladder_checks_privacy_before_it_reads_a_credential",
    ),
    (
        # `explain` took no `require_json` at all, so the `capability_unmet` refusals the
        # loop applies before reading a credential were invisible to the surface that
        # claims to describe the loop. Both `scam_shield` and `undx_capability_planner`
        # route with it set.
        "stop telling the gate ladder that JSON was required",
        EVIDENCE,
        '        refused = router._gate(name, privacy_class=privacy_class, budget=budget,\n'
        '                               require_json=require_json) or {}\n',
        '        refused = router._gate(name, privacy_class=privacy_class,\n'
        '                               budget=budget) or {}\n',
        "test_a_json_requirement_is_reported_as_the_loop_would_apply_it",
        EVIDENCE_TESTS,
    ),
    (
        # The latent one, and the reason it is here rather than filed as a nice-to-have:
        # `_PREFERENCE` is empty today, so omitting `_domain_ordered` produces identical
        # output and nothing observable is wrong. The test that catches this installs a
        # preference, because a test asserting against the real empty table would agree
        # with the broken tree. Restoring the omission has to fail *now*, otherwise the
        # first domain to declare a preference makes this surface wrong silently.
        "stop applying the declared domain in the evidence surface",
        EVIDENCE,
        '    actual = router._domain_ordered(lane_plan, call_domain)\n',
        '    actual = list(lane_plan)\n',
        "test_the_declared_domain_reorders_the_explained_plan",
        EVIDENCE_TESTS,
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
    """Run every mutation, or the subset named by `--only SUBSTRING`.

    The filter is a convenience with one real use: a full run is a pytest invocation per
    mutation, so iterating on a single new mutation costs the whole set otherwise. It
    matches on the label. Ported from `scripts/undx_call_guard_mutation_check.py`, where
    the argument for it is already written; the one thing worth repeating is that a
    filtered run is not evidence about the mutations it skipped, so the count printed at
    the end says how many ran rather than how many exist.
    """
    only = ""
    args = sys.argv[1:]
    if args and args[0] == "--only":
        if len(args) < 2:
            print("--only needs a substring", file=sys.stderr)
            return 2
        only = args[1]

    selected = [m for m in MUTATIONS if not only or only in m[0]]
    if not selected:
        print(f"no mutation label contains {only!r}", file=sys.stderr)
        return 2

    failures = []
    for entry in selected:
        label, target, old, new, expect = entry[:5]
        tests = entry[5] if len(entry) > 5 else TESTS
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
                [sys.executable, "-m", "pytest", tests, "-q", "--no-header", "-p", "no:cacheprovider"],
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
    scope = f" (filtered to {only!r}; {len(MUTATIONS)} exist)" if only else ""
    print(f"All {len(selected)} mutations behaved as specified{scope}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
