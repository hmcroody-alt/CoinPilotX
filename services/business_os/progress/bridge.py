"""The seam between Progress OS and the rest of PulseSoc.

Everything the monolith needs to call lives here, so ``bot.py`` gains thin
delegations instead of program logic, and the integration points are auditable
in one file.

The defect this module closes
-----------------------------
``record_referral_signup`` writes ``referral_conversions`` with ``counted=1``
**at signup**. ``pulse_referral_status_for_user`` counts exactly those rows and
returns them as ``completed``. That number is passed to
``privilege_engine.get_user_privileges``, where ``referral_count >= 30``
unlocks Live. Composed, those three facts mean **thirty bare signups unlock
Live Creator** — no profile, no post, no person. That is the farm the Founding
Member Challenge exists to prevent, and it predates this program.

``qualified_referral_count`` replaces that arithmetic at its source. Attribution
stays canonical; only the decision about whether an attributed signup *counts*
moves here.

Why nobody loses access
-----------------------
Switching the count would strip Live from people who unlocked it under the old
rule. Silently revoking a privilege someone already has and may already be
using is not an acceptable way to fix our own bug.

``grandfather_legacy_live_access`` handles that once per user, lazily: on the
first read after the switch, a user with no explicit access row but ≥30 legacy
counted signups gets a real ``livestream_access`` row stamped
``approved_by=0`` with a legacy reason. This is strictly *more* honest than the
status quo — it converts a privilege that was being silently recomputed from a
number into an explicit, dated, attributable grant — and it means the new rule
governs only new progress.

Fail-closed on grants, fail-open on access
------------------------------------------
If Progress OS is unavailable, ``qualified_referral_count`` returns 0 rather
than falling back to the legacy count, because a fallback would quietly reopen
the hole precisely when the system is least healthy. Existing creators are
unaffected: their access lives in the explicit ``livestream_access`` row, not
in the count.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import campaign as campaign_mod
from . import milestones as ms
from . import qualification as qual
from .schema import ensure_schema


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


# --- the live-eligibility seam ---------------------------------------------
def qualified_referral_count(cur, user_id, *, campaign_id: str = "") -> int:
    """The number ``pulse_referral_status_for_user`` should report.

    Fails closed to 0. A count is an assertion that someone earned something;
    when the system cannot verify that assertion the honest answer is zero, not
    the old unverified number.
    """
    try:
        return qual.qualified_count(user_id, campaign_id=campaign_id, conn=cur)
    except Exception:
        return 0


def legacy_signup_count(cur, user_id) -> int:
    """The pre-Progress-OS count: attributed signups, nothing more.

    Kept only to decide grandfathering and to explain the difference to the
    user. It is never returned as progress.
    """
    uid = int(user_id or 0)
    try:
        row = _row_to_dict(cur.execute(
            "SELECT COUNT(*) AS total FROM referral_conversions "
            "WHERE inviter_user_id=? AND counted=1 AND COALESCE(fraud_flag,0)=0",
            (uid,),
        ).fetchone()) or {}
        return int(row.get("total") or 0)
    except Exception:
        return 0


def grandfather_legacy_live_access(cur, user_id, legacy_count: int,
                                   *, campaign_id: str = "") -> bool:
    """Preserve Live access earned under the old rule, once, explicitly.

    Idempotent by the table's PRIMARY KEY on ``user_id``: a user who already
    has any access row — eligible, approved, suspended — is left completely
    alone. In particular a *suspended* creator is never resurrected by this.
    """
    uid = int(user_id or 0)
    if uid <= 0 or int(legacy_count or 0) < 30:
        return False
    try:
        existing = cur.execute(
            "SELECT user_id FROM livestream_access WHERE user_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if existing:
            return False
        now = _utcnow()
        cur.execute(
            "INSERT INTO livestream_access "
            "(user_id, status, referral_count, approved_by, suspended_reason, "
            " created_at, updated_at) VALUES (?, 'eligible', ?, 0, NULL, ?, ?)",
            (uid, int(legacy_count), now, now),
        )
    except Exception:
        return False
    try:
        camp = campaign_mod.get(campaign_id)
        qual.log_event(
            camp.campaign_id, uid, "live_access_grandfathered",
            visibility="private",
            detail={"legacy_signup_count": int(legacy_count),
                    "reason": "earned_under_pre_progress_os_rules"},
            actor="progress_os", conn=cur,
        )
    except Exception:
        pass
    return True


def referral_status(cur, user_id, *, campaign_id: str = "") -> dict:
    """The payload behind ``pulse_referral_status_for_user``'s counts.

    Returns qualified progress plus enough context for the UI to explain the
    gap between "people who joined" and "people who count" without the client
    having to compute anything.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    legacy = legacy_signup_count(cur, uid)
    grandfathered = grandfather_legacy_live_access(cur, uid, legacy,
                                                   campaign_id=camp.campaign_id)
    qualified = qualified_referral_count(cur, uid, campaign_id=camp.campaign_id)
    return {
        "completed": qualified,
        "qualified": qualified,
        "invited": legacy,
        "required": camp.qualification_target,
        "remaining": max(0, camp.qualification_target - qualified),
        "grandfathered": grandfathered,
    }


