"""Business OS — the marketplace REVIEW AUTHORITY, §1/§9/§15/§17/§18/§34/§37/§38.

The mission's premise was that no backend review queue existed. One does:
``/admin/marketplace-command``, titled "Marketplace Review", with approve,
reject, request-changes, a mandatory reason and an audit row. The real defect
was that the *authority* behind it lived inline in the body of a Flask route
that also renders an HTML table, so a second caller — a bulk endpoint — could
not ask it anything. The only way to add one over an inline rule is to write
the rule a second time, and then "may this be approved" has two answers.

So these tests are about a rule being answerable, and about the two gates the
inline version did not have:

  * **§18 self-review.** Admin rights and a seller account are not exclusive
    here. Nothing in the stack stopped a moderator approving their own product,
    and the inline check did not look at who was asking — it took no reviewer
    at all. Enforced on every verdict, not only on approve: a reviewer
    *rejecting* a competitor's listing is the same conflict wearing the other
    hat.
  * **§34 prohibited goods.** A human clicking Approve on a weapons listing is
    not evidence that the goods policy changed. This is the one block a
    reviewer cannot talk past, and it must not be reachable by routing around
    the page.

And three properties that are cheap to delete by accident:

  * **§38 the queue and the gate ask one question.** ``queue_sql`` is the SQL
    form of the same predicate ``block_reason`` evaluates in Python. Asserted
    by running both over the same rows in a real database — a queue that lists
    what the gate refuses, or hides what it would allow, is the "no fake review"
    failure the mission names, and neither half looks wrong on its own.
  * **§15 blocked is not dropped.** An id with no row is reported, not silently
    removed from the batch. A batch that shrinks reports a ``requested_count``
    smaller than the reviewer's selection, and the products that vanished are
    exactly the ones they needed telling about.
  * **§37 approving is not publishing.** Approval writes two of the five
    conditions the lifecycle requires for discovery. The read-back must be able
    to say "approved, and still not visible", or "Approved / Live" is a claim
    about buyers that nobody checked.

Run standalone::

    ./.venv/bin/python3 -m pytest tests/business_os/test_listing_review_authority.py
"""

import os
import sqlite3
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="listing_review_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services import marketplace_listing_lifecycle as lifecycle  # noqa: E402
from services.business_os.marketplace import listing_review as rv  # noqa: E402

SELLER = 5501
REVIEWER = 9901


def listing(**overrides):
    """A listing sitting in review: the merchant released it, nobody decided yet.

    Both axes matter and the fixture sets both, because a fixture that set only
    ``approval_status`` would be describing a state ``awaiting_moderation``
    deliberately refuses — an untouched draft, whose approval column reads
    ``pending_review`` only because that is the column default.
    """
    base = {
        "id": 1,
        "seller_user_id": SELLER,
        "title": "Brass desk lamp",
        "description": "A weighted brass lamp with a linen shade.",
        "category": "Home",
        "price_label": "$24.00",
        "cover_image_url": "https://cdn.example/lamp.jpg",
        "status": lifecycle.PENDING_REVIEW,
        "approval_status": lifecycle.PENDING_REVIEW,
        "product_type": "physical",
        "quantity": 12,
    }
    base.update(overrides)
    return base


# --- §1: one rule, and it is a function ---------------------------------------


def test_a_listing_in_review_can_be_decided():
    for action in rv.ACTIONS:
        assert rv.block_reason(listing(), action, reviewer_id=REVIEWER) is None, action


def test_a_missing_row_is_reported_and_not_treated_as_decidable():
    assert rv.block_reason(None, rv.APPROVE, reviewer_id=REVIEWER) == rv.NOT_FOUND
    assert rv.block_reason({}, rv.APPROVE, reviewer_id=REVIEWER) == rv.NOT_FOUND


def test_a_draft_the_merchant_never_released_cannot_be_approved():
    """The protection inside ``awaiting_moderation``, restated at this level.

    ``approval_status`` is ``DEFAULT 'pending_review'``, so every untouched
    draft in the table reads as awaiting a decision on the moderation axis
    alone. If this gate asked only that column, a moderator could one-click
    publish an unpriced, coverless, never-submitted draft belonging to someone
    who has not finished writing it.
    """
    draft = listing(status="draft")
    assert rv.block_reason(draft, rv.APPROVE, reviewer_id=REVIEWER) == rv.NOT_AWAITING_REVIEW


