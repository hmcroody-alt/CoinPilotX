"""Referral qualification: the state machine that decides what a referral is worth.

The rule this module exists to enforce
--------------------------------------
Before Progress OS, ``pulse_referral_status_for_user`` counted rows in
``referral_conversions`` where ``counted=1``. ``record_referral_signup`` writes
that row, with ``counted=1``, **at signup time** — before the invited person has
completed a profile, posted anything, or proved they are a person. That count
feeds ``privilege_engine.get_user_privileges``, where ``referral_count >= 30``
unlocks Live. In other words the pre-existing system unlocked Live Creator for
thirty bare signups.

This module replaces that arithmetic with earned qualification. It does not
replace the *attribution* — ``users.referred_by`` and ``referral_conversions``
remain the canonical record of who invited whom, and this module reads them.
What is new is the decision about whether an attributed signup counts.

Server authority
----------------
Every input is read from a server-owned source: ``users.onboarding_complete``
for profile completion, ``pulse_posts.created_at`` for posting days,
``users.account_status`` for standing. Nothing here trusts a client claim, and
there is no code path by which a request body can set a state.

Signals are not verdicts
------------------------
Risk state is advisory. A shared IP, a shared device, or a household of family
members is *normal* and must never by itself cost someone a reward. Risk can
move a referral to REVIEW_REQUIRED, which pauses payment pending a human — it
cannot silently mark it DISQUALIFIED. Only confirmed, durable facts (a deleted
account, a confirmed-abuse suspension) disqualify.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from services import db

from . import campaign as campaign_mod
from .schema import ensure_schema

# --- states -----------------------------------------------------------------
INVITED = "INVITED"
ATTRIBUTED = "ATTRIBUTED"
SIGNED_UP = "SIGNED_UP"
PROFILE_COMPLETED = "PROFILE_COMPLETED"
POSTED_DAY_1 = "POSTED_DAY_1"
POSTED_DAY_2 = "POSTED_DAY_2"
QUALIFICATION_PENDING = "QUALIFICATION_PENDING"
QUALIFIED = "QUALIFIED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
DISQUALIFIED = "DISQUALIFIED"
EXPIRED = "EXPIRED"

#: Only this state contributes to a referrer's qualified count. Everything
#: else — including REVIEW_REQUIRED — is worth zero until it resolves.
COUNTING_STATES = (QUALIFIED,)

#: Terminal states are never recomputed back into progress by the evaluator.
#: DISQUALIFIED is reachable only from confirmed facts, never from a signal.
TERMINAL = (DISQUALIFIED, EXPIRED)

RISK_CLEAR = "clear"
RISK_REVIEW = "review"
RISK_BLOCKED = "blocked"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _day_key(timestamp: str) -> str:
    """The calendar day of a server timestamp, as ``YYYY-MM-DD``.

    Day bucketing is done in UTC deliberately. Using the viewer's local
    timezone would make the same two posts qualify or not depending on who is
    looking, and would hand a user a trivial way to manufacture a second
    "day" by changing device timezone around midnight.
    """
    raw = str(timestamp or "").strip()
    if not raw:
        return ""
    # Accept both "YYYY-MM-DD HH:MM:SS" and ISO-8601 with T/offset.
    return raw.replace("T", " ")[:10]


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


#: What counts as a qualifying post, strictest form first.
#:
#: Reposts are excluded. A repost is a single tap with no authored content,
#: which makes it the cheapest possible way to fake activity — a script can
#: manufacture a "posting day" for a hundred accounts in a minute. Original
#: posts and reels count; so does going live, because that is *harder* than a
#: text post, not easier, and excluding it would penalise creators who mainly
#: broadcast.
#:
#: The second query is a fallback for deployments predating the repost columns.
#: Degrading to "count everything" is the right failure direction here: being
#: slightly too generous during a partial rollout is recoverable, whereas
#: reporting zero days would strip qualifications from people who earned them.
_QUALIFYING_POST_QUERIES = (
    "SELECT created_at FROM pulse_posts "
    "WHERE user_id=? AND deleted_at IS NULL "
    "AND COALESCE(moderation_status,'') NOT IN ('rejected','removed') "
    "AND COALESCE(post_type,'') <> 'repost' "
    "AND repost_of_post_id IS NULL",
    "SELECT created_at FROM pulse_posts "
    "WHERE user_id=? AND deleted_at IS NULL "
    "AND COALESCE(moderation_status,'') NOT IN ('rejected','removed')",
)


# --- attribution ------------------------------------------------------------
def attribute(referrer_user_id, referred_user_id, *, referral_code: str = "",
              campaign_id: str = "", conn=None) -> dict:
    """Pin one referred person to one referrer for one campaign.

    Returns ``{"ok": bool, "duplicate": bool, "state": str}``.

    A second attempt to claim the same referred user — whether a replayed
    signup callback or a genuinely competing referrer — does NOT overwrite the
    first. The UNIQUE constraint on ``(campaign_id, referred_user_id)`` makes
    first-write-wins a property of the database rather than a race.
    """
    camp = campaign_mod.get(campaign_id)
    referrer = int(referrer_user_id or 0)
    referred = int(referred_user_id or 0)
    if referrer <= 0 or referred <= 0:
        return {"ok": False, "duplicate": False, "error": "invalid_user"}
    if referrer == referred:
        # Self-referral is not a fraud signal to be weighed; it is arithmetic
        # that cannot be true.
        return {"ok": False, "duplicate": False, "error": "self_referral"}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        existing = _row_to_dict(conn.execute(
            "SELECT referrer_user_id, state FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if existing:
            return {
                "ok": True,
                "duplicate": True,
                "state": existing.get("state"),
                "referrer_user_id": existing.get("referrer_user_id"),
            }
        now = _utcnow()
        conn.execute(
            """
            INSERT INTO progress_referral_qualifications
            (campaign_id, campaign_version, referrer_user_id, referred_user_id,
             referral_code, state, signed_up, attributed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (camp.campaign_id, camp.campaign_version, referrer, referred,
             referral_code or "", SIGNED_UP, now, now, now),
        )
        if owned:
            conn.commit()
        return {"ok": True, "duplicate": False, "state": SIGNED_UP,
                "referrer_user_id": referrer}
    except Exception as exc:
        # A unique violation here means a concurrent writer won the race.
        # That is the correct outcome, not an error worth surfacing.
        if _is_unique_violation(exc):
            existing = _row_to_dict(conn.execute(
                "SELECT referrer_user_id, state FROM progress_referral_qualifications "
                "WHERE campaign_id=? AND referred_user_id=?",
                (camp.campaign_id, referred),
            ).fetchone()) or {}
            return {"ok": True, "duplicate": True,
                    "state": existing.get("state"),
                    "referrer_user_id": existing.get("referrer_user_id")}
        raise
    finally:
        if owned:
            conn.close()


