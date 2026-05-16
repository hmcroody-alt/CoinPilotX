"""Continuous scenario generation for CoinPilotXAI Arena."""

from __future__ import annotations

import hashlib
from datetime import datetime


SCENARIOS = [
    ("market_panic", "BTC drops fast while social feeds amplify fear.", ["Wait for confirmation", "Full-size panic sell", "Scan news source", "Chase rebound"]),
    ("fake_support", "A fake support account says your wallet must be validated.", ["Never share secrets", "Send seed phrase", "Install remote access", "Pay unlock fee"]),
    ("whale_movement", "A whale transfer appears during low liquidity.", ["Observe and size small", "Assume guaranteed pump", "Go max leverage", "Ignore risk"]),
    ("breakout_trap", "Price breaks resistance but volume is weak.", ["Wait for confirmation", "FOMO buy", "Ask AI for context", "Use fake small size"]),
    ("fake_airdrop", "A free token claim asks you to connect wallet now.", ["Scan first", "Sign approval", "Share private key", "Ignore official sources"]),
    ("volatility_spike", "Volatility expands near a major news event.", ["Reduce size", "Revenge trade", "Observe", "Force entry"]),
]


def generate_scenario(mode="quick_battle", difficulty=1, player_profile=None, world_state=None):
    seed_text = f"{mode}:{difficulty}:{datetime.utcnow().strftime('%Y-%m-%d:%H:%M:%S')}:{(player_profile or {}).get('user_id','')}"
    index = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16) % len(SCENARIOS)
    key, prompt, options = SCENARIOS[index]
    if world_state and world_state.get("title"):
        prompt = f"{prompt} World state: {world_state.get('title')}."
    round_id = hashlib.sha256(f"{seed_text}:{key}".encode("utf-8")).hexdigest()[:16]
    return {
        "round_id": round_id,
        "scenario_key": key,
        "mode": mode,
        "difficulty": max(1, int(difficulty or 1)),
        "prompt": prompt,
        "options": options,
        "best_actions": [options[0]],
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def score_answer(scenario, answer):
    answer = answer or ""
    best_actions = (scenario or {}).get("best_actions") or []
    correct_answer = best_actions[0] if best_actions else ""
    best = set(best_actions)
    correct = answer in best
    partial = any(word in answer.lower() for word in ["scan", "wait", "observe", "small", "reduce"])
    score = 90 if correct else 55 if partial else 25
    xp = 35 + max(0, score - 40)
    key = (scenario or {}).get("scenario_key") or ""
    explanations = {
        "fake_support": {
            "correct": "Correct. Never share seed phrases, private keys, wallet passwords, or recovery codes. Real support will never ask for them.",
            "wrong": "Incorrect. This is a scam pattern. Fake support often creates urgency and asks for secrets, payments, or remote access.",
        },
        "fake_airdrop": {
            "correct": "Correct. Free-token claims can hide malicious approvals. Scan the source before connecting a wallet.",
            "wrong": "Incorrect. Fake airdrops often pressure you to sign approvals or reveal secrets. Verify before interacting.",
        },
        "market_panic": {
            "correct": "Correct. Waiting for confirmation protects you from emotional selling and rumor-driven decisions.",
            "wrong": "Incorrect. Panic decisions usually reduce discipline. Slow down, verify the source, and size risk carefully.",
        },
        "whale_movement": {
            "correct": "Correct. Whale transfers are context signals, not guaranteed outcomes. Observation and small size protect risk.",
            "wrong": "Incorrect. A whale alert does not guarantee direction. Treat it as context, not a command to overexpose.",
        },
        "breakout_trap": {
            "correct": "Correct. Weak-volume breakouts need confirmation before aggressive fake positioning.",
            "wrong": "Incorrect. FOMO entries after weak confirmation can punish discipline. Wait for volume or reduce size.",
        },
        "volatility_spike": {
            "correct": "Correct. Reducing size during volatility protects emotional control and keeps you in the game.",
            "wrong": "Incorrect. Volatility is not a reason to force trades. Protect capital, reduce size, or observe.",
        },
    }
    explanation_pack = explanations.get(key, {})
    explanation = explanation_pack.get("correct" if correct else "wrong") or (
        "Correct. You chose the most disciplined action." if correct else "Incorrect. This answer increases risk. Slow down and verify before acting."
    )
    return {
        "correct": correct,
        "selected": answer,
        "correct_answer": correct_answer,
        "score": score,
        "xp": xp if correct else max(5, xp // 3),
        "explanation": explanation,
        "feedback": explanation,
        "verdict": "Correct" if correct else "Incorrect",
    }
