"""A creator says which product their post is about, and the system believes them.

Every other retrieval source in this package is an *inference*. This one is a
*statement*, and the whole reason the module is mostly refusals is that a
statement with the wrong name on it is worse than a missing guess. So the tests
below are weighted the same way: a handful assert that a tag works, and most
assert that it does not work when it shouldn't.

Five of them exist because a code comment promised they did. `relationship.py`
says the duplicated ``SOURCE_TAGGED`` constant "is pinned by a test that asserts
the two constants are equal"; `tagging.py` says "there is a test for it here"
about the suitability gate. A promise in a comment that no test keeps is worse
than no comment, because the next reader stops looking.

The one structural trap in this file is worth naming up front, because it has
already caught me once in this package (see §23): **`engine.serve` returns the
same value for a crash and for a decision.** Every negative assertion here is
therefore written against something other than the length of the result — a row
count in the tags table, the provenance stamped on a placement, a refusal reason,
or a mock that must not be called. The conftest `swallowed_serve_failures` guard
catches the subset that goes through `serve`'s own fail-safe; it does *not* catch
a failure swallowed lower down, which is why `tagged_listing_ids`' own
`except` gets an explicit test rather than a trusting one.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from unittest import mock

import pytest

from services.commerce_discovery import (
    engine,
    pool,
    relationship,
    schema,
    suitability,
    tagging,
)

REPO = Path(__file__).resolve().parents[2]

VIEWER = 9001
POST = 4242

#: Derived, never listed. A hand-written list would go stale the moment a seventh
#: surface is added, and it would go stale in the safe-looking direction: the new
#: surface would simply not be tested for the property, rather than failing.
NON_CONTENT_SURFACES = frozenset(schema.SURFACES) - suitability.CONTENT_SURFACES


def _rows(market) -> list[tuple]:
    cur = market.conn.cursor()
    cur.execute(
        "SELECT content_type, content_id, listing_id, attached_by_user_id, "
        "seller_user_id, authority FROM pulse_content_products ORDER BY id"
    )
    return [tuple(dict(row).values()) for row in cur.fetchall()]


def _provenance(market, placements) -> list[str]:
    """The §6 relationship recorded against each placement, read back from the row.

    *Not* off the payload, and the distinction is the point: `_serve` keeps the
    relationship off the row dict on purpose, and `PIPELINE_ONLY_FIELDS` strips it
    as belt-and-braces, because the product serializer is a denylist and anything
    left on a row ships to the buyer's device. A test that read
    ``placement["relationship"]`` would be asserting that the leak exists.
    """
    if not placements:
        return []
    cur = market.conn.cursor()
    out = []
    for placement in placements:
        cur.execute(
            "SELECT relationship FROM commerce_discovery_placements WHERE placement_id=?",
            (placement["placement_id"],),
        )
        row = cur.fetchone()
        out.append(str(dict(row)["relationship"] or "") if row else "")
    return out


def _provenance_of(market, placements, listing_id: int) -> str:
    cur = market.conn.cursor()
    cur.execute(
        "SELECT relationship FROM commerce_discovery_placements "
        "WHERE listing_id=? ORDER BY rowid DESC LIMIT 1",
        (int(listing_id),),
    )
    row = cur.fetchone()
    return str(dict(row)["relationship"] or "") if row else ""


def _listing_ids(placements) -> list[int]:
    out = []
    for placement in placements:
        product = placement.get("product") or {}
        out.append(int(product.get("listing_id") or product.get("id") or 0))
    return out


# --- driving the real route -------------------------------------------------
# The `market` fixture calls `engine.serve` directly and therefore sits *below*
# the route, where `content_post_id` is already decided. Two properties in this
# file are about that decision itself — which surfaces are allowed to supply the
# id, and whether the suitability refusal gets there first — so they need the
# handler on the path. The stubs below are the same four
# `test_suitability_gate_is_wired.py` uses, and for its stated reason: a test
# that needed a seeded catalogue to pass would also pass with the rule deleted.
#
# Written out here rather than imported from that module because a fixture shared
# between two test files couples their lifetimes, and this harness asserts
# something narrower: it records `content_post_id` per call, which is the whole
# observable.

class _ServeSpy:
    """Records every `engine.serve` call's surface and `content_post_id`."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, cur, user_id, surface, **kwargs):
        self.calls.append({
            "surface": surface,
            "content_post_id": kwargs.get("content_post_id"),
        })
        return []

    @property
    def called(self) -> bool:
        return bool(self.calls)

    @property
    def post_ids(self) -> list:
        return [call["content_post_id"] for call in self.calls]


class _PostRows:
    """A `cur` answering the two reads `_content_refusal` makes, keyed by post id.

    A cursor rather than a monkeypatch of `_content_post`, so the query, the
    column list and the tuple unpacking stay on the path. Rows are returned as
    *tuples* deliberately: that is the SQLite shape, and a dict here would hide
    the row-access asymmetry this repo keeps being bitten by.
    """

    def __init__(self, posts: dict[int, dict] | None = None):
        self.posts = posts or {}
        self._pending = None

    def execute(self, sql, params=()):
        from services import commerce_discovery_routes as routes

        if "pulse_reels" in sql:
            # No reel rows: an absent reel is not a refusal, so this leaves the
            # post's own verdict standing rather than adding a second one.
            self._pending = None
            return
        row = self.posts.get(int(params[0]))
        self._pending = None if row is None else tuple(
            row.get(name) for name in routes._POST_COLUMNS
        )

    def fetchone(self):
        return self._pending