def test_a_listing_already_decided_cannot_be_decided_again():
    for approval in (lifecycle.APPROVED, lifecycle.REJECTED, lifecycle.CHANGES_REQUESTED):
        row = listing(status=lifecycle.PUBLISHED, approval_status=approval)
        assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) == rv.NOT_AWAITING_REVIEW


def test_a_dropship_listing_published_before_review_is_still_decidable():
    """The case that made approval unreachable before ``awaiting_moderation``.

    ``drafts.publish`` sets ``status='published'`` and deliberately leaves the
    moderation axis alone, because discovery needs both and so the row is
    correctly invisible. A gate reading ``status`` alone calls that "already
    decided" and returns 409 — which is how every CJ import ended up needing a
    hand-written UPDATE to go live.
    """
    row = listing(status=lifecycle.PUBLISHED, approval_status=lifecycle.PENDING_REVIEW)
    assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) is None


# --- §18: a reviewer is not a party to their own listing ----------------------


def test_a_reviewer_cannot_approve_their_own_listing():
    row = listing(seller_user_id=REVIEWER)
    assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) == rv.SELF_REVIEW


def test_self_review_is_blocked_on_every_verdict_not_only_approval():
    """Rejecting your own competitor is the same conflict, other way round."""
    row = listing(seller_user_id=REVIEWER)
    for action in rv.ACTIONS:
        assert rv.block_reason(row, action, reviewer_id=REVIEWER) == rv.SELF_REVIEW, action


def test_ownership_is_compared_as_text_so_a_string_id_is_still_the_same_person():
    """The two ids reach this function from different places.

    ``seller_user_id`` comes off a database row and ``reviewer_id`` off the
    admin session; one of them being ``"9901"`` where the other is ``9901`` is
    an ordinary consequence of that, and an ``==`` on mixed types would have
    quietly opened the gate.
    """
    assert rv.block_reason(listing(seller_user_id="9901"), rv.APPROVE,
                           reviewer_id=9901) == rv.SELF_REVIEW
    assert rv.block_reason(listing(seller_user_id=9901), rv.APPROVE,
                           reviewer_id=" 9901 ") == rv.SELF_REVIEW


def test_another_sellers_listing_is_not_self_review():
    assert rv.block_reason(listing(seller_user_id=SELLER), rv.APPROVE,
                           reviewer_id=REVIEWER) is None


def test_an_unowned_listing_does_not_match_an_unknown_reviewer():
    """Two blanks are not the same person.

    ``""`` == ``""`` is true, and a row with no seller judged by a request with
    no reviewer would block on SELF_REVIEW — a listing nobody owns becoming
    permanently undecidable.
    """
    assert rv.block_reason(listing(seller_user_id=None), rv.APPROVE,
                           reviewer_id=None) is None
    assert rv.block_reason(listing(seller_user_id=""), rv.APPROVE,
                           reviewer_id="") is None


# --- §34: the policy outranks the human ---------------------------------------


def test_a_prohibited_category_cannot_be_approved_by_a_human():
    row = listing(category="Weapons")
    assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) == rv.PROHIBITED


def test_a_prohibited_signal_in_the_copy_cannot_be_approved_either():
    """The category is clean and the product is not. Both routes into PROHIBITED."""
    row = listing(title="Counterfeit designer handbag",
                  description="A counterfeit bag, indistinguishable from the real one.")
    assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) == rv.PROHIBITED


def test_a_prohibited_product_can_still_be_rejected():
    """Blocking every verdict would leave the listing with no reachable answer.

    The point of the §34 gate is that a prohibited product cannot go live, not
    that it cannot be dealt with. A reviewer must be able to reject it, and the
    seller must get the reason.
    """
    row = listing(category="Weapons")
    for action in (rv.REJECT, rv.REQUEST_CHANGES, rv.RESTRICT):
        assert rv.block_reason(row, action, reviewer_id=REVIEWER) is None, action


def test_a_category_needing_manual_review_is_deliberately_not_blocked():
    """Deciding one of these is exactly the reviewer's job.

    ``MANUAL_REVIEW_REQUIRED`` is not ``PROHIBITED``. Folding the two together
    would be the easy mistake, and it would strand every luxury-goods, medical
    -device and collectibles listing in the queue with no verdict a reviewer
    could record — the review gate refusing the listings that exist *because*
    they need a human.
    """
    from services import marketplace_goods_policy as goods
    for category in ("Luxury goods", "Medical devices", "High value collectibles"):
        row = listing(category=category)
        assert goods.evaluate(row)["decision"] == "MANUAL_REVIEW_REQUIRED", category
        assert rv.block_reason(row, rv.APPROVE, reviewer_id=REVIEWER) is None, category


