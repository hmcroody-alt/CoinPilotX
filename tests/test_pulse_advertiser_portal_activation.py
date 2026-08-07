"""§37: no campaign activation without verification, policy, eligibility and funding.

What `campaign_action("resume")` did before this suite existed: it looked up the
caller's role, wrote `status='active'`, and called `reserve_campaign_budget`,
which is the only check that ever ran and checks only the wallet. Nothing looked
at whether the ad account was allowed to advertise, whether any creative had been
approved, whether the campaign had a placement to run in, or even what state the
campaign was in when Resume was pressed.

Meanwhile the ad selector requires all of that and more. So the two halves of the
product disagreed about what "Active" meant, and the disagreement cost money: a
resume reserves real funds out of the advertiser's spendable balance, and it
reserved them for campaigns that could not have served a single impression.

The tests below are written as the questions an advertiser would ask.

  * "I pressed Resume and it said Active — is it running?"
  * "Why did nothing happen?" — and is the answer the same one the campaign card
    gave them, or a second, different reason?
  * "I archived this six months ago. Why is it spending again?"

The last one is not hypothetical. There was no precondition on the campaign's
current status anywhere in the function, so `resume` on an archived or completed
campaign set it back to active and reserved budget for it.

sqlite in memory, no app, no network: every defect here is in SQL and control
flow, and nothing else needs to be running to prove it.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import pulse_ads_service, pulse_advertiser_portal  # noqa: E402


SCHEMA = """
CREATE TABLE pulse_ad_accounts (
    id INTEGER PRIMARY KEY, owner_user_id INTEGER, business_name TEXT,
    business_type TEXT, business_email TEXT, business_website TEXT,
    status TEXT, verification_status TEXT,
    verification_submitted_at TEXT, verification_reviewed_at TEXT,
    verification_reviewer_id INTEGER, verification_reason TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE pulse_ad_campaigns (
    id INTEGER PRIMARY KEY, ad_account_id INTEGER, campaign_name TEXT,
    objective TEXT, status TEXT, budget_type TEXT,
    daily_budget_cents INTEGER, lifetime_budget_cents INTEGER, spent_cents INTEGER,
    start_at TEXT, end_at TEXT, priority INTEGER, pacing_mode TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE pulse_ad_creatives (
    id INTEGER PRIMARY KEY, ad_account_id INTEGER, campaign_id INTEGER,
    creative_type TEXT, title TEXT, status TEXT, moderation_status TEXT,
    media_asset_id INTEGER, thumbnail_asset_id INTEGER
);
CREATE TABLE pulse_ad_media_assets (
    id INTEGER PRIMARY KEY, moderation_status TEXT
);
CREATE TABLE pulse_ad_placements (
    id INTEGER PRIMARY KEY, placement_key TEXT, is_active INTEGER
);
CREATE TABLE pulse_ad_campaign_placements (
    id INTEGER PRIMARY KEY, campaign_id INTEGER, placement_id INTEGER
);
CREATE TABLE pulse_ad_team_members (
    id INTEGER PRIMARY KEY, account_id INTEGER, user_id INTEGER,
    role TEXT, status TEXT
);
CREATE TABLE pulse_ad_campaign_history (
    id INTEGER PRIMARY KEY, campaign_id INTEGER, actor_user_id INTEGER,
    action TEXT, before_json TEXT, after_json TEXT, created_at TEXT
);
CREATE TABLE pulse_ad_notifications (
    id INTEGER PRIMARY KEY, account_id INTEGER, campaign_id INTEGER,
    creative_id INTEGER, recipient_user_id INTEGER, notification_type TEXT,
    title TEXT, body TEXT, status TEXT, read_at TEXT, created_at TEXT
);
CREATE TABLE pulse_ad_audit_logs (
    id INTEGER PRIMARY KEY, actor_user_id INTEGER, action TEXT,
    entity_type TEXT, entity_id TEXT, before_json TEXT, after_json TEXT,
    ip_hash TEXT, user_agent_hash TEXT, created_at TEXT
);
"""

OWNER = 99


def _conn(
    *,
    account_status="active",
    verification="verified",
    verification_reason=None,
    campaign_status="paused",
    creatives=(("text", "approved", "approved", None),),
    placements=("feed_inline",),
    daily_budget=5000,
):
    """A campaign that can run, minus whatever the caller takes away.

    Every default is the passing value, so each test names exactly one thing it
    breaks. That is the point: a fixture where the defaults are broken makes it
    impossible to tell which condition a failure is about.

    `creatives` items are `(type, status, moderation_status, media_asset_id)`.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO pulse_ad_accounts (id, owner_user_id, business_name, status, verification_status, verification_reason)"
        " VALUES (1, ?, 'Roody Goods', ?, ?, ?)",
        (OWNER, account_status, verification, verification_reason),
    )
    conn.execute(
        "INSERT INTO pulse_ad_campaigns (id, ad_account_id, campaign_name, status, daily_budget_cents,"
        " lifetime_budget_cents, spent_cents) VALUES (7, 1, 'Summer drop', ?, ?, 0, 0)",
        (campaign_status, daily_budget),
    )
    for i, (kind, status, moderation, media_id) in enumerate(creatives):
        conn.execute(
            "INSERT INTO pulse_ad_creatives (id, ad_account_id, campaign_id, creative_type, title,"
            " status, moderation_status, media_asset_id) VALUES (?, 1, 7, ?, ?, ?, ?, ?)",
            (i + 1, kind, f"Creative {i + 1}", status, moderation, media_id),
        )
    for i, key in enumerate(placements):
        conn.execute("INSERT INTO pulse_ad_placements VALUES (?, ?, 1)", (i + 1, key))
        conn.execute("INSERT INTO pulse_ad_campaign_placements VALUES (?, 7, ?)", (i + 1, i + 1))
    conn.commit()
    return conn


def _resume(conn, user_id=OWNER):
    return pulse_advertiser_portal.campaign_action(conn, user_id, 7, "resume")


def _refusal(conn, user_id=OWNER):
    """Resume, expecting a refusal, and hand back the message the advertiser sees."""
    try:
        _resume(conn, user_id)
    except pulse_ads_service.PulseAdsError as exc:
        return str(exc)
    raise AssertionError("resume was allowed when it should have been refused")


def _status(conn):
    return conn.execute("SELECT status FROM pulse_ad_campaigns WHERE id=7").fetchone()["status"]


# --------------------------------------------------------------------------
# Verification — the gate that had a column, three readers, and no writer
# --------------------------------------------------------------------------

def test_unverified_account_cannot_resume():
    conn = _conn(account_status="pending_verification", verification="unverified")
    message = _refusal(conn)
    assert "verification" in message.lower()
    # And the campaign is untouched. A refusal that still writes the status is
    # worse than no gate, because now the record and the behaviour disagree.
    assert _status(conn) == "paused"


def test_pending_verification_says_it_is_pending_not_that_it_is_missing():
    # These are different instructions. "Request verification" sends someone to
    # do a thing they have already done.
    conn = _conn(account_status="pending_verification", verification="pending")
    assert "still in review" in _refusal(conn)


def test_rejected_verification_quotes_the_reason():
    conn = _conn(
        account_status="pending_verification",
        verification="rejected",
        verification_reason="The business website didn't resolve.",
    )
    message = _refusal(conn)
    assert "The business website didn't resolve." in message
    assert "again" in message  # there is a way back in


def test_suspended_account_is_named_before_verification():
    # A suspended account whose verification also lapsed should hear about the
    # suspension. Sending them to the verification queue wastes both their time
    # and the reviewer's, because approval would not unblock them.
    conn = _conn(account_status="suspended", verification="unverified")
    assert "suspended" in _refusal(conn).lower()


def test_verified_and_active_account_passes_the_account_gate():
    conn = _conn()
    assert pulse_advertiser_portal._account_activation_blocker(conn, 1) is None


def test_legacy_approved_wording_counts_as_verified():
    # Rows written by the one-off audit scripts carry 'approved'. They mean
    # verified, and a vocabulary change should not retroactively lock out the
    # accounts that predate it.
    conn = _conn(verification="approved")
    assert pulse_advertiser_portal._account_activation_blocker(conn, 1) is None


# --------------------------------------------------------------------------
# Policy — approval on both columns, and media the selector will accept
# --------------------------------------------------------------------------

def test_campaign_with_no_creative_cannot_resume():
    conn = _conn(creatives=())
    assert "Add an ad" in _refusal(conn)


def test_creative_still_in_review_is_not_reported_as_missing():
    conn = _conn(creatives=(("text", "pending", "pending", None),))
    message = _refusal(conn)
    assert "approved yet" in message
    assert "Add an ad" not in message


def test_all_creatives_rejected_points_at_the_policy_center():
    conn = _conn(creatives=(("text", "rejected", "rejected", None),))
    assert "Policy Center" in _refusal(conn)


def test_moderation_approved_but_status_not_is_still_blocked():
    # The selector requires `cr.status='approved'` AND `cr.moderation_status
    # ='approved'`. A gate that checked only one of them would pass campaigns the
    # selector then drops silently — the same contradiction, one layer down.
    conn = _conn(creatives=(("text", "draft", "approved", None),))
    _refusal(conn)


def test_image_creative_without_media_is_blocked():
    conn = _conn(creatives=(("image", "approved", "approved", None),))
    assert "media" in _refusal(conn).lower()


def test_image_creative_with_unapproved_media_is_blocked():
    conn = _conn(creatives=(("image", "approved", "approved", 4),))
    conn.execute("INSERT INTO pulse_ad_media_assets VALUES (4, 'pending')")
    conn.commit()
    assert "media" in _refusal(conn).lower()


def test_one_usable_creative_is_enough():
    # Advertisers routinely keep rejected and draft creatives alongside a live
    # one. Blocking on the presence of a bad creative rather than the absence of
    # a good one would stop campaigns that are perfectly able to run.
    conn = _conn(
        creatives=(
            ("text", "rejected", "rejected", None),
            ("text", "approved", "approved", None),
        )
    )
    assert pulse_advertiser_portal._creative_activation_blocker(conn, 7) is None


# --------------------------------------------------------------------------
# Eligibility and budget
# --------------------------------------------------------------------------

def test_campaign_with_no_placement_cannot_resume():
    conn = _conn(placements=())
    assert "placement" in _refusal(conn).lower()


def test_campaign_with_no_budget_cannot_resume():
    conn = _conn(daily_budget=0)
    assert "budget" in _refusal(conn).lower()


def test_account_is_named_before_the_creative():
    """Gate order has to match the campaign card, or the advertiser chases ghosts.

    The client's `deliveryBlocker` reports account, then creative, then
    placement, then budget. If the server picked a different first blocker, an
    advertiser would fix the one the card named, press Resume, and be told
    something else entirely — twice, in either order, with no way to know how
    many more there were.
    """
    conn = _conn(
        account_status="pending_verification",
        verification="unverified",
        creatives=(),
        placements=(),
        daily_budget=0,
    )
    assert "verification" in _refusal(conn).lower()


# --------------------------------------------------------------------------
# Transitions — "archived" was a label, not an end state
# --------------------------------------------------------------------------

def test_archived_campaign_cannot_be_resumed():
    conn = _conn(campaign_status="archived")
    message = _refusal(conn)
    assert "archived" in message.lower()
    assert "Duplicate" in message  # §37: a refusal with somewhere to go
    assert _status(conn) == "archived"


def test_completed_campaign_cannot_be_resumed():
    conn = _conn(campaign_status="completed")
    assert "finished" in _refusal(conn).lower()
    assert _status(conn) == "completed"


def test_draft_campaign_is_told_to_submit_rather_than_resume():
    conn = _conn(campaign_status="draft")
    assert "Submit" in _refusal(conn)


def test_campaign_in_review_is_not_told_to_submit_again():
    conn = _conn(campaign_status="pending_review")
    message = _refusal(conn)
    assert "in review" in message.lower()
    assert "Submit" not in message


def test_archiving_a_running_campaign_asks_for_a_pause_first():
    conn = _conn(campaign_status="active")
    try:
        pulse_advertiser_portal.campaign_action(conn, OWNER, 7, "archive")
    except pulse_ads_service.PulseAdsError as exc:
        assert "Pause" in str(exc)
    else:
        raise AssertionError("archive from active should ask for a pause")
    assert _status(conn) == "active"


def test_pausing_a_paused_campaign_is_not_an_error():
    # Idempotence, not indulgence: a second press of a button whose first press
    # worked should not produce an error the reader has to interpret.
    conn = _conn(campaign_status="paused")
    result = pulse_advertiser_portal.campaign_action(conn, OWNER, 7, "pause")
    assert result["status"] == "paused"


# --------------------------------------------------------------------------
# Role — the false 404
# --------------------------------------------------------------------------

def test_campaign_manager_is_told_why_not_that_the_campaign_vanished():
    """`reserve_campaign_budget` answers 404 "Campaign not found." for non-owners.

    A campaign manager is one of `WRITE_ROLES`; `campaign_action` authorises them
    four lines before handing off to the payment layer, which then denies them
    with a sentence that is not true. The campaign exists. They cannot spend from
    the wallet, which is a different fact, a different remedy, and a 403.
    """
    conn = _conn()
    conn.execute("INSERT INTO pulse_ad_team_members VALUES (1, 1, 42, 'campaign_manager', 'active')")
    conn.commit()
    message = _refusal(conn, user_id=42)
    assert "not found" not in message.lower()
    assert "owner" in message.lower()
    assert "wallet" in message.lower()


def test_the_role_refusal_comes_after_the_campaign_is_actually_fixable():
    # The role message must not pre-empt a blocker the manager could clear
    # themselves. Telling someone "only the owner can do this" when the real
    # problem is a missing placement sends them to ask for permission they do not
    # need.
    conn = _conn(placements=())
    conn.execute("INSERT INTO pulse_ad_team_members VALUES (1, 1, 42, 'campaign_manager', 'active')")
    conn.commit()
    assert "placement" in _refusal(conn, user_id=42).lower()


# --------------------------------------------------------------------------
# The verification lifecycle itself
# --------------------------------------------------------------------------

def test_submitting_verification_moves_the_account_into_review():
    conn = _conn(account_status="pending_verification", verification="unverified")
    result = pulse_ads_service.submit_account_verification(conn, OWNER, 1, {})
    assert result["verification_status"] == "pending"
    assert pulse_advertiser_portal._account_activation_blocker(conn, 1)[0] == "account_verification_pending"


def test_verification_cannot_be_submitted_twice_while_it_is_in_review():
    conn = _conn(account_status="pending_verification", verification="pending")
    try:
        pulse_ads_service.submit_account_verification(conn, OWNER, 1, {})
    except pulse_ads_service.PulseAdsError as exc:
        assert "already in review" in str(exc)
    else:
        raise AssertionError("a second submission should be refused")


def test_approval_writes_the_column_the_selector_actually_reads():
    """`select_ads` tests `a.status='active'`, not `verification_status`.

    Approving verification without writing `status` would produce an account
    labelled verified whose ads still never appear — the exact split between the
    record and the behaviour this phase exists to close. So this asserts both
    columns, and it is the single most important assertion in the file.
    """
    conn = _conn(account_status="pending_verification", verification="pending")
    pulse_ads_service.approve_account_verification(conn, 1, 1, "Looks fine")
    row = conn.execute("SELECT status, verification_status FROM pulse_ad_accounts WHERE id=1").fetchone()
    assert row["status"] == "active"
    assert row["verification_status"] == "verified"
    assert pulse_advertiser_portal._account_activation_blocker(conn, 1) is None


def test_a_suspended_account_cannot_be_verified_around_the_suspension():
    conn = _conn(account_status="suspended", verification="pending")
    try:
        pulse_ads_service.approve_account_verification(conn, 1, 1, "")
    except pulse_ads_service.PulseAdsError as exc:
        assert "suspended" in str(exc)
    else:
        raise AssertionError("approving a suspended account should be refused")


def test_rejection_requires_a_reason():
    conn = _conn(account_status="pending_verification", verification="pending")
    try:
        pulse_ads_service.reject_account_verification(conn, 1, 1, "")
    except pulse_ads_service.PulseAdsError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("a rejection with no reason should be refused")


def test_a_rejected_account_can_try_again():
    """The whole point of storing the reason.

    A rejection the advertiser cannot answer is a locked door with no sign on it,
    which is the dead end §37 forbids. Reject, read the reason, resubmit.
    """
    conn = _conn(account_status="pending_verification", verification="pending")
    pulse_ads_service.reject_account_verification(conn, 1, 1, "Business name doesn't match the website.")
    assert "doesn't match" in _refusal(conn)
    result = pulse_ads_service.submit_account_verification(conn, OWNER, 1, {})
    assert result["verification_status"] == "pending"


def test_the_review_board_is_oldest_first():
    # It is a queue someone is sitting in, not a feed.
    conn = _conn(account_status="pending_verification", verification="unverified")
    conn.execute(
        "INSERT INTO pulse_ad_accounts (id, owner_user_id, business_name, status, verification_status,"
        " verification_submitted_at) VALUES (2, 5, 'Later Co', 'pending_verification', 'pending', '2026-02-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO pulse_ad_accounts (id, owner_user_id, business_name, status, verification_status,"
        " verification_submitted_at) VALUES (3, 6, 'Earlier Co', 'pending_verification', 'pending', '2026-01-01T00:00:00+00:00')"
    )
    conn.commit()
    board = pulse_ads_service.account_review_board(conn)
    assert [row["business_name"] for row in board] == ["Earlier Co", "Later Co"]


def test_the_review_board_excludes_decided_accounts():
    conn = _conn()  # verified
    assert pulse_ads_service.account_review_board(conn) == []