class _StubBot:
    def parse_price_label_to_cents(self, *_a, **_k):  # pragma: no cover
        return 0

    def pulse_marketplace_listing_payload(self, *_a, **_k):  # pragma: no cover
        return {}


#: A post the gate refuses, and the reason is bereavement rather than any of the
#: structural checks. `risk_score` is 0, not 100: `pulse_posts.risk_score` runs
#: 0–100 with *high* meaning risky, so 100 would be refused for `CONTENT_RISK`
#: and every test below would certify the gate on a reason that has nothing to do
#: with grief. `moderation_status` is "approved" for the same reason.
BEREAVEMENT_POST = {
    "post_type": "post",
    "moderation_status": "approved",
    "risk_score": 0,
    "title": "Funeral arrangements for my father",
    "body": "He passed away on Sunday. Thank you all for the kind words.",
    "ai_summary": "",
    "tags_json": "[]",
    "ai_tags_json": "[]",
}


@pytest.fixture
def route(monkeypatch):
    """`(client, spy, rows)` — the real blueprint over stubbed edges."""
    from flask import Flask

    from services import commerce_discovery_routes as routes

    spy = _ServeSpy()
    rows = _PostRows({POST: dict(BEREAVEMENT_POST)})
    monkeypatch.setattr(routes.engine, "serve", spy)
    monkeypatch.setattr(routes, "_bot", lambda: _StubBot())
    monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": VIEWER}, None))
    # The limiter is in-process and shared with the whole session, so leaving it
    # live would make these results depend on execution order.
    monkeypatch.setattr(routes, "_rate_limited", lambda _ref: False)
    monkeypatch.setattr(routes, "_with_db", lambda handler: handler(rows, None))

    app = Flask(__name__)
    app.register_blueprint(routes.discovery_blueprint)
    return app.test_client(), spy, rows


def _ask(client, surface: str, **body):
    from services import commerce_discovery_routes as routes

    return client.post(
        f"{routes.API_PREFIX}/{surface}", json={"session_id": "s-1", **body},
    )


class TestTheVocabularyIsNoLongerAPromise:
    """`CREATER_TAGGED` was declared, documented and unproducible for months.

    These four tests are the ones that would have gone red the moment the value
    stopped being reachable again — by a rename, by a source being dropped from
    `CANDIDATE_SOURCES`, or by someone moving it back to the unimplemented list
    because nothing appeared to produce it.
    """

    def test_creator_tagged_is_servable(self):
        assert relationship.CREATOR_TAGGED in relationship.SERVABLE_RELATIONSHIPS

    def test_creator_tagged_is_no_longer_listed_as_unimplemented(self):
        assert relationship.CREATOR_TAGGED not in relationship.UNIMPLEMENTED_RELATIONSHIPS

    def test_the_two_source_constants_are_the_same_string(self):
        """`relationship` deliberately does not import `pool`, so the name is
        written twice. The duplication is only safe while this holds — and the
        drift is asymmetric: renaming `pool`'s would silently stop `classify`
        ever returning `CREATOR_TAGGED`, with no error anywhere."""
        assert relationship.SOURCE_TAGGED == pool.SOURCE_TAGGED

    def test_the_source_is_registered_so_metrics_can_count_it(self):
        """`metrics.observe_sources` reports per-source counts against
        `CANDIDATE_SOURCES`. A source missing from it retrieves rows that no
        operator can see the retrieval of."""
        assert pool.SOURCE_TAGGED in pool.CANDIDATE_SOURCES