def _is_unique_violation(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("unique" in text and "constraint" in text) or "duplicate key" in text


# --- posting days -----------------------------------------------------------
def record_posting_day(user_id, *, posted_at: str = "", post_id=None,
                       campaign_id: str = "", conn=None) -> dict:
    """Record that ``user_id`` posted on the calendar day of ``posted_at``.

    Append-only and idempotent. Two posts on the same day collapse to one row
    because of the UNIQUE constraint, which is the entire "two separate days"
    rule expressed as a schema fact rather than a conditional someone can
    forget to write.

    Recording is also *durable against deletion*: once a real post existed on
    a day, deleting it later does not strip the earned day. That direction
    matters more than it looks — otherwise a referred user could be talked
    into deleting their posts to sabotage someone else's reward.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    day = _day_key(posted_at or _utcnow())
    if uid <= 0 or not day:
        return {"ok": False, "recorded": False}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        existing = conn.execute(
            "SELECT id FROM progress_posting_days "
            "WHERE campaign_id=? AND user_id=? AND day_key=?",
            (camp.campaign_id, uid, day),
        ).fetchone()
        if existing:
            return {"ok": True, "recorded": False, "day_key": day}
        conn.execute(
            """
            INSERT INTO progress_posting_days
            (campaign_id, user_id, day_key, first_post_id, recorded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (camp.campaign_id, uid, day, int(post_id or 0) or None, _utcnow()),
        )
        if owned:
            conn.commit()
        return {"ok": True, "recorded": True, "day_key": day}
    except Exception as exc:
        if _is_unique_violation(exc):
            return {"ok": True, "recorded": False, "day_key": day}
        raise
    finally:
        if owned:
            conn.close()


def posting_day_count(user_id, *, campaign_id: str = "", conn=None) -> int:
    """Distinct qualifying posting days for a user.

    Takes the union of recorded evidence and the live ``pulse_posts`` table so
    that the rule holds for accounts that posted before Progress OS existed —
    a backfill that reads the authoritative timestamps rather than assuming
    history began at deploy time.

    Observing a live post *persists* the day it proves. Without that write the
    durability guarantee in ``record_posting_day`` would be true only for days
    some hook happened to catch: a day seen through this union would silently
    disappear the moment the post was deleted. Persisting on observation is what
    actually makes the count monotonic, and it costs nothing extra because the
    UNIQUE constraint already collapses repeats.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    if uid <= 0:
        return 0
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        recorded = set()
        try:
            rows = conn.execute(
                "SELECT day_key FROM progress_posting_days "
                "WHERE campaign_id=? AND user_id=?",
                (camp.campaign_id, uid),
            ).fetchall()
            for r in rows:
                d = _row_to_dict(r) or {}
                if d.get("day_key"):
                    recorded.add(str(d["day_key"]))
        except Exception:
            pass

        observed = set()
        for sql in _QUALIFYING_POST_QUERIES:
            try:
                rows = conn.execute(sql, (uid,)).fetchall()
            except Exception:
                # Older deployment without the repost columns: fall through to
                # the looser query rather than reporting zero days, which would
                # wrongly strip qualifications during a partial rollout.
                continue
            for r in rows:
                d = _row_to_dict(r) or {}
                key = _day_key(d.get("created_at") or "")
                if key:
                    observed.add(key)
            break

        for day in sorted(observed - recorded):
            try:
                conn.execute(
                    """
                    INSERT INTO progress_posting_days
                    (campaign_id, user_id, day_key, first_post_id, recorded_at)
                    VALUES (?, ?, ?, NULL, ?)
                    """,
                    (camp.campaign_id, uid, day, _utcnow()),
                )
            except Exception as exc:
                # A concurrent writer got there first; the day is recorded
                # either way, which is all this loop is trying to achieve.
                if not _is_unique_violation(exc):
                    raise
        if owned:
            conn.commit()
        return len(recorded | observed)
    finally:
        if owned:
            conn.close()


# --- authoritative fact reads ----------------------------------------------
def _profile_completed(conn, user_id: int) -> bool:
    """Reuses the canonical onboarding definition; does not invent a second one."""
    try:
        row = _row_to_dict(conn.execute(
            "SELECT onboarding_complete FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()) or {}
        return bool(int(row.get("onboarding_complete") or 0))
    except Exception:
        return False


def _standing(conn, user_id: int) -> dict:
    """Confirmed account standing.

    Deliberately narrow. Only states that are already *confirmed decisions*
    about the account disqualify: deletion, and a suspension/ban that an
    operator or the safety system has actually applied. Open reports,
    suspicion, and risk scores are not consulted here — they route to review.
    """
    try:
        row = _row_to_dict(conn.execute(
            "SELECT account_status, access_enabled FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone())
    except Exception:
        row = None
    if row is None:
        return {"exists": False, "good": False, "reason": "account_missing"}
    status = str(row.get("account_status") or "active").strip().lower()
    if status in {"deleted", "removed"}:
        return {"exists": True, "good": False, "reason": "account_deleted"}
    if status in {"suspended", "banned", "disabled"}:
        return {"exists": True, "good": False, "reason": "account_suspended"}
    return {"exists": True, "good": True, "reason": ""}


# --- evaluation -------------------------------------------------------------
def evaluate(referred_user_id, *, campaign_id: str = "", conn=None,
             actor: str = "system") -> dict:
    """Recompute one referral's state from authoritative sources.

    Idempotent and safe to call from any event (signup, profile save, post
    create, account status change) as well as from a bounded reconciliation
    sweep. It never advances a terminal state and never trusts its own
    previous output — every call re-derives from the source tables, so a
    corrupted or hand-edited state row self-heals on the next event.
    """
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    if referred <= 0:
        return {"ok": False, "error": "invalid_user"}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT * FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return {"ok": False, "error": "not_attributed"}

        previous_state = str(row.get("state") or ATTRIBUTED)
        if previous_state in TERMINAL:
            return {"ok": True, "state": previous_state, "changed": False}

        standing = _standing(conn, referred)
        profile_ok = _profile_completed(conn, referred)
        days = posting_day_count(referred, campaign_id=camp.campaign_id, conn=conn)
        risk = str(row.get("risk_state") or RISK_CLEAR)

        # --- decide -------------------------------------------------------
        if not standing["good"]:
            state = DISQUALIFIED
            disqualified_reason = standing["reason"]
        else:
            disqualified_reason = None
            if not profile_ok:
                state = SIGNED_UP
            elif days <= 0:
                state = PROFILE_COMPLETED
            elif days < camp.required_posting_days:
                state = POSTED_DAY_1
            else:
                # All product requirements met. Risk decides whether that
                # becomes a payable fact now or waits for a human.
                if risk == RISK_BLOCKED:
                    state = DISQUALIFIED
                    disqualified_reason = "risk_blocked"
                elif risk == RISK_REVIEW:
                    state = REVIEW_REQUIRED
                else:
                    state = QUALIFIED

        now = _utcnow()
        qualified_at = row.get("qualified_at")
        if state == QUALIFIED and not qualified_at:
            qualified_at = now

        conn.execute(
            """
            UPDATE progress_referral_qualifications
            SET state=?, signed_up=1, profile_completed=?, posting_days=?,
                good_standing=?, qualified_at=?, disqualified_reason=?, updated_at=?
            WHERE campaign_id=? AND referred_user_id=?
            """,
            (state, int(profile_ok), int(days), int(standing["good"]),
             qualified_at, disqualified_reason, now,
             camp.campaign_id, referred),
        )

        changed = state != previous_state
        if changed:
            _log_event(
                conn, camp.campaign_id,
                user_id=int(row.get("referrer_user_id") or 0),
                subject_user_id=referred,
                event_type=f"referral_{state.lower()}",
                visibility="public" if state in {QUALIFIED, POSTED_DAY_1} else "private",
                detail={"from": previous_state, "to": state,
                        "posting_days": days, "profile_completed": profile_ok},
                actor=actor,
            )
        if owned:
            conn.commit()
        return {"ok": True, "state": state, "changed": changed,
                "posting_days": days, "profile_completed": profile_ok,
                "good_standing": standing["good"]}
    finally:
        if owned:
            conn.close()


def qualified_count(referrer_user_id, *, campaign_id: str = "", conn=None) -> int:
    """The authoritative number of qualified referrals for a referrer.

    This is the single number the whole program is built on: milestones,
    reward cycles and Live eligibility all read it. It counts only rows in
    ``QUALIFIED``. A referral under review contributes nothing — absence of a
    decision is not a decision in the user's favour when money is downstream.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(referrer_user_id or 0)
    if uid <= 0:
        return 0
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        placeholders = ",".join("?" for _ in COUNTING_STATES)
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM progress_referral_qualifications "
            f"WHERE campaign_id=? AND referrer_user_id=? AND state IN ({placeholders})",
            (camp.campaign_id, uid, *COUNTING_STATES),
        ).fetchone()
        return int((_row_to_dict(row) or {}).get("total") or 0)
    finally:
        if owned:
            conn.close()


