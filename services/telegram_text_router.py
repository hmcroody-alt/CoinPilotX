"""Natural-language Telegram routing for the CoinPilotXAI companion bot."""

import json
import os
import re
from datetime import datetime

import requests

from . import live_market_service, scam_shield_engine


ALERT_RE = re.compile(
    r"(?:create\s+)?(?:alert|notify\s+me\s+when)\s+([A-Za-z]{2,10})\s+(above|over|below|under)\s+\$?([0-9][0-9,]*(?:\.[0-9]+)?)",
    re.I,
)


def parse_alert_request(text):
    match = ALERT_RE.search(text or "")
    if not match:
        return None
    condition = match.group(2).lower()
    return {
        "symbol": match.group(1).upper(),
        "condition": "below" if condition in {"below", "under"} else "above",
        "threshold": float(match.group(3).replace(",", "")),
    }


def _price_line(symbol):
    quote = live_market_service.get_crypto_quote(symbol)
    if not quote.get("ok"):
        return "I couldn’t reach live market data right now. Try again shortly."
    asset = quote.get("asset") or {}
    price = asset.get("price") or asset.get("current_price") or asset.get("usd")
    change = asset.get("change_24h") or asset.get("price_change_percentage_24h")
    if price is None:
        return "I couldn’t reach live market data right now. Try again shortly."
    change_text = f" · 24h {float(change):+.2f}%" if change is not None else ""
    return f"{symbol.upper()} is about ${float(price):,.2f}{change_text}.\nUpdated: {quote.get('updated_at') or datetime.utcnow().isoformat(timespec='seconds')} · Source: {quote.get('source') or 'live provider'}"


def route_text(text, linked_user=None):
    raw = (text or "").strip()
    lowered = raw.lower()
    alert_request = parse_alert_request(raw)
    if any(phrase in lowered for phrase in ["connect my account", "link telegram", "how do i connect", "website account", "pair my telegram", "connect website", "connectwebsite"]):
        return {"intent": "reply", "message": connect_website_instructions()}
    if alert_request:
        return {"intent": "create_alert", "alert": alert_request}
    if any(phrase in lowered for phrase in ["show my alerts", "my alerts", "alerts", "alert summary"]):
        return {"intent": "alert_summary"}
    if any(phrase in lowered for phrase in ["pro status", "my pro", "account status", "subscription", "am i pro"]):
        return {"intent": "account_status"}
    if any(phrase in lowered for phrase in ["alpha arena", "arena", "how do i play", "roast battle"]):
        return {"intent": "openai", "message": raw}
    if any(phrase in lowered for phrase in ["http", "www.", ".com", ".app", ".xyz", "scam", "phishing", "wallet drain", "airdrop", "seed phrase", "private key", "connect wallet"]):
        result = scam_shield_engine.analyze(raw, "telegram_text")
        if result.get("ok"):
            return {"intent": "reply", "message": format_scam_scan(result)}
    for symbol in ("BTC", "ETH", "SOL"):
        if symbol.lower() in lowered or f"{symbol.lower()} price" in lowered:
            return {"intent": "reply", "message": _price_line(symbol)}
    if lowered == "help":
        return {
            "intent": "reply",
            "message": (
                "I can help with BTC/ETH/SOL market questions, Scam Shield basics, Alpha Arena, alerts, and account status.\n\n"
                "Try: “What is BTC doing?” or “create alert BTC above 100000”."
            ),
        }
    return {"intent": "openai", "message": raw}


def connect_website_instructions():
    return (
        "CONNECT YOUR WEBSITE ACCOUNT:\n"
        "1. Open PulseSoc website\n"
        "2. Go to Account -> Telegram Companion\n"
        "3. Generate link code\n"
        "4. Send this in Telegram:\n"
        "/link YOUR_CODE\n\n"
        "Also works: /connect YOUR_CODE\n"
        "Website: https://pulsesoc.com/account"
    )


def format_scam_scan(result):
    flags = result.get("red_flags") or []
    actions = result.get("safe_actions") or []
    lines = [
        "Scam Shield scan",
        "",
        f"Risk: {result.get('risk_level')} ({result.get('risk_score')}/100)",
        f"Confidence: {float(result.get('confidence') or 0):.2f}",
        "",
        result.get("summary") or "Scan complete.",
    ]
    if flags:
        lines.extend(["", "Red flags:"] + [f"- {item}" for item in flags[:4]])
    if actions:
        lines.extend(["", "Safe next steps:"] + [f"- {item}" for item in actions[:4]])
    if result.get("risk_level") in {"High", "Critical"}:
        lines.extend(["", "Do not connect your wallet, sign approvals, share seed phrases, or send funds until independently verified."])
    return "\n".join(lines)[:3500]


def answer_telegram_with_openai(user_text, user_context=None):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return "I’m online, but my AI brain is temporarily unavailable. Try again in a moment or use /help."
    context = user_context or {}
    system = (
        "You are the CoinPilotXAI Telegram companion. Answer concisely for Telegram. "
        "You can explain CoinPilotXAI, Alpha Arena, Roast Battle, alerts, Scam Shield, portfolio concepts, wallets, and crypto basics. "
        "Educational information only, not financial advice. Never request seed phrases, private keys, or wallet passwords. "
        "Do not invent live prices; if live data is unavailable, say so."
    )
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_TELEGRAM_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Linked account: {bool(context.get('linked_user'))}\nQuestion: {user_text[:3000]}"},
                ],
                "temperature": 0.35,
                "max_tokens": 420,
            },
            timeout=15,
        )
        if not response.ok:
            return "I’m online, but my AI brain is temporarily unavailable. Try again in a moment or use /help."
        content = response.json()["choices"][0]["message"]["content"].strip()
        return content[:3500] or "I’m online. Ask me about alerts, Alpha Arena, Scam Shield, or market questions."
    except Exception:
        return "I’m online, but my AI brain is temporarily unavailable. Try again in a moment or use /help."