def test_every_block_code_has_something_the_reviewer_can_read():
    for code in (rv.NOT_FOUND, rv.NOT_AWAITING_REVIEW, rv.SELF_REVIEW, rv.PROHIBITED):
        assert rv.BLOCK_NOTES.get(code, "").strip(), code


# --- §9/§36: a rejection carries a reason, and the reason is data -------------


def test_a_rejection_without_a_reason_is_refused():
    for action in sorted(rv.REASON_REQUIRED):
        with pytest.raises(rv.BatchError) as caught:
            rv.normalize_reason(action, "", "")
        assert caught.value.code == "REASON_REQUIRED"
        with pytest.raises(rv.BatchError):
            rv.normalize_reason(action, None, "a note with no code")


def test_an_unrecognised_reason_code_is_refused_and_not_filed_as_other():
    """A typo that lands in the audit trail as a real category is the worse
    outcome: it is counted, reported on, and never noticed."""
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_reason(rv.REJECT, "PROHIBTED_PRODUCT", "")
    assert caught.value.code == "UNKNOWN_REASON"


def test_a_reason_code_is_accepted_in_any_case_the_client_sends_it():
    assert rv.normalize_reason(rv.REJECT, "invalid_price", "")["reason_code"] == rv.INVALID_PRICE
    assert rv.normalize_reason(rv.REJECT, "  Invalid_Price  ", "")["reason_code"] == rv.INVALID_PRICE


def test_an_approval_carries_no_rejection_category():
    """Accepting one would put a rejection reason on an approved listing's row."""
    reason = rv.normalize_reason(rv.APPROVE, rv.PROHIBITED_PRODUCT, "looks fine")
    assert reason["reason_code"] == ""
    assert reason["note"] == "looks fine"


def test_a_reviewer_note_is_bounded():
    reason = rv.normalize_reason(rv.REJECT, rv.OTHER, "x" * 5000)
    assert len(reason["note"]) == 1200


def test_every_reason_code_has_a_seller_sentence():
    for code in rv.REASON_CODES:
        message = rv.seller_message(code)
        assert message.strip() and message != code, code


def test_the_seller_sentence_is_a_lookup_and_cannot_carry_a_private_note():
    """§43. The one way a reviewer's internal note reaches a merchant is by
    being interpolated into the message, so the message is not assembled."""
    internal = "seller is a repeat offender, risk score 91, cj_account=acct_71ff"
    assert internal not in rv.seller_message(rv.POLICY_VIOLATION)
    assert rv.seller_message("NOT_A_CODE") == rv.SELLER_MESSAGES[rv.OTHER]


# --- §16: what a valid batch request is ---------------------------------------


def test_an_unknown_action_is_refused():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request("delete", [1], idempotency_key="k")
    assert caught.value.code == "UNKNOWN_ACTION"


def test_suspend_and_archive_are_not_review_verdicts():
    """They act on listings whose review already concluded. Folding them in
    would make "has this been reviewed" unanswerable from the action name."""
    for action in ("suspend", "archive", "feature"):
        with pytest.raises(rv.BatchError):
            rv.normalize_request(action, [1], idempotency_key="k")


def test_a_batch_without_an_idempotency_key_is_refused():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request(rv.APPROVE, [1], idempotency_key="  ")
    assert caught.value.code == "IDEMPOTENCY_KEY_REQUIRED"


def test_an_empty_selection_is_refused():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request(rv.APPROVE, [], idempotency_key="k")
    assert caught.value.code == "NO_LISTINGS"


def test_a_duplicate_tick_is_one_decision_and_the_order_survives():
    normalized = rv.normalize_request(rv.APPROVE, [7, 3, 7, 9, 3], idempotency_key="k")
    assert normalized["listing_ids"] == [7, 3, 9]


def test_a_selection_larger_than_the_cap_is_refused():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request(rv.APPROVE, list(range(1, rv.MAX_BATCH + 2)),
                             idempotency_key="k")
    assert caught.value.code == "BATCH_TOO_LARGE"
    assert rv.normalize_request(rv.APPROVE, list(range(1, rv.MAX_BATCH + 1)),
                                idempotency_key="k")["listing_ids"]


