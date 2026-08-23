"""Premium feature readiness registry.

Every capability Premium is *sold on* is declared here with its real
implementation status. The registry exists because the forensic audit found the
opposite of what the product page implies: several entitlements are granted into
the database on purchase and then **never read by any gate**, so buying them
changes nothing.

Statuses
--------

``PRODUCTION``   Implemented AND enforced by a real gate. Safe to advertise.
``BETA``         Implemented and enforced, but incomplete or behind a flag. May
                 be advertised only with an explicit "beta" label.
``NEXT_WAVE``    Committed and scheduled, not yet built. MUST NOT be advertised
                 as an existing benefit.
``FUTURE``       Directional idea. MUST NOT be advertised at all.
``BLOCKED``      Cannot ship as specified; blocked by a dependency or policy.

The rule this file enforces
---------------------------
``sellable(key)`` is the ONLY thing marketing surfaces should consult. A
capability that is granted but unenforced is not sellable, no matter how good it
sounds. ``tests/business_os/test_premium_canonical.py`` asserts that anything
claiming ``PRODUCTION`` names the gate that enforces it, so a future edit cannot
promote a promise to "production" without pointing at the code that delivers it.

Relationship to ``services/premium_capability_engine``
------------------------------------------------------
Both modules describe "what Premium offers", so it is worth being precise about
why both exist rather than letting them drift into a second split-brain.

``premium_capability_engine`` is keyed by **product area** (Premium Identity,
Creator AI, Elite Themes) and answers *"does the plumbing exist?"* — it tracks
required tables, routes and services. It is what the admin console renders.

This module is keyed by **entitlement key** (the thing a purchase actually
grants) and answers a different, narrower question: *"is there a gate that reads
this key, and may we therefore sell it?"* That question is what the audit found
nobody was asking, which is how five keys ended up granted-but-unenforced.

Where they overlap, THIS module wins for sales claims: a product area may be
"active" in the capability engine because its tables and routes exist, while the
entitlement that is supposed to gate it is read by nothing. ``sellable()`` is
the authority on advertising, and ``premium_api.status_center`` derives its
benefit list by filtering on it, so an over-optimistic capability entry cannot
by itself put a promise in front of a user.
"""

from __future__ import annotations

from typing import Optional

PRODUCTION = "PRODUCTION"
BETA = "BETA"
NEXT_WAVE = "NEXT_WAVE"
FUTURE = "FUTURE"
BLOCKED = "BLOCKED"

_SELLABLE = {PRODUCTION, BETA}


class Feature:
    """One advertised capability and the truth about it.

    ``enforced_by`` names the module/function that actually gates the feature.
    It is required for PRODUCTION/BETA and must be empty otherwise — that
    asymmetry is what stops an aspirational feature from being quietly marked
    shippable.
    """

    __slots__ = ("key", "label", "status", "enforced_by", "note")

    def __init__(self, key: str, label: str, status: str,
                 enforced_by: str = "", note: str = ""):
        self.key = key
        self.label = label
        self.status = status
        self.enforced_by = enforced_by
        self.note = note

    @property
    def sellable(self) -> bool:
        return self.status in _SELLABLE

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "status": self.status,
                "enforced_by": self.enforced_by, "note": self.note,
                "sellable": self.sellable}


