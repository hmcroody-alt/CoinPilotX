"""§6: how the product got here, recorded — and never conflated with the other two.

§6 names seven relationship types and says they "MUST NOT be silently conflated".
Before `relationship.py` they were, and the interesting part is that it was not
carelessness — the axis did not exist. Two things were being mistaken for it:

``promotion_class``
    who *funded* the card.

``reason_code``
    what the buyer is *told*, derived in `ranking.choose_reason` from signal
    thresholds with no knowledge of which retrieval question produced the row.

So an untargeted `rotation` listing whose relevance happened to clear the bar
earned ``related_to_this_post`` and was afterwards indistinguishable from one
retrieved *because* it matched the post. `pool` knew the difference and stamped
``candidate_source`` on every row, but `metrics.observe_sources` aggregated it to
a per-surface count and nothing persisted it.

What this file pins, and why each one is here rather than being a comment:

* **The two readers of the 0.6 threshold move together.** `choose_reason` uses it
  to decide whether to say "related to this post" out loud; `classify` uses it to
  decide whether the recorded provenance is ``contextual``. Two copies would be
  one edit away from a card whose label claims a match while its own audit row
  says the context never matched — the same conflation, one layer down. Tested by
  moving the constant and watching both follow, which a test asserting ``== 0.6``
  cannot do.
* **§44's ordering.** Context beats personalization, so ``PERSONALIZED`` means
  *the content did not match* rather than "affinity retrieved it". Get this
  backwards and "is personalization overriding context?" reports violations that
  did not happen while hiding ones that did.
* **The three declared-but-unbuilt values never leak.** ``creator_tagged``,
  ``complementary`` and ``pulsedrop_curated`` have no write path. A placement row
  claiming one would be a *fabricated* provenance, which is worse than a missing
  one because it would be believed.
* **A surface with no contextual claim cannot record a contextual match.** The
  route hands `messenger` and `marketplace` a client-supplied context even though
  neither has a `CONTEXT_CLAIM` entry, so this is reachable from the wire rather
  than hypothetical.
* **The column is really on the row in the database**, not merely computed. That
  is the whole point of the additive schema path.
"""

from __future__ import annotations

import pytest

from services.commerce_discovery import engine, ranking, relationship, schema

#: A listing bland enough that no reason *except* the contextual one can fire, so
#: the threshold tests below are measuring the threshold. No followed seller, no
#: viewed category, no stats, and every other signal left out entirely.
LISTING = {"seller_user_id": 4242, "category": "shoes", "title": "Sneakers"}


def placement_rows(market):
    """Every placement row this database holds, as dicts."""
    cur = market.conn.cursor()
    cur.execute(
        "SELECT surface, reason_code, relationship, promotion_class "
        "FROM commerce_discovery_placements ORDER BY created_at, slot"
    )
    return [dict(row) for row in cur.fetchall()]


class TestTheVocabularyIsClosed:
    def test_the_seven_types_section_six_names_are_all_declared(self):
        """Spelled out, not derived. A typo'd constant would still be a member of
        `ALL_RELATIONSHIPS` if the set were built from the constants themselves —
        the brief's spelling is the thing being pinned."""
        for name in (
            "creator_tagged", "contextual", "personalized", "similar",
            "complementary", "pulsedrop_curated", "sponsored",
        ):
            assert name in relationship.ALL_RELATIONSHIPS, name

    def test_catalogue_is_the_eighth_and_is_deliberate(self):
        """§6 enumerates seven. `trending` and `rotation` are neither about the
        content nor about the viewer, and folding them into one of the seven is
        the conflation §6 forbids. Flagged in the report, not decided quietly."""
        assert relationship.CATALOGUE not in {
            relationship.CREATOR_TAGGED, relationship.CONTEXTUAL,
            relationship.PERSONALIZED, relationship.SIMILAR,
            relationship.COMPLEMENTARY, relationship.PULSEDROP_CURATED,
            relationship.SPONSORED,
        }
        assert relationship.CATALOGUE in relationship.SERVABLE_RELATIONSHIPS

    def test_servable_and_unimplemented_do_not_overlap(self):
        assert not (
            relationship.SERVABLE_RELATIONSHIPS & relationship.UNIMPLEMENTED_RELATIONSHIPS
        )

    def test_sponsored_is_neither_servable_nor_merely_unbuilt(self):
        """The distinction matters: the other three are "not yet", this one is
        "not here, ever" — `services/business_os/advertising` owns it."""
        assert relationship.SPONSORED not in relationship.SERVABLE_RELATIONSHIPS
        assert relationship.SPONSORED not in relationship.UNIMPLEMENTED_RELATIONSHIPS


