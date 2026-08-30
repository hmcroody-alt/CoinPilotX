"""Milestone awards and reward cycles.

Two things happen when a referrer's qualified count moves: they may cross a
one-time milestone, and they may complete a repeatable reward cycle. Both are
idempotent, and they are idempotent in *different, independent* ways so that a
single bug cannot produce a double award.

Layered idempotency for cash
----------------------------
A $30 reward has to get past three separate locks to be paid twice:

1. ``progress_reward_cycles`` is UNIQUE on ``(campaign_id, user_id,
   cycle_index)``. Cycle 2 exists at most once, ever.
2. The cycle's ``reward_event_key`` is deterministic —
   ``FOUNDING_MEMBER_CHALLENGE_V1:441:cycle_2`` — and the rewards engine
   enforces UNIQUE on ``reward_events.event_key`` at the database level.
3. Cash rewards land ``pending`` and money only moves on an explicit,
   separately-audited approval + disbursal. Nothing in this module moves money.

That last point is worth stating plainly: **this module never calls the
ledger.** It records that a reward was *earned*. Turning earned into paid is a
deliberate human-authorized act through the existing rewards engine, which is
also why running these tests cannot move real money.

Milestones are one-time and never repeat
----------------------------------------
The repeat rule applies to cash only. Crossing 30 again at 60 does not re-issue
the Founding Member badge or re-grant Live eligibility — ``milestones_reached``
is evaluated against a UNIQUE award table, so a milestone at threshold 30 is
awarded on the first crossing and is thereafter a no-op.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from services import db

from . import campaign as campaign_mod
from . import qualification as qual
from .schema import ensure_schema


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _is_unique_violation(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("unique" in text and "constraint" in text) or "duplicate key" in text


def reward_event_key(campaign_id: str, user_id, cycle_index: int) -> str:
    """The deterministic key that makes a cycle payable exactly once."""
    return f"{campaign_id}:{int(user_id)}:cycle_{int(cycle_index)}"


# --- badge + entitlement side effects ---------------------------------------
def _award_badge(conn, user_id: int, badge_key: str, label: str = "",
                 description: str = "") -> bool:
    """Write to the canonical badge store. Never creates a second one."""
    if not badge_key:
        return False
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pulse_badges "
            "(badge_key, label, description, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (badge_key, label or badge_key,
             description or "PulseSoc Founding Path status.", _utcnow()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO pulse_user_badges "
            "(user_id, badge_key, granted_by, created_at) VALUES (?, ?, ?, ?)",
            (user_id, badge_key, 0, _utcnow()),
        )
        return True
    except Exception:
        # Badge table absent in this deployment (e.g. a lean test database).
        # The milestone award itself still stands; the badge is a projection.
        return False


def _grant_entitlement(user_id: int, key: str, reference: str) -> bool:
    if not key:
        return False
    try:
        from services.business_os.entitlements import service as ent_service
        ent_service.grant_entitlement(
            user_id, key, source="progress_milestone",
            source_reference=reference,
        )
        return True
    except Exception:
        return False


def _grant_live_access(conn, user_id: int, qualified: int) -> bool:
    """Project Live Creator into the existing server-authoritative Live gate.

    Suspensions and revocations always win. All other pre-unlock states may
    advance to eligible once the persisted milestone exists.
    """
    try:
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO livestream_access
            (user_id, status, referral_count, approved_by, suspended_reason,
             created_at, updated_at)
            VALUES (?, 'eligible', ?, 0, '', ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              status=CASE
                WHEN lower(COALESCE(livestream_access.status,'')) IN ('suspended','revoked')
                  THEN livestream_access.status
                ELSE 'eligible'
              END,
              referral_count=CASE
                WHEN COALESCE(livestream_access.referral_count,0) > excluded.referral_count
                  THEN livestream_access.referral_count
                ELSE excluded.referral_count
              END,
              updated_at=excluded.updated_at
            """,
            (user_id, int(qualified or 0), now, now),
        )
        return True
    except Exception:
        return False
