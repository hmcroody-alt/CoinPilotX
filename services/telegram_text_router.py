"""Natural-language Telegram routing for the CoinPilotXAI companion bot."""

import re
from datetime import datetime

from . import live_market_service


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
    if alert_request:
        return {"intent": "create_alert", "alert": alert_request}
    if any(phrase in lowered for phrase in ["show my alerts", "my alerts", "alerts", "alert summary"]):
        return {"intent": "alert_summary"}
    if any(phrase in lowered for phrase in ["pro status", "my pro", "account status", "subscription", "am i pro"]):
        return {"intent": "account_status"}
    if any(phrase in lowered for phrase in ["alpha arena", "arena", "how do i play", "roast battle"]):
        return {
            "intent": "reply",
            "message": (
                "Alpha Arena is the CoinPilotXAI training environment for simulated market battles, Scam Hunter, live rooms, and ranked play.\n\n"
                "Start here: https://coinpilotx.app/arena/play\n"
                "Tip: use Training Missions first, then Scam Hunter, then Ranked when you’re warm."
            ),
        }
    if any(phrase in lowered for phrase in ["scam", "phishing", "wallet drain", "airdrop", "seed phrase"]):
        return {
            "intent": "reply",
            "message": (
                "Scam Shield rule of thumb: never share seed phrases, verify domains manually, and treat surprise airdrops or approval requests as high risk.\n\n"
                "Paste suspicious text or links into Scam Shield on the site for a deeper scan: https://coinpilotx.app/scam-shield"
            ),
        }
    for symbol in ("BTC", "ETH", "SOL"):
        if symbol.lower() in lowered or f"{symbol.lower()} price" in lowered:
            return {"intent": "reply", "message": _price_line(symbol)}
    if lowered in {"help", "hi", "hello", "start"}:
        return {
            "intent": "reply",
            "message": (
                "I can help with BTC/ETH/SOL market questions, Scam Shield basics, Alpha Arena, alerts, and account status.\n\n"
                "Try: “What is BTC doing?” or “create alert BTC above 100000”."
            ),
        }
    if linked_user:
        return {
            "intent": "reply",
            "message": (
                "I’m here. Ask me about markets, alerts, Scam Shield, Alpha Arena, or your Pro status.\n\n"
                "Example: “show my alerts” or “what is BTC doing?”"
            ),
        }
    return {
        "intent": "reply",
        "message": (
            "I can answer general CoinPilotXAI, market, Arena, and scam-defense questions here.\n\n"
            "For account-specific features, generate a code from Account Settings and send /link CODE."
        ),
    }