def test_something_that_is_not_a_listing_id_is_refused_not_skipped():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request(rv.APPROVE, [1, "all"], idempotency_key="k")
    assert caught.value.code == "INVALID_LISTING_ID"


def test_a_rejection_batch_validates_its_reason_too():
    with pytest.raises(rv.BatchError) as caught:
        rv.normalize_request(rv.REJECT, [1], idempotency_key="k")
    assert caught.value.code == "REASON_REQUIRED"


# --- §17: the fingerprint of what was asked for -------------------------------


def test_the_same_selection_in_a_different_order_is_the_same_request():
    assert rv.request_hash(rv.APPROVE, [3, 1, 2]) == rv.request_hash(rv.APPROVE, [1, 2, 3])


def test_two_rejections_of_the_same_listings_for_different_reasons_differ():
    """Replaying the first answer for the second would file the wrong category
    against every row in the batch."""
    assert rv.request_hash(rv.REJECT, [1, 2], rv.INVALID_PRICE) != \
        rv.request_hash(rv.REJECT, [1, 2], rv.INVALID_MEDIA)


def test_approving_and_rejecting_the_same_listings_are_different_requests():
    assert rv.request_hash(rv.APPROVE, [1, 2]) != rv.request_hash(rv.REJECT, [1, 2])


def test_adding_one_listing_changes_the_request():
    assert rv.request_hash(rv.APPROVE, [1, 2]) != rv.request_hash(rv.APPROVE, [1, 2, 3])


# --- §15: the honest split ----------------------------------------------------


def test_the_split_reports_every_id_that_was_asked_about():
    rows = [listing(id=1), listing(id=2, status="draft"), listing(id=3, seller_user_id=REVIEWER)]
    verdict = rv.evaluate_rows(rows, [1, 2, 3, 4], rv.APPROVE, reviewer_id=REVIEWER)

    assert verdict["eligible"] == [1]
    assert [entry["listing_id"] for entry in verdict["blocked"]] == [2, 3, 4]
    assert len(verdict["eligible"]) + len(verdict["blocked"]) == 4


def test_an_id_with_no_row_is_blocked_rather_than_dropped():
    """A batch that silently shrinks reports a count smaller than the reviewer's
    selection, and the two products that vanished are the ones they needed
    telling about."""
    verdict = rv.evaluate_rows([], [41, 42], rv.APPROVE, reviewer_id=REVIEWER)
    assert verdict["eligible"] == []
    assert [entry["error_code"] for entry in verdict["blocked"]] == [rv.NOT_FOUND] * 2


def test_every_blocked_entry_carries_the_reason_and_a_sentence():
    verdict = rv.evaluate_rows([listing(id=1, category="Weapons")], [1], rv.APPROVE,
                               reviewer_id=REVIEWER)
    entry = verdict["blocked"][0]
    assert entry["outcome"] == rv.BLOCKED
    assert entry["error_code"] == rv.PROHIBITED
    assert entry["blockers"] == [rv.PROHIBITED]
    assert entry["note"] == rv.BLOCK_NOTES[rv.PROHIBITED]


def test_a_summary_of_a_mixed_batch_agrees_with_its_own_detail():
    rows = [listing(id=n) for n in range(1, 24)]
    verdict = rv.evaluate_rows(rows, list(range(1, 26)), rv.APPROVE, reviewer_id=REVIEWER)
    results = verdict["blocked"] + [
        rv.result_entry(listing_id, rv.SUCCEEDED) for listing_id in verdict["eligible"]]

    summary = rv.summarize("mrb_test", rv.APPROVE, results)
    assert summary["requested_count"] == 25
    assert summary["successful_count"] == 23
    assert summary["blocked_count"] == 2
    assert summary["failed_count"] == 0


# --- §37: approving is not publishing -----------------------------------------


def test_the_read_back_reports_live_when_every_condition_is_met():
    row = listing(status=lifecycle.PUBLISHED, approval_status=lifecycle.APPROVED,
                  seller_status="approved", store_name="Lamp Co")
    back = rv.publication_readback(row)
    assert back == {"review_state": "approved", "publication_state": "published",
                    "live": True, "blockers": [], "note": ""}