class TestOnlyTheOwnerMayTag:
    """Tagging someone else's product is affiliate marketing.

    It needs a commission model, a jurisdiction-dependent disclosure obligation
    and a decision about whether PulseSoc takes a cut. None of those are
    engineering decisions and all of them are far harder to withdraw than to
    delay, so the check is ownership and the refusal is explicit.
    """

    def test_the_owner_may_tag_their_own_listing(self, market):
        owner = market.seller_of(1)
        assert market.tag(1, post_id=POST, user_id=owner)["ok"] is True
        assert _rows(market) == [("post", POST, 1, owner, owner, "owner")]

    def test_a_stranger_may_not(self, market):
        owner = market.seller_of(1)
        stranger = owner + 1
        result = market.tag(1, post_id=POST, user_id=stranger)
        assert result["ok"] is False
        assert result["reason"] == tagging.REFUSED_NOT_OWNER
        # The assertion that matters. A refusal that returned `ok: False` and
        # wrote the row anyway would pass the line above.
        assert _rows(market) == []

    def test_the_viewer_may_not_tag_a_sellers_product(self, market):
        result = market.tag(1, post_id=POST, user_id=VIEWER)
        assert result["reason"] == tagging.REFUSED_NOT_OWNER
        assert _rows(market) == []

    def test_a_listing_that_does_not_exist_is_a_different_refusal(self, market):
        """Kept distinct from `NOT_OWNER` on purpose: one is "you can't", the
        other is "there is no such thing", and an operator reading logs needs to
        be able to tell a permissions problem from a broken client."""
        result = market.tag(999999, post_id=POST, user_id=1001)
        assert result["reason"] == tagging.REFUSED_NO_LISTING

    def test_every_refusal_is_declared(self, market):
        """`REFUSALS` exists so this can be asserted rather than restated. A new
        refusal reason that forgets to join the set is a reason no caller can
        branch on safely."""
        attempts = [
            market.tag(1, post_id=POST, user_id=VIEWER),
            market.tag(999999, post_id=POST, user_id=1001),
            market.tag(1, post_id=0, user_id=1001),
            market.tag(1, post_id=POST, content_type="billboard", user_id=1001),
        ]
        reasons = {attempt["reason"] for attempt in attempts if not attempt["ok"]}
        assert reasons, "every attempt above was supposed to be refused"
        assert reasons <= tagging.REFUSALS

    def test_nothing_writes_an_authority_other_than_owner(self, market):
        """The day a second authority exists — affiliate, or a brand partnership —
        the rows written under this one must still be distinguishable. That only
        works if today's writer is incapable of writing anything else."""
        for listing_id in (1, 2, 3):
            market.tag(listing_id, post_id=POST)
        assert {row[5] for row in _rows(market)} == {tagging.AUTHORITY_OWNER}
        source = (REPO / "services/commerce_discovery/tagging.py").read_text()
        inserts = [
            line for line in source.splitlines()
            if "authority" in line and "INSERT" in line.upper()
        ]
        assert "AUTHORITY_OWNER" in source
        assert not [line for line in inserts if "'" in line], (
            "a literal authority string in the INSERT means a second value can be "
            f"written without touching AUTHORITY_OWNER: {inserts}"
        )


class TestABadReferenceIsRefusedRatherThanFiled:
    """An attachment filed against the wrong thing is invisible, not wrong-looking.

    ``content_id`` spaces overlap across the four content tables, so a tag stored
    under the wrong ``content_type`` sits pointing at a stranger's content forever
    and nobody ever sees a symptom. That is why `normalize_content_type` returns
    ``""`` instead of defaulting.
    """

    @pytest.mark.parametrize("kind", tagging.CONTENT_TYPES)
    def test_the_four_real_kinds_are_accepted(self, market, kind):
        assert market.tag(1, post_id=POST, content_type=kind)["ok"] is True

    @pytest.mark.parametrize("kind", ["", None, "billboard", "POST ", "story", 7])
    def test_anything_else_is_refused(self, market, kind):
        result = market.tag(1, post_id=POST, content_type=kind)
        if kind == "POST ":
            # Normalisation is real, not a whitelist of exact strings: a client
            # sending a padded, upper-cased kind meant `post` and gets it.
            assert result["ok"] is True
            return
        assert result["ok"] is False
        assert result["reason"] == tagging.REFUSED_UNKNOWN_CONTENT
        assert _rows(market) == []

    @pytest.mark.parametrize("bad", [0, None, "", -1, "abc", 2.5, True, [], {}])
    def test_a_missing_content_id_is_refused(self, market, bad):
        result = market.tag(1, post_id=bad)
        assert result["ok"] is False
        assert result["reason"] == tagging.REFUSED_BAD_INPUT
        assert _rows(market) == []

    def test_a_lossy_id_is_refused_rather_than_truncated(self, market):
        """``2.5`` is not post 2. Truncating it files the tag against a stranger's
        content and shows no symptom to anybody — `content_id` spaces overlap
        across the four content tables, so the row is not even obviously
        orphaned."""
        assert market.tag(1, post_id=2.5)["reason"] == tagging.REFUSED_BAD_INPUT
        assert market.tag(1, post_id=POST + 0.0)["ok"] is True, (
            "JSON has no integer type, so a client sending 4242.0 meant 4242"
        )
        assert _rows(market) == [("post", POST, 1, 1001, 1001, "owner")]

    def test_a_lossy_listing_id_is_refused_too(self, market):
        assert market.tag(1.5, post_id=POST, user_id=1001)["reason"] == tagging.REFUSED_BAD_INPUT
        assert _rows(market) == []


class TestTaggingTheSameProductTwiceIsNotAnError:
    def test_a_retried_save_does_not_fail_the_post(self, market):
        """A composer that retries a save must not turn a duplicate into a failed
        post, which is why the INSERT is `OR IGNORE` against the UNIQUE key."""
        first = market.tag(1, post_id=POST)
        second = market.tag(1, post_id=POST)
        assert first["ok"] is True and second["ok"] is True
        assert len(_rows(market)) == 1

    def test_the_same_product_on_two_posts_is_two_rows(self, market):
        market.tag(1, post_id=POST)
        market.tag(1, post_id=POST + 1)
        assert len(_rows(market)) == 2
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST
        ) == (1,)


