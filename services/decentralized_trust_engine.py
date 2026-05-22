"""Decentralized trust architecture foundation."""

from __future__ import annotations


def validate_trust_signal(signal=None) -> dict:
    signal = signal or {}
    source_trust = int(signal.get("source_trust") or 0)
    weight = max(0.1, min(2.0, source_trust / 60 if source_trust else 0.5))
    return {"valid": source_trust >= 30, "trust_weight": round(weight, 2)}
