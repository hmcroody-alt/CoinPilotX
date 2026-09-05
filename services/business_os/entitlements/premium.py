"""Canonical Premium membership resolution.

This module is the ONE place that answers "is this subject a PulseSoc Premium
member, and why?". Everything else — the legacy
``services.premium_entitlement_service`` shim, the identity/badge engine, the
status centre, the admin console — resolves through here.

Why this module exists
----------------------
The forensic inventory found **four** places that independently decided premium
truth:

* **A — legacy** ``services/premium_entitlement_service.py``: the
  ``user_entitlements`` / ``premium_entitlements`` / ``founder_memberships``
  tables.
* **B — canonical** ``business_os_ent_grants`` (this package).
* **C — identity columns** on ``users``: ``premium_status``,
  ``lifetime_premium``, ``premium_glow_manual_grant``, ``premium_mark_override``,
  ``subscription_status``, ``is_pro``, ``plan``. Read directly by
  ``services/premium_identity_engine.py`` to decide badges.
* **D — session flag** ``has_premium_access`` computed at login.

Four authorities means a user can be Premium in one and not another
("split-brain"). This module does not add a fifth: it *reads* A and C, resolves
B as canonical, and reports the disagreement explicitly so the split can be
measured and closed before cutover rather than discovered in production.

Precedence
----------
``resolve()`` mirrors the facade's mode flag (``BUSINESS_OS_ENTITLEMENTS``):

* ``off``       — legacy (A, which itself consults C) is authoritative. Zero
  behaviour change; canonical is computed only when explicitly requested.
* ``shadow``    — legacy authoritative, canonical computed and diffed.
* ``canonical`` — canonical (B) authoritative, with a logged legacy fallback
  when canonical is silent, and an account hold overriding any grant.

An account hold (suspended/disabled) always beats a paid grant.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from services import db
from services.business_os.entitlements import facade as _facade
from services.business_os.entitlements import service as _svc

_log = logging.getLogger("business_os.entitlements.premium")

# --- canonical vocabulary ---------------------------------------------------
#: Umbrella membership key. Granted by every plan that confers Premium.
PREMIUM_ACCESS = "premium.access"

#: Capability keys that Premium plans confer. Membership (`PREMIUM_ACCESS`) is
#: deliberately separate from capability so a capability can be added or removed
#: from a plan without redefining who is a member.
PREMIUM_CAPABILITIES = (
    "premium.profile.customization",
    "premium.media.higher_quality",
    "premium.undx.advanced",
    # Crypto intelligence. These are capabilities of the EXISTING Premium
    # membership, not a separate crypto product: no new plan, no new SKU. A
    # capability added here is presented by the Status Center automatically
    # (``premium_api._PRESENTED``), so it must also be registered in
    # ``facade._LEGACY_READERS`` — otherwise the default ``off`` mode has no
    # legacy opinion for the key and denies it to every paying member.
    "premium.crypto.advanced_alerts",
    "premium.crypto.portfolio",
    "premium.crypto.intelligence",
    # Unlocks the ability to SUBMIT a Blue Check verification request. It does
    # not grant the badge and carries no weight in the review: a reviewer still
    # decides on the evidence. Membership buys the application, never the
    # outcome — see NOT_VERIFICATION in premium_api.
    "premium.verification.blue_check.apply",
    # NOTE: premium.crypto.portfolio_intelligence is deliberately NOT listed
    # here. It is the crypto branch's single-key spelling of what
    # premium.crypto.portfolio and premium.crypto.intelligence split in two, and
    # it exists only so the call sites using that name resolve. It is registered
    # where registration actually matters — facade._LEGACY_READERS, so the
    # default ``off`` mode has an opinion and does not deny it, and schema.py's
    # per-plan rows, so a purchase grants it. Neither reads this tuple.
    #
    # This tuple is the *presentation* list: everything in it becomes
    # premium_api._PRESENTED and is advertised as a benefit on the Premium
    # screen. An alias listed here would sell one capability to one member twice
    # under two names. That is what test_every_advertised_benefit_has_native_copy
    # catches by demanding a translated label for it; the honest response is not
    # to write the label but to stop advertising the alias.
)

#: Plans that confer Premium membership, in the catalog's vocabulary.
PREMIUM_PLAN_KEYS = (
    "pulse_premium_monthly",
    "pulse_premium_annual",
    "pulse_premium_trial",
    "pulse_premium_grandfathered",
    "pulse_business_monthly",
)

#: The plan a Founder is carried on. Founders are grandfathered members: their
#: grant never expires and is never re-priced. This is an EXISTING catalog plan;
#: founders are not given a bespoke plan of their own.
FOUNDER_PLAN_KEY = "pulse_premium_grandfathered"

#: Founder locked price, in cents. Mirrors the legacy
#: ``premium_entitlement_service.FOUNDER_PRICE_CENTS`` and must not drift.
FOUNDER_PRICE_CENTS = 499


# --- legacy (authority A) readers -------------------------------------------
# These call the legacy module's RAW readers, never its public shimmed API, so
# that once ``is_premium_user`` delegates to canonical the parity comparison
# still compares two genuinely independent answers. Comparing canonical against
# a shim that itself calls canonical would always report "in sync" and would
# hide exactly the split-brain this module exists to surface.
def _legacy_module():
    try:
        from services import premium_entitlement_service as _pes
        return _pes
    except Exception:  # noqa: BLE001 — legacy must never break resolution
        _log.exception("legacy premium_entitlement_service unavailable")
        return None


def legacy_premium(user_id: Any) -> Optional[bool]:
    """Authority A's answer, read from the legacy tables. None if unavailable."""
    mod = _legacy_module()
    if mod is None:
        return None
    raw = getattr(mod, "_is_premium_user_raw", None) or getattr(mod, "is_premium_user", None)
    if raw is None:
        return None
    try:
        return bool(raw(int(user_id)))
    except Exception:  # noqa: BLE001
        _log.exception("legacy premium read failed user=%s", user_id)
        return None


