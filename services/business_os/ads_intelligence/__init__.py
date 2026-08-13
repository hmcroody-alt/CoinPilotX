"""Ads intelligence — measurement and decision layer for PulseSoc advertising.

This package owns events, delivery decisions, interest, performance rollups,
pacing, frequency, and diagnostics. It owns no advertiser, campaign, creative,
audience, wallet, ledger, price, bill, review queue, or admin approval — each of
those already has exactly one home and keeps it.

See `docs/advertising_current_architecture.md` for the end-to-end map and the
per-component KEEP / EXTEND / NORMALIZE / MIGRATE classification.
"""

from __future__ import annotations

import os

#: Master rollout flag. Follows the advertising slice's convention exactly, so
#: operators have one mental model for both. Unset means off: the default
#: posture of this whole subsystem is inert.
FLAG_ENV = "BUSINESS_OS_ADS_INTELLIGENCE"

_TRUTHY = {"1", "true", "on", "yes", "enabled", "canonical"}


def is_enabled() -> bool:
    """True only when the rollout flag is explicitly on."""
    return (os.environ.get(FLAG_ENV) or "").strip().lower() in _TRUTHY


def measurement_enabled() -> bool:
    """True when passive measurement (decision + event recording) may run.

    Deliberately separate from :func:`is_enabled`, because the staged rollout
    turns measurement on well before anything is allowed to influence delivery.
    ``BUSINESS_OS_ADS_INTELLIGENCE_MEASUREMENT`` enables recording on its own;
    the master flag implies it.
    """
    if is_enabled():
        return True
    raw = os.environ.get(FLAG_ENV + "_MEASUREMENT") or ""
    return raw.strip().lower() in _TRUTHY
