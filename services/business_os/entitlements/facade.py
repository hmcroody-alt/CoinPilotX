"""Compatibility facade + shadow mode for the entitlement migration.

This is the seam that lets the canonical service (``service.py``) be introduced
in front of the legacy entitlement machinery **without changing user-visible
access until we choose to**. Callers in ``bot.py`` migrate to ``facade.check()``;
the facade decides, per the ``BUSINESS_OS_ENTITLEMENTS`` mode, whether the answer
comes from canonical, legacy, or both-with-comparison.

Modes (env ``BUSINESS_OS_ENTITLEMENTS``):

* ``off`` (default)  — legacy only. Zero behaviour change. Canonical is not even
  consulted, so shipping this code dark is safe.
* ``shadow``         — serve the **legacy** answer (still authoritative), but also
  compute the canonical answer, record a ``shadow_diff`` audit row when they
  disagree, and emit an observability log. Never changes access.
* ``canonical``      — serve the **canonical** answer, but fall back to legacy when
  canonical is silent (no grant either way), logging every fallback so we can see
  how much legacy still carries.

Legacy fallback (report precedence rule 6) is implemented here, not in the
canonical core, so ``service.has_entitlement`` stays a clean canonical decision.

The legacy readers are imported lazily and defensively: if the legacy module
can't be imported (or a specific key isn't mapped), the facade degrades to
canonical-only rather than raising, and says so in the observability record.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Optional

from services import db
from services.business_os.entitlements import service as _svc

_log = logging.getLogger("business_os.entitlements.facade")

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_CANONICAL = "canonical"
_VALID_MODES = {MODE_OFF, MODE_SHADOW, MODE_CANONICAL}


def get_mode() -> str:
    """Resolve the current facade mode from the environment. Unknown/unset -> off.

    Read on every call (not cached) so an operator flipping the flag takes effect
    without a process restart, matching the ledger slice's flag semantics.
    """
    raw = (os.getenv("BUSINESS_OS_ENTITLEMENTS", "") or "").strip().lower()
    if raw in ("1", "true", "on", "yes", MODE_CANONICAL):
        return MODE_CANONICAL
    if raw == MODE_SHADOW:
        return MODE_SHADOW
    # "", "0", "false", "off", "no", or any unrecognised value -> fail safe to
    # off (legacy authoritative, zero behaviour change).
    return MODE_OFF


# --- legacy key mapping -----------------------------------------------------
# Maps a canonical entitlement key to a callable ``(subject_id) -> Optional[bool]``
# that answers using the LEGACY system. Returning None means "legacy has no
# opinion" (canonical stays authoritative). Only keys with a real legacy analogue
# are mapped; unmapped keys are canonical-only by definition.
def _legacy_premium_customization(subject_id: Any) -> Optional[bool]:
    svc = _load_legacy()
    if svc is None:
        return None
    # Deliberately the RAW legacy reader, not the public ``is_premium_user``.
    # ``is_premium_user`` is now a shim that resolves through the canonical
    # service; calling it here would make shadow mode compare canonical against
    # itself, report perfect agreement, and hide every split-brain account the
    # comparison exists to find. ``_is_premium_user_raw`` still reads the legacy
    # tables directly. The getattr fallback keeps older checkouts working.
    reader = getattr(svc, "_is_premium_user_raw", None) or svc.is_premium_user
    try:
        return bool(reader(int(subject_id)))
    except Exception:  # noqa: BLE001 — legacy must never break the facade
        _log.exception("legacy is_premium_user failed for subject=%s", subject_id)
        return None


def _legacy_has_entitlement(key: str):
    """Factory: legacy reader that calls the legacy has_entitlement(key)."""
    def _reader(subject_id: Any) -> Optional[bool]:
        svc = _load_legacy()
        if svc is None:
            return None
        try:
            return bool(svc.has_entitlement(int(subject_id), key))
        except Exception:  # noqa: BLE001
            _log.exception("legacy has_entitlement(%s) failed subject=%s", key, subject_id)
            return None
    return _reader


_LEGACY_READERS: dict[str, Callable[[Any], Optional[bool]]] = {
    # First vertical slice: profile customization maps to legacy premium truthiness.
    "premium.profile.customization": _legacy_premium_customization,
    # R3.1 slice: identity effects (aura) — same legacy premium truthiness so that,
    # while no canonical grant exists yet, an eligible premium user still qualifies
    # under canonical mode (account-hold precedence is applied on top in explain()).
    "premium.identity.effects": _legacy_premium_customization,
    "premium.media.higher_quality": _legacy_has_entitlement("premium.media.higher_quality"),
    "premium.undx.advanced": _legacy_has_entitlement("premium.undx.advanced"),
    # Crypto intelligence maps to legacy premium truthiness rather than to
    # ``_legacy_has_entitlement``. The latter reads legacy entitlement ROWS, and
    # no row has ever been written for a key introduced today — it would answer
    # False for every existing member and make the capability look like a
    # separate purchase. Premium membership is the thing that confers it, which
    # is exactly what ``_legacy_premium_customization`` reports.
    "premium.crypto.advanced_alerts": _legacy_premium_customization,
    "premium.crypto.portfolio": _legacy_premium_customization,
    "premium.crypto.intelligence": _legacy_premium_customization,
}

_legacy_module = None
_legacy_load_attempted = False


def _load_legacy():
    """Lazily import the legacy premium entitlement service. Cached; defensive."""
    global _legacy_module, _legacy_load_attempted
    if _legacy_load_attempted:
        return _legacy_module
    _legacy_load_attempted = True
    try:
        from services import premium_entitlement_service as _pes
        _legacy_module = _pes
    except Exception:  # noqa: BLE001
        _log.exception("could not import legacy premium_entitlement_service")
        _legacy_module = None
    return _legacy_module


def _legacy_opinion(subject_id: Any, key: str) -> Optional[bool]:
    reader = _LEGACY_READERS.get(key)
    if reader is None:
        return None
    return reader(subject_id)


# --- account-hold precedence (report finding R3) ----------------------------
# One authoritative definition, used by every on-flag decision path so the
# suspension rule is not duplicated per route. Mirrors ``services.pro_access``:
# an account is eligible only while ``account_status == 'active'`` (the column
# default) AND access is not administratively disabled. Any other status string
# (suspended, disabled, banned, restricted, deleted, ...) is a hold. This is a
# RESTRICTION that overrides any paid/promotional grant — a paid grant must
# never beat an account hold.
_ELIGIBLE_STATUS = "active"


def _account_hold(subject_id: Any, context: Optional[dict] = None) -> dict:
    """Resolve the authoritative account-hold state for a subject.

    Returns ``{on_hold, account_status, access_enabled, reason}``.

    Prefers ``context`` (the in-memory account row the caller already loaded,
    i.e. the freshest authoritative values) and only touches the DB when the
    caller did not supply ``account_status``. Fails SAFE for the *feature*: if
    the account state cannot be determined, it reports ``on_hold=False`` and
    ``reason='account_status_unavailable'`` so the base decision (legacy/
    canonical) is preserved rather than the feature breaking. When the status
    IS known and indicates a hold, access is denied.
    """
    status: Any = None
    access_enabled: Any = None
    have_status = False
    if context is not None and "account_status" in context:
        status = context.get("account_status")
        access_enabled = context.get("access_enabled")
        have_status = True
    if not have_status:
        try:
            conn = db.connect()
            try:
                cur = conn.execute(
                    "SELECT account_status, access_enabled FROM users "
                    "WHERE user_id = ? LIMIT 1",
                    (int(subject_id),),
                )
                row = cur.fetchone()
            finally:
                conn.close()
            if row is not None:
                status = row[0]
                access_enabled = row[1]
                have_status = True
        except Exception:  # noqa: BLE001 — missing table/row/col -> fail safe
            _log.debug("account-hold lookup unavailable for subject=%s", subject_id)
            return {"on_hold": False, "account_status": None,
                    "access_enabled": None, "reason": "account_status_unavailable"}
    if not have_status:
        return {"on_hold": False, "account_status": None,
                "access_enabled": None, "reason": "account_status_unavailable"}
    norm = (str(status).strip().lower() if status is not None else _ELIGIBLE_STATUS) or _ELIGIBLE_STATUS
    if norm != _ELIGIBLE_STATUS:
        return {"on_hold": True, "account_status": norm,
                "access_enabled": access_enabled, "reason": f"account_{norm}"}
    # access_enabled defaults to 1; only an explicit 0/false is a hold.
    if access_enabled is not None:
        try:
            if int(access_enabled) == 0:
                return {"on_hold": True, "account_status": norm,
                        "access_enabled": 0, "reason": "account_access_disabled"}
        except (TypeError, ValueError):
            pass
    return {"on_hold": False, "account_status": norm,
            "access_enabled": access_enabled, "reason": ""}


def account_hold(subject_id: Any, context: Optional[dict] = None) -> dict:
    """Public accessor for the authoritative account-hold state (R3.2).

    Thin wrapper over the internal resolver so presentation/status callers can
    reflect the SAME suspension precedence the capability gates enforce, without
    duplicating the rule. Returns ``{on_hold, account_status, access_enabled,
    reason}``; fails safe (``on_hold=False``) when the account state is unknown.
    """
    return _account_hold(subject_id, context)


# --- shadow-diff audit ------------------------------------------------------
def _record_shadow_diff(subject_type: str, subject_id: Any, key: str,
                        legacy: Optional[bool], canonical: bool,
                        intended_winner: str, migration_action: str,
                        account_hold: Optional[dict] = None) -> None:
    """Write a shadow_diff audit row. Best-effort; never breaks the read path."""
    try:
        after: dict = {"canonical": canonical, "intended_winner": intended_winner}
        if account_hold is not None and account_hold.get("on_hold"):
            after["account_hold"] = True
            after["account_status"] = account_hold.get("account_status")
            after["hold_reason"] = account_hold.get("reason")
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO business_os_ent_audit "
                "(subject_type, subject_id, entitlement_key, action, actor, reason, "
                "before_json, after_json, created_at) "
                "VALUES (?, ?, ?, 'shadow_diff', 'facade', ?, ?, ?, ?)",
                (
                    subject_type, str(subject_id), key,
                    migration_action,
                    json.dumps({"legacy": legacy}, sort_keys=True),
                    json.dumps(after, sort_keys=True),
                    _svc._now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        _log.exception("failed to record shadow_diff key=%s subject=%s", key, subject_id)


# --- public API -------------------------------------------------------------
def check(subject_id: Any, key: str, *, subject_type: str = "user",
          context: Optional[dict] = None) -> bool:
    """The migration-aware entitlement check. Returns the authoritative bool for
    the current mode. Thin wrapper over :func:`explain` (single decision path).

    off       -> legacy only (canonical + account-hold untouched: zero change).
    shadow    -> legacy authoritative; canonical computed + diffed; access unchanged.
    canonical -> canonical authoritative; legacy consulted only when canonical is
                 silent (no grant); an account hold overrides any grant.
    """
    return explain(subject_id, key, subject_type=subject_type, context=context)["allowed"]


def explain(subject_id: Any, key: str, *, subject_type: str = "user",
            context: Optional[dict] = None) -> dict:
    """Explainable entitlement decision for testing and audit.

    Returns ``{allowed, flag_mode, decision_source, mode, account_hold,
    account_status, reason, legacy, canonical_grant}``. ``check`` returns the
    ``allowed`` field. The account-hold restriction is evaluated only on the
    on-flag paths (shadow/canonical); ``off`` is byte-for-byte legacy.
    """
    mode = get_mode()
    legacy = _legacy_opinion(subject_id, key)
    legacy_bool = bool(legacy) if legacy is not None else False

    if mode == MODE_OFF:
        # Preserve current behaviour exactly: legacy only, no account-hold overlay.
        return {
            "allowed": legacy_bool,
            "flag_mode": MODE_OFF,
            "decision_source": "legacy",
            "mode": "legacy",
            "account_hold": False,
            "account_status": None,
            "reason": "flag_off_legacy",
            "legacy": legacy,
            "canonical_grant": None,
        }

    hold = _account_hold(subject_id, context)

    if mode == MODE_SHADOW:
        canonical_grant = _svc.has_entitlement(subject_id, key, subject_type=subject_type)
        # Access is UNCHANGED in shadow: we still serve the legacy answer. But we
        # record what canonical WOULD do, including the account-hold override, so
        # the migration gap (and every suspended-but-premium account) is visible.
        effective_canonical = False if hold["on_hold"] else canonical_grant
        if legacy_bool != effective_canonical:
            _log.info("entitlement shadow diff key=%s subject=%s legacy=%s canonical=%s hold=%s",
                      key, subject_id, legacy_bool, effective_canonical, hold["reason"])
            _record_shadow_diff(subject_type, subject_id, key, legacy, effective_canonical,
                                intended_winner="legacy",
                                migration_action=_suggest_action(legacy_bool, effective_canonical),
                                account_hold=hold)
        return {
            "allowed": legacy_bool,  # legacy remains authoritative in shadow
            "flag_mode": MODE_SHADOW,
            "decision_source": "legacy",
            "mode": "legacy",
            "account_hold": hold["on_hold"],
            "account_status": hold["account_status"],
            "reason": hold["reason"] or "shadow_serves_legacy",
            "legacy": legacy,
            "canonical_grant": canonical_grant,
        }

    # MODE_CANONICAL -----------------------------------------------------------
    canonical_allowed, canonical_silent = _canonical_decision(subject_id, key, subject_type)
    if canonical_silent:
        # Canonical silent -> legacy fallback (report precedence rule 6), logged.
        base_allowed = legacy_bool
        base_source = "legacy_fallback"
        if legacy is not None:
            _log.info("entitlement legacy_fallback key=%s subject=%s legacy=%s",
                      key, subject_id, legacy)
    else:
        base_allowed = canonical_allowed
        base_source = "canonical_grant"

    # Account-hold is a RESTRICTION and overrides any grant (paid or otherwise).
    if hold["on_hold"]:
        return {
            "allowed": False,
            "flag_mode": MODE_CANONICAL,
            "decision_source": "account_hold",
            "mode": hold["reason"],  # e.g. account_suspended / account_access_disabled
            "account_hold": True,
            "account_status": hold["account_status"],
            "reason": hold["reason"],
            "legacy": legacy,
            "canonical_grant": None if canonical_silent else canonical_allowed,
        }
    return {
        "allowed": base_allowed,
        "flag_mode": MODE_CANONICAL,
        "decision_source": base_source,
        "mode": base_source,
        "account_hold": False,
        "account_status": hold["account_status"],
        "reason": hold["reason"] or base_source,
        "legacy": legacy,
        "canonical_grant": None if canonical_silent else canonical_allowed,
    }


def _canonical_decision(subject_id: Any, key: str, subject_type: str):
    """Returns ``(allowed, silent)``. ``silent`` is True when no grant rows exist
    for the key at all, so the facade may fall back to legacy."""
    conn = db.connect()
    try:
        grants = _svc._fetch_grants(conn, subject_type, _svc._sid(subject_id), key)
        if not grants:
            return (False, True)
        decision = _svc._resolve(grants, _svc._utc_now())
        return (decision["allowed"], False)
    finally:
        conn.close()


def _suggest_action(legacy: bool, canonical: bool) -> str:
    if legacy and not canonical:
        return "backfill_grant"  # canonical missing a grant legacy implies
    if canonical and not legacy:
        return "verify_canonical"  # canonical grants access legacy doesn't
    return "in_sync"


def shadow_compare(subject_id: Any, key: str, *, subject_type: str = "user",
                   context: Optional[dict] = None) -> dict:
    """Compare legacy vs canonical for one key WITHOUT changing access.

    Returns ``{legacy, canonical, differs, intended_winner, migration_action,
    mode, account_hold, account_status, effective_canonical}`` and writes a
    ``shadow_diff`` audit row. ``canonical`` is the raw grant-only answer (kept
    stable for existing callers); ``effective_canonical`` additionally applies
    the account-hold override so a suspended-but-premium account is visible in
    the recorded disagreement.
    """
    legacy = _legacy_opinion(subject_id, key)
    canonical = _svc.has_entitlement(subject_id, key, subject_type=subject_type)
    legacy_bool = bool(legacy) if legacy is not None else False
    differs = legacy_bool != canonical
    hold = _account_hold(subject_id, context)
    effective_canonical = False if hold["on_hold"] else canonical
    mode = get_mode()
    intended_winner = "canonical" if mode == MODE_CANONICAL else "legacy"
    action = _suggest_action(legacy_bool, effective_canonical)
    _record_shadow_diff(subject_type, subject_id, key, legacy, effective_canonical,
                        intended_winner=intended_winner, migration_action=action,
                        account_hold=hold)
    return {
        "legacy": legacy,
        "canonical": canonical,
        "differs": differs,
        "intended_winner": intended_winner,
        "migration_action": action,
        "mode": mode,
        "account_hold": hold["on_hold"],
        "account_status": hold["account_status"],
        "effective_canonical": effective_canonical,
    }
