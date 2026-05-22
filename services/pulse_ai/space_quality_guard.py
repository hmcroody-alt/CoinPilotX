"""Quality and safety guardrails for autonomous Pulse Space posts."""

import re

MIN_QUALITY_SCORE = 85

BLOCKED_PATTERNS = [
    r"\bguaranteed\s+(profit|profits|returns|wins)\b",
    r"\b100%\s+(safe|sure|guaranteed)\b",
    r"\bsure\s+bet\b",
    r"\bbetting\s+(pick|signal|lock)\b",
    r"\bfinancial\s+advice\b",
    r"\blegal\s+advice\b",
    r"\bmedical\s+advice\b",
    r"\bseed\s+phrase\b.*\bshare\b",
    r"\bexploit\s+this\b",
]

GENERIC_PHRASES = [
    "in today's fast-paced world",
    "it is important to note",
    "this article will discuss",
    "unlock your potential",
    "game changer",
    "revolutionize the industry",
]


def _count_mobile_lines(text):
    return len([line for line in str(text or "").splitlines() if line.strip()])


def safety_flags(text):
    text = str(text or "")
    lowered = text.lower()
    flags = []
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, lowered):
            flags.append(pattern)
    return flags


def score_post(text, tags=None):
    text = str(text or "").strip()
    lowered = text.lower()
    tags = tags or []
    score = 100
    reasons = []
    if len(text) < 180:
        score -= 22
        reasons.append("too_short")
    if len(text) > 1800:
        score -= 12
        reasons.append("too_long")
    if _count_mobile_lines(text) < 4:
        score -= 12
        reasons.append("not_mobile_readable")
    if "?" not in text:
        score -= 6
        reasons.append("missing_discussion_hook")
    if not tags:
        score -= 5
        reasons.append("missing_tags")
    if len(set(re.findall(r"[a-zA-Z]{4,}", lowered))) < 28:
        score -= 8
        reasons.append("low_vocabulary_density")
    for phrase in GENERIC_PHRASES:
        if phrase in lowered:
            score -= 18
            reasons.append("generic_phrase")
    flags = safety_flags(text)
    if flags:
        score = min(score, 40)
        reasons.extend(["safety_flag"] * len(flags))
    return {"score": max(0, min(100, score)), "reasons": reasons, "blocked": bool(flags), "flags": flags}


def passes_quality(text, tags=None, minimum=MIN_QUALITY_SCORE):
    result = score_post(text, tags=tags)
    return result["score"] >= minimum and not result["blocked"], result

