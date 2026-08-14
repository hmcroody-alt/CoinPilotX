"""Progress OS adversarial suite (PROG-001..).

These tests are written from the attacker's chair, not the happy path. The
Founding Member Challenge pays real money for referrals, which makes it a
standing invitation to fraud, and it withholds real money from real people,
which makes false positives just as expensive in a different currency. Both
failure directions are asserted here:

* **Fraud must not pay.** Signup farms, same-day double posts, replayed
  events, forged client claims, and duplicate attribution all have to earn
  exactly zero.
* **Normal life must not be punished.** Families, roommates, offices, schools
  and CGNAT share IPs and devices. A shared network is not evidence of
  anything, and no test here lets it cost someone a reward.

The money assertions are deliberately exact — 29 → $0, 30 → $30, replay →
still $30, 59 → $30, 60 → $60 — because "roughly right" is not a property a
payout system can have.
"""

import os
import sys
import tempfile

# Bind a throwaway database BEFORE ``services.db`` is imported. An empty
# DATABASE_URL falls back to the local ``coinpilotx.db`` — the developer's real
# database — and this suite would seed sixty fake users into it.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="progress_os_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import db  # noqa: E402
from services.business_os.progress import admin_api as padmin  # noqa: E402
from services.business_os.progress import campaign as camp_mod  # noqa: E402
from services.business_os.progress import milestones as ms  # noqa: E402
from services.business_os.progress import missions as missions_mod  # noqa: E402
from services.business_os.progress import progress_api as papi  # noqa: E402
from services.business_os.progress import qualification as qual  # noqa: E402
from services.business_os.progress import schema as sch  # noqa: E402

CAMP = camp_mod.get()


def setup_module():
    conn = db.connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, "
        "onboarding_complete INTEGER DEFAULT 0, account_status TEXT DEFAULT 'active', "
        "access_enabled INTEGER DEFAULT 1, referral_code TEXT, username TEXT, "
        "display_name TEXT, email TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY, "
        "user_id INTEGER, created_at TEXT, deleted_at TEXT, "
        "moderation_status TEXT, post_type TEXT, repost_of_post_id INTEGER)"
    )
    conn.commit()
    conn.close()
    sch.ensure_ready()


# --- helpers ----------------------------------------------------------------
_next_post_id = [1]