# --- milestones -------------------------------------------------------------
def award_milestones(user_id, *, campaign_id: str = "", conn=None,
                     qualified: Optional[int] = None) -> dict:
    """Award every milestone the user has newly reached.

    Returns ``{"awarded": [keys], "already": [keys]}``. Safe to call on every
    qualification change; crossing the same threshold repeatedly awards once.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    if uid <= 0:
        return {"awarded": [], "already": []}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        count = (qualified if qualified is not None
                 else qual.qualified_count(uid, campaign_id=camp.campaign_id,
                                           conn=conn))
        existing = set()
        for r in conn.execute(
            "SELECT milestone_key FROM progress_milestone_awards "
            "WHERE campaign_id=? AND user_id=? AND revoked_at IS NULL",
            (camp.campaign_id, uid),
        ).fetchall():
            d = _row_to_dict(r) or {}
            if d.get("milestone_key"):
                existing.add(str(d["milestone_key"]))

        awarded, already = [], []
        for m in camp.milestones_reached(count):
            if m.key in existing:
                # Repair projections for awards written under an older campaign
                # version without creating a second achievement authority.
                _award_badge(conn, uid, m.badge_key, m.label, m.description)
                if m.entitlement_key:
                    _grant_entitlement(uid, m.entitlement_key,
                                       f"{camp.campaign_id}:{uid}:{m.key}")
                if m.key == "live_creator":
                    _grant_live_access(conn, uid, count)
                already.append(m.key)
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO progress_milestone_awards
                    (campaign_id, campaign_version, user_id, milestone_key,
                     threshold, qualified_count_snapshot, badge_key,
                     entitlement_key, earned_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (camp.campaign_id, camp.campaign_version, uid, m.key,
                     m.threshold, count, m.badge_key, m.entitlement_key,
                     _utcnow()),
                )
            except Exception as exc:
                if _is_unique_violation(exc):
                    already.append(m.key)
                    continue
                raise
            _award_badge(conn, uid, m.badge_key, m.label, m.description)
            if m.entitlement_key:
                _grant_entitlement(uid, m.entitlement_key,
                                   f"{camp.campaign_id}:{uid}:{m.key}")
            if m.key == "live_creator":
                _grant_live_access(conn, uid, count)
            qual._log_event(
                conn, camp.campaign_id, user_id=uid,
                event_type="milestone_earned", visibility="public",
                detail={"milestone": m.key, "label": m.label,
                        "threshold": m.threshold, "qualified": count},
                actor="progress_os",
            )
            awarded.append(m.key)

        if owned:
            conn.commit()
        return {"awarded": awarded, "already": already, "qualified": count}
    finally:
        if owned:
            conn.close()


def earned_milestones(user_id, *, campaign_id: str = "", conn=None) -> list:
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT milestone_key, threshold, earned_at FROM progress_milestone_awards "
            "WHERE campaign_id=? AND user_id=? AND revoked_at IS NULL",
            (camp.campaign_id, uid),
        ).fetchall()
        return [_row_to_dict(r) or {} for r in rows]
    finally:
        if owned:
            conn.close()


def has_milestone(user_id, milestone_key: str, *, campaign_id: str = "",
                  conn=None) -> bool:
    return any(m.get("milestone_key") == milestone_key
               for m in earned_milestones(user_id, campaign_id=campaign_id,
                                          conn=conn))


# --- reward cycles ----------------------------------------------------------
def sync_reward_cycles(user_id, *, campaign_id: str = "", conn=None,
                       qualified: Optional[int] = None) -> dict:
    """Record every completed reward cycle as an EARNED (unpaid) reward.

    Cycles are recorded from 1..n where n = qualified // reward_interval, so a
    user who jumps from 0 to 60 in one sweep gets cycle 1 and cycle 2 — one
    each, not two of the same. No money moves here.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    if uid <= 0:
        return {"created": [], "existing": [], "cycles": 0}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        count = (qualified if qualified is not None
                 else qual.qualified_count(uid, campaign_id=camp.campaign_id,
                                           conn=conn))
        target_cycles = camp.cycles_earned(count)

        existing = set()
        for r in conn.execute(
            "SELECT cycle_index FROM progress_reward_cycles "
            "WHERE campaign_id=? AND user_id=?",
            (camp.campaign_id, uid),
        ).fetchall():
            d = _row_to_dict(r) or {}
            existing.add(int(d.get("cycle_index") or 0))

        created = []
        for cycle in range(1, target_cycles + 1):
            if cycle in existing:
                continue
            key = reward_event_key(camp.campaign_id, uid, cycle)
            snapshot = cycle * camp.reward_interval
            try:
                conn.execute(
                    """
                    INSERT INTO progress_reward_cycles
                    (campaign_id, campaign_version, user_id, cycle_index,
                     qualified_count_snapshot, amount_cents, currency,
                     reward_event_key, status, evidence_json, earned_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (camp.campaign_id, camp.campaign_version, uid, cycle,
                     snapshot, camp.reward_amount_cents, camp.reward_currency,
                     key, json.dumps({"qualified_at_creation": count}),
                     _utcnow(), _utcnow()),
                )
            except Exception as exc:
                if _is_unique_violation(exc):
                    continue
                raise
            reward_id = _record_reward(key, uid, camp, snapshot)
            if reward_id:
                conn.execute(
                    "UPDATE progress_reward_cycles SET reward_id=?, updated_at=? "
                    "WHERE campaign_id=? AND user_id=? AND cycle_index=?",
                    (reward_id, _utcnow(), camp.campaign_id, uid, cycle),
                )
            qual._log_event(
                conn, camp.campaign_id, user_id=uid,
                event_type="reward_earned", visibility="public",
                detail={"cycle": cycle, "amount_cents": camp.reward_amount_cents,
                        "currency": camp.reward_currency},
                actor="progress_os",
            )
            created.append(cycle)

        if owned:
            conn.commit()
        return {"created": created, "existing": sorted(existing),
                "cycles": target_cycles, "qualified": count}
    finally:
        if owned:
            conn.close()


def _record_reward(event_key: str, user_id: int, camp, snapshot: int):
    """Hand the earned reward to the canonical rewards engine.

    Cash rewards land ``pending`` there by design — the engine never moves
    money at grant time. If the engine is unavailable we still keep our own
    cycle row, because losing the record of what someone earned is worse than
    a delayed hand-off, and the deterministic key makes the hand-off retryable.
    """
    try:
        from services.business_os.rewards import engine as rewards_engine
        rewards_engine.ensure_schema()
        result = rewards_engine.grant_reward(
            event_key=event_key,
            user_id=str(user_id),
            event_type="progress_referral_cycle",
            reward_kind="cash",
            amount=camp.reward_amount_cents,
            source=camp.campaign_id,
            details={"campaign_id": camp.campaign_id,
                     "campaign_version": camp.campaign_version,
                     "qualified_count_snapshot": snapshot},
            currency=camp.reward_currency,
        )
        reward = (result or {}).get("reward") or {}
        return reward.get("id")
    except Exception:
        return None


def reward_summary(user_id, *, campaign_id: str = "", conn=None) -> dict:
    """Earned / pending / available, reported from the reward authority.

    ``available`` is deliberately conservative: a cycle counts as available
    only once the rewards engine says it reached a paid state. Progress OS
    records what was *earned*; it does not get a vote on what is spendable.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        rows = [_row_to_dict(r) or {} for r in conn.execute(
            "SELECT cycle_index, amount_cents, currency, status, earned_at, "
            "reward_event_key, qualified_count_snapshot "
            "FROM progress_reward_cycles WHERE campaign_id=? AND user_id=? "
            "ORDER BY cycle_index",
            (camp.campaign_id, uid),
        ).fetchall()]

        statuses = _reward_statuses([r.get("reward_event_key") for r in rows])
        earned_cents = 0
        available_cents = 0
        pending_cents = 0
        history = []
        for r in rows:
            amount = int(r.get("amount_cents") or 0)
            status = statuses.get(r.get("reward_event_key")) or r.get("status") or "pending"
            earned_cents += amount
            if status == "disbursed":
                available_cents += amount
            else:
                pending_cents += amount
            history.append({
                "cycle": int(r.get("cycle_index") or 0),
                "amount_cents": amount,
                "currency": r.get("currency") or camp.reward_currency,
                "status": status,
                "earned_at": r.get("earned_at"),
                "qualified_count_snapshot": int(r.get("qualified_count_snapshot") or 0),
            })

        count = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        return {
            "currency": camp.reward_currency,
            "earned_cents": earned_cents,
            "pending_cents": pending_cents,
            "available_cents": available_cents,
            "cycles_completed": len(rows),
            "next_cycle": camp.next_cycle_progress(count),
            "reward_amount_cents": camp.reward_amount_cents,
            "history": history,
        }
    finally:
        if owned:
            conn.close()


def _reward_statuses(event_keys) -> dict:
    """Ask the rewards engine for the real status of each earned reward."""
    keys = [k for k in (event_keys or []) if k]
    if not keys:
        return {}
    out = {}
    try:
        from services.business_os.rewards import engine as rewards_engine
        for key in keys:
            try:
                reward = rewards_engine.get_reward(event_key=key)
                if reward:
                    out[key] = reward.get("status")
            except Exception:
                continue
    except Exception:
        return {}
    return out


# --- the one entry point ----------------------------------------------------
def sync(user_id, *, campaign_id: str = "", conn=None) -> dict:
    """Recompute Founding Path milestones for one referrer.

    Call this after any qualification change. Everything it does is idempotent,
    so calling it too often is merely wasted work — never a double award.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    if uid <= 0:
        return {"ok": False, "error": "invalid_user"}
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        count = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        ms = award_milestones(uid, campaign_id=camp.campaign_id, conn=conn,
                              qualified=count)
        rc = {"created": []}
        # Historical campaigns may still define reward cycles. Founding Path
        # deliberately defines zero, so a read or qualification can never
        # create a new cash award.
        if camp.reward_interval > 0 and camp.reward_amount_cents > 0:
            rc = sync_reward_cycles(uid, campaign_id=camp.campaign_id, conn=conn,
                                    qualified=count)
        if owned:
            conn.commit()
        return {"ok": True, "qualified": count,
                "milestones_awarded": ms.get("awarded", []),
                "cycles_created": rc.get("created", [])}
    finally:
        if owned:
            conn.close()
