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
    seed_text = f"{mode}:{difficulty}:{datetime.utcnow().strftime('%Y-%m-%d:%H')}:{(player_profile or {}).get('user_id','')}"
    index = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16) % len(SCENARIOS)
    key, prompt, options = SCENARIOS[index]
    if world_state and world_state.get("title"):
        prompt = f"{prompt} World state: {world_state.get('title')}."
    return {
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
    best = set((scenario or {}).get("best_actions") or [])
    score = 90 if answer in best else 55 if any(word in answer.lower() for word in ["scan", "wait", "observe", "small", "reduce"]) else 25
    xp = 35 + max(0, score - 40)
    return {
        "score": score,
        "xp": xp,
        "feedback": "Disciplined answer. You protected decision quality." if score >= 80 else "Training note: slow down, verify, and avoid emotional entries.",
    }
