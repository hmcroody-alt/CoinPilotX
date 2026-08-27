"""Progress OS HTTP controller.

Framework-agnostic by convention: every handler returns ``(status, body)`` and
nothing here imports Flask. ``bot.py`` owns authentication, CSRF and RBAC and
converts the tuple. That split is what lets this module be tested without
booting the monolith.

Privacy is structural, not procedural
-------------------------------------
Two decisions do the heavy lifting:

* **Referrals are addressed by an opaque ``ref`` token**, never by user id. The
  token is an HMAC over ``(campaign, referrer, referred)``, so it is stable,
  unguessable, and — because the referrer is inside the hash — a token minted
  for one referrer simply does not resolve for another. Referral detail needs
  no separate ownership check; the addressing scheme *is* the check.
* **The private surface has no ``user_id`` parameter to point at someone else.**
  Every read takes the authenticated viewer's own id from the caller. There is
  no route shape here that expresses "show me another person's progress", which
  is a stronger guarantee than remembering to compare ids on every handler.

Security signals never appear. The controller can emit "Under review" and
nothing more granular: no risk scores, no IP or device facts, no Sentinel
reasoning. Those values are not in the payload vocabulary, so they cannot leak
through a field someone forgets to strip.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from services import db

from . import campaign as campaign_mod
from . import milestones as ms
from . import missions as missions_mod
from . import qualification as qual
from .schema import ensure_schema

#: Public-facing program disclaimer. The Founding Path is a growth program, not
#: a verification or identity signal, and the surface says so in its own payload.
NOT_VERIFICATION = (
    "The PulseSoc Founding Path is a community growth program. It is not "
    "identity verification and does not affect verification eligibility.")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _ref_secret() -> bytes:
    """Key for referral tokens.

    Falls back to a per-deployment constant when no secret is configured. The
    token is an addressing handle, not an authentication credential — it is
    only ever resolved against rows already scoped to the viewer — so a weak
    key degrades unguessability, not access control.
    """
    secret = (os.environ.get("PROGRESS_REF_SECRET")
              or os.environ.get("SECRET_KEY") or "pulsesoc-progress")
    return str(secret).encode("utf-8")


def ref_token(campaign_id: str, referrer_user_id, referred_user_id) -> str:
    msg = f"{campaign_id}:{int(referrer_user_id)}:{int(referred_user_id)}".encode()
    return hmac.new(_ref_secret(), msg, hashlib.sha256).hexdigest()[:20]


def _public_name(row: dict) -> str:
    """A display name safe to show the referrer, or a neutral placeholder.

    Never returns a username, email, phone or internal id. A referrer is
    entitled to know their invite landed; they are not entitled to a profile
    dossier on someone who has not finished signing up.
    """
    name = str(row.get("display_name") or "").strip()
    return name or "New member"


# --- user surface -----------------------------------------------------------
def _invite_counts(breakdown: dict, certified: int) -> dict:
    """Invited / In Progress / Certified, from states the engine already owns.

    ``in_progress`` folds review standing in with everyone else still on their
    way. Splitting it out would tell a referrer which of their invites tripped
    a check, which is exactly the signal this surface must not carry.
    """
    invited = sum(int(breakdown.get(k) or 0) for k in
                  ("qualified", "needs_another_day", "hasnt_posted",
                   "getting_started", "in_review", "not_counted"))
    counted_out = int(breakdown.get("not_counted") or 0)
    return {
        "invited": invited,
        "in_progress": max(0, invited - certified - counted_out),
        "certified": certified,
    }


def _next_unlock(camp, certified: int) -> Optional[dict]:
    nxt = camp.next_milestone(certified)
    if not nxt:
        return None
    previous = 0
    for m in camp.milestones:
        if m.threshold < nxt.threshold:
            previous = m.threshold
    span = max(1, nxt.threshold - previous)
    return {
        "key": nxt.key,
        "label": nxt.label,
        "kind": nxt.kind,
        "description": nxt.description,
        "threshold": nxt.threshold,
        "current": certified,
        "remaining": max(0, nxt.threshold - certified),
        "percent": _percent(max(0, certified - previous), span),
    }


def overview(user_id, *, campaign_id: str = "") -> tuple:
    """The Progress Center headline: Founding Path state and the next unlock."""
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    conn = db.connect()
    try:
        ensure_schema(conn)
        certified = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        counts = qual.breakdown(uid, campaign_id=camp.campaign_id, conn=conn)
        awards = ms.earned_milestones(uid, campaign_id=camp.campaign_id, conn=conn)
        earned = {m.get("milestone_key") for m in awards}
        track = missions_mod.track_for(certified, campaign_id=camp.campaign_id)

        body = {
            "ok": True,
            "campaign": {
                "campaign_id": camp.campaign_id,
                "campaign_version": camp.campaign_version,
                "name": camp.name,
                "status": camp.status,
                "target": camp.qualification_target,
            },
            "path": {
                "certified": certified,
                "target": camp.qualification_target,
                "remaining": max(0, camp.qualification_target - certified),
                "percent": _percent(certified, camp.qualification_target),
                "complete": certified >= camp.qualification_target,
            },
            "invites": _invite_counts(counts, certified),
            "breakdown": counts,
            "next_unlock": _next_unlock(camp, certified),
            "milestones_earned": sorted(earned),
            "live_creator": "live_creator" in earned,
            "founding_member": "founding_member" in earned,
            "founding": _founding_status(conn, uid, camp, awards),
            "legacy": _legacy(conn, uid, camp, certified),
            "track": track,
            "not_verification": NOT_VERIFICATION,
        }
        return 200, body
    finally:
        conn.close()


def _founding_status(conn, uid: int, camp, awards) -> Optional[dict]:
    """Founding Generation standing, or ``None`` if the rung is not earned.

    The founding number is the award row's position in the award table, not a
    minted counter: ``progress_milestone_awards.id`` is monotonic and the row
    is immutable once written, so the number a member sees today is the number
    they will see in five years. Revoked awards still occupy their position on
    purpose — renumbering the people behind them would make the number a lie.
    """
    award = next((a for a in awards
                  if a.get("milestone_key") == "founding_member"), None)
    if not award:
        return None
    number = 0
    try:
        row = _row_to_dict(conn.execute(
            "SELECT COUNT(*) AS position FROM progress_milestone_awards "
            "WHERE campaign_id=? AND milestone_key='founding_member' "
            "AND id <= (SELECT id FROM progress_milestone_awards "
            "           WHERE campaign_id=? AND user_id=? "
            "           AND milestone_key='founding_member')",
            (camp.campaign_id, camp.campaign_id, uid),
        ).fetchone()) or {}
        number = int(row.get("position") or 0)
    except Exception:
        number = 0
    return {
        "generation": "Founding Generation",
        "member_since": award.get("earned_at"),
        "founding_number": number or None,
    }


def _legacy(conn, uid: int, camp, certified: int) -> dict:
    """Real network impact only: counts and dates the engine already recorded.

    There is no referral graph here and none is inferred. Everything reported
    is a row this user's own invites produced.
    """
    first_at, latest_certified_at = None, None
    try:
        row = _row_to_dict(conn.execute(
            "SELECT MIN(created_at) AS first_at, MAX(qualified_at) AS latest "
            "FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referrer_user_id=?",
            (camp.campaign_id, uid),
        ).fetchone()) or {}
        first_at = row.get("first_at")
        latest_certified_at = row.get("latest")
    except Exception:
        pass
    return {
        "certified": certified,
        "first_invite_at": first_at,
        "latest_certified_at": latest_certified_at,
    }


def _percent(current: int, target: int) -> int:
    if target <= 0:
        return 0
    return max(0, min(100, int(round((current / target) * 100))))


def milestones(user_id, *, campaign_id: str = "") -> tuple:
    """The Founding Path ladder — the "Your Unlocks" surface.

    Every rung is LOCKED, IN_PROGRESS or UNLOCKED, and the decision is made
    here. A rung reads UNLOCKED only when an award row exists for it, so the
    app can never paint a privilege the server has not actually granted.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    conn = db.connect()
    try:
        ensure_schema(conn)
        certified = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        earned = {m.get("milestone_key"): m for m in
                  ms.earned_milestones(uid, campaign_id=camp.campaign_id, conn=conn)}
        upcoming = camp.next_milestone(certified)
        out = []
        for m in camp.milestones:
            if m.key in earned:
                state = "UNLOCKED"
            elif certified >= m.threshold or (upcoming and m.key == upcoming.key):
                # Reached but not yet recorded — the sweep has not run. Report
                # it as in progress rather than claiming an award that has no
                # row behind it.
                state = "IN_PROGRESS"
            else:
                state = "LOCKED"
            out.append({
                "key": m.key,
                "label": m.label,
                "threshold": m.threshold,
                "kind": m.kind,
                "description": m.description,
                "state": state,
                "earned_at": (earned.get(m.key) or {}).get("earned_at"),
                "progress": min(certified, m.threshold),
            })
        return 200, {"ok": True, "certified": certified, "milestones": out}
    finally:
        conn.close()


