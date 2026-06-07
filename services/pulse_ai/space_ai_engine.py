"""Autonomous educational post generation for PulseSoc Spaces.

This engine is intentionally deterministic and safety-first. It creates
category-aware AI-style community posts without external network calls, so the
background scheduler can run reliably inside the existing worker process.
"""

from datetime import datetime
import hashlib
import json

from .space_engagement_ranker import compute_post_scores
from .space_prompt_builder import DEFAULT_FORMATS, build_prompt_context
from .space_quality_guard import MIN_QUALITY_SCORE, passes_quality

LIVE_SPACE_SLUGS = {
    "haiti-crypto-community",
    "ethical-hackers",
    "ai-builders",
    "creator-economy",
    "bitcoin-strategy",
    "scam-hunters",
    "prompt-engineering",
    "sports-edge",
}

SPACE_CONTENT_STRATEGIES = {
    "ethical-hackers": {
        "post_types": ["exploit_breakdown", "defense_challenge", "red_team_debate", "phishing_analysis", "osint_prompt"],
        "tone": "tactical and security-focused",
        "vocabulary": ["threat model", "payload", "privilege escalation", "mitigation", "detection", "IOC", "sandbox", "phishing kit"],
        "question": "What detection signal would you add before this becomes an incident?",
    },
    "bitcoin-strategy": {
        "post_types": ["market_thesis", "self_custody_lesson", "fee_strategy", "cycle_analysis", "liquidity_question"],
        "tone": "analytical and custody-first",
        "vocabulary": ["halving", "liquidity", "custody", "UTXO", "fees", "cold wallet", "macro", "volatility", "DCA"],
        "question": "What one habit would make this strategy safer during volatility?",
    },
    "ai-builders": {
        "post_types": ["prompt_teardown", "agent_workflow", "model_comparison", "automation_idea", "product_critique"],
        "tone": "builder-focused and experimental",
        "vocabulary": ["workflow", "context window", "retrieval", "evals", "agentic loop", "latency", "API cost"],
        "question": "What would you measure before shipping this AI workflow?",
    },
    "marketplace": {
        "post_types": ["buyer_trust_tip", "seller_optimization", "scam_warning", "pricing_strategy"],
        "tone": "practical commerce",
        "vocabulary": ["listing quality", "disputes", "escrow", "reviews", "fulfillment", "buyer protection"],
        "question": "What trust signal would make you buy or walk away?",
    },
    "reels-music": {
        "post_types": ["creator_prompt", "hook_idea", "audio_trend", "edit_breakdown"],
        "tone": "creative and energetic",
        "vocabulary": ["hook", "drop", "remix", "retention", "caption", "trend", "loop"],
        "question": "What first three seconds would keep people watching?",
    },
    "cybersecurity": {
        "post_types": ["threat_report", "malware_analysis", "blue_team_prompt", "defense_checklist"],
        "tone": "defensive and technical",
        "vocabulary": ["SIEM", "endpoint", "persistence", "lateral movement", "phishing", "malware", "detection rule"],
        "question": "Which control would reduce the blast radius first?",
    },
}


def _strategy_for(context):
    slug = context.get("space_slug") or ""
    category = str(context.get("category") or "").lower()
    if slug in SPACE_CONTENT_STRATEGIES:
        return SPACE_CONTENT_STRATEGIES[slug]
    if "market" in slug:
        return SPACE_CONTENT_STRATEGIES["marketplace"]
    if "music" in slug or "reel" in slug:
        return SPACE_CONTENT_STRATEGIES["reels-music"]
    if "cyber" in category or "security" in category:
        return SPACE_CONTENT_STRATEGIES["cybersecurity"]
    if category == "ai":
        return SPACE_CONTENT_STRATEGIES["ai-builders"]
    if category == "crypto":
        return SPACE_CONTENT_STRATEGIES["bitcoin-strategy"]
    return {
        "post_types": ["community_prompt", "learning_challenge", "trust_check", "resource_request"],
        "tone": "practical and community-first",
        "vocabulary": ["community signal", "learning loop", "trust check", "examples", "next step", "member question"],
        "question": "What example would help a new member understand this faster?",
    }


def _structure_name(seed, attempt):
    return _choice(["checklist", "scenario", "debate", "mini_case", "decision_tree", "challenge"], seed, attempt)


def live_space_slugs():
    return set(LIVE_SPACE_SLUGS)


def _choice(items, seed, offset=0):
    items = list(items or [])
    if not items:
        return ""
    return items[(seed + offset) % len(items)]


def _seed(space_slug, post_type, schedule_slot, memory=None):
    raw = "|".join([
        space_slug or "",
        post_type or "",
        schedule_slot or "",
        datetime.utcnow().strftime("%Y-%m-%d"),
        ",".join((memory or {}).get("recent_topics") or [])[:120],
    ])
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10], 16)


