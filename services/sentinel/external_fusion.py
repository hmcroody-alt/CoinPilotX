"""Deterministic external/internal evidence fusion (Mission 4, Stages 22–24).

THE EXTERNAL-EVIDENCE CEILING: external intelligence ALONE can never push an
indicator to HIGH_RISK or trigger enforcement. External evidence raises
suspicion up to EXTERNAL_ONLY_RISK_CAP (0.6, strictly below the 0.7
HIGH_RISK line used by the Mission 3 risk model); crossing that line
requires INTERNAL corroboration. Provider disagreement is surfaced, never
averaged away (Stage 23); expired intelligence has already degraded to
UNKNOWN before it gets here (Stage 24).

This module computes and explains. It does not block, suspend, ban, or
seize — no enforcement path exists (Stage 44).
"""

from __future__ import annotations

from services.sentinel import external_observations

# External evidence alone caps here — below the HIGH_RISK threshold (0.7).
EXTERNAL_ONLY_RISK_CAP = 0.6

# Verdict weights: how much one provider observation can contribute.
_VERDICT_WEIGHT = {"MALICIOUS": 1.0, "SUSPICIOUS": 0.6, "VULNERABLE": 0.8,
                   "BENIGN": -0.5, "NOT_AFFECTED": -0.5, "UNKNOWN": 0.0}

RISK_BANDS = ("NONE", "LOW", "MEDIUM", "ELEVATED", "HIGH")


def _band(score: float) -> str:
    if score >= 0.7:
        return "HIGH"
    if score >= 0.5:
        return "ELEVATED"
    if score >= 0.3:
        return "MEDIUM"
    if score > 0.0:
        return "LOW"
    return "NONE"


def fuse(indicator_type: str, indicator_ref: str, *,
         internal_corroboration: list[dict] | None = None,
         conn=None) -> dict:
    """Fuse all fresh external observations for one indicator with any
    internal corroborating evidence. Deterministic and fully explained.

    ``internal_corroboration``: list of {"source": str, "weight": float
    (0..1), "reason": str} items produced by INTERNAL detection (Mission 2/3
    events, invariants) — never by another external provider.
    """
    dis = external_observations.disagreement(indicator_type, indicator_ref,
                                             conn=conn)
    providers = dis["providers"]
    reasons: list[str] = []
    external_score = 0.0
    for provider_id, obs in sorted(providers.items()):
        weight = _VERDICT_WEIGHT.get(obs["verdict"], 0.0)
        contribution = weight * float(obs["confidence"])
        external_score += contribution
        reasons.append(
            f"{provider_id}: {obs['verdict']} "
            f"(confidence {obs['confidence']:.2f}, trust {obs['source_trust']}, "
            f"contribution {contribution:+.2f})")
    external_score = max(0.0, min(external_score, 1.0))

    capped = min(external_score, EXTERNAL_ONLY_RISK_CAP)
    if external_score > EXTERNAL_ONLY_RISK_CAP:
        reasons.append(
            f"external-only score {external_score:.2f} capped at "
            f"{EXTERNAL_ONLY_RISK_CAP} — external evidence alone never "
            f"reaches HIGH_RISK (Stage 22)")

    internal_score = 0.0
    for item in (internal_corroboration or []):
        try:
            w = max(0.0, min(float(item.get("weight", 0.0)), 1.0))
        except (TypeError, ValueError):
            w = 0.0
        if w > 0:
            internal_score = max(internal_score, w)
            reasons.append(f"internal corroboration [{item.get('source', '?')}]: "
                           f"{item.get('reason', '')} (weight {w:.2f})")
    if not internal_corroboration:
        reasons.append("no internal corroboration: risk stays at the "
                       "external-only cap or below")

    # Corroborated risk may exceed the cap; uncorroborated risk may not.
    if internal_score > 0:
        fused_score = min(1.0, max(capped, (capped + internal_score) / 2
                                   + 0.5 * min(capped, internal_score)))
    else:
        fused_score = capped
    fused_score = round(min(fused_score, 1.0), 4)

    if dis["disagreement"]:
        reasons.append("providers DISAGREE on this indicator — verdicts listed "
                       "verbatim above, not averaged (Stage 23)")

    return {
        "indicator_type": indicator_type,
        "indicator_digest": dis["indicator_digest"],
        "providers": providers,
        "disagreement": dis["disagreement"],
        "external_score": round(external_score, 4),
        "external_score_capped": round(capped, 4),
        "internal_corroboration_score": round(internal_score, 4),
        "risk_score": fused_score,
        "risk_band": _band(fused_score),
        "reasons": reasons,
        "enforcement": "NONE",
        "authority_note": "fusion output is intelligence for humans and "
                          "deterministic policy; it authorizes no enforcement "
                          "(Stage 44, SC2)",
    }
