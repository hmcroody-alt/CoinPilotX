"""Grounding and identity verification for the UNDX messenger. Not a router.

It used to be one, and the name is kept because five audit scripts, a test suite
and the architecture catalogue all reach for it — but the thing it does now is
narrower and the thing it stopped doing matters.

**What it stopped doing.** This module held a second five-provider table, with its
own ``PULSE_AI_*_MODEL`` defaults, its own key lookup, its own timeout, and three
hand-rolled HTTP transports (OpenAI-compatible, Anthropic, Gemini). That is the
same job ``undx_router`` does, done again, differently. Two routers is not
redundancy — it is two answers to "which model is PulseSoc using", and they
disagreed: this table defaulted Claude to ``claude-3-5-haiku-latest`` and Gemini to
``gemini-1.5-flash``, both of which were retired upstream and returned 404 to every
request. ``undx_router`` had already been corrected. A call arriving here got the
dead models, and the only symptom was that UNDX "felt slower", because the chain
burned two providers before reaching one that answered.

It also meant this path had no privacy ceiling, no spend budget, no circuit
breaker and no provider health recording, because all four live in ``undx_router``.
The busiest chat surface in the product was the one with none of the controls.

So execution now delegates: :func:`generate_response` calls
``undx_router.route_structured_request``, which owns provider selection, key
handling, failover, the privacy ceiling, the budget, the breaker and the usage
ledger. The transports, the provider table and the duplicate model defaults are
gone from this file.

**What it keeps, and why it is not merely a shim.** Two things here are not
transport and have no home in ``undx_router``:

* :func:`prepare_undx_model_request` — four canonical system blocks prepended and
  then *verified present*, failing closed if any is missing. Identity, company
  grounding, capability lifecycle, fact policy. This is the reason UNDX answers
  "who are you" consistently across providers, retries and fallbacks.
* :func:`undx_identity_violation` — the output-side check, with one regeneration
  attempt and a safe canonical reply if the second answer still breaks identity.

Grounding a request and verifying its answer are the caller's job, not the
transport's. ``undx_router`` deliberately does not know what a UNDX messenger turn
is supposed to sound like.

**Retired capability, recorded rather than dropped.** ``UNDX_CANDIDATE`` was a
self-hosted OpenAI-compatible endpoint that joined the pool when
``UNDX_CANDIDATE_ENABLED`` was true and ``UNDX_CANDIDATE_BASE_URL`` was set. It was
off by default and had no configured deployment. It was also the one chat endpoint
in this repo an operator could point anywhere with an environment variable, and the
one a URL-literal scanner could never see — so it is exactly what §11/§12 prohibit.
Its transport is gone. :func:`provider_status` still reports the candidate block so
the status endpoint's shape is unchanged, now with ``retired`` set, and
:func:`candidate_enabled` still reads the flag so an operator who had it on is told
so instead of silently losing it.

Provider secrets and raw provider errors still never reach a user.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import undx_router

from services import undx_call_domain
from services import undx_capability_lifecycle
from services import undx_company_identity
from services import undx_fact_policy
from services import undx_privacy


LOGGER = logging.getLogger(__name__)
TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_TIMEOUT_SECONDS = 18
UNDX_IDENTITY_REQUIRED_PHRASE = "canonical name is UNDX"
UNDX_IDENTITY_BLOCK = """You are UNDX, PulseSOC’s intelligence companion.

