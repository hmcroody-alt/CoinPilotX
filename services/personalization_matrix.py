"""Personalization matrix foundation."""

from __future__ import annotations


def user_matrix(user=None, interactions=None) -> dict:
    user = user or {}
    interactions = interactions or []
    topics = {}
    for item in interactions:
        topic = str(item.get("topic") or item.get("post_type") or "general").lower()
        topics[topic] = topics.get(topic, 0) + float(item.get("weight") or 1)
    sorted_topics = sorted(topics.items(), key=lambda row: row[1], reverse=True)
    return {
        "user_id": user.get("user_id") or user.get("id"),
        "learning_goals": user.get("learning_goals") or [],
        "top_topics": [topic for topic, _ in sorted_topics[:8]],
        "feed_mode": user.get("feed_mode") or "balanced",
    }


def recommend_spaces(matrix=None, spaces=None) -> list[dict]:
    matrix = matrix or {}
    preferred = set(matrix.get("top_topics") or [])
    ranked = []
    for space in spaces or []:
        tags = {str(tag).lower() for tag in space.get("tags", [])}
        item = dict(space)
        item["personalization_score"] = len(preferred & tags) * 20 + int(space.get("trust_score") or 0) * 0.2
        ranked.append(item)
    return sorted(ranked, key=lambda row: row.get("personalization_score", 0), reverse=True)
