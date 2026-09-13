#!/usr/bin/env python3
"""Prove `tests/test_command_center_ai_routing.py` fails when U10 is undone.

§50. Sixth harness of its kind, and the one with the least to lose: `command_center_worker`
is not in the Procfile, so all five tasks are unreachable in production today. Nothing
breaks if this migration is wrong, which is exactly the condition under which a suite
quietly stops measuring anything. Hence mutations rather than inspection.

U10 was not a transport migration. There was no `requests.post` to delete and no API key to
stop reading — `_provider_adapter` read `PULSE_AI_PROVIDER` and then returned
"provider_adapter_pending" whatever the answer was. What it was instead was a module that
already **spoke as though it had executed**, so the mutation families here are different
from U1-U4's:

* **Re-asserting attribution that no provider produced.** `response.setdefault("model",
  ai_model())` and the literal status `"unavailable"`. Both are one line, neither changes a
  single answer any user sees, and between them they wrote a model name and a status into
  `command_center_ai_events` for calls that never left the process. An audit table is the
  thing you read when you no longer remember, which makes it the worst place in this repo
  for that confusion.

* **Prompts that assert evidence the payload does not carry.** Removing the
  `STRUCTURED_INPUT_KEYS` branch restores the original defect exactly: the summary falls
  back to "no raw message body stored" underneath a prompt promising "the verdict and
  signals recorded below". The suite has to notice, because a model will not — it will
  describe signals it cannot see, and the answer will read fine.

* **Prompt audience and intent.** Six mutations move a prompt's reader or delete the
  sentence that keeps a human in the loop. None is detectable from a response shape.

* **Reaching past the seam, into the probe.** One mutation reverts
  `undx_source_probe.env_wrappers` to the single-tier version it had before this phase.
  `ai_messaging` reads through two tiers (`_env_bool` → `_env_text` → `os.getenv`), so the
  one-tier probe reported `PULSE_AI_MAX_CONTEXT_MESSAGES` and was blind to
  `PULSE_AI_ENABLED` and `PULSE_AI_INTERNAL_ONLY` — the feature switch and the privacy
  gate. Every `assertNotIn` keyed to that probe was therefore satisfiable by reading a
  vendor variable through the second tier. That is the mutation immediately after it.

* **Prose (must stay GREEN).** Five trailing mutations spell out, in comments and
  docstrings, the vendor hosts, the removed environment variables and the old "pending"
  reason. Every mutation above adds mechanism; a check that also fires on the paragraph
  explaining the rule is indistinguishable from a correct one from inside a harness like
  this, and deleting the explanation would be the cheapest way to green. `ai_messaging`
  deliberately names `PULSE_AI_PROVIDER` and `PULSE_AI_MODEL` in a comment block that
  records what was removed and why.

Never mutates the working tree: `build_sandbox` symlinks the repo and makes exactly the one
mutated file real. Shared with the sibling harnesses rather than reimplemented, which is
also what keeps it §53-compliant — there is no `git stash` anywhere in it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

TESTS = "tests/test_command_center_ai_routing.py"

AI = "services/command_center_worker/ai_messaging.py"
PROBE = "tests/undx_source_probe.py"

#: Each mutation is (label, target file, old, new, test that must fail).
#:
#: `expect=None` means the mutation must be *allowed* and the suite has to stay green.
MUTATIONS = [
    # ---- Attribution is execution: the two false claims this phase removed ----------
    (
        # The exact line that was there. `ai_model()` is gone, so the mutation has to bring
        # back a source for the string too — which is the honest version of the mutation,
        # because that is what restoring this would actually require.
        "record a model name for a call no provider answered",
        AI,
        '    status = "completed" if response.get("available") else "unavailable"',
        '    response.setdefault("model", os.getenv("PULSE_AI_MODEL", "gpt-4o"))\n'
        '    status = "completed" if response.get("available") else "unavailable"',
        "test_nothing_answered_means_there_is_no_model",
    ),
    (
        "hardcode the audit status back to unavailable",
        AI,
        '    status = "completed" if response.get("available") else "unavailable"',
        '    status = "unavailable"',
        "test_the_recorded_status_is_the_one_that_happened",
    ),
    (
        # The mirror image, and the reason the status test needs both halves: a status
        # hardcoded to the *success* value is just as wrong and fails a different test.
        "hardcode the audit status to completed",
        AI,
        '    status = "completed" if response.get("available") else "unavailable"',
        '    status = "completed"',
        "test_a_failure_is_recorded_as_a_failure",
    ),
    (
        "report the operator's model instead of the one that answered",
        AI,
        '        "model": _clean_text(envelope.get("model"), 120),',
        '        "model": _clean_text(os.getenv("PULSE_AI_MODEL") or envelope.get("model"), 120),',
        "test_the_module_no_longer_reads_a_provider_or_model_from_the_environment",
    ),

    # ---- The record has to reach the model the prompt promised it to ----------------
    (
        # Restores the live defect verbatim: `security_event` is a dict, the five string
        # keys below never match a dict, and the fallback sentence goes out under a prompt
        # asserting a recorded verdict.
        "drop the structured-record branch, restoring the invented-evidence defect",
        AI,
        '    for key in STRUCTURED_INPUT_KEYS:\n'
        '        if isinstance(payload.get(key), dict) and payload.get(key):\n'
        '            record = _record_lines(payload[key])\n'
        '            if record:\n'
        '                return "\\n".join(record)[:MAX_INPUT_SUMMARY_CHARS]\n',
        '',
        "test_the_production_security_event_arrives_in_the_user_turn",
    ),
    (
        # A shallower version: the record is rendered but nested values are not, so
        # `details` — which is where the attempt count and the country live — vanishes. The
        # summary still looks populated, which is what makes this the dangerous shape.
        "render only the top level of the record",
        AI,
        '    if depth > 3:',
        '    if depth > 1:',
        "test_the_production_security_event_arrives_in_the_user_turn",
    ),
    (
        # The anchor carries the following line too. `_sanitize_payload` applies the same
        # exclusion with the same wording, so the guard line alone matches twice and the
        # harness refused to guess which one was meant — correctly.
        "stop excluding secret-shaped keys from the rendered record",
        AI,
        '            if not safe_key or any(marker in safe_key.lower() for marker in SECRET_KEY_MARKERS):\n'
        '                # The same exclusion list `_sanitize_payload` applies to stored payloads. A',
        '            if not safe_key:\n'
        '                # The same exclusion list `_sanitize_payload` applies to stored payloads. A',
        "test_secret_shaped_keys_are_dropped_from_the_record",
    ),
    (
        # SURVIVED the first version of the stability test, which ran one dict twice and
        # compared — insertion order made that pass with or without the sort. The test was
        # rewritten to compare two dicts with equal content in different order.
        "render record keys in insertion order instead of sorted",
        AI,
        '        for key in sorted(str(name) for name in value):',
        '        for key in (str(name) for name in value):',
        "test_the_record_is_rendered_in_a_stable_order",
    ),
    (
        # SURVIVED on the first run, and the reason is worth keeping: the only test for the
        # honest fallback passed `{}`, which the *outer* guard rejects for being falsy, so
        # it never reached the inner one this mutation deletes. Two guards, one covered.
        # `test_a_record_that_renders_to_nothing_also_says_so` was written for the other:
        # a non-empty record whose every key is excluded as secret-shaped renders to no
        # lines, and without `if record:` that becomes an empty user turn sent under a
        # prompt promising a recorded verdict.
        "let a record that renders to nothing replace the honest fallback sentence",
        AI,
        '            record = _record_lines(payload[key])\n'
        '            if record:\n'
        '                return "\\n".join(record)[:MAX_INPUT_SUMMARY_CHARS]',
        '            record = _record_lines(payload[key])\n'
        '            return "\\n".join(record)[:MAX_INPUT_SUMMARY_CHARS]',
        "test_a_record_that_renders_to_nothing_also_says_so",
    ),
    (
        "prefer the structured record over the conversation",
        AI,
        '    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []',
        '    messages = []',
        "test_a_conversation_still_wins_over_a_record",
    ),

    # ---- The prompt's audience, and the sentences that keep a human in the loop ------
    (
        # The draft this phase corrected. Written from the task's name; no member can reach
        # the route.
        "address the scam explanation to the member under investigation",
        AI,
        '        "You are writing for an administrator reviewing a security event. A deterministic "',
        '        "Explain, for the member, what this security event means. A deterministic "',
        "test_the_scam_explanation_is_written_for_an_administrator",
    ),
    (
        "let the model overturn the deterministic verdict",
        AI,
        '        "verdict as your own finding, do not overturn it, and do not declare anything safe. "',
        '        "verdict as your own finding. State whether you agree with it. "',
        "test_the_scam_explanation_may_not_overturn_the_deterministic_verdict",
    ),
    (
        "stop telling the model what to do with an empty record",
        AI,
        '        "If the record below contains no signals, say that it contains none rather than "\n'
        '        "describing signals it does not contain."',
        '        "Describe the signals in the record below."',
        "test_the_scam_explanation_is_told_what_to_do_with_an_empty_record",
    ),
    (
        "let the moderation insight decide the outcome",
        AI,
        '        "settle it. Do not decide the outcome and do not recommend an action."',
        '        "settle it. Recommend the action the reviewer should take."',
        "test_the_moderation_insight_does_not_decide_the_outcome",
    ),
    (
        "let the translation task translate",
        AI,
        '        "(names, product terms, amounts). Do not translate the message."',
        '        "(names, product terms, amounts), then translate the message."',
        "test_translation_preparation_does_not_translate",
    ),
    (
        "name a vendor inside a prompt",
        AI,
        '        "the member wants and what has already been tried. Do not speculate about anything "',
        '        "the member wants and what has already been tried. You are Claude. Do not speculate about anything "',
        "test_no_prompt_names_a_vendor",
    ),
    (
        "flatten the drafting temperature to zero",
        AI,
        '    "smart_replies": {"timeout": 20, "temperature": 0.5, "max_tokens": 380},',
        '    "smart_replies": {"timeout": 20, "temperature": 0.0, "max_tokens": 380},',
        "test_only_the_drafting_task_gets_a_creative_temperature",
    ),
    (
        "let the security analysis sample creatively",
        AI,
        '    "scam_explanation": {"timeout": 20, "temperature": 0.1, "max_tokens": 420},',
        '    "scam_explanation": {"timeout": 20, "temperature": 0.9, "max_tokens": 420},',
        "test_only_the_drafting_task_gets_a_creative_temperature",
    ),

    # ---- §4: the privacy class, which is never lowered to make routing possible -----
    (
        "lower the privacy class to PUBLIC",
        AI,
        'AI_MESSAGING_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL',
        'AI_MESSAGING_PRIVACY_CLASS = undx_privacy.SENSITIVITY_PUBLIC',
        "test_all_five_tasks_declare_confidential",
    ),
    (
        # §35's typo family. This one fails *closed* — an unknown name ranks SECRET, so
        # every call refuses — and is still a bug, because the symptom is a total outage
        # reported as a privacy decision.
        "misspell the privacy class",
        AI,
        'AI_MESSAGING_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL',
        'AI_MESSAGING_PRIVACY_CLASS = "CONFIDENTAIL"',
        "test_the_declared_class_is_a_class_the_router_knows",
    ),
    (
        "stop declaring a privacy class at all",
        AI,
        '        privacy_class=AI_MESSAGING_PRIVACY_CLASS,\n',
        '',
        "test_all_five_tasks_declare_confidential",
    ),
    (
        "send message bodies without redacting them",
        AI,
        '            body = _redact_text(item.get("body") or item.get("text") or item.get("preview") or "", 240)',
        '            body = _clean_text(item.get("body") or item.get("text") or item.get("preview") or "", 240)',
        "test_redaction_is_not_declassification",
    ),

    # ---- §5: routing may use domain, permissions may not ----------------------------
    (
        "let the payload choose its own call domain",
        AI,
        '        call_domain=AI_TASK_DOMAINS[task_type],',
        '        call_domain=payload.get("call_domain") or AI_TASK_DOMAINS[task_type],',
        "test_a_payload_cannot_choose_its_own_domain",
    ),
    (
        "route the scam explanation as ordinary messaging",
        AI,
        '    "scam_explanation": undx_call_domain.CALL_DOMAIN_SCAM_SHIELD,',
        '    "scam_explanation": undx_call_domain.CALL_DOMAIN_MESSAGING,',
        "test_each_task_declares_its_own_domain",
    ),
    (
        "declare a domain the router does not recognise",
        AI,
        '    "moderation_insight": undx_call_domain.CALL_DOMAIN_SECURITY,\n}',
        '    "moderation_insight": "SECURITY_REVIEW",\n}',
        "test_every_declared_domain_is_one_the_router_recognises",
    ),

    # ---- §17: the boundary is stated, not assumed -----------------------------------
    (
        "drop the untrusted-content rule from the system prompt",
        AI,
        '        f"{AI_TASK_PROMPTS[task_type]}\\n\\n{UNTRUSTED_CONTENT_RULE}",',
        '        AI_TASK_PROMPTS[task_type],',
        "test_the_rule_is_in_every_system_prompt",
    ),
    (
        # The shape §17 exists to forbid: member-authored text appended to the instruction
        # block. The user turn is still passed, so every behavioural test still passes.
        "append the member's content to the system block",
        AI,
        '        f"{AI_TASK_PROMPTS[task_type]}\\n\\n{UNTRUSTED_CONTENT_RULE}",\n'
        '        _input_summary(task_type, payload),',
        '        f"{AI_TASK_PROMPTS[task_type]}\\n\\n{UNTRUSTED_CONTENT_RULE}\\n\\n"\n'
        '        f"{_input_summary(task_type, payload)}",\n'
        '        _input_summary(task_type, payload),',
        "test_an_injection_stays_in_the_user_turn",
    ),

    # ---- The privacy gate: closed until a deployment opens it -----------------------
    (
        "default the internal-only gate to open",
        AI,
        '    return _env_bool("PULSE_AI_INTERNAL_ONLY", True)',
        '    return _env_bool("PULSE_AI_INTERNAL_ONLY", False)',
        "test_the_default_is_closed",
    ),
    (
        # Check the gate but check it too late. The response is identical; the conversation
        # has already been sent.
        "check the privacy gate after calling the router",
        AI,
        '    if internal_only():\n'
        '        return _provider_unavailable_response(task_type, "internal_only")\n'
        '    parameters = AI_TASK_PARAMETERS[task_type]',
        '    parameters = AI_TASK_PARAMETERS[task_type]',
        "test_nothing_is_sent_while_the_gate_is_closed",
    ),
    (
        "report a privacy refusal as a disabled feature",
        AI,
        '        return _provider_unavailable_response(task_type, "internal_only")',
        '        return _provider_unavailable_response(task_type, "ai_disabled")',
        "test_nothing_is_sent_while_the_gate_is_closed",
    ),

    # ---- One "unavailable" for three problems with three owners ---------------------
    (
        "collapse the router's reason into a constant",
        AI,
        '            task_type, _clean_text(envelope.get("error") or "no provider answered", 240))',
        '            task_type, "unavailable")',
        "test_a_budget_refusal_says_so",
    ),
    (
        "accept an empty answer as a completed call",
        AI,
        '    if not answer:\n'
        '        return _provider_unavailable_response(task_type, "provider returned an empty answer")\n',
        '',
        "test_an_empty_answer_is_a_failure_not_a_success",
    ),

    # ---- §11-12, module-scoped ------------------------------------------------------
    (
        "post directly to a provider",
        AI,
        'def _provider_adapter(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:',
        'def _direct_chat(prompt: str) -> Any:\n'
        '    import requests\n'
        '    return requests.post("https://api.openai.com/v1/chat/completions",\n'
        '                         json={"messages": [{"role": "user", "content": prompt}]},\n'
        '                         timeout=20)\n'
        '\n'
        '\n'
        'def _provider_adapter(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:',
        "test_the_module_makes_no_transport_call",
    ),
    (
        "leave a vendor host in a string the module could request",
        AI,
        'STRUCTURED_INPUT_KEYS = ("security_event", "report", "moderation_report")',
        'STRUCTURED_INPUT_KEYS = ("security_event", "report", "moderation_report")\n'
        'FALLBACK_ENDPOINT = "https://api.anthropic.com/v1/messages"',
        "test_the_module_holds_no_provider_host",
    ),
    (
        # §12's named shape: the host comes from an environment variable, so only the route
        # path is visible in the source. A host-only check sees nothing here.
        "compose a provider URL from an env-pointable base",
        AI,
        'STRUCTURED_INPUT_KEYS = ("security_event", "report", "moderation_report")',
        'STRUCTURED_INPUT_KEYS = ("security_event", "report", "moderation_report")\n'
        'CHAT_ROUTE = "/v1/chat/completions"',
        "test_the_module_holds_no_provider_route_path",
    ),
    (
        # A module-scope `import openai` was the obvious mutation and it was the wrong one:
        # the package is not installed here, so it died during collection with
        # ModuleNotFoundError and never reached the assertion it was meant to prove. That
        # counts as no evidence — the suite would have "caught" it with the SDK test
        # deleted. A lazy import inside an uncalled helper is both the realistic shape (a
        # legacy client kept behind a function to avoid a hard dependency) and the one that
        # isolates the claim: the module still imports, every behavioural test still passes,
        # and only the AST check can see it.
        "construct a provider SDK behind a lazy import",
        AI,
        'def _provider_adapter(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:',
        'def _legacy_client():\n'
        '    import openai\n'
        '    return openai.OpenAI()\n'
        '\n'
        '\n'
        'def _provider_adapter(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:',
        "test_the_module_constructs_no_provider_sdk",
    ),
    (
        # SURVIVED before the probe was made transitive. `_env_bool` forwards to
        # `_env_text`, which forwards to `os.getenv`; the single-tier `env_wrappers` knew
        # only the second hop, so a credential read through `_env_bool` was invisible to
        # every absence assertion in the file.
        "read a provider credential through the module's second-tier env helper",
        AI,
        '    return _env_bool("PULSE_AI_ENABLED", False)',
        '    if _env_bool("OPENAI_API_KEY", False):\n'
        '        return True\n'
        '    return _env_bool("PULSE_AI_ENABLED", False)',
        "test_the_module_reads_no_provider_credential",
    ),
    (
        # Reaching past the seam into the probe itself. Reverts `env_wrappers` to the
        # single-tier version. The mutation above is the reason this matters: with the
        # one-tier probe it survives, and so do three absence assertions.
        "revert the env probe to a single tier of wrapper",
        PROBE,
        '                elif isinstance(target, ast.Name):\n'
        '                    reads_env = target.id in wrappers and target.id != node.name',
        '                elif isinstance(target, ast.Name):\n'
        '                    reads_env = False',
        "test_the_module_no_longer_reads_a_provider_or_model_from_the_environment",
    ),

    # ---- The worker contract four route handlers branch on -------------------------
    (
        "stop answering the key the client reads",
        AI,
        '        "available": True,\n'
        '        "status": "completed",',
        '        "status": "completed",',
        "test_a_success_still_answers_the_keys_the_client_reads",
    ),
    (
        "turn a provider outage into a failed service call",
        AI,
        'def _provider_unavailable_response(task_type: str, reason: str) -> dict[str, Any]:\n'
        '    return {\n'
        '        "ok": True,',
        'def _provider_unavailable_response(task_type: str, reason: str) -> dict[str, Any]:\n'
        '    return {\n'
        '        "ok": False,',
        "test_the_envelope_stays_ok_when_the_provider_does_not",
    ),

    # ---- Prose: must stay GREEN ----------------------------------------------------
    (
        "name the vendor hosts in a comment (must stay GREEN)",
        AI,
        '#: Payload keys holding a structured record rather than prose.',
        '# A comment is not a call. api.openai.com/v1/chat/completions,\n'
        '# api.anthropic.com/v1/messages and generativelanguage.googleapis.com are what this\n'
        '# module is forbidden to reach, and saying so must not be what trips the check.\n'
        '#: Payload keys holding a structured record rather than prose.',
        None,
    ),
    (
        "name the removed environment variables in a docstring (must stay GREEN)",
        AI,
        '    """One model turn, reached only through the router.',
        '    """One model turn, reached only through the router.\n'
        '\n'
        '    This used to read PULSE_AI_PROVIDER and PULSE_AI_MODEL and then return\n'
        '    "provider_adapter_pending" whatever the answer was. A docstring is an\n'
        '    ast.Constant, so a literal scan sees this paragraph; naming what was removed\n'
        '    must not be what makes the check fire.',
        None,
    ),
    (
        "put a route path in a comment (must stay GREEN)",
        AI,
        '    if not envelope.get("ok"):',
        '    # The router owns the /v1/chat/completions and /v1/messages call shapes, not this\n'
        '    # module. Naming them here is documentation, not a request.\n'
        '    if not envelope.get("ok"):',
        None,
    ),
    (
        "name a vendor in a prompt table comment (must stay GREEN)",
        AI,
        '#: One system prompt per task.',
        '#: One system prompt per task. Not one of them says Claude, OpenAI, Gemini or\n'
        '#: DeepSeek, and this line saying so is a comment on the table rather than a value\n'
        '#: inside it — the check reads the table.\n'
        '#: One system prompt per task.',
        None,
    ),
    (
        "explain the old reason strings in the test file's own prose (must stay GREEN)",
        TESTS,
        '"""The four AI routes nobody can reach yet, and the one that would have invented evidence.',
        '"""The four AI routes nobody can reach yet, and the one that would have invented evidence.\n'
        '\n'
        'The stub returned "provider_adapter_pending" and "provider_not_configured", read\n'
        'PULSE_AI_PROVIDER and PULSE_AI_MODEL, and the prompt said "Explain, for the member".\n'
        'Naming all of that is how the file explains itself, and must not be how it passes.',
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
