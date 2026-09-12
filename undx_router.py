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
    "You are UNDX Core, the premium intelligence layer inside CoinPlotXAI. "
    "Your job is to help build, expand, secure, and evolve CoinPlotXAI phase by phase. "
    "Respond like a strategic AI builder. When the user gives a mission, classify it, explain the objective, "
    "suggest modules, identify risks, recommend next actions, and keep responses focused on building CoinPlotXAI."
)

PROVIDER_ALIASES = {
    "anthropic": "claude",
    "claude": "claude",
    "deepseek": "deepseek",
    "gemini": "gemini",
    "google": "gemini",
    "groq": "groq",
    "meta": "meta",
    "muse": "meta",
    "openai": "openai",
    "perplexity": "perplexity",
    "pplx": "perplexity",
    "sonar": "perplexity",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    label: str
    key_env: str
    model_env: str
    default_model: str
    #: Env flag that must not be false for this provider to be tried at all.
    #: A key alone is not consent - §73 of the multi-provider brief requires a
    #: per-provider kill switch that takes a provider out of rotation without
    #: deleting the credential. Empty means "no switch, key presence decides".
    enable_env: str = ""
    #: Env var holding a per-provider timeout in milliseconds, and the fallback
    #: used when it is unset. Meta's first call on a cold route measured 26.7s,
    #: which the module-wide 25s default would have cut off.
    timeout_ms_env: str = ""
    default_timeout_ms: int = 0
    #: Tokens this provider spends thinking before it emits any answer, which on
    #: a reasoning model come out of the same `max_tokens` budget as the answer.
    #: See `_effective_max_tokens`.
    reasoning_overhead_tokens: int = 0


PROVIDERS = {
    "openai": ProviderConfig("openai", "OpenAI", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini",
                             enable_env="UNDX_OPENAI_ENABLED"),
    # `claude-3-5-haiku-latest` was retired upstream and 404s, which is the whole
    # of the "Claude is dead in production" outage - the credential was always
    # valid. `claude-haiku-4-5` is an alias that resolves live to
    # claude-haiku-4-5-20251001; verified against GET /v1/models, not guessed.
    "claude": ProviderConfig("claude", "Claude", "CLAUDE_AI_API", "CLAUDE_MODEL", "claude-haiku-4-5",
                             enable_env="UNDX_CLAUDE_ENABLED"),
    "gemini": ProviderConfig("gemini", "Gemini", "Gemini_AI_API", "GEMINI_MODEL", "gemini-1.5-flash"),
    "deepseek": ProviderConfig("deepseek", "DeepSeek", "DEEPSEEK_AI_API", "DEEPSEEK_MODEL", "deepseek-chat"),
    "groq": ProviderConfig("groq", "Groq", "GROQ_AI_API", "GROQ_MODEL", "llama-3.1-8b-instant"),
    # Meta Model API. Model IDs, base URL, reasoning enum and the 1M context are
    # from the live console for project 1656198352782001, not from documentation:
    # see UNDX_META_MUSE_CONFIGURATION.md. The default is the Standard-tier model
    # deliberately - the Contributor variant is 95% cheaper because Meta trains on
    # its inputs and outputs, which is not a trade PulseSoc user content can make.
    "meta": ProviderConfig("meta", "Meta Muse", "META_MODEL_API_KEY", "META_MUSE_MODEL", "muse-spark-1.3",
                           enable_env="META_MUSE_ENABLED",
                           timeout_ms_env="META_MUSE_TIMEOUT_MS", default_timeout_ms=60000,
                           reasoning_overhead_tokens=3000),
    # Perplexity is the grounded-research lane: it answers from a live search and
    # returns the sources alongside the prose (§20/§59). `sonar-reasoning` is
    # retired upstream and 400s; `sonar` and `sonar-pro` are current.
    "perplexity": ProviderConfig("perplexity", "Perplexity", "PERPLEXITY_API_KEY", "PERPLEXITY_MODEL", "sonar",
                                 enable_env="UNDX_PERPLEXITY_ENABLED",
                                 timeout_ms_env="PERPLEXITY_TIMEOUT_MS", default_timeout_ms=45000),
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
    {
        "key": "testing",
        "name": "Testing Agent",
        "role": "Testing strategy, validation planning, regression planning, and release confidence",
        "preferred_provider": "openai",
    },
    {
        "key": "security",
        "name": "Security Agent",
        "role": "Security review, threat analysis, permission review, and risk analysis",
        "preferred_provider": "claude",
    },
    {
        "key": "documentation",
        "name": "Documentation Agent",
        "role": "Documentation planning, onboarding plans, change summaries, and release documentation",
        "preferred_provider": "openai",
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


def _raw_api_key(provider: str) -> str:
    """The configured value exactly as set, valid or not.

    Redaction needs this: a malformed value is still secret-bearing, and is in
    fact the value most likely to end up in a log, because it is the one that
    makes the HTTP layer raise.
    """
    config = PROVIDERS[provider]
    if provider == "gemini":
        return (os.getenv("Gemini_AI_API") or os.getenv("GEMINI_AI_API") or "").strip()
    return (os.getenv(config.key_env) or "").strip()


def _api_key(provider: str) -> str:
    """The configured credential, or empty if it cannot safely be sent.

    A key is sent as an HTTP header value, and a header value containing a
    newline is rejected by the HTTP layer - which raises an exception quoting the
    offending value. That is how a credential reaches the application log: not
    through a successful request, but through a malformed one.

    This is not hypothetical. A provider variable in this project was set to a
    JSON configuration document that contained a key, rather than to the key.
    Every call raised, and every raise logged a slice of that document.

    Returning empty here means the provider is reported unconfigured and skipped
    before anything touches the credential, so a misconfigured variable costs one
    provider rather than leaking its contents once per request.
    """
    raw = _raw_api_key(provider)
    if not raw or any(char in raw for char in "\r\n\t") or " " in raw:
        return ""
    return raw


def _credential_fragments(secret: str) -> list[str]:
    """Credential-shaped substrings of a configured value.

    Redacting only the whole value assumes the variable holds exactly one key.
    A Railway variable in this project holds a JSON document that *contains* a
    key, and `requests` reproduced a slice of that document - key included - in
    its exception text. The slice never equalled the variable, so whole-value
    matching found nothing and the key was logged.

    Splitting on characters that cannot appear inside a token, then keeping the
    long fragments, catches the key whatever wrapper it arrived in. The floor is
    high enough that ordinary words in a config blob are not redacted out of
    error messages.
    """
    fragments = re.split(r"[^A-Za-z0-9_\-.~+/=]+", secret)
    return sorted({f for f in fragments if len(f) >= 20}, key=len, reverse=True)


def _safe_error(exc: Exception) -> str:
    """Exception text for a log line, with any credential removed.

    `requests` puts the full request URL into the string form of its exceptions,
    so anything a provider carries in a query string is reproduced verbatim in
    whatever the caller logs. The Gemini call used to pass `?key=<API_KEY>` and
    therefore wrote the key into the application log on every failed request.

    That call now sends a header instead, so this is defence in depth rather
    than the fix. It stays because the next provider added here will be written
    by copying an existing one, and a redaction that only exists in the caller's
    memory is not a redaction. Known key material is matched by value as well,
    which catches a leak through a parameter name nobody thought of.
    """
    text = str(exc)
    text = re.sub(r"([?&])(key|api_key|access_token|token)=[^&\s\"']+", r"\1\2=***", text, flags=re.I)
    # A JSON-embedded credential names itself: `"api_key": "gsk_..."`. Redact by
    # position before the value-matching below, which catches the same thing only
    # when the exact configured value is present.
    text = re.sub(r'("?(?:api[_-]?key|secret|token|password)"?\s*[:=]\s*"?)([A-Za-z0-9_\-.~+/=]{16,})',
                  r"\1***", text, flags=re.I)
    for provider in PROVIDERS:
        secret = _raw_api_key(provider)
        # Short values are not credentials and would redact ordinary words.
        if secret and len(secret) >= 8:
            text = text.replace(secret, "***")
            for fragment in _credential_fragments(secret):
                text = text.replace(fragment, "***")
    return text[:400]


def _model(provider: str) -> str:
    config = PROVIDERS[provider]
    return (os.getenv(config.model_env) or config.default_model).strip()


def provider_enabled(provider: str) -> bool:
    """Whether this provider may be tried, ignoring whether it is configured.

    Separate from key presence so a provider can be pulled out of rotation
    without deleting its credential - the difference between "we are not using
    Meta right now" and "we have lost the ability to use Meta". Defaults to
    enabled: a provider with a key and no switch behaves exactly as before.
    """
    config = PROVIDERS[provider]
    return _flag(config.enable_env, True) if config.enable_env else True


def _timeout(provider: str, fallback_seconds: int) -> int:
    """Seconds to allow this provider, preferring its own configured budget.

    The caller's number is a module-wide default chosen for chat completions.
    A reasoning model on a cold route takes considerably longer than that - the
    first Meta call measured against the live API took 26.7s against a 25s
    default - so a provider that declares its own budget wins, and the caller's
    number is only ever used to raise it, never to cut it short.
    """
    config = PROVIDERS[provider]
    declared_ms = 0
    if config.timeout_ms_env:
        raw = (os.getenv(config.timeout_ms_env) or "").strip()
        if raw.isdigit():
            declared_ms = int(raw)
    declared_ms = declared_ms or config.default_timeout_ms
    if not declared_ms:
        return fallback_seconds
    return max(fallback_seconds, (declared_ms + 999) // 1000)


def _effective_max_tokens(provider: str, max_tokens: int) -> int:
    """Raise the output budget to cover a reasoning model's hidden spend.

    On Meta's Muse Spark, `max_tokens` bounds reasoning *and* answer together.
    Measured against the live API: a two-letter answer at `reasoning_effort=high`
    consumed 381 completion tokens, 370 of them reasoning. So this module's 900
    token chat default leaves a real task almost nothing, and the 320 that
    `route_structured_request` asks for can be spent entirely on thinking - the
    provider then returns `content: null` with `finish_reason: "length"`, which
    reads as a broken provider rather than as a budget that was too small.

    Non-reasoning providers declare no overhead and are returned untouched.
    """
    overhead = PROVIDERS[provider].reasoning_overhead_tokens
    return max_tokens + overhead if overhead else max_tokens


def _provider_text(provider: str, content: Any, finish_reason: Any = None) -> str:
    """Text from a completion, with an empty answer named rather than crashed on.

    `content` is not always a string. A reasoning model that spends its whole
    budget thinking returns JSON `null` here, and `None.strip()` raises
    AttributeError inside the per-provider `try`, where it is caught, logged as a
    generic `response_failed` and silently failed over. The provider is fine; the
    request was under-budgeted. Distinguishing the two is the difference between
    a one-line config fix and a provider that appears permanently broken.
    """
    if isinstance(content, str) and content.strip():
        return content.strip()
    reason = str(finish_reason or "").strip() or "unspecified"
    if reason == "length":
        raise ValueError(
            f"{PROVIDERS[provider].label} returned no text: the token budget was "
            f"consumed before the answer began (finish_reason=length)"
        )
    raise ValueError(f"{PROVIDERS[provider].label} returned no text (finish_reason={reason})")


def provider_status() -> dict[str, bool]:
    return {provider: bool(_api_key(provider)) for provider in PROVIDERS}


def provider_health(provider: str, routing_available: bool = True) -> str:
    provider = _normalize_provider(provider)
    if not _api_key(provider):
        # "Set, but not to something sendable" is a different problem from "not
        # set", and reporting both as missing sends whoever investigates looking
        # for an absent variable that is right there in the dashboard.
        return "Malformed API Key" if _raw_api_key(provider) else "Missing API Key"
    if not provider_enabled(provider):
        return "Disabled"
    return "Online" if routing_available else "Offline"


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
    routing_available = True
    openai_health = provider_health("openai", routing_available)
    openai_available = openai_health == "Online"
    agents: list[dict[str, Any]] = []

    for agent in COUNCIL_AGENT_PROVIDER_MAP:
        preferred = _normalize_provider(agent["preferred_provider"])
        preferred_health = provider_health(preferred, routing_available)
        preferred_available = preferred_health == "Online"
        selected = preferred if preferred_available else "openai"
        fallback_used = selected != preferred
        selected_health = provider_health(selected, routing_available)
        if fallback_used:
            fallback_status = "Fallback Active" if openai_available else openai_health
            display_status = "Fallback Active" if openai_available else preferred_health
        else:
            fallback_status = "Not needed"
            display_status = preferred_health

        agents.append(
            {
                "key": agent["key"],
                "name": agent["name"],
                "role": agent["role"],
                "preferred_provider": preferred,
                "preferred_provider_label": provider_label(preferred),
                "preferred_provider_status": preferred_health,
                "selected_provider": selected,
                "selected_provider_label": provider_label(selected),
                "provider_status": display_status,
                "selected_provider_status": selected_health,
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
        "router_status": "Online" if routing_available else "Offline",
        "multi_model_mode": multi_model_mode(),
        "default_provider": default_provider(),
        "fallback_provider": "openai",
        "fallback_provider_label": "OpenAI",
        "providers": status,
        "agents": agents,
    }


def log_provider_status() -> None:
    """One line naming what the router can currently reach.

    Derived from PROVIDERS rather than written out, because the hardcoded
    five-provider version of this line kept reporting five providers after a
    sixth was added - it did not fail, it just quietly stopped being the whole
    picture, which is worse in the one place that exists to give the whole
    picture. Reports keys and switches separately: "configured but switched off"
    and "never configured" call for opposite fixes.
    """
    status = provider_status()
    summary = " ".join(
        f"{provider}={'yes' if status[provider] else 'no'}"
        f"{'' if provider_enabled(provider) else '(disabled)'}"
        for provider in sorted(PROVIDERS)
    )
    logging.info("UNDX provider keys configured: %s", summary)


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
        # Freshness is checked before every other category. A question about what
        # is true *now* routed to a model answering from training data does not
        # fail loudly - it returns a confident, well-formed, stale answer, and the
        # only reader able to detect it is the one who already knew. Every other
        # category here can be served acceptably by any provider; this one cannot.
        ("current_web", ["today", "right now", "latest", "current", "currently", "news", "this week",
                         "this month", "recent", "as of", "up to date", "breaking", "announced",
                         "price of", "stock", "who won", "release date", "2026", "2027"]),
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

    if "current_web" in matches:
        # Deliberately not decided by signal count. "What is the latest Stripe
        # webhook version" carries one freshness term against several repository
        # ones, and counting hands it to a provider with no access to the answer.
        category = "current_web"
    elif not matches and len(text) < 160:
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
        # Perplexity leads exactly one lane, and leads it because it is the only
        # provider here that can see the answer - it searches at request time and
        # returns its sources. That is a structural difference, not a quality
        # judgement, so it does not wait on benchmark evidence.
        "current_web": ["perplexity", "openai", "claude", "gemini", "meta", "groq"],
        "security": ["claude", "openai", "deepseek", "gemini", "meta", "groq"],
        # Meta Muse is built for long-horizon multi-step work over a 1M-token
        # context, which is what the repository and automation lanes are. It sits
        # behind the incumbents on purpose: §7 of the integration brief admits it
        # as an available specialist, and promoting it past a provider already
        # serving production is a decision for benchmark evidence, not for the
        # commit that first makes it reachable.
        "repository": ["deepseek", "openai", "claude", "meta", "gemini", "groq"],
        "automation": ["openai", "groq", "claude", "meta", "deepseek", "gemini"],
        "research": ["perplexity", "gemini", "openai", "claude", "meta", "deepseek"],
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
    # A provider switched off must not merely be skipped when the loop reaches
    # it: it must not be planned for. Otherwise `attempts` reports a provider
    # that was never going to be tried, and the kill switch looks like a failure.
    return list(dict.fromkeys(
        provider for provider in ordered
        if provider in PROVIDERS and provider_enabled(provider)
    ))


def _messages(system_prompt: str, message: str, history: Any,
              *, user_content: str | None = None) -> list[dict[str, str]]:
    """Provider-neutral message list.

    ``user_content``, when supplied, replaces the builder scaffolding on the final
    user turn and is sent verbatim. That scaffolding names report sections
    ("Mission Classification", "Build Steps", …) and is exactly right for the
    mission-builder this router was written for — and exactly wrong for a caller
    that needs one machine-readable object back. Such a caller would be fighting
    its own transport for the shape of the answer.

    Defaulting to ``None`` keeps every existing call byte-identical.
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(clean_history(history))
    messages.append(
        {
            "role": "user",
            "content": _clean_text(user_content, 6000) if user_content else (
                f"Mission directive from user:\n{_clean_text(message)}\n\n"
                "When relevant, use these sections: Mission Classification, Objective, Suggested Modules, "
                "Build Steps, Security Notes, Recommended Next Action."
            ),
        }
    )
    return messages


def _openai_compatible(provider: str, endpoint: str, system_prompt: str, message: str, history: Any, timeout: int,
                       *, user_content: str | None = None,
                       temperature: float = 0.35, max_tokens: int = 900,
                       extra_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    config = PROVIDERS[provider]
    payload = {
        "model": _model(provider),
        "messages": _messages(system_prompt, message, history, user_content=user_content),
        "max_tokens": _effective_max_tokens(provider, max_tokens),
        "temperature": temperature,
    }
    payload.update(extra_payload or {})
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {_api_key(provider)}", "Content-Type": "application/json"},
        json=payload,
        timeout=_timeout(provider, timeout),
    )
    response.raise_for_status()
    choice = (response.json().get("choices") or [{}])[0]
    text = _provider_text(provider, (choice.get("message") or {}).get("content"), choice.get("finish_reason"))
    return {"text": text, "model": payload["model"], "source": config.label}


def _call_openai(system_prompt: str, message: str, history: Any, timeout: int, **kwargs: Any) -> dict[str, Any]:
    return _openai_compatible("openai", "https://api.openai.com/v1/chat/completions", system_prompt, message, history, timeout, **kwargs)


def _call_deepseek(system_prompt: str, message: str, history: Any, timeout: int, **kwargs: Any) -> dict[str, Any]:
    return _openai_compatible("deepseek", "https://api.deepseek.com/chat/completions", system_prompt, message, history, timeout, **kwargs)


def _call_groq(system_prompt: str, message: str, history: Any, timeout: int, **kwargs: Any) -> dict[str, Any]:
    return _openai_compatible("groq", "https://api.groq.com/openai/v1/chat/completions", system_prompt, message, history, timeout, **kwargs)


META_BASE_URL = "https://api.meta.ai/v1"

#: Accepted by the live API; anything else is rejected with HTTP 400 naming the
#: full set, which is how this list was obtained rather than guessed. `max` is
#: Standard-tier muse-spark-1.3 only.
META_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def _meta_reasoning_effort() -> str:
    value = (os.getenv("META_MUSE_REASONING_EFFORT") or "high").strip().lower()
    return value if value in META_REASONING_EFFORTS else "high"


def _call_meta(system_prompt: str, message: str, history: Any, timeout: int, **kwargs: Any) -> dict[str, Any]:
    """Meta Model API, over its OpenAI-compatible chat surface.

    Meta also exposes a Responses API, and the configuration this integration
    started from named it. Chat completions is used instead because it is the
    shape every other provider in this module already speaks, so Muse joins the
    existing failover loop rather than needing a second response-parsing path -
    and because the one thing Responses offers that matters here, reasoning
    summaries, is not actually delivered: asked with `summary: "auto"` the live
    API returned an empty summary list. A parsing branch maintained for a field
    that arrives empty is a liability, not a capability.

    `reasoning_effort` is sent on every call. The model reasons whether or not it
    is asked to, so the choice is between a budget this module sets deliberately
    and whatever the default happens to become.
    """
    return _openai_compatible(
        "meta", f"{META_BASE_URL}/chat/completions", system_prompt, message, history, timeout,
        extra_payload={"reasoning_effort": _meta_reasoning_effort()}, **kwargs,
    )


def _call_perplexity(system_prompt: str, message: str, history: Any, timeout: int, **kwargs: Any) -> dict[str, Any]:
    """Perplexity, whose answer is only half the payload.

    Every response carries the pages it was grounded in. Those are the reason to
    route a question here at all: a research answer whose sources were dropped in
    transit is indistinguishable from the same sentence invented by a model that
    has never seen the web, and UNDX cannot attribute what it was not handed.
    So the citations ride back in the envelope.
    """
    config = PROVIDERS["perplexity"]
    payload = {
        "model": _model("perplexity"),
        "messages": _messages(system_prompt, message, history, user_content=kwargs.get("user_content")),
        "max_tokens": kwargs.get("max_tokens", 900),
        "temperature": kwargs.get("temperature", 0.35),
    }
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {_api_key('perplexity')}", "Content-Type": "application/json"},
        json=payload,
        timeout=_timeout("perplexity", timeout),
    )
    response.raise_for_status()
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    text = _provider_text("perplexity", (choice.get("message") or {}).get("content"), choice.get("finish_reason"))
    sources = data.get("search_results") or data.get("citations") or []
    return {
        "text": text,
        "model": payload["model"],
        "source": config.label,
        "citations": sources if isinstance(sources, list) else [],
    }


def _call_claude(system_prompt: str, message: str, history: Any, timeout: int,
                 *, user_content: str | None = None,
                 temperature: float = 0.35, max_tokens: int = 900) -> dict[str, Any]:
    messages = [item for item in _messages(system_prompt, message, history, user_content=user_content)
                if item["role"] != "system"]
    payload = {
        "model": _model("claude"),
        "system": system_prompt,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # The endpoint is hardcoded, and deliberately does NOT honour
    # ANTHROPIC_BASE_URL. That variable is set in this production environment -
    # to `https://api.meta.ai`, alongside ANTHROPIC_MODEL=muse-spark-1.3-contributor.
    # They are Claude Code CLI settings that leaked into the service. A router
    # that honoured them would silently send every request routed to the "Claude"
    # specialist to Meta's Contributor tier instead - the tier whose console
    # states inputs and outputs are used to train Meta's models, and which
    # UNDX_PROVIDER_DATA_POLICY.md restricts to SYNTHETIC traffic only. The
    # provider a caller asked for is the provider that must answer, and an
    # ambient environment variable does not get to redirect user content into a
    # training corpus. Pinned by ClaudeEndpointTest.
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": _api_key("claude"),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=_timeout("claude", timeout),
    )
    response.raise_for_status()
    data = response.json()
    joined = "".join(part.get("text") or "" for part in (data.get("content") or []) if part.get("type") == "text")
    text = _provider_text("claude", joined, data.get("stop_reason"))
    return {"text": text, "model": payload["model"], "source": "Claude"}


def _call_gemini(system_prompt: str, message: str, history: Any, timeout: int,
                 *, user_content: str | None = None,
                 temperature: float = 0.35, max_tokens: int = 900) -> dict[str, Any]:
    contents = []
    for item in clean_history(history):
        contents.append({"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]})
    contents.append({"role": "user", "parts": [{"text": _messages(system_prompt, message, [], user_content=user_content)[1]["content"]}]})
    model = _model("gemini")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        # The key travels as a header, never as `?key=`. Google accepts both, but
        # a query parameter is copied into every proxy access log, every browser
        # referer, and - the reason this was found - into the string form of
        # requests.RequestException, which this module logs on failure. Sending
        # it as a query parameter meant a Gemini outage wrote the API key into
        # the application log once per failed request.
        headers={"Content-Type": "application/json", "x-goog-api-key": _api_key("gemini")},
        json={
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    joined = "".join(part.get("text") or "" for part in parts)
    text = _provider_text("gemini", joined, candidate.get("finishReason"))
    return {"text": text, "model": model, "source": "Gemini"}


CALLERS = {
    "openai": _call_openai,
    "claude": _call_claude,
    "gemini": _call_gemini,
    "deepseek": _call_deepseek,
    "groq": _call_groq,
    "meta": _call_meta,
    "perplexity": _call_perplexity,
}


def route_structured_request(
    user_id: Any,
    system_prompt: str,
    user_content: str,
    *,
    timeout: int = 12,
    temperature: float = 0.0,
    max_tokens: int = 320,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    """One model turn whose answer is meant to be parsed, not read.

    Same provider selection, same server-side key handling and same failover as
    :func:`route_undx_request` — the point of routing through here rather than
    calling a provider directly is that API keys stay on the server and one module
    owns which vendor is tried in what order.

    What differs is the contract. The user turn is sent verbatim instead of being
    wrapped in the mission-builder scaffolding, temperature defaults to ``0`` and the
    budget is small, because a caller that needs a single structured object back
    gains nothing from either creativity or length.

    Returns the same envelope shape as :func:`route_undx_request`. It deliberately
    does not parse or validate the text: this function knows about transport, and
    the caller knows what the answer is supposed to mean.
    """
    history: list[dict[str, str]] = []
    ordered = [p for p in (providers or []) if p in PROVIDERS and provider_enabled(p)]
    if not ordered:
        ordered = provider_priority(classify_request(user_content)) if router_enabled() \
            else [default_provider()]
    attempts: list[dict[str, str]] = []
    started = time.time()

    for provider in ordered:
        config = PROVIDERS[provider]
        if not _api_key(provider):
            attempts.append({"provider": config.label, "status": "not_configured"})
            continue
        try:
            result = CALLERS[provider](
                system_prompt, "", history, timeout,
                user_content=user_content, temperature=temperature, max_tokens=max_tokens,
            )
            text = _clean_text(result.get("text"), 4000)
            if not text:
                raise ValueError("empty provider response")
            return {
                "ok": True,
                "response": text,
                "provider": provider,
                "source": result.get("source") or config.label,
                "model": result.get("model") or _model(provider),
                "citations": result.get("citations") or [],
                "attempts": attempts + [{"provider": config.label, "status": "success"}],
                "latency_ms": int((time.time() - started) * 1000),
            }
        except requests.Timeout:
            logging.warning("UNDX structured provider timeout user_id=%s provider=%s", user_id, provider)
            attempts.append({"provider": config.label, "status": "timeout"})
        except requests.RequestException as exc:
            logging.warning("UNDX structured provider request failed provider=%s error=%s",
                            provider, _safe_error(exc))
            attempts.append({"provider": config.label, "status": "request_failed"})
        except Exception as exc:  # noqa: BLE001 - a transport fault must stay a typed miss
            logging.warning("UNDX structured provider response failed provider=%s error=%s",
                            provider, _safe_error(exc))
            attempts.append({"provider": config.label, "status": "response_failed"})

    return {
        "ok": False,
        "response": "",
        "error": "no configured provider answered",
        "attempts": attempts,
        "latency_ms": int((time.time() - started) * 1000),
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
                # Present and empty for every provider that does not ground its
                # answer, so a caller can render attribution without first
                # knowing which provider served the request.
                "citations": result.get("citations") or [],
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
            logging.warning("UNDX router provider request failed provider=%s error=%s", provider, _safe_error(exc))
            attempts.append({"provider": config.label, "status": "request_failed"})
        except Exception as exc:
            logging.warning("UNDX router provider response failed provider=%s error=%s", provider, _safe_error(exc))
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