class TestTheCapHoldsOnBothSides:
    """Enforced at the write *and* at the read, which is not redundancy.

    The write cap stops a composer creating a spam post. The read limit stops
    rows that predate a cap change — or were written by some future second
    writer — from filling a pool that `pool` hands the ``tagged`` source an
    unbounded quota for. A read path that trusts a write-time invariant is
    trusting every past version of the writer.
    """

    def test_the_writer_stops_at_the_cap(self, market):
        cap = tagging.MAX_TAGGED_PER_CONTENT
        for listing_id in range(1, cap + 1):
            assert market.tag(listing_id, post_id=POST)["ok"] is True
        over = market.tag(cap + 1, post_id=POST)
        assert over["ok"] is False
        assert over["reason"] == tagging.REFUSED_CAP
        assert len(_rows(market)) == cap

    def test_the_reader_stops_at_the_cap_even_when_the_writer_did_not(self, market):
        """Rows inserted behind `attach`'s back, as a pre-cap history or a second
        writer would have left them."""
        cap = tagging.MAX_TAGGED_PER_CONTENT
        cur = market.conn.cursor()
        for listing_id in range(1, cap + 6):
            cur.execute(
                "INSERT INTO pulse_content_products "
                "(content_type, content_id, listing_id, attached_by_user_id, "
                " seller_user_id, authority, created_at) VALUES (?,?,?,?,?,?,?)",
                ("post", POST, listing_id, market.seller_of(listing_id),
                 market.seller_of(listing_id), "owner", "2026-03-02T09:00:00.000000Z"),
            )
        market.conn.commit()
        assert len(_rows(market)) == cap + 5
        found = tagging.tagged_listing_ids(cur, content_type="post", content_id=POST)
        assert len(found) == cap


class TestTheOrderIsTheCreators:
    def test_oldest_first(self, market):
        """The first product they attached is the one the post is about. Ranking
        may reorder within the pool, but retrieval should not start by discarding
        the only ordering anybody intended."""
        for listing_id in (7, 3, 9):
            market.tag(listing_id, post_id=POST)
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST
        ) == (7, 3, 9)


class TestATransferredListingLosesItsTag:
    """`seller_user_id` is stored so this can be noticed.

    It is the seller the tag was *authorised against*. A listing that changes
    hands afterwards carries a permission its new owner never granted, and the
    read drops it rather than serving it — in the JOIN, so a transferred listing
    costs nothing to exclude.
    """

    def test_the_tag_survives_while_the_owner_does(self, market):
        market.tag(1, post_id=POST)
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST
        ) == (1,)

    def test_the_tag_is_dropped_once_the_listing_changes_hands(self, market):
        market.tag(1, post_id=POST)
        market.transfer(1, market.seller_of(1) + 1)
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST
        ) == ()

    def test_the_row_is_kept_rather_than_deleted(self, market):
        """Dropped at read time, not on transfer. The row is a record that a
        permission was granted, and a sale is not a reason to rewrite history —
        it is a reason to stop acting on it."""
        market.tag(1, post_id=POST)
        market.transfer(1, market.seller_of(1) + 1)
        assert len(_rows(market)) == 1

    def test_a_transfer_back_restores_it(self, market):
        """Which is the proof that the JOIN compares the two columns rather than
        latching a flag somewhere the first time it notices a mismatch."""
        owner = market.seller_of(1)
        market.tag(1, post_id=POST)
        market.transfer(1, owner + 1)
        market.transfer(1, owner)
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST
        ) == (1,)

    def test_the_new_owner_can_tag_it_themselves(self, market):
        """The refusal is about *whose permission it was*, not about the product
        being untaggable. The new owner grants their own."""
        new_owner = market.seller_of(1) + 1
        market.tag(1, post_id=POST)
        market.transfer(1, new_owner)
        assert market.tag(1, post_id=POST + 1, user_id=new_owner)["ok"] is True
        assert tagging.tagged_listing_ids(
            market.conn.cursor(), content_type="post", content_id=POST + 1
        ) == (1,)


class TestALookupFailureIsQuietButNotSilent:
    def test_an_unreadable_table_returns_nothing(self, market):
        """§82: commerce must never break the post."""
        cur = market.conn.cursor()
        cur.execute("DROP TABLE pulse_content_products")
        market.conn.commit()
        assert tagging.tagged_listing_ids(cur, content_type="post", content_id=POST) == ()

    def test_and_says_so_in_the_log(self, market, caplog):
        """The one source whose absence downgrades an explicit creator statement
        to a guess. The symptom — a tagged post showing unrelated products —
        looks exactly like a ranking complaint, and an operator chasing that
        needs to be able to find the line."""
        cur = market.conn.cursor()
        cur.execute("DROP TABLE pulse_content_products")
        market.conn.commit()
        with caplog.at_level(logging.WARNING, logger="services.commerce_discovery.tagging"):
            tagging.tagged_listing_ids(cur, content_type="post", content_id=POST)
        assert "COMMERCE_DISCOVERY_TAGGED_LOOKUP_FAILED" in caplog.text

    def test_a_bad_reference_costs_no_query_at_all(self, market):
        """Returns `()` before touching the cursor, so a surface with no post on
        screen — which is most of them — pays nothing for this source."""
        touched = []

        class Watchful:
            def execute(self, *args, **kwargs):
                touched.append(args)
                raise AssertionError("a bad reference reached the database")

        assert tagging.tagged_listing_ids(Watchful(), content_type="post", content_id=0) == ()
        assert tagging.tagged_listing_ids(Watchful(), content_type="", content_id=POST) == ()
        assert touched == []