def mkuser(uid, *, profile=True, status="active", name=""):
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, onboarding_complete, account_status, "
        "referral_code, username, display_name, created_at) VALUES (?,?,?,?,?,?,?)",
        (uid, 1 if profile else 0, status, f"CODE{uid}", f"u{uid}", name,
         "2026-08-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()
    return uid


def post(uid, day, *, post_type="text", repost_of=None, deleted=None):
    pid = _next_post_id[0]
    _next_post_id[0] += 1
    conn = db.connect()
    conn.execute(
        "INSERT INTO pulse_posts (id, user_id, created_at, deleted_at, "
        "moderation_status, post_type, repost_of_post_id) VALUES (?,?,?,?,?,?,?)",
        (pid, uid, f"{day}T12:00:00+00:00", deleted, "approved", post_type, repost_of),
    )
    conn.commit()
    conn.close()
    return pid


def qualify(referrer, referred, *, day_a="2026-08-01", day_b="2026-08-02"):
    """Take one referred user all the way to QUALIFIED, honestly."""
    mkuser(referred)
    qual.attribute(referrer, referred)
    post(referred, day_a)
    post(referred, day_b)
    qual.evaluate(referred)
    return referred


def uid_block(base, n):
    return [base + i for i in range(n)]


# =====================================================================
# Attribution
# =====================================================================
def test_prog_001_signup_alone_is_worth_nothing():
    """A signup is not a referral. Thirty of them are not thirty referrals.

    This is the exact defect Progress OS was built to close: the pre-existing
    code counted signups and unlocked Live at thirty of them.
    """
    ref = mkuser(100)
    for i, u in enumerate(uid_block(101, 30)):
        mkuser(u, profile=False)
        qual.attribute(ref, u)
        qual.evaluate(u)
    assert qual.qualified_count(ref) == 0
    assert ms.sync(ref)["cycles_created"] == []
    assert ms.reward_summary(ref)["earned_cents"] == 0


def test_prog_002_one_referred_user_belongs_to_one_referrer():
    """Two referrers racing for the same signup: the database decides, once."""
    first, second = mkuser(200), mkuser(201)
    referred = mkuser(202)
    a = qual.attribute(first, referred)
    b = qual.attribute(second, referred)
    assert a["duplicate"] is False
    assert b["duplicate"] is True
    assert b["referrer_user_id"] == first


def test_prog_003_replayed_attribution_creates_nothing():
    ref, referred = mkuser(210), mkuser(211)
    for _ in range(5):
        qual.attribute(ref, referred)
    conn = db.connect()
    total = dict(conn.execute(
        "SELECT COUNT(*) AS t FROM progress_referral_qualifications "
        "WHERE campaign_id=? AND referred_user_id=?",
        (CAMP.campaign_id, referred)).fetchone())["t"]
    conn.close()
    assert total == 1


def test_prog_004_self_referral_is_arithmetic_not_fraud_review():
    """Inviting yourself is not suspicious, it is impossible. Reject outright."""
    me = mkuser(220)
    assert qual.attribute(me, me)["error"] == "self_referral"


# =====================================================================
# The two-separate-days rule
# =====================================================================
def test_prog_010_five_posts_in_one_day_is_one_day():
    ref = mkuser(300)
    referred = mkuser(301)
    qual.attribute(ref, referred)
    for _ in range(5):
        post(referred, "2026-08-01")
    result = qual.evaluate(referred)
    assert result["posting_days"] == 1
    assert result["state"] == qual.POSTED_DAY_1
    assert qual.qualified_count(ref) == 0


def test_prog_011_two_separate_days_qualifies():
    ref = mkuser(310)
    referred = qualify(ref, 311)
    assert qual.evaluate(referred)["state"] == qual.QUALIFIED
    assert qual.qualified_count(ref) == 1


def test_prog_012_reposts_do_not_manufacture_a_posting_day():
    """A repost is one tap. It is the cheapest possible fake activity."""
    ref = mkuser(320)
    referred = mkuser(321)
    qual.attribute(ref, referred)
    post(referred, "2026-08-01")
    post(referred, "2026-08-02", post_type="repost", repost_of=1)
    assert qual.evaluate(referred)["posting_days"] == 1
    assert qual.qualified_count(ref) == 0


def test_prog_013_deleting_posts_cannot_strip_an_earned_day():
    """A referred user must not be able to sabotage someone else's reward.

    Nor should their own change of heart about a post retroactively rewrite
    history. The day was genuinely earned when it happened.
    """
    ref = mkuser(330)
    referred = qualify(ref, 331)
    conn = db.connect()
    conn.execute("UPDATE pulse_posts SET deleted_at='2026-08-09' WHERE user_id=?",
                 (referred,))
    conn.commit()
    conn.close()
    assert qual.posting_day_count(referred) == 2
    assert qual.evaluate(referred)["state"] == qual.QUALIFIED
    assert qual.qualified_count(ref) == 1


def test_prog_014_deleting_and_reposting_does_not_add_a_day():
    ref = mkuser(340)
    referred = qualify(ref, 341)
    before = qual.posting_day_count(referred)
    post(referred, "2026-08-01")
    post(referred, "2026-08-02")
    assert qual.posting_day_count(referred) == before


def test_prog_015_profile_is_required_before_posts_count():
    ref = mkuser(350)
    referred = mkuser(351, profile=False)
    qual.attribute(ref, referred)
    post(referred, "2026-08-01")
    post(referred, "2026-08-02")
    assert qual.evaluate(referred)["state"] == qual.SIGNED_UP
    assert qual.qualified_count(ref) == 0


# =====================================================================
# Money: the exact arithmetic
# =====================================================================
def _bring_to(referrer, base, n):
    for i, u in enumerate(uid_block(base, n)):
        qualify(referrer, u)
    return qual.qualified_count(referrer)


def test_prog_020_twentynine_pays_nothing():
    ref = mkuser(1000)
    assert _bring_to(ref, 1100, 29) == 29
    ms.sync(ref)
    assert ms.reward_summary(ref)["earned_cents"] == 0


def test_prog_021_thirty_pays_exactly_thirty_dollars_and_replay_does_not():
    ref = mkuser(2000)
    assert _bring_to(ref, 2100, 30) == 30
    assert ms.sync(ref)["cycles_created"] == [1]
    assert ms.reward_summary(ref)["earned_cents"] == 3000
    for _ in range(5):
        assert ms.sync(ref)["cycles_created"] == []
    assert ms.reward_summary(ref)["earned_cents"] == 3000


def test_prog_022_fiftynine_still_pays_thirty():
    ref = mkuser(3000)
    _bring_to(ref, 3100, 59)
    ms.sync(ref)
    assert ms.reward_summary(ref)["earned_cents"] == 3000


def test_prog_023_sixty_pays_sixty_and_replay_does_not():
    ref = mkuser(4000)
    _bring_to(ref, 4100, 60)
    ms.sync(ref)
    assert ms.reward_summary(ref)["earned_cents"] == 6000
    for _ in range(5):
        ms.sync(ref)
    assert ms.reward_summary(ref)["earned_cents"] == 6000
    cycles = ms.reward_summary(ref)["history"]
    assert [c["cycle"] for c in cycles] == [1, 2]


def test_prog_024_jumping_straight_to_sixty_creates_two_cycles_not_two_of_one():
    """A backfill that discovers 60 at once must pay cycle 1 and cycle 2."""
    ref = mkuser(5000)
    _bring_to(ref, 5100, 60)
    result = ms.sync(ref)
    assert result["cycles_created"] == [1, 2]
    assert ms.reward_summary(ref)["earned_cents"] == 6000


def test_prog_025_reward_event_keys_are_deterministic_and_distinct():
    """The key is the second, independent lock on double payment."""
    k1 = ms.reward_event_key(CAMP.campaign_id, 77, 1)
    k2 = ms.reward_event_key(CAMP.campaign_id, 77, 2)
    assert k1 == ms.reward_event_key(CAMP.campaign_id, 77, 1)
    assert k1 != k2
    assert k1 != ms.reward_event_key(CAMP.campaign_id, 78, 1)


def test_prog_026_progress_os_never_moves_money():
    """Every recorded cycle is unpaid until a separate, audited approval."""
    ref = mkuser(6000)
    _bring_to(ref, 6100, 30)
    ms.sync(ref)
    summary = ms.reward_summary(ref)
    assert summary["available_cents"] == 0
    assert summary["pending_cents"] == 3000
    assert all(c["status"] != "disbursed" for c in summary["history"])


# =====================================================================
# Milestones
# =====================================================================
def test_prog_030_milestones_award_once_and_never_repeat_at_sixty():
    ref = mkuser(7000)
    _bring_to(ref, 7100, 30)
    first = ms.sync(ref)
    assert sorted(first["milestones_awarded"]) == [
        "creator_perk", "early_supporter", "founding_member", "priority_creator"]
    _bring_to(ref, 7200, 30)
    second = ms.sync(ref)
    assert second["qualified"] == 60
    assert second["milestones_awarded"] == []


def test_prog_031_milestone_thresholds_match_the_published_ladder():
    thresholds = {m.key: m.threshold for m in CAMP.milestones}
    assert thresholds == {"early_supporter": 5, "creator_perk": 10,
                          "priority_creator": 20, "founding_member": 30}


def test_prog_032_partial_progress_awards_only_what_is_reached():
    ref = mkuser(8000)
    _bring_to(ref, 8100, 19)
    awarded = sorted(ms.sync(ref)["milestones_awarded"])
    assert awarded == ["creator_perk", "early_supporter"]
    _bring_to(ref, 8200, 1)
    assert ms.sync(ref)["milestones_awarded"] == ["priority_creator"]


# =====================================================================
# Risk: signals are not verdicts
# =====================================================================
def test_prog_040_shared_household_is_not_fraud():
    """A family on one router is the normal case, not the adversarial one.

    Nothing in the qualification path consults IP, device or network. This test
    exists so that stays true: if someone later adds an automatic
    shared-network penalty, it fails here.
    """
    ref = mkuser(9000)
    for u in uid_block(9100, 4):
        qualify(ref, u)
    assert qual.qualified_count(ref) == 4
    for u in uid_block(9100, 4):
        conn = db.connect()
        row = dict(conn.execute(
            "SELECT risk_state FROM progress_referral_qualifications "
            "WHERE campaign_id=? AND referred_user_id=?",
            (CAMP.campaign_id, u)).fetchone())
        conn.close()
        assert row["risk_state"] == qual.RISK_CLEAR


def test_prog_041_review_pauses_but_does_not_destroy():
    ref = mkuser(9200)
    referred = qualify(ref, 9201)
    qual.set_risk_state(referred, qual.RISK_REVIEW, reason="burst")
    assert qual.evaluate(referred)["state"] == qual.REVIEW_REQUIRED
    assert qual.qualified_count(ref) == 0
    qual.set_risk_state(referred, qual.RISK_CLEAR)
    assert qual.evaluate(referred)["state"] == qual.QUALIFIED
    assert qual.qualified_count(ref) == 1


def test_prog_042_risk_can_never_award():
    """An automated signal may pause a reward. It may not create one."""
    ref = mkuser(9300)
    referred = mkuser(9301)
    qual.attribute(ref, referred)  # no posts at all
    qual.set_risk_state(referred, qual.RISK_CLEAR, reason="looks great")
    assert qual.evaluate(referred)["state"] != qual.QUALIFIED
    assert qual.qualified_count(ref) == 0


def test_prog_043_invalid_risk_state_is_refused():
    assert qual.set_risk_state(1, "definitely_a_bot")["error"] == "invalid_risk_state"


def test_prog_044_suspension_removes_a_qualification_and_reinstatement_restores_it():
    ref = mkuser(9400)
    referred = qualify(ref, 9401)
    assert qual.qualified_count(ref) == 1
    conn = db.connect()
    conn.execute("UPDATE users SET account_status='suspended' WHERE user_id=?", (referred,))
    conn.commit()
    conn.close()
    assert qual.evaluate(referred)["state"] == qual.DISQUALIFIED
    assert qual.qualified_count(ref) == 0


# =====================================================================
# Server authority
# =====================================================================
def test_prog_050_client_cannot_assert_a_measured_mission():
    assert missions_mod.complete_mission(1, "first_post")["error"] == "mission_is_measured"
    assert missions_mod.complete_mission(1, "reach_100_followers")["error"] == "mission_is_measured"


def test_prog_051_unknown_mission_is_refused():
    assert missions_mod.complete_mission(1, "give_me_money")["error"] == "unknown_mission"


def test_prog_052_unmeasurable_missions_report_zero_not_a_guess():
    """A fabricated "3 of 5 followers" is a lie the user can act on."""
    items = missions_mod.list_missions(9500, track=missions_mod.TRACK_NEWCOMER)
    manual = [m for m in items if not m["measurable"]]
    assert manual, "expected at least one unmeasurable mission"
    assert all(m["current_progress"] == 0 for m in manual)


def test_prog_053_track_switches_only_at_the_target():
    assert missions_mod.track_for(CAMP.qualification_target - 1) == missions_mod.TRACK_NEWCOMER
    assert missions_mod.track_for(CAMP.qualification_target) == missions_mod.TRACK_CREATOR


# =====================================================================
# Privacy
# =====================================================================
def test_prog_060_referral_list_never_exposes_user_ids():
    ref = mkuser(10000)
    qualify(ref, 10001)
    status, body = papi.referrals(ref)
    assert status == 200
    assert body["referrals"]
    blob = repr(body)
    for item in body["referrals"]:
        assert "user_id" not in item
        assert "referred_user_id" not in item
    assert "10001" not in blob


def test_prog_061_a_referral_token_does_not_resolve_for_another_referrer():
    """The addressing scheme is the ownership check."""
    owner = mkuser(10100)
    stranger = mkuser(10101)
    referred = qualify(owner, 10102)
    token = papi.ref_token(CAMP.campaign_id, owner, referred)
    ok_status, _ = papi.referral_detail(owner, token)
    assert ok_status == 200
    denied_status, denied_body = papi.referral_detail(stranger, token)
    assert denied_status == 404
    assert denied_body["error"] == "not_found"


def test_prog_062_no_member_handler_accepts_a_target_user():
    """There is no route shape that expresses 'show me someone else's progress'."""
    import inspect
    for name in ("overview", "milestones", "referrals", "rewards", "missions",
                 "activity", "invite", "tile"):
        params = list(inspect.signature(getattr(papi, name)).parameters)
        assert params[0] == "user_id", name
        for p in params[1:]:
            assert p not in {"target_user_id", "subject_user_id", "referrer_user_id"}, name


def test_prog_063_checklist_never_leaks_security_internals():
    ref = mkuser(10200)
    referred = qualify(ref, 10201)
    qual.set_risk_state(referred, qual.RISK_REVIEW, reason="ip 10.0.0.1 matched device fp abc123")
    detail = qual.checklist(referred)
    blob = repr(detail).lower()
    for forbidden in ("10.0.0.1", "abc123", "device", "fingerprint",
                      "risk_state", "sentinel", "score"):
        assert forbidden not in blob, forbidden
    assert detail["summary"] == "Under review"


def test_prog_064_activity_feed_excludes_private_events():
    ref = mkuser(10300)
    qual.log_event(CAMP.campaign_id, ref, "admin_secret_action",
                   visibility="private", detail={"reason": "internal only"})
    status, body = papi.activity(ref)
    assert status == 200
    assert all(e["event_type"] != "admin_secret_action" for e in body["activity"])


def test_prog_065_unauthenticated_reads_are_refused():
    for name in ("overview", "milestones", "referrals", "rewards", "missions",
                 "activity", "invite", "tile"):
        status, body = getattr(papi, name)(0)
        assert status == 401, name
        assert body["error"] == "login_required"


def test_prog_066_program_states_it_is_not_verification():
    _, body = papi.how_it_works()
    assert "not identity verification" in body["not_verification"]


def test_prog_067_faq_returns_i18n_keys_not_display_copy():
    _, body = papi.faq()
    assert body["faq"]
    assert all(item["key"].startswith("progress.faq.") for item in body["faq"])


# =====================================================================
# Admin surface
# =====================================================================
def test_prog_070_admin_actions_require_actor_and_reason():
    ref = mkuser(11000)
    referred = qualify(ref, 11001)
    for call in (
        lambda: padmin.approve_qualification(referred, actor="", reason="valid reason"),
        lambda: padmin.reject_qualification(referred, actor="admin", reason=""),
        lambda: padmin.restore_qualification(referred, actor="admin", reason="no"),
        lambda: padmin.hold_reward(ref, 1, actor="", reason="valid reason"),
        lambda: padmin.revoke_milestone(ref, "founding_member", actor="admin", reason=""),
    ):
        status, body = call()
        assert status == 400
        assert body["error"] in {"actor_required", "reason_required"}


def test_prog_071_admin_approval_cannot_manufacture_a_qualification():
    """The strongest thing an admin can do is remove an obstacle.

    Approving someone who never posted twice still does not qualify them. This
    is the property that makes a compromised admin session survivable.
    """
    ref = mkuser(11100)
    referred = mkuser(11101)
    qual.attribute(ref, referred)  # profile only, zero posts
    status, body = padmin.approve_qualification(
        referred, actor="admin:7", reason="vouched for by community manager")
    assert status == 200
    assert body["state"] != qual.QUALIFIED
    assert qual.qualified_count(ref) == 0


def test_prog_072_there_is_no_generic_progress_setter():
    """Named decisions only. A generic setter is an unlimited cash printer."""
    exported = dir(padmin)
    for forbidden in ("set_qualified", "set_progress", "set_count",
                      "grant_reward", "award_reward", "set_state", "pay"):
        assert forbidden not in exported, forbidden


def test_prog_073_reject_then_restore_re_derives_from_facts():
    ref = mkuser(11200)
    referred = qualify(ref, 11201)
    padmin.reject_qualification(referred, actor="admin:7", reason="confirmed farm account")
    assert qual.qualified_count(ref) == 0
    padmin.restore_qualification(referred, actor="admin:9", reason="review was mistaken")
    assert qual.qualified_count(ref) == 1


def test_prog_074_every_admin_action_writes_a_private_audit_row():
    ref = mkuser(11300)
    referred = qualify(ref, 11301)
    padmin.reject_qualification(referred, actor="admin:42", reason="confirmed abuse ring")
    conn = db.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT event_type, visibility, actor, detail_json FROM progress_events "
        "WHERE campaign_id=? AND subject_user_id=? AND event_type LIKE 'admin_%'",
        (CAMP.campaign_id, referred)).fetchall()]
    conn.close()
    assert rows
    assert all(r["visibility"] == "private" for r in rows)
    assert any(r["actor"] == "admin:42" and "confirmed abuse ring" in r["detail_json"]
               for r in rows)