def test_an_approved_listing_behind_a_suspended_seller_is_not_reported_live():
    """The failure §37 exists for: HTTP 200, "Listing updated.", and no buyer
    can reach it. Approval writes two of five conditions."""
    row = listing(status=lifecycle.PUBLISHED, approval_status=lifecycle.APPROVED,
                  seller_status="suspended", store_name="Lamp Co")
    back = rv.publication_readback(row)
    assert back["review_state"] == "approved"
    assert back["live"] is False
    assert back["blockers"] == ["seller_approved"]
    assert back["note"] == "the seller account is not approved"


def test_an_approved_listing_with_no_stock_is_not_reported_live():
    row = listing(status=lifecycle.PUBLISHED, approval_status=lifecycle.APPROVED,
                  seller_status="approved", store_name="Lamp Co", quantity=0)
    back = rv.publication_readback(row)
    assert back["live"] is False
    assert back["blockers"] == ["in_stock"]


def test_a_rejected_listing_reads_back_as_not_live():
    row = listing(status=lifecycle.REJECTED, approval_status=lifecycle.REJECTED,
                  seller_status="approved", store_name="Lamp Co")
    back = rv.publication_readback(row)
    assert back["review_state"] == "rejected"
    assert back["publication_state"] == "rejected"
    assert back["live"] is False


def test_the_read_back_verdict_is_the_lifecycles_and_not_a_second_opinion():
    """Recomputed here, this sentence would drift away from the predicate that
    actually hides the row from buyers."""
    for status in (lifecycle.PUBLISHED, lifecycle.REJECTED, "draft", "active"):
        for seller in ("approved", "suspended"):
            row = listing(status=status, approval_status=lifecycle.APPROVED,
                          seller_status=seller, store_name="Lamp Co")
            assert rv.publication_readback(row)["live"] is (
                not lifecycle.publication_blocker(row))


# --- §1: restrict parks moderation without discarding the merchant's release --


def test_restrict_leaves_the_merchants_release_alone():
    """A restricted listing is not a rejected one. Overwriting ``status`` would
    lose the fact that the merchant had released it, which is what a later
    approval needs in order to actually publish rather than silently do
    nothing."""
    status, approval = rv.TRANSITIONS[rv.RESTRICT]
    assert status is None
    assert approval == "restricted"


def test_every_action_has_exactly_one_transition():
    assert set(rv.TRANSITIONS) == set(rv.ACTIONS)
    assert rv.TRANSITIONS[rv.APPROVE] == (lifecycle.PUBLISHED, lifecycle.APPROVED)


def test_the_state_a_verdict_lands_in_is_not_one_that_can_be_decided_again():
    """Otherwise the queue never drains: a listing would reappear after every
    decision except the one that happens to name a terminal state."""
    for action in rv.ACTIONS:
        status, approval = rv.TRANSITIONS[action]
        landed = listing(status=status or lifecycle.PUBLISHED, approval_status=approval)
        assert not lifecycle.awaiting_moderation(landed), action


# --- §38: the queue and the gate ask one question -----------------------------


def test_the_queue_predicate_is_the_lifecycles_own():
    assert rv.queue_sql("l") == lifecycle.awaiting_moderation_sql("l")
    assert rv.queue_sql("x") != rv.queue_sql("l"), "the alias is not being applied"


def test_no_pending_listing_is_unreachable_and_no_listed_one_is_undecidable():
    """The §38 failure is a *pair* and neither half looks wrong alone.

    A queue that omits a listing the gate would approve is the "in review, but
    nowhere to review it" report the mission opens with. A queue that lists one
    the gate refuses is a reviewer clicking Approve and getting 409 forever.
    Both are only visible by running the SQL and the Python over the same rows.
    """
    rows = []
    for index, (status, approval) in enumerate([
        (lifecycle.PENDING_REVIEW, lifecycle.PENDING_REVIEW),
        (lifecycle.PUBLISHED, lifecycle.PENDING_REVIEW),
        ("active", "review_ready"),
        ("review_ready", lifecycle.PENDING_REVIEW),
        ("draft", lifecycle.PENDING_REVIEW),
        (lifecycle.PUBLISHED, lifecycle.APPROVED),
        (lifecycle.REJECTED, lifecycle.REJECTED),
        ("draft", "draft"),
        ("", ""),
        (None, None),
    ], start=1):
        rows.append({"id": index, "status": status, "approval_status": approval})

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE marketplace_listings "
                 "(id INTEGER PRIMARY KEY, status TEXT, approval_status TEXT)")
    conn.executemany("INSERT INTO marketplace_listings VALUES (?,?,?)",
                     [(r["id"], r["status"], r["approval_status"]) for r in rows])
    listed = {row[0] for row in conn.execute(
        f"SELECT id FROM marketplace_listings l WHERE {rv.queue_sql('l')}")}
    conn.close()

    decidable = {
        row["id"] for row in rows
        if rv.block_reason(dict(row, seller_user_id=SELLER), rv.APPROVE,
                           reviewer_id=REVIEWER) is None}

    assert listed == decidable, (
        f"queue and gate disagree: only in queue {sorted(listed - decidable)}, "
        f"only decidable {sorted(decidable - listed)}")
    assert listed, "the fixture no longer contains a single reviewable listing"


