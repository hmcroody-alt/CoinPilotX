"""Marketplace-ready scaffolding for future compliant creator products."""

from __future__ import annotations


def marketplace_foundation():
    return {
        "status": "foundation_only",
        "launch_blockers": ["policy review", "payments compliance", "creator payout approval"],
        "future_products": ["premium rooms", "tournaments", "digital badges", "creator subscriptions", "educational products"],
        "rule": "Do not launch real marketplace transactions until policy and legal pages are ready.",
    }