def _tags(context, topic, post_type):
    strategy = _strategy_for(context)
    strategy_tags = [str(item).lower().replace(" ", "-") for item in strategy.get("vocabulary", [])[:3]]
    tags = [context["space_slug"], context["category"], topic, post_type, *_choice([strategy_tags, list(reversed(strategy_tags))], len(topic), 0)]
    for item in context.get("topics", [])[:4]:
        tags.append(item)
    clean = []
    for tag in tags:
        value = str(tag or "").lower().replace(" ", "-").replace("/", "-")[:36].strip("-")
        if value and value not in clean:
            clean.append(value)
    return clean[:10]


def _visual_metadata(context, post_type, topic):
    if post_type not in {"scam_alert", "trend_explainer", "ai_summary", "builder_lesson"}:
        return {}
    return {
        "visual_type": "educational_card",
        "prompt": f"{context['space_name']} educational visual about {topic}, premium dark social card, clear icons, no financial promises",
        "style": "pulse-intelligence-card",
    }


def _body_for_type(context, topic, hook, seed, attempt=0):
    post_type = context["post_type"]
    space = context["space_name"]
    region = context["region"]
    strategy = _strategy_for(context)
    vocabulary = strategy.get("vocabulary", [])
    vocab_a = _choice(vocabulary, seed, 0) or topic
    vocab_b = _choice(vocabulary, seed, 2) or "verification"
    vocab_c = _choice(vocabulary, seed, 4) or "risk"
    structure = _structure_name(seed, attempt)
    desired_type = _choice(strategy.get("post_types", []), seed, attempt) or post_type
    question = strategy.get("question") or "What would you add?"
    if structure == "checklist":
        return f"""{hook}

{space} prompt type: {desired_type.replace('_', ' ')}.

Use this quick checklist around {topic}:
- Name the real risk before naming the tool.
- Check the {vocab_a} signal instead of reacting to noise.
- Ask whether {vocab_b} changes the decision.
- Turn the lesson into one repeatable habit.

Tone for this space: {strategy['tone']}.

{question}"""
    if structure == "scenario":
        return f"""{hook}

Scenario for {space}:

A member sees a claim about {topic}. The claim sounds useful, but the missing piece is {vocab_a}.

Best next move:
1. Pause the reaction.
2. Find the source.
3. Compare the claim against {vocab_b}.
4. Share the safest interpretation, not the loudest one.

Where would this scenario break in real life?"""
    if structure == "debate":
        return f"""{hook}

Debate frame: {desired_type.replace('_', ' ')}.

Side A says {topic} is mostly about speed.
Side B says it is mostly about {vocab_a}, {vocab_b}, and disciplined timing.

The useful answer may be boring: document the evidence, name the assumption, and make the next step smaller.

Which side is stronger for {region} members, and why?"""
    if structure == "mini_case":
        return f"""{hook}

Mini case for {space}:

One small mistake around {topic} can create three downstream problems:
- weak {vocab_a}
- unclear {vocab_b}
- poor {vocab_c}

The fix is not more hype.
The fix is a cleaner process members can repeat.

What process would you teach first?"""
    if structure == "decision_tree":
        return f"""{hook}

Decision tree:

If {topic} increases urgency, verify {vocab_a}.
If the source is unclear, ask for {vocab_b}.
If the risk touches another member, slow down and document {vocab_c}.

This is how {space} turns attention into community intelligence.

What branch should be added to this decision tree?"""
    if post_type == "quick_insight":
        return f"""{hook}

One useful signal today: watch the behavior, not the noise.

In {space}, the smarter move is to ask:
- who benefits if people rush?
- what proof is visible?
- what risk is being ignored?

Small habit: pause for 30 seconds before reacting to any urgent claim.

What is one signal you check before trusting a post?"""
    if post_type == "thread_post":
        return f"""{hook}

Here is a practical breakdown for {space}:

1. Separate evidence from excitement.
2. Check whether the source has something to gain.
3. Look for missing context, especially around {topic}.
4. Save examples that teach the community what to avoid next time.

The goal is not to be paranoid.
The goal is to become harder to manipulate.

What would you add as rule number 5?"""
    if post_type == "scam_alert":
        return f"""Scam pattern to watch in {space}:

The message feels helpful, but it pushes urgency before verification.

Common signs:
- pressure to act privately
- screenshots with no source
- “support” accounts asking for wallet details
- emotional language around missed opportunity

Safe response:
slow down, verify publicly, and never share private access.

Seen a similar pattern lately? Drop the warning signs, not personal data."""
    if post_type == "trend_explainer":
        return f"""{topic.title()} is getting attention, but the useful question is simpler:

What changed, and who can verify it?

For {region} builders and learners, a trend only matters when it changes behavior:
- safer decisions
- clearer tools
- better timing
- stronger community literacy

If the trend cannot be explained in plain language, it probably needs more work.

What part of this trend still feels unclear?"""
    if post_type == "community_question":
        return f"""Quick community pulse for {space}:

What is one lesson about {topic} you wish someone taught you earlier?

Keep it practical.
One warning, one habit, or one tool is enough.

Best replies are the ones another member can use today."""
    if post_type == "builder_lesson":
        return f"""Builder lesson for {space}:

Make the safe path the easiest path.

If your workflow depends on people remembering every risk, the workflow is weak.
If it nudges people toward verification, backups, and clear decisions, it becomes community infrastructure.

Try this today:
write one checklist for {topic} that a beginner can follow in under 60 seconds.

What would your first checklist item be?"""
    if post_type == "ai_summary":
        return f"""AI summary for {space}:

The strongest conversations right now should focus on three things:

- clarity: explain {topic} without hype
- safety: call out risky shortcuts early
- execution: turn discussion into one useful habit

Community intelligence compounds when members bring examples, not noise.

What should this space track more closely this week?"""
    return f"""{hook}

Challenge for {space}:

Create one example around {topic} that teaches {vocab_a}, tests {vocab_b}, and leaves members with a safer next action.

Constraint:
keep it specific enough that a beginner can use it today.

What is your example?"""