# --- §26/§27/§28/§29/§42: the queue is reachable ------------------------------


QUEUE_ROWS = [
    # id, status, approval_status, title, category, seller, safety, created_at
    (1, "pending_review", "pending_review", "Brass desk lamp", "Home", 5501, 4, "2026-01-01"),
    (2, "published", "pending_review", "Cross Border Jeans Ripped Mid Waist", "Apparel", 5502, 9, "2026-01-02"),
    (3, "active", "review_ready", "Ceramic mug", "Home", 5501, 1, "2026-01-03"),
    (4, "changes_requested", "changes_requested", "Wool scarf", "Apparel", 5502, 2, "2026-01-04"),
    (5, "rejected", "rejected", "Counterfeit handbag", "Apparel", 5503, 88, "2026-01-05"),
    (6, "published", "approved", "Oak side table", "Home", 5501, 0, "2026-01-06"),
    (7, "published", "restricted", "Vintage watch", "Luxury goods", 5503, 40, "2026-01-07"),
    (8, "draft", "pending_review", "Half-written listing", "Home", 5502, 0, "2026-01-08"),
]


@pytest.fixture()
def queue_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE marketplace_listings (
        id INTEGER PRIMARY KEY, status TEXT, approval_status TEXT, title TEXT,
        description TEXT DEFAULT '', category TEXT, seller_user_id INTEGER,
        safety_score INTEGER, created_at TEXT)""")
    conn.executemany(
        "INSERT INTO marketplace_listings"
        " (id,status,approval_status,title,category,seller_user_id,safety_score,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)", QUEUE_ROWS)
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def run_queue(conn, **params):
    """Exactly how the page builds a page: one predicate, used twice."""
    query = rv.normalize_query(params)
    where, args = rv.queue_where(query, "l")
    total = conn.execute(
        f"SELECT COUNT(*) FROM marketplace_listings l WHERE {where}", args).fetchone()[0]
    window = rv.page_window(query, total)
    rows = conn.execute(
        f"SELECT l.id FROM marketplace_listings l WHERE {where} "
        f"ORDER BY {rv.queue_order(query, 'l')} LIMIT ? OFFSET ?",
        args + [window["page_size"], window["offset"]]).fetchall()
    return [row[0] for row in rows], window


def test_the_default_slice_is_the_work_queue(queue_db):
    ids, window = run_queue(queue_db)
    assert ids == [1, 2, 3]
    assert window["total"] == 3


def test_an_unrecognised_filter_falls_back_to_the_work_queue_not_the_catalogue(queue_db):
    """A typo must not quietly show a reviewer every listing in the marketplace
    and let them read it as their queue."""
    assert run_queue(queue_db, filter="pendign")[0] == [1, 2, 3]
    assert run_queue(queue_db, filter="")[0] == [1, 2, 3]


def test_the_default_order_is_oldest_first_so_the_backlog_drains(queue_db):
    """Newest-first is what every other admin table does and it is exactly wrong
    here: the listings that waited longest are the ones never reached."""
    assert run_queue(queue_db)[0] == [1, 2, 3]
    assert run_queue(queue_db, sort="newest")[0] == [3, 2, 1]


def test_an_unrecognised_sort_is_not_interpolated_into_the_query(queue_db):
    ids, _ = run_queue(queue_db, sort="id; DROP TABLE marketplace_listings")
    assert ids == [1, 2, 3]
    assert queue_db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0] == 8


def test_sorting_by_risk_puts_the_worst_first(queue_db):
    assert run_queue(queue_db, sort="risk")[0] == [2, 1, 3]


def test_each_decided_slice_shows_what_was_decided(queue_db):
    assert run_queue(queue_db, filter="changes_requested")[0] == [4]
    assert run_queue(queue_db, filter="rejected")[0] == [5]
    assert run_queue(queue_db, filter="approved")[0] == [6]
    assert run_queue(queue_db, filter="restricted")[0] == [7]


def test_the_all_slice_includes_the_drafts_the_queue_correctly_hides(queue_db):
    ids, window = run_queue(queue_db, filter="all")
    assert window["total"] == len(QUEUE_ROWS)
    assert 8 in ids, "the unreleased draft should be findable, just not reviewable"


def test_a_draft_is_never_in_the_work_queue(queue_db):
    """Its approval column reads ``pending_review`` only because that is the
    default. The gate refuses it, so the queue must not offer it."""
    assert 8 not in run_queue(queue_db)[0]


def test_search_reaches_a_listing_that_is_not_on_the_current_page(queue_db):
    """§27. Filtering the rows already fetched finds a listing only when the
    reviewer had already scrolled to it -- which is when they did not need to
    search."""
    ids, _ = run_queue(queue_db, filter="all", q="cross border")
    assert ids == [2]


def test_search_matches_the_category_too(queue_db):
    assert run_queue(queue_db, filter="all", q="apparel")[0] == [2, 4, 5]


def test_a_pasted_listing_id_finds_that_listing_and_not_a_digit_match(queue_db):
    queue_db.execute("UPDATE marketplace_listings SET description=? WHERE id=1",
                     ("Stock code 7 of the winter range",))
    ids, _ = run_queue(queue_db, filter="all", q="7")
    assert ids == [7], "an id search should not return every row containing that digit"


def test_a_seller_id_finds_that_sellers_listings(queue_db):
    assert run_queue(queue_db, filter="all", q="5503")[0] == [5, 7]


def test_search_does_not_escape_into_the_sql(queue_db):
    ids, window = run_queue(queue_db, filter="all", q="' OR 1=1 --")
    assert ids == []
    assert window["total"] == 0
    assert queue_db.execute("SELECT COUNT(*) FROM marketplace_listings").fetchone()[0] == 8


def test_the_count_and_the_page_are_built_from_one_predicate(queue_db):
    """§29. A badge counted with one predicate over a list built with another is
    "340 pending" above a table that ends at row 100."""
    for slice_name in rv.QUEUE_FILTERS:
        ids, window = run_queue(queue_db, filter=slice_name, page_size=rv.MAX_BATCH)
        assert len(ids) == window["total"], slice_name


def test_no_pending_listing_is_stranded_past_the_last_page(queue_db):
    """§38/§42. The defect this replaces was a bare ``LIMIT 100``: listing 101
    was in review, counted, and unreachable by any reviewer."""
    queue_db.executemany(
        "INSERT INTO marketplace_listings"
        " (id,status,approval_status,title,category,seller_user_id,safety_score,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(100 + n, "pending_review", "pending_review", f"Bulk import {n}", "Home",
          5501, 0, f"2026-02-{n:02d}") for n in range(1, 29)])

    seen: list = []
    page = 1
    while True:
        ids, window = run_queue(queue_db, page=page, page_size=10)
        seen.extend(ids)
        if not window["has_next"]:
            break
        page += 1

    assert len(seen) == 31
    assert len(set(seen)) == 31, "a listing appeared on two pages"
    assert window["pages"] == 4


def test_a_page_past_the_end_lands_on_the_last_real_page(queue_db):
    """A reviewer who clears the last page must not be shown an empty table,
    which reads as "no work left" when there are thirty listings behind it."""
    ids, window = run_queue(queue_db, page=99, page_size=2)
    assert window["page"] == 2
    assert ids == [3]


def test_an_empty_queue_still_has_one_page(queue_db):
    ids, window = run_queue(queue_db, filter="pending", q="nothing matches this")
    assert ids == []
    assert window == {"page": 1, "pages": 1, "page_size": rv.PAGE_SIZE, "total": 0,
                      "offset": 0, "has_prev": False, "has_next": False}


def test_a_page_cannot_hold_more_rows_than_a_batch_can_decide(queue_db):
    """Select-all on a page posts one batch. A page larger than ``MAX_BATCH``
    would render a Select-all the endpoint refuses."""
    assert rv.normalize_query({"page_size": 10_000})["page_size"] <= rv.MAX_BATCH
    assert rv.PAGE_SIZE <= rv.MAX_BATCH


def test_junk_paging_values_do_not_break_the_queue(queue_db):
    for params in ({"page": "abc"}, {"page": -4}, {"page_size": "0"},
                   {"page_size": None}, {"page": None}):
        ids, window = run_queue(queue_db, **params)
        assert window["page"] >= 1 and window["page_size"] >= 1
        assert ids


# --- §17: the ledger ----------------------------------------------------------


@pytest.fixture()
def ledger():
    rv.ensure_schema()
    conn = db.connect()
    conn.execute("DELETE FROM marketplace_review_batches")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def test_a_claim_is_taken_once(ledger):
    normalized = rv.normalize_request(rv.APPROVE, [1, 2], idempotency_key="tap-1")
    claimed = rv.claim(ledger, REVIEWER, normalized)
    assert claimed["state"] == "claimed"
    assert claimed["batch_id"].startswith("mrb_")


def test_a_second_tap_before_the_first_finished_is_not_a_second_batch(ledger):
    """The double-click. Recording the claim *after* the writes would leave a
    window in which both taps approve every listing, emit every notification
    and file every audit row twice."""
    normalized = rv.normalize_request(rv.APPROVE, [1, 2], idempotency_key="tap-1")
    rv.claim(ledger, REVIEWER, normalized)
    with pytest.raises(rv.BatchError) as caught:
        rv.claim(ledger, REVIEWER, normalized)
    assert caught.value.code == "BATCH_IN_PROGRESS"


def test_a_retry_after_the_answer_replays_it_rather_than_acting_again(ledger):
    normalized = rv.normalize_request(rv.APPROVE, [1, 2], idempotency_key="tap-1")
    batch_id = rv.claim(ledger, REVIEWER, normalized)["batch_id"]
    answer = rv.summarize(batch_id, rv.APPROVE, [rv.result_entry(1, rv.SUCCEEDED),
                                                 rv.result_entry(2, rv.BLOCKED)])
    rv.finalize(ledger, batch_id, answer)
    ledger.commit()

    replay = rv.claim(ledger, REVIEWER, normalized)
    assert replay["state"] == "replayed"
    assert replay["response"] == answer


def test_a_spent_key_reused_for_different_listings_is_refused(ledger):
    """Not replayed. Answering the second request with the first one's summary
    would report twelve products approved that nobody looked at."""
    first = rv.normalize_request(rv.APPROVE, [1, 2], idempotency_key="tap-1")
    rv.claim(ledger, REVIEWER, first)
    second = rv.normalize_request(rv.APPROVE, [3, 4], idempotency_key="tap-1")
    with pytest.raises(rv.BatchError) as caught:
        rv.claim(ledger, REVIEWER, second)
    assert caught.value.code == "IDEMPOTENCY_KEY_CONFLICT"


def test_a_spent_key_reused_with_a_different_reason_is_refused(ledger):
    first = rv.normalize_request(rv.REJECT, [1], idempotency_key="tap-1",
                                 reason_code=rv.INVALID_PRICE)
    rv.claim(ledger, REVIEWER, first)
    second = rv.normalize_request(rv.REJECT, [1], idempotency_key="tap-1",
                                  reason_code=rv.INVALID_MEDIA)
    with pytest.raises(rv.BatchError) as caught:
        rv.claim(ledger, REVIEWER, second)
    assert caught.value.code == "IDEMPOTENCY_KEY_CONFLICT"


def test_two_reviewers_may_use_the_same_key(ledger):
    """The key is the client's, and two clients are two namespaces. Colliding
    them would make one reviewer's batch answer the other's request."""
    normalized = rv.normalize_request(rv.APPROVE, [1], idempotency_key="tap-1")
    assert rv.claim(ledger, REVIEWER, normalized)["state"] == "claimed"
    assert rv.claim(ledger, REVIEWER + 1, normalized)["state"] == "claimed"


def test_the_review_ledger_is_not_the_seller_batch_ledger(ledger):
    """Both ids come from the same user-id sequence, so an admin who also sells
    would share one key namespace across two unrelated features."""
    from services.business_os.marketplace import listing_batch
    listing_batch.ensure_schema()
    normalized = rv.normalize_request(rv.APPROVE, [1], idempotency_key="tap-1")
    rv.claim(ledger, REVIEWER, normalized)

    seller_side = listing_batch.claim(
        ledger, REVIEWER,
        listing_batch.normalize_request("publish", [1], idempotency_key="tap-1"))
    assert seller_side["state"] == "claimed"


def test_ensure_schema_runs_twice_without_complaint():
    rv.ensure_schema()
    rv.ensure_schema()