class TestASystemThatServesTheTag:
    """The engine end, where the tag has to actually change what a viewer sees."""

    def test_a_tagged_product_is_served_on_the_post_it_was_tagged_on(self, market):
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail", content_post_id=POST)
        assert 1 in _listing_ids(placements)

    def test_and_is_labelled_creator_tagged(self, market):
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail", content_post_id=POST)
        assert 1 in _listing_ids(placements), "the tagged product was not served at all"
        assert _provenance_of(market, placements, 1) == relationship.CREATOR_TAGGED

    def test_the_provenance_does_not_reach_the_buyers_device(self, market):
        """The §6 axis is an internal audit record, not a label. It is persisted
        against the placement and kept off the payload, because `_payload`'s
        serializer is a denylist and `seller_risk_score` and `candidate_source` are
        leaking today for exactly that reason."""
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail", content_post_id=POST)
        assert placements
        for placement in placements:
            assert "relationship" not in placement
            assert "relationship" not in (placement.get("product") or {})

    def test_creator_tagged_outranks_contextual(self, market):
        """A row can be true on two axes at once — retrieved by the tag *and* a
        good match for the post. One value gets recorded, and the same logic that
        makes `CONTEXTUAL` beat `PERSONALIZED` makes this beat `CONTEXTUAL`: for
        "how many cards matched nothing about the content?" to have a true answer,
        the more specific fact has to win."""
        assert relationship.classify(
            candidate_source=pool.SOURCE_TAGGED,
            signals={"relevance": 1.0},
            context_offered=True,
        ) == relationship.CREATOR_TAGGED

    def test_and_outranks_similar_on_a_product_page(self, market):
        assert relationship.classify(
            candidate_source=pool.SOURCE_TAGGED,
            signals={"relevance": 1.0},
            subject_is_product=True,
            context_offered=True,
        ) == relationship.CREATOR_TAGGED

    def test_no_post_id_means_no_tagged_provenance(self, market):
        """A client that sends nothing gets the system as it was. Asserted on the
        provenance rather than on emptiness, because the surface serves plenty of
        other rows and `[]` would prove nothing either way."""
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail")
        assert placements, "the surface served nothing, so this proves nothing"
        assert relationship.CREATOR_TAGGED not in _provenance(market, placements)

    def test_a_tag_on_another_post_does_not_leak_onto_this_one(self, market):
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail", content_post_id=POST + 1)
        assert placements, "the surface served nothing, so this proves nothing"
        assert relationship.CREATOR_TAGGED not in _provenance(market, placements)


class TestATagIsNotPersonalisation:
    def test_an_opted_out_viewer_still_sees_the_creators_product(self, market, monkeypatch):
        """A creator tag says nothing about the viewer — it is identical for every
        person reading the post — so the personalisation opt-out must not suppress
        it. The opt-out that *does* is the surface-level one, which returns before
        retrieval runs at all.

        This is the test that fails if the `tagged_listing_ids` call is ever
        "tidied up" inside the `if policy.personalized` block that `profile` is
        behind, which is the obvious-looking place for it.
        """
        from services.commerce_discovery import preferences

        real = preferences.viewer_policy

        def unpersonalised(cur, user_id, **kwargs):
            policy = real(cur, user_id, **kwargs)
            return policy.__class__(**{**policy.__dict__, "personalized": False})

        monkeypatch.setattr(preferences, "viewer_policy", unpersonalised)
        market.tag(1, post_id=POST)
        placements = market.serve("post_detail", content_post_id=POST)
        assert 1 in _listing_ids(placements)