def test_prog_075_revoked_milestone_stops_counting_but_keeps_its_evidence():
    ref = mkuser(11400)
    _bring_to(ref, 11500, 30)
    ms.sync(ref)
    assert ms.has_milestone(ref, "founding_member")
    padmin.revoke_milestone(ref, "founding_member", actor="admin:7",
                            reason="qualifications traced to a farm")
    assert not ms.has_milestone(ref, "founding_member")
    conn = db.connect()
    row = dict(conn.execute(
        "SELECT revoked_at, revoked_reason FROM progress_milestone_awards "
        "WHERE campaign_id=? AND user_id=? AND milestone_key='founding_member'",
        (CAMP.campaign_id, ref)).fetchone())
    conn.close()
    assert row["revoked_at"]
    assert "farm" in row["revoked_reason"]


def test_prog_076_reward_hold_is_not_a_denial_and_release_is_not_an_approval():
    ref = mkuser(11600)
    _bring_to(ref, 11700, 30)
    ms.sync(ref)
    status, body = padmin.hold_reward(ref, 1, actor="admin:7", reason="investigating cluster")
    assert status == 200 and body["status"] == padmin.REWARD_HOLD
    status, body = padmin.release_reward(ref, 1, actor="admin:7", reason="cleared")
    assert status == 200 and body["status"] == padmin.REWARD_PENDING
    assert ms.reward_summary(ref)["available_cents"] == 0


