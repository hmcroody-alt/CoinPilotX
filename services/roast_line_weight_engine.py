"""Weighted scoring for safe, competitive Roast Battle lines."""

import re


IMPACT_TIERS = [
    (92, "Nuclear Comeback", 95000, -80000, "triumphant", "arena eruption"),
    (82, "Crowd Breaker", 68000, -56000, "dominant", "crowd breaker"),
    (70, "Heavy Hit", 42000, -35000, "confident", "eruption"),
    (55, "Clean Hit", 24000, -16000, "focused", "cheer"),
    (38, "Light Tap", 8000, -5000, "steady", "murmur"),
    (25, "Weak Shot", -10000, 5000, "shaken", "crowd sympathy"),
    (0, "Missed Swing", -20000, 8000, "rattled", "silence"),
]

COMEBACK_WORDS = {"again", "still", "comeback", "recover", "folded", "pressure", "answer", "return"}
LOW_EFFORT = {"lol", "trash", "bad", "weak", "noob", "boring"}


def _words(text):
    return re.findall(r"[a-zA-Z0-9']+", str(text or "").lower())


def score_line(text, moderation=None, reaction_count=0, repeated=False):
    moderation = moderation or {"ok": True}
    if not moderation.get("ok"):
        return {
            "weight": 0,
            "impact_label": "Unsafe Blocked",
            "balance_delta": -75000,
            "target_balance_delta": 0,
            "emotion": "warned",
            "crowd_reaction": "blocked",
            "safe": False,
            "moderation_reason": moderation.get("message") or "Unsafe content blocked.",
        }

    tokens = _words(text)
    token_count = len(tokens)
    unique_ratio = len(set(tokens)) / max(1, token_count)
    punctuation = min(8, str(text or "").count("!") * 2 + str(text or "").count("?"))
    comeback = 10 if COMEBACK_WORDS.intersection(tokens) else 0
    low_effort = 35 if token_count < 4 or any(token in LOW_EFFORT for token in tokens) else 0
    repetition_penalty = 14 if repeated else 0
    reaction_boost = min(8, int(reaction_count or 0))

    wit = min(28, token_count * 2 + int(unique_ratio * 10))
    originality = int(unique_ratio * 22)
    clean_intensity = min(18, punctuation + comeback + (6 if token_count >= 8 else 0))
    weight = max(0, min(100, 28 + wit + originality + clean_intensity + reaction_boost - low_effort - repetition_penalty))

    for threshold, label, sender_delta, target_delta, emotion, crowd in IMPACT_TIERS:
        if weight >= threshold:
            return {
                "weight": weight,
                "impact_label": label,
                "balance_delta": sender_delta,
                "target_balance_delta": target_delta,
                "emotion": emotion,
                "crowd_reaction": crowd,
                "safe": True,
                "moderation_reason": None,
            }

    return {
        "weight": weight,
        "impact_label": "Missed Swing",
        "balance_delta": -20000,
        "target_balance_delta": 8000,
        "emotion": "rattled",
        "crowd_reaction": "silence",
        "safe": True,
        "moderation_reason": None,
    }
