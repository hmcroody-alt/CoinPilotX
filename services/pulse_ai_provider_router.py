"""Provider fallback router for UNDX.

The router reads provider configuration from environment variables and never
returns provider secrets or raw provider errors to users.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests


LOGGER = logging.getLogger(__name__)
TRUE_VALUES = {"1", "true", "yes", "on"}
DEFAULT_TIMEOUT_SECONDS = 18


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


class PulseAIProviderError(RuntimeError):
    def __init__(self, provider: str, reason: str, status_code: int = 0):
        super().__init__(reason)
        self.provider = provider
        self.reason = reason
        self.status_code = status_code


def _env_text(key: str, default: str = "") -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else default


def _timeout() -> float:
    try:
        return max(2.0, min(float(_env_text("PULSE_AI_PROVIDER_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))), 45.0))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _key_for(config: ProviderConfig) -> str:
    for key in config.env_keys:
        value = _env_text(key)
        if value:
            return value
    return ""


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
        "configured_count": sum(1 for item in providers if item["configured"]),
        "fallback_order": [item.name for item in PROVIDERS],
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
    preferred = [item.strip().lower() for item in _env_text("PULSE_AI_PROVIDER_ORDER").split(",") if item.strip()]
    if not preferred:
        preferred = _task_preference(task)
    ordered = list(PROVIDERS)
    if preferred:
        by_name = {item.name: item for item in PROVIDERS}
        ordered = [by_name[name] for name in preferred if name in by_name] + [item for item in PROVIDERS if item.name not in preferred]
    return [config for config in ordered if _key_for(config)]


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
    else:
        url = "https://api.openai.com/v1/chat/completions"
    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
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
            reply = _call_provider(config, messages)
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
