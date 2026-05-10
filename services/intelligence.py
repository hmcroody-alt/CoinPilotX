import os
import requests
from . import market_data

DISCLAIMER = "Informational only — not financial advice."


def fallback_response(question, pro=False):
    snapshot = market_data.live_market_board(limit=8)
    summary = snapshot.get("summary", {})
    live_note = snapshot.get("warning") or "Live market data is available from public sources."
    depth = "Pro view includes deeper risk context and what could change the signal." if pro else "Free view gives a shorter safety-first summary."
    return (
        "💬 CoinPilotX AI Assistant\n\n"
        f"Market Snapshot:\nBTC: {summary.get('btc_price') or 'unavailable'} · ETH: {summary.get('eth_price') or 'unavailable'}\n"
        f"Momentum Read:\nMarket trend appears {summary.get('market_trend', 'mixed')} based on available live data.\n\n"
        f"Risk Level:\n{summary.get('risk_level', 'Medium')}\n\n"
        f"What to Watch:\n{live_note}\n\n"
        f"Safer Next Step:\nAsk one specific question, verify live data, and avoid decisions based on urgency. {depth}\n\n"
        f"Question received: {question[:500]}\n\n{DISCLAIMER}"
    )


def assistant_response(user_id, question, pro=False):
    api_key = os.getenv("OPENAI_API_KEY")
    import logging
    logging.info("OpenAI key loaded: %s", bool(api_key))
    if not api_key:
        return (
            "AI intelligence is temporarily unavailable. The OpenAI key is not configured.\n\n"
            "Educational information only. Not financial, betting, investment, or legal advice."
        )
    snapshot = market_data.live_market_board(limit=10)
    system = (
        "You are CoinPilotX, powered by CoinPilotXAI Inc. Give honest crypto, wallet, scam, market, sports, and portfolio education. "
        "Never guarantee profits, betting wins, certainty, or insider information. Never ask for seed phrases, private keys, recovery phrases, wallet passwords, or signing credentials. "
        "Use sections: Market Snapshot, Momentum Read, Risk Level, What to Watch, Safer Next Step, Disclaimer. "
        "If live data is unavailable, say so clearly."
    )
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Live context: {snapshot}\n\nQuestion: {question}"},
        ],
        "max_tokens": 850 if pro else 320,
        "temperature": 0.35,
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        required = "Educational information only. Not financial, betting, investment, or legal advice."
        if "not financial" not in text.lower() and "financial advice" not in text.lower():
            text += f"\n\n{required}"
        logging.info("OpenAI response success")
        return text
    except Exception as exc:
        logging.warning("OpenAI error message: %s", exc)
        return fallback_response(question, pro)


def intelligence_feed():
    board = market_data.live_market_board(limit=12)
    summary = board.get("summary", {})
    avg = summary.get("average_change_24h")
    signal = 50
    if avg is not None:
        signal = max(1, min(99, int(55 + float(avg) * 8)))
    risk = 55 if summary.get("risk_level") == "Elevated" else 38
    if avg is not None and abs(float(avg)) > 3:
        risk = 72
    action = "WATCH CLOSELY" if risk >= 70 else "WAIT" if signal < 58 else "HOLD"
    return {
        "signal": signal,
        "risk": risk,
        "action": action,
        "btc_price": summary.get("btc_price"),
        "eth_price": summary.get("eth_price"),
        "market_state": summary.get("market_trend", "mixed"),
        "confidence": 70 if board.get("markets") else 35,
        "updated_at": board.get("updated_at"),
        "source": board.get("source"),
        "warning": board.get("warning"),
        "educational": DISCLAIMER,
    }
