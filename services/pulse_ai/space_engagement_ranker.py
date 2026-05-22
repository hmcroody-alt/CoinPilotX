"""Engagement scoring for Pulse Space AI content."""


def compute_post_scores(post_type, text, trust_score=85, activity_score=75):
    text = str(text or "")
    hook_strength = 12 if text.strip().splitlines() and len(text.strip().splitlines()[0]) <= 110 else 6
    question_bonus = 10 if "?" in text else 0
    readability = 12 if max((len(line) for line in text.splitlines() if line.strip()), default=0) <= 160 else 6
    type_bonus = {
        "community_question": 14,
        "scam_alert": 13,
        "builder_lesson": 12,
        "thread_post": 11,
        "hot_take": 10,
        "trend_explainer": 9,
        "quick_insight": 8,
        "ai_summary": 8,
    }.get(post_type, 8)
    energy = min(100, int(activity_score or 75) + hook_strength + question_bonus + type_bonus - 18)
    trust = min(100, max(55, int(trust_score or 85) + (8 if post_type in {"scam_alert", "builder_lesson", "thread_post"} else 2)))
    sentiment = "protective" if post_type == "scam_alert" else "curious" if post_type == "community_question" else "educational"
    topic_score = min(100, type_bonus * 5 + readability + question_bonus)
    return {
        "topic_score": topic_score,
        "trust_score": trust,
        "energy_score": energy,
        "sentiment_score": 74 if sentiment == "educational" else 80,
        "sentiment": sentiment,
        "predicted_engagement_score": min(100, int((energy * 0.46) + (topic_score * 0.34) + (trust * 0.2))),
    }


def summarize_engagement(row=None):
    row = row or {}
    comments = int(row.get("comments") or 0)
    likes = int(row.get("likes") or 0)
    shares = int(row.get("shares") or 0)
    saves = int(row.get("saves") or 0)
    read_time = float(row.get("read_time_seconds") or 0)
    return {
        "score": min(100, comments * 8 + likes * 3 + shares * 10 + saves * 7 + int(read_time / 8)),
        "comments": comments,
        "likes": likes,
        "shares": shares,
        "saves": saves,
        "read_time_seconds": read_time,
    }