class TestUnknownInputDoesNotBecomeAValue:
    @pytest.mark.parametrize("value", ["", None, "  ", "nonsense", "creator-tagged", 0, [], {}])
    def test_normalize_returns_empty_rather_than_guessing(self, value):
        assert relationship.normalize(value) == ""

    def test_an_unknown_value_never_defaults_to_catalogue(self):
        """The one value that would raise no eyebrow in a report is exactly the
        wrong default: a malformed relationship would be silently accounted as
        "the ranker's own choice", which claims no relationship to anything."""
        assert relationship.normalize("tagged_by_creator") != relationship.CATALOGUE

    def test_case_and_padding_are_tolerated_on_a_real_name(self):
        assert relationship.normalize("  CONTEXTUAL ") == relationship.CONTEXTUAL


class TestTheWriteGateRefuses:
    def test_sponsored_raises_and_says_who_owns_it(self):
        with pytest.raises(relationship.RelationshipError) as caught:
            relationship.assert_servable(relationship.SPONSORED)
        assert "advertising" in str(caught.value)

    @pytest.mark.parametrize("value", sorted(relationship.UNIMPLEMENTED_RELATIONSHIPS))
    def test_a_declared_but_unbuilt_value_cannot_be_written(self, value):
        with pytest.raises(relationship.RelationshipError):
            relationship.assert_servable(value)

    @pytest.mark.parametrize("value", sorted(relationship.SERVABLE_RELATIONSHIPS))
    def test_every_servable_value_passes(self, value):
        assert relationship.assert_servable(value) == value

    def test_an_unrecorded_relationship_is_refused_rather_than_defaulted(self):
        """`_persist` calls this with `row.get("relationship")`. If the engine ever
        stops stamping the row, the correct outcome is a dropped placement and a
        logged warning — not a row asserting a provenance nobody computed."""
        with pytest.raises(relationship.RelationshipError):
            relationship.assert_servable(None)


class TestClassifyNeverProducesWhatNothingCanProduce:
    """A sweep rather than a single case, because the guarantee is universal."""

    def test_no_input_combination_yields_an_unbuilt_value(self):
        produced = set()
        sources = list(relationship.VIEWER_SOURCES) + ["trending", "rotation", "", None, "bogus"]
        for source in sources:
            for relevance in (0.0, 0.59, 0.6, 1.0, None):
                for subject_is_product in (False, True):
                    for offered in (False, True):
                        produced.add(relationship.classify(
                            candidate_source=source,
                            signals={"relevance": relevance},
                            subject_is_product=subject_is_product,
                            context_offered=offered,
                        ))
        assert not (produced & relationship.UNIMPLEMENTED_RELATIONSHIPS)
        assert relationship.SPONSORED not in produced
        assert produced <= relationship.SERVABLE_RELATIONSHIPS

    def test_every_servable_value_is_reachable(self):
        """The other half. A guarantee that `classify` never returns an unbuilt
        value would also hold if it always returned the same thing."""
        assert relationship.classify(
            candidate_source="rotation", signals={"relevance": 0.9},
            context_offered=True,
        ) == relationship.CONTEXTUAL
        assert relationship.classify(
            candidate_source="rotation", signals={"relevance": 0.9},
            subject_is_product=True, context_offered=True,
        ) == relationship.SIMILAR
        assert relationship.classify(
            candidate_source="affinity", signals={"relevance": 0.0},
        ) == relationship.PERSONALIZED
        assert relationship.classify(
            candidate_source="trending", signals={"relevance": 0.0},
        ) == relationship.CATALOGUE

    def test_missing_signals_does_not_raise(self):
        assert relationship.classify(candidate_source="trending") == relationship.CATALOGUE