def diversity_score(candidate, recent_posts=None):
    body = str(candidate.get("body") or "")
    tags = set(candidate.get("tags") or [])
    title = str(candidate.get("title") or "")
    structure = (candidate.get("metadata") or {}).get("structure") or ""
    worst_body = 0.0
    worst_tags = 0.0
    worst_title = 0.0
    worst_structure = 0.0
    for post in recent_posts or []:
        other_body = str(post.get("body") or "")
        other_tags = set(post.get("tags") or [])
        if other_body:
            body_words = set(body.lower().split())
            other_words = set(other_body.lower().split())
            worst_body = max(worst_body, len(body_words & other_words) / max(1, len(body_words | other_words)))
        if other_tags:
            worst_tags = max(worst_tags, len(tags & other_tags) / max(1, len(tags | other_tags)))
        if title and post.get("title"):
            worst_title = max(worst_title, 1.0 if title.lower() == str(post.get("title")).lower() else 0.0)
        other_structure = (post.get("metadata") or {}).get("structure") or ""
        if structure and other_structure:
            worst_structure = max(worst_structure, 1.0 if structure == other_structure else 0.0)
    return {
        "title_similarity": round(worst_title, 3),
        "body_similarity": round(worst_body, 3),
        "hashtag_overlap": round(worst_tags, 3),
        "structure_similarity": round(worst_structure, 3),
        "topic_relevance": 1.0,
        "passes": worst_body <= 0.65 and worst_structure < 1.0,
    }


def generate_space_post(space, post_type=None, memory=None, schedule_slot="morning", attempt=0):
    post_type = post_type or DEFAULT_FORMATS[attempt % len(DEFAULT_FORMATS)]
    context = build_prompt_context(space, post_type, memory=memory, schedule_slot=schedule_slot)
    seed = _seed(context["space_slug"], post_type, schedule_slot, memory=memory) + attempt
    topics = [topic for topic in context.get("topics", []) if topic not in set(context.get("recent_topics", [])[:4])] or context.get("topics", [])
    topic = _choice(topics, seed, attempt) or context["category"]
    hooks = [hook for hook in context.get("hooks", []) if hook not in set(context.get("avoid_phrases", [])[:4])] or context.get("hooks", [])
    hook = _choice(hooks, seed, attempt) or f"{context['space_name']} intelligence drop:"
    structure = _structure_name(seed, attempt)
    body = _body_for_type(context, topic, hook, seed, attempt)
    tags = _tags(context, topic, post_type)
    quality_ok, quality = passes_quality(body, tags=tags, minimum=MIN_QUALITY_SCORE)
    scores = compute_post_scores(post_type, body, trust_score=space.get("trust_score"), activity_score=space.get("energy_score"))
    metadata = {
        "engine": "pulse_space_ai",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "schedule_slot": schedule_slot,
        "topic": topic,
        "hook": hook,
        "structure": structure,
        "quality": quality,
        "scores": scores,
        "visual": _visual_metadata(context, post_type, topic),
    }
    diversity = diversity_score({"title": f"{context['space_name']} · {post_type.replace('_', ' ').title()}", "body": body, "tags": tags, "metadata": metadata}, (memory or {}).get("recent_posts") or [])
    metadata["diversity"] = diversity
    return {
        "ok": quality_ok,
        "space_slug": context["space_slug"],
        "title": f"{context['space_name']} · {post_type.replace('_', ' ').title()}",
        "body": body,
        "post_type": post_type,
        "tags": tags,
        "topic": topic,
        "hook": hook,
        "quality_score": int(quality["score"]),
        "topic_score": int(scores["topic_score"]),
        "trust_score": int(scores["trust_score"]),
        "energy_score": int(scores["energy_score"]),
        "sentiment_score": int(scores["sentiment_score"]),
        "metadata": metadata,
        "metadata_json": json.dumps(metadata, ensure_ascii=True),
    }
