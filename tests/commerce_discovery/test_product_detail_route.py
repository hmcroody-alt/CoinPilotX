"""The two lines of the serve route that decide what a product page asks for.

The rest of the route is covered by the pipeline tests, which go through
``engine.serve``. These two helpers sit *above* the engine, on the path from an
HTTP body to a serve call, and they are the only place on this surface where the
client's description of the world meets the server's:

* :func:`_anchor_listing_id` decides which product is excluded from its own
  recommendations. It takes an integer from the request body on purpose, because
  every use of it narrows the result — there is no value a client can send that
  makes more products come back.

* :func:`_anchor_context` decides what the row is allowed to *claim*. This
  surface labels its cards ``similar_to_this_product``, and a similarity claim is
  only true if the server owns both sides of the comparison. So the anchor's
  taxonomy is read from the database and replaces the body's copy of it outright
  rather than defaulting to it.

The reason this file exists at all is one line inside the second helper::

    values = db_module.row_values(row)

``tuple(row)`` is what that line wants to be, and it would pass every test in
this repository. A Postgres row is a Mapping, so iterating it yields column
*names*: the function would return ``{"category": "category"}`` in production
and the correct value everywhere else — the shape that took comm_v2 down as
issue #25. ``IS_POSTGRES`` is never true under pytest, so no fixture-backed test
can catch a regression here. The Mapping case below is therefore constructed
directly, which is the only way to assert it.
"""

import pytest

from services.db import CompatRow
from services.commerce_discovery_routes import _anchor_context, _anchor_listing_id


class FakeCursor:
    """Answers one SELECT with whatever row shape the case is about."""

    def __init__(self, row):
        self._row = row
        self.queries: list[tuple] = []

    def execute(self, sql, params=()):
        self.queries.append((sql, params))

    def fetchone(self):
        return self._row


class RaisingCursor:
    def execute(self, *_args, **_kwargs):
        raise RuntimeError("connection went away mid-request")

    def fetchone(self):  # pragma: no cover - never reached
        raise AssertionError("execute should have raised first")


class TestWhichProductIsExcluded:
    def test_reads_the_listing_id_the_client_is_looking_at(self):
        assert _anchor_listing_id({"listing_id": 41}) == 41

    def test_accepts_the_id_as_a_string(self):
        # The client sends JSON, but a string here is a wire detail rather than
        # an attack: it still only ever removes a candidate.
        assert _anchor_listing_id({"listing_id": "41"}) == 41

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"listing_id": None},
            {"listing_id": ""},
            {"listing_id": "not-a-number"},
            {"listing_id": []},
            {"listing_id": {"id": 41}},
        ],
    )
    def test_anything_unreadable_means_no_anchor(self, payload):
        # Zero is the "there is no anchor" value, and the caller passes an empty
        # exclusion tuple for it. Raising instead would turn a malformed body
        # into a 500 on a surface whose whole failure posture is to serve an
        # empty row.
        assert _anchor_listing_id(payload) == 0

    def test_a_negative_id_is_not_an_anchor(self):
        # Clamped rather than passed through, so nothing downstream has to
        # decide what excluding listing -1 means.
        assert _anchor_listing_id({"listing_id": -5}) == 0


class TestWhatTheRowIsAllowedToClaim:
    def test_reads_the_anchors_own_taxonomy(self):
        cursor = FakeCursor(("shoes", "sneakers"))
        assert _anchor_context(cursor, 41) == {
            "category": "shoes",
            "subcategory": "sneakers",
            "topic": "",
            "tags": [],
        }

    def test_asks_only_for_the_anchor(self):
        cursor = FakeCursor(("shoes", "sneakers"))
        _anchor_context(cursor, 41)
        sql, params = cursor.queries[0]
        assert "marketplace_listings" in sql
        assert params == (41,)

    def test_a_mapping_row_yields_values_and_not_column_names(self):
        """The Postgres shape, which no fixture in this repository can produce.

        If this line ever goes back to ``tuple(row)`` the returned category
        becomes the literal string ``"category"``, every product page starts
        asking for products similar to a category that does not exist, and the
        row silently claims similarity it cannot have. Locally it would still be
        green, because SQLite hands back a sequence.
        """
        cursor = FakeCursor(CompatRow(["category", "subcategory"], ["shoes", "sneakers"]))
        context = _anchor_context(cursor, 41)
        assert context["category"] == "shoes"
        assert context["subcategory"] == "sneakers"
        assert "category" not in context.values()

    def test_carries_no_context_beyond_the_taxonomy(self):
        # ``topic`` and ``tags`` are the post and reel fields. Left empty rather
        # than absent so the engine sees the same context shape on every surface.
        cursor = FakeCursor(("shoes", "sneakers"))
        context = _anchor_context(cursor, 41)
        assert context["topic"] == ""
        assert context["tags"] == []

    def test_null_columns_become_empty_strings(self):
        cursor = FakeCursor((None, None))
        assert _anchor_context(cursor, 41) == {
            "category": "",
            "subcategory": "",
            "topic": "",
            "tags": [],
        }

    def test_a_long_category_is_truncated(self):
        cursor = FakeCursor(("x" * 500, "y" * 500))
        context = _anchor_context(cursor, 41)
        assert len(context["category"]) == 80
        assert len(context["subcategory"]) == 80

    def test_no_anchor_means_no_query_at_all(self):
        cursor = FakeCursor(("shoes", "sneakers"))
        assert _anchor_context(cursor, 0) == {}
        assert cursor.queries == []

    def test_a_listing_that_no_longer_exists_claims_nothing(self):
        # Empty, not the request body's version. Falling back would let the row
        # keep claiming similarity to a product the server can no longer see.
        assert _anchor_context(FakeCursor(None), 41) == {}

    def test_a_short_row_claims_nothing(self):
        assert _anchor_context(FakeCursor(("shoes",)), 41) == {}

    def test_a_failed_read_claims_nothing_rather_than_raising(self):
        # The serve endpoint answers 200 with an empty row for every failure.
        # An exception here would be the one path that reached the client as a
        # 500 on a product page.
        assert _anchor_context(RaisingCursor(), 41) == {}
