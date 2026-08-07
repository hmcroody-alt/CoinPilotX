"""Provider fallback router for UNDX.

The router reads provider configuration from environment variables and never
returns provider secrets or raw provider errors to users.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from services import undx_capability_lifecycle
from services import undx_company_identity
from services import undx_fact_policy


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


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    env_keys: tuple[str, ...]
    model_env: str
    default_model: str
    kind: str


PROVIDERS: tuple[ProviderConfig, ...] = (
    ProviderConfig("openai", ("OPENAI_API_KEY",), "PULSE_AI_OPENAI_MODEL", "gpt-4o-mini", "openai"),
    ProviderConfig("claude", ("CLAUDE_AI_API", "ANTHROPIC_API_KEY"), "PULSE_AI_CLAUDE_MODEL", "claude-3-5-haiku-latest", "anthropic"),
    ProviderConfig("gemini", ("GEMINI_AI_API", "Gemini_AI_API", "GOOGLE_AI_API_KEY"), "PULSE_AI_GEMINI_MODEL", "gemini-1.5-flash", "gemini"),
    ProviderConfig("deepseek", ("DEEPSEEK_AI_API", "DEEPSEEK_API_KEY"), "PULSE_AI_DEEPSEEK_MODEL", "deepseek-chat", "openai_compatible"),
    ProviderConfig("groq", ("GROQ_AI_API", "GROQ_API_KEY"), "PULSE_AI_GROQ_MODEL", "llama-3.1-8b-instant", "openai_compatible"),
)

# Self-hosted UNDX candidate. Deliberately kept OUT of PROVIDERS so it can never
# activate from a stray key: it only joins the pool when UNDX_CANDIDATE_ENABLED is true
# AND UNDX_CANDIDATE_BASE_URL is set. Served over an OpenAI-compatible API. Off by default.
UNDX_CANDIDATE = ProviderConfig(
    "undx_candidate", ("UNDX_CANDIDATE_API_KEY",), "UNDX_CANDIDATE_MODEL", "undx-core-v1", "openai_compatible"
)


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
    return _env_text("UNDX_CANDIDATE_ENABLED", "false").lower() in TRUE_VALUES


def _candidate_base_url() -> str:
    return _env_text("UNDX_CANDIDATE_BASE_URL").rstrip("/")


def _key_for(config: ProviderConfig) -> str:
    for key in config.env_keys:
        value = _env_text(key)
        if value:
            return value
    return ""


def _provider_configured(config: ProviderConfig) -> bool:
    if config.name == UNDX_CANDIDATE.name:
        # Self-hosted endpoint may be keyless; requires the flag and a reachable base URL.
        return candidate_enabled() and bool(_candidate_base_url())
    return bool(_key_for(config))


def _provider_pool() -> list[ProviderConfig]:
    pool = list(PROVIDERS)
    if candidate_enabled():
        pool.append(UNDX_CANDIDATE)
    return pool


def _model_for(config: ProviderConfig) -> str:
    if config.name == "openai":
        return _env_text(config.model_env) or _env_text("OPENAI_MODEL") or _env_text("PULSE_AI_MODEL") or config.default_model
    return _env_text(config.model_env) or config.default_model


def provider_status() -> dict[str, Any]:
    providers = []
    for config in PROVIDERS:
        providers.append({
            "provider": config.name,
            "configured": bool(_key_for(config)),
            "model": _model_for(config),
        })
    return {
        "ok": True,
        "providers": providers,
        "candidate": {
            "provider": UNDX_CANDIDATE.name,
            "enabled": candidate_enabled(),
            "configured": _provider_configured(UNDX_CANDIDATE),
            "model": _model_for(UNDX_CANDIDATE),
        },
        "configured_count": sum(1 for item in providers if item["configured"]),
        "fallback_order": [item.name for item in _provider_pool()],
    }


def configured_providers() -> list[ProviderConfig]:
    return configured_providers_for_task("general")


def _task_preference(task: str = "general") -> list[str]:
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


def configured_providers_for_task(task: str = "general") -> list[ProviderConfig]:
    pool = _provider_pool()
    preferred = [item.strip().lower() for item in _env_text("PULSE_AI_PROVIDER_ORDER").split(",") if item.strip()]
    if not preferred:
        preferred = _task_preference(task)
    ordered = list(pool)
    if preferred:
        by_name = {item.name: item for item in pool}
        ordered = [by_name[name] for name in preferred if name in by_name] + [item for item in pool if item.name not in preferred]
    return [config for config in ordered if _provider_configured(config)]


def _safe_text(value: Any, limit: int = 6000) -> str:
    return str(value or "").strip()[:limit]


def _extract_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return _safe_text(message.get("content") or choices[0].get("text") or "")


def _post_openai_compatible(config: ProviderConfig, messages: list[dict[str, str]]) -> str:
    key = _key_for(config)
    model = _model_for(config)
    if config.name == "deepseek":
        url = "https://api.deepseek.com/chat/completions"
    elif config.name == "groq":
        url = "https://api.groq.com/openai/v1/chat/completions"
    elif config.name == UNDX_CANDIDATE.name:
        base = _candidate_base_url()
        if not base:
            raise PulseAIProviderError(config.name, "candidate_base_url_missing")
        url = f"{base}/chat/completions"
    else:
        url = "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = requests.post(
        url,
        headers=headers,
        json={"model": model, "messages": messages, "temperature": 0.35, "max_tokens": 850},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        raise PulseAIProviderError(config.name, "provider_rejected", response.status_code)
    try:
        return _extract_openai_text(response.json())
    except ValueError as exc:
        raise PulseAIProviderError(config.name, exc.__class__.__name__, response.status_code) from exc


def _post_anthropic(config: ProviderConfig, messages: list[dict[str, str]]) -> str:
    key = _key_for(config)
    system = "\n\n".join(item.get("content") or "" for item in messages if item.get("role") == "system")
    conversation = [item for item in messages if item.get("role") != "system"]
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"model": _model_for(config), "system": system, "messages": conversation, "max_tokens": 850, "temperature": 0.35},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        raise PulseAIProviderError(config.name, "provider_rejected", response.status_code)
    try:
        data = response.json()
        parts = data.get("content") or []
        return _safe_text("\n".join(part.get("text") or "" for part in parts if isinstance(part, dict)))
    except ValueError as exc:
        raise PulseAIProviderError(config.name, exc.__class__.__name__, response.status_code) from exc


def _post_gemini(config: ProviderConfig, messages: list[dict[str, str]]) -> str:
    key = _key_for(config)
    model = _model_for(config)
    system = "\n\n".join(item.get("content") or "" for item in messages if item.get("role") == "system")
    contents = []
    for item in messages:
        if item.get("role") == "system":
            continue
        role = "model" if item.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": item.get("content") or ""}]})
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        json={"system_instruction": {"parts": [{"text": system}]}, "contents": contents, "generationConfig": {"temperature": 0.35, "maxOutputTokens": 850}},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        raise PulseAIProviderError(config.name, "provider_rejected", response.status_code)
    try:
        data = response.json()
        candidates = data.get("candidates") or []
        content = (candidates[0].get("content") or {}) if candidates else {}
        parts = content.get("parts") or []
        return _safe_text("\n".join(part.get("text") or "" for part in parts if isinstance(part, dict)))
    except (ValueError, IndexError) as exc:
        raise PulseAIProviderError(config.name, exc.__class__.__name__, response.status_code) from exc


def _call_provider(config: ProviderConfig, messages: list[dict[str, str]]) -> str:
    if config.kind == "anthropic":
        return _post_anthropic(config, messages)
    if config.kind == "gemini":
        return _post_gemini(config, messages)
    return _post_openai_compatible(config, messages)


def generate_response(messages: list[dict[str, str]], correlation_id: str = "", task: str = "general") -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    try:
        final_messages = prepare_undx_model_request(messages, correlation_id)
    except PulseAIProviderError:
        return {
            "ok": False,
            "error": "identity_configuration_error",
            "reason": "identity_configuration_error",
            "message": "UNDX is temporarily unavailable. Please try again soon.",
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    providers = configured_providers_for_task(task)
    if not providers:
        return {
            "ok": False,
            "error": "ai_unavailable",
            "reason": "provider_config_missing",
            "message": "UNDX is temporarily unavailable. Please try again soon.",
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    for config in providers:
        started = time.perf_counter()
        try:
            reply = _call_provider(config, final_messages)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if not reply:
                raise PulseAIProviderError(config.name, "empty_response")
            violation = undx_identity_violation(reply)
            regenerated = False
            if violation:
                LOGGER.warning(
                    "UNDX_IDENTITY_RESPONSE_REJECTED provider=%s reason=%s correlation_id=%s",
                    config.name,
                    violation,
                    correlation_id,
                )
                correction = {
                    "role": "system",
                    "content": "Identity verification failed. Regenerate the answer while preserving the canonical UNDX identity above.",
                }
                reply = _call_provider(config, [final_messages[0], correction, *final_messages[1:]])
                regenerated = True
                violation = undx_identity_violation(reply)
            if violation:
                LOGGER.error(
                    "UNDX_IDENTITY_RESPONSE_BLOCKED provider=%s reason=%s correlation_id=%s",
                    config.name,
                    violation,
                    correlation_id,
                )
                reply = UNDX_IDENTITY_SAFE_REPLY
            return {
                "ok": True,
                "reply": reply,
                "provider": config.name,
                "model": _model_for(config),
                "latency_ms": latency_ms,
                "identity_regenerated": regenerated,
                "identity_validated": True,
                "attempts": attempts + [{"provider": config.name, "ok": True, "latency_ms": latency_ms}],
            }
        except (requests.RequestException, PulseAIProviderError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            status_code = getattr(exc, "status_code", 0)
            reason = getattr(exc, "reason", exc.__class__.__name__)
            attempts.append({"provider": config.name, "ok": False, "reason": reason, "status_code": int(status_code or 0), "latency_ms": latency_ms})
            LOGGER.warning(
                "PULSE_AI_PROVIDER_FAILED provider=%s reason=%s status_code=%s correlation_id=%s task=%s",
                config.name,
                reason,
                int(status_code or 0),
                correlation_id,
                task,
            )
    return {
        "ok": False,
        "error": "ai_unavailable",
        "reason": "all_providers_failed",
        "message": "UNDX is temporarily unavailable. Please try again soon.",
        "correlation_id": correlation_id,
        "attempts": attempts,
    }


def generate_task_response(
    messages: list[dict[str, str]],
    correlation_id: str = "",
    task: str = "general",
    unavailable_message: str = "This service is temporarily unavailable. Please try again soon.",
) -> dict[str, Any]:
    """Run a bounded non-assistant task through the existing provider pool.

    UNDX chat calls must continue through :func:`generate_response`, which injects
    and validates the canonical identity. Infrastructure tasks such as content
    translation are not assistant conversations and must not leak that identity
    into their output. They still reuse the same provider ordering, timeouts,
    secret handling, fallback behavior, and curated errors.
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
    providers = configured_providers_for_task(task)
    if not providers:
        return {
            "ok": False,
            "error": "ai_unavailable",
            "reason": "provider_config_missing",
            "message": unavailable_message,
            "correlation_id": correlation_id,
            "attempts": attempts,
        }
    for config in providers:
        started = time.perf_counter()
        try:
            reply = _call_provider(config, bounded_messages)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if not reply:
                raise PulseAIProviderError(config.name, "empty_response")
            return {
                "ok": True,
                "reply": reply,
                "provider": config.name,
                "model": _model_for(config),
                "latency_ms": latency_ms,
                "attempts": attempts + [{"provider": config.name, "ok": True, "latency_ms": latency_ms}],
            }
        except (requests.RequestException, PulseAIProviderError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            status_code = getattr(exc, "status_code", 0)
            reason = getattr(exc, "reason", exc.__class__.__name__)
            attempts.append({
                "provider": config.name,
                "ok": False,
                "reason": reason,
                "status_code": int(status_code or 0),
                "latency_ms": latency_ms,
            })
            LOGGER.warning(
                "PULSE_AI_TASK_PROVIDER_FAILED provider=%s reason=%s status_code=%s correlation_id=%s task=%s",
                config.name,
                reason,
                int(status_code or 0),
                correlation_id,
                task,
            )
    return {
        "ok": False,
        "error": "ai_unavailable",
        "reason": "all_providers_failed",
        "message": unavailable_message,
        "correlation_id": correlation_id,
        "attempts": attempts,
    }
