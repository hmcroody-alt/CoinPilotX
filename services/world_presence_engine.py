"""World presence feed that makes CoinPilotXAI feel alive without fake users."""

from datetime import datetime

from . import live_event_engine, live_market_service, roast_live_engine


def _market_line(symbol):
    quote = live_market_service.get_crypto_quote(symbol)
    if not quote.get("ok"):
        return None
    asset = quote.get("asset") or {}
    price = asset.get("price") or asset.get("current_price") or asset.get("usd")
    change = asset.get("change_24h") or asset.get("price_change_percentage_24h")
    if price is None:
        return None
    change_text = f" · 24h {float(change):+.2f}%" if change is not None else ""
    return {"type": "market", "title": f"{symbol.upper()} live pulse", "body": f"${float(price):,.2f}{change_text}", "url": f"/quote/crypto/{symbol.upper()}"}


def world_feed(limit=12):
    items = []
    for symbol in ("BTC", "ETH", "SOL"):
        line = _market_line(symbol)
        if line:
            items.append(line)
    roast = roast_live_engine.snapshot(1)
    items.append({"type": "roast", "title": "Roast Battle world stage", "body": f"{roast['watching_worldwide']:,} watching · heat {roast['heat_meter']}%", "url": "/arena/roast-battle"})
    items.append({"type": "arena", "title": "Start Here path active", "body": "Training Mission → Scam Hunter → Quick Battle → Live Room → Roast Battle", "url": "/arena/play"})
    items.append({"type": "trust", "title": "Scam Shield lesson", "body": "Verify before signing. CoinPilotXAI never asks for seed phrases.", "url": "/scam-shield"})
    live_events = live_event_engine.poll("global", after_id=0, limit=5)
    for event in live_events[-5:]:
        payload = event.get("payload") or {}
        items.append({"type": event.get("event_type"), "title": payload.get("title") or "Live platform event", "body": payload.get("body") or "CoinPilotXAI live event", "url": payload.get("url") or "/arena/play"})
    return {"ok": True, "updated_at": datetime.utcnow().isoformat(timespec="seconds"), "items": items[: int(limit or 12)]}
