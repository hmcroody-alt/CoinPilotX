#!/usr/bin/env python3
"""Prove `tests/test_assistant_response_routing.py` fails when U2's migration is undone.

§50. U2 is the widest call site in `UNDX_PROVIDER_CALLSITE_CENSUS.md`: one function,
five callers, three of which publish something to a user. So its protections span five
files and each mutation below names its own target, which is why this harness carries a
per-mutation target where the Sports Edge one has a single module-level `TARGET`.

Three mutation families here do not exist in the Sports Edge harness, because U2 is the
first call site where they are possible:

* **Domain forwarding.** U1 could declare a constant — both its callers are Telegram
  handlers. U2 cannot, so the declaration moved outward to five sites and "did every
  caller remember" became the thing worth breaking. The mutations replace a forwarded
  parameter with a hardcoded constant, which is the failure that produces *no symptom*
  at runtime: §5 forbids a domain from widening anything, so a wrong domain costs a
  routing preference and nothing else.
* **Attribution.** Two callers used to compute a user-visible `source` from whether
  `OPENAI_API_KEY` was set. That was imprecise before routing and wrong after it — the
  key can be set while Gemini answers. Restoring it is a mutation because it is the
  regression a future reader is most likely to reintroduce while "simplifying".
* **Prose (must stay GREEN).** Both trailing mutations spell out, in full, every
  mechanism this migration removed. They exist because the entire preceding list only
  ever *adds mechanism*, so a check that fires on the paragraph explaining the rule is
  indistinguishable from a correct one from inside a harness like this. That is not
  hypothetical here: the first run of this test file failed five times, every failure on
  its own docstrings and comments naming what had been removed. The cheapest way to
  green a prose-sensitive test is to delete the explanation next to it, so these two
  mutations are what forbid one from being written.

Never mutates the working tree: `build_sandbox` symlinks the repo and makes exactly the
one mutated file real. Shared with the two sibling harnesses rather than reimplemented.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

TESTS = "tests/test_assistant_response_routing.py"

INTELLIGENCE = "services/intelligence.py"
AI_ROUTER = "services/ai_router.py"
COMMAND_ROUTER = "services/command_router.py"
AI_SERVICE = "services/ai_service.py"
BOT = "bot.py"

#: Each mutation is (label, target file, old, new, test that must fail).
#:
#: `expect=None` means the mutation must be *allowed* and the suite has to stay green.
MUTATIONS = [
    # ---- §11-12: the transport actually moved -------------------------------------
    (
        "restore the direct provider call the migration removed",
        INTELLIGENCE,
        '    snapshot = market_data.live_market_board(limit=10)\n',
        '    snapshot = market_data.live_market_board(limit=10)\n'
        '    requests.post("https://api.openai.com/v1/chat/completions", timeout=20)\n',
        "test_the_module_performs_no_http_of_its_own",
    ),
    (
        "restore the OPENAI_API_KEY gate that used to decide availability",
        INTELLIGENCE,
        '    snapshot = market_data.live_market_board(limit=10)\n',
        '    snapshot = market_data.live_market_board(limit=10)\n'
        '    if not os.getenv("OPENAI_API_KEY"):\n'
        '        return {"text": fallback_response(question, pro), "routed": False,\n'
        '                "provider": None, "source": FALLBACK_SOURCE_LABEL,\n'
        '                "call_domain": None}\n',
        "test_no_environment_variable_is_read_here_at_all",
    ),
    (
        "put the vendor endpoint back in a string the module could request",
        INTELLIGENCE,
        'FALLBACK_SOURCE_LABEL = "CoinPlotXAI deterministic market read"',
        'FALLBACK_SOURCE_LABEL = "CoinPlotXAI deterministic market read"\n'
        'CHAT_URL = "https://api.openai.com/v1/chat/completions"',
        "test_no_vendor_endpoint_survives_as_a_string_it_could_request",
    ),

    # ---- §4: the privacy class is declared, named, and not lowered ----------------
    (
        "stop declaring a privacy class",
        INTELLIGENCE,
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,\n',
        '',
        "test_the_privacy_class_is_declared_and_is_confidential",
    ),
    (
        "pass the privacy class as a bare literal instead of the constant",
        INTELLIGENCE,
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,',
        '            privacy_class="CONFIDENTIAL",',
        "test_both_declarations_are_constants_and_not_string_literals",
    ),
    (
        # §4's named failure, and the tempting one: PUBLIC admits seven providers where
        # CONFIDENTIAL admits three, so this mutation makes routing *succeed more often*.
        # It is still wrong — `question` is free text the user wrote.
        "widen routing by lowering the class to PUBLIC",
        INTELLIGENCE,
        '            privacy_class=undx_privacy.SENSITIVITY_CONFIDENTIAL,',
        '            privacy_class=undx_privacy.SENSITIVITY_PUBLIC,',
        "test_the_privacy_class_is_declared_and_is_confidential",
    ),
    (
        "let the user id reach the prompt",
        INTELLIGENCE,
        '            f"Live context: {snapshot}\\n\\nQuestion: {question}",',
        '            f"Live context: {snapshot}\\n\\nUser {user_id} asks: {question}",',
        "test_the_user_id_never_reaches_the_prompt",
    ),

    # ---- §3: prompt intent, sampling and budget survived the move ------------------
    (
        "drift the sampling temperature",
        INTELLIGENCE,
        '            temperature=0.35,',
        '            temperature=0.7,',
        "test_the_sampling_parameters_and_timeout_survived",
    ),
    (
        # The split is the entitlement: a Pro reply is nearly three times as long. Making
        # it uniform is invisible in every behavioural test that does not check both.
        "collapse the Pro token split into one budget",
        INTELLIGENCE,
        '            max_tokens=850 if pro else 320,',
        '            max_tokens=850,',
        "test_the_pro_token_split_survived",
    ),
    (
        "soften the system instruction",
        INTELLIGENCE,
        'ASSISTANT_SYSTEM_PROMPT = (',
        'ASSISTANT_SYSTEM_PROMPT = "Be a helpful crypto assistant." or (',
        "test_the_system_instruction_is_preserved_verbatim",
    ),
    (
        "stop putting the live market context in the prompt",
        INTELLIGENCE,
        '            f"Live context: {snapshot}\\n\\nQuestion: {question}",',
        '            f"Question: {question}",',
        "test_the_live_context_and_question_both_reach_the_prompt",
    ),

    # ---- the two-condition disclosure check ----------------------------------------
    (
        # "not investment or financial advice" contains "financial advice" and does not
        # contain "not financial", so collapsing the check appends a second disclosure to
        # a reply that already carried one.
        "collapse the two-condition disclosure check into one",
        INTELLIGENCE,
        '    if "not financial" not in text.lower() and "financial advice" not in text.lower():',
        '    if "not financial" not in text.lower():',
        "test_the_financial_advice_phrasing_also_satisfies_it",
    ),
    (
        "stop appending the required disclosure at all",
        INTELLIGENCE,
        '        text += f"\\n\\n{REQUIRED_DISCLOSURE}"',
        '        pass',
        "test_it_is_appended_when_the_model_omits_it",
    ),
    (
        "make the disclosure check case sensitive",
        INTELLIGENCE,
        '    if "not financial" not in text.lower() and "financial advice" not in text.lower():',
        '    if "not financial" not in text and "financial advice" not in text:',
        "test_the_check_is_case_insensitive",
    ),

    # ---- graceful degradation: a deterministic read, never an apology ---------------
    (
        "return an empty answer instead of the deterministic read",
        INTELLIGENCE,
        '            "text": fallback_response(question, pro),',
        '            "text": "",',
        "test_the_string_form_never_returns_empty",
    ),
    (
        "let a raising router propagate to the caller",
        INTELLIGENCE,
        '    except Exception as exc:\n'
        '        # The router returns a typed miss rather than raising.',
        '    except KeyboardInterrupt as exc:\n'
        '        # The router returns a typed miss rather than raising.',
        "test_a_raising_router_falls_back_rather_than_propagating",
    ),
    (
        # Survived the first run of this harness, because every refusal fixture in the
        # suite had an empty `response` — so dropping the `ok` guard changed nothing that
        # was being looked at. The test it now names uses a refusal that carries text,
        # which is the only shape where the guard does any work.
        "treat a refusal envelope as a successful answer",
        INTELLIGENCE,
        '    text = str(envelope.get("response") or "").strip() if envelope.get("ok") else ""',
        '    text = str(envelope.get("response") or "").strip()',
        "test_a_refusal_that_carries_text_still_falls_back",
    ),
    (
        "report a fallback as though a provider had answered",
        INTELLIGENCE,
        '            "routed": False,\n',
        '            "routed": True,\n',
        "test_a_router_that_refused_every_provider_falls_back",
    ),

    # ---- §5: five callers, five declarations ---------------------------------------
    (
        "stop forwarding the declared domain to the router",
        INTELLIGENCE,
        '            call_domain=call_domain,\n',
        '',
        "test_the_declared_domain_is_forwarded_untouched",
    ),
    (
        # Invents provenance the function does not have. Produces no runtime symptom,
        # because §5 means a domain cannot widen what a call may do — which is exactly
        # why it needs a test rather than a code review.
        "invent GENERAL inside the shared function instead of forwarding",
        INTELLIGENCE,
        '            call_domain=call_domain,\n',
        '            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,\n',
        "test_the_declared_domain_is_forwarded_untouched",
    ),
    (
        "drop the domain from the string-form signature",
        INTELLIGENCE,
        '    return assistant_response_envelope(user_id, question, pro=pro,\n'
        '                                       call_domain=call_domain)["text"]',
        '    return assistant_response_envelope(user_id, question, pro=pro)["text"]',
        "test_the_string_form_forwards_the_domain_too",
    ),
    (
        "let the Telegram handler stop declaring where its text came from",
        BOT,
        '            user_id, question, pro=is_pro(user_id),\n'
        '            call_domain=undx_call_domain.CALL_DOMAIN_TELEGRAM,\n',
        '            user_id, question, pro=is_pro(user_id),\n',
        "test_every_bot_py_call_site_declares_a_domain_as_a_named_constant",
    ),
    (
        "declare the Telegram handler's provenance as a bare literal",
        BOT,
        '            call_domain=undx_call_domain.CALL_DOMAIN_TELEGRAM,\n'
        '        )\n'
        '        response = answer["text"]',
        '            call_domain="TELEGRAM",\n'
        '        )\n'
        '        response = answer["text"]',
        "test_every_bot_py_call_site_declares_a_domain_as_a_named_constant",
    ),
    (
        "let the web chat router stop declaring a domain",
        AI_ROUTER,
        '            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,\n',
        '',
        "test_the_message_router_declares_a_domain",
    ),
    (
        "have the uncalled wrapper invent GENERAL instead of forwarding",
        AI_SERVICE,
        '        call_domain=call_domain,',
        '        call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,',
        "test_the_uncalled_wrapper_forwards_rather_than_inventing_a_domain",
    ),
    (
        # The rot the derivation exists to avoid. A table of known channels with a
        # GENERAL default looks safer and is not: `telegram_menu` is not in the table,
        # so it silently becomes GENERAL and nothing anywhere says so.
        "replace the channel derivation with a table and a GENERAL default",
        COMMAND_ROUTER,
        '    if "telegram" in name:\n'
        '        return undx_call_domain.CALL_DOMAIN_TELEGRAM\n'
        '    return undx_call_domain.CALL_DOMAIN_GENERAL',
        '    return {"telegram": undx_call_domain.CALL_DOMAIN_TELEGRAM}.get(\n'
        '        name, undx_call_domain.CALL_DOMAIN_GENERAL)',
        "test_the_menu_dispatcher_translates_its_channel_into_a_domain",
    ),
    (
        "stop lowercasing the channel before deriving from it",
        COMMAND_ROUTER,
        '    name = str(channel or "").lower()',
        '    name = str(channel or "")',
        "test_the_menu_dispatcher_translates_its_channel_into_a_domain",
    ),
    (
        "call the shared function without translating the channel",
        COMMAND_ROUTER,
        '            user_id, question, pro=pro, call_domain=_call_domain_for(channel))',
        '            user_id, question, pro=pro)',
        "test_the_menu_dispatcher_call_site_actually_uses_the_translation",
    ),

    # ---- attribution is execution, not configuration --------------------------------
    (
        "derive a user-visible source from a credential again",
        AI_ROUTER,
        '        source = (f"{answer[\'source\']} + CoinPlotXAI context" if answer["routed"]\n'
        '                  else intelligence.FALLBACK_SOURCE_LABEL)',
        '        source = ("OpenAI + CoinPlotXAI context" if os.getenv("OPENAI_API_KEY")\n'
        '                  else intelligence.FALLBACK_SOURCE_LABEL)',
        "test_neither_source_publishing_caller_reads_a_credential",
    ),
    (
        "hardcode the vendor in the source the menu dispatcher publishes",
        COMMAND_ROUTER,
        '        source = (f"{answer[\'source\']} + public market context" if answer["routed"]\n'
        '                  else intelligence.FALLBACK_SOURCE_LABEL)',
        '        source = "OpenAI + public market context"',
        "test_neither_caller_hardcodes_a_vendor_in_the_source_it_publishes",
    ),
    (
        "report the configured provider rather than the one that answered",
        INTELLIGENCE,
        '        "provider": envelope.get("provider"),',
        '        "provider": "openai",',
        "test_a_successful_route_reports_the_provider_that_answered",
    ),

    # ---- prose: must stay GREEN ------------------------------------------------------
    (
        # The five failures that produced this harness were all of this shape, in this
        # file. Spelling every removed mechanism out in full and demanding GREEN is what
        # keeps the next reader free to explain the migration where it happened.
        "spell out every removed mechanism in the module docstring (must stay GREEN)",
        INTELLIGENCE,
        '## Why `call_domain` is a parameter here and was a constant in U1',
        'Removed here: the `OPENAI_API_KEY` availability gate, the `OPENAI_MODEL` default,\n'
        'the hand-rolled POST to https://api.openai.com/v1/chat/completions, and the\n'
        'user-visible "OpenAI" source label. None of the four survive.\n'
        '\n'
        '## Why `call_domain` is a parameter here and was a constant in U1',
        None,
    ),
    (
        # `ast.parse` drops comments, so a comment passes every AST check for free and
        # fails a substring check exactly as hard as a docstring does. Worth its own
        # mutation so that a substring check added later in good faith is caught here
        # rather than by deleting the comment.
        "spell them out in a comment at the call site instead (must stay GREEN)",
        INTELLIGENCE,
        '    snapshot = market_data.live_market_board(limit=10)\n',
        '    # No OPENAI_API_KEY, no OPENAI_MODEL, no api.openai.com, no "OpenAI" label.\n'
        '    snapshot = market_data.live_market_board(limit=10)\n',
        None,
    ),
    (
        "explain the credential removal in a caller comment too (must stay GREEN)",
        COMMAND_ROUTER,
        'def _call_domain_for(channel):',
        '# This module no longer reads OPENAI_API_KEY and no longer publishes "OpenAI".\n'
        'def _call_domain_for(channel):',
        None,
    ),
]


def main() -> int:
    failures = []
    for label, target, old, new, expect in MUTATIONS:
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
                    failures.append(f"{label}: prose is not supposed to trip anything\n{output[-1500:]}")
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
