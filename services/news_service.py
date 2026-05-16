"""Free/fallback crypto news intelligence for CoinPilotXAI.

No paid provider is required. Optional keys improve freshness, but RSS and
cached intelligence keep the UI useful when live providers are unavailable.
"""

import hashlib
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from . import user_context


CACHE = {"items": None, "created_at": 0, "status": {}}
CACHE_SECONDS = int(os.getenv("NEWS_CACHE_SECONDS", "900"))
TIMEOUT = float(os.getenv("NEWS_PROVIDER_TIMEOUT_SECONDS", "7"))

RSS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
]

TAG_RULES = {
    "BTC": ["btc", "bitcoin"],
    "ETH": ["eth", "ethereum", "ether"],
    "SOL": ["solana", " sol "],
    "XRP": ["xrp", "ripple"],
    "scams": ["scam", "phishing", "fraud", "drainer", "rug pull"],
    "hacks": ["hack", "exploit", "breach", "stolen", "attack"],
    "ETFs": ["etf", "fund flow", "inflow", "outflow"],
    "regulation": ["sec", "cftc", "regulation", "lawsuit", "court", "compliance", "ban"],
    "macro": ["fed", "rate", "inflation", "jobs", "dollar", "macro", "treasury"],
}


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _sentiment(title, summary):
    text = f" {title} {summary} ".lower()
    bullish = sum(1 for w in ["approval", "inflow", "rally", "record", "adoption", "surge", "partnership", "launch"] if w in text)
    bearish = sum(1 for w in ["hack", "exploit", "lawsuit", "ban", "outflow", "selloff", "fraud", "liquidation", "crackdown"] if w in text)
    if bearish > bullish:
        return "caution"
    if bullish > bearish:
        return "constructive"
    return "neutral"


def _tags(title, summary):
    text = f" {title} {summary} ".lower()
    found = []
    for tag, needles in TAG_RULES.items():
        if any(needle in text for needle in needles):
            found.append(tag)
    return found or ["market"]


def _confidence(source, summary):
    score = 0.68
    if source in {"CoinDesk", "Cointelegraph", "Decrypt", "The Block", "Bitcoin Magazine", "NewsAPI"}:
        score += 0.16
    if summary and len(summary) > 80:
        score += 0.08
    return round(min(score, 0.92), 2)


def _story_id(title, url):
    return hashlib.sha256(f"{title}|{url}".encode("utf-8")).hexdigest()[:24]


def normalize_story(title, summary="", source="", url="", published_at=""):
    title = _clean(title)[:180]
    summary = _clean(summary)[:420] or "Headline detected. Full source context may be limited."
    tags = _tags(title, summary)
    assets = [tag for tag in tags if tag in {"BTC", "ETH", "SOL", "XRP"}]
    sentiment = _sentiment(title, summary)
    ai_summary = f"{title}. Market read: {sentiment}. Watch tags: {', '.join(tags)}."
    return {
        "id": _story_id(title, url),
        "title": title,
        "summary": summary,
        "ai_summary": ai_summary,
        "source": source or "cached intelligence",
        "url": url or "",
        "published_at": published_at or _now(),
        "last_updated": _now(),
        "tags": tags,
        "affected_assets": assets,
        "sentiment": sentiment,
        "confidence": _confidence(source, summary),
    }


