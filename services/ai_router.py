"""AI orchestration layer for concise, live-data-aware CoinPlotXAI answers."""

import time

# `os` used to be imported for one thing: reading `OPENAI_API_KEY` to decide what to
# tell the user about where their answer came from. Routing removed the question, so
# the import goes with it rather than staying as a hint that this module still has an
# opinion about a specific vendor's credentials.
from . import intelligence, live_market_service, predictions_service, scam_shield, undx_call_domain, wallet_intel


SYSTEM_RULES = (
    "Answer directly. Be concise. Use live data when relevant. "
    "Do not invent prices, news, probabilities, or outcomes. "
    "Never ask for seed phrases, private keys, wallet passwords, or exchange passwords. "
    "No guaranteed profit claims."
)


def _live_prefix():
    return "Live source is reconnecting right now. Here is what I can safely tell you…"


def _trim(text, limit=2400):
    text = (text or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def route(user_id, message, pro=False, memory=None, timeout_seconds=12):
    started = time.time()
    message = _trim(message, 1800)
    low = message.lower()
    source = "coinplotxai"
    confidence = "Medium"
    response = ""
    live_context = {}

    if any(token in low for token in ["btc", "bitcoin price", "/btc"]):
        quote = live_market_service.get_crypto_quote("BTC")
        live_context["btc"] = quote
        if quote.get("ok") and quote.get("asset", {}).get("price"):
            asset = quote["asset"]
            response = f"BTC is about ${float(asset.get('price') or 0):,.2f}"
            if asset.get("change_24h") is not None:
                response += f", {float(asset.get('change_24h')):+.2f}% over 24h."
            source = quote.get("source") or "market provider"
            confidence = "High"
        else:
            response = f"{_live_prefix()}\n\nBTC quote data is temporarily unavailable from the live provider."
            source = "market provider unavailable"
            confidence = "Low"
    elif any(token in low for token in ["market", "prices", "gainers", "losers"]):
        market = live_market_service.get_crypto_market(limit=8)
        live_context["market"] = market
        rows = []
        for item in (market.get("markets") or [])[:6]:
            price = item.get("price")
            change = item.get("change_24h")
            line = f"{item.get('symbol')}: ${float(price or 0):,.2f}"
            if change is not None:
                line += f" ({float(change):+.2f}% 24h)"
            rows.append(line)
        response = "\n".join(rows) if rows else f"{_live_prefix()}\n\nMarket list data is temporarily unavailable."
        source = market.get("source") or "market provider"
        confidence = "High" if rows else "Low"
    elif "scam" in low or "seed phrase" in low or "airdrop" in low or "private key" in low:
        result = scam_shield.analyze_text(message)
        response = result.get("response") or result.get("explanation") or "Scam Shield could not analyze this text right now."
        live_context["scam"] = result
        source = "CoinPlotXAI Scam Shield"
        confidence = result.get("confidence", "High" if result.get("risk_level") in {"critical", "high"} else "Medium")
    elif "wallet" in low or "address" in low:
        result = wallet_intel.analyze_public_identifier(message)
        response = result.get("response") or "Address detected. I need transaction/context to assess risk."
        source = "Wallet intelligence"
    elif "prediction" in low:
        parts = message.split()
        market_id = next((part for part in parts if "-" in part and len(part) > 6), "")
        context = predictions_service.get_prediction_context_for_ai(market_id) if market_id else ""
        if not context or context == "Prediction context unavailable.":
            markets = predictions_service.get_active_crypto_predictions(limit=5)
            context = "\n".join(f"{m.get('title')} · {m.get('yes_probability')}% · {m.get('source')}" for m in markets)
        response = context or f"{_live_prefix()}\n\nPrediction data is temporarily unavailable."
        source = "Predictions provider"
    else:
        prompt = f"{SYSTEM_RULES}\n\nUser question:\n{message}"
        # GENERAL (§5): both callers of `route` are website chat routes
        # (`bot.py:29535`, `bot.py:29590`). Declared here rather than threaded through
        # the signature because there is no second provenance to distinguish, and an
        # argument every caller passes the same value to is a default with extra steps.
        answer = intelligence.assistant_response_envelope(
            user_id, prompt, pro=pro,
            call_domain=undx_call_domain.CALL_DOMAIN_GENERAL,
        )
        response = answer["text"]
        # Reports who actually answered. This line used to read `OPENAI_API_KEY` and
        # print "OpenAI + CoinPlotXAI context" if it was set — a test of *configuration*
        # standing in for a fact about *execution*. Routing is what made that false
        # rather than merely fragile: the key can be present while Gemini answers, and
        # absent while Claude answers perfectly well. `source` is user-visible, so it
        # now comes from the envelope or says plainly that no provider was involved.
        source = (f"{answer['source']} + CoinPlotXAI context" if answer["routed"]
                  else intelligence.FALLBACK_SOURCE_LABEL)

    if not response:
        response = f"{_live_prefix()}\n\nI could not produce a reliable answer from the available providers."
        confidence = "Low"

    return {
        "ok": True,
        "response": _trim(response, 3200),
        "source": source,
        "confidence": confidence,
        "latency_ms": int((time.time() - started) * 1000),
        "live_context": live_context,
        "rules": SYSTEM_RULES,
    }
