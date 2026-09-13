#!/usr/bin/env python3
"""Prove the "route on the question, not on the scaffolding" tests fail when broken.

§50: a protection test that has never been seen to fail is a comment with a test runner
attached. This defect is the strongest argument in this repo for that rule, because it
lived in production under a suite of 264 passing tests. Nothing was wrong with those
tests' assertions — they asserted that the request succeeded, and it did. It succeeded
against a model chosen from a capability catalog and a CoinGecko snapshot rather than
from what the user asked.

So every mutation here is paired with an assertion about **which provider was reached**
or **which argument was handed to the router**. A mutation that leaves `ok` True and the
route wrong must still kill a named test, and the `expect` field is what proves the
assertion doing the killing is the one written for the job.

Two facts worth keeping next to the mutations, because both were measured and neither is
inferable from reading the code:

  * The privacy ceiling was *masking* most of this. At CONFIDENTIAL — which every service
    caller declares — DeepSeek, Gemini, Groq and Perplexity are refused, so the
    `current_web`, `repository` and `research` lanes all collapse onto the same reachable
    chain `[openai, claude, meta]`. The one category that survives the collapse is
    `security`, which leads with Claude. That is why the tests declare PUBLIC where they
    are about classification: an assertion satisfied by the privacy gate is not an
    assertion about routing.
  * The live market board's only freshness cue is `2026`, out of its own `updated_at`
    stamp. `2026` and `2027` are cues and `2028` is not, so this misroute changes
    category on 1 January 2028 with no diff. The fixtures pin a fixed date for that
    reason.

Never mutates the working tree: `build_sandbox` symlinks the repo and makes exactly the
one mutated file real. Shares that function by import with
`undx_call_domain_mutation_check.py` rather than copying it — it is the seventh caller,
and the reason not to copy is written in its docstring: a depth-two assumption in an
earlier version resolved a sandbox path back at the repo and truncated real files.

Run: .venv/bin/python3 scripts/undx_classification_subject_mutation_check.py
     .venv/bin/python3 scripts/undx_classification_subject_mutation_check.py --only planner
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

ROUTER = "undx_router.py"
PLANNER = "services/undx_capability_planner.py"
INTEL = "services/intelligence.py"
RUNTIME = "services/undx_agent_runtime.py"
SUMMARIZER = "services/pulse_briefings/summarizer.py"
INTEGRITY = "tests/protection/test_protection_suite_integrity.py"
ENV_CONTRACT = "tests/protection/test_environment_contract.py"

ROUTER_TESTS = "tests/test_undx_router_multi_provider.py"
PLANNER_TESTS = "tests/undx_agent/test_capability_planner.py"
INTEL_TESTS = "tests/test_assistant_response_routing.py"
BRIEFING_TESTS = "tests/briefings/test_pulse_briefings.py"

#: (label, file, old, new, test that must fail, test file)
MUTATIONS = [
    (
        # The state this repo was in until this phase. `classify_text` exists but the
        # router ignores it, so every caller's declaration is decorative.
        "router: ignore the declared subject and classify what is sent",
        ROUTER,
        '        subject = user_content if classify_text is None else classify_text\n',
        '        subject = user_content\n',
        "test_the_named_subject_decides_the_route_and_not_the_text_sent",
        ROUTER_TESTS,
    ),
    (
        # The plausible-looking version. `or` reads as a safe default and is not one: it
        # silently restores the defect for a caller that explicitly said the subject is
        # empty, which is the caller that was most careful.
        "router: fall back to the prompt when the declared subject is empty",
        ROUTER,
        '        subject = user_content if classify_text is None else classify_text\n',
        '        subject = classify_text or user_content\n',
        "test_an_empty_subject_is_taken_at_its_word",
        ROUTER_TESTS,
    ),
    (
        "planner: stop naming the message as the classification subject",
        PLANNER,
        '            classify_text=message,\n',
        '',
        "test_the_routing_decision_is_made_from_the_message_not_the_catalog",
        PLANNER_TESTS,
    ),
    (
        "planner: hope for JSON instead of requiring it",
        PLANNER,
        '            require_json=True,\n',
        '',
        "test_json_is_required_rather_than_hoped_for",
        PLANNER_TESTS,
    ),
    (
        # Behaviour-neutral and still caught, which is the point of §4. An omitted class
        # normalises to CONFIDENTIAL, so nothing routes differently; what is lost is the
        # ability to tell a considered classification from an absent one.
        "planner: leave the privacy class to the default",
        PLANNER,
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,\n',
        '',
        "test_a_privacy_class_is_declared_rather_than_defaulted",
        PLANNER_TESTS,
    ),
    (
        # Not "drop the domain" — hardcode it. Dropping it is obvious in review; pinning
        # it to the value today's only caller happens to pass is not, and it silently
        # removes the caller's ability to say anything else.
        "planner: hardcode the domain instead of threading the caller's",
        PLANNER,
        '            call_domain=call_domain,\n',
        '            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,\n',
        "test_the_domain_comes_from_the_caller_and_never_from_the_message",
        PLANNER_TESTS,
    ),
    (
        "runtime: stop supplying a domain to the planner",
        RUNTIME,
        '        result = undx_capability_planner.plan(\n'
        '            text, user_id=int(user_id),\n'
        '            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL)\n',
        '        result = undx_capability_planner.plan(text, user_id=int(user_id))\n',
        "test_the_agent_runtime_supplies_a_domain_it_can_actually_vouch_for",
        PLANNER_TESTS,
    ),
    (
        "intelligence: classify the market board again instead of the question",
        INTEL,
        '            classify_text=question,\n',
        '',
        "test_the_question_is_what_gets_classified",
        INTEL_TESTS,
    ),
    (
        # Control. The reasoning lives in comments; comments are invisible to
        # `ast.parse` and must not be able to trip a behavioural suite. A harness with no
        # green-expected mutation cannot distinguish "the tests are sharp" from "the
        # tests fire on anything".
        "intelligence: reword the comment above the declaration (must stay GREEN)",
        INTEL,
        '            # Route on the question, not on the market board sitting in front of it. The\n',
        '            # Classify the question rather than the board. The\n',
        None,
        INTEL_TESTS,
    ),

    # ---- the briefing summarizer
    #
    # This call site declared none of the four and its suite was green, because every
    # UNDX test in that file stubs the router as `lambda *a, **k` and asserts on
    # `copy["source"]`. A `**k` cannot notice an argument that is absent. These
    # mutations exist to prove the replacement assertions read the arguments.
    (
        "briefing: leave the privacy class to the default",
        SUMMARIZER,
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,\n',
        '',
        "test_a_privacy_class_is_declared_rather_than_defaulted",
        BRIEFING_TESTS,
    ),
    (
        "briefing: leave the call domain to the default",
        SUMMARIZER,
        '            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,\n',
        '',
        "test_a_call_domain_is_declared_rather_than_defaulted",
        BRIEFING_TESTS,
    ),
    (
        "briefing: classify the serialized payload again instead of the job",
        SUMMARIZER,
        '            classify_text=ROUTING_SUBJECT,\n',
        '',
        "test_the_routing_subject_is_the_job_and_not_the_serialized_payload",
        BRIEFING_TESTS,
    ),
    (
        # Not "delete the subject" — keep it and make it wrong. Dropping the argument is
        # caught by the test above; this asks whether anything checks that the constant
        # *routes somewhere different from the payload*. Without that assertion
        # `ROUTING_SUBJECT` could be any string at all and the equality test would still
        # pass, which would make it a test about a variable name rather than about
        # routing. The replacement classifies as `research` — the same lane the payload
        # already reached — so it restores the defect while keeping every other
        # assertion in the class satisfied.
        "briefing: word the routing subject so it lands back in the research lane",
        SUMMARIZER,
        'ROUTING_SUBJECT = "summarize a bounded fact payload into notification copy"\n',
        'ROUTING_SUBJECT = "summarize the crypto market payload into notification copy"\n',
        "test_the_declared_subject_and_the_payload_route_to_different_lanes",
        BRIEFING_TESTS,
    ),
    (
        # The mutation in the *strict* direction, which is the one a reviewer would wave
        # through. `require_json=True` reads as unambiguously safer and is wrong here:
        # Claude has no JSON mode, CONFIDENTIAL already narrows the chain to
        # [openai, claude], and a parse failure at this call site degrades to a
        # deterministic grounded template rather than dropping work. So the guarantee
        # costs half the chain and buys nothing.
        "briefing: require JSON, refusing half the reachable chain to no benefit",
        SUMMARIZER,
        '            # Deliberately NOT require_json=True, which is the opposite of the call in\n',
        '            require_json=True,\n'
        '            # Deliberately NOT require_json=True, which is the opposite of the call in\n',
        "test_json_is_not_required_so_claude_stays_in_the_chain",
        BRIEFING_TESTS,
    ),

    # ---- the guard against tests that cannot fail
    (
        # Self-referential on purpose. `test_no_test_module_defines_the_same_test_twice`
        # reports a count of offending modules and passes on zero — the exact shape that
        # cannot distinguish "clean repo" from "broken detector". Neutering the detector
        # must therefore kill the paired test that feeds it a module known to be broken,
        # and must *not* be survivable just because the repo happens to be clean.
        "integrity: make the shadowed-definition detector always report nothing",
        INTEGRITY,
        '    tree = ast.parse(source)\n'
        '    shadowed = []\n',
        '    tree = ast.parse(source)\n'
        '    shadowed = []\n'
        '    return shadowed\n',
        "test_the_shadowed_definition_check_can_actually_fail",
        INTEGRITY,
    ),
    (
        # Under-strip. The scanner then reads prose about code as code again, which is
        # what demanded `.env.example` document `PULSE_AI_PROVIDER` — a variable whose
        # only three appearances in the repo are comments explaining that it is no
        # longer read.
        "env contract: stop stripping comments before scanning for reads",
        ENV_CONTRACT,
        '        text = _without_comments(text)\n',
        '',
        "test_every_variable_production_code_reads_is_documented",
        ENV_CONTRACT,
    ),
    (
        # Over-strip, and the more dangerous direction of the two. `os.getenv("X")`
        # holds the name in a *string literal*, so dropping strings blinds the scanner
        # almost entirely — and a blind scanner reports zero undocumented variables,
        # which is indistinguishable from a complete `.env.example`. The
        # `len(read) > 300` guard is what should catch it; this mutation is how we find
        # out whether it does.
        "env contract: strip string literals as well as comments",
        ENV_CONTRACT,
        '        if token.type != tokenize.COMMENT:\n',
        '        if token.type not in (tokenize.COMMENT, tokenize.STRING):\n',
        "test_the_comment_stripper_hides_prose_without_hiding_code",
        ENV_CONTRACT,
    ),
]


def main() -> int:
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

    failures: list[str] = []
    for label, target, old, new, expect, tests in selected:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp), target)
            path = sandbox / target
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                failures.append(f"{label}: anchor matched {source.count(old)}x, expected 1")
                print(f"BAD {label}: anchor matched {source.count(old)}x")
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")

            env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", tests, "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=900,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: a comment is not supposed to trip "
                                    f"anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            marker = "BAD" if failures and failures[-1].startswith(f"{label}:") else "ok "
            print(f"{marker} {label}: {verdict}")

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