class TestContextBeatsPersonalization:
    """§44, as arithmetic rather than as a policy someone has to remember."""

    def test_an_affinity_row_that_also_matched_the_post_is_contextual(self):
        assert relationship.classify(
            candidate_source="affinity",
            signals={"relevance": 0.95},
            context_offered=True,
        ) == relationship.CONTEXTUAL

    def test_personalized_means_the_content_did_not_match(self):
        """The load-bearing consequence. If this said `personalized`, then
        counting personalized cards on a content surface would count cards that
        *did* match the content, and the §44 audit would be meaningless."""
        assert relationship.classify(
            candidate_source="affinity",
            signals={"relevance": 0.1},
            context_offered=True,
        ) == relationship.PERSONALIZED

    def test_a_context_policy_forbade_cannot_produce_a_contextual_match(self):
        """`engine.serve` passes `context=None` when policy forbids
        personalization, so a request can carry a context that was never used."""
        assert relationship.classify(
            candidate_source="affinity",
            signals={"relevance": 0.95},
            context_offered=False,
        ) == relationship.PERSONALIZED

    def test_an_unrecognised_source_understates_rather_than_overstates(self):
        """A source added to `pool` and not to `VIEWER_SOURCES` falls through to
        `catalogue`. Claiming less than we know beats claiming more."""
        assert relationship.classify(
            candidate_source="a_source_invented_next_quarter",
            signals={"relevance": 0.0},
        ) == relationship.CATALOGUE


class TestTheThresholdHasExactlyOneSource:
    def test_the_boundary_is_inclusive_on_both_readers(self):
        floor = ranking.CONTEXT_CLAIM_MIN_RELEVANCE
        assert relationship.classify(
            candidate_source="rotation", signals={"relevance": floor},
            context_offered=True,
        ) == relationship.CONTEXTUAL
        assert relationship.classify(
            candidate_source="rotation", signals={"relevance": floor - 0.01},
            context_offered=True,
        ) == relationship.CATALOGUE

    def test_moving_the_constant_moves_both_readers_together(self, monkeypatch):
        """The real assertion of this file, and why it is not `assert x == 0.6`.

        A test pinning the literal would still pass if `classify` grew its own
        copy. Raising the bar to 0.95 must silence *both* the spoken claim and
        the recorded provenance for a card at 0.7; if only one moves, the label
        and the audit row have started disagreeing.
        """
        monkeypatch.setattr(ranking, "CONTEXT_CLAIM_MIN_RELEVANCE", 0.95)

        # Only `relevance` fires, so nothing else can supply the claim and make
        # this pass for the wrong reason.
        signals = {"relevance": 0.7}
        spoken = ranking.choose_reason(
            LISTING, signals, has_context=True, context_reason=ranking.REASON_CONTEXT,
        )
        recorded = relationship.classify(
            candidate_source="rotation", signals=signals, context_offered=True,
        )

        assert spoken != ranking.REASON_CONTEXT, (
            "choose_reason read a private copy of the threshold"
        )
        assert recorded != relationship.CONTEXTUAL, (
            "classify read a private copy of the threshold"
        )

    def test_lowering_it_licenses_both(self, monkeypatch):
        monkeypatch.setattr(ranking, "CONTEXT_CLAIM_MIN_RELEVANCE", 0.05)
        signals = {"relevance": 0.1}
        assert ranking.choose_reason(
            LISTING, signals, has_context=True, context_reason=ranking.REASON_CONTEXT,
        ) == ranking.REASON_CONTEXT
        assert relationship.classify(
            candidate_source="rotation", signals=signals, context_offered=True,
        ) == relationship.CONTEXTUAL