#: Tabs map to sets of states. "PENDING" deliberately groups everything that is
#: on its way, so the user sees momentum rather than a taxonomy.
_TABS = {
    "all": None,
    "qualified": (qual.QUALIFIED,),
    "pending": (qual.SIGNED_UP, qual.ATTRIBUTED, qual.INVITED,
                qual.PROFILE_COMPLETED, qual.POSTED_DAY_1),
    "review": (qual.REVIEW_REQUIRED,),
}


def referrals(user_id, *, tab: str = "all", campaign_id: str = "",
              limit: int = 100) -> tuple:
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    tab = str(tab or "all").lower()
    if tab not in _TABS:
        tab = "all"
    limit = max(1, min(int(limit or 100), 200))

    conn = db.connect()
    try:
        ensure_schema(conn)
        states = _TABS[tab]
        sql = ("SELECT referred_user_id, state, posting_days, updated_at "
               "FROM progress_referral_qualifications "
               "WHERE campaign_id=? AND referrer_user_id=?")
        params = [camp.campaign_id, uid]
        if states:
            sql += " AND state IN (" + ",".join("?" for _ in states) + ")"
            params.extend(states)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)

        rows = [_row_to_dict(r) or {} for r in conn.execute(sql, params).fetchall()]
        items = []
        for r in rows:
            referred = int(r.get("referred_user_id") or 0)
            profile = _row_to_dict(conn.execute(
                "SELECT display_name FROM users WHERE user_id=?", (referred,),
            ).fetchone()) or {}
            state = str(r.get("state") or "")
            items.append({
                # No user_id. The token is the only handle the client gets.
                "ref": ref_token(camp.campaign_id, uid, referred),
                "name": _public_name(profile),
                "state": state,
                "summary": qual._summary_for(
                    state, int(r.get("posting_days") or 0),
                    camp.required_posting_days),
                "counts": state == qual.QUALIFIED,
            })
        return 200, {"ok": True, "tab": tab, "referrals": items,
                     "count": len(items)}
    finally:
        conn.close()


