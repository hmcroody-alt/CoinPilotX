#!/usr/bin/env python3
"""Prove `tests/test_sports_edge_routing.py` fails when the migration is undone.

§50. This is the first migrated call site, so its test file is the template every
later migration copies. A template that passes vacuously is worse than no template:
it would be copied eight more times and each copy would assert nothing while looking
like it asserted everything. So each mutation below undoes one specific thing the
migration did, and names the assertion that is supposed to notice.

Three of them exist because the first draft of that test file *was* vacuous or nearly
so, and one of those was caught only by running it:

* `restore the direct requests.post` — the transport check originally banned the bare
  attribute name `get` and fired on four `envelope.get(...)` dict reads. Rewritten to
  look at the receiver instead, which is narrower in the right direction. This
  mutation is what proves the rewrite still catches a real vendor call.
* `read a model default at the call site` — the env check collects positional
  uppercase-with-underscore string constants, which is an unusual enough shape that
  "it passes" and "it can never fail" look identical from the outside.
* `pass the privacy class as a bare literal` — the whole point of §35's typo family
  is that a misspelt class is a *value*, not an error. A literal that happens to be
  spelt correctly today passes every behavioural assertion in the file.

The last two mutations are the opposite shape and they are the important ones. Every
other mutation here *adds mechanism*; none of them add prose. A check that fires on the
paragraph explaining the rule is therefore indistinguishable, from in here, from a
correct one — it catches all nineteen and would also catch a reviewer writing a
sentence. Three checks in the test file passed only because `bot.py`'s docstring
happened to say `openai_*` rather than the full old name; the sibling file for the next
call site failed five times on exactly that. So the docstring and comment mutations
spell every removed mechanism out in full and demand GREEN, because the cheapest way to
silence a prose-sensitive test is to delete the explanation it sits next to.

Never mutates the working tree: builds a sandbox of symlinks to the real repo and
replaces `bot.py` with a mutated copy. Shares `build_sandbox` with
`undx_call_domain_mutation_check.py` rather than reimplementing it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

TARGET = "bot.py"
TESTS = "tests/test_sports_edge_routing.py"

#: Each mutation is (label, old, new, test that must fail).
#:
#: `expect=None` means the mutation must be *allowed* — the suite has to stay green.
#: Two of those here, and they guard the two checks most likely to drift into banning
#: words rather than mechanisms.
MUTATIONS = [
    (
        "restore the direct provider call the migration removed",
        '    try:\n        envelope = undx_router.route_structured_request(',
        '    try:\n        requests.post("https://api.openai.com/v1/chat/completions", timeout=20)\n'
        '        envelope = undx_router.route_structured_request(',
        "test_the_function_performs_no_http_of_its_own",
    ),
    (
        "read one more dict key (must stay GREEN)",
        '    text = str(envelope.get("response") or "").strip()',
        '    text = str(envelope.get("response") or "").strip()\n'
        '    _ = game.get("home_team")',
        None,
    ),
    (
        "restore the OPENAI_MODEL default the router is supposed to own",
        '            timeout=20,',
        '            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),\n            timeout=20,',
        "test_no_provider_credential_or_model_default_is_read_here",
    ),
    (
        "call a helper with a lowercase argument (must stay GREEN)",
        '    text = str(envelope.get("response") or "").strip()',
        '    text = str(envelope.get("response") or "").strip()\n'
        '    logging.debug("sports edge routed")',
        None,
    ),
    (
        "stop declaring a privacy class",
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,\n',
        '',
        "test_the_privacy_class_is_declared_and_is_public",
    ),
    (
        "pass the privacy class as a bare literal instead of the constant",
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,',
        '            privacy_class="PUBLIC",',
        "test_both_declarations_are_constants_and_not_string_literals",
    ),
    (
        # Anchored on the privacy line above it, because `bot.py` now declares
        # `CALL_DOMAIN_TELEGRAM` twice: this call site and the Telegram assistant
        # handler migrated in the following phase. Two Telegram handlers both telling
        # the router they are Telegram handlers is the system working — it just means a
        # one-line anchor here stopped being unique. The pair is unique: U1 is the only
        # PUBLIC-classified call in the file.
        "stop declaring a call domain",
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,\n'
        '            call_domain=undx_call_domain.CALL_DOMAIN_TELEGRAM,\n',
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,\n',
        "test_the_call_domain_is_declared_and_is_telegram",
    ),
    (
        "lower the privacy class to CONFIDENTIAL",
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,',
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,',
        "test_the_privacy_class_is_declared_and_is_public",
    ),
    (
        "let the user id reach the prompt",
        '        f"Required safety line: {SPORTS_SAFETY_LINE}"',
        '        f"Required safety line: {SPORTS_SAFETY_LINE} (for {user_id})"',
        "test_the_user_id_never_reaches_the_prompt",
    ),
    (
        "drift the sampling temperature",
        '            temperature=0.32,',
        '            temperature=0.7,',
        "test_the_sampling_parameters_and_budget_survived",
    ),
    (
        "drift the token budget",
        '            max_tokens=700,',
        '            max_tokens=1400,',
        "test_the_sampling_parameters_and_budget_survived",
    ),
    (
        "soften the safety posture in the system instruction",
        '    "You are CoinPilotX Sports Edge: cautious, ethical, analytical, and never "\n'
        '    "certainty-based."',
        '    "You are CoinPilotX Sports Edge: a sharp, confident analyst."',
        "test_the_system_instruction_is_preserved_verbatim",
    ),
    (
        "stop guaranteeing the safety line",
        '    if SPORTS_SAFETY_LINE not in text:\n'
        '        text += f"\\n\\n{SPORTS_SAFETY_LINE}"\n',
        '',
        "test_it_is_appended_when_the_model_omits_it",
    ),
    (
        "append the safety line unconditionally",
        '    if SPORTS_SAFETY_LINE not in text:\n'
        '        text += f"\\n\\n{SPORTS_SAFETY_LINE}"',
        '    text += f"\\n\\n{SPORTS_SAFETY_LINE}"',
        "test_it_is_not_duplicated_when_the_model_includes_it",
    ),
    (
        "let a router refusal propagate instead of degrading",
        '    if not envelope.get("ok"):\n'
        '        logging.info("Sports Edge analysis unavailable: %s attempts=%s",\n'
        '                     envelope.get("error"), envelope.get("attempts"))\n'
        '        return None',
        '    if not envelope.get("ok"):\n'
        '        raise RuntimeError(envelope.get("error"))',
        "test_a_router_that_refused_every_provider_returns_none",
    ),
    (
        # `except Exception as exc:` on its own matches 465 times in this file, which is
        # a fair description of how much of `bot.py` is written defensively. The anchor
        # has to carry the following comment to be unique.
        "let a raising router take the whole Telegram reply with it",
        '    except Exception as exc:\n'
        '        # The router is not supposed to raise',
        '    except KeyboardInterrupt as exc:\n'
        '        # The router is not supposed to raise',
        "test_a_router_that_raises_returns_none_rather_than_propagating",
    ),
    (
        "return a bare safety line when the provider returned nothing",
        '    text = str(envelope.get("response") or "").strip()\n    if not text:\n        return None',
        '    text = str(envelope.get("response") or "").strip()',
        "test_an_empty_answer_returns_none_rather_than_a_bare_safety_line",
    ),
    (
        "spend money for a free user",
        '    if not user_id or not is_pro(user_id):\n        return None',
        '    if not user_id:\n        return None',
        "test_a_free_user_is_not_routed_at_all",
    ),
    (
        "route an anonymous caller",
        '    if not user_id or not is_pro(user_id):\n        return None',
        '    if user_id is None:\n        return None',
        "test_an_anonymous_caller_is_not_routed_at_all",
    ),
    (
        # The mutation class this harness could not express, and therefore the one that
        # got through. Every mutation above *adds mechanism*; none of them add prose. So
        # a check that fires on prose rather than on mechanism looks identical to a
        # correct one from in here — it catches all nineteen and would also catch a
        # reviewer writing a sentence.
        #
        # That is not hypothetical. Three checks in the test file passed only because
        # this docstring happened to say `openai_*` rather than the full old name, and
        # never to spell the endpoint out. The sibling file for the next call site
        # failed five times on exactly this, in the paragraphs explaining the removal.
        #
        # The cost of getting it wrong is worse than a false failure: the cheapest way
        # to make a prose-sensitive test green is to delete the paragraph that says why
        # the rule exists, so the test quietly eats its own documentation. Naming every
        # removed mechanism in full here, and requiring GREEN, is what forbids that.
        "spell out every removed mechanism in the docstring (must stay GREEN)",
        '    Three things genuinely change.',
        '    This function used to be `openai_sports_edge_analysis` and used to POST to\n'
        '    https://api.openai.com/v1/chat/completions with `OPENAI_API_KEY`, defaulting\n'
        '    its model from `OPENAI_MODEL`. None of those four things happen here now.\n'
        '\n'
        '    Three things genuinely change.',
        None,
    ),
    (
        # Same rule one level down, and the reason a comment is worth its own mutation:
        # `ast.parse` drops comments entirely, so a comment passes every AST check for
        # free and fails a substring check exactly as hard as a docstring does. A file
        # protected only by AST checks can be broken by a substring check added later in
        # good faith, and this is the mutation that would say so.
        "spell them out in a comment instead (must stay GREEN)",
        '    if not user_id or not is_pro(user_id):\n        return None',
        '    # No OPENAI_API_KEY read, no OPENAI_MODEL default, no api.openai.com request.\n'
        '    if not user_id or not is_pro(user_id):\n        return None',
        None,
    ),
]


def main() -> int:
    failures = []
    for label, old, new, expect in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = build_sandbox(pathlib.Path(tmp), TARGET)
            path = sandbox / TARGET
            source = path.read_text(encoding="utf-8")
            if source.count(old) != 1:
                failures.append(f"{label}: anchor matched {source.count(old)}x, expected 1")
                print(f"BAD {label}: anchor matched {source.count(old)}x")
                continue
            path.write_text(source.replace(old, new), encoding="utf-8")

            env = dict(os.environ, PYTHONPATH=str(sandbox), PYTHONDONTWRITEBYTECODE="1")
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", TESTS, "-q", "--no-header",
                 "-p", "no:cacheprovider"],
                cwd=sandbox, env=env, capture_output=True, text=True, timeout=900,
            )
            output = proc.stdout + proc.stderr
            died = proc.returncode != 0
            before = len(failures)

            if expect is None:
                verdict = "GREEN (correct)" if not died else "FAILED (should have been allowed)"
                if died:
                    failures.append(f"{label}: this is not supposed to trip anything\n{output[-1500:]}")
            elif not died:
                verdict = "SURVIVED"
                failures.append(f"{label}: suite stayed green")
            elif expect not in output:
                verdict = f"died, but not on {expect}"
                failures.append(f"{label}: expected {expect} to fail\n{output[-1500:]}")
            else:
                verdict = f"caught by {expect}"
            print(f"{'ok ' if len(failures) == before else 'BAD'} {label}: {verdict}")

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
