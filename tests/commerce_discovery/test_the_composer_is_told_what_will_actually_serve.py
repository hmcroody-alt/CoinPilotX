"""A tag can succeed and still show nothing. This is the endpoint that says so.

``tagging.attach`` asks one question — do you own this listing — and
``eligibility.gate`` asks a different one at serve time, on every serve, against
the live row. Neither is wrong. Together they leave a gap that nothing in this
repository could previously express: a creator tags a listing with no cover
image, the tag is written, the post publishes cleanly, and the product is never
shown to anybody. No refusal, no log line (``pulse_attach_products_to_content``
logs the *refused* ones and this was not refused), no symptom.

``/taggable-products`` is the only place that difference is expressible, so these
tests are mostly about the difference rather than about the query. The claims
worth holding, in the order they would hurt if they broke:

* **An ineligible listing is returned, with its reason.** Filtering is the obvious
  implementation and it recreates the silence: the listing vanishes from the
  picker and the creator learns nothing. This is the property most likely to be
  "cleaned up" by someone who reads the route as a candidate query.
* **An error is not an empty store.** Every other read in this package answers
  ``200 []`` on failure because an empty carousel is a truthful rendering of
  nothing-to-show. Here the empty list is a claim about the creator's own
  inventory, and a dropped connection must not be allowed to make it.
* **The pipeline columns do not ship** — the reader being the seller does not
  entitle them to ``seller_risk_score``, which is an assessment of them.
* **The cap is served, not mirrored.** A client copy goes stale in the permissive
  direction: the picker allows six, the sixth is refused after publication.

On the serializer used below. It is a pass-through stand-in, for the reason
`test_pipeline_columns_stay_server_side.py` sets out at length: ``conftest``'s
fixture passes no ``serialize`` and `_payload`'s fallback is an *allowlist*, which
is the one shape that cannot leak and the one production does not have. A test of
a leak driven through an allowlist proves nothing. That file separately pins that
the real serializer is still a denylist, so this one does not restate it.
"""

from __future__ import annotations

import pytest
from flask import Flask

from services import commerce_discovery_routes as routes
from services.commerce_discovery import eligibility, engine, tagging

SELLER = 7
OTHER_SELLER = 8


def pass_through_serializer(listing):
    """Unknown keys survive, which is the only property that matters here.

    Deliberately not imported from `test_pipeline_columns_stay_server_side.py`.
    Two test files sharing a stand-in makes one of them fail for a reason
    belonging to the other, and this one is not making a claim about the real
    serializer's shape — that file is.
    """
    return dict(listing or {})


def listing_row(listing_id, **overrides):
    """A row shaped like `eligibility.candidate_projection()` returns.

    Eligible by default, so a test that wants a *blocked* row says which column
    blocks it and nothing else. The alternative — a minimal row plus whatever each
    test adds — makes every test silently depend on `gate`'s evaluation order.
    """
    row = {
        "id": listing_id,
        "seller_user_id": SELLER,
        "title": f"Listing {listing_id}",
        "short_description": "",
        "description": "",
        "category": "electronics",
        "subcategory": "",
        "price_label": "$40.00",
        "currency": "USD",
        "quantity": 3,
        "product_type": "physical",
        "listing_type": "fixed",
        "listing_metadata_json": "",
        "cover_image_url": "/static/uploads/cover.jpg",
        "gallery_json": "",
        "video_url": "",
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-20T00:00:00Z",
        "featured": 0,
        "delivery_type": "shipping",
        "status": "active",
        "approval_status": "approved",
        "safety_score": 0,
        "moderation_reason": "",
        "seller_status": "approved",
        "seller_store_name": "Seller Seven",
        "seller_business_name": "Seller Seven Ltd",
        "seller_verification_status": "verified",
        "seller_risk_score": 0,
        "seller_created_at": "2026-01-01T00:00:00Z",
        "seller_username": "seven",
    }
    row.update(overrides)
    return row


class _Cursor:
    """Returns the rows it was given and remembers what it was asked.

    The SQL is recorded because one of the claims below is about the ``WHERE``
    clause rather than about the response: a route that returned the right rows
    from a query with no ownership filter would pass every behavioural test here
    while serving one seller's inventory to another. Reading back the parameters
    is how that is checked without a database.
    """

    def __init__(self, rows=(), raises=None):
        self.rows = list(rows)
        self.raises = raises
        self.statements: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params or ())))
        if self.raises is not None:
            raise self.raises

    def fetchall(self):
        return list(self.rows)