def legacy_founder(user_id: Any) -> Optional[bool]:
    """Authority A's founder answer, read from ``founder_memberships``."""
    mod = _legacy_module()
    if mod is None:
        return None
    raw = getattr(mod, "_is_founder_member_raw", None) or getattr(mod, "is_founder_member", None)
    if raw is None:
        return None
    try:
        return bool(raw(int(user_id)))
    except Exception:  # noqa: BLE001
        _log.exception("legacy founder read failed user=%s", user_id)
        return None


# --- identity columns (authority C) -----------------------------------------
# This tuple is not a convenience list — it is the input to
# ``has_active_premium``, and any column it omits is a column that reader sees
# as NULL. That is how the trial divergence happened: the reader's trial branch
# consults trial_end_date, pro_expires_at and subscription_expires_at, none of
# which were selected here, so a member inside an open trial had the door opened
# by the access authority and the badge withheld by this one. Same row, two
# answers. If a column is added to a branch of has_active_premium, it belongs
# here in the same change.
# Kept as a flat literal on purpose: tests/test_users_schema_columns_sql.py
# parses this assignment with ``ast`` and checks every name against the real
# ``users`` DDL. Building it from concatenated tuples turns the node into a
# BinOp the guard cannot read, and the guard silently stops covering this list.
_USER_COLUMNS = (
    "premium_status", "subscription_status", "lifetime_premium",
    "premium_glow_manual_grant", "premium_mark_override", "premium_expires_at",
    "is_pro", "plan", "subscription_plan",
    "trial_end_date", "pro_expires_at", "subscription_expires_at",
)

#: The columns added for the trial/expiry branches. Split out so a schema that
#: predates them can be retried without them rather than losing every column.
_EXPIRY_COLUMNS = ("trial_end_date", "pro_expires_at", "subscription_expires_at")

_BASE_USER_COLUMNS = tuple(c for c in _USER_COLUMNS if c not in _EXPIRY_COLUMNS)

# founder_number/founder_status are not columns on ``users``; they live on
# founder_memberships, whose ``status`` the rest of the codebase aliases to
# founder_status. has_active_premium() reads both, so they have to be joined
# in rather than dropped.
_FOUNDER_COLUMNS = ("founder_number", "founder_status")

_IDENTITY_COLUMNS = _USER_COLUMNS + _FOUNDER_COLUMNS