class TestTheSuitabilityGateIsUpstreamOfAllOfThis:
    """A creator tag cannot force commerce onto a post the gate refuses.

    Not because the code is subtle — retrieval simply never runs on a refused
    request — but because a future refactor that "optimises" tagged lookups to
    happen *before* the gate would be a product failure with a green suite.
    Bereavement, medical and distress posts are what this is about.
    """

    def test_the_fixture_post_is_one_the_gate_refuses(self):
        """The precondition, asserted separately so the tests below cannot pass by
        being handed a post nobody was ever going to refuse. If this goes red they
        are all vacuous, and it names that rather than letting them stay green."""
        verdict = suitability.assess(dict(BEREAVEMENT_POST))
        assert verdict["code"] == suitability.SENSITIVE_CONTEXT

    def test_the_refusal_returns_before_retrieval(self, route):
        """Driven through the real route, and asserted on `engine.serve` never
        being called.

        The earlier version of this test called `suitability.assess` directly and
        then asserted `not served.called` — which was true because nothing had
        called the handler, so it certified the ordering while never exercising it.
        A mutation replacing the refusal with `None` survived it. The assertion
        only means something when a request actually reaches the route.

        Asserted on the call and not on the response body, because the route's
        fail-safe answers HTTP 200 with `[]`: a crash satisfies "no placements"
        more thoroughly than a refusal does.
        """
        client, spy, _rows = route
        response = _ask(client, "post_detail", post_id=POST)
        assert response.status_code == 200
        assert not spy.called, (
            "retrieval ran on a post the suitability gate refuses, so a creator "
            "tag on a bereavement post would be served"
        )

    def test_a_tag_cannot_be_resolved_on_a_refused_post(self, route, monkeypatch):
        """One level deeper than the test above: `tagging.tagged_listing_ids` is
        never reached either.

        `engine.serve` is what performs the lookup, so "serve was not called"
        already implies this — but only as long as that stays true. This asserts
        the property directly against the function that would do the work, so a
        future route that resolves tags itself (to "avoid a wasted engine call on
        untagged posts", the plausible version) fails here even though the
        assertion above still holds.
        """
        calls: list[int] = []
        real = tagging.tagged_listing_ids

        def watched(cur, **kwargs):
            calls.append(int(kwargs.get("content_id") or 0))
            return real(cur, **kwargs)

        monkeypatch.setattr(tagging, "tagged_listing_ids", watched)
        client, _spy, _rows = route
        _ask(client, "post_detail", post_id=POST)
        assert not calls, f"a refused post resolved creator tags anyway: {calls}"

    def test_the_lookup_is_below_the_gate_in_the_route(self):
        """Structural, because the ordering is the property and it lives in the
        route's control flow rather than in a value anybody can read back. The
        route resolves the id from the body, then the refusal `return`s, and the
        engine is what performs the lookup — so a refused request never reaches
        `tagging`."""
        source = (REPO / "services/commerce_discovery_routes.py").read_text()
        tree = ast.parse(source)
        # Scoped to the *serve* route's nested handler, not to the first function
        # in the module named `handler`. There are several — `marketplace/modules`
        # and `taggable-products` have one each — and `next()` over `ast.walk`
        # silently takes whichever the walk reached first. That happened to be
        # this one; the day a route moves above it, the test would go on reporting
        # confidently about a different handler.
        serve_route = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "commerce_discovery_serve"
        )
        handler = next(
            node for node in ast.walk(serve_route)
            if isinstance(node, ast.FunctionDef) and node.name == "handler"
        )
        lines = {"refusal": 0, "serve": 0}
        for node in ast.walk(handler):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_content_refusal":
                lines["refusal"] = node.lineno
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "serve":
                lines["serve"] = node.lineno
        assert lines["refusal"] and lines["serve"], lines
        # Strictly less, and the refusal line must not mention `serve` at all.
        # Line-number comparison alone is defeated by putting both on one line —
        # `verdict = None if engine.serve else _content_refusal(...)` keeps the
        # refusal textually first while never calling it.
        refusal_line = source.splitlines()[lines["refusal"] - 1]
        assert "serve" not in refusal_line, (
            "the suitability refusal line now references retrieval: "
            f"{refusal_line.strip()!r}"
        )
        assert lines["refusal"] < lines["serve"], (
            "`engine.serve` — which is what resolves creator tags — now runs at or "
            f"above the suitability refusal: {lines}. A creator tag must not be "
            "able to put commerce on a bereavement post."
        )
        # Scoped to the serve route, and it used to be `"tagging" not in source`
        # over the whole module. That was a cheap proxy for the right claim and it
        # went stale the moment a *write-side* endpoint in the same file needed
        # `tagging.MAX_TAGGED_PER_CONTENT` — the composer's picker, which is not on
        # the serve path and has no gate to be upstream or downstream of. It failed
        # loudly rather than quietly, which is the only reason it is being narrowed
        # instead of mourned.
        #
        # The property being kept: *this handler* must not resolve a tag itself. The
        # engine does the lookup, below the refusal, and a route that reached for
        # `tagging` here would be doing it above.
        # An alias (`from ... import tagging as _t`) defeats this string check, and
        # that is fine rather than a hole: `test_a_tag_cannot_be_resolved_on_a_
        # refused_post` above watches the function itself, so an aliased call is
        # still the same module attribute and is still recorded. Structure here,
        # behaviour there; the matrix entry `serve-route-resolves-tags-early` uses
        # the aliased form on purpose, so the pair is tested as a pair.
        served = ast.get_source_segment(source, serve_route) or ""
        assert "tagging." not in served, (
            "the serve route now resolves tags itself, which puts the lookup on "
            "the other side of the gate from where this file certifies it is"
        )

    @pytest.mark.parametrize("surface", sorted(NON_CONTENT_SURFACES))
    def test_a_surface_with_no_post_on_screen_ignores_a_post_id(self, route, surface):
        """Messenger and Marketplace have no post, so a `post_id` in their request
        body describes nothing the viewer can look at. Honouring it would put a
        creator's product inside a private conversation under a claim about
        content that is not there — the same mistake `CONTEXT_CLAIM` was rewritten
        to avoid.

        Behavioural, and it had to become behavioural: the earlier version matched
        a regex for the assignment *inside* the `CONTENT_SURFACES` branch, which an
        *additional* unconditional assignment above the branch leaves intact. That
        mutation — one line, and it hands every surface the id — survived the whole
        820-test package. Reading the value the engine was actually given is the
        only form of this assertion a mutant cannot satisfy by addition.
        """
        client, spy, _rows = route
        response = _ask(client, surface, post_id=POST, listing_id=1)
        assert response.status_code == 200
        assert spy.called, (
            f"{surface} served nothing at all, so this proves nothing about the "
            "post id it was given"
        )
        assert spy.post_ids == [0], (
            f"{surface} was handed post {spy.post_ids} from the request body. It "
            "has no post on screen, so any creator tag resolved from that id "
            "would be served under a claim about content the viewer cannot see."
        )

    def test_every_surface_that_does_have_a_post_uses_it(self, route):
        """The other half, so the test above cannot be satisfied by hard-wiring 0.

        Both directions in one place on purpose: "ignores the id" and "honours the
        id" are each trivially passable alone, and the pair is the actual rule.
        """
        client, spy, rows = route
        for surface in sorted(suitability.CONTENT_SURFACES):
            # A cleared post, so the gate permits and the handler reaches `serve`.
            rows.posts[POST] = {**BEREAVEMENT_POST,
                                "title": "my new running shoes",
                                "body": "finally found a pair that fits"}
            _ask(client, surface, post_id=POST)
        assert spy.post_ids == [POST] * len(suitability.CONTENT_SURFACES), (
            f"a content surface did not pass the post id through: {spy.calls}"
        )

    def test_a_deleted_post_serves_no_tags_and_the_rows_remain(self, route, market):
        """Deletion in PulseSoc is *soft*, and there is no cascade — not for this
        table and not for `pulse_content_music` either. I went looking for the one
        the precedent was supposed to provide; the only `DELETE FROM
        pulse_content_music` statements in `bot.py` are in the audio-*replacement*
        route.

        So "a deleted post shows no products" rests on the suitability gate, not on
        the data: `_content_post` filters `deleted_at IS NULL` in the query, an
        absent row is `_ROW_ABSENT`, and `_content_refusal` refuses on absent. Both
        halves are asserted here in one place — the refusal, and the rows surviving
        — because the second is the retention fact and the first is what makes it
        harmless. Anybody moving the gate needs to see them together.
        """
        market.tag(1, post_id=POST)
        before = _rows(market)
        assert before, "nothing was tagged, so this proves nothing"

        client, spy, rows = route

        # A *benign* post first, and the assertion that it serves. Without this the
        # test would be the §23 defect it is about: the `route` fixture's post is a
        # bereavement post, so removing it proves nothing — absence and grief would
        # refuse identically and the test could not tell which one it measured.
        rows.posts[POST] = {**BEREAVEMENT_POST,
                            "title": "my new running shoes",
                            "body": "finally found a pair that fits"}
        _ask(client, "post_detail", post_id=POST)
        assert spy.post_ids == [POST], (
            "the benign control did not reach retrieval, so the absence below "
            "cannot be what refuses"
        )
        spy.calls.clear()

        # Now the row is gone, which is exactly what a soft-deleted post looks like
        # to `_content_post` — it filters `deleted_at IS NULL` in the query.
        rows.posts.pop(POST, None)
        response = _ask(client, "post_detail", post_id=POST)
        assert response.status_code == 200
        assert not spy.called, (
            "a post the server cannot see reached retrieval, so its creator tags "
            "would have been resolved"
        )
        assert _rows(market) == before, (
            "the tag rows were cleaned up somewhere. That is not wrong, but this "
            "test and the retention note in §24 both say they are not, so one of "
            "the two is now lying."
        )

    def test_the_post_id_is_resolved_in_exactly_one_place(self):
        """Structural backstop, counting the call sites rather than matching one.

        A behavioural test covers the three shipped non-content surfaces; this
        covers the fourth that does not exist yet. `SURFACES` grows, and a surface
        added after this file would get the id unconditionally with nothing red if
        the only guard were the parametrized test above.
        """
        source = (REPO / "services/commerce_discovery_routes.py").read_text()
        tree = ast.parse(source)
        handler = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "handler"
        )

        # Every `_content_post_id(...)` call in the handler, with the chain of
        # `If` tests it sits under. Built by walking down from the handler rather
        # than up from the call, because `ast` nodes carry no parent pointer.
        found: list[list[ast.expr]] = []

        def descend(node, guards):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_content_post_id":
                found.append(guards)
            if isinstance(node, ast.If):
                # `elif` is an `If` inside the outer `If`'s `orelse`, so the
                # recursion has to inspect a node's *own* test rather than only
                # its children's — the first version of this walker did the
                # latter and reported every call site as unguarded.
                descend(node.test, guards)
                for sub in node.body:
                    descend(sub, guards + [node.test])
                for sub in node.orelse:
                    descend(sub, guards)
                return
            for child in ast.iter_child_nodes(node):
                descend(child, guards)

        descend(handler, [])
        assert len(found) == 1, (
            f"the handler resolves the content post id in {len(found)} places, not "
            "one. Every extra one is a surface getting a post id the viewer has no "
            "post for."
        )
        guarded_by = {ast.unparse(test) for test in found[0]}
        assert "surface in suitability.CONTENT_SURFACES" in guarded_by, (
            "the post id is resolved outside the CONTENT_SURFACES branch; it is "
            f"guarded only by {sorted(guarded_by)}"
        )
        assert suitability.CONTENT_SURFACES == frozenset({"feed", "reels", "post_detail"})


