"""Trust-aware Pulse search foundation."""

from __future__ import annotations

import re
from collections import Counter


def tokenize(query: str) -> list[str]:
    return [part.lower() for part in re.findall(r"[a-zA-Z0-9_#]+", query or "") if len(part) > 1]


def score_result(query: str, item=None) -> dict:
    item = item or {}
    tokens = tokenize(query)
    haystack = " ".join(str(item.get(k) or "") for k in ("title", "body", "display_name", "tags", "category")).lower()
    keyword_score = sum(8 for token in tokens if token in haystack)
    trust = float(item.get("trust_score") or item.get("author_trust_score") or 0)
    freshness = float(item.get("freshness_score") or 0)
    score = keyword_score + trust * 0.18 + freshness * 0.1
    out = dict(item)
    out["search_score"] = int(score)
    return out


def search(query: str, items=None, limit: int = 20) -> dict:
    ranked = sorted((score_result(query, item) for item in items or []), key=lambda row: row.get("search_score", 0), reverse=True)
    topics = Counter(token for item in ranked for token in tokenize(" ".join(map(str, item.get("tags", [])))))
    return {"query": query, "results": ranked[:limit], "topic_clusters": topics.most_common(8)}