def _select_identity(user_id: Any, columns: tuple) -> Any:
    conn = db.connect()
    try:
        cur = conn.execute(
            f"SELECT {', '.join('u.' + c for c in columns)}, "
            "fm.founder_number AS founder_number, "
            "fm.status AS founder_status "
            "FROM users u "
            "LEFT JOIN founder_memberships fm "
            "ON fm.user_id = u.user_id AND fm.status = 'active' "
            "WHERE u.user_id = ? LIMIT 1",
            (int(user_id),),
        )
        return cur.fetchone()
    finally:
        conn.close()


def identity_row(user_id: Any) -> dict:
    """Read the premium-bearing columns on ``users``. Empty dict when absent."""
    columns = _USER_COLUMNS
    try:
        row = _select_identity(user_id, columns)
    except Exception:  # noqa: BLE001 — missing columns on older schemas
        # Degrade one field, not the whole answer. A single absent expiry column
        # used to take the entire SELECT down, and returning {} here does not
        # mean "not premium" — it means authority C abstains, which drops a real
        # badge for every member on that schema. Retrying without the expiry
        # columns keeps the answer this function gave before they were added.
        try:
            columns = _BASE_USER_COLUMNS
            row = _select_identity(user_id, columns)
        except Exception:  # noqa: BLE001
            _log.debug("identity columns unavailable for user=%s", user_id)
            return {}
    if row is None:
        return {}
    keys = columns + _FOUNDER_COLUMNS
    try:
        return {k: row[k] for k in keys}
    except (TypeError, IndexError, KeyError):
        return dict(zip(keys, tuple(row)))


def identity_premium(user_id: Any, row: Optional[dict] = None) -> Optional[bool]:
    """Authority C's answer, derived from the ``users`` columns.

    Delegates to ``premium_identity_engine.has_active_premium`` so there is ONE
    definition of the column-derived answer rather than a reimplementation that
    can drift from the badge the user actually sees.
    """
    if row is None:
        row = identity_row(user_id)
    if not row:
        return None
    try:
        from services import premium_identity_engine as _pie
        return bool(_pie.has_active_premium(row))
    except Exception:  # noqa: BLE001
        _log.exception("identity premium read failed user=%s", user_id)
        return None


# --- canonical (authority B) ------------------------------------------------
def canonical_premium(user_id: Any, *, subject_type: str = "user") -> tuple[bool, bool]:
    """Authority B's answer as ``(allowed, silent)``.

    ``silent`` is True when no grant rows exist for ``premium.access`` at all,
    which is what licenses a legacy fallback rather than a hard denial.
    """
    conn = db.connect()
    try:
        grants = _svc._fetch_grants(conn, subject_type, _svc._sid(user_id), PREMIUM_ACCESS)
        if not grants:
            return (False, True)
        return (_svc._resolve(grants, _svc._utc_now())["allowed"], False)
    finally:
        conn.close()


def canonical_mode(user_id: Any, *, subject_type: str = "user") -> str:
    """The winning grant phase for membership: active|grace|grandfathered|
    suspended|revoked|none. Drives the Status Centre's wording."""
    conn = db.connect()
    try:
        grants = _svc._fetch_grants(conn, subject_type, _svc._sid(user_id), PREMIUM_ACCESS)
        if not grants:
            return "none"
        return _svc._resolve(grants, _svc._utc_now())["mode"]
    finally:
        conn.close()