def _fetch_newsapi(limit):
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key:
        return [], "NEWS_API_KEY missing"
    response = requests.get(
        "https://newsapi.org/v2/everything",
        params={"q": "crypto OR bitcoin OR ethereum", "language": "en", "sortBy": "publishedAt", "pageSize": min(limit, 50), "apiKey": api_key},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    stories = []
    for article in payload.get("articles") or []:
        source = (article.get("source") or {}).get("name") or "NewsAPI"
        stories.append(normalize_story(article.get("title"), article.get("description"), source, article.get("url"), article.get("publishedAt")))
    return stories, ""


def _fetch_cryptopanic(limit):
    api_key = os.getenv("CRYPTOPANIC_API_KEY")
    if not api_key:
        return [], "CRYPTOPANIC_API_KEY missing"
    response = requests.get(
        "https://cryptopanic.com/api/v1/posts/",
        params={"auth_token": api_key, "public": "true", "kind": "news"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    stories = []
    for item in (payload.get("results") or [])[:limit]:
        stories.append(normalize_story(item.get("title"), "", item.get("source", {}).get("title") or "CryptoPanic", item.get("url"), item.get("published_at")))
    return stories, ""


def _fetch_rss(limit):
    stories = []
    errors = []
    for source, url in RSS_FEEDS:
        try:
            response = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "CoinPilotXAI/1.0"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item"):
                stories.append(normalize_story(item.findtext("title"), item.findtext("description"), source, item.findtext("link"), item.findtext("pubDate")))
                if len(stories) >= limit:
                    return stories, ""
        except Exception as exc:
            errors.append(f"{source}: {str(exc)[:120]}")
            logging.info("RSS news fetch failed for %s: %s", source, exc)
    return stories, "; ".join(errors)


def _dedupe(stories):
    seen = set()
    result = []
    for story in stories:
        key = (story.get("title") or "").lower()[:120]
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(story)
    return result


def cache_headlines(stories, topic="global"):
    if not stories:
        return
    conn = user_context.connect()
    cur = conn.cursor()
    now = _now()
    for story in stories:
        cur.execute(
            """
            INSERT INTO crypto_news_cache
            (topic, country, title, summary, sentiment, source, url, published_at, created_at, tags, affected_assets, ai_summary, confidence)
            VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic,
                story["title"],
                story["summary"],
                story["sentiment"],
                story["source"],
                story["url"],
                story["published_at"],
                now,
                json.dumps(story["tags"])[:1000],
                json.dumps(story["affected_assets"])[:1000],
                story["ai_summary"],
                story["confidence"],
            ),
        )
    conn.commit()
    conn.close()


def cached_headlines(limit=20):
    conn = user_context.connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM crypto_news_cache ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    stories = []
    for row in rows:
        stories.append({
            "id": str(row.get("id")),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "ai_summary": row.get("ai_summary") or f"{row.get('title')} Market read: {row.get('sentiment') or 'neutral'}.",
            "source": row.get("source") or "cached intelligence",
            "url": row.get("url") or "",
            "published_at": row.get("published_at") or row.get("created_at"),
            "last_updated": row.get("created_at"),
            "tags": json.loads(row.get("tags") or "[]") if row.get("tags") else _tags(row.get("title"), row.get("summary")),
            "affected_assets": json.loads(row.get("affected_assets") or "[]") if row.get("affected_assets") else [],
            "sentiment": row.get("sentiment") or "neutral",
            "confidence": float(row.get("confidence") or 0.58),
            "cached": True,
        })
    return stories


def get_crypto_news(limit=20, force_refresh=False):
    if CACHE["items"] and not force_refresh and time.time() - CACHE["created_at"] < CACHE_SECONDS:
        mode = CACHE["status"].get("mode", "live")
        return {
            "ok": True,
            "items": CACHE["items"][:limit],
            "mode": mode,
            "source": "cached intelligence" if mode != "live" else "free provider stack",
            "source_badge": "Cached intelligence" if mode != "live" else "Live free sources",
            "provider_status": CACHE["status"],
            "last_updated": CACHE["status"].get("last_updated"),
        }
    provider_status = {
        "newsapi": "missing" if not os.getenv("NEWS_API_KEY") else "configured",
        "cryptopanic": "missing" if not os.getenv("CRYPTOPANIC_API_KEY") else "configured",
        "rss": "ready",
        "coinmarketcap": "configured" if os.getenv("COINMARKETCAP_API_KEY") else "missing",
        "last_updated": _now(),
        "errors": [],
    }
    stories = []
    for name, fetcher in (("newsapi", _fetch_newsapi), ("cryptopanic", _fetch_cryptopanic), ("rss", _fetch_rss)):
        try:
            fetched, error = fetcher(limit)
            if error:
                provider_status["errors"].append({name: error})
            if fetched:
                provider_status[name] = "active"
                stories.extend(fetched)
        except Exception as exc:
            provider_status[name] = "failed"
            provider_status["errors"].append({name: str(exc)[:200]})
        if len(stories) >= limit:
            break
    stories = _dedupe(stories)[:limit]
    mode = "live"
    if stories:
        cache_headlines(stories)
    else:
        stories = cached_headlines(limit)
        mode = "cached intelligence"
    if not stories:
        stories = [
            normalize_story(
                "Crypto market news intelligence is warming up",
                "Live RSS and optional API sources are reconnecting. Watch BTC, ETH, ETF flows, regulation, scams, hacks, and macro liquidity while cached intelligence fills.",
                "CoinPilotXAI cached intelligence",
                "",
                _now(),
            )
        ]
        mode = "cached intelligence"
    provider_status["mode"] = mode
    CACHE["items"] = stories
    CACHE["created_at"] = time.time()
    CACHE["status"] = provider_status
    return {
        "ok": True,
        "items": stories[:limit],
        "mode": mode,
        "source": "cached intelligence" if mode != "live" else "free provider stack",
        "source_badge": "Cached intelligence" if mode != "live" else "Live free sources",
        "provider_status": provider_status,
        "last_updated": provider_status["last_updated"],
    }


def summarize_news(limit=6):
    payload = get_crypto_news(limit=limit)
    lines = []
    for story in payload.get("items", [])[:limit]:
        tags = ", ".join(story.get("tags") or [])
        assets = ", ".join(story.get("affected_assets") or []) or "market"
        lines.append(f"{story.get('sentiment', 'neutral').title()}: {story.get('title')} · Assets: {assets} · Tags: {tags} · Source: {story.get('source')}")
    return {
        "ok": True,
        "summary": "\n".join(lines),
        "items": payload.get("items", []),
        "source": payload.get("mode", "live"),
        "provider_status": payload.get("provider_status", {}),
        "last_updated": payload.get("last_updated"),
    }


def health():
    payload = get_crypto_news(limit=5)
    return {
        "ok": True,
        "mode": payload.get("mode"),
        "count": len(payload.get("items") or []),
        "providers": payload.get("provider_status", {}),
        "cache_age_seconds": int(time.time() - CACHE.get("created_at", 0)) if CACHE.get("created_at") else None,
    }