def breakdown(referrer_user_id, *, campaign_id: str = "", conn=None) -> dict:
    """Counts per state, for the overview's "why isn't this higher" panel."""
    camp = campaign_mod.get(campaign_id)
    uid = int(referrer_user_id or 0)
    out = {"qualified": 0, "needs_another_day": 0, "hasnt_posted": 0,
           "getting_started": 0, "in_review": 0, "not_counted": 0}
    if uid <= 0:
        return out
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT state, COUNT(*) AS total FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referrer_user_id=? GROUP BY state",
            (camp.campaign_id, uid),
        ).fetchall()
        for r in rows:
            d = _row_to_dict(r) or {}
            state = str(d.get("state") or "")
            total = int(d.get("total") or 0)
            if state == QUALIFIED:
                out["qualified"] += total
            elif state == POSTED_DAY_1:
                out["needs_another_day"] += total
            elif state == PROFILE_COMPLETED:
                out["hasnt_posted"] += total
            elif state in {SIGNED_UP, ATTRIBUTED, INVITED}:
                out["getting_started"] += total
            elif state == REVIEW_REQUIRED:
                out["in_review"] += total
            else:
                out["not_counted"] += total
        return out
    finally:
        if owned:
            conn.close()


# --- user-safe explanation --------------------------------------------------
def checklist(referred_user_id, *, campaign_id: str = "", conn=None) -> dict:
    """The referral-detail checklist, in language safe to show a user.

    This surface is intentionally incapable of leaking security internals.
    It reports only product requirements — profile, posting days, standing —
    and collapses every risk nuance into a single neutral "under review" line.
    IP addresses, device fingerprints, correlation scores and Sentinel
    reasoning have no representation here, so they cannot escape through it.
    """
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        row = _row_to_dict(conn.execute(
            "SELECT * FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (camp.campaign_id, referred),
        ).fetchone())
        if not row:
            return {"ok": False, "error": "not_found"}

        days = int(row.get("posting_days") or 0)
        need = camp.required_posting_days
        state = str(row.get("state") or "")
        items = [
            {"key": "signed_up", "label": "Signed up through your link",
             "done": bool(int(row.get("signed_up") or 0))},
            {"key": "profile", "label": "Completed their profile",
             "done": bool(int(row.get("profile_completed") or 0))},
        ]
        for i in range(1, need + 1):
            items.append({
                "key": f"posting_day_{i}",
                "label": (f"Posted on day {i}" if days >= i
                          else "Needs another separate posting day"),
                "done": days >= i,
            })
        items.append({"key": "standing", "label": "Good standing",
                      "done": bool(int(row.get("good_standing") or 0))})
        items.append({"key": "checks", "label": "Safety and quality checks",
                      "done": state == QUALIFIED})

        return {
            "ok": True,
            "state": state,
            "counts_toward_progress": state == QUALIFIED,
            "checklist": items,
            "summary": _summary_for(state, days, need),
        }
    finally:
        if owned:
            conn.close()