# --- unified resolution -----------------------------------------------------
def resolve(user_id: Any, *, subject_type: str = "user",
            context: Optional[dict] = None) -> dict:
    """The single premium answer, with the full derivation attached.

    Returns a dict with:

    ``is_premium``      the authoritative boolean for the current mode
    ``source``          which authority decided (canonical_grant | legacy |
                        legacy_fallback | account_hold)
    ``membership_mode`` canonical grant phase (active/grace/grandfathered/...)
    ``flag_mode``       off | shadow | canonical
    ``account_hold``    True when a suspension overrides any grant
    ``authorities``     per-authority raw answers, for parity reporting
    ``split_brain``     True when the authorities disagree
    """
    mode = _facade.get_mode()
    legacy = legacy_premium(user_id)
    legacy_bool = bool(legacy) if legacy is not None else False
    # Read once and reuse: identity_premium() would otherwise re-query, and the
    # denial reason has to be derived from the SAME row the answer came from.
    row = identity_row(user_id)
    identity = identity_premium(user_id, row=row) if row else None

    if mode == _facade.MODE_OFF:
        # Byte-for-byte current behaviour: legacy decides, canonical untouched.
        return _state(
            is_premium=legacy_bool, source="legacy", membership_mode="legacy",
            flag_mode=mode, account_hold=False, account_status=None,
            legacy=legacy, canonical=None, identity=identity, identity_row=row,
        )

    hold = _facade.account_hold(user_id, context)
    canonical_allowed, canonical_silent = canonical_premium(
        user_id, subject_type=subject_type)

    if mode == _facade.MODE_SHADOW:
        # Access unchanged; canonical is computed only to be measured.
        return _state(
            is_premium=legacy_bool, source="legacy", membership_mode="legacy",
            flag_mode=mode, account_hold=hold["on_hold"],
            account_status=hold["account_status"], legacy=legacy,
            canonical=None if canonical_silent else canonical_allowed,
            identity=identity, identity_row=row,
        )

    # MODE_CANONICAL
    if hold["on_hold"]:
        return _state(
            is_premium=False, source="account_hold",
            membership_mode=hold["reason"], flag_mode=mode, account_hold=True,
            account_status=hold["account_status"], legacy=legacy,
            canonical=None if canonical_silent else canonical_allowed,
            identity=identity, identity_row=row,
        )
    if canonical_silent:
        if legacy is not None:
            _log.info("premium legacy_fallback user=%s legacy=%s", user_id, legacy)
        return _state(
            is_premium=legacy_bool, source="legacy_fallback",
            membership_mode="legacy_fallback", flag_mode=mode,
            account_hold=False, account_status=hold["account_status"],
            legacy=legacy, canonical=None, identity=identity, identity_row=row,
        )
    return _state(
        is_premium=canonical_allowed, source="canonical_grant",
        membership_mode=canonical_mode(user_id, subject_type=subject_type),
        flag_mode=mode, account_hold=False,
        account_status=hold["account_status"], legacy=legacy,
        canonical=canonical_allowed, identity=identity, identity_row=row,
    )


# --- observability vocabulary ------------------------------------------------
# A closed enum, safe to log and to ship to a client. Every value describes WHY
# the resolver answered as it did, and none of them can carry a receipt, a
# transaction identifier, a token, or any billing payload — the reason is a
# label, never a copy of the evidence.
REASON_ACTIVE_TRIAL = "ACTIVE_TRIAL"
REASON_ACTIVE_SUBSCRIPTION = "ACTIVE_SUBSCRIPTION"
REASON_ACTIVE_HIGHER_TIER = "ACTIVE_HIGHER_TIER"
REASON_EXPIRED = "EXPIRED"
REASON_REVOKED = "REVOKED"
REASON_ACCOUNT_HOLD = "ACCOUNT_HOLD"
REASON_NO_ENTITLEMENT = "NO_ENTITLEMENT"

REASONS = (
    REASON_ACTIVE_TRIAL, REASON_ACTIVE_SUBSCRIPTION, REASON_ACTIVE_HIGHER_TIER,
    REASON_EXPIRED, REASON_REVOKED, REASON_ACCOUNT_HOLD, REASON_NO_ENTITLEMENT,
)


def legacy_denial_reason(row: Optional[dict]) -> str:
    """Why a legacy-column denial happened: EXPIRED vs NO_ENTITLEMENT.

    The legacy reader returns a bare boolean, so the reason has to be recovered
    from the same columns it read. A recorded period end that has passed is
    evidence of expiry; no recorded end at all is evidence of nothing ever
    having been granted. Anything ambiguous reports NO_ENTITLEMENT rather than
    guessing EXPIRED — a label is only useful if it is never confidently wrong.
    """
    row = row or {}
    try:
        from services import premium_identity_engine as _pie
        for col in ("premium_expires_at", "subscription_expires_at", "pro_expires_at"):
            if row.get(col):
                return (REASON_EXPIRED if _pie.period_ended(row.get(col))
                        else REASON_NO_ENTITLEMENT)
    except Exception:  # noqa: BLE001 — a label must never break resolution
        _log.debug("legacy denial reason unavailable")
    return REASON_NO_ENTITLEMENT


