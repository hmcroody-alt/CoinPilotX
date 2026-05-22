"""Safe AI feed personalization primitives."""

from __future__ import annotations


FEED_MODES = ["Explore", "Learn", "Safe Mode", "Fast News", "Deep Research", "Creator Discovery", "Scam Watch", "Elite Feed"]


def user_interest_profile(events=None, explicit_topics=None):
    events = events or []
    topics = list(dict.fromkeys([*(explicit_topics or []), *[item.get("topic") for item in events if item.get("topic")]]))
    return {"topics": [str(topic) for topic in topics if topic][:20], "modes": FEED_MODES}


def diversify_posts(posts=None, max_per_creator=3):
    counts = {}
    result = []
    overflow = []
    for post in posts or []:
        creator = post.get("user_id") or ((post.get("author") or {}).get("user_id"))
        counts[creator] = counts.get(creator, 0) + 1
        if counts[creator] <= max_per_creator:
            result.append(post)
        else:
            overflow.append(post)
    return result + overflow
