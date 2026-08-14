"""Admin review and control surface for Progress OS.

What this module deliberately does not offer
--------------------------------------------
There is no ``set_qualified(True)``, no ``set_progress(30)``, no
``grant_reward(user)``. Those shapes are the reason referral programs get
quietly drained from the inside: a single generic write endpoint turns every
compromised or careless admin session into an unlimited cash printer, and it
leaves an audit trail that says "someone set a number" rather than "someone
decided a thing".

Instead the surface exposes a small, closed set of *named decisions*:

``approve_qualification``  Release a review hold; the state machine then
                           re-derives the outcome from real facts. Approval
                           unblocks evaluation — it does not assert a result,
                           so an approved referral that never posted twice
                           still does not qualify.
``reject_qualification``   Record a confirmed-abuse finding. This is the only
                           path to DISQUALIFIED from human judgement.
``restore_qualification``  Undo a rejection when review was wrong.
``hold_reward``            Pause an earned reward pending investigation.
``release_reward``         Lift the pause.
``revoke_milestone``       Withdraw an achievement obtained fraudulently.

Every one of them requires a ``reason`` and an ``actor``, refuses to run
without both, and writes an audit row before returning. The reason is not
decoration: a review action without a stated reason is indistinguishable from
an accident, and six months later nobody can tell which it was.

Approval cannot manufacture value
---------------------------------
Note the asymmetry with ``qualification.set_risk_state``. An automated signal
may pause but not award; an admin may unpause but still not award. Only the
evidence — profile, two posting days, standing — awards. The strongest thing
any human here can do is remove an obstacle and let the facts speak.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from services import db

from . import campaign as campaign_mod
from . import milestones as ms
from . import qualification as qual
from .schema import ensure_schema

#: Reward statuses this surface may set. Payment states (``approved``,
#: ``disbursing``, ``disbursed``) are absent on purpose — those belong to the
#: rewards engine and its own separately-audited approval path. Progress OS
#: can stop a payment; it cannot start one.
REWARD_HOLD = "on_hold"
REWARD_PENDING = "pending"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _require(actor: str, reason: str) -> Optional[tuple]:
    """Both or nothing. An unattributed, unexplained action is not permitted."""
    if not str(actor or "").strip():
        return 400, {"ok": False, "error": "actor_required"}
    if len(str(reason or "").strip()) < 3:
        return 400, {"ok": False, "error": "reason_required"}
    return None


def _audit(conn, campaign_id: str, *, action: str, actor: str, reason: str,
           subject_user_id=None, referrer_user_id=None,
           detail: Optional[dict] = None) -> None:
    """Write the private audit row, and mirror to Sentinel when available.

    The local row is written first and unconditionally. Sentinel is a
    best-effort mirror: if the security bus is down, the decision must still be
    recorded here rather than vanishing because a downstream service blinked.
    """
    payload = dict(detail or {})
    payload["reason"] = reason
    qual._log_event(
        conn, campaign_id,
        user_id=int(referrer_user_id or 0),
        subject_user_id=subject_user_id,
        event_type=f"admin_{action}",
        visibility="private",
        detail=payload,
        actor=actor,
    )
    try:
        from services.sentinel import events as sentinel_events
        sentinel_events.ingest(sentinel_events.Event(
            category="ADMIN",
            event_type=f"progress.{action}",
            severity="medium",
            actor_id=str(actor),
            source="progress_os",
            subject_type="user",
            subject_id=str(subject_user_id or referrer_user_id or ""),
            payload=payload,
            source_system="pulsesoc",
            source_component="progress_admin",
        ))
    except Exception:
        pass


# --- review queue -----------------------------------------------------------
def review_queue(*, campaign_id: str = "", limit: int = 100) -> tuple:
    """Referrals awaiting a human decision, oldest first.

    Oldest first because a review queue sorted by newest quietly buries the
    cases that have been waiting longest — which are exactly the people whose
    reward is being withheld while nobody looks.
    """
    camp = campaign_mod.get(campaign_id)
    limit = max(1, min(int(limit or 100), 500))
    conn = db.connect()
    try:
        ensure_schema(conn)
        rows = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT referrer_user_id, referred_user_id, state, risk_state, "
            "review_reason, posting_days, profile_completed, updated_at "
            "FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND state=? ORDER BY updated_at ASC LIMIT ?",
            (camp.campaign_id, qual.REVIEW_REQUIRED, limit),
        ).fetchall()]
        return 200, {"ok": True, "count": len(rows), "queue": rows}
    finally:
        conn.close()


def inspect_referral(referred_user_id, *, campaign_id: str = "") -> tuple:
    """Everything an operator needs to make a defensible decision.

    Unlike the user-facing checklist, this surface *does* carry the risk state
    and its reason — an operator cannot judge a case whose evidence is hidden
    from them. What it still does not carry is a verdict: no score, no
    recommendation, no "likely fraud" label. The operator reads facts and
    decides; the system does not decide and ask them to click yes.
    """
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    if referred <= 0:
        return 400, {"ok": False, "error": "invalid_user"}
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT * FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}

        days = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT day_key, first_post_id, recorded_at FROM progress_posting_days "
            "WHERE campaign_id=? AND user_id=? ORDER BY day_key",
            (camp.campaign_id, referred),
        ).fetchall()]
        history = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT event_type, detail_json, actor, created_at FROM progress_events "
            "WHERE campaign_id=? AND subject_user_id=? ORDER BY id DESC LIMIT 50",
            (camp.campaign_id, referred),
        ).fetchall()]
        account = _row_to_dict(conn.execute(
            "SELECT account_status, onboarding_complete, created_at "
            "FROM users WHERE user_id=?", (referred,),
        ).fetchone()) or {}

        return 200, {
            "ok": True,
            "qualification": row,
            "posting_day_evidence": days,
            "account": account,
            "history": history,
        }
    finally:
        conn.close()


# --- qualification decisions ------------------------------------------------
def approve_qualification(referred_user_id, *, actor: str, reason: str,
                          campaign_id: str = "") -> tuple:
    """Clear a review hold and let the state machine re-derive the outcome.

    This does not set QUALIFIED. It sets risk back to clear and re-evaluates;
    if the person genuinely never posted on two days, the result is still not
    qualified. An admin can vouch for someone's legitimacy — not for facts that
    did not happen.
    """
    bad = _require(actor, reason)
    if bad:
        return bad
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT referrer_user_id, state FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}
        referrer = int(row.get("referrer_user_id") or 0)

        conn.execute(
            "UPDATE progress_referral_qualifications "
            "SET risk_state=?, review_reason=?, disqualified_reason=NULL, updated_at=? "
            "WHERE campaign_id=? AND referred_user_id=?",
            (qual.RISK_CLEAR, "", _utcnow(), camp.campaign_id, referred),
        )
        result = qual.evaluate(referred, campaign_id=camp.campaign_id, conn=conn,
                               actor=actor)
        _audit(conn, camp.campaign_id, action="approve_qualification",
               actor=actor, reason=reason, subject_user_id=referred,
               referrer_user_id=referrer,
               detail={"previous_state": row.get("state"),
                       "resulting_state": result.get("state")})
        conn.commit()
        _resync(referrer, camp.campaign_id)
        return 200, {"ok": True, "state": result.get("state")}
    finally:
        conn.close()


def reject_qualification(referred_user_id, *, actor: str, reason: str,
                         campaign_id: str = "") -> tuple:
    """Record a confirmed finding of abuse. Reversible via ``restore``."""
    bad = _require(actor, reason)
    if bad:
        return bad
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT referrer_user_id, state FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}
        referrer = int(row.get("referrer_user_id") or 0)

        conn.execute(
            "UPDATE progress_referral_qualifications "
            "SET state=?, risk_state=?, review_reason=?, disqualified_reason=?, "
            "qualified_at=NULL, updated_at=? "
            "WHERE campaign_id=? AND referred_user_id=?",
            (qual.DISQUALIFIED, qual.RISK_BLOCKED, reason, "admin_rejected",
             _utcnow(), camp.campaign_id, referred),
        )
        _audit(conn, camp.campaign_id, action="reject_qualification",
               actor=actor, reason=reason, subject_user_id=referred,
               referrer_user_id=referrer,
               detail={"previous_state": row.get("state")})
        conn.commit()
        _resync(referrer, camp.campaign_id)
        return 200, {"ok": True, "state": qual.DISQUALIFIED}
    finally:
        conn.close()


def restore_qualification(referred_user_id, *, actor: str, reason: str,
                          campaign_id: str = "") -> tuple:
    """Undo a rejection. Re-derives from facts rather than restoring a snapshot.

    Restoring a remembered previous state would reinstate whatever was true
    before the rejection, including staleness. Re-deriving means the restored
    outcome is correct as of now.
    """
    bad = _require(actor, reason)
    if bad:
        return bad
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT referrer_user_id, state FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}
        referrer = int(row.get("referrer_user_id") or 0)

        conn.execute(
            "UPDATE progress_referral_qualifications "
            "SET state=?, risk_state=?, review_reason=?, disqualified_reason=NULL, "
            "updated_at=? WHERE campaign_id=? AND referred_user_id=?",
            (qual.SIGNED_UP, qual.RISK_CLEAR, "", _utcnow(),
             camp.campaign_id, referred),
        )
        result = qual.evaluate(referred, campaign_id=camp.campaign_id, conn=conn,
                               actor=actor)
        _audit(conn, camp.campaign_id, action="restore_qualification",
               actor=actor, reason=reason, subject_user_id=referred,
               referrer_user_id=referrer,
               detail={"previous_state": row.get("state"),
                       "resulting_state": result.get("state")})
        conn.commit()
        _resync(referrer, camp.campaign_id)
        return 200, {"ok": True, "state": result.get("state")}
    finally:
        conn.close()


# --- reward decisions -------------------------------------------------------
def hold_reward(user_id, cycle_index, *, actor: str, reason: str,
                campaign_id: str = "") -> tuple:
    """Pause an earned reward pending investigation.

    Holds the Progress OS cycle row and, separately, asks the rewards engine to
    mark its own record under review. Two systems, two independent brakes: a
    hold that only existed here would be bypassed by anyone approving the
    reward directly in the rewards console.
    """
    bad = _require(actor, reason)
    if bad:
        return bad
    return _set_reward_status(user_id, cycle_index, REWARD_HOLD, actor=actor,
                              reason=reason, campaign_id=campaign_id,
                              action="hold_reward")


def release_reward(user_id, cycle_index, *, actor: str, reason: str,
                   campaign_id: str = "") -> tuple:
    """Lift a hold, returning the cycle to ``pending``.

    Returning to ``pending`` and not to ``approved`` is the point: releasing a
    Progress OS hold restores the reward's eligibility to be approved, it does
    not approve it. Payment approval stays where it belongs.
    """
    bad = _require(actor, reason)
    if bad:
        return bad
    return _set_reward_status(user_id, cycle_index, REWARD_PENDING, actor=actor,
                              reason=reason, campaign_id=campaign_id,
                              action="release_reward")


def _set_reward_status(user_id, cycle_index, status: str, *, actor: str,
                       reason: str, campaign_id: str, action: str) -> tuple:
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    cycle = int(cycle_index or 0)
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT status, reward_event_key, reward_id FROM progress_reward_cycles "
            "WHERE campaign_id=? AND user_id=? AND cycle_index=?",
            (camp.campaign_id, uid, cycle),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}

        conn.execute(
            "UPDATE progress_reward_cycles SET status=?, updated_at=? "
            "WHERE campaign_id=? AND user_id=? AND cycle_index=?",
            (status, _utcnow(), camp.campaign_id, uid, cycle),
        )
        _mirror_reward_fraud_state(row.get("reward_id"), status,
                                   actor=actor, reason=reason)
        _audit(conn, camp.campaign_id, action=action, actor=actor, reason=reason,
               referrer_user_id=uid,
               detail={"cycle_index": cycle, "previous_status": row.get("status"),
                       "status": status})
        conn.commit()
        return 200, {"ok": True, "cycle_index": cycle, "status": status}
    finally:
        conn.close()


def _mirror_reward_fraud_state(reward_id, status: str, *, actor: str,
                               reason: str) -> None:
    """Mirror the hold onto the canonical reward row.

    Only ``review`` and ``clear`` are ever sent. ``blocked`` is deliberately
    unreachable from here because the engine turns it into ``denied`` — killing
    someone's reward outright is a decision that belongs in the rewards console
    with its own audit, not a side effect of a Progress OS hold.

    Releasing is safe: the engine auto-grants on ``clear`` only for
    ``pulse_credits``. A cash reward stays ``pending`` and still needs the
    separate approval + disbursal path, so nothing here can move money.
    """
    if not reward_id:
        return
    try:
        from services.business_os.rewards import engine as rewards_engine
        rewards_engine.set_fraud_state(
            int(reward_id),
            "review" if status == REWARD_HOLD else "clear",
            actor,
            note=reason,
        )
    except Exception:
        # The local hold already stands. A failed mirror must not roll back the
        # brake that was successfully applied.
        pass


# --- milestone decisions ----------------------------------------------------
def revoke_milestone(user_id, milestone_key: str, *, actor: str, reason: str,
                     campaign_id: str = "") -> tuple:
    """Withdraw an achievement obtained fraudulently.

    Soft revocation: the row stays with ``revoked_at`` set rather than being
    deleted. A deleted award leaves no evidence that it ever existed, which
    makes an appeal impossible to adjudicate and a mistaken revocation
    impossible to notice.
    """
    bad = _require(actor, reason)
    if bad:
        return bad
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    key = str(milestone_key or "").strip()
    conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT id, revoked_at, badge_key FROM progress_milestone_awards "
            "WHERE campaign_id=? AND user_id=? AND milestone_key=?",
            (camp.campaign_id, uid, key),
        ).fetchone())
        if not row:
            return 404, {"ok": False, "error": "not_found"}
        if row.get("revoked_at"):
            return 200, {"ok": True, "milestone_key": key, "already_revoked": True}

        conn.execute(
            "UPDATE progress_milestone_awards SET revoked_at=?, revoked_reason=? "
            "WHERE campaign_id=? AND user_id=? AND milestone_key=?",
            (_utcnow(), reason, camp.campaign_id, uid, key),
        )
        badge = row.get("badge_key")
        if badge:
            try:
                conn.execute(
                    "DELETE FROM pulse_user_badges WHERE user_id=? AND badge_key=? "
                    "AND granted_by='progress_os'",
                    (uid, badge),
                )
            except Exception:
                pass
        _audit(conn, camp.campaign_id, action="revoke_milestone", actor=actor,
               reason=reason, referrer_user_id=uid,
               detail={"milestone_key": key, "badge_key": badge})
        conn.commit()
        return 200, {"ok": True, "milestone_key": key, "revoked": True}
    finally:
        conn.close()


# --- referrer overview ------------------------------------------------------
def inspect_referrer(user_id, *, campaign_id: str = "") -> tuple:
    """One referrer's whole picture: counts, milestones, cycles, holds."""
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    if uid <= 0:
        return 400, {"ok": False, "error": "invalid_user"}
    conn = db.connect()
    try:
        ensure_schema(conn)
        counts = qual.breakdown(uid, campaign_id=camp.campaign_id, conn=conn)
        qualified = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        awards = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT milestone_key, threshold, earned_at, revoked_at, revoked_reason "
            "FROM progress_milestone_awards WHERE campaign_id=? AND user_id=?",
            (camp.campaign_id, uid),
        ).fetchall()]
        cycles = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT cycle_index, amount_cents, currency, status, "
            "reward_event_key, qualified_count_snapshot, earned_at "
            "FROM progress_reward_cycles WHERE campaign_id=? AND user_id=? "
            "ORDER BY cycle_index",
            (camp.campaign_id, uid),
        ).fetchall()]
        return 200, {
            "ok": True,
            "user_id": uid,
            "qualified": qualified,
            "breakdown": counts,
            "expected_cycles": camp.cycles_earned(qualified),
            "milestones": awards,
            "reward_cycles": cycles,
        }
    finally:
        conn.close()