class TestTheColumnReachesTheDatabase:
    """End to end, against the real schema — the point of the additive path."""

    def test_every_served_placement_records_a_servable_relationship(self, market):
        served = market.serve("feed")
        assert served, "the fixture must produce placements for this to mean anything"
        rows = placement_rows(market)
        assert len(rows) == len(served)
        for row in rows:
            assert relationship.is_servable(row["relationship"]), row

    def test_a_matching_context_is_recorded_as_contextual(self, market):
        served = market.serve("feed", context={"category": "shoes"})
        assert served
        recorded = {row["relationship"] for row in placement_rows(market)}
        assert relationship.CONTEXTUAL in recorded

    def test_a_product_page_records_similar_rather_than_contextual(self, market):
        served = market.serve("product_detail", context={"category": "shoes"})
        assert served
        recorded = {row["relationship"] for row in placement_rows(market)}
        assert relationship.SIMILAR in recorded
        assert relationship.CONTEXTUAL not in recorded

    def test_no_context_records_no_contextual_match_anywhere(self, market):
        assert market.serve("feed")
        for row in placement_rows(market):
            assert row["relationship"] not in (
                relationship.CONTEXTUAL, relationship.SIMILAR,
            ), row

    def test_the_recorded_relationship_agrees_with_the_spoken_reason(self, market):
        """The conflation check, on real rows. If the label says "related to this
        post" the provenance must be a contextual one, and vice versa."""
        assert market.serve("feed", context={"category": "shoes"})
        for row in placement_rows(market):
            claims_context = row["reason_code"] == ranking.REASON_CONTEXT
            recorded_context = row["relationship"] == relationship.CONTEXTUAL
            assert claims_context == recorded_context, row

    def test_the_three_axes_are_independent_columns_on_one_row(self, market):
        """§6's audit is reading them side by side; it cannot be done if one of
        them is derived from another at read time."""
        assert market.serve("feed", context={"category": "shoes"})
        row = placement_rows(market)[0]
        assert set(row) >= {"promotion_class", "reason_code", "relationship"}
        assert row["promotion_class"] and row["reason_code"] and row["relationship"]


class TestASurfaceWithNoClaimCannotRecordOne:
    """Reachable from the wire: the serve route hands `messenger` and
    `marketplace` a client-supplied context, and neither is in `CONTEXT_CLAIM`."""

    @pytest.mark.parametrize("surface", ["messenger", "marketplace"])
    def test_a_client_context_does_not_become_a_contextual_provenance(self, market, surface):
        assert surface not in engine.CONTEXT_CLAIM, (
            "this test is about the surfaces that cannot make a contextual claim; "
            "if one has gained a CONTEXT_CLAIM entry, move it to the other class"
        )
        served = market.serve(surface, context={"category": "shoes"})
        assert served, "a context must not empty the surface — that would hide the bug"
        for row in placement_rows(market):
            assert row["relationship"] not in (
                relationship.CONTEXTUAL, relationship.SIMILAR,
            ), (
                f"{surface} recorded a contextual match while its own reason_code "
                f"({row['reason_code']!r}) could not claim one"
            )


class TestTheAdditiveColumnIsIdempotent:
    def test_a_second_pass_over_a_database_that_has_the_column_succeeds(self, market):
        """Which is every pass after the first, in production and here.

        On PostgreSQL `services.db._translate_alter_table` injects
        `IF NOT EXISTS`; on SQLite the ALTER raises `duplicate column name` and
        `_add_columns` treats that as success. This exercises the SQLite half,
        which is the half that raises.
        """
        schema.ensure_schema.reset()
        assert schema.ensure_schema(market.conn) is True
        schema.ensure_schema.reset()
        assert schema.ensure_schema(market.conn) is True

    def test_a_real_alter_failure_is_not_swallowed(self, market):
        """The marker list must catch "already there" and nothing else. A bare
        `except Exception: continue` would make a mistyped column type invisible.
        """
        cur = market.conn.cursor()
        with pytest.raises(Exception) as caught:
            cur.execute("ALTER TABLE a_table_that_does_not_exist ADD COLUMN x TEXT")
        assert not any(
            marker in str(caught.value).lower()
            for marker in schema._DUPLICATE_COLUMN_MARKERS
        ), (
            "a missing-table error matches the duplicate-column marker, so "
            "`_add_columns` would swallow it"
        )
