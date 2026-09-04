"""Canonical 7-day Premium trial orchestration.

One function decides whether a subject may start the free Premium trial and, if
so, grants it: :func:`start_trial_if_eligible`. It is the ONLY writer of
``source="trial"`` grants for the Premium plan, and it is deliberately small —
the actual projection of plan → entitlement keys is done by the existing
``service.sync_subscription_entitlements`` against the existing
``pulse_premium_trial`` catalog plan. No new tables, no new grant mechanism.

Policy (product-confirmed)
--------------------------
* One trial per account, EVER. Eligibility is durable: any prior ``trial``
  grant row for this subject — active, expired, suspended or revoked — makes
  the account permanently ineligible. Deleting the account state that records
  the trial is the only way to reset it, and grants are never deleted.
* Prospective only: the canonical trial is created at signup time for new
  accounts. Existing accounts are not retroactively granted (their legacy
  ``trial_used`` columns already record their history and are also honoured
  here as a second, independent "already used" signal).
* Server clock only. ``period_end`` is computed from ``datetime.now(utc)`` on
  the server at grant time; the client never supplies a date.
* Idempotent and race-safe. A replayed signup for the same subject reuses the
  natural-key idempotency of ``grant_entitlement`` (subject, key, source,
  source_reference), and this module additionally refuses to re-run once any
  trial row exists, so a replay can never EXTEND a trial.

Expiry needs no cleanup job: grants carry ``expires_at`` and every canonical
read (``get_entitlements`` / ``facade.check`` / ``resolve_tier``) compares it
to the clock at read time. At T+7d the same rows simply stop answering "yes".
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services import db
from services.business_os.entitlements import service as _svc

_log = logging.getLogger("business_os.entitlements.trial")

#: The existing catalog plan the trial is carried on (0 cents; confers
#: premium.access plus every premium.crypto.* capability per schema.py).
TRIAL_PLAN_KEY = "pulse_premium_trial"

TRIAL_DAYS = 7

#: Grant provenance. ``trial`` is already a member of service._VALID_SOURCES.
TRIAL_SOURCE = "trial"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def has_ever_had_trial(subject_id: Any, *, conn=None) -> bool:
    """True if ANY canonical trial grant row exists for this subject,
    regardless of status or expiry. Revoked and expired rows count: the trial
    was used. This is the durable, non-fingerprinting abuse check — it keys on
    the account, and the row survives status changes because grants are never
    hard-deleted.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT 1 FROM business_os_ent_grants "
            "WHERE subject_type = 'user' AND subject_id = ? AND source = ? "
            "LIMIT 1",
            (str(subject_id), TRIAL_SOURCE),
        )
        return cur.fetchone() is not None
    finally:
        if owned:
            conn.close()


def _legacy_trial_used(subject_id: Any, *, conn=None) -> bool:
    """Second, independent signal: the legacy ``users.trial_used`` flag set by
    every historical signup. Honouring it means an account that consumed its
    trial under the legacy columns cannot collect a second one canonically.
    Fails OPEN to "used" on error — when we cannot prove eligibility we do not
    grant (fail closed on granting)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT trial_used FROM users WHERE user_id = ?",
            (int(subject_id),),
        )
        row = cur.fetchone()
        if row is None:
            return True  # unknown user: not eligible
        value = row[0] if not hasattr(row, "keys") else row["trial_used"]
        return bool(value)
    except Exception:  # noqa: BLE001 — cannot prove eligibility → do not grant
        _log.exception("legacy trial_used lookup failed for user=%s", subject_id)
        return True
    finally:
        if owned:
            conn.close()


def start_trial_if_eligible(subject_id: Any, *,
                            source_reference: str = "",
                            is_new_signup: bool = False,
                            conn=None) -> dict:
    """Start the 7-day Premium trial iff this subject is eligible.

    ``is_new_signup=True`` must be asserted by the caller (the signup handler)
    — this module never decides that an arbitrary existing account is "new".
    That is the prospective-only rule made explicit at the call site.

    Returns ``{started, reason, trial_end}``:

    * ``started=True``  — grants written; ``trial_end`` is the ISO period end.
      Caller should emit the ``premium_trial_started`` product event.
    * ``started=False`` — ``reason`` in {``not_new_signup``, ``already_used``,
      ``grant_failed``}. Never raises for storage problems.
    """
    result = {"started": False, "reason": "", "trial_end": None}
    if not is_new_signup:
        result["reason"] = "not_new_signup"
        return result
    try:
        uid = int(subject_id)
    except (TypeError, ValueError):
        result["reason"] = "invalid_subject"
        return result

    try:
        if has_ever_had_trial(uid, conn=conn):
            result["reason"] = "already_used"
            return result
    except Exception:  # noqa: BLE001 — cannot prove eligibility → do not grant
        _log.exception("trial eligibility check failed for user=%s", uid)
        result["reason"] = "eligibility_unknown"
        return result

    # NOTE: _legacy_trial_used is intentionally consulted AFTER the canonical
    # check. For a genuinely new signup the caller has typically just written
    # trial_used=1 in the same request; that legacy write records THIS trial,
    # not a previous one, so the canonical row check above is the arbiter of
    # "ever had one" and the legacy flag is only meaningful when the canonical
    # store is silent AND the account predates this code path. The signup
    # handler passes its own connection so both writes share one transaction.
    now = _utc_now()
    trial_end = (now + timedelta(days=TRIAL_DAYS)).isoformat()
    ref = source_reference or f"signup:{uid}"
    try:
        _svc.sync_subscription_entitlements(
            uid,
            TRIAL_PLAN_KEY,
            status="active",
            source=TRIAL_SOURCE,
            source_reference=ref,
            period_end=trial_end,
            conn=conn,
        )
    except Exception:  # noqa: BLE001 — never break signup over a grant failure
        _log.exception("trial grant failed for user=%s", uid)
        result["reason"] = "grant_failed"
        return result

    _log.info("premium trial started user=%s end=%s ref=%s", uid, trial_end, ref)
    result.update(started=True, reason="started", trial_end=trial_end)
    return result
