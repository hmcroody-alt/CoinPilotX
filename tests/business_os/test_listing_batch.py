"""Business OS — bulk listing actions, §38–§41.

What these tests are for, in order of how much they matter:

  * a bulk publish **cannot act on a listing nobody checked**. "No verdict
    arrived" is a blocker, not a shrug. This is the rule the whole mission turns
    on and it is the cheapest one to delete by accident;
  * publishability is **read from the verdict, never re-derived here**. Proven by
    handing the module a listing that is obviously broken together with a
    verdict that says it is fine, and requiring the verdict to win;
  * **blocked and failed never merge.** Blocked is a listing the seller can go
    fix; failed is an action that could not be attempted. A seller cannot act on
    the union;
  * a **spent idempotency key never acts twice**, and never answers a different
    request — proven against a real database, including the in-flight window
    that the obvious apply-then-record ordering leaves open;
  * the three counts are **derived from the results**, so a summary cannot
    disagree with its own detail.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="listing_batch_"), "test.db")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + _TMP_DB)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from services import db  # noqa: E402
from services.business_os.marketplace import listing_batch as b  # noqa: E402
from services.business_os.marketplace import listing_readiness as r  # noqa: E402


def listing(**overrides):
    """A draft that is ready to publish, so each test breaks exactly one thing.

    A *draft*, not a live listing. Publishing is a transition out of one of the
    `PUBLISHABLE_FROM` states, so a fixture in the state publishing leads to is
    not the input to the action under test -- it is the output.
    """
    base = {
        "id": 1,
        "title": "Brass desk lamp",
        "description": "A weighted brass lamp with a linen shade.",
        "category": "Home",
        "cover_image_url": "https://cdn.example/lamp.jpg",
        "price_label": "$24.00",
        "approval_status": "approved",
        "status": "draft",
        "listing_type": "physical",
        "product_type": "physical",
        "quantity": 40,
    }
    base.update(overrides)
    return base


READY = {"publishable": True, "checkout_ready": True, "blockers": [], "warnings": []}


# --- request validation ------------------------------------------------------


def test_a_valid_request_normalizes():
    assert b.normalize_request("publish", [3, 1, 2], "key-1") == {
        "action": "publish",
        "listing_ids": [1, 2, 3],
        "idempotency_key": "key-1",
    }


@pytest.mark.parametrize("action", ["", "delete", "PUBLISH", None, 7, "bulk_price"])
def test_an_action_this_build_cannot_perform_is_refused(action):
    # Not ignored, not treated as a no-op. An unrecognised action that fell
    # through would answer with a successful_count for work nobody did.
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request(action, [1], "key-1")
    assert exc.value.code == "UNSUPPORTED_ACTION"


@pytest.mark.parametrize(
    "ids",
    [
        [],
        "1,2,3",
        None,
        [0],
        [-4],
        ["abc"],
        [None],
        [1, None],
        {"1": 1},
    ],
)
def test_an_unusable_id_list_is_refused(ids):
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request("publish", ids, "key-1")
    assert exc.value.code == "INVALID_LISTING_IDS"


def test_a_boolean_is_not_listing_one():
    # `True == 1` in Python. A client confused enough to send a boolean here
    # must not have it silently resolved into a real listing.
    with pytest.raises(b.BatchError):
        b.normalize_request("publish", [True], "key-1")


def test_string_ids_from_json_are_accepted():
    # JSON from a mobile client routinely carries numbers as strings. That is
    # not a confused request, it is a serialization detail.
    assert b.normalize_request("hide", ["4", "2"], "k")["listing_ids"] == [2, 4]


def test_duplicates_collapse_rather_than_failing_the_batch():
    # The seller's intent is unambiguous; failing eighteen rows over a client
    # bug in one of them punishes the wrong person.
    assert b.normalize_request("publish", [5, 5, 5, 2], "k")["listing_ids"] == [2, 5]


def test_the_request_is_capped():
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request("publish", list(range(1, b.MAX_BATCH + 2)), "k")
    assert exc.value.code == "BATCH_TOO_LARGE"
    # The cap itself is allowed, so the boundary is not off by one.
    assert len(b.normalize_request("publish", list(range(1, b.MAX_BATCH + 1)), "k")["listing_ids"]) == b.MAX_BATCH


@pytest.mark.parametrize("key", ["", "   ", None, 5, "x" * 129])
def test_a_request_without_a_usable_key_is_refused(key):
    # Without a key there is no way to tell a retry from a second request, which
    # is the whole protection against publishing everything twice.
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request("publish", [1], key)
    assert exc.value.code == "MISSING_IDEMPOTENCY_KEY"


# --- the request fingerprint -------------------------------------------------


def test_the_same_selection_in_a_different_order_is_the_same_request():
    assert b.request_hash("publish", [3, 1, 2]) == b.request_hash("publish", [1, 2, 3])


def test_a_different_action_on_the_same_rows_is_a_different_request():
    assert b.request_hash("publish", [1, 2]) != b.request_hash("hide", [1, 2])


def test_a_different_row_set_is_a_different_request():
    assert b.request_hash("publish", [1, 2]) != b.request_hash("publish", [1, 2, 3])


# --- eligibility -------------------------------------------------------------


def test_a_ready_listing_publishes():
    assert b.block_reason(listing(), "publish", READY) is None


@pytest.mark.parametrize("status", list(b.PUBLISHABLE_FROM))
def test_every_state_publishing_leads_out_of_is_publishable(status):
    assert b.block_reason(listing(status=status), "publish", READY) is None


@pytest.mark.parametrize("status,code", [
    ("active", "ALREADY_PUBLISHED"),
    ("pending_review", "ALREADY_SUBMITTED"),
    ("seller_deleted", "DELETED"),
])
def test_a_listing_already_past_publication_is_blocked_not_republished(status, code):
    """"Select all" then Publish must not cost a seller their storefront.

    A live listing re-submitted goes back to `pending_review` and stays there
    until a moderator clears the queue. The write succeeds, so the batch would
    have counted it under `successful_count` -- a number that says the seller
    got what they asked for while their shop is dark.
    """
    block = b.block_reason(listing(status=status), "publish", READY)
    assert block is not None, f"{status!r} was re-published"
    assert block["code"] == code


def test_the_state_gate_runs_before_readiness_is_consulted():
    """An already-live listing reads "Already published", not "2 things left".

    Order matters for what the seller is told. A live listing that has since
    lost its price is not a draft with work outstanding; asking them to fix it
    would send them to repair a listing that is already selling.
    """
    block = b.block_reason(listing(status="active"), "publish", None)
    assert block["code"] == "ALREADY_PUBLISHED"


def test_a_listing_nobody_checked_does_not_publish():
    # The rule the mission turns on. `None` here means no verdict was obtained,
    # which is not a verdict of yes — and a bulk publish is the most expensive
    # place to assume otherwise, because the unexamined row goes live beside
    # seventeen examined ones.
    block = b.block_reason(listing(), "publish", None)
    assert block["code"] == "NO_READINESS"
    assert block["reason"] == "No readiness check yet"


def test_the_verdict_decides_publishability_and_this_module_does_not():
    # A listing with no price, no title, no category and no image — together
    # with a verdict saying it is fine. If anything here formed a second
    # opinion, this row would be blocked. The verdict has to win, because a
    # second opinion would diverge from the one the seller was shown on the row.
    wreck = listing(title="", category="", price_label="", cover_image_url="", media_url="")
    assert r.evaluate(wreck)["publishable"] is False
    assert b.block_reason(wreck, "publish", READY) is None


@pytest.mark.parametrize(
    "blockers,expected",
    [
        (["MISSING_PRICE"], "1 thing left"),
        (["MISSING_PRICE", "NO_VALID_MEDIA"], "2 things left"),
        (["A", "B", "C"], "3 things left"),
    ],
)
def test_a_blocked_listing_states_how_much_work_is_left(blockers, expected):
    verdict = {"publishable": False, "checkout_ready": False, "blockers": blockers, "warnings": []}
    block = b.block_reason(listing(), "publish", verdict)
    assert block["reason"] == expected
    assert block["blockers"] == blockers


def test_not_publishable_with_no_blockers_still_blocks():
    # Unreachable through `evaluate`, which derives one from the other. If the
    # two ever disagree, the safe reading of "not publishable" is not
    # publishable.
    verdict = {"publishable": False, "checkout_ready": False, "blockers": [], "warnings": []}
    assert b.block_reason(listing(), "publish", verdict)["code"] == "NOT_READY"


def test_a_deleted_listing_is_blocked_for_both_actions():
    row = listing(status="seller_deleted")
    assert b.block_reason(row, "publish", READY)["code"] == "DELETED"
    assert b.block_reason(row, "hide", None)["code"] == "DELETED"


def test_hiding_a_live_listing_is_always_allowed():
    assert b.block_reason(listing(), "hide", None) is None


@pytest.mark.parametrize("status", ["paused", "hidden", "PAUSED", " Hidden "])
def test_already_hidden_is_reported_not_counted_as_done(status):
    # Reporting this as a success would inflate "Hid 18" with rows that were
    # already hidden, which is the same class of lie as counting blocked rows as
    # published.
    block = b.block_reason(listing(status=status), "hide", None)
    assert block["code"] == "ALREADY_HIDDEN"


def test_hiding_never_consults_the_readiness_engine(monkeypatch):
    # A seller must be able to hide a broken listing. Making that depend on the
    # health of the readiness engine would mean the listings most urgent to pull
    # are exactly the ones that cannot be pulled.
    calls = []

    def spy(row, **kwargs):
        calls.append(row.get("id"))
        return READY

    monkeypatch.setattr(b._readiness, "evaluate", spy)

    b.evaluate_rows([listing(id=1), listing(id=2)], "hide")
    assert calls == []

    # And consults it exactly once per row for publish — not zero times, which
    # would mean the verdict is coming from somewhere else.
    b.evaluate_rows([listing(id=1), listing(id=2)], "publish")
    assert calls == [1, 2]


def test_media_rows_reach_the_verdict():
    # A listing whose only image is a media row, not a cover column, is ready in
    # the seller's list — that route passes `media=` into `evaluate`. If the
    # batch omitted it, the same listing would read "Ready to publish" on the
    # row and come back NO_VALID_MEDIA from the action taken on it.
    row = listing(id=7, cover_image_url="", media_url="")
    assert b.evaluate_rows([row], "publish")[0][1]["blockers"] == ["NO_VALID_MEDIA"]

    media = {7: [{"media_url": "https://cdn.example/lamp.jpg", "media_type": "image"}]}
    assert b.evaluate_rows([row], "publish", media)[0][1] is None


def test_evaluate_rows_decides_every_row():
    rows = [listing(id=1), listing(id=2, price_label=""), listing(id=3, status="seller_deleted")]
    decided = b.evaluate_rows(rows, "publish")
    assert [row["id"] for row, _ in decided] == [1, 2, 3]
    assert decided[0][1] is None
    assert decided[1][1]["blockers"] == ["MISSING_PRICE"]
    assert decided[2][1]["code"] == "DELETED"


# --- the answer --------------------------------------------------------------


def test_the_counts_are_derived_from_the_results():
    results = [
        b.result_entry(1, b.SUCCEEDED),
        b.result_entry(2, b.SUCCEEDED),
        b.result_entry(3, b.BLOCKED, reason="1 thing left"),
        b.result_entry(4, b.FAILED, reason_code=b.NOT_FOUND),
    ]
    summary = b.summarize("mlb_x", "publish", results)
    assert summary["requested_count"] == 4
    assert summary["successful_count"] == 2
    assert summary["blocked_count"] == 1
    assert summary["failed_count"] == 1
    # The invariant a seller checks by eye. If it can fail, the summary is
    # describing something other than the list beneath it.
    assert (
        summary["successful_count"] + summary["blocked_count"] + summary["failed_count"]
        == summary["requested_count"]
        == len(summary["results"])
    )


def test_an_outcome_outside_the_three_is_a_programming_error():
    with pytest.raises(ValueError):
        b.summarize("mlb_x", "publish", [{"listing_id": 1, "outcome": "maybe"}])


def test_a_result_entry_omits_absent_detail_rather_than_nulling_it():
    assert b.result_entry(1, b.SUCCEEDED, reason=None) == {"listing_id": 1, "outcome": "succeeded"}


# --- idempotency, against a real database ------------------------------------


@pytest.fixture(autouse=True, scope="module")
def _schema():
    b.ensure_schema()


@pytest.fixture
def conn():
    connection = db.connect()
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


_counter = [0]


def fresh_key():
    _counter[0] += 1
    return f"key-{_counter[0]}"


def test_a_first_request_claims_the_key(conn):
    claimed = b.claim(conn, 41, b.normalize_request("publish", [1, 2], fresh_key()))
    assert claimed["state"] == "claimed"
    assert claimed["batch_id"].startswith("mlb_")


def test_an_identical_retry_replays_the_stored_answer_instead_of_acting_again(conn):
    key = fresh_key()
    request = b.normalize_request("publish", [1, 2], key)
    first = b.claim(conn, 41, request)
    answer = b.summarize(first["batch_id"], "publish", [b.result_entry(1, b.SUCCEEDED), b.result_entry(2, b.BLOCKED)])
    b.finalize(conn, first["batch_id"], answer)

    replay = b.claim(conn, 41, b.normalize_request("publish", [2, 1], key))
    assert replay["state"] == "replayed"
    # Byte-identical, not merely equivalent: a retry that re-derived the summary
    # would quietly report the store's state *now* rather than what the batch
    # did, and those differ the moment the seller fixes one of the blocked rows.
    assert replay["response"] == answer


def test_a_retry_arriving_before_the_first_finishes_is_told_so(conn):
    # The window the obvious apply-then-record ordering leaves open. Answering
    # "done" here claims work that has not happened; answering by re-applying
    # defeats the claim. Neither is available.
    key = fresh_key()
    request = b.normalize_request("publish", [1], key)
    b.claim(conn, 41, request)
    with pytest.raises(b.BatchError) as exc:
        b.claim(conn, 41, b.normalize_request("publish", [1], key))
    assert exc.value.code == "BATCH_IN_PROGRESS"
    assert exc.value.status == 409


def test_reusing_a_key_for_different_listings_is_a_conflict(conn):
    key = fresh_key()
    first = b.claim(conn, 41, b.normalize_request("publish", [1, 2], key))
    b.finalize(conn, first["batch_id"], b.summarize(first["batch_id"], "publish", []))

    with pytest.raises(b.BatchError) as exc:
        b.claim(conn, 41, b.normalize_request("publish", [8, 9], key))
    assert exc.value.code == "IDEMPOTENCY_KEY_CONFLICT"
    assert exc.value.status == 409


def test_reusing_a_key_for_a_different_action_is_a_conflict(conn):
    key = fresh_key()
    first = b.claim(conn, 41, b.normalize_request("publish", [1], key))
    b.finalize(conn, first["batch_id"], b.summarize(first["batch_id"], "publish", []))

    with pytest.raises(b.BatchError):
        b.claim(conn, 41, b.normalize_request("hide", [1], key))


def test_one_sellers_key_never_returns_another_sellers_batch(conn):
    # Two clients generating the same UUID is improbable. One seller receiving
    # another seller's batch summary because of it is not a risk worth taking
    # for a scope that costs nothing.
    key = fresh_key()
    mine = b.claim(conn, 41, b.normalize_request("publish", [1], key))
    b.finalize(conn, mine["batch_id"], b.summarize(mine["batch_id"], "publish", []))

    theirs = b.claim(conn, 77, b.normalize_request("publish", [1], key))
    assert theirs["state"] == "claimed"
    assert theirs["batch_id"] != mine["batch_id"]


class RecordingConn:
    """A connection that remembers what was bound, and delegates everything else.

    Needed because the obvious test — insert, read the value back, assert it is
    a string — **cannot see this bug**. The column is declared TEXT, and SQLite's
    type affinity silently converts an integer 41 to '41' on the way in, so the
    round trip looks correct no matter what was bound. PostgreSQL does no such
    conversion: it raises. The assertion therefore has to be on the binding, not
    on the behaviour, or it passes locally and crashes in production.
    """

    def __init__(self, inner):
        self._inner = inner
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return self._inner.execute(sql, params)

    def params_for(self, fragment):
        return [params for sql, params in self.calls if fragment in sql]


class BlindConn(RecordingConn):
    """A connection whose lookup of the batch ledger always comes back empty.

    This is not a contrived state. Two requests carrying the same key arrive
    together, both run the SELECT before either runs the INSERT, and both see
    nothing — which is precisely what this simulates, deterministically, in one
    process. The alternative is threads and a sleep, which would make the test
    both slower and occasionally wrong.
    """

    class _Empty:
        rowcount = 0

        def fetchone(self):
            return None

    def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        if sql.strip().upper().startswith("SELECT") and "marketplace_listing_batches" in sql:
            return self._Empty()
        return self._inner.execute(sql, params)


def test_losing_the_insert_race_refuses_rather_than_acting_twice(conn):
    # Without this branch the loser of the race falls through and applies the
    # batch a second time under a key that is already spent — which for publish
    # means a second review submission for every row in the selection. The
    # UNIQUE constraint catches the duplicate; what matters is that the code
    # notices it did, rather than reading `ON CONFLICT DO NOTHING` as success.
    key = fresh_key()
    request = b.normalize_request("publish", [1, 2], key)
    first = b.claim(conn, 41, request)
    b.finalize(conn, first["batch_id"], b.summarize(first["batch_id"], "publish", []))

    with pytest.raises(b.BatchError) as exc:
        b.claim(BlindConn(conn), 41, request)
    assert exc.value.code == "BATCH_IN_PROGRESS"
    assert exc.value.status == 409

    # And exactly one batch exists for that key — the race produced no second row.
    rows = conn.execute(
        "SELECT batch_id FROM marketplace_listing_batches WHERE seller_user_id=? AND idempotency_key=?",
        ("41", key),
    ).fetchall()
    assert len(rows) == 1


def test_the_seller_id_is_bound_as_text_everywhere_it_is_bound(conn):
    spy = RecordingConn(conn)
    key = fresh_key()
    b.claim(spy, 41, b.normalize_request("publish", [1], key))

    bound = spy.params_for("marketplace_listing_batches")
    assert bound, "claim bound nothing — the test is watching the wrong table"
    # Every column on this table is TEXT, so every bound value must be a str.
    # Stated as a property over all of them rather than as a check on the one
    # parameter that happens to be wrong today.
    offenders = [v for params in bound for v in params if not isinstance(v, str)]
    assert offenders == [], f"non-text values bound to a TEXT table: {offenders!r}"


def test_the_select_and_the_insert_agree_about_the_seller_type(conn):
    # The pair matters more than either half: binding "41" on write and 41 on
    # read would find nothing on PostgreSQL, so every retry would act again.
    spy = RecordingConn(conn)
    key = fresh_key()
    b.claim(spy, 41, b.normalize_request("publish", [1], key))

    selects = [p for sql, p in spy.calls if sql.strip().upper().startswith("SELECT")]
    inserts = [p for sql, p in spy.calls if sql.strip().upper().startswith("INSERT")]
    assert selects and inserts
    assert selects[0][0] == "41" and isinstance(selects[0][0], str)
    assert inserts[0][1] == "41" and isinstance(inserts[0][1], str)


# --- bulk reprice ------------------------------------------------------------
#
# The action that carries a payload. Everything above this line describes a
# request whose entire meaning is its name; a price request means nothing
# without the rule, and the two failures that buys are (a) the rule escaping the
# idempotency fingerprint, so a second, different price is served the first
# one's summary, and (b) an unknown cost being treated as a cost of zero.


def test_a_price_request_carries_its_rule():
    normalized = b.normalize_request(
        "price", [2, 1], "key-p", {"type": "COST_PLUS_PERCENT", "value": 20}
    )
    assert normalized["payload"] == {"type": "COST_PLUS_PERCENT", "value": 20.0}
    assert normalized["listing_ids"] == [1, 2]


@pytest.mark.parametrize(
    "rule",
    [
        None,                                   # manual: names no number
        {"type": "MANUAL_PRICE"},               # ditto, spelled out
        {"type": "NONSENSE", "value": 5},
        {"type": "MULTIPLIER", "value": 0},     # engine's own range rules
        {"type": "MULTIPLIER", "value": -2},
        {"type": "TARGET_MARGIN", "value": 100},
        {"type": "COST_PLUS_PERCENT", "value": "20"},
        {"type": "COST_PLUS_PERCENT", "value": True},
        "COST_PLUS_PERCENT",
    ],
)
def test_a_price_request_without_a_usable_rule_is_refused(rule):
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request("price", [1], "key-p", rule)
    assert exc.value.code == "INVALID_PRICING_RULE"


@pytest.mark.parametrize("action", ["publish", "hide"])
def test_a_payload_on_an_action_that_ignores_it_is_refused(action):
    # Otherwise the seller is told the batch succeeded, including at whatever
    # they believed the payload was asking for.
    with pytest.raises(b.BatchError) as exc:
        b.normalize_request(action, [1], "key-1", {"type": "MULTIPLIER", "value": 2})
    assert exc.value.code == "UNSUPPORTED_ACTION"


def test_the_rule_is_part_of_the_request_fingerprint():
    # THE test for this feature. Same key, same listings, different price is a
    # different request. If these hashes match, `claim` calls the second one a
    # replay and hands back the first one's "40 repriced" summary while the
    # store keeps its old prices.
    ten = b.request_hash("price", [1, 2], {"type": "COST_PLUS_PERCENT", "value": 10})
    twentyfive = b.request_hash("price", [1, 2], {"type": "COST_PLUS_PERCENT", "value": 25})
    assert ten != twentyfive


def test_adding_a_payload_did_not_move_every_existing_fingerprint():
    # Hashing an absent payload as `null` would change the digest of every
    # publish and hide batch ever recorded, so a client retrying through a
    # timeout would be refused with IDEMPOTENCY_KEY_CONFLICT instead of served
    # the answer -- on rows the first attempt already wrote.
    import hashlib as _h, json as _j

    legacy = _h.sha256(
        _j.dumps(
            {"action": "publish", "listing_ids": [1, 2]},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert b.request_hash("publish", [1, 2]) == legacy


def test_unknown_cost_is_not_a_cost_of_zero():
    # The bug this whole path is shaped around: a missing supplier row must
    # block the listing, never price it at $0.00 and report it repriced.
    plan = b.price_proposal({"type": "COST_PLUS_PERCENT", "value": 20}, None, 2400)
    assert plan.get("price_cents") is None
    assert plan["block"]["code"] == "UNKNOWN_COST"


def test_a_known_cost_produces_the_engine_s_price():
    # Delegated, not reimplemented: the assertion is that this equals the
    # pricing engine's own answer rather than a number retyped here.
    from services.business_os.suppliers import pricing as p

    plan = b.price_proposal({"type": "COST_PLUS_PERCENT", "value": 20}, 1000, 900)
    assert plan["price_cents"] == p.apply_rule({"type": "COST_PLUS_PERCENT", "value": 20}, 1000)
    assert plan["price_cents"] == 1200
    assert "block" not in plan


def test_a_rule_that_lands_on_zero_is_not_a_price():
    plan = b.price_proposal({"type": "COST_PLUS_PERCENT", "value": 20}, 0, 500)
    assert plan["block"]["code"] == "PRICE_OUT_OF_RANGE"


def test_a_listing_already_at_the_target_price_is_blocked_not_repriced():
    # `price_label` is a MATERIAL_FIELD, so "changing" a live listing to the
    # price it already has sends it back to pending_review and off sale. A
    # store-wide reprice would shelve every listing already at target and call
    # it a success.
    plan = b.price_proposal({"type": "COST_PLUS_PERCENT", "value": 20}, 1000, 1200)
    assert plan["block"]["code"] == "PRICE_UNCHANGED"


def test_a_row_nobody_priced_is_blocked():
    # Absence is not permission -- the same rule the publish path applies to a
    # missing verdict, one action across.
    assert b.block_reason(listing(), "price", None, None) == {
        "code": "NO_PRICE_PROPOSAL",
        "reason": "No price worked out",
    }


def test_a_deleted_listing_is_not_repriced():
    assert b.block_reason(listing(status="seller_deleted"), "price", None,
                          {"price_cents": 1200})["code"] == "DELETED"


@pytest.mark.parametrize("status", ["draft", "active", "paused", "rejected", "changes_requested"])
def test_reprice_reaches_listings_publish_cannot(status):
    # Readiness and publishability are deliberately not consulted for a price
    # change. An unfinished or paused listing is exactly the kind a seller
    # reprices, and the publish gates would make those the only rows a bulk
    # price change cannot touch.
    assert b.block_reason(listing(status=status), "price", None, {"price_cents": 1200}) is None


def test_the_blocking_price_and_the_written_price_are_one_object():
    rows = [listing(id=1), listing(id=2)]
    plans = b.build_price_plans(
        rows,
        {"type": "COST_PLUS_PERCENT", "value": 20},
        {1: 1000, 2: None},
        {1: 900, 2: 900},
    )
    decided = dict((int(row["id"]), block) for row, block in
                   b.evaluate_rows(rows, "price", None, plans))
    assert decided[1] is None
    assert decided[2]["code"] == "UNKNOWN_COST"
    # The number the writer will use is the same object the decision was made
    # from, not a second evaluation of the rule.
    assert plans[1]["price_cents"] == 1200
