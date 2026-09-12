#!/usr/bin/env python3
"""Prove `tests/test_pulse_ai_provider_reconciliation.py` fails when §13 is undone.

§50. This is the third harness of its kind and the widest, because §13 is a different
kind of change from U1 and U2. Those migrated a call; this retired a *router*. So the
mutations come in families that the two sibling harnesses have no equivalent for:

* **Rebuilding the thing that was removed.** A second provider table, a model default,
  a credential read, a transport. Each is one line, each restores a piece of the
  duplicate authority, and none of them breaks a single behavioural test — which is
  precisely why they are the mutations that matter. The product ran for months with a
  second table naming two models that had been retired upstream and returned 404 to
  every request; the only symptom was that UNDX felt slow.

* **The envelope translation layer.** `_translate_attempts` and `_failure_reason` look
  like boilerplate and are not. Passing the router's attempts through unchanged writes
  every row in `pulse_ai_provider_events` as `failed` with a blank reason, including
  the successful one. Lowercasing a display label writes `meta muse` where the ledger
  has always stored `meta`, and the admin dashboard groups by that column. Collapsing a
  privacy refusal into `all_providers_failed` pages out a classification decision as an
  outage. None of these are visible from the reply the user gets.

* **Reaching past the seam.** One mutation targets `undx_router` rather than the
  reconciled module: restoring the hardcoded `history = []` that was the entire reason a
  second router existed. Every behavioural test in the suite mocks
  `route_structured_request`, so all of them pass against a router that accepts history
  and discards it. That mutation is what forces the one test in the file that drives a
  real adapter.

* **Prose (must stay GREEN).** Four trailing mutations spell out, in full, the endpoints,
  model names and credential names this reconciliation removed — in a module docstring,
  a function docstring, a comment, and the test file's own prose. They exist because
  every mutation above only ever *adds mechanism*, so a check that fires on the paragraph
  explaining the rule is indistinguishable from a correct one from inside a harness like
  this. The module docstring here deliberately names `claude-3-5-haiku-latest` and
  `gemini-1.5-flash` — the two stale models — so a naive substring check would have
  made deleting that explanation the cheapest way to green.

Two mutations in this file were written expecting to be caught and were not, which is
the entire justification for running it:

* `PROVIDER_ORDER = ["openai", "claude", "gemini"]` at module scope **survived**. The
  literal scan could not tell a rebuilt table from `_task_preference`'s ordering hints,
  because it reads literals module-wide and those five names legitimately appear. Fixed
  by adding a structural scan for a module-level collection of provider names, which is
  what a table is and what a function-local list is not.
* Reading `OPENAI_API_KEY` **survived**, because the module reads every variable through
  its own `_env_text` helper and `undx_source_probe.environment_reads` only understood
  `os.getenv` directly. Three absence assertions were therefore vacuous. Fixed in the
  probe by detecting env wrappers rather than by naming `_env_text` in a test.

Never mutates the working tree: `build_sandbox` symlinks the repo and makes exactly the
one mutated file real. Shared with the sibling harnesses rather than reimplemented, and
it is also what keeps this §53-compliant — there is no `git stash` anywhere in it.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from undx_call_domain_mutation_check import build_sandbox  # noqa: E402

TESTS = "tests/test_pulse_ai_provider_reconciliation.py"

PROVIDER = "services/pulse_ai_provider_router.py"
ROUTER = "undx_router.py"
SERVICE = "services/pulse_ai_service.py"

#: Each mutation is (label, target file, old, new, test that must fail).
#:
#: `expect=None` means the mutation must be *allowed* and the suite has to stay green.
MUTATIONS = [
    # ---- §11-12: the transports are gone, and so is the invitation -----------------
    (
        "restore a direct provider POST",
        PROVIDER,
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        'def _direct_chat(payload: dict) -> Any:\n'
        '    return requests.post("https://api.openai.com/v1/chat/completions",\n'
        '                         json=payload, timeout=20)\n'
        '\n'
        '\n'
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        "test_the_module_performs_no_http_of_its_own",
    ),
    (
        # Weaker than the mutation above and the one worth having anyway: an import is
        # not a call, so nothing behaves differently, and the next person to need an
        # HTTP call in this file will see it and reasonably conclude that is allowed.
        "re-import requests without calling it",
        PROVIDER,
        'import undx_router\n',
        'import requests\n'
        '\n'
        'import undx_router\n',
        "test_requests_is_not_even_imported_any_more",
    ),
    (
        "put a vendor endpoint back in a string the module could request",
        PROVIDER,
        'UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."',
        'UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."\n'
        'CLAUDE_URL = "https://api.anthropic.com/v1/messages"',
        "test_no_vendor_endpoint_survives_as_a_string_it_could_request",
    ),
    (
        # SURVIVED on the first run. The module reads every variable through `_env_text`,
        # and `environment_reads` only understood `os.getenv` at the call site, so it
        # reported this file as reading *nothing* while it demonstrably read three
        # variables. Three absence assertions were vacuous. Fixed by teaching the probe
        # to detect env wrappers, not by naming `_env_text` in the test.
        "read a provider credential again, through the module's own env helper",
        PROVIDER,
        '    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES',
        '    if _env_text("OPENAI_API_KEY"):\n'
        '        return True\n'
        '    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES',
        "test_no_provider_credential_is_read_here",
    ),
    (
        # §12's named shape. `UNDX_CANDIDATE` composed `f"{base}/chat/completions"`, so
        # no URL-literal scanner could see where it pointed. That is why the candidate was
        # retired rather than migrated, and why the *base URL* is what this forbids —
        # `UNDX_CANDIDATE_ENABLED` is still read on purpose.
        "restore the retired candidate's operator-pointable base URL",
        PROVIDER,
        '    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES',
        '    if _env_text("UNDX_CANDIDATE_BASE_URL"):\n'
        '        return True\n'
        '    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES',
        "test_the_retired_candidate_left_no_pointable_endpoint_behind",
    ),

    # ---- §25-27: one table owns the models ----------------------------------------
    (
        # The exact string the deleted table held, retired upstream and 404ing.
        "name a model default in this module again",
        PROVIDER,
        'DEFAULT_TIMEOUT_SECONDS = 18',
        'DEFAULT_TIMEOUT_SECONDS = 18\n'
        'DEFAULT_MODEL = "claude-3-5-haiku-latest"',
        "test_the_model_for_every_provider_comes_from_one_table",
    ),
    (
        "let an environment variable override the router's model",
        PROVIDER,
        '            "model": undx_router._model(name),',
        '            "model": _env_text("PULSE_AI_CLAUDE_MODEL") or undx_router._model(name),',
        "test_the_model_for_every_provider_comes_from_one_table",
    ),
    (
        # SURVIVED on the first run: the literal scan cannot tell which function a
        # literal came from, and all three of these names legitimately appear in
        # `_task_preference`'s ordering hints. Fixed by adding a structural scan for a
        # module-level collection of provider names.
        "rebuild a provider table out of names the ordering hints already use",
        PROVIDER,
        'DEFAULT_TIMEOUT_SECONDS = 18',
        'DEFAULT_TIMEOUT_SECONDS = 18\n'
        'PROVIDER_ORDER = ["openai", "claude", "gemini"]',
        "test_there_is_no_second_provider_table",
    ),
    (
        "name a provider the ordering hints do not",
        PROVIDER,
        'UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."',
        'UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."\n'
        'SPECIALIST = "perplexity"',
        "test_there_is_no_second_provider_table",
    ),

    # ---- §4: a privacy class, declared, named, and not lowerable -------------------
    (
        "stop declaring a privacy class on the routed request",
        PROVIDER,
        '        privacy_class=MESSENGER_PRIVACY_CLASS,\n',
        '',
        "test_the_privacy_class_is_declared_and_is_confidential",
    ),
    (
        # §4's named failure and the tempting one: PUBLIC admits every provider where
        # CONFIDENTIAL admits three, so this makes routing *succeed more often*. It is
        # still wrong — the content is what a person typed plus their retrieved memory.
        "widen routing by lowering the messenger's class to PUBLIC",
        PROVIDER,
        'MESSENGER_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL',
        'MESSENGER_PRIVACY_CLASS = undx_privacy.SENSITIVITY_PUBLIC',
        "test_the_privacy_class_is_declared_and_is_confidential",
    ),
    (
        # §35. `UNDX_SHADOW_MAX_PRIVACY_CLASS=PUBIC` once cleared CONFIDENTIAL content,
        # because an unrecognised name ranks SECRET — right for a request, inverted for a
        # ceiling. A literal spelt correctly today passes every behavioural assertion.
        "declare the privacy class as a bare string literal",
        PROVIDER,
        'MESSENGER_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL',
        'MESSENGER_PRIVACY_CLASS = "CONFIDENTIAL"',
        "test_the_privacy_class_is_a_constant_not_a_string_literal",
    ),
    (
        # The asymmetry §5 requires. A caller may reorder providers; it may never say the
        # content is less sensitive than it is. Adding the parameter is the whole failure
        # — no call site has to pass it for the ceiling to have become negotiable.
        "make the privacy class something a caller can pass",
        PROVIDER,
        '    task: str = "general",\n'
        '    user_id: Any = None,\n'
        '    call_domain: str | None = None,\n'
        ') -> dict[str, Any]:\n'
        '    """One grounded, identity-verified UNDX messenger turn.',
        '    task: str = "general",\n'
        '    user_id: Any = None,\n'
        '    call_domain: str | None = None,\n'
        '    privacy_class: str = MESSENGER_PRIVACY_CLASS,\n'
        ') -> dict[str, Any]:\n'
        '    """One grounded, identity-verified UNDX messenger turn.',
        "test_a_caller_may_override_the_domain_but_not_the_privacy_class",
    ),

    # ---- §5: a call domain, declared and known -------------------------------------
    (
        "stop declaring a call domain",
        PROVIDER,
        '        call_domain=call_domain or MESSENGER_CALL_DOMAIN,\n',
        '',
        "test_the_call_domain_is_declared_and_is_messaging",
    ),
    (
        "bill the turn to nobody",
        PROVIDER,
        '    return undx_router.route_structured_request(\n'
        '        user_id,\n',
        '    return undx_router.route_structured_request(\n'
        '        None,\n',
        "test_the_user_id_reaches_the_router_so_the_spend_has_an_owner",
    ),

    # ---- the second routed request is still a routed request -----------------------
    (
        # The regeneration path is the easiest gap in the file to miss: it runs only when
        # a model has already broken identity, which is rare, and it produces the answer
        # the user actually reads.
        "let the regeneration bypass the seam and forget its declarations",
        PROVIDER,
        '        retry = _route(f"{system_prompt}\\n\\n{correction}", history, user_content,\n'
        '                       task=task, user_id=user_id, call_domain=call_domain,\n'
        '                       providers=[provider] if provider in undx_router.PROVIDERS else None)',
        '        retry = undx_router.route_structured_request(\n'
        '            user_id, f"{system_prompt}\\n\\n{correction}", user_content,\n'
        '            history=history, timeout=int(_timeout()), temperature=0.35,\n'
        '            max_tokens=850,\n'
        '            providers=[provider] if provider in undx_router.PROVIDERS else None)',
        "test_the_regeneration_request_declares_the_same_things",
    ),
    (
        # A *correct* second seam — it declares everything — so only the one-seam
        # assertion can object. Two seams are two argument lists to keep in agreement,
        # and the privacy class is among the arguments.
        "add a second, fully-declared seam to the router",
        PROVIDER,
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        'def _route_again(system_prompt: str, user_content: str, user_id: Any) -> dict:\n'
        '    return undx_router.route_structured_request(\n'
        '        user_id, system_prompt, user_content,\n'
        '        privacy_class=MESSENGER_PRIVACY_CLASS,\n'
        '        call_domain=MESSENGER_CALL_DOMAIN)\n'
        '\n'
        '\n'
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        "test_execution_leaves_through_exactly_one_seam",
    ),

    # ---- task is a capability hint, not provenance and not permission --------------
    (
        "let the task set the call domain",
        PROVIDER,
        '        call_domain=call_domain or MESSENGER_CALL_DOMAIN,',
        '        call_domain=call_domain or (undx_call_domain.CALL_DOMAIN_SECURITY\n'
        '                                    if "cyber" in (task or "") else MESSENGER_CALL_DOMAIN),',
        "test_every_task_reaches_providers_and_not_the_domain",
    ),
    (
        # "This turn is about the web" quietly becoming "therefore it may go anywhere a
        # public question may go". The inference is upstream and unreliable; the ceiling
        # is a claim about content.
        "let the task lower the privacy class",
        PROVIDER,
        '        privacy_class=MESSENGER_PRIVACY_CLASS,',
        '        privacy_class=(undx_privacy.SENSITIVITY_PUBLIC if "web" in (task or "")\n'
        '                       else MESSENGER_PRIVACY_CLASS),',
        "test_every_task_reaches_providers_and_not_the_domain",
    ),
    (
        "drop the task's ordering hint on the floor",
        PROVIDER,
        '        providers=providers if providers is not None else (configured_providers_for_task(task) or None),',
        '        providers=providers,',
        "test_a_recognised_task_actually_reorders_the_chain",
    ),
    (
        # An empty list is not "no preference" to `route_structured_request` — it is a
        # filtered-to-nothing preference, and the difference decides whether the router
        # classifies the text itself or falls back to a default provider.
        "send an empty preference instead of no preference",
        PROVIDER,
        '        providers=providers if providers is not None else (configured_providers_for_task(task) or None),',
        '        providers=providers if providers is not None else configured_providers_for_task(task),',
        "test_an_unrecognised_task_expresses_no_preference",
    ),
    (
        "let the inferred task outrank the operator's explicit order",
        PROVIDER,
        '    preferred = [item.strip().lower() for item in _env_text("PULSE_AI_PROVIDER_ORDER").split(",") if item.strip()]\n'
        '    if not preferred:\n'
        '        preferred = _task_preference(task)',
        '    preferred = _task_preference(task)\n'
        '    if not preferred:\n'
        '        preferred = [item.strip().lower() for item in _env_text("PULSE_AI_PROVIDER_ORDER").split(",") if item.strip()]',
        "test_an_operator_order_wins_over_the_task_preference",
    ),
    (
        # Does not degrade the ordering — throws. `undx_router._api_key` raises KeyError
        # for a name `PROVIDERS` does not have, and the comprehension below calls it on
        # every surviving name. One transposed character in an environment variable an
        # operator edits during an incident, and the messenger stops answering.
        "forward an unknown provider name instead of dropping it",
        PROVIDER,
        '    ordered = [name for name in preferred if name in known]',
        '    ordered = [name for name in preferred]',
        "test_a_typo_in_the_operator_order_costs_only_that_name",
    ),

    # ---- grounding: four blocks, in order, in front of the question ----------------
    (
        "take only the first system message and drop the caller's addendum",
        PROVIDER,
        '    return "\\n\\n".join(part for part in system_parts if part), turns[:-1], turns[-1]["content"]',
        '    return (system_parts[0] if system_parts else ""), turns[:-1], turns[-1]["content"]',
        "test_a_caller_inserted_system_message_survives_the_split",
    ),
    (
        "take only the last system message and displace the identity block",
        PROVIDER,
        '    return "\\n\\n".join(part for part in system_parts if part), turns[:-1], turns[-1]["content"]',
        '    return (system_parts[-1] if system_parts else ""), turns[:-1], turns[-1]["content"]',
        "test_the_identity_block_leads_the_prompt_the_provider_receives",
    ),
    (
        # The migration would look complete and UNDX would have quietly lost its memory
        # of the conversation it is in the middle of.
        "drop the conversation on the way to the router",
        PROVIDER,
        '    return "\\n\\n".join(part for part in system_parts if part), turns[:-1], turns[-1]["content"]',
        '    return "\\n\\n".join(part for part in system_parts if part), [], turns[-1]["content"]',
        "test_the_conversation_survives_as_history_and_the_last_turn_is_the_question",
    ),
    (
        # Repairing it answers a different conversation, silently, and an invented empty
        # user turn asks a model to speak unprompted.
        "repair a conversation that does not end on the user instead of refusing it",
        PROVIDER,
        '    if not turns or turns[-1]["role"] != "user":\n'
        '        raise PulseAIProviderError("undx_router", "final_user_turn_required")',
        '    if not turns:\n'
        '        raise PulseAIProviderError("undx_router", "final_user_turn_required")\n'
        '    if turns[-1]["role"] != "user":\n'
        '        turns.append({"role": "user", "content": ""})',
        "test_a_conversation_not_ending_on_the_user_is_refused_not_repaired",
    ),
    (
        # Caught twice over: the `if` goes, and the bare `assert` two lines down still
        # stops it. Defence in depth is the intended design, and the test still reports
        # the failure, so this mutation records both facts rather than only one.
        "stop verifying that the identity block is present",
        PROVIDER,
        '    if not identity_present or not company_present or not capability_present or not fact_policy_present:',
        '    if not company_present or not capability_present or not fact_policy_present:',
        "test_a_missing_grounding_block_fails_closed_without_calling_a_provider",
    ),
    (
        # §3. Inheriting the router's structured-output defaults turns every UNDX
        # conversation into a terse machine answer: no error, no failing test, no log.
        "inherit the router's structured-output voice instead of the messenger's",
        PROVIDER,
        '        temperature=0.35,\n'
        '        max_tokens=850,\n',
        '',
        "test_the_messenger_voice_is_preserved_not_inherited",
    ),
    (
        "ignore the operator's configured timeout",
        PROVIDER,
        '        timeout=int(_timeout()),',
        '        timeout=DEFAULT_TIMEOUT_SECONDS,',
        "test_the_configured_timeout_still_reaches_the_router",
    ),

    # ---- verification: the output-side check this module exists for ----------------
    (
        "stop checking the answer's identity at all",
        PROVIDER,
        '    violation = undx_identity_violation(reply)\n'
        '    regenerated = False',
        '    violation = ""\n'
        '    regenerated = False',
        "test_a_broken_identity_is_regenerated_on_the_provider_that_broke_it",
    ),
    (
        # Answers a different question. "Can this model hold the line when told directly"
        # becomes "can some model", and then reports the second provider's success as the
        # first one's correction.
        "regenerate on a fresh chain rather than the provider that broke identity",
        PROVIDER,
        '                       providers=[provider] if provider in undx_router.PROVIDERS else None)',
        '                       providers=None)',
        "test_a_broken_identity_is_regenerated_on_the_provider_that_broke_it",
    ),
    (
        "put the correction above the identity block instead of after it",
        PROVIDER,
        '        retry = _route(f"{system_prompt}\\n\\n{correction}", history, user_content,',
        '        retry = _route(f"{correction}\\n\\n{system_prompt}", history, user_content,',
        "test_a_broken_identity_is_regenerated_on_the_provider_that_broke_it",
    ),
    (
        "return the violating reply when the regeneration also fails",
        PROVIDER,
        '        reply = UNDX_IDENTITY_SAFE_REPLY\n',
        '        pass\n',
        "test_a_second_violation_returns_the_safe_reply_and_never_the_violation",
    ),
    (
        "drop a family from the identity validator",
        PROVIDER,
        '        (r"\\b(i am|i\'m) (a )?(human|conscious|sentient)\\b", "human_or_conscious_claim"),\n',
        '',
        "test_the_validator_still_catches_every_family_it_used_to",
    ),

    # ---- a refusal is not an outage ------------------------------------------------
    (
        # A privacy ceiling that excluded every provider is the system working. Reporting
        # it as `all_providers_failed` pages out a classification decision as an incident
        # and buries the only fact that would explain it.
        "collapse a privacy refusal into an outage",
        PROVIDER,
        '    if statuses <= {"privacy_refused"}:\n'
        '        return "privacy_ceiling_refused"\n',
        '',
        "test_a_privacy_refusal_is_the_system_working",
    ),
    (
        "collapse a budget stop into an outage",
        PROVIDER,
        '    if statuses <= {"budget_exceeded"}:\n'
        '        return "budget_exhausted"\n',
        '',
        "test_a_budget_stop_is_a_spend_decision",
    ),
    (
        "call a real outage an operator configuration problem",
        PROVIDER,
        '    return "all_providers_failed"',
        '    return "provider_config_missing"',
        "test_a_mixed_or_failing_chain_is_an_outage",
    ),
    (
        "show the raw provider error to the user",
        PROVIDER,
        '            "error": "ai_unavailable",\n'
        '            "reason": reason,\n'
        '            "message": UNAVAILABLE_MESSAGE,',
        '            "error": "ai_unavailable",\n'
        '            "reason": reason,\n'
        '            "message": _safe_text(envelope.get("error"), 200),',
        "test_no_failure_path_returns_a_provider_error_to_the_user",
    ),

    # ---- the events ledger gets the truth ------------------------------------------
    (
        # The bug this translation layer was written to prevent, caught in review before
        # it shipped. `_record_provider_events` reads `ok`/`reason`/`latency_ms`; the
        # router emits `provider`/`status`. A pass-through writes every attempt as
        # `failed` with a blank reason, including the one that answered.
        "pass the router's attempt chain through untranslated",
        PROVIDER,
        '    attempts = _translate_attempts(envelope, latency_ms)\n'
        '\n'
        '    if not envelope.get("ok"):',
        '    attempts = list(envelope.get("attempts") or [])\n'
        '\n'
        '    if not envelope.get("ok"):',
        "test_the_successful_attempt_is_recorded_as_successful",
    ),
    (
        # "Meta Muse".lower() is "meta muse", and the ledger has always stored "meta".
        # The admin dashboard does `GROUP BY provider`, so one provider becomes two rows
        # and neither total is right.
        "lowercase the display label instead of translating it to the ledger key",
        PROVIDER,
        'def _provider_key(label: str) -> str:\n'
        '    for name, config in undx_router.PROVIDERS.items():\n'
        '        if config.label == label or name == label:\n'
        '            return name\n'
        '    return str(label or "").strip().lower()',
        'def _provider_key(label: str) -> str:\n'
        '    return str(label or "").strip().lower()',
        "test_the_ledger_stores_one_spelling_per_provider",
    ),
    (
        # The router times the chain, not each hop. Attributing the total to every
        # attempt is a plausible-looking lie in a column somebody will average.
        "attribute the whole chain's latency to every attempt in it",
        PROVIDER,
        '            "latency_ms": total_latency_ms if succeeded else 0,',
        '            "latency_ms": total_latency_ms,',
        "test_latency_is_attributed_to_the_attempt_that_finished_the_chain",
    ),
    (
        # §41: even when failover succeeds, the original failure has to be recorded, or
        # provider health never learns which provider is sick.
        "record every attempt as a success",
        PROVIDER,
        '            "ok": succeeded,\n'
        '            "reason": "" if succeeded else (status or "unknown"),',
        '            "ok": True,\n'
        '            "reason": "",',
        "test_a_provider_that_was_tried_and_failed_still_appears",
    ),
    (
        # `privacy_refused`, `budget_exceeded` and `circuit_open` describe providers
        # deliberately not consulted. Flattening them to "failed" is what makes a refusal
        # indistinguishable from an outage in the one table that could tell them apart.
        "flatten every non-success status into 'failed'",
        PROVIDER,
        '            "reason": "" if succeeded else (status or "unknown"),',
        '            "reason": "" if succeeded else "failed",',
        "test_the_three_statuses_this_ledger_could_not_previously_express",
    ),
    (
        "stop recording the regeneration's attempts",
        PROVIDER,
        '        attempts += _translate_attempts(retry, int(retry.get("latency_ms") or 0))',
        '        pass',
        "test_the_regeneration_attempts_are_recorded_too",
    ),

    # ---- attribution is execution, not configuration -------------------------------
    (
        # The mistake `route_undx_request`'s own failure envelope still makes one module
        # over: it hardcodes `provider: "openai"` regardless of who was tried.
        "report a configured provider rather than the one that answered",
        PROVIDER,
        '    provider = _provider_key(str(envelope.get("provider") or ""))',
        '    provider = "openai"',
        "test_the_answering_provider_and_model_are_reported_faithfully",
    ),
    (
        # Looks harmless and is not: the router may have answered on a model the local
        # lookup does not know about, and the local answer is the one that gets stored.
        "look the model up locally instead of reading what answered",
        PROVIDER,
        '    model = _safe_text(envelope.get("model"), 120)',
        '    model = undx_router._model(provider)',
        "test_the_model_is_not_looked_up_locally",
    ),

    # ---- one authority for configuration -------------------------------------------
    (
        # Somebody who set `UNDX_CANDIDATE_ENABLED=true` made a deliberate choice.
        # Discovering it stopped working from a silence is worse than from a status field.
        "stop reading the retired candidate's flag and just report it off",
        PROVIDER,
        '            "enabled": candidate_enabled(),',
        '            "enabled": False,',
        "test_the_retired_candidate_is_reported_retired_rather_than_vanishing",
    ),
    (
        "let the candidate vanish from the status payload without saying so",
        PROVIDER,
        '            "retired": True,\n',
        '',
        "test_the_retired_candidate_is_reported_retired_rather_than_vanishing",
    ),
    (
        # `UNDX_CLAUDE_ENABLED=false` is how an operator takes a provider out of rotation.
        # Reporting it configured anyway means the status page disagrees with the router
        # about which providers are in play, which is the §13 failure in miniature.
        "call a provider configured on key presence alone, ignoring its switch",
        PROVIDER,
        '            "configured": bool(undx_router._api_key(name)) and undx_router.provider_enabled(name),',
        '            "configured": bool(undx_router._api_key(name)),',
        "test_a_provider_killed_by_its_switch_is_not_reported_configured",
    ),

    # ---- the task path was migrated too --------------------------------------------
    (
        # Translation output is not an assistant conversation. Injecting UNDX's identity
        # into it leaks "I'm UNDX, PulseSOC's intelligence companion" into translated
        # product copy.
        "inject the assistant identity into the infrastructure task path",
        PROVIDER,
        '    try:\n'
        '        system_prompt, history, user_content = _split_for_router(bounded_messages)',
        '    try:\n'
        '        bounded_messages = prepare_undx_model_request(bounded_messages, correlation_id)\n'
        '        system_prompt, history, user_content = _split_for_router(bounded_messages)',
        "test_it_still_does_not_inject_the_assistant_identity",
    ),
    (
        "stop requiring a system instruction on a bounded task",
        PROVIDER,
        '    if not bounded_messages or not any(item["role"] == "system" for item in bounded_messages):',
        '    if not bounded_messages:',
        "test_a_request_without_a_system_instruction_is_still_refused",
    ),
    (
        # The caller's message exists because the caller knows what it is doing. A
        # translation failure telling the user "UNDX is temporarily unavailable" names a
        # product they were not using.
        "show the assistant's unavailable message on a task failure",
        PROVIDER,
        '            "error": "ai_unavailable",\n'
        '            "reason": reason,\n'
        '            "message": unavailable_message,',
        '            "error": "ai_unavailable",\n'
        '            "reason": reason,\n'
        '            "message": UNAVAILABLE_MESSAGE,',
        "test_its_failure_message_is_the_callers_and_not_the_assistants",
    ),

    # ---- past the seam: the router capability the whole phase depended on -----------
    (
        # The regression this phase exists to have fixed. Every behavioural test in the
        # suite mocks `route_structured_request`, so all of them pass against a router
        # that accepts `history` and throws it away — which is what it did.
        "restore the hardcoded empty history in the router",
        ROUTER,
        '    ordered = [p for p in (providers or []) if p in PROVIDERS and provider_enabled(p)]',
        '    history = []\n'
        '    ordered = [p for p in (providers or []) if p in PROVIDERS and provider_enabled(p)]',
        "test_the_turns_reach_the_provider",
    ),
    (
        # One adapter of seven. Only OpenAI is exercised behaviourally, so this is
        # invisible until a failover routes a conversation to Perplexity and it arrives
        # with no memory of itself — the least observable moment available.
        "let one adapter accept history and drop it",
        ROUTER,
        '        "messages": _messages(system_prompt, message, history, user_content=kwargs.get("user_content")),',
        '        "messages": _messages(system_prompt, message, [], user_content=kwargs.get("user_content")),',
        "test_every_adapter_forwards_history_rather_than_only_the_one_tested_above",
    ),

    # ---- the production caller ------------------------------------------------------
    (
        "stop attributing the messenger's spend to the person who spent it",
        SERVICE,
        'prompt_messages, correlation_id=correlation_id, task=task, user_id=int(user_id))',
        'prompt_messages, correlation_id=correlation_id, task=task)',
        "test_the_messenger_turn_attributes_its_spend_to_the_person",
    ),

    # ---- prose: must stay GREEN ------------------------------------------------------
    (
        # The module docstring already names both stale models on purpose, because naming
        # them is how a reader understands what two routers cost. A substring check would
        # make deleting that the cheapest way to green.
        "spell every removed mechanism out in the module docstring (must stay GREEN)",
        PROVIDER,
        'Provider secrets and raw provider errors still never reach a user.',
        'Removed here: the five-entry provider table, its PULSE_AI_*_MODEL defaults\n'
        '(claude-3-5-haiku-latest, gemini-1.5-flash, gpt-4o-mini), the OPENAI_API_KEY /\n'
        'CLAUDE_AI_API / Gemini_AI_API credential reads, the hand-rolled posts to\n'
        'https://api.openai.com/v1/chat/completions, https://api.anthropic.com/v1/messages\n'
        'and generativelanguage.googleapis.com/v1beta/models/{model}:generateContent, and\n'
        'UNDX_CANDIDATE_BASE_URL + UNDX_CANDIDATE_API_KEY. None of them survive.\n'
        '\n'
        'Provider secrets and raw provider errors still never reach a user.',
        None,
    ),
    (
        "explain the same removals in a function docstring (must stay GREEN)",
        PROVIDER,
        '    """The single seam through which this module reaches a model.\n',
        '    """The single seam through which this module reaches a model.\n'
        '\n'
        '    Replaces _post_openai_compatible (api.openai.com/v1/chat/completions),\n'
        '    _post_anthropic (api.anthropic.com/v1/messages) and _post_gemini\n'
        '    (:generateContent), plus the claude-3-5-haiku-latest and gemini-1.5-flash\n'
        '    model defaults and the OPENAI_API_KEY lookups all three shared.\n',
        None,
    ),
    (
        # `ast.parse` drops comments entirely, so a comment passes every AST check for
        # free and fails a substring check exactly as hard as a docstring does. Worth its
        # own mutation so a substring check added later in good faith is caught here
        # rather than by someone deleting the comment.
        "explain them in a comment at the seam instead (must stay GREEN)",
        PROVIDER,
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        '# No OPENAI_API_KEY, no PULSE_AI_CLAUDE_MODEL, no api.openai.com, no\n'
        '# api.anthropic.com/v1/messages, no :generateContent, no UNDX_CANDIDATE_BASE_URL,\n'
        '# no claude-3-5-haiku-latest, no gemini-1.5-flash, no requests.post.\n'
        'def _safe_text(value: Any, limit: int = 6000) -> str:',
        None,
    ),
    (
        # The test file explains the live bug by naming the two stale models. A check that
        # read its own source would be the silliest possible version of this failure, and
        # it is not a hypothetical shape — `pulsesoc_content_translation_audit.py` pinned
        # a literal and fired on the docstring that explained the literal.
        "name the stale models in the test file's own prose (must stay GREEN)",
        TESTS,
        '"""There is one router. The second one grounds and verifies, and nothing else.',
        '"""There is one router. The second one grounds and verifies, and nothing else.\n'
        '\n'
        'The deleted table held claude-3-5-haiku-latest and gemini-1.5-flash, posted to\n'
        'api.openai.com/v1/chat/completions and api.anthropic.com/v1/messages, and read\n'
        'OPENAI_API_KEY and UNDX_CANDIDATE_BASE_URL directly.',
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
