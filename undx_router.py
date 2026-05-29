"""UNDX Intelligence Router.

Server-side provider selection for UNDX chat. The router never exposes API
keys to the browser and keeps OpenAI as the final fallback provider.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_UNDX_SYSTEM_PROMPT = (
    "You are UNDX Core, the premium intelligence layer inside CoinPilotXAI. "
    "Your job is to help build, expand, secure, and evolve CoinPilotXAI phase by phase. "
    "Respond like a strategic AI builder. When the user gives a mission, classify it, explain the objective, "
    "suggest modules, identify risks, recommend next actions, and keep responses focused on building CoinPilotXAI."
)

PROVIDER_ALIASES = {
    "anthropic": "claude",
    "claude": "claude",
    "deepseek": "deepseek",
    "gemini": "gemini",
    "google": "gemini",
    "groq": "groq",
    "openai": "openai",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    label: str
    key_env: str
    model_env: str
    default_model: str


PROVIDERS = {
    "openai": ProviderConfig("openai", "OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini"),
    "claude": ProviderConfig("claude", "Claude", "CLAUDE_AI_API", "CLAUDE_MODEL", "claude-3-5-haiku-latest"),
    "gemini": ProviderConfig("gemini", "Gemini", "Gemini_AI_API", "GEMINI_MODEL", "gemini-1.5-flash"),
    "deepseek": ProviderConfig("deepseek", "DeepSeek", "DEEPSEEK_AI_API", "DEEPSEEK_MODEL", "deepseek-chat"),
    "groq": ProviderConfig("groq", "Groq", "GROQ_AI_API", "GROQ_MODEL", "llama-3.1-8b-instant"),
}

COUNCIL_AGENT_PROVIDER_MAP = [
    {
        "key": "architect",
        "name": "Architect Agent",
        "role": "System design and mission architecture",
        "preferred_provider": "claude",
    },
    {
        "key": "research",
        "name": "Research Agent",
        "role": "Evidence, market, and technical discovery",
        "preferred_provider": "gemini",
    },
    {
        "key": "builder",
        "name": "Builder Agent",
        "role": "Implementation path and build sequencing",
        "preferred_provider": "openai",
    },
    {
        "key": "optimization",
        "name": "Optimization Agent",
        "role": "Performance, code quality, and system refinement",
        "preferred_provider": "deepseek",
    },
    {
        "key": "rapid_response",
        "name": "Rapid Response Agent",
        "role": "Fast triage, concise direction, and immediate next moves",
        "preferred_provider": "groq",
    },
]


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def router_enabled() -> bool:
    return _flag("UNDX_ROUTER_ENABLED", False)


def multi_model_mode() -> bool:
    return _flag("UNDX_MULTI_MODEL_MODE", False)


def _normalize_provider(value: str | None) -> str:
    provider = PROVIDER_ALIASES.get(str(value or "").strip().lower(), "")
    return provider if provider in PROVIDERS else "openai"


def default_provider() -> str:
    return _normalize_provider(os.getenv("UNDX_DEFAULT_AI_PROVIDER") or "openai")


def _api_key(provider: str) -> str:
    config = PROVIDERS[provider]
    if provider == "gemini":
        return (os.getenv("Gemini_AI_API") or os.getenv("GEMINI_AI_API") or "").strip()
    return (os.getenv(config.key_env) or "").strip()


def _model(provider: str) -> str:
    config = PROVIDERS[provider]
    return (os.getenv(config.model_env) or config.default_model).strip()


def provider_status() -> dict[str, bool]:
    return {provider: bool(_api_key(provider)) for provider in PROVIDERS}


def provider_label(provider: str) -> str:
    provider = _normalize_provider(provider)
    return PROVIDERS[provider].label


def council_agent_provider_plan(message: str = "") -> dict[str, Any]:
    """Return safe provider routing metadata for the UNDX Agent Council.

    The plan exposes provider availability as yes/no only. It never returns API
    keys and never calls a model. OpenAI remains the automatic fallback lane.
    """

    status = provider_status()
    classification = classify_request(message)
    openai_available = status.get("openai", False)
    agents: list[dict[str, Any]] = []

    for agent in COUNCIL_AGENT_PROVIDER_MAP:
        preferred = _normalize_provider(agent["preferred_provider"])
        preferred_available = status.get(preferred, False)
        selected = preferred if preferred_available else "openai"
        fallback_used = selected != preferred
        selected_available = status.get(selected, False)
        if fallback_used:
            fallback_status = "Using OpenAI fallback" if openai_available else "OpenAI fallback unavailable"
        else:
            fallback_status = "Not needed"

        agents.append(
            {
                "key": agent["key"],
                "name": agent["name"],
                "role": agent["role"],
                "preferred_provider": preferred,
                "preferred_provider_label": provider_label(preferred),
                "selected_provider": selected,
                "selected_provider_label": provider_label(selected),
                "provider_status": "Configured" if preferred_available else "Unavailable",
                "selected_provider_status": "Configured" if selected_available else "Unavailable",
                "fallback_provider": "openai",
                "fallback_provider_label": "OpenAI",
                "fallback_used": fallback_used,
                "fallback_status": fallback_status,
            }
        )

    return {
        "name": "UNDX Intelligence Router",
        "classification": classification,
        "router_enabled": router_enabled(),
        "multi_model_mode": multi_model_mode(),
        "default_provider": default_provider(),
        "fallback_provider": "openai",
        "fallback_provider_label": "OpenAI",
        "providers": status,
        "agents": agents,
    }


def log_provider_status() -> None:
    status = provider_status()
    logging.info(
        "UNDX provider keys configured: openai=%s claude=%s gemini=%s deepseek=%s groq=%s",
        "yes" if status["openai"] else "no",
        "yes" if status["claude"] else "no",
        "yes" if status["gemini"] else "no",
        "yes" if status["deepseek"] else "no",
        "yes" if status["groq"] else "no",
    )


def _clean_text(value: Any, limit: int = 2200) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    return re.sub(r"\s+\n", "\n", text).strip()[:limit]


def clean_history(history: Any) -> list[dict[str, str]]:
    clean: list[dict[str, str]] = []
    if not isinstance(history, list):
        return clean
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        if role == "undx":
            role = "assistant"
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(item.get("text") or item.get("content") or "", 1800)
        if content:
            clean.append({"role": role, "content": content})
    return clean


def classify_request(message: str) -> dict[str, Any]:
    text = _clean_text(message, 2600).lower()
    rules = [
        ("security", ["security", "secure", "scam", "risk", "wallet", "auth", "token", "secret", ".env", "credential"]),
        ("repository", ["repo", "repository", "folder", "file", "code", "debug", "bug", "diff", "commit", "git", "python", "javascript"]),
        ("automation", ["automation", "agent", "workflow", "memory", "schedule", "mission control", "autonomous"]),
        ("research", ["research", "market", "crypto", "token", "analysis", "compare", "investigate", "evidence"]),
        ("product", ["dashboard", "ui", "interface", "design", "premium", "page", "product", "growth"]),
    ]
    matches: dict[str, list[str]] = {}
    for category, terms in rules:
        found = [term for term in terms if term in text]
        if found:
            matches[category] = found[:6]

    if not matches and len(text) < 160:
        category = "fast_directive"
    elif matches:
        category = max(matches, key=lambda key: len(matches[key]))
    else:
        category = "general_builder"

    return {
        "category": category,
        "signals": matches.get(category, []),
        "reason": f"Matched {category.replace('_', ' ')} routing signals." if matches else "No specialized routing signals found.",
    }


def provider_priority(classification: dict[str, Any]) -> list[str]:
    category = classification.get("category")
    priorities = {
        "security": ["claude", "openai", "deepseek", "gemini", "groq"],
        "repository": ["deepseek", "openai", "claude", "gemini", "groq"],
        "automation": ["openai", "groq", "claude", "deepseek", "gemini"],
        "research": ["gemini", "openai", "claude", "deepseek", "groq"],
        "product": ["openai", "claude", "gemini", "groq", "deepseek"],
        "fast_directive": ["groq", "openai", "claude", "gemini", "deepseek"],
        "general_builder": ["openai", "claude", "gemini", "deepseek", "groq"],
    }
    selected = priorities.get(str(category), priorities["general_builder"])
    preferred = default_provider()
    ordered = selected if multi_model_mode() else [preferred]
    ordered = [preferred, *ordered] if preferred not in ordered else ordered
    if "openai" not in ordered:
        ordered.append("openai")
    return list(dict.fromkeys(provider for provider in ordered if provider in PROVIDERS))


def _messages(system_prompt: str, message: str, history: Any) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(clean_history(history))
    messages.append(
        {
            "role": "user",
            "content": (
                f"Mission directive from user:\n{_clean_text(message)}\n\n"
                "When relevant, use these sections: Mission Classification, Objective, Suggested Modules, "
                "Build Steps, Security Notes, Recommended Next Action."
            ),
        }
    )
    return messages


def _openai_compatible(provider: str, endpoint: str, system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    config = PROVIDERS[provider]
    payload = {
        "model": _model(provider),
        "messages": _messages(system_prompt, message, history),
        "max_tokens": 900,
        "temperature": 0.35,
    }
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {_api_key(provider)}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    text = response.json()["choices"][0]["message"]["content"].strip()
    return {"text": text, "model": payload["model"], "source": config.label}


def _call_openai(system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    return _openai_compatible("openai", "https://api.openai.com/v1/chat/completions", system_prompt, message, history, timeout)


def _call_deepseek(system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    return _openai_compatible("deepseek", "https://api.deepseek.com/chat/completions", system_prompt, message, history, timeout)


def _call_groq(system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    return _openai_compatible("groq", "https://api.groq.com/openai/v1/chat/completions", system_prompt, message, history, timeout)


def _call_claude(system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    messages = [item for item in _messages(system_prompt, message, history) if item["role"] != "system"]
    payload = {
        "model": _model("claude"),
        "system": system_prompt,
        "messages": messages,
        "max_tokens": 900,
        "temperature": 0.35,
    }
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": _api_key("claude"),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    text = "".join(part.get("text", "") for part in data.get("content", []) if part.get("type") == "text").strip()
    return {"text": text, "model": payload["model"], "source": "Claude"}


def _call_gemini(system_prompt: str, message: str, history: Any, timeout: int) -> dict[str, Any]:
    contents = []
    for item in clean_history(history):
        contents.append({"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]})
    contents.append({"role": "user", "parts": [{"text": _messages(system_prompt, message, [])[1]["content"]}]})
    model = _model("gemini")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": _api_key("gemini")},
        headers={"Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": 0.35, "maxOutputTokens": 900},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    parts = data["candidates"][0]["content"].get("parts", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    return {"text": text, "model": model, "source": "Gemini"}


CALLERS = {
    "openai": _call_openai,
    "claude": _call_claude,
    "gemini": _call_gemini,
    "deepseek": _call_deepseek,
    "groq": _call_groq,
}


def route_undx_request(user_id: Any, message: str, history: Any = None, system_prompt: str = DEFAULT_UNDX_SYSTEM_PROMPT, timeout: int = 25) -> dict[str, Any]:
    started = time.time()
    message = _clean_text(message, 2200)
    log_provider_status()
    classification = classify_request(message)
    ordered = provider_priority(classification) if router_enabled() else ["openai"]
    attempts: list[dict[str, str]] = []

    for provider in ordered:
        config = PROVIDERS[provider]
        if not _api_key(provider):
            attempts.append({"provider": config.label, "status": "not_configured"})
            continue
        try:
            result = CALLERS[provider](system_prompt, message, history or [], timeout)
            text = _clean_text(result.get("text"), 5200)
            if not text:
                raise ValueError("empty provider response")
            return {
                "ok": True,
                "response": text,
                "builder_directive": f"UNDX mission directive:\n{message}\n\nUNDX response:\n{text}",
                "source": result.get("source") or config.label,
                "provider": provider,
                "model": result.get("model") or _model(provider),
                "classification": classification,
                "router": {
                    "name": "UNDX Intelligence Router",
                    "enabled": router_enabled(),
                    "multi_model_mode": multi_model_mode(),
                    "default_provider": default_provider(),
                    "selected_provider": provider,
                    "fallback_provider": "openai",
                    "attempts": attempts + [{"provider": config.label, "status": "success"}],
                },
                "latency_ms": int((time.time() - started) * 1000),
            }
        except requests.Timeout:
            logging.warning("UNDX router provider timeout user_id=%s provider=%s", user_id, provider)
            attempts.append({"provider": config.label, "status": "timeout"})
        except requests.RequestException as exc:
            logging.warning("UNDX router provider request failed provider=%s error=%s", provider, exc)
            attempts.append({"provider": config.label, "status": "request_failed"})
        except Exception as exc:
            logging.warning("UNDX router provider response failed provider=%s error=%s", provider, exc)
            attempts.append({"provider": config.label, "status": "response_failed"})

    openai_configured = bool(_api_key("openai"))
    status = 502 if openai_configured else 503
    error = "UNDX OpenAI bridge is temporarily unavailable." if openai_configured else "OpenAI intelligence bridge is not configured on this server."
    return {
        "ok": False,
        "status": status,
        "error": error,
        "source": "OpenAI",
        "provider": "openai",
        "classification": classification,
        "router": {
            "name": "UNDX Intelligence Router",
            "enabled": router_enabled(),
            "multi_model_mode": multi_model_mode(),
            "default_provider": default_provider(),
            "fallback_provider": "openai",
            "attempts": attempts,
        },
    }