def _summary_for(state: str, days: int, need: int) -> str:
    if state == QUALIFIED:
        return "Qualified"
    if state == REVIEW_REQUIRED:
        return "Under review"
    if state == DISQUALIFIED:
        return "Not counted"
    if state == POSTED_DAY_1:
        return f"Needs {max(0, need - days)} more posting day"
    if state == PROFILE_COMPLETED:
        return "Hasn't posted yet"
    return "Getting started"


# --- risk (advisory only) ---------------------------------------------------
def set_risk_state(referred_user_id, risk_state: str, *, reason: str = "",
                   campaign_id: str = "", actor: str = "sentinel",
                   conn=None) -> dict:
    """Attach an advisory risk state to a referral.

    Note what this function cannot do: it cannot mark anything QUALIFIED, and
    the only way it removes value is by routing to REVIEW_REQUIRED (reversible,
    human-resolvable) or, for a confirmed block, DISQUALIFIED. The asymmetry is
    intentional — an automated signal may pause a reward but may not award one.
    """
    camp = campaign_mod.get(campaign_id)
    referred = int(referred_user_id or 0)
    risk_state = str(risk_state or RISK_CLEAR).strip().lower()
    if risk_state not in {RISK_CLEAR, RISK_REVIEW, RISK_BLOCKED}:
        return {"ok": False, "error": "invalid_risk_state"}
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE progress_referral_qualifications "
            "SET risk_state=?, review_reason=?, updated_at=? "
            "WHERE campaign_id=? AND referred_user_id=?",
            (risk_state, reason or "", _utcnow(), camp.campaign_id, referred),
        )
        if owned:
            conn.commit()
        result = evaluate(referred, campaign_id=camp.campaign_id, conn=conn,
                          actor=actor)
        if owned:
            conn.commit()
        return {"ok": True, "risk_state": risk_state, "state": result.get("state")}
    finally:
        if owned:
            conn.close()


# --- events -----------------------------------------------------------------
def _log_event(conn, campaign_id: str, *, user_id: int, event_type: str,
               subject_user_id=None, visibility: str = "private",
               detail: Optional[dict] = None, actor: str = "system") -> None:
    try:
        conn.execute(
            """
            INSERT INTO progress_events
            (campaign_id, user_id, subject_user_id, event_type, visibility,
             detail_json, actor, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (campaign_id, int(user_id or 0),
             int(subject_user_id) if subject_user_id else None,
             event_type, visibility, json.dumps(detail or {}), actor, _utcnow()),
        )
    except Exception:
        # The event log is an observability aid. Losing a row must never fail
        # the decision that produced it.
        pass


def log_event(campaign_id: str, user_id, event_type: str, *,
              subject_user_id=None, visibility: str = "private",
              detail: Optional[dict] = None, actor: str = "system",
              conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        _log_event(conn, campaign_id, user_id=int(user_id or 0),
                   event_type=event_type, subject_user_id=subject_user_id,
                   visibility=visibility, detail=detail, actor=actor)
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()
