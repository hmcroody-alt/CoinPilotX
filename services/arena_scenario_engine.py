"""Continuous scenario generation for CoinPilotXAI Arena."""

from __future__ import annotations

import hashlib
from datetime import datetime


OPTION_IDS = ["A", "B", "C", "D"]


SCENARIOS = [
    ("market_panic", "BTC drops fast while social feeds amplify fear.", ["Wait for confirmation", "Full-size panic sell", "Scan news source", "Chase rebound"]),
    ("fake_support", "A fake support account says your wallet must be validated.", ["Never share secrets", "Send seed phrase", "Install remote access", "Pay unlock fee"]),
    ("whale_movement", "A whale transfer appears during low liquidity.", ["Observe and size small", "Assume guaranteed pump", "Go max leverage", "Ignore risk"]),
    ("breakout_trap", "Price breaks resistance but volume is weak.", ["Wait for confirmation", "FOMO buy", "Ask AI for context", "Use simulated small size"]),
    ("fake_airdrop", "A free token claim asks you to connect wallet now.", ["Scan first", "Sign approval", "Share private key", "Ignore official sources"]),
    ("volatility_spike", "Volatility expands near a major news event.", ["Reduce size", "Revenge trade", "Observe", "Force entry"]),
]


def normalize_scenario(scenario):
    scenario = dict(scenario or {})
    raw_options = scenario.get("options") or []
    normalized = []
    for index, option in enumerate(raw_options[:4]):
        option_id = OPTION_IDS[index] if index < len(OPTION_IDS) else str(index + 1)
        if isinstance(option, dict):
            text = str(option.get("text") or option.get("label") or option.get("answer") or "").strip()
            option_id = str(option.get("id") or option.get("option_id") or option_id).strip() or option_id
        else:
            text = str(option or "").strip()
        if text:
            normalized.append({"id": option_id, "text": text})
    if not normalized:
        normalized = [{"id": "A", "text": "Pause and verify"}]
    correct_option_id = str(scenario.get("correct_option_id") or "").strip()
    correct_answer = str(
        scenario.get("correct_answer")
        or scenario.get("answer")
        or ((scenario.get("best_actions") or [""])[0])
        or ""
    ).strip()
    correct_index = scenario.get("correct_index", scenario.get("correct_choice"))
    if not correct_option_id and correct_index is not None:
        try:
            correct_option_id = normalized[int(correct_index)]["id"]
        except Exception:
            correct_option_id = ""
    if not correct_option_id and correct_answer:
        for option in normalized:
            if option["text"].strip().lower() == correct_answer.lower():
                correct_option_id = option["id"]
                break
    if not correct_option_id:
        correct_option_id = normalized[0]["id"]
    correct = next((option for option in normalized if option["id"] == correct_option_id), normalized[0])
    scenario["options"] = normalized
    scenario["correct_option_id"] = correct["id"]
    scenario["correct_answer"] = correct["text"]
    scenario["best_actions"] = [correct["text"]]
    return scenario


def generate_scenario(mode="quick_battle", difficulty=1, player_profile=None, world_state=None):
    seed_text = f"{mode}:{difficulty}:{datetime.utcnow().strftime('%Y-%m-%d:%H:%M:%S')}:{(player_profile or {}).get('user_id','')}"
    index = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16) % len(SCENARIOS)
    key, prompt, options = SCENARIOS[index]
    if world_state and world_state.get("title"):
        prompt = f"{prompt} World state: {world_state.get('title')}."
    round_id = hashlib.sha256(f"{seed_text}:{key}".encode("utf-8")).hexdigest()[:16]
    return normalize_scenario({
        "round_id": round_id,
        "scenario_key": key,
        "mode": mode,
        "difficulty": max(1, int(difficulty or 1)),
        "question": prompt,
        "prompt": prompt,
        "options": [{"id": OPTION_IDS[index], "text": option} for index, option in enumerate(options)],
        "correct_option_id": "A",
        "correct_answer": options[0],
        "explanation": "",
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    })


def score_answer(scenario, answer=None, selected_option_id=None):
    scenario = normalize_scenario(scenario)
    selected_option_id = str(selected_option_id or "").strip()
    answer = str(answer or "").strip()
    if not selected_option_id and answer:
        for option in scenario.get("options") or []:
            if answer == option.get("id") or answer.lower() == str(option.get("text") or "").lower():
                selected_option_id = option.get("id")
                break
    selected = next((option for option in scenario.get("options") or [] if option.get("id") == selected_option_id), None)
    if not selected and answer:
        selected = {"id": selected_option_id or "", "text": answer}
    if not selected:
        selected = {"id": "", "text": ""}
    correct_option_id = scenario.get("correct_option_id") or "A"
    correct_answer = scenario.get("correct_answer") or ""
    correct = bool(selected.get("id")) and selected.get("id") == correct_option_id
    selected_text = selected.get("text") or ""
    partial = any(word in selected_text.lower() for word in ["scan", "wait", "observe", "small", "reduce"])
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
        "selected": selected_text,
        "selected_answer": selected_text,
        "selected_option_id": selected.get("id") or "",
        "correct_option_id": correct_option_id,
        "correct_answer": correct_answer,
        "score": score,
        "xp": xp if correct else 0,
        "explanation": explanation,
        "feedback": explanation,
        "verdict": "Correct" if correct else "Incorrect",
    }