class TestTheFixtureIsTheTableProductionHas:
    """This suite's DDL is a hand-copy of `bot.init_db`'s, so it can drift.

    Without this test every assertion in the file would keep passing against an
    imaginary table after a column was added in production — which is the failure
    mode that makes a green suite worthless rather than merely incomplete.
    """

    def test_the_columns_match_bot_init_db(self, market):
        source = (REPO / "bot.py").read_text()
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS pulse_content_products \((.*?)\n    \)",
            source, re.S,
        )
        assert match, "bot.py no longer creates pulse_content_products"
        declared = {
            line.strip().split()[0]
            for line in match.group(1).strip().splitlines()
            if line.strip() and not line.strip().upper().startswith("UNIQUE")
        }
        cur = market.conn.cursor()
        cur.execute("PRAGMA table_info(pulse_content_products)")
        fixture = {dict(row)["name"] for row in cur.fetchall()}
        assert declared == fixture, (
            "the test fixture's table no longer matches production's. Missing "
            f"from the fixture: {sorted(declared - fixture)}; extra in the "
            f"fixture: {sorted(fixture - declared)}"
        )

    def test_the_package_does_not_create_the_table_itself(self):
        """The writer is the composer, so the table belongs to `bot.init_db`.
        Making a post save depend on the discovery package's schema guard would
        be the wrong direction for that dependency — and would mean a broken
        commerce migration could stop people posting."""
        source = (REPO / "services/commerce_discovery/schema.py").read_text()
        assert tagging.TABLE not in source
        assert tagging.TABLE not in schema.TABLES if hasattr(schema, "TABLES") else True