def test_prog_077_review_queue_is_oldest_first():
    ref = mkuser(11800)
    for u in uid_block(11810, 3):
        referred = qualify(ref, u)
        qual.set_risk_state(referred, qual.RISK_REVIEW, reason="clustered signups")
    status, body = padmin.review_queue()
    assert status == 200
    stamps = [r["updated_at"] for r in body["queue"]]
    assert stamps == sorted(stamps)


def test_prog_078_reconcile_is_idempotent():
    ref = mkuser(11900)
    _bring_to(ref, 11910, 3)
    first = padmin.reconcile()
    second = padmin.reconcile()
    assert first["ok"] and second["ok"]
    assert second["changed"] == 0
    assert qual.qualified_count(ref) == 3


def test_prog_079_admin_inspection_carries_evidence_but_not_a_verdict():
    ref = mkuser(12000)
    referred = qualify(ref, 12001)
    status, body = padmin.inspect_referral(referred)
    assert status == 200
    assert body["posting_day_evidence"]
    assert body["qualification"]["state"] == qual.QUALIFIED
    blob = repr(body).lower()
    for forbidden in ("likely_fraud", "recommendation", "fraud_score", "verdict"):
        assert forbidden not in blob, forbidden


# =====================================================================
# Campaign versioning
# =====================================================================
def test_prog_080_campaign_rules_are_config_not_code():
    assert CAMP.qualification_target == 30
    assert CAMP.reward_interval == 30
    assert CAMP.reward_amount_cents == 3000
    assert CAMP.required_posting_days == 2
    assert CAMP.campaign_version >= 1


def test_prog_081_cycle_arithmetic_at_the_boundaries():
    for count, expected in ((0, 0), (1, 0), (29, 0), (30, 1), (31, 1),
                            (59, 1), (60, 2), (89, 2), (90, 3)):
        assert CAMP.cycles_earned(count) == expected, count


def test_prog_082_negative_and_garbage_counts_earn_nothing():
    assert CAMP.cycles_earned(-5) == 0
    assert CAMP.cycles_earned(0) == 0
