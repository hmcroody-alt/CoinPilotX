"""Retention missions — the journey after the challenge.

Progress OS exists because a referral counter that stops at 30 turns a member
into a lead-generation instrument and then abandons them. Two populations need
somewhere to go:

* the **referrer** who finished the Founding Member Challenge, and
* the **referred** person, who until now has been an object in someone else's
  reward program and should become the subject of their own.

Missions are deliberately generic — ``objective_type`` + ``target`` +
``current_progress`` — so the next PulseSoc program can use this engine without
touching referrals at all. That genericity is the point: the mission brief asked
for a foundation, not a bigger referral screen.

Honesty rule
------------
A mission's progress is either measured from a real source or it is reported as
zero. Nothing here estimates, extrapolates, or shows a plausible-looking number
to make the screen feel alive. A mission whose objective this deployment cannot
measure stays at 0 and says so, because a fabricated "3 of 5 followers" is a lie
the user can act on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from services import db

from . import campaign as campaign_mod
from .schema import ensure_schema

LOCKED = "LOCKED"
AVAILABLE = "AVAILABLE"
IN_PROGRESS = "IN_PROGRESS"
COMPLETE = "COMPLETE"

#: Where a mission's progress is read from. ``manual`` means no automatic
#: measurement exists in this deployment; such a mission never fabricates a
#: number.
SOURCE_POSTS = "posts"
SOURCE_FOLLOWERS = "followers"
SOURCE_QUALIFIED = "qualified_referrals"
SOURCE_MANUAL = "manual"

#: Track a mission belongs to. Referrers who completed the challenge get the
#: creator track; newly referred members get the onboarding track.
TRACK_CREATOR = "creator"
TRACK_NEWCOMER = "newcomer"


class Mission:
    __slots__ = ("mission_id", "track", "title_key", "objective_type",
                 "target", "source", "order")

    def __init__(self, mission_id: str, track: str, title_key: str,
                 objective_type: str, target: int, source: str, order: int):
        self.mission_id = mission_id
        self.track = track
        self.title_key = title_key
        self.objective_type = objective_type
        self.target = int(target)
        self.source = source
        self.order = order

    def as_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "track": self.track,
            "title_key": self.title_key,
            "objective_type": self.objective_type,
            "target": self.target,
            "order": self.order,
        }


# ``title_key`` is an i18n key, never display text. The API returns keys and the
# client renders them, which keeps the server out of the translation business
# and satisfies the app's gated i18n contract.
CATALOG = (
    # --- post-30 creator journey
    Mission("host_first_live", TRACK_CREATOR, "progress.missions.hostFirstLive",
            "host_live", 1, SOURCE_MANUAL, 10),
    Mission("reach_100_followers", TRACK_CREATOR, "progress.missions.reach100Followers",
            "followers", 100, SOURCE_FOLLOWERS, 20),
    Mission("build_creator_page", TRACK_CREATOR, "progress.missions.buildCreatorPage",
            "creator_page", 1, SOURCE_MANUAL, 30),
    Mission("start_selling", TRACK_CREATOR, "progress.missions.startSelling",
            "marketplace_listing", 1, SOURCE_MANUAL, 40),
    Mission("grow_community", TRACK_CREATOR, "progress.missions.growCommunity",
            "qualified_referrals", 60, SOURCE_QUALIFIED, 50),

    # --- newcomer journey: the referred person's own beginning
    Mission("complete_profile", TRACK_NEWCOMER, "progress.missions.completeProfile",
            "profile", 1, SOURCE_MANUAL, 10),
    Mission("first_post", TRACK_NEWCOMER, "progress.missions.firstPost",
            "posts", 1, SOURCE_POSTS, 20),
    Mission("post_another_day", TRACK_NEWCOMER, "progress.missions.postAnotherDay",
            "posting_days", 2, SOURCE_POSTS, 30),
    Mission("follow_five", TRACK_NEWCOMER, "progress.missions.followFive",
            "following", 5, SOURCE_MANUAL, 40),
    Mission("start_own_challenge", TRACK_NEWCOMER, "progress.missions.startOwnChallenge",
            "qualified_referrals", 1, SOURCE_QUALIFIED, 50),
)

_BY_ID = {m.mission_id: m for m in CATALOG}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row) -> Optional[dict]:
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _measure(conn, mission: Mission, user_id: int, qualified: int) -> Optional[int]:
    """Real progress, or ``None`` when this deployment cannot measure it."""
    if mission.source == SOURCE_QUALIFIED:
        return int(qualified or 0)
    if mission.source == SOURCE_POSTS:
        try:
            if mission.objective_type == "posting_days":
                from . import qualification as qual
                return qual.posting_day_count(user_id, conn=conn)
            row = _row_to_dict(conn.execute(
                "SELECT COUNT(*) AS total FROM pulse_posts "
                "WHERE user_id=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()) or {}
            return int(row.get("total") or 0)
        except Exception:
            return None
    if mission.source == SOURCE_FOLLOWERS:
        for table, column in (("pulse_follows", "following_user_id"),
                              ("user_follows", "following_id")):
            try:
                row = _row_to_dict(conn.execute(
                    f"SELECT COUNT(*) AS total FROM {table} WHERE {column}=?",
                    (user_id,),
                ).fetchone()) or {}
                return int(row.get("total") or 0)
            except Exception:
                continue
        return None
    return None


def list_missions(user_id, *, track: str = TRACK_CREATOR, campaign_id: str = "",
                  qualified: int = 0, conn=None) -> list:
    """Missions for a track with real, measured progress.

    Stored rows are only used for missions this deployment cannot measure
    automatically; measurable ones are always recomputed so a stale row can
    never overstate someone's progress.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        stored = {}
        for r in conn.execute(
            "SELECT mission_id, status, current_progress, completed_at "
            "FROM progress_missions WHERE campaign_id=? AND user_id=?",
            (camp.campaign_id, uid),
        ).fetchall():
            d = _row_to_dict(r) or {}
            stored[str(d.get("mission_id"))] = d

        out = []
        for m in sorted((x for x in CATALOG if x.track == track),
                        key=lambda x: x.order):
            row = stored.get(m.mission_id) or {}
            measured = _measure(conn, m, uid, qualified)
            if measured is None:
                progress = int(row.get("current_progress") or 0)
                measurable = False
            else:
                progress = measured
                measurable = True

            if row.get("status") == COMPLETE or progress >= m.target:
                status = COMPLETE
            elif progress > 0:
                status = IN_PROGRESS
            else:
                status = AVAILABLE

            out.append({
                "mission_id": m.mission_id,
                "title_key": m.title_key,
                "objective_type": m.objective_type,
                "target": m.target,
                "current_progress": min(progress, m.target),
                "status": status,
                "measurable": measurable,
                "completed_at": row.get("completed_at"),
            })
        return out
    finally:
        if owned:
            conn.close()


