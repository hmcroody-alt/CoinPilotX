"""What "Why am I seeing this?" is allowed to say.

The sheet is a user-facing feature, so it has two obligations that pull in
opposite directions: it has to be a real answer (a recommender nobody can
interrogate is one no seller will trust), and it has to disclose nothing that
belongs to somebody else.

The thing being guarded is narrow and specific. ``explain`` picks the top
positive score contributions, and *positive* is not the same test as *sayable*:

* ``COMMERCE_DISCOVERY_WEIGHTS`` is a JSON env override, so one sign typo turns
  a penalty into a positive contributor;
* ``seller_risk`` and ``refund_risk`` are internal judgements about a named
  store, and the sheet is read by that store's potential customers;
* none of the four penalties has an i18n key, so rendering one puts a raw key
  string on screen in every locale.

``load_placement`` is stubbed here rather than exercised. It is an HMAC check
and a database read — worth its own coverage, but neither is what decides what
the sheet says, and threading a real cursor through would bury the one line
under a schema fixture.
"""

import json

import pytest

from services.commerce_discovery import events, ranking


def breakdown(**contributions):
    return json.dumps({"contributions": contributions})


def placement_row(score_breakdown_json, **overrides):
    row = {
        "placement_id": "pl_1",
        "reason_code": "because_you_viewed",
        "promotion_class": "organic",
        "ranking_version": "commerce-discovery-v1",
        "score_breakdown_json": score_breakdown_json,
    }
    row.update(overrides)
    return row


@pytest.fixture
def stub_placement(monkeypatch):
    """Serve one fixed row in place of the HMAC check and the database read."""

    def load(row):
        monkeypatch.setattr(events, "load_placement", lambda cur, pid, token: row)

    return load


class TestWhatTheSheetSays:
    def test_names_the_top_contributors_and_never_their_numbers(self, stub_placement):
        stub_placement(
            placement_row(
                breakdown(relevance=0.25, quality=0.14, freshness=0.05, seller_reliability=0.01)
            )
        )
        result = events.explain(None, "pl_1", "tok")

        # Names only. Publishing the weight vector would let anyone
        # reverse-engineer the ranker by listing products and reading their own
        # scores back.
        assert result["factors"] == ["relevance", "quality", "freshness"]
        assert all(isinstance(name, str) for name in result["factors"])

    def test_caps_the_answer_at_three_reasons(self, stub_placement):
        stub_placement(
            placement_row(
                breakdown(
                    relevance=0.9,
                    quality=0.8,
                    freshness=0.7,
                    seller_reliability=0.6,
                    diversity_bonus=0.5,
                )
            )
        )
        assert len(events.explain(None, "pl_1", "tok")["factors"]) == 3

    def test_carries_the_reason_and_the_ranking_version(self, stub_placement):
        stub_placement(placement_row(breakdown(relevance=0.3)))
        result = events.explain(None, "pl_1", "tok")
        assert result["ok"] is True
        assert result["reason"] == "because_you_viewed"
        assert result["ranking_version"] == "commerce-discovery-v1"

    def test_labels_the_placement_from_its_class(self, stub_placement):
        stub_placement(placement_row(breakdown(relevance=0.3), promotion_class="organic"))
        assert events.explain(None, "pl_1", "tok")["label_key"] == (
            "commerce:discovery.label.recommended"
        )


class TestWhatTheSheetMustNeverSay:
    @pytest.mark.parametrize(
        "penalty", ["seller_risk", "refund_risk", "hide_penalty", "repetition_penalty"]
    )
    def test_omits_a_penalty_even_when_its_contribution_is_positive(
        self, stub_placement, penalty
    ):
        # The sign-typo scenario, reproduced: an operator flips one weight in
        # COMMERCE_DISCOVERY_WEIGHTS and the penalty now contributes positively.
        # A `contribution > 0` filter alone would publish it.
        stub_placement(placement_row(breakdown(relevance=0.1, **{penalty: 0.9})))
        factors = events.explain(None, "pl_1", "tok")["factors"]
        assert penalty not in factors
        assert factors == ["relevance"]

    def test_says_nothing_rather_than_naming_an_unsayable_term(self, stub_placement):
        # Every positive contributor is a penalty. An empty list renders the
        # sheet's "no detail available" copy; a populated one would render four
        # raw i18n keys.
        stub_placement(
            placement_row(breakdown(seller_risk=0.9, refund_risk=0.8, hide_penalty=0.7))
        )
        assert events.explain(None, "pl_1", "tok")["factors"] == []

    def test_omits_a_term_the_model_gained_before_the_catalog_did(self, stub_placement):
        # A new signal added to the ranker with no i18n key yet. It must wait
        # for its translation rather than render as `commerce:...new_signal`.
        stub_placement(placement_row(breakdown(relevance=0.2, brand_new_signal=0.9)))
        assert events.explain(None, "pl_1", "tok")["factors"] == ["relevance"]

    def test_every_sayable_factor_has_somewhere_to_be_said(self):
        # The list the filter admits is exactly the list the client can render.
        assert ranking.EXPLAINABLE_FACTORS
        for name in ranking.EXPLAINABLE_FACTORS:
            assert name.replace("_", "").isalnum()


class TestMalformedRows:
    @pytest.mark.parametrize("raw", ["", None, "{not json", "[]", "null"])
    def test_an_unreadable_breakdown_is_an_empty_answer_not_an_error(
        self, stub_placement, raw
    ):
        # The sheet degrades to "we can't explain this one". Raising would turn
        # a curiosity tap into an error dialog.
        stub_placement(placement_row(raw))
        result = events.explain(None, "pl_1", "tok")
        assert result["ok"] is True
        assert result["factors"] == []

    @pytest.mark.parametrize("raw", ['{"contributions": []}', '{"contributions": "x"}',
                                     '{"contributions": null}'])
    def test_a_breakdown_shaped_wrongly_is_also_just_an_empty_answer(self, stub_placement, raw):
        # These parse without raising, so the try/except around `json.loads` never
        # sees them. The shape has to be checked separately or the next
        # `.items()` call is the thing that fails.
        stub_placement(placement_row(raw))
        assert events.explain(None, "pl_1", "tok")["factors"] == []

    def test_ignores_contributions_that_are_not_numbers(self, stub_placement):
        stub_placement(
            placement_row(breakdown(relevance="high", quality=None, freshness=0.2))
        )
        assert events.explain(None, "pl_1", "tok")["factors"] == ["freshness"]

    def test_omits_a_term_that_contributed_nothing(self, stub_placement):
        stub_placement(placement_row(breakdown(relevance=0.3, quality=0.0, freshness=-0.1)))
        # Zero did not lift this product, and negative argues against it.
        assert events.explain(None, "pl_1", "tok")["factors"] == ["relevance"]
