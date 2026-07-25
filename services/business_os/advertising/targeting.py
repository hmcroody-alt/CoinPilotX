"""Advertising slice-6 — governed targeting foundation (audience + placement).

This module is the single server-side authority that decides what an advertiser
is allowed to target. It is intentionally *restrictive*: a safe MVP audience
surface only, with everything sensitive rejected by name, and anything unknown
rejected by default. It is pure/validation-only — no DB, no delivery, no money —
so it can be unit-tested in isolation and reused by the ad-set service.

Design rules (spec §2, §3):

  * Allowlist, not denylist, for the audience *shape*: only the fields below are
    accepted; every other key is rejected server-side (``unknown_targeting_field``).
  * A named denylist on TOP of that, purely to give a precise, honest error for
    the sensitive/prohibited categories a client might try (``prohibited_targeting``)
    rather than a generic "unknown field".
  * The result is a NORMALIZED, canonical, versionable dict — never the raw client
    JSON. The ad-set service persists exactly what this returns.
  * Placement is a strict allowlist of {feed, reels}, config only. No placement
    delivery is wired anywhere.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from services.business_os.advertising.service import AdvertisingError


# --- placement (spec §3) ----------------------------------------------------
# Strict allowlist. `reels` is included as a *config* option only; no placement
# delivery, insertion, or ranking is wired by this slice.
PLACEMENTS_ALLOWED = ("feed", "reels")


def validate_placements(value: Any) -> list:
    """Return a normalized, de-duplicated, ordered placement selection.

    Rejects a non-list, an empty selection, and anything outside the allowlist
    (``unsupported_placement``). Order is canonicalized to PLACEMENTS_ALLOWED so
    the stored structure is stable.
    """
    if not isinstance(value, list) or not value:
        raise AdvertisingError(
            "At least one placement is required.", 400, "placement_required")
    seen = set()
    for item in value:
        if not isinstance(item, str):
            raise AdvertisingError(
                "Placement must be a string.", 400, "unsupported_placement")
        key = item.strip().lower()
        if key not in PLACEMENTS_ALLOWED:
            raise AdvertisingError(
                f"Unsupported placement: {item!r}.", 400, "unsupported_placement")
        seen.add(key)
    # canonical order
    return [p for p in PLACEMENTS_ALLOWED if p in seen]


# --- audience (spec §2) -----------------------------------------------------
# The ONLY top-level audience fields an advertiser may set. Placement lives on
# the ad set itself (validate_placements), not inside the audience spec.
AUDIENCE_ALLOWED_FIELDS = frozenset({
    "countries", "languages", "min_age", "max_age",
    "device_classes", "connections", "exclusions",
})

# Named sensitive / prohibited categories. Rejected with a precise code even
# though they would also be caught by the allowlist — a clear signal is safer.
AUDIENCE_PROHIBITED_FIELDS = frozenset({
    "sensitive_traits", "precise_location", "location_radius", "lat", "lng",
    "custom_audience", "custom_audiences", "uploaded_list", "customer_list",
    "lookalike", "lookalikes", "retargeting", "retargeting_pixel", "pixel",
    "interests", "behaviors", "health", "religion", "religious",
    "politics", "political", "race", "ethnicity", "ethnic",
    "sexual_orientation", "gender_identity", "financial_hardship",
    "income", "children", "minors", "under_18",
})

# Device classes we recognise. First-party, non-sensitive.
DEVICE_CLASSES_ALLOWED = frozenset({"mobile", "tablet", "desktop"})

# First-party audience connections the platform can honour without any
# consent-backed external dataset.
CONNECTIONS_ALLOWED = frozenset({"existing_followers", "previous_engagers"})
# `marketplace_customers` is deliberately NOT in CONNECTIONS_ALLOWED: it would
# require an authoritative, consent-backed Marketplace customer dataset which is
# not wired in this slice. It is rejected explicitly (documented as deferred)
# rather than silently accepted.
CONNECTIONS_DEFERRED = frozenset({"marketplace_customers"})

# Ages: MVP refuses to target minors at all (children targeting is prohibited),
# so the floor is 18. Upper bound guards nonsense values; 65 is treated as "65+".
AGE_MIN_FLOOR = 18
AGE_MAX_CEIL = 120

_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
_MAX_LIST = 60


def _norm_code_list(value: Any, field: str) -> list:
    if not isinstance(value, list) or not value:
        raise AdvertisingError(
            f"{field} must be a non-empty list.", 400, "bad_targeting")
    if len(value) > _MAX_LIST:
        raise AdvertisingError(f"Too many {field}.", 400, "bad_targeting")
    out = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not _CODE_RE.match(item.strip()):
            raise AdvertisingError(
                f"Invalid {field} value: {item!r}.", 400, "bad_targeting")
        key = item.strip().lower()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _validate_ages(spec: dict, out: dict) -> None:
    has_min = "min_age" in spec
    has_max = "max_age" in spec
    if not has_min and not has_max:
        return
    min_age = spec.get("min_age", AGE_MIN_FLOOR)
    max_age = spec.get("max_age", AGE_MAX_CEIL)
    if not isinstance(min_age, int) or isinstance(min_age, bool):
        raise AdvertisingError("min_age must be an integer.", 400, "bad_age_range")
    if not isinstance(max_age, int) or isinstance(max_age, bool):
        raise AdvertisingError("max_age must be an integer.", 400, "bad_age_range")
    if min_age < AGE_MIN_FLOOR:
        raise AdvertisingError(
            f"min_age must be at least {AGE_MIN_FLOOR}.", 400, "bad_age_range")
    if max_age > AGE_MAX_CEIL:
        raise AdvertisingError(
            f"max_age must be at most {AGE_MAX_CEIL}.", 400, "bad_age_range")
    if min_age > max_age:
        raise AdvertisingError(
            "min_age must not exceed max_age.", 400, "bad_age_range")
    out["min_age"] = min_age
    out["max_age"] = max_age


def _validate_connections(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise AdvertisingError(f"{field} must be an object.", 400, "bad_targeting")
    out = {}
    for key, flag in value.items():
        if key in CONNECTIONS_DEFERRED:
            raise AdvertisingError(
                f"{key!r} targeting requires a consent-backed dataset that is not "
                "available yet.", 400, "unsupported_connection")
        if key not in CONNECTIONS_ALLOWED:
            raise AdvertisingError(
                f"Unknown {field} key: {key!r}.", 400, "unknown_targeting_field")
        if not isinstance(flag, bool):
            raise AdvertisingError(
                f"{field}.{key} must be true/false.", 400, "bad_targeting")
        if flag:
            out[key] = True
    return out


def validate_audience(spec: Any) -> dict:
    """Validate + normalize a governed audience spec into canonical form.

    Returns a NEW dict containing only recognised, normalized fields. Raises
    ``AdvertisingError`` (400) on any prohibited category, unknown field, or
    malformed value. An empty/None spec is a valid "broad" audience and yields an
    empty canonical dict.
    """
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise AdvertisingError("Audience must be an object.", 400, "bad_targeting")

    # Prohibited categories first, for a precise error.
    for key in spec:
        low = str(key).strip().lower()
        if low in AUDIENCE_PROHIBITED_FIELDS:
            raise AdvertisingError(
                f"Targeting on {key!r} is not permitted.",
                400, "prohibited_targeting")
    # Then the allowlist: any remaining unknown key is rejected.
    for key in spec:
        if str(key).strip().lower() not in AUDIENCE_ALLOWED_FIELDS:
            raise AdvertisingError(
                f"Unknown targeting field: {key!r}.", 400, "unknown_targeting_field")

    out: dict = {}
    if "countries" in spec:
        out["countries"] = _norm_code_list(spec["countries"], "countries")
    if "languages" in spec:
        out["languages"] = _norm_code_list(spec["languages"], "languages")
    _validate_ages(spec, out)
    if "device_classes" in spec:
        dcs = spec["device_classes"]
        if not isinstance(dcs, list) or not dcs:
            raise AdvertisingError(
                "device_classes must be a non-empty list.", 400, "bad_targeting")
        norm = []
        seen = set()
        for d in dcs:
            key = d.strip().lower() if isinstance(d, str) else None
            if key not in DEVICE_CLASSES_ALLOWED:
                raise AdvertisingError(
                    f"Unsupported device class: {d!r}.", 400, "bad_targeting")
            if key not in seen:
                seen.add(key)
                norm.append(key)
        out["device_classes"] = norm
    if "connections" in spec:
        conns = _validate_connections(spec["connections"], "connections")
        if conns:
            out["connections"] = conns
    if "exclusions" in spec:
        excl = _validate_connections(spec["exclusions"], "exclusions")
        if excl:
            out["exclusions"] = excl
    return out


def audience_is_valid(audience: Optional[dict]) -> bool:
    """Cheap re-check used by the derived readiness view. A stored audience is
    'valid' if it re-validates cleanly (or is the empty/broad audience)."""
    try:
        validate_audience(audience or {})
        return True
    except AdvertisingError:
        return False


def placements_are_valid(placements: Any) -> bool:
    try:
        validate_placements(placements)
        return True
    except AdvertisingError:
        return False