def _reason_for(*, is_premium: bool, source: str, membership_mode: str,
                identity_row: Optional[dict] = None) -> str:
    """Map a resolved state onto the closed reason enum."""
    mode = str(membership_mode or "")
    if is_premium:
        if mode == "grandfathered":
            return REASON_ACTIVE_HIGHER_TIER
        if mode == "trial":
            return REASON_ACTIVE_TRIAL
        return REASON_ACTIVE_SUBSCRIPTION
    if source == "account_hold":
        return REASON_ACCOUNT_HOLD
    if mode == "revoked":
        return REASON_REVOKED
    if mode == "suspended":
        return REASON_ACCOUNT_HOLD
    if mode in ("legacy", "legacy_fallback"):
        return legacy_denial_reason(identity_row)
    return REASON_NO_ENTITLEMENT


def _state(*, is_premium: bool, source: str, membership_mode: str,
           flag_mode: str, account_hold: bool, account_status,
           legacy: Optional[bool], canonical: Optional[bool],
           identity: Optional[bool], identity_row: Optional[dict] = None) -> dict:
    answers = [a for a in (legacy, canonical, identity) if a is not None]
    return {
        "is_premium": bool(is_premium),
        "source": source,
        "reason": _reason_for(is_premium=bool(is_premium), source=source,
                              membership_mode=membership_mode,
                              identity_row=identity_row),
        "membership_mode": membership_mode,
        "flag_mode": flag_mode,
        "account_hold": bool(account_hold),
        "account_status": account_status,
        "authorities": {
            "legacy": legacy,
            "canonical": canonical,
            "identity_columns": identity,
        },
        "split_brain": len(set(bool(a) for a in answers)) > 1,
    }


def is_premium(user_id: Any, *, subject_type: str = "user",
               context: Optional[dict] = None) -> bool:
    """Boolean convenience wrapper over :func:`resolve`."""
    return resolve(user_id, subject_type=subject_type, context=context)["is_premium"]


# --- parity -----------------------------------------------------------------
def parity(user_id: Any, *, subject_type: str = "user") -> dict:
    """Compare all three readable authorities for one user WITHOUT changing access.

    This is the pre-cutover safety check: it must show zero disagreements before
    ``BUSINESS_OS_ENTITLEMENTS`` is moved to ``canonical``, otherwise flipping the
    flag would silently strip Premium from users who only exist in legacy.
    """
    legacy = legacy_premium(user_id)
    identity = identity_premium(user_id)
    canonical_allowed, canonical_silent = canonical_premium(
        user_id, subject_type=subject_type)
    legacy_bool = bool(legacy) if legacy is not None else False
    return {
        "user_id": str(user_id),
        "legacy": legacy,
        "identity_columns": identity,
        "canonical": canonical_allowed,
        "canonical_silent": canonical_silent,
        "differs": legacy_bool != canonical_allowed,
        "action": _parity_action(legacy_bool, canonical_allowed, canonical_silent),
    }


def _parity_action(legacy: bool, canonical: bool, silent: bool) -> str:
    if legacy and silent:
        return "backfill_grant"      # legacy-only member; canonical must learn them
    if legacy and not canonical:
        return "repair_grant"        # canonical has a grant but it is not granting
    if canonical and not legacy:
        return "verify_canonical"    # canonical grants access legacy does not
    return "in_sync"


