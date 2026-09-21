"""Organic and house commerce discovery for PulseSoc.

Surfaces Marketplace products across Feed, Reels, Messenger and Marketplace
itself **before a seller is required to buy advertising**. It is not an ad
system with the billing removed — it is a separate engine with its own
eligibility bar, its own ranking model, its own event tables and its own
labelling, deliberately walled off from ``services/business_os/advertising`` so
that paid and unpaid reach can never be accounted as the same thing.

Read ``promotion.py`` first; it explains the wall and why it is load-bearing.

Module map
----------

``promotion``   the three promotion classes and the assertions that separate them
``config``      every env knob and the ranking weight vector, in one place
``schema``      the five ``commerce_discovery_*`` tables
``subject``     time, ids, the salted viewer hash, the placement HMAC
``eligibility`` which listings may be *pushed*, a strict subset of purchasable
``ranking``     the twelve-signal explainable score
``preferences`` the viewer's consent and suppression state, resolved once
``engine``      the serve pipeline; returns ``[]`` for every failure mode
``events``      impression, engagement and feedback writes, all idempotent

The HTTP surface is ``services/commerce_discovery_routes.py``.
"""

from __future__ import annotations

from . import (  # noqa: F401  (re-exported for callers and tests)
    config,
    eligibility,
    engine,
    events,
    preferences,
    promotion,
    ranking,
    schema,
    subject,
)

__all__ = [
    "config",
    "eligibility",
    "engine",
    "events",
    "preferences",
    "promotion",
    "ranking",
    "schema",
    "subject",
]
