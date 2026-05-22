"""Neural discovery foundation for creators, lessons, spaces, and lives."""

from __future__ import annotations


def discovery_score(user_matrix=None, item=None) -> int:
    user_matrix = user_matrix or {}
    item = item or {}
    topics = set(user_matrix.get("top_topics") or [])
    item_topics = {str(t).lower() for t in item.get("tags", [])}
    trust = int(item.get("trust_score") or 0)
    novelty = int(item.get("novelty_score") or 30)
    return max(0, min(100, len(topics & item_topics) * 18 + trust * 0.35 + novelty * 0.25))