def parity_report(*, subject_type: str = "user", limit: int = 5000) -> dict:
    """Sweep every user who is Premium under ANY authority and report the split.

    Returns counts plus the individual disagreements (capped by ``limit``) so an
    operator can see exactly how many accounts a cutover would affect.
    """
    ids: set[str] = set()
    conn = db.connect()
    try:
        for sql in (
            "SELECT DISTINCT subject_id FROM business_os_ent_grants "
            "WHERE subject_type = ? AND entitlement_key = ?",
        ):
            try:
                cur = conn.execute(sql, (subject_type, PREMIUM_ACCESS))
                ids.update(str(r[0]) for r in cur.fetchall())
            except Exception:  # noqa: BLE001
                _log.debug("canonical grant sweep unavailable")
        for sql in (
            "SELECT DISTINCT user_id FROM founder_memberships WHERE status='active'",
            "SELECT DISTINCT user_id FROM user_entitlements WHERE status='active'",
            "SELECT DISTINCT user_id FROM premium_entitlements WHERE status='active'",
            "SELECT user_id FROM users WHERE premium_status IN ('active','trialing') "
            "OR lifetime_premium = 1 OR premium_glow_manual_grant = 1",
        ):
            try:
                cur = conn.execute(sql)
                ids.update(str(r[0]) for r in cur.fetchall())
            except Exception:  # noqa: BLE001 — table may not exist yet
                _log.debug("parity sweep skipped a source: %s", sql.split()[3:5])
    finally:
        conn.close()

    counts = {"in_sync": 0, "backfill_grant": 0, "repair_grant": 0,
              "verify_canonical": 0}
    diffs = []
    for uid in sorted(ids):
        row = parity(uid, subject_type=subject_type)
        counts[row["action"]] = counts.get(row["action"], 0) + 1
        if row["action"] != "in_sync" and len(diffs) < limit:
            diffs.append(row)
    return {
        "subjects_examined": len(ids),
        "counts": counts,
        "safe_to_cut_over": counts.get("backfill_grant", 0) == 0
        and counts.get("repair_grant", 0) == 0,
        "differences": diffs,
    }


# --- founder allocation policy ----------------------------------------------
# The audit finding: legacy allocation used ``MAX(founder_number) + 1`` with NO
# cap, NO closed flag and NO cutoff date. Sequential numbering is NOT a cap — it
# only guarantees uniqueness, not scarcity. Founder membership was therefore
# effectively unlimited and open, despite being sold as a founding cohort at a
# locked lifetime price.
#
# The policy below makes the allocation state EXPLICIT and auditable. It is
# default-open so that enabling this code changes nothing until an operator
# deliberately sets a limit; the point is that the state is now declared and
# reportable rather than an accident of a MAX() query.
_ENV_LIMIT = "PULSE_FOUNDER_ALLOCATION_LIMIT"
_ENV_CLOSED = "PULSE_FOUNDER_ALLOCATION_CLOSED"


def founder_allocation_limit() -> Optional[int]:
    """Configured maximum number of founders, or None for uncapped."""
    raw = (os.getenv(_ENV_LIMIT, "") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s is not an integer: %r (treating as uncapped)", _ENV_LIMIT, raw)
        return None
    return value if value > 0 else None


def founder_allocation_closed() -> bool:
    """True when founder allocation has been administratively closed."""
    return (os.getenv(_ENV_CLOSED, "") or "").strip().lower() in {
        "1", "true", "yes", "on"}


def founder_count() -> int:
    """Number of founder numbers ISSUED, including revoked memberships.

    Counts issued numbers rather than active ones on purpose: a revoked founder
    still consumes their number permanently (numbers are never reused), so an
    active-only count would let the cohort grow past its cap through churn.
    """
    conn = db.connect()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM founder_memberships")
        return int(cur.fetchone()[0] or 0)
    except Exception:  # noqa: BLE001
        return 0
    finally:
        conn.close()


def founder_allocation_status() -> dict:
    """Explicit, reportable state of the founder cohort."""
    limit = founder_allocation_limit()
    closed = founder_allocation_closed()
    issued = founder_count()
    remaining = None if limit is None else max(0, limit - issued)
    if closed:
        state = "closed"
    elif limit is None:
        state = "open_uncapped"
    elif remaining == 0:
        state = "exhausted"
    else:
        state = "open_capped"
    return {
        "state": state,
        "issued": issued,
        "limit": limit,
        "remaining": remaining,
        "closed": closed,
        "price_cents": FOUNDER_PRICE_CENTS,
        "plan_key": FOUNDER_PLAN_KEY,
        "accepting_new": state in {"open_uncapped", "open_capped"},
    }


def founder_allocation_available() -> tuple[bool, str]:
    """``(allowed, reason)`` for issuing one more founder membership."""
    status = founder_allocation_status()
    if status["closed"]:
        return (False, "founder_allocation_closed")
    if status["state"] == "exhausted":
        return (False, "founder_allocation_exhausted")
    return (True, "")