Your canonical name is UNDX.
When asked your name, identity, or role, answer that you are UNDX.
Never identify yourself as Pulse AI, ChatGPT, a generic assistant, or an unknown bot.
Do not claim to be human, conscious, sentient, or omniscient.
Your identity must remain consistent across native, WebView, streaming, retries,
fallback models, tool calls, summaries, and resumed conversations."""
UNDX_IDENTITY_SAFE_REPLY = "I’m UNDX, PulseSOC’s intelligence companion."


#: What the messenger sends, in ``undx_privacy`` terms. An in-app UNDX conversation
#: carries whatever the person typed plus their retrieved memory and knowledge items,
#: so CONFIDENTIAL is the floor, not a cautious guess. §4 requires the declaration and
#: forbids lowering it to make routing easier: if a ceiling excludes every provider,
#: the correct outcome is the refusal envelope below, not PUBLIC.
MESSENGER_PRIVACY_CLASS = undx_privacy.SENSITIVITY_CONFIDENTIAL

#: The UNDX in-app messenger. Declared as a constant rather than accepted as a
#: parameter because — unlike ``services.intelligence.assistant_response``, which five
#: different surfaces reach — :func:`generate_response` has exactly one production
#: caller (``services.pulse_ai_service``, the messenger turn handler). One caller of
#: one kind can state its provenance as a fact. A ``call_domain`` argument still
#: exists for the same reason the privacy class does not: a future second surface must
#: be able to say so, and §5 guarantees a domain can only reorder providers, never
#: widen what they may receive.
MESSENGER_CALL_DOMAIN = undx_call_domain.CALL_DOMAIN_MESSAGING


class PulseAIProviderError(RuntimeError):
    def __init__(self, provider: str, reason: str, status_code: int = 0):
        super().__init__(reason)
        self.provider = provider
        self.reason = reason
        self.status_code = status_code


def prepare_undx_model_request(messages: list[dict[str, str]], correlation_id: str = "") -> list[dict[str, str]]:
    """Build the final provider request and fail closed if identity is absent.

    Two canonical system blocks are prepended and verified here so every provider,
    fallback, retry, and stream is grounded identically without trusting the client,
    retrieval, memory, or history: UNDX's own identity, and the authoritative
    company/founder/product grounding (who builds PulseSoc, the product definition,
    and the fact/capability honesty + injection-resistance rules).
    """
    try:
        capability_block = undx_capability_lifecycle.capability_lifecycle_block()
    except Exception:
        # Fail closed: a broken lifecycle projection must not let UNDX answer
        # capability questions ungrounded (it would fabricate availability).
        LOGGER.exception("capability_grounding_error correlation_id=%s", correlation_id)
        raise PulseAIProviderError("undx_identity", "capability_grounding_error")
    final_messages = [
        {"role": "system", "content": UNDX_IDENTITY_BLOCK},
        {"role": "system", "content": undx_company_identity.company_identity_block()},
        {"role": "system", "content": capability_block},
        {"role": "system", "content": undx_fact_policy.fact_policy_block()},
    ]
    final_messages.extend(dict(item) for item in messages if isinstance(item, dict))
    final_system_context = "\n\n".join(
        str(item.get("content") or "") for item in final_messages if item.get("role") == "system"
    )
    identity_present = UNDX_IDENTITY_REQUIRED_PHRASE in final_system_context
    company_present = undx_company_identity.COMPANY_IDENTITY_REQUIRED_PHRASE in final_system_context
    capability_present = "UNDX capability state" in final_system_context
    fact_policy_present = undx_fact_policy.FACT_POLICY_REQUIRED_PHRASE in final_system_context
    if not identity_present or not company_present or not capability_present or not fact_policy_present:
        LOGGER.error(
            "identity_configuration_error correlation_id=%s identity_present=%s company_present=%s "
            "capability_present=%s fact_policy_present=%s",
            correlation_id, identity_present, company_present, capability_present, fact_policy_present,
        )
        raise PulseAIProviderError("undx_identity", "identity_configuration_error")
    assert UNDX_IDENTITY_REQUIRED_PHRASE in final_system_context
    assert undx_company_identity.COMPANY_IDENTITY_REQUIRED_PHRASE in final_system_context
    assert undx_fact_policy.FACT_POLICY_REQUIRED_PHRASE in final_system_context
    LOGGER.info(
        "UNDX_FINAL_MODEL_REQUEST correlation_id=%s identity_present=true system_context=%r roles=%s",
        correlation_id,
        UNDX_IDENTITY_BLOCK,
        [str(item.get("role") or "") for item in final_messages],
    )
    return final_messages


def undx_identity_violation(reply: str) -> str:
    text = " ".join(str(reply or "").lower().replace("’", "'").split())
    rules = (
        (r"\bpulse\s*ai\b", "pulse_ai_identity"),
        (r"\b(chatgpt|unknown bot|generic assistant)\b", "alternate_identity"),
        (r"\b(i am not|i'm not) undx\b", "undx_denial"),
        (r"\b(i (do not|don't) know|never heard of) undx\b", "undx_unknown"),
        (r"\bmy name is (?!undx\b)[a-z0-9_-]+", "alternate_name"),
        (r"\b(i am|i'm) (a )?(human|conscious|sentient)\b", "human_or_conscious_claim"),
    )
    for pattern, reason in rules:
        if re.search(pattern, text):
            return reason
    return ""


def _env_text(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else default


def _timeout() -> float:
    try:
        return max(2.0, min(float(_env_text("PULSE_AI_PROVIDER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))), 45.0))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def candidate_enabled() -> bool:
    """Whether an operator still has the retired self-hosted candidate switched on.

    Kept after the candidate's transport was deleted, precisely so the answer can be
    reported rather than assumed. Somebody who set ``UNDX_CANDIDATE_ENABLED=true``
    made a deliberate choice; discovering it stopped working from a silence is worse
    than discovering it from a status field that says ``retired``.
    """
    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES


def provider_status() -> dict[str, Any]:
    """Configuration, as ``undx_router`` sees it, in the shape the status API expects.

    Same four keys the ``/api/pulse-ai/status`` payload has always carried. The
    difference is that ``providers`` and their models are now read out of
    ``undx_router.PROVIDERS`` instead of a second table that disagreed with it — which
    is the whole §13 point: a status endpoint reporting models from a table the request
    path no longer uses is worse than no status endpoint, because it looks authoritative.

    ``configured`` means what it has always meant here — a usable credential is present
    — deliberately not "and the provider works". ``undx_router.provider_configuration``
    was renamed from ``provider_health`` after both Claude and Gemini reported "Online"
    through this field for the entire period they returned 404 to every request.
    """
    providers = [
        {
            "provider": name,
            "configured": bool(undx_router._api_key(name)) and undx_router.provider_enabled(name),
            "model": undx_router._model(name),
        }
        for name in undx_router.PROVIDERS
    ]
    return {
        "ok": True,
        "providers": providers,
        "candidate": {
            "provider": "undx_candidate",
            "enabled": candidate_enabled(),
            "configured": False,
            "retired": True,
            "model": "",
        },
        "configured_count": sum(1 for item in providers if item["configured"]),
        "fallback_order": [item["provider"] for item in providers if item["configured"]],
    }


def configured_providers() -> list[str]:
    return configured_providers_for_task("general")


def _task_preference(task: str = "general") -> list[str]:
    """Which providers suit this kind of work, in order. A hint, never a permission.

    ``task`` is a *capability* statement — "this turn is about security", "this turn
    needs code" — and it is inferred upstream from the safety classifier and the
    router's own topic guess. It is emphatically not provenance: it says nothing about
    which surface the request came from or what the content is worth protecting at.

    Phase 4 established the distinction and it decides where this value goes. A
    capability hint maps to ``providers=`` on the routed request, where it reorders a
    chain. It must not map to ``call_domain``, which is a fact the *caller* holds, and
    it must never touch ``privacy_class``, which is a claim about the content.
    Collapsing the three is how "this question is about security" quietly becomes
    "therefore it may go anywhere a security question may go".

    An empty list means no preference, which is the honest answer for a general turn:
    ``undx_router`` then classifies the text and applies its own lane priorities,
    which are maintained against live provider behaviour.
    """
    task = (task or "general").lower()
    if "cyber" in task or "security" in task or "safety" in task:
        return ["claude", "openai", "gemini", "deepseek", "groq"]
    if "technical" in task or "code" in task or "developer" in task:
        return ["deepseek", "openai", "claude", "gemini", "groq"]
    if "web" in task or "current" in task or "search" in task:
        return ["openai", "gemini", "claude", "groq", "deepseek"]
    if "fast" in task:
        return ["groq", "openai", "gemini", "claude", "deepseek"]
    return []


def configured_providers_for_task(task: str = "general") -> list[str]:
    """Provider names to prefer for this task, filtered to the ones actually usable.

    Returns names now, not ``ProviderConfig`` objects, because the config objects were
    part of the duplicate table and ``undx_router.route_structured_request`` takes names
    in its ``providers=`` argument. The ``PULSE_AI_PROVIDER_ORDER`` override still works
    and still wins over the task preference — that is an operator's explicit
    instruction, and the point of keeping it is that an incident response ("stop using
    Groq") should not require a deploy.

    Names not in ``undx_router.PROVIDERS`` are dropped rather than passed through. A
    typo in ``PULSE_AI_PROVIDER_ORDER`` should cost the ordering hint it names and
    nothing else; forwarding it would have the router silently ignore the whole list.

    The ``name in known`` filter is load-bearing beyond that, which is easy to miss
    because it reads like tidiness: ``undx_router._api_key`` raises ``KeyError`` for a
    name ``PROVIDERS`` does not have, and the comprehension below calls it on everything
    that survives. So an unfiltered typo does not merely degrade the ordering — it throws
    out of the messenger turn. One transposed character in a variable an operator edits
    during an incident, and UNDX stops answering at all.
    """
    known = list(undx_router.PROVIDERS)
    preferred = [item.strip().lower() for item in _env_text("PULSE_AI_PROVIDER_ORDER").split(",") if item.strip()]
    if not preferred:
        preferred = _task_preference(task)
    ordered = [name for name in preferred if name in known]
    ordered += [name for name in known if name not in ordered]
    return [name for name in ordered
            if undx_router._api_key(name) and undx_router.provider_enabled(name)]


def _safe_text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


def _split_for_router(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]], str]:
    """Split a chat message list into the three things a routed request takes.

    ``undx_router`` is shaped around one system prompt, a history, and a final user
    turn, which is exactly what every provider wants and what each adapter already
    knows how to express in its own dialect (Claude's top-level ``system`` field,
    Gemini's ``systemInstruction``, everyone else's leading system message).

    System messages are joined in order, not just the first one. That matters here:
    :func:`prepare_undx_model_request` contributes four, and ``pulse_ai_service``
    inserts a fifth at index 1 for a cyber-safety addendum or a "live search was
    unavailable" notice. Taking only the first would drop the identity grounding's
    companions, or the safety addendum, depending on which end you picked.

    A request whose last turn is not the user's is rejected rather than repaired.
    Reordering it to make it routable would silently answer a different conversation,
    and inventing an empty user turn would ask a model to speak unprompted.
    """
    system_parts: list[str] = []
    turns: list[dict[str, str]] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "")
        if role == "system":
            system_parts.append(content)
        elif role in {"user", "assistant"}:
            turns.append({"role": role, "content": content})
    if not turns or turns[-1]["role"] != "user":
        raise PulseAIProviderError("undx_router", "final_user_turn_required")
    return "\n\n".join(part for part in system_parts if part), turns[:-1], turns[-1]["content"]


#: undx_router reports attempts by display label ("OpenAI", "Meta Muse"); the
#: ``pulse_ai_provider_events`` ledger has always stored the lowercase key. Translating
#: rather than lowercasing, because "Meta Muse" does not lowercase into "meta" and a
#: ledger with two spellings for one provider cannot be grouped by provider — which is
#: exactly what the admin dashboard's `GROUP BY provider` does.
def _provider_key(label: str) -> str:
    for name, config in undx_router.PROVIDERS.items():
        if config.label == label or name == label:
            return name
    return str(label or "").strip().lower()


def _translate_attempts(envelope: dict[str, Any], total_latency_ms: int) -> list[dict[str, Any]]:
    """Re-express the router's attempt chain in the shape the events ledger stores.

    ``_record_provider_events`` reads ``ok``, ``reason``, ``status_code`` and
    ``latency_ms`` per attempt. The router reports ``provider`` and ``status``, so a
    straight pass-through would write every attempt as ``status='failed'`` with a blank
    reason — including the successful one, because ``ok`` would be absent.

    Three of the statuses are new to this ledger and are the reason the translation is
    worth having rather than skipping: ``privacy_refused``, ``budget_exceeded`` and
    ``circuit_open`` describe providers deliberately not consulted. They were
    unrepresentable before, because this module had no privacy ceiling, no budget and
    no breaker. A refusal now lands in the ledger as a refusal instead of as nothing.

    Per-attempt latency is not available — the router times the chain, not each hop —
    so the total is attributed to the attempt that finished it and the rest carry 0.
    Attributing the total to every attempt would be a plausible-looking lie in a table
    someone will eventually average.
    """
    attempts: list[dict[str, Any]] = []
    for attempt in envelope.get("attempts") or []:
        status = str(attempt.get("status") or "")
        succeeded = status == "success"
        attempts.append({
            "provider": _provider_key(str(attempt.get("provider") or "")),
            "ok": succeeded,
            "reason": "" if succeeded else (status or "unknown"),
            "detail": _safe_text(attempt.get("detail"), 200),
            "status_code": 0,
            "latency_ms": total_latency_ms if succeeded else 0,
        })
    return attempts


def _failure_reason(envelope: dict[str, Any]) -> str:
    """``provider_config_missing`` vs ``all_providers_failed``, preserved from before.

    The old code answered the first before trying anything and the second after trying
    everything, and both strings are already in the events ledger and in dashboards.
    The distinction is still real and still worth keeping: nothing configured is an
    operator's problem, everything failing is an outage.

    A chain where every entry is ``not_configured`` is the first case. A chain that is
    entirely ``privacy_refused`` or ``budget_exceeded`` is neither — those are the
    system working as designed, and collapsing them into "all providers failed" is how
    a classification decision or a spend decision gets paged out as an outage. The
    router already distinguishes them in ``error``; this keeps its word for them.
    """
    statuses = {str(item.get("status") or "") for item in envelope.get("attempts") or []}
    if not statuses or statuses <= {"not_configured"}:
        return "provider_config_missing"
    if statuses <= {"privacy_refused"}:
        return "privacy_ceiling_refused"
    if statuses <= {"budget_exceeded"}:
        return "budget_exhausted"
    return "all_providers_failed"


def _route(
    system_prompt: str,
    history: list[dict[str, str]],
    user_content: str,
    *,
    task: str,
    user_id: Any,
    call_domain: str | None,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    """The single seam through which this module reaches a model.

    One function, so that there is exactly one place to read to know what this file
    sends and one place for a test to intercept. It is deliberately thin: every
    decision it does not make — which provider, which model, whether the privacy
    ceiling allows it, whether the budget allows it, whether the breaker is open, what
    the usage cost was — belongs to ``undx_router`` and is made there for every caller
    in the product rather than again here for this one.

    ``temperature=0.35`` and ``max_tokens=850`` are carried over exactly from the
    transports this replaced. They are the messenger's voice, and §3 requires the
    migration preserve it rather than inherit the router's structured-output defaults
    of 0.0 and 320 — which would turn a conversation into a terse machine answer.
    """
    return undx_router.route_structured_request(
        user_id,
        system_prompt,
        user_content,
        history=history,
        timeout=int(_timeout()),
        temperature=0.35,
        max_tokens=850,
        providers=providers if providers is not None else (configured_providers_for_task(task) or None),
        privacy_class=MESSENGER_PRIVACY_CLASS,
        call_domain=call_domain or MESSENGER_CALL_DOMAIN,
    )


UNAVAILABLE_MESSAGE = "UNDX is temporarily unavailable. Please try again soon."


def generate_response(
    messages: list[dict[str, str]],
    correlation_id: str = "",
    task: str = "general",
    user_id: Any = None,
    call_domain: str | None = None,
) -> dict[str, Any]:
    """One grounded, identity-verified UNDX messenger turn.

    The envelope shape is unchanged — ``ok``, ``reply``, ``provider``, ``model``,
    ``latency_ms``, ``identity_regenerated``, ``identity_validated``, ``attempts`` on
    success; ``ok``, ``error``, ``reason``, ``message``, ``correlation_id``,
    ``attempts`` on failure — because ``pulse_ai_service`` reads all of it and writes
    most of it into ``pulse_ai_messages`` and ``pulse_ai_provider_events``.

    What changed is underneath: provider selection, failover, the privacy ceiling, the
    spend budget, the circuit breaker and the usage ledger are ``undx_router``'s, not
    this module's. What stayed is the pair of things that are actually about UNDX
    rather than about transport — grounding the request and verifying the answer.

    ``user_id`` is threaded through because the router attributes spend and logs with
    it. Defaulting to ``None`` keeps the old two- and three-argument calls working, but
    a ``None`` here means the turn's cost is recorded against nobody, so the production
    caller passes it.
    """
    attempts: list[dict[str, Any]] = []
    try:
        final_messages = prepare_undx_model_request(messages, correlation_id)
        system_prompt, history, user_content = _split_for_router(final_messages)
    except PulseAIProviderError as exc:
        # Both failures here are the request's own fault and neither is worth a provider
        # call. Reported with the router's reason rather than a generic one so that a
        # missing identity block and a malformed turn order are distinguishable in the
        # log — the first is a deploy problem, the second is a caller bug.
        reason = getattr(exc, "reason", "identity_configuration_error")
        return {
            "ok": False,
            "error": "identity_configuration_error" if reason == "identity_configuration_error" else "invalid_request",
            "reason": reason,
            "message": UNAVAILABLE_MESSAGE,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }

    started = time.perf_counter()
    envelope = _route(system_prompt, history, user_content,
                      task=task, user_id=user_id, call_domain=call_domain)
    latency_ms = int(envelope.get("latency_ms") or (time.perf_counter() - started) * 1000)
    attempts = _translate_attempts(envelope, latency_ms)

    if not envelope.get("ok"):
        reason = _failure_reason(envelope)
        LOGGER.warning(
            "PULSE_AI_PROVIDER_FAILED reason=%s detail=%s correlation_id=%s task=%s attempts=%s",
            reason, _safe_text(envelope.get("error"), 200), correlation_id, task, len(attempts),
        )
        return {
            "ok": False,
            "error": "ai_unavailable",
            "reason": reason,
            "message": UNAVAILABLE_MESSAGE,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }

    reply = _safe_text(envelope.get("response"))
    provider = _provider_key(str(envelope.get("provider") or ""))
    model = _safe_text(envelope.get("model"), 120)
    violation = undx_identity_violation(reply)
    regenerated = False
    if violation:
        LOGGER.warning(
            "UNDX_IDENTITY_RESPONSE_REJECTED provider=%s reason=%s correlation_id=%s",
            provider, violation, correlation_id,
        )
        # Regenerated on the provider that broke identity, not on a fresh chain. The
        # question being asked is "can this model hold the line when told directly",
        # and handing the retry to a different provider would answer a different one —
        # then report the second provider's success as the first one's correction.
        correction = (
            "Identity verification failed. Regenerate the answer while preserving the "
            "canonical UNDX identity above."
        )
        retry = _route(f"{system_prompt}\n\n{correction}", history, user_content,
                       task=task, user_id=user_id, call_domain=call_domain,
                       providers=[provider] if provider in undx_router.PROVIDERS else None)
        regenerated = True
        attempts += _translate_attempts(retry, int(retry.get("latency_ms") or 0))
        if retry.get("ok"):
            reply = _safe_text(retry.get("response")) or reply
            model = _safe_text(retry.get("model"), 120) or model
        violation = undx_identity_violation(reply)
    if violation:
        LOGGER.error(
            "UNDX_IDENTITY_RESPONSE_BLOCKED provider=%s reason=%s correlation_id=%s",
            provider, violation, correlation_id,
        )
        reply = UNDX_IDENTITY_SAFE_REPLY
    return {
        "ok": True,
        "reply": reply,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "identity_regenerated": regenerated,
        "identity_validated": True,
        "attempts": attempts,
    }


def generate_task_response(
    messages: list[dict[str, str]],
    correlation_id: str = "",
    task: str = "general",
    unavailable_message: str = "This service is temporarily unavailable. Please try again soon.",
    user_id: Any = None,
    call_domain: str | None = None,
) -> dict[str, Any]:
    """Run a bounded non-assistant task through the router, without UNDX's identity.

    UNDX chat calls must continue through :func:`generate_response`, which injects
    and validates the canonical identity. Infrastructure tasks such as content
    translation are not assistant conversations and must not leak that identity
    into their output. They still reuse the same provider ordering, timeouts,
    secret handling, fallback behavior, and curated errors — which since the §13
    reconciliation means ``undx_router``'s, along with its privacy ceiling, budget and
    breaker, none of which this path had before.

    It has no production callers today and is reached only by
    ``scripts/pulsesoc_content_translation_audit.py``. Migrated anyway rather than
    deleted, because an unrouted call site nobody exercises is exactly the one that
    survives a migration and becomes call site #8 the next time somebody needs a
    non-assistant model call and finds a helper that looks ready.
    """
    attempts: list[dict[str, Any]] = []
    bounded_messages: list[dict[str, str]] = []
    for item in messages[:8]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"system", "user", "assistant"}:
            continue
        bounded_messages.append({"role": role, "content": _safe_text(item.get("content"), 6000)})
    if not bounded_messages or not any(item["role"] == "system" for item in bounded_messages):
        return {
            "ok": False,
            "error": "invalid_task_request",
            "reason": "system_instruction_required",
            "message": unavailable_message,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    try:
        system_prompt, history, user_content = _split_for_router(bounded_messages)
    except PulseAIProviderError as exc:
        return {
            "ok": False,
            "error": "invalid_task_request",
            "reason": getattr(exc, "reason", "final_user_turn_required"),
            "message": unavailable_message,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    envelope = _route(system_prompt, history, user_content,
                      task=task, user_id=user_id, call_domain=call_domain)
    latency_ms = int(envelope.get("latency_ms") or 0)
    attempts = _translate_attempts(envelope, latency_ms)
    if not envelope.get("ok"):
        reason = _failure_reason(envelope)
        LOGGER.warning(
            "PULSE_AI_TASK_PROVIDER_FAILED reason=%s detail=%s correlation_id=%s task=%s attempts=%s",
            reason, _safe_text(envelope.get("error"), 200), correlation_id, task, len(attempts),
        )
        return {
            "ok": False,
            "error": "ai_unavailable",
            "reason": reason,
            "message": unavailable_message,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    return {
        "ok": True,
        "reply": _safe_text(envelope.get("response")),
        "provider": _provider_key(str(envelope.get("provider") or "")),
        "model": _safe_text(envelope.get("model"), 120),
        "latency_ms": latency_ms,
        "attempts": attempts,
    }