def _resync(referrer_user_id: int, campaign_id: str) -> None:
    """Recompute the referrer's milestones and cycles after a decision.

    Runs on its own connection after the decision has committed, so a resync
    failure can never roll back the audited decision that caused it.
    """
    if int(referrer_user_id or 0) <= 0:
        return
    try:
        ms.sync(referrer_user_id, campaign_id=campaign_id)
    except Exception:
        pass


def reconcile(*, campaign_id: str = "", limit: int = 500) -> dict:
    """Bounded safety-net sweep.

    Event hooks are the primary path; this exists because hooks are missed —
    a crashed worker, a code path added later that forgets to call, a row
    written by a migration. It is bounded so it cannot become an unbounded
    table scan in production, and it is idempotent so running it twice is
    indistinguishable from running it once.
    """
    camp = campaign_mod.get(campaign_id)
    limit = max(1, min(int(limit or 500), 5000))
    conn = db.connect()
    changed, referrers = 0, set()
    try:
        ensure_schema(conn)
        rows = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT referred_user_id, referrer_user_id FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND state NOT IN (?, ?) "
            "ORDER BY updated_at ASC LIMIT ?",
            (camp.campaign_id, qual.DISQUALIFIED, qual.EXPIRED, limit),
        ).fetchall()]
        for r in rows:
            result = qual.evaluate(int(r.get("referred_user_id") or 0),
                                   campaign_id=camp.campaign_id, conn=conn,
                                   actor="reconcile")
            if result.get("changed"):
                changed += 1
                referrers.add(int(r.get("referrer_user_id") or 0))
        conn.commit()
    finally:
        conn.close()
    for referrer in referrers:
        _resync(referrer, camp.campaign_id)
    return {"ok": True, "examined": len(rows), "changed": changed,
            "referrers_resynced": len(referrers)}
