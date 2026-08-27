"""Milestone awards for the Founding Path.

When a referrer's certified-invite count moves they may cross a rung of the
ladder. Crossing a rung grants a badge and, where the rung maps to one, an
existing entitlement. Nothing here is monetary: the Founding Path awards
status and capability only, and this module has no path that reaches a
ledger, a reward engine or a payout.

Milestones are one-time and never repeat
----------------------------------------
``milestones_reached`` is evaluated against a UNIQUE award table, so a rung is
awarded on the first crossing and is thereafter a no-op. Passing 30 again at
60 does not re-issue the Founding Member badge or re-grant Live eligibility.
"""

from __future__ import annotations

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


# --- badge + entitlement side effects ---------------------------------------
def _award_badge(conn, user_id: int, badge_key: str) -> bool:
    """Write to the canonical badge store. Never creates a second one."""
    if not badge_key:
        return False
    try:
        conn.execute(
            "INSERT OR IGNORE INTO pulse_user_badges "
            "(user_id, badge_key, granted_by, created_at) VALUES (?, ?, ?, ?)",
            (user_id, badge_key, "progress_os", _utcnow()),
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
            _award_badge(conn, uid, m.badge_key)
            if m.entitlement_key:
                _grant_entitlement(uid, m.entitlement_key,
                                   f"{camp.campaign_id}:{uid}:{m.key}")
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
        awards = award_milestones(uid, campaign_id=camp.campaign_id, conn=conn,
                                  qualified=count)
        if owned:
            conn.commit()
        return {"ok": True, "qualified": count,
                "milestones_awarded": awards.get("awarded", [])}
    finally:
        if owned:
            conn.close()
