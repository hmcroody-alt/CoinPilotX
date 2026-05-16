"""Arena psychology scoring for fake-money educational battles."""

from __future__ import annotations


def analyze_trade_behavior(side: str, notional: float, balance: float, existing_position: float = 0.0, recent_loss: bool = False):
    side = (side or "").lower()
    balance = max(float(balance or 0), 1.0)
    notional = max(float(notional or 0), 0.0)
    exposure_pct = min(100.0, (notional / balance) * 100.0)
    fomo = 10
    revenge = 8 if recent_loss else 0
    panic = 8
    greed = 10
    discipline = 82
    notes = []

    if exposure_pct > 60:
        fomo += 34
        greed += 30
        discipline -= 34
        notes.append("Position size is very large for a training battle.")
    elif exposure_pct > 30:
        fomo += 18
        greed += 16
        discipline -= 18
        notes.append("Position size is meaningful. Risk control matters here.")
    else:
        discipline += 8
        notes.append("Position size is controlled for a learning scenario.")

    if side == "sell" and existing_position <= 0:
        panic += 20
        discipline -= 10
        notes.append("Selling without an existing position can reflect a rushed reaction in this fake engine.")
    if recent_loss:
        revenge += 22
        discipline -= 12
        notes.append("Recent losses can create revenge-trading pressure.")

    return {
        "fomo_score": max(0, min(100, round(fomo))),
        "revenge_score": max(0, min(100, round(revenge))),
        "panic_score": max(0, min(100, round(panic))),
        "greed_score": max(0, min(100, round(greed))),
        "discipline_score": max(0, min(100, round(discipline))),
        "emotional_state": "disciplined" if discipline >= 75 else "heated" if max(fomo, revenge, panic, greed) >= 50 else "watchful",
        "notes": notes,
    }


def match_psychology_summary(scores):
    scores = scores or {}
    discipline = int(scores.get("discipline_score") or 50)
    fomo = int(scores.get("fomo_score") or 0)
    revenge = int(scores.get("revenge_score") or 0)
    if discipline >= 80:
        return "Strong discipline. The player is prioritizing process over impulse."
    if fomo >= 55:
        return "FOMO risk is elevated. The safer training move is to slow down and confirm the setup."
    if revenge >= 45:
        return "Revenge-trading pressure detected. A simulator pause would protect decision quality."
    return "Balanced psychology profile. Keep position size and emotional state visible."
