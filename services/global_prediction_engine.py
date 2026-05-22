"""Predictive global systems foundation."""

from __future__ import annotations


def global_prediction_map(metrics=None) -> dict:
    metrics = metrics or {}
    scam_pressure = int(metrics.get("scam_pressure") or 0)
    creator_growth = int(metrics.get("creator_growth") or 0)
    education_demand = int(metrics.get("education_demand") or 0)
    return {
        "scam_wave_risk": "high" if scam_pressure >= 70 else "watch" if scam_pressure >= 40 else "low",
        "creator_growth_outlook": "surging" if creator_growth >= 70 else "building",
        "education_demand": "high" if education_demand >= 65 else "normal",
    }