def referral_detail(user_id, ref: str, *, campaign_id: str = "") -> tuple:
    """Explain one referral to the person who made it.

    Resolution is by recomputing the token for each of the viewer's own
    referrals. A token belonging to someone else cannot match, because the
    viewer's id is part of the hashed message.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    ref = str(ref or "").strip()
    if not ref:
        return 400, {"ok": False, "error": "ref_required"}
    camp = campaign_mod.get(campaign_id)

    conn = db.connect()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT referred_user_id FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referrer_user_id=?",
            (camp.campaign_id, uid),
        ).fetchall()
        target = None
        for r in rows:
            referred = int((_row_to_dict(r) or {}).get("referred_user_id") or 0)
            if hmac.compare_digest(ref_token(camp.campaign_id, uid, referred), ref):
                target = referred
                break
        if not target:
            return 404, {"ok": False, "error": "not_found"}

        detail = qual.checklist(target, campaign_id=camp.campaign_id, conn=conn)
        if not detail.get("ok"):
            return 404, {"ok": False, "error": "not_found"}
        profile = _row_to_dict(conn.execute(
            "SELECT display_name FROM users WHERE user_id=?", (target,),
        ).fetchone()) or {}
        detail["name"] = _public_name(profile)
        detail["ref"] = ref
        return 200, detail
    finally:
        conn.close()


def missions(user_id, *, campaign_id: str = "") -> tuple:
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    conn = db.connect()
    try:
        ensure_schema(conn)
        qualified = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        track = missions_mod.track_for(qualified, campaign_id=camp.campaign_id)
        items = missions_mod.list_missions(
            uid, track=track, campaign_id=camp.campaign_id,
            qualified=qualified, conn=conn)
        return 200, {"ok": True, "track": track, "missions": items}
    finally:
        conn.close()


def activity(user_id, *, campaign_id: str = "", limit: int = 50) -> tuple:
    """The user-visible slice of the event log.

    Filters on ``visibility='public'``. Private events — risk transitions,
    admin actions, disqualification reasons — share the table but are excluded
    by the query, so a user-facing feed cannot accidentally render one.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    limit = max(1, min(int(limit or 50), 100))
    conn = db.connect()
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT event_type, detail_json, created_at, subject_user_id "
            "FROM progress_events WHERE campaign_id=? AND user_id=? "
            "AND visibility='public' ORDER BY id DESC LIMIT ?",
            (camp.campaign_id, uid, limit),
        ).fetchall()
        items = []
        for r in rows:
            d = _row_to_dict(r) or {}
            subject = d.get("subject_user_id")
            name = ""
            if subject:
                p = _row_to_dict(conn.execute(
                    "SELECT display_name FROM users WHERE user_id=?", (subject,),
                ).fetchone()) or {}
                name = _public_name(p)
            items.append({
                "event_type": d.get("event_type"),
                "name": name,
                "created_at": d.get("created_at"),
            })
        return 200, {"ok": True, "activity": items}
    finally:
        conn.close()


