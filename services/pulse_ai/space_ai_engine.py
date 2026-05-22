"""Autonomous educational post generation for Pulse Spaces.

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
    tags = [context["space_slug"], context["category"], topic, post_type]
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


def _body_for_type(context, topic, hook, seed):
    post_type = context["post_type"]
    space = context["space_name"]
    region = context["region"]
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
    return f"""Hot take for {space}:

The community that wins is not the loudest one.
It is the one that teaches members how to think under pressure.

That matters for {topic}, because attention can make weak ideas look strong.

Disagree? Good. What is the strongest counterexample?"""


def generate_space_post(space, post_type=None, memory=None, schedule_slot="morning", attempt=0):
    post_type = post_type or DEFAULT_FORMATS[attempt % len(DEFAULT_FORMATS)]
    context = build_prompt_context(space, post_type, memory=memory, schedule_slot=schedule_slot)
    seed = _seed(context["space_slug"], post_type, schedule_slot, memory=memory) + attempt
    topics = [topic for topic in context.get("topics", []) if topic not in set(context.get("recent_topics", [])[:4])] or context.get("topics", [])
    topic = _choice(topics, seed, attempt) or context["category"]
    hooks = [hook for hook in context.get("hooks", []) if hook not in set(context.get("avoid_phrases", [])[:4])] or context.get("hooks", [])
    hook = _choice(hooks, seed, attempt) or f"{context['space_name']} intelligence drop:"
    body = _body_for_type(context, topic, hook, seed)
    tags = _tags(context, topic, post_type)
    quality_ok, quality = passes_quality(body, tags=tags, minimum=MIN_QUALITY_SCORE)
    scores = compute_post_scores(post_type, body, trust_score=space.get("trust_score"), activity_score=space.get("energy_score"))
    metadata = {
        "engine": "pulse_space_ai",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "schedule_slot": schedule_slot,
        "topic": topic,
        "hook": hook,
        "quality": quality,
        "scores": scores,
        "visual": _visual_metadata(context, post_type, topic),
    }
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

