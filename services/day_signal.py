from datetime import datetime
from .user_context import connect

QUESTIONS = [
    {"key": "feeling", "question": "How do you feel today?", "options": [("confident", "Confident"), ("calm", "Calm"), ("nervous", "Nervous"), ("tired", "Tired")]},
    {"key": "prepared", "question": "How prepared are you for what you want to do today?", "options": [("very_prepared", "Very prepared"), ("somewhat_prepared", "Somewhat prepared"), ("not_prepared", "Not prepared"), ("guessing", "I'm guessing")]},
    {"key": "opportunity", "question": "What kind of opportunity are you thinking about?", "options": [("crypto", "Crypto/Trading"), ("sports", "Sports Edge"), ("business", "Business/Money"), ("personal", "Personal decision")]},
    {"key": "walkaway", "question": "Are you willing to walk away if the signal looks risky?", "options": [("yes", "Yes"), ("maybe", "Maybe"), ("no", "No")]},
]


PRO_KEYS = {
    "time_horizon": "Time horizon",
    "risk_tolerance": "Risk tolerance",
    "portfolio_exposure": "Portfolio exposure",
    "btc_eth_preference": "BTC/ETH preference",
    "market_sentiment": "Market sentiment",
    "news_impact": "News impact",
    "volatility_comfort": "Volatility comfort",
    "stop_loss_discipline": "Stop-loss discipline",
    "emotional_state": "Emotional state",
    "learning_goal": "Learning goal",
}


def generate(answers, pro=False):
    if not isinstance(answers, dict):
        return {"ok": False, "response": "Answer the Day Signal questions first."}
    normalized = {}
    for q in QUESTIONS:
        value = str(answers.get(q["key"], "")).strip().lower()
        valid = {item[0] for item in q["options"]}
        if value not in valid:
            return {"ok": False, "response": f"Please answer: {q['question']}"}
        normalized[q["key"]] = value
    score = (
        {"confident": 22, "calm": 25, "nervous": 12, "tired": 8}[normalized["feeling"]]
        + {"very_prepared": 35, "somewhat_prepared": 24, "not_prepared": 8, "guessing": 4}[normalized["prepared"]]
        + {"crypto": 14, "sports": 12, "business": 17, "personal": 18}[normalized["opportunity"]]
        + {"yes": 22, "maybe": 12, "no": 0}[normalized["walkaway"]]
    )
    score = max(0, min(100, score))
    signal = "Strong" if score >= 80 else "Moderate" if score >= 62 else "Caution" if score >= 42 else "Not Today"
    best = {
        "crypto": "Review your plan, risk limit, entry reason, and exit rule before touching real money.",
        "sports": "Compare matchup data, timing, and risk. If the edge is unclear, waiting is valid.",
        "business": "Take one researched step and choose the smallest move that still teaches you something.",
        "personal": "Choose the clearest next step and keep the decision reversible if possible.",
    }[normalized["opportunity"]]
    avoid = {
        "crypto": "Avoid revenge trading, oversized positions, leverage, or chasing a move.",
        "sports": "Avoid forcing a position just because a game is live.",
        "business": "Avoid rushed agreements, emotional spending, or choices you cannot explain tomorrow.",
        "personal": "Avoid high-stakes choices from fatigue, pressure, pride, or fear of missing out.",
    }[normalized["opportunity"]]
    if normalized["prepared"] in {"not_prepared", "guessing"}:
        msg = "Your signal is asking for more preparation before action. The goal today is clarity, not pressure."
    elif normalized["walkaway"] == "no":
        msg = "Your drive is active, but discipline is the missing protection layer. A strong day still needs a stop point."
    elif normalized["feeling"] == "tired":
        msg = "Your energy looks low, so treat today as a risk-control day."
    else:
        msg = "You look more aligned when confidence, preparation, and walk-away discipline are working together."
    disclaimer = "This is motivational and educational insight only. It is not financial, betting, or life advice."
    pro_section = ""
    if pro:
        context_lines = []
        for key, label in PRO_KEYS.items():
            value = str(answers.get(key, "")).strip()
            if value:
                context_lines.append(f"• {label}: {value[:120]}")
        pro_section = (
            "\n\nPro Depth:\n"
            "• Time horizon and risk tolerance should decide position size before emotion does.\n"
            "• Volatility comfort and stop-loss discipline matter more than conviction during fast moves.\n"
            "• News impact should be treated as context, not certainty.\n"
            + ("\n" + "\n".join(context_lines) if context_lines else "\nAdd Pro context fields for even sharper coaching.")
        )
    response = (
        "✨ CoinPilotX Day Signal\n\n"
        f"Today's Day Score: {score}/100\n\nSignal: {signal}\n\nAI Message:\n{msg}\n\n"
        f"Best Move Today:\n{best}\n\nAvoid Today:\n{avoid}{pro_section}\n\nRisk Reminder:\nDo not risk money you cannot afford to lose. Walk away if the setup becomes unclear.\n\nDisclaimer:\n{disclaimer}"
    )
    return {"ok": True, "score": score, "signal": signal, "message": msg, "best_move": best, "avoid": avoid, "pro_depth": bool(pro), "response": response, "disclaimer": disclaimer}


def save_result(user_id, result, answers=None):
    if not user_id or not result.get("ok"):
        return
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS day_signal_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            score INTEGER,
            signal TEXT,
            answers_json TEXT,
            response TEXT,
            created_at TEXT
        )
        """
    )
    cur.execute(
        "INSERT INTO day_signal_results (user_id, score, signal, answers_json, response, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, result.get("score"), result.get("signal"), __import__("json").dumps(answers or {}), result.get("response"), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