def invite(user_id, *, campaign_id: str = "") -> tuple:
    """Return the viewer's canonical referral link.

    Reuses the existing per-user referral code rather than minting a parallel
    one, so a link shared before Progress OS existed still attributes.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    conn = db.connect()
    try:
        row = _row_to_dict(conn.execute(
            "SELECT referral_code FROM users WHERE user_id=?", (uid,),
        ).fetchone()) or {}
        code = str(row.get("referral_code") or "").strip()
    finally:
        conn.close()
    if not code:
        # Minting belongs to the canonical helper in bot.py; the controller
        # reports the absence rather than inventing a second code format.
        return 409, {"ok": False, "error": "referral_code_unavailable"}
    return 200, {
        "ok": True,
        "referral_code": code,
        "referral_link": f"https://pulsesoc.com/r/{code}",
    }


def how_it_works(*, campaign_id: str = "") -> tuple:
    camp = campaign_mod.get(campaign_id)
    return 200, {
        "ok": True,
        "steps": [
            {"key": "invite", "order": 1},
            {"key": "join", "order": 2},
            {"key": "profile", "order": 3},
            {"key": "post_two_days", "order": 4},
            {"key": "qualified", "order": 5},
        ],
        "required_posting_days": camp.required_posting_days,
        "target": camp.qualification_target,
        "live_threshold": camp.live_threshold(),
        "fairness_note_key": "progress.howItWorks.fairness",
        "not_verification": NOT_VERIFICATION,
    }


def faq(*, campaign_id: str = "") -> tuple:
    """FAQ as i18n keys; the server never ships display copy to the app."""
    keys = (
        "howInvitesWork", "whatCounts", "whyNotCertifiedYet", "whatDoIUnlock",
        "howToUnlockLive", "canFamilyParticipate", "underReview",
        "isFoundingMemberPermanent", "isThereALimit", "whenDoesItEnd",
    )
    return 200, {"ok": True,
                 "faq": [{"key": f"progress.faq.{k}", "order": i}
                         for i, k in enumerate(keys)]}


# --- profile tile -----------------------------------------------------------
def tile(user_id, *, campaign_id: str = "") -> tuple:
    """Compact state for the Profile OS tile.

    Only ever called for the profile owner. A visitor's request never reaches
    here because the tile is not in the visitor tile set — enforced client-side
    for layout and server-side by this handler having no target-user parameter.
    """
    uid = int(user_id or 0)
    if uid <= 0:
        return 401, {"ok": False, "error": "login_required"}
    camp = campaign_mod.get(campaign_id)
    conn = db.connect()
    try:
        ensure_schema(conn)
        certified = qual.qualified_count(uid, campaign_id=camp.campaign_id, conn=conn)
        earned = {m.get("milestone_key") for m in
                  ms.earned_milestones(uid, campaign_id=camp.campaign_id, conn=conn)}
        in_review = qual.breakdown(uid, campaign_id=camp.campaign_id,
                                   conn=conn).get("in_review", 0)
        if "founding_member" in earned:
            state = "COMPLETE"
        elif certified > 0:
            state = "ACTIVE"
        elif in_review:
            state = "REVIEW"
        else:
            state = "START"
        nxt = camp.next_milestone(certified)
        return 200, {
            "ok": True,
            "state": state,
            "certified": certified,
            "target": camp.qualification_target,
            "percent": _percent(certified, camp.qualification_target),
            "next_unlock_label": nxt.label if nxt else "",
            "live_creator": "live_creator" in earned,
            "founding_member": "founding_member" in earned,
        }
    finally:
        conn.close()
