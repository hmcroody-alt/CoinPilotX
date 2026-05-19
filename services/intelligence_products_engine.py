"""Aggregate-only enterprise intelligence product helpers."""

from __future__ import annotations


def enterprise_products():
    return [
        {"name": "Scam Trend Reports", "privacy": "aggregate_only", "description": "High-level scam category trends without private identities."},
        {"name": "Market Sentiment API", "privacy": "aggregate_only", "description": "Public and anonymized community market mood summaries."},
        {"name": "Creator Trend Reports", "privacy": "public_only", "description": "Public creator rankings and content trends."},
        {"name": "Fraud Intelligence Summaries", "privacy": "aggregate_only", "description": "Risk pattern summaries for safer crypto education."},
    ]