class _StubBot:
    """Only the three attributes the handler reaches for."""

    PULSE_PRODUCT_TAG_REQUEST_LIMIT = 20

    @staticmethod
    def parse_price_label_to_cents(label, currency="USD"):
        text = str(label or "").replace("$", "").replace(",", "").strip()
        if not text:
            raise ValueError("no price")
        return int(round(float(text) * 100)), currency or "USD"

    pulse_marketplace_listing_payload = staticmethod(pass_through_serializer)


@pytest.fixture
def api(monkeypatch):
    """``(client, cursor_holder)`` — the real blueprint over a fake cursor."""
    holder: dict = {"cursor": _Cursor()}
    monkeypatch.setattr(routes, "_bot", lambda: _StubBot())
    monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": SELLER}, None))
    monkeypatch.setattr(routes, "_with_db", lambda handler: handler(holder["cursor"], None))
    app = Flask(__name__)
    app.register_blueprint(routes.discovery_blueprint)
    return app.test_client(), holder


def get(client, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return client.get(f"{routes.API_PREFIX}/taggable-products" + (f"?{query}" if query else ""))


class TestAnIneligibleListingIsShownWithItsReason:
    """The property the endpoint exists for."""

    def test_a_listing_with_no_cover_image_is_returned_not_filtered(self, api):
        client, holder = api
        holder["cursor"] = _Cursor([listing_row(11, cover_image_url="")])
        body = get(client).get_json()

        assert [item["listing_id"] for item in body["products"]] == [11], (
            "the listing was dropped from the response. A creator whose product is "
            "missing a photo then sees an empty picker and has no way to learn that "
            "adding one is the whole fix — which is the silence this endpoint exists "
            "to break, not to reproduce."
        )
        assert body["products"][0]["serves"] is False
        assert body["products"][0]["blocked_reason"] == "no_cover_image"

    def test_an_eligible_listing_says_it_serves(self, api):
        client, holder = api
        holder["cursor"] = _Cursor([listing_row(12)])
        product = get(client).get_json()["products"][0]

        assert product["serves"] is True
        assert product["blocked_reason"] == ""

    @pytest.mark.parametrize(
        "code,overrides",
        [
            ("not_purchasable", {"status": "draft"}),
            ("no_cover_image", {"cover_image_url": ""}),
            ("no_resolvable_price", {"price_label": "contact seller"}),
            ("moderation_flagged", {"moderation_reason": "reported by a buyer"}),
            ("listing_risk", {"safety_score": 90}),
            ("seller_risk", {"seller_risk_score": 95}),
        ],
    )
    def test_each_reason_reaches_the_creator_verbatim(self, api, code, overrides):
        """`eligibility`'s code, not a boolean and not a re-worded copy.

        "Add a cover photo" and "a moderator flagged this" are the same boolean and
        different instructions, and a translation layer here would be a second
        vocabulary to keep in step with `INELIGIBLE_CODES`.
        """
        client, holder = api
        holder["cursor"] = _Cursor([listing_row(13, **overrides)])
        product = get(client).get_json()["products"][0]

        assert product["blocked_reason"] == code
        assert product["serves"] is False

    def test_every_reason_this_route_can_emit_is_one_eligibility_declares(self, api):
        """No code invented here.

        `eligibility.INELIGIBLE_CODES` is the declared vocabulary and two of its
        entries (`suppressed`, `over_exposed`) are per-viewer facts this route
        cannot produce — so the assertion is containment, not equality.
        """
        client, holder = api
        holder["cursor"] = _Cursor([
            listing_row(1, status="draft"),
            listing_row(2, cover_image_url=""),
            listing_row(3, price_label=""),
            listing_row(4, moderation_reason="held"),
            listing_row(5, safety_score=90),
            listing_row(6, seller_risk_score=95),
            listing_row(7),
        ])
        products = get(client).get_json()["products"]

        emitted = {item["blocked_reason"] for item in products} - {""}
        assert emitted <= set(eligibility.INELIGIBLE_CODES), (
            f"codes not in eligibility.INELIGIBLE_CODES: "
            f"{sorted(emitted - set(eligibility.INELIGIBLE_CODES))}"
        )
        assert emitted, "no row was blocked, so this asserted nothing"


class TestAFailureIsNotAnEmptyStore:
    """Error and empty must never co-render, and here they would say opposite things."""

    def test_a_database_error_is_a_500_with_a_code(self, api):
        client, holder = api
        holder["cursor"] = _Cursor(raises=RuntimeError("no such column: l.taggable"))
        response = get(client)

        assert response.status_code == 500, (
            "a read failure answered 200. Every other read in this package is right "
            "to do that — an empty carousel is a truthful rendering of nothing to "
            "show. This list is a claim about the creator's own inventory, and "
            "'you have no products' is not something a dropped connection may say."
        )
        body = response.get_json()
        assert body["ok"] is False
        assert body["error_code"] == "TAGGABLE_PRODUCTS_UNAVAILABLE"
        assert "products" not in body, (
            "the failure response carries a products key, so a client reading it "
            "without checking `ok` renders an empty store for a database error"
        )

    def test_the_error_does_not_repeat_the_database_message(self, api):
        """A column name is schema, and the creator is not the audience for it."""
        client, holder = api
        holder["cursor"] = _Cursor(raises=RuntimeError("no such column: ms.internal_risk_note"))
        body = get(client).get_data(as_text=True)

        assert "internal_risk_note" not in body
        assert "no such column" not in body

    def test_a_genuinely_empty_store_is_an_empty_list_and_ok(self, api):
        """The other half. Without this the test above passes on a route that 500s always."""
        client, holder = api
        holder["cursor"] = _Cursor([])
        response = get(client)

        assert response.status_code == 200
        assert response.get_json() == {
            "ok": True, "products": [],
            "max_per_content": tagging.MAX_TAGGED_PER_CONTENT,
            "request_limit": _StubBot.PULSE_PRODUCT_TAG_REQUEST_LIMIT,
        }


class TestThePipelineColumnsStillDoNotShip:
    """Being the seller is not an entitlement to the assessment *of* the seller."""

    @pytest.mark.parametrize("field", sorted(engine.PIPELINE_ONLY_FIELDS))
    def test_no_pipeline_column_reaches_the_response(self, api, field):
        client, holder = api
        row = listing_row(21)
        # Present on the row regardless of whether this pipeline's SELECT happens
        # to carry it today, so the assertion is about the strip and not about the
        # projection. `relationship` is not in `candidate_projection()` and is in
        # `PIPELINE_ONLY_FIELDS` precisely as belt and braces.
        row[field] = "leaked"
        holder["cursor"] = _Cursor([row])
        body = get(client).get_data(as_text=True)

        assert "leaked" not in body, f"{field} reached the response"
        assert field not in (get(client).get_json()["products"][0]["product"] or {})

    def test_the_strip_is_the_engine_s_own(self, api):
        """Not a second copy of the list.

        A copy is how the original leak happened: the marketplace serializer's
        denylist did not know about this package's columns. A second denylist here
        would have the same relationship to `PIPELINE_ONLY_FIELDS` that that one
        had to `eligibility`'s SELECT.
        """
        import inspect

        source = inspect.getsource(routes.commerce_discovery_taggable_products)
        assert "engine.buyer_safe(" in source, (
            "the handler no longer calls `engine.buyer_safe`. If the strip was "
            "inlined, `PIPELINE_ONLY_FIELDS` now has two readers that can disagree."
        )


class TestOnlyTheCreatorsOwnListings:
    def test_the_query_filters_by_the_signed_in_user(self, api):
        """Behavioural tests cannot see this, and it is the security property.

        Every row the fake cursor returns comes back whatever the ``WHERE`` said,
        so a route that dropped the ownership filter would pass every other test
        in this file while serving one seller's inventory to another.
        """
        client, holder = api
        cursor = _Cursor([listing_row(31)])
        holder["cursor"] = cursor
        get(client)

        sql, params = cursor.statements[0]
        assert "l.seller_user_id" in sql and "=?" in sql.replace(" ", "")
        assert SELLER in params, (
            f"the signed-in user id is not a query parameter: {params!r}. "
            "The rows this route returns are the ones a creator may tag, and "
            "`tagging.attach` re-checks ownership per row — but a picker offering "
            "another seller's products is a disclosure of their catalogue "
            "regardless of what the write path then refuses."
        )
        assert OTHER_SELLER not in params

    def test_the_projection_is_eligibility_s_own(self, api):
        """`gate` passes several unprojected columns by default.

        A hand-trimmed SELECT would therefore report a listing as servable that
        the serve path drops — the gate would be answering about columns that are
        absent rather than clean. Pinned by comparing against
        `candidate_projection()` rather than by naming columns.
        """
        client, holder = api
        cursor = _Cursor([listing_row(32)])
        holder["cursor"] = cursor
        get(client)

        sql, _ = cursor.statements[0]
        assert eligibility.candidate_projection() in sql


class TestTheCapIsServedRatherThanMirrored:
    def test_max_per_content_is_the_write_path_s_constant(self, api):
        client, holder = api
        holder["cursor"] = _Cursor([listing_row(41)])
        body = get(client).get_json()

        assert body["max_per_content"] == tagging.MAX_TAGGED_PER_CONTENT
        # Derived, not the literal 5. A test that spelled the number would keep
        # passing after the constant changed and the route kept sending the old
        # one, which is the exact failure a served cap is meant to prevent.
        assert body["request_limit"] == _StubBot.PULSE_PRODUCT_TAG_REQUEST_LIMIT

    def test_the_cap_tracks_the_constant_rather_than_equalling_it_today(
        self, api, monkeypatch,
    ):
        """The test above cannot tell `tagging.MAX_TAGGED_PER_CONTENT` from `5`.

        Written down because that is not a hypothetical: replacing the reference
        with the literal survived every other test in this file. Both sides of
        ``==`` were the same number, so the assertion held on a route that had
        stopped reading the constant — the shape this repository has hit before,
        where a fixture supplies both the value and the threshold it is checked
        against. Moving the constant is the only way to see the difference.
        """
        client, holder = api
        monkeypatch.setattr(tagging, "MAX_TAGGED_PER_CONTENT", 3)
        holder["cursor"] = _Cursor([listing_row(43)])

        assert get(client).get_json()["max_per_content"] == 3, (
            "the route reported the old cap after the constant moved, so it is "
            "sending a number of its own rather than the write path's"
        )

    def test_the_page_bound_is_not_the_per_content_cap(self, api):
        """Two different numbers that a reader will want to conflate.

        `TAGGABLE_PAGE_MAX` bounds one response; `MAX_TAGGED_PER_CONTENT` bounds
        what one post may carry. Returning five products because a post may carry
        five would make the picker unable to *offer* a choice.
        """
        assert routes.TAGGABLE_PAGE_MAX > tagging.MAX_TAGGED_PER_CONTENT

    @pytest.mark.parametrize("requested,expected", [
        ("0", 1), ("1", 1), ("7", 7), ("999", routes.TAGGABLE_PAGE_MAX),
        ("-4", 1), ("abc", routes.TAGGABLE_PAGE_MAX), ("", routes.TAGGABLE_PAGE_MAX),
    ])
    def test_the_client_cannot_widen_the_page(self, api, requested, expected):
        client, holder = api
        cursor = _Cursor([listing_row(42)])
        holder["cursor"] = cursor
        get(client, limit=requested)

        _, params = cursor.statements[0]
        assert params[-1] == expected


class TestARowTheSerializerCannotRenderIsSkipped:
    def test_a_serializer_failure_drops_that_row_and_keeps_the_others(
        self, api, monkeypatch, caplog,
    ):
        """A selectable blank is worse than an absence.

        The row is dropped rather than emitted with an empty ``product``, because
        the picker would draw a tappable card with no title and no price, and a
        creator selecting it would tag a product they could not identify.
        """
        client, holder = api

        def explodes_on_one(listing):
            if int(dict(listing).get("id") or 0) == 52:
                raise RuntimeError("gallery_json is not JSON")
            return dict(listing)

        holder["cursor"] = _Cursor([listing_row(51), listing_row(52), listing_row(53)])
        # On the *instance*, so the class stays clean for every other test in the
        # file. `monkeypatch` is function-scoped and therefore the same object the
        # `api` fixture used, so this replacement is undone with the rest.
        bot = _StubBot()
        bot.pulse_marketplace_listing_payload = explodes_on_one
        monkeypatch.setattr(routes, "_bot", lambda: bot)

        with caplog.at_level("WARNING"):
            body = get(client).get_json()

        assert [item["listing_id"] for item in body["products"]] == [51, 53]
        assert "COMMERCE_DISCOVERY_TAGGABLE_SERIALIZE_FAILED" in caplog.text, (
            "the row vanished silently. This is the one failure here that an "
            "operator has to be able to find: the creator sees a product missing "
            "from their own picker and nothing explains it."
        )


class TestTheEndpointRequiresASignedInCreator:
    def test_a_signed_out_request_is_401(self, monkeypatch):
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot())
        monkeypatch.setattr(
            routes, "_require_user",
            lambda: (None, routes._error("Login required.", 401, code="LOGIN_REQUIRED")),
        )
        app = Flask(__name__)
        app.register_blueprint(routes.discovery_blueprint)
        response = get(app.test_client())

        assert response.status_code == 401
        assert response.get_json()["error_code"] == "LOGIN_REQUIRED"

    def test_no_database_connection_is_opened_before_the_auth_check(self, monkeypatch):
        """Ordering, not just the status code.

        A handler that opened a connection and then returned 401 would spend one of
        eight pooled connections per unauthenticated request, which is a denial of
        service against every other feature sharing the pool rather than against
        this one.
        """
        opened = []
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot())
        monkeypatch.setattr(
            routes, "_require_user",
            lambda: (None, routes._error("Login required.", 401, code="LOGIN_REQUIRED")),
        )
        monkeypatch.setattr(routes, "_with_db", lambda handler: opened.append(handler))
        app = Flask(__name__)
        app.register_blueprint(routes.discovery_blueprint)
        get(app.test_client())

        assert opened == []