# --- event hooks ------------------------------------------------------------
# Each hook is a no-op-on-failure delegation. Progress OS is a program layered
# on top of the product; a bug in it must never break a signup, a post, or a
# profile save. The reconciliation sweep in ``admin_api`` exists to repair
# anything a swallowed exception here drops.

def on_referral_signup(referrer_user_id, referred_user_id, *,
                       referral_code: str = "", campaign_id: str = "") -> None:
    """Called when a signup is attributed to a referrer."""
    try:
        result = qual.attribute(referrer_user_id, referred_user_id,
                                referral_code=referral_code,
                                campaign_id=campaign_id)
        if result.get("ok") and not result.get("duplicate"):
            qual.evaluate(referred_user_id, campaign_id=campaign_id)
    except Exception:
        pass


def on_post_created(user_id, *, post_id=None, created_at: str = "",
                    campaign_id: str = "") -> None:
    """Called after a post is durably written.

    Records the posting day, then re-evaluates the poster's own referral (if
    they were referred) and resyncs their referrer's milestones and cycles. A
    post is the event that most often completes a qualification, so this is
    where the challenge actually advances.
    """
    try:
        qual.record_posting_day(user_id, posted_at=created_at, post_id=post_id,
                                campaign_id=campaign_id)
    except Exception:
        return
    _reevaluate_and_resync(user_id, campaign_id=campaign_id)


def on_profile_completed(user_id, *, campaign_id: str = "") -> None:
    _reevaluate_and_resync(user_id, campaign_id=campaign_id)


def on_account_status_changed(user_id, *, campaign_id: str = "") -> None:
    """Suspension or deletion can revoke a qualification, so resync downward too."""
    _reevaluate_and_resync(user_id, campaign_id=campaign_id)


def _reevaluate_and_resync(referred_user_id, *, campaign_id: str = "") -> None:
    """Re-derive one referral, then resync whoever referred them.

    Both halves are idempotent, so the hook firing twice — or firing after the
    reconciliation sweep already handled the same change — costs a little work
    and changes nothing.
    """
    try:
        from services import db
    except Exception:
        return
    referrer = 0
    try:
        conn = db.connect()
    except Exception:
        return
    try:
        ensure_schema(conn)
        camp = campaign_mod.get(campaign_id)
        row = _row_to_dict(conn.execute(
            "SELECT referrer_user_id FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, int(referred_user_id or 0)),
        ).fetchone()) or {}
        referrer = int(row.get("referrer_user_id") or 0)
        if referrer:
            qual.evaluate(referred_user_id, campaign_id=camp.campaign_id,
                          conn=conn)
            conn.commit()
    except Exception:
        return
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if referrer:
        try:
            ms.sync(referrer, campaign_id=campaign_id)
        except Exception:
            pass
