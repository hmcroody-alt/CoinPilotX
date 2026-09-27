"""The pool row carries ranking internals. The buyer's device must not.

This file exists because the suite was structurally incapable of catching the
defect it pins, and that is worth stating plainly rather than fixing quietly.

`_payload` hands the product row to `bot.pulse_marketplace_listing_payload`, and
its docstring used to argue that doing so was the *safe* choice because that
serializer "strips the reviewer-only columns". Half true, in the dangerous
direction. The serializer is a **denylist**::

    {k: v for k, v in dict(listing).items() if k not in MARKETPLACE_REVIEWER_ONLY_FIELDS}

It removes the columns *it* knows about. Every column this package invents — and
`eligibility.py`'s SELECT list invents several — is unknown to it and therefore
ships to the phone by default.

Why no existing test could see it
--------------------------------

``conftest``'s ``SimulatedMarketplace.serve`` passes no ``serialize``, so every
test in this package takes `_payload`'s ``serialize is None`` fallback — and that
fallback is a hand-written **allowlist** of nine buyer-visible fields. So the
fixture models the one code path that is safe by construction and never the one
production runs. Driven against the real serializer, two columns were already
reaching buyers before this file existed:

``seller_risk_score``
    ``COALESCE(ms.risk_score,0)`` (`eligibility.py`), read by the ranker's
    seller-reliability signal. `ranking.EXPLAINABLE_FACTORS` already refuses to
    publish the *reason* derived from this number, on the stated grounds that
    naming it "publishes an internal assessment of a named store". The raw score
    is strictly worse than the reason.

``candidate_source``
    Stamped per row by `pool.build`. A free readout of which retrieval question
    produced each card.

So the tests below do not use the fixture's serializer. They use a stand-in whose
*only* behaviour is the one that matters — unknown keys pass through — and
separately pin that the real serializer still has that shape, so the stand-in
cannot drift into flattering the engine.
"""

from __future__ import annotations

import ast
import os

import pytest

from services.commerce_discovery import engine

#: Mirrors `bot.MARKETPLACE_REVIEWER_ONLY_FIELDS`. Only the two this package's
#: SELECT actually carries, because the stand-in's job is to be *pass-through*,
#: not to be a faithful copy of a list that is pinned separately below.
STANDIN_DENYLIST = frozenset({"safety_score", "moderation_reason"})


def pass_through_serializer(listing):
    """A denylist serializer, which is what production has.

    Deliberately not an allowlist. An allowlist stand-in would pass no matter
    what the engine put on the row, which is exactly how the real defect stayed
    invisible for as long as it did.
    """
    return {key: value for key, value in dict(listing or {}).items()
            if key not in STANDIN_DENYLIST}


def serve_with_real_serializer(market, surface="feed", **kwargs):
    return engine.serve(
        market.conn.cursor(),
        market.viewer_id,
        surface,
        conn=market.conn,
        serialize=pass_through_serializer,
        **kwargs,
    )


class TestTheStandInIsFaithful:
    """If these drift, everything below becomes decoration."""

    def test_the_real_serializer_is_still_a_denylist(self):
        """Source-level, because importing `bot` runs `init_db()` at module scope.

        Pinned as a *shape* rather than a literal: what matters is that the first
        thing the function does is a dict comprehension filtered by ``not in``
        some name. An allowlist rewrite would make `PIPELINE_ONLY_FIELDS`
        unnecessary — and this test the place that says so.
        """
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repo, "bot.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())

        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "pulse_marketplace_listing_payload":
                target = node
                break
        assert target is not None, (
            "bot.pulse_marketplace_listing_payload is gone; this package's "
            "`serialize` contract needs re-reading before this file means anything"
        )

        first = target.body[0]
        assert isinstance(first, ast.Assign), ast.dump(first)
        assert isinstance(first.value, ast.DictComp), (
            "the serializer no longer opens with a filtering dict comprehension; "
            "re-read it — if it has become an allowlist, `PIPELINE_ONLY_FIELDS` "
            "is now belt-and-braces rather than the only thing standing between "
            "a ranking internal and a buyer's phone"
        )
        comparisons = [
            node for node in ast.walk(first.value)
            if isinstance(node, ast.Compare)
            and any(isinstance(op, ast.NotIn) for op in node.ops)
        ]
        assert comparisons, (
            "the comprehension no longer filters on `not in`, so it may have "
            "inverted from a denylist to an allowlist"
        )

    def test_the_fixture_serializer_is_the_one_that_hid_this(self):
        """Names the gap so it cannot be reintroduced by 'simplifying' the fixture.

        `_payload`'s no-serializer fallback is an allowlist. That is correct for
        the fallback and wrong as a test double, and the distinction is the whole
        reason this file passes its own serializer.
        """
        import inspect
        source = inspect.getsource(engine._payload)
        assert "Minimal safe fallback" in source, (
            "the allowlist fallback has moved or been renamed; if the fallback is "
            "no longer an allowlist, the fixture's blind spot has changed shape"
        )


class TestNoPipelineColumnReachesTheBuyer:
    @pytest.mark.parametrize("field", sorted(engine.PIPELINE_ONLY_FIELDS))
    def test_each_declared_field_is_absent_from_the_product(self, market, field):
        served = serve_with_real_serializer(market, context={"category": "shoes"})
        assert served, "no placements means this proves nothing"
        for placement in served:
            assert field not in placement["product"], (
                f"{field!r} reached the client payload"
            )

    def test_the_reviewer_only_columns_are_still_removed_too(self, market):
        """The guarantee `_payload`'s docstring always claimed. Now that this
        package strips its own columns first, it must not have taken over the
        serializer's job and stopped passing it the ones it handles."""
        served = serve_with_real_serializer(market)
        assert served
        for placement in served:
            for field in STANDIN_DENYLIST:
                assert field not in placement["product"], field

    def test_seller_risk_score_is_on_the_row_so_the_strip_is_doing_work(self, market):
        """Otherwise the test above passes because the column was never selected,
        and a future SELECT that adds it back would ship it unnoticed."""
        from services.commerce_discovery import eligibility

        assert any(
            "seller_risk_score" in expression
            for expression in eligibility.CANDIDATE_COLUMNS
        ), (
            "seller_risk_score is no longer selected. If the ranker stopped "
            "reading it, remove it from PIPELINE_ONLY_FIELDS deliberately rather "
            "than leaving a guard over a column that cannot occur."
        )

    def test_the_buyer_still_gets_what_the_buyer_needs(self, market):
        """The other side of the strip: over-filtering would empty the card, and
        an empty product renders as a blank shelf rather than as an error."""
        served = serve_with_real_serializer(market)
        assert served
        for placement in served:
            product = placement["product"]
            for field in ("id", "title", "price_label", "cover_image_url", "category"):
                assert field in product, field
            assert product["title"], product


class TestTheStripIsACopy:
    def test_the_row_still_has_its_columns_after_serialization(self, market):
        """`_buyer_safe` must not mutate: the ranker's signals were already
        computed, but `_payload` reads the row again for the price path and
        `metrics` reads it afterwards."""
        row = {
            "id": 7, "title": "T", "seller_risk_score": 88,
            "candidate_source": "affinity", "price_label": "$1.00",
        }
        safe = engine._buyer_safe(row)
        assert "seller_risk_score" not in safe
        assert row["seller_risk_score"] == 88, "the row was mutated"
        assert row["candidate_source"] == "affinity", "the row was mutated"

    def test_a_none_row_does_not_raise(self):
        assert engine._buyer_safe(None) == {}