def complete_mission(user_id, mission_id: str, *, campaign_id: str = "",
                     conn=None) -> dict:
    """Mark a non-measurable mission complete from a trusted server event.

    Only callable for missions whose source is ``manual``. A measurable
    mission's completion is derived from its real source and cannot be
    asserted, which stops a client from ever declaring itself finished.
    """
    camp = campaign_mod.get(campaign_id)
    uid = int(user_id or 0)
    mission = _BY_ID.get(mission_id)
    if not mission or uid <= 0:
        return {"ok": False, "error": "unknown_mission"}
    if mission.source != SOURCE_MANUAL:
        return {"ok": False, "error": "mission_is_measured"}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        now = _utcnow()
        existing = conn.execute(
            "SELECT id FROM progress_missions "
            "WHERE campaign_id=? AND user_id=? AND mission_id=?",
            (camp.campaign_id, uid, mission_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE progress_missions SET status=?, current_progress=?, "
                "completed_at=COALESCE(completed_at, ?), updated_at=? "
                "WHERE campaign_id=? AND user_id=? AND mission_id=?",
                (COMPLETE, mission.target, now, now,
                 camp.campaign_id, uid, mission_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO progress_missions
                (campaign_id, user_id, mission_id, status, current_progress,
                 target, completed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (camp.campaign_id, uid, mission_id, COMPLETE, mission.target,
                 mission.target, now, now, now),
            )
        if owned:
            conn.commit()
        return {"ok": True, "mission_id": mission_id, "status": COMPLETE}
    finally:
        if owned:
            conn.close()


def track_for(qualified: int, *, campaign_id: str = "") -> str:
    """Which journey a user is on.

    Finishing the challenge switches the headline from "your challenge" to
    "your next mission" — the transition the brief asked for, decided on the
    server so the client cannot get it wrong.
    """
    camp = campaign_mod.get(campaign_id)
    return TRACK_CREATOR if int(qualified or 0) >= camp.qualification_target else TRACK_NEWCOMER
