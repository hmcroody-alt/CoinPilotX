"""Prompt/personality builder for autonomous Pulse Spaces."""

DEFAULT_FORMATS = [
    "quick_insight",
    "thread_post",
    "scam_alert",
    "trend_explainer",
    "community_question",
    "builder_lesson",
    "ai_summary",
    "hot_take",
]

SPACE_PERSONALITIES = {
    "haiti-crypto-community": {
        "tone": ["energetic", "educational", "local-first", "opportunity-driven", "anti-scam"],
        "topics": ["remittance safety", "crypto literacy", "mobile money", "local fintech", "scam warnings", "Haitian builders", "DeFi education"],
        "hooks": ["Haiti does not need crypto hype. It needs crypto literacy.", "The safest wallet habit is boring until it saves your money.", "A remittance lesson every builder should know:"],
    },
    "ethical-hackers": {
        "tone": ["technical", "elite", "analytical", "cyber-intelligence"],
        "topics": ["phishing breakdowns", "malware analysis", "OSINT", "threat reports", "OPSEC", "exploit education", "security tools"],
        "hooks": ["A strong security team studies patterns before tools.", "The attack starts before the link is clicked.", "Here is the quiet signal most defenders miss:"],
    },
    "ai-builders": {
        "tone": ["visionary", "startup-focused", "experimental", "innovation-heavy"],
        "topics": ["agent workflows", "AI automation", "GPT systems", "startup execution", "prompts", "creator AI", "AI monetization"],
        "hooks": ["The best AI product is not the smartest model. It is the cleanest workflow.", "Most AI builders fail at handoffs, not prompts.", "A tiny automation lesson with big startup value:"],
    },
    "creator-economy": {
        "tone": ["creator-first", "practical", "trust-building", "business-aware"],
        "topics": ["audience trust", "content systems", "offers", "creator funnels", "brand safety", "community retention"],
        "hooks": ["Creators do not lose audiences overnight. They lose trust one lazy promise at a time.", "A creator business gets stronger when the offer gets clearer.", "The algorithm notices what the audience repeats:"],
    },
    "bitcoin-strategy": {
        "tone": ["calm", "long-term", "custody-first", "risk-aware"],
        "topics": ["self custody", "market cycles", "liquidity education", "fees", "long-term planning", "security habits"],
        "hooks": ["Bitcoin strategy begins before price enters the conversation.", "Most cycle mistakes are emotional, not technical.", "A custody habit worth practicing before you need it:"],
    },
    "scam-hunters": {
        "tone": ["protective", "sharp", "evidence-first", "community-defense"],
        "topics": ["scam patterns", "fake support", "wallet drainers", "impersonation", "proof gathering", "report hygiene"],
        "hooks": ["A scam usually looks urgent before it looks obvious.", "The best scam detector is a calm second look.", "Before you report a scam, capture these signals:"],
    },
    "prompt-engineering": {
        "tone": ["precise", "creative", "systems-minded", "practical"],
        "topics": ["prompt structure", "evaluation loops", "tool workflows", "AI safety", "creator prompts", "task decomposition"],
        "hooks": ["A good prompt is not magic. It is a tiny operating system.", "The output improves when the evaluation is explicit.", "Try this prompt habit before adding another tool:"],
    },
    "sports-edge": {
        "tone": ["analytical", "fan-intelligent", "discipline-first", "no-picks"],
        "topics": ["sports psychology", "team momentum", "film study", "fan debates", "analytics literacy", "risk education"],
        "hooks": ["Sports intelligence is not about picks. It is about reading context.", "A fan who understands variance argues better.", "The scoreboard tells the result. The pattern tells the lesson:"],
    },
}

CATEGORY_TOPICS = {
    "crypto": ["wallet safety", "liquidity", "risk psychology", "protocol education", "custody habits", "scam defense"],
    "cybersecurity": ["phishing", "OPSEC", "OSINT", "threat modeling", "safe disclosure", "incident review"],
    "AI": ["agents", "automation", "prompt systems", "AI products", "model workflows", "creator AI"],
    "creators": ["audience retention", "brand trust", "content systems", "creator offers", "community growth"],
    "sports": ["analytics literacy", "fan discussion", "performance psychology", "team context", "no-pick education"],
    "countries": ["local builders", "digital literacy", "payments", "community safety", "creator economy", "education"],
}


def personality_for_space(space):
    slug = (space or {}).get("slug") or ""
    if slug in SPACE_PERSONALITIES:
        return dict(SPACE_PERSONALITIES[slug])
    category = (space or {}).get("category") or "community"
    topics = list(CATEGORY_TOPICS.get(category, CATEGORY_TOPICS.get(str(category).lower(), ["community learning", "trusted discussion", "creator growth"])))
    tags = list((space or {}).get("tags") or [])
    return {
        "tone": ["educational", "trustworthy", "community-first", "practical"],
        "topics": list(dict.fromkeys(tags + topics)),
        "hooks": [
            f"{(space or {}).get('name', 'This space')} gets stronger when the discussion gets more useful.",
            "A small lesson can save someone from a big mistake.",
            "Here is a practical community question for today:",
        ],
    }


def build_prompt_context(space, post_type, memory=None, schedule_slot="morning"):
    personality = personality_for_space(space)
    return {
        "space_slug": (space or {}).get("slug") or "",
        "space_name": (space or {}).get("name") or "Pulse Space",
        "category": (space or {}).get("category") or "community",
        "region": (space or {}).get("region") or "Global",
        "description": (space or {}).get("description") or "",
        "post_type": post_type if post_type in DEFAULT_FORMATS else "quick_insight",
        "schedule_slot": schedule_slot,
        "tone": personality["tone"],
        "topics": personality["topics"],
        "hooks": personality["hooks"],
        "recent_topics": (memory or {}).get("recent_topics") or [],
        "avoid_phrases": (memory or {}).get("recent_hooks") or [],
    }