# --- the registry -----------------------------------------------------------
# Statuses below are the AUDITED state as of this change, not aspirations.
_REGISTRY: tuple[Feature, ...] = (
    # --- canonical capability keys (enforced through business_os entitlements)
    Feature(
        "premium.access", "PulseSoc Premium membership", PRODUCTION,
        enforced_by="services.business_os.entitlements.premium.resolve",
        note="The umbrella membership fact. Every premium plan grants it.",
    ),
    Feature(
        "premium.profile.customization", "Profile customization", PRODUCTION,
        enforced_by="services.business_os.entitlements.facade.check",
        note="Gated through the facade; has a legacy reader mapping.",
    ),
    Feature(
        "premium.media.higher_quality", "Higher-quality media", BETA,
        enforced_by="services.business_os.entitlements.facade.check",
        note="Entitlement is enforced, but the quality ladder itself is partial.",
    ),
    Feature(
        "premium.identity.effects", "Profile identity effects", BETA,
        enforced_by="services.business_os.entitlements.facade.check",
        note="Aura/identity effects; mapped in the facade's legacy readers.",
    ),
    Feature(
        "premium.undx.advanced", "Advanced UNDX access", BETA,
        enforced_by="services.business_os.entitlements.facade.check",
        note=(
            "Capability gate is real. There is NO credit meter behind it — see "
            "premium.undx.credits below. Do not advertise a numeric allowance."
        ),
    ),

    Feature(
        "premium.crypto.advanced_alerts", "Advanced crypto alerts", PRODUCTION,
        enforced_by="services.alert_engine.create_alert_rule",
        note=(
            "Multi-metric compound conditions (price, 24h change, 24h volume, "
            "market cap) joined by and/or, plus crossing comparators. Gated in "
            "create_alert_rule rather than in the HTTP route, because the worker, "
            "UNDX and the admin tools all create rules through that function. "
            "Basic single-threshold alerts stay free and take the original "
            "evaluation path. Time-window conditions now evaluate against a real "
            "sampled series (services.market_observations, written only by "
            "alert_worker) and a window the series cannot answer is undecidable "
            "rather than zero. They are still not advertised: no surface offers "
            "them yet, and a window is only honest to sell once the creation UI "
            "offers exactly what coverage() reports it can answer."
        ),
    ),
    Feature(
        "premium.crypto.portfolio", "Premium crypto portfolio", NEXT_WAVE,
        note=(
            "Entitlement key is registered. The portfolio backend exists but is "
            "still gated by the legacy api_pro_required check and has no native "
            "client. Promote when the gate reads this key."
        ),
    ),
    Feature(
        "premium.crypto.intelligence", "UNDX crypto intelligence", NEXT_WAVE,
        note=(
            "Entitlement key is registered. UNDX has no grounded crypto "
            "portfolio/alert fact builder yet. Promote when one exists AND "
            "reads this key. Never advertise trading or price prediction."
        ),
    ),

    # --- legacy entitlement keys that are GRANTED BUT NEVER READ -------------
    # Each of these is written to the entitlement tables on purchase and then
    # checked by nothing. They were verified unreferenced outside the legacy
    # constant list itself. They must not appear as current benefits.
    Feature(
        "founder_badge", "Founder badge", PRODUCTION,
        enforced_by="services.premium_identity_engine.identity_mark",
        note="Genuinely rendered: founder number drives the badge.",
    ),
    Feature(
        "founder_access", "Founder status", PRODUCTION,
        enforced_by="services.premium_entitlement_service.is_founder_member",
        note="Backed by founder_memberships; drives the founder wall.",
    ),
    Feature(
        "creator_analytics", "Creator analytics", BETA,
        enforced_by="services.premium_entitlement_service.has_entitlement",
        note="Checked at a real call site, but coverage is partial.",
    ),
    Feature(
        "ai_creator_assistant", "AI creator assistant", BETA,
        enforced_by="services.premium_entitlement_service.has_entitlement",
        note="Referenced by the assistant surfaces; still maturing.",
    ),
    Feature(
        "creator_studio_pro", "Creator Studio Pro", NEXT_WAVE,
        note="GRANTED BUT UNENFORCED — no gate reads this key. Not a benefit yet.",
    ),
    Feature(
        "founder_hub_access", "Founder hub", NEXT_WAVE,
        note="GRANTED BUT UNENFORCED — no gate reads this key.",
    ),
    Feature(
        "premium_upload_limits", "Raised upload limits", NEXT_WAVE,
        note=(
            "GRANTED BUT UNENFORCED — no quota engine reads this key. There is "
            "no storage allowance behind it. Do not advertise a GB figure."
        ),
    ),
    Feature(
        "priority_support", "Priority support", NEXT_WAVE,
        note="GRANTED BUT UNENFORCED — no support-routing code reads this key.",
    ),
    Feature(
        "early_access_features", "Early access", NEXT_WAVE,
        note="GRANTED BUT UNENFORCED — no gate reads this key.",
    ),

    # --- explicitly disallowed claims ---------------------------------------
    Feature(
        "priority_verification", "Priority verification", BLOCKED,
        note=(
            "MUST NOT BE SOLD. Selling a faster path to verification makes "
            "verification purchasable, which is exactly what verification is "
            "supposed to rule out. Premium is not verification and must not "
            "buy influence over it. Retained as a key only so existing grants "
            "resolve; it confers nothing."
        ),
    ),
    Feature(
        "premium.ads.free", "Ad-free browsing", FUTURE,
        note=(
            "NOT IMPLEMENTED. No ad path consults premium status: the ad "
            "eligibility and serving code contains no premium suppression. If "
            "this is ever built it must suppress at the ad opportunity layer "
            "and record an explicit PREMIUM_SUPPRESSED no-fill reason, so "
            "suppressed impressions are distinguishable from a delivery outage."
        ),
    ),
    Feature(
        "premium.undx.credits", "UNDX credit allowance", FUTURE,
        note=(
            "NOT IMPLEMENTED. No credit meter exists for UNDX; a search for a "
            "2,000-unit allowance found nothing. Metering would go through the "
            "existing business_os_ent_usage engine (check_and_consume), not a "
            "second quota system. Do not advertise a number."
        ),
    ),
    Feature(
        "premium.security.timeline", "Security timeline", FUTURE,
        note=(
            "NOT IMPLEMENTED as a premium benefit. Security event history is "
            "core account safety and must not be paywalled."
        ),
    ),
)

_BY_KEY = {f.key: f for f in _REGISTRY}


# --- API --------------------------------------------------------------------
def get(key: str) -> Optional[Feature]:
    return _BY_KEY.get(key)


def status(key: str) -> str:
    """Readiness status for ``key``. Unknown keys are FUTURE (fail closed)."""
    f = _BY_KEY.get(key)
    return f.status if f else FUTURE


def sellable(key: str) -> bool:
    """True only when the capability is actually implemented AND enforced.

    Marketing surfaces must consult this rather than assuming that a key in the
    catalog means a working feature.
    """
    f = _BY_KEY.get(key)
    return bool(f and f.sellable)


def sellable_features() -> list[dict]:
    """Everything that may honestly appear on a Premium sales surface."""
    return [f.as_dict() for f in _REGISTRY if f.sellable]


def unenforced_features() -> list[dict]:
    """Keys that are granted on purchase but gate nothing. The honesty gap."""
    return [f.as_dict() for f in _REGISTRY
            if f.status in {NEXT_WAVE, FUTURE, BLOCKED}]


def all_features() -> list[dict]:
    return [f.as_dict() for f in _REGISTRY]


def report() -> dict:
    """Registry summary for the admin console and the status centre."""
    counts: dict[str, int] = {}
    for f in _REGISTRY:
        counts[f.status] = counts.get(f.status, 0) + 1
    return {
        "counts": counts,
        "sellable": [f.key for f in _REGISTRY if f.sellable],
        "not_sellable": [f.key for f in _REGISTRY if not f.sellable],
        "total": len(_REGISTRY),
    }
