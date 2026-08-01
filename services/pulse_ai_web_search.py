"""Safe, timeout-bound web search helper for Pulse AI.

The service prefers configured search APIs and falls back to DuckDuckGo's public
instant-answer endpoint. It is intentionally small, cache-first, and never
blocks normal Messenger behavior when live sources are unavailable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import time
from typing import Any
from urllib.parse import quote_plus

import requests

from services.undx_brain import envelope


LOGGER = logging.getLogger(__name__)
DEFAULT_TIMEOUT_SECONDS = 6.0
MAX_RESULTS = 5
_CACHE: dict[str, dict[str, Any]] = {}


FRESHNESS_TERMS = {
    "latest",
    "current",
    "recent",
    "today",
    "tonight",
    "now",
    "breaking",
    "live",
    "price",
    "prices",
    "news",
    "advisory",
    "advisories",
    "cve",
    "vulnerability",
    "vulnerabilities",
    "app store",
    "regulation",
    "weather",
    "market",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
}

TRUSTED_SOURCE_HINTS = {
    "cisa.gov",
    "nist.gov",
    "microsoft.com",
    "apple.com",
    "google.com",
    "cloudflare.com",
    "mozilla.org",
    "owasp.org",
    "mitre.org",
    "cve.org",
    "coingecko.com",
    "coinmarketcap.com",
    "binance.com",
    "kraken.com",
    "finance.yahoo.com",
    "nasdaq.com",
    "sec.gov",
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "nasa.gov",
    "noaa.gov",
    "usgs.gov",
}


def _env(key: str) -> str:
    value = os.getenv(key, "")
    return value.strip() if isinstance(value, str) else ""


def _timeout() -> float:
    try:
        return max(2.0, min(float(_env("PULSE_AI_WEB_SEARCH_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS), 8.0))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _ttl_seconds() -> int:
    try:
        return max(60, min(int(_env("PULSE_AI_WEB_SEARCH_CACHE_SECONDS") or "900"), 3600))
    except ValueError:
        return 900


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:24]


def _cached(query: str) -> dict[str, Any] | None:
    key = _cache_key(query)
    item = _CACHE.get(key)
    if not item:
        return None
    expires_at = item.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at > datetime.now(timezone.utc):
        payload = dict(item.get("payload") or {})
        payload["cache_hit"] = True
        return payload
    _CACHE.pop(key, None)
    return None


def _save_cache(query: str, payload: dict[str, Any]) -> None:
    _CACHE[_cache_key(query)] = {
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=_ttl_seconds()),
        "payload": dict(payload),
    }


def should_search(query: str) -> bool:
    lowered = " ".join(str(query or "").lower().split())
    if not lowered:
        return False
    if "search the web" in lowered or "look up" in lowered or "web search" in lowered:
        return True
    return any(term in lowered for term in FRESHNESS_TERMS)


def provider_status() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": [
            {"provider": "brave", "configured": bool(_env("BRAVE_SEARCH_API_KEY"))},
            {"provider": "bing", "configured": bool(_env("BING_SEARCH_API_KEY") or _env("BING_SEARCH_V7_SUBSCRIPTION_KEY"))},
            {"provider": "serpapi", "configured": bool(_env("SERPAPI_API_KEY"))},
            {"provider": "tavily", "configured": bool(_env("TAVILY_API_KEY"))},
            {"provider": "duckduckgo_instant", "configured": True},
        ],
        "timeout_seconds": _timeout(),
        "cache_seconds": _ttl_seconds(),
    }


def _clean_result(title: Any, url: Any, snippet: Any, source: str) -> dict[str, str]:
    clean_url = str(url or "").strip()[:500]
    domain_quality = "trusted" if any(hint in clean_url.lower() for hint in TRUSTED_SOURCE_HINTS) else "unverified"
    return {
        "title": " ".join(str(title or "Untitled result").split())[:180],
        "url": clean_url,
        "snippet": " ".join(str(snippet or "").split())[:500],
        "source": source,
        "quality": domain_quality,
    }


def _search_brave(query: str) -> dict[str, Any]:
    key = _env("BRAVE_SEARCH_API_KEY")
    if not key:
        return {"ok": False, "reason": "config_missing"}
    response = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": key},
        params={"q": query, "count": MAX_RESULTS, "safesearch": "moderate"},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}
    data = response.json()
    results = [
        _clean_result(item.get("title"), item.get("url"), item.get("description"), "brave")
        for item in ((data.get("web") or {}).get("results") or [])[:MAX_RESULTS]
    ]
    return {"ok": bool(results), "provider": "brave", "results": results, "reason": "" if results else "empty"}


def _search_bing(query: str) -> dict[str, Any]:
    key = _env("BING_SEARCH_API_KEY") or _env("BING_SEARCH_V7_SUBSCRIPTION_KEY")
    if not key:
        return {"ok": False, "reason": "config_missing"}
    response = requests.get(
        "https://api.bing.microsoft.com/v7.0/search",
        headers={"Ocp-Apim-Subscription-Key": key},
        params={"q": query, "count": MAX_RESULTS, "safeSearch": "Moderate", "textDecorations": False, "textFormat": "Raw"},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}
    data = response.json()
    results = [
        _clean_result(item.get("name"), item.get("url"), item.get("snippet"), "bing")
        for item in ((data.get("webPages") or {}).get("value") or [])[:MAX_RESULTS]
    ]
    return {"ok": bool(results), "provider": "bing", "results": results, "reason": "" if results else "empty"}


def _search_serpapi(query: str) -> dict[str, Any]:
    key = _env("SERPAPI_API_KEY")
    if not key:
        return {"ok": False, "reason": "config_missing"}
    response = requests.get(
        "https://serpapi.com/search.json",
        params={"q": query, "api_key": key, "num": MAX_RESULTS, "safe": "active"},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}
    data = response.json()
    results = [
        _clean_result(item.get("title"), item.get("link"), item.get("snippet"), "serpapi")
        for item in (data.get("organic_results") or [])[:MAX_RESULTS]
    ]
    return {"ok": bool(results), "provider": "serpapi", "results": results, "reason": "" if results else "empty"}


def _search_tavily(query: str) -> dict[str, Any]:
    key = _env("TAVILY_API_KEY")
    if not key:
        return {"ok": False, "reason": "config_missing"}
    response = requests.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "search_depth": "basic", "max_results": MAX_RESULTS, "include_answer": True},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}
    data = response.json()
    results = [
        _clean_result(item.get("title"), item.get("url"), item.get("content"), "tavily")
        for item in (data.get("results") or [])[:MAX_RESULTS]
    ]
    return {"ok": bool(results), "provider": "tavily", "results": results, "answer": data.get("answer") or "", "reason": "" if results else "empty"}


def _search_duckduckgo(query: str) -> dict[str, Any]:
    response = requests.get(
        "https://api.duckduckgo.com/",
        params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
        timeout=_timeout(),
    )
    if not (200 <= response.status_code < 300):
        return {"ok": False, "reason": "provider_rejected", "status_code": response.status_code}
    data = response.json()
    results: list[dict[str, str]] = []
    if data.get("AbstractText") or data.get("AbstractURL"):
        results.append(_clean_result(data.get("Heading") or query, data.get("AbstractURL"), data.get("AbstractText"), "duckduckgo_instant"))
    for item in (data.get("RelatedTopics") or [])[:8]:
        if isinstance(item, dict) and item.get("FirstURL"):
            results.append(_clean_result(item.get("Text"), item.get("FirstURL"), item.get("Text"), "duckduckgo_instant"))
        if len(results) >= MAX_RESULTS:
            break
    return {"ok": bool(results), "provider": "duckduckgo_instant", "results": results, "reason": "" if results else "empty"}


def search(query: str, *, purpose: str = "pulse_ai", force: bool = False) -> dict[str, Any]:
    query = " ".join(str(query or "").split())[:240]
    started = time.perf_counter()
    if not query:
        return {"ok": False, "error": "empty_query", "results": [], "latency_ms": 0}
    if not force and not should_search(query):
        return {"ok": False, "error": "not_needed", "results": [], "latency_ms": 0}
    cached = _cached(query)
    if cached:
        return cached

    attempts: list[dict[str, Any]] = []
    for searcher in (_search_brave, _search_bing, _search_serpapi, _search_tavily, _search_duckduckgo):
        provider_name = searcher.__name__.replace("_search_", "")
        try:
            result = searcher(query)
            attempts.append({"provider": provider_name, "ok": bool(result.get("ok")), "reason": result.get("reason") or ""})
            if result.get("ok"):
                payload = {
                    "ok": True,
                    "query": query,
                    "provider": result.get("provider") or provider_name,
                    "results": result.get("results") or [],
                    "answer": result.get("answer") or "",
                    "attempts": attempts,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "cache_hit": False,
                    "purpose": purpose,
                }
                _save_cache(query, payload)
                return payload
        except (requests.RequestException, ValueError) as exc:
            attempts.append({"provider": provider_name, "ok": False, "reason": exc.__class__.__name__})
            LOGGER.warning("PULSE_AI_WEB_SEARCH_PROVIDER_FAILED provider=%s reason=%s purpose=%s", provider_name, exc.__class__.__name__, purpose)

    return {
        "ok": False,
        "error": "search_unavailable",
        "message": "I couldn't reach live sources right now, but I can still help with general guidance.",
        "query": query,
        "results": [],
        "attempts": attempts,
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "search_url": f"https://duckduckgo.com/?q={quote_plus(query)}",
    }


#: The line this function has always opened with. Kept as a constant because the
#: enveloped form deliberately drops it — the envelope's declaration says everything
#: this sentence said and says it about the right thing — and a test needs to be able to
#: assert both the unchanged legacy string and its absence from the sealed form.
LEGACY_PREAMBLE = (
    "Live web search context. Use carefully, cite source names when helpful, and say if "
    "information may change:"
)


def context_block(search_result: dict[str, Any], *, env: Any = None) -> str:
    """Render search results for the prompt, sealed if the envelope flag allows it.

    This is the most attacker-controllable input in the system: anybody who can rank for
    a query can write into it. Until the envelope existed it was the *least* protected —
    the string this function returns is inserted into the ``knowledge`` list by
    ``pulse_ai_service``, and ``pulse_ai_knowledge.build_system_prompt`` renders that
    list into the **system message** under the heading ``Approved PulseSoc knowledge``.
    A stranger's web page was arriving labelled approved, in the message carrying the
    most authority in the request. A preamble asking the model to "use carefully" is not
    a boundary; it is a request, addressed to the same reader the attacker is addressing.

    With ``UNDX_BRAIN_ENVELOPE_ENABLED`` off this returns exactly the string it always
    returned, byte for byte, including the preamble and the 4000-character clamp. On, it
    returns a sealed envelope instead: the results go inside a fence they cannot escape,
    the declaration naming them as web text with no authority goes before them, and a
    reassertion goes after so the payload never has the last word. The preamble is
    dropped in that form because the declaration supersedes it.

    The clamp is applied to the payload *before* sealing, never to the rendered
    envelope. Truncating a sealed string would cut the closing fence off and produce
    exactly the unterminated-fence state the envelope exists to make impossible.
    """
    if not search_result.get("ok"):
        return ""
    lines = []
    if search_result.get("answer"):
        lines.append(f"Summary: {search_result['answer']}")
    for item in (search_result.get("results") or [])[:MAX_RESULTS]:
        title = item.get("title") or "Result"
        snippet = item.get("snippet") or ""
        url = item.get("url") or ""
        quality = item.get("quality") or "unverified"
        lines.append(f"- {title} ({quality}): {snippet} Source: {url}")
    if not envelope.enabled(env):
        return "\n".join([LEGACY_PREAMBLE, *lines])[:4000]
    if not lines:
        # Off, an ``ok`` result with nothing in it has always rendered the bare preamble,
        # and that stays exactly as it was. On, an envelope around nothing is prompt
        # budget spent to say nothing, so it renders nothing.
        return ""
    return envelope.seal("\n".join(lines)[:4000], envelope.Provenance.WEB_SEARCH).rendered
