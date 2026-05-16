"""Persistent world-state engine for CoinPilotXAI Arena.

Everything here is educational, simulated, and non-financial. The world state
is used to make Arena feel alive without creating gambling mechanics.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta


WORLD_STATES = [
    ("bull_market_expansion", "Bull Market Expansion", "Momentum is rising, but discipline still decides long-term skill."),
    ("extreme_fear", "Extreme Fear", "Global fear levels are elevated. Calm risk control earns extra respect."),
    ("meme_coin_madness", "Meme Coin Madness", "Hype is everywhere. FOMO resistance missions are amplified."),
    ("institutional_accumulation", "Institutional Accumulation", "Whale and liquidity reads matter more than social noise."),
    ("exchange_panic", "Exchange Panic", "Counterparty and custody education missions are active."),
    ("regulatory_shock", "Regulatory Shock", "News literacy and uncertainty management are the focus."),
    ("ai_euphoria", "AI Euphoria", "AI narratives are heating up. Verify before chasing."),
    ("liquidity_crisis", "Liquidity Crisis", "Volatility survival and position sizing are the core skills."),
]


GOVERNORS = [
    ("oracle", "The Oracle", "Narrates uncertainty and probability."),
    ("guardian", "The Guardian", "Protects discipline and scam defense."),
    ("strategist", "The Strategist", "Explains portfolio and team tactics."),
    ("watcher", "The Watcher", "Tracks whales, sentiment, and world events."),
    ("analyst", "The Analyst", "Turns live data into clean lessons."),
]


def current_world_state(seed_text: str | None = None):
    day = datetime.utcnow().strftime("%Y-%m-%d")
    seed = int(hashlib.sha256((seed_text or day).encode("utf-8")).hexdigest(), 16)
    key, title, description = WORLD_STATES[seed % len(WORLD_STATES)]
    intensity = 45 + (seed % 46)
    governor_key, governor_name, governor_focus = GOVERNORS[seed % len(GOVERNORS)]
    narrative = (
        f"{title} is shaping Arena decisions today. {description} "
        f"{governor_name} is watching for discipline, scam defense, and emotional control."
    )
    return {
        "state_key": key,
        "title": title,
        "description": description,
        "intensity": intensity,
        "market_climate": "volatile" if intensity >= 72 else "active" if intensity >= 55 else "calm",
        "social_mood": "competitive" if intensity >= 65 else "focused",
        "ai_global_sentiment": "cautious" if "fear" in key or "panic" in key or "crisis" in key else "constructive",
        "governor": {"key": governor_key, "name": governor_name, "focus": governor_focus},
        "narrative": narrative,
        "xp_modifier": 1.25 if intensity >= 72 else 1.1 if intensity >= 55 else 1.0,
        "next_shift_at": (datetime.utcnow() + timedelta(days=1)).isoformat(timespec="seconds"),
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def player_archetype(profile: dict | None):
    profile = profile or {}
    discipline = int(profile.get("discipline_score") or 50)
    scam = int(profile.get("scam_defense_score") or 50)
    strategy = int(profile.get("strategy_score") or 50)
    iq = int(profile.get("arena_iq") or 50)
    if discipline >= 80 and scam >= 70:
        return "The Risk Guardian"
    if strategy >= 80:
        return "The Strategist"
    if iq >= 85:
        return "The Oracle"
    if discipline >= 70:
        return "The Survivor"
    return "The Momentum Hunter"


def player_story(profile: dict | None, badges=None):
    profile = profile or {}
    badges = badges or []
    archetype = player_archetype(profile)
    rank = profile.get("rank") or "Rookie"
    streak = int(profile.get("streak_count") or 0)
    xp = int(profile.get("xp") or 0)
    badge_names = ", ".join([b.get("name", "") for b in badges[:3] if b.get("name")])
    story = f"{profile.get('username') or 'This pilot'} is evolving as {archetype}, currently ranked {rank} with {xp} XP."
    if streak:
        story += f" They have survived {streak} Arena training streak checks."
    if badge_names:
        story += f" Recent identity markers: {badge_names}."
    return {"archetype": archetype, "story": story}