class TestAFormPostReachesTheHelperToo:
    """`pulse_product_tag_ids_from_payload` accepts three key names "because
    three clients" — and for one of them the keys were unreachable.

    `POST /api/pulse/posts` has two branches. The JSON one hands
    `request.get_json()` to the helper directly, so any of its three names
    arrives. The multipart/urlencoded one *rebuilds* `payload` as a dict literal
    from named form fields, and that literal did not name the tag ids at all — so
    a form post's tags were dropped before the helper ran, with no error on either
    side. The helper's own docstring calls `listing_ids` the name "the web
    composer's existing marketplace forms already use", which makes the client the
    alias exists for precisely the client that could not use it.

    These are structural rather than behavioural because the route is in `bot.py`,
    where `import bot` runs `init_db()` at module scope. The claim is about which
    keys the branch reads, which is visible in the source, so the cost of a live
    request buys nothing here.

    Parsed with `ast` and never indexed back to a line: `bot.py` contains raw
    U+2028, which `str.splitlines()` treats as a break and `ast` does not, so any
    node-to-source-line mapping in this file would be off by a growing amount.
    """

    @staticmethod
    def _bot_tree():
        return ast.parse((REPO / "bot.py").read_text())

    @staticmethod
    def _alias_tuple(function: ast.FunctionDef) -> list[str]:
        """The key names a `for key in (...)` loop in `function` walks."""
        for node in ast.walk(function):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                names = [
                    element.value for element in node.iter.elts
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                ]
                if "product_listing_ids" in names:
                    return names
        return []

    def _function(self, name: str) -> ast.FunctionDef:
        tree = self._bot_tree()
        found = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        assert len(found) == 1, f"expected exactly one {name} in bot.py, found {len(found)}"
        return found[0]

    def test_the_form_branch_names_the_tag_ids_in_its_payload(self):
        route = self._function("api_pulse_posts")
        # The rebuilt payload, identified by two keys only it has, so this does
        # not depend on it being the only dict literal in the route.
        literals = [
            node for node in ast.walk(route)
            if isinstance(node, ast.Dict)
            and {
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            } >= {"post_type", "media_ids", "visibility"}
        ]
        assert len(literals) == 1, (
            f"found {len(literals)} candidate rebuilt payloads in api_pulse_posts; "
            "this test can no longer tell which one the form branch uses"
        )
        keys = {
            key.value for key in literals[0].keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        assert "product_listing_ids" in keys, (
            "the multipart branch of POST /api/pulse/posts rebuilds its payload "
            "without the tag ids, so every product a form-based composer tags is "
            f"discarded before pulse_product_tag_ids_from_payload runs. Keys: {sorted(keys)}"
        )

    def test_the_form_branch_reads_every_name_the_helper_accepts(self):
        """The two lists are the same claim written twice, so they can drift.

        Adding a fourth alias to the helper is a one-line change that would look
        complete and would work on the JSON path only — the same defect this class
        exists for, one alias later.
        """
        helper_names = self._alias_tuple(self._function("pulse_product_tag_ids_from_payload"))
        assert helper_names, "pulse_product_tag_ids_from_payload no longer walks a tuple of key names"
        form_names = self._alias_tuple(self._function("api_pulse_posts"))
        assert form_names == helper_names, (
            "the multipart branch and the payload helper disagree about which key "
            f"names carry tag ids. Helper accepts {helper_names}; the form branch "
            f"reads {form_names}. A name in the first list and not the second is "
            "accepted on the JSON path and silently dropped on the form path."
        )

    def test_the_form_branch_does_not_lose_a_repeated_field(self):
        """A checkbox picker submits one name many times.

        `form.get` returns the first value of a repeated field and discards the
        rest, which would present as "only the first product I ticked was tagged"
        — a partial silent loss, which is harder to notice than a total one.
        """
        route = self._function("api_pulse_posts")
        reads = [
            ast.unparse(node) for node in ast.walk(route)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getlist"
        ]
        assert any("key" in read for read in reads), (
            "the form branch reads its tag ids with form.get rather than "
            f"form.getlist, so a repeated field keeps only its first value. getlist calls: {reads}"
        )
