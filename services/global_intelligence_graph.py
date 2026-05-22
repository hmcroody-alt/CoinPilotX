"""Global intelligence graph foundation for CoinPilotXAI.

The graph connects social, creator, education, safety, market, and commerce
entities without exposing private user data. Scores are deterministic and safe
to run during admin dashboards or background jobs.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from math import log1p


TRUST_WEIGHTS = {
    "verified": 12,
    "teacher": 10,
    "creator": 8,
    "seller": 6,
    "scam_report_accuracy": 14,
    "moderation_strike": -18,
    "unsafe_report": -12,
}


def entity_key(entity_type: str, entity_id) -> str:
    return f"{str(entity_type or 'entity').strip().lower()}:{entity_id}"


def node(entity_type: str, entity_id, label: str = "", metadata=None, score: float = 0) -> dict:
    return {
        "key": entity_key(entity_type, entity_id),
        "entity_type": str(entity_type or "entity"),
        "entity_id": str(entity_id or ""),
        "label": str(label or "")[:180],
        "score": round(float(score or 0), 2),
        "metadata": metadata or {},
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def edge(source_type: str, source_id, target_type: str, target_id, relationship: str, weight: float = 1, metadata=None) -> dict:
    return {
        "source_key": entity_key(source_type, source_id),
        "target_key": entity_key(target_type, target_id),
        "relationship": str(relationship or "related_to")[:80],
        "weight": round(float(weight or 1), 3),
        "metadata": metadata or {},
        "updated_at": datetime.utcnow().isoformat(timespec="seconds"),
    }


def creator_influence_score(stats=None) -> int:
    stats = stats or {}
    followers = float(stats.get("followers") or stats.get("follower_count") or 0)
    posts = float(stats.get("posts") or 0)
    reactions = float(stats.get("reactions") or 0)
    comments = float(stats.get("comments") or 0)
    trust = float(stats.get("trust_score") or 0)
    reports = float(stats.get("reports") or 0)
    score = trust * 0.42 + log1p(followers) * 12 + log1p(posts + reactions + comments) * 8 - reports * 5
    return max(0, min(100, int(score)))


def trust_propagation_score(entity=None, signals=None) -> int:
    entity = entity or {}
    signals = signals or []
    base = float(entity.get("trust_score") or entity.get("score") or 50)
    modifier = 0
    for signal in signals:
        kind = str(signal.get("signal_type") or signal.get("type") or "").lower()
        modifier += TRUST_WEIGHTS.get(kind, 0) * float(signal.get("weight") or 1)
    return max(0, min(100, int(base + modifier)))


def detect_scam_clusters(edges=None, min_weight: float = 3) -> list[dict]:
    edges = edges or []
    cluster_weights = defaultdict(float)
    cluster_members = defaultdict(set)
    for item in edges:
        relation = str(item.get("relationship") or "").lower()
        if relation not in {"reported_scam", "shares_wallet", "shares_link", "impersonates", "phishing_signal"}:
            continue
        source = item.get("source_key")
        target = item.get("target_key")
        cluster_key = target or source
        cluster_weights[cluster_key] += float(item.get("weight") or 1)
        if source:
            cluster_members[cluster_key].add(source)
        if target:
            cluster_members[cluster_key].add(target)
    clusters = []
    for key, weight in cluster_weights.items():
        if weight >= min_weight:
            clusters.append({"cluster_key": key, "risk_weight": round(weight, 2), "members": sorted(cluster_members[key])[:40]})
    return sorted(clusters, key=lambda row: row["risk_weight"], reverse=True)


def trace_trend_origin(events=None) -> list[dict]:
    events = events or []
    topic_first_seen = {}
    topic_counts = Counter()
    for event in events:
        topic = str(event.get("topic") or event.get("signal") or "").strip().lower()
        if not topic:
            continue
        topic_counts[topic] += 1
        created_at = event.get("created_at") or event.get("timestamp") or ""
        if topic not in topic_first_seen or str(created_at) < str(topic_first_seen[topic].get("created_at") or ""):
            topic_first_seen[topic] = event
    return [
        {
            "topic": topic,
            "mentions": count,
            "origin": topic_first_seen.get(topic, {}),
        }
        for topic, count in topic_counts.most_common(20)
    ]


def graph_health(nodes=None, edges=None, signals=None) -> dict:
    nodes = nodes or []
    edges = edges or []
    signals = signals or []
    risk_signals = sum(1 for item in signals if str(item.get("signal_type") or "").lower() in {"scam", "abuse", "phishing", "bot"})
    trust_nodes = sum(1 for item in nodes if float(item.get("score") or 0) >= 70)
    coverage = min(100, int((len(edges) / max(1, len(nodes))) * 35))
    trust = min(100, int((trust_nodes / max(1, len(nodes))) * 100))
    safety = max(0, 100 - risk_signals * 4)
    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "signal_count": len(signals),
        "coverage_score": coverage,
        "trust_score": trust,
        "safety_score": safety,
        "health_score": max(0, min(100, int(coverage * 0.35 + trust * 0.35 + safety * 0.3))),
    }


def executive_summary(nodes=None, edges=None, signals=None) -> dict:
    health = graph_health(nodes, edges, signals)
    scam_clusters = detect_scam_clusters(edges or [])
    trend_origins = trace_trend_origin(signals or [])
    attention = []
    if scam_clusters:
        attention.append(f"{len(scam_clusters)} scam-risk cluster(s) need review")
    if health["coverage_score"] < 35:
        attention.append("Graph coverage is still early; more entity edges should be indexed")
    if not attention:
        attention.append("Global intelligence graph is stable")
    return {
        "health": health,
        "scam_clusters": scam_clusters[:5],
        "trend_origins": trend_origins[:8],
        "what_needs_attention": attention,
    }
