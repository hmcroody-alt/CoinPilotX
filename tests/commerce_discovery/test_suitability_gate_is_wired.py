"""The suitability gate is reached by a real HTTP request, and refuses first.

``test_content_suitability.py`` proves the rule is *correct*. This file proves it
is *connected*, which is a different failure and the one this repository has been
bitten by: a green suite and a shipped build where the code was never on the
path. A module nothing imports refuses nothing.

So every test here goes in through the Flask route rather than calling
``suitability`` directly, and the central assertion is not about the response
body — it is that ``engine.serve`` was **never called**. That is the mechanical
difference between a refusal and a low score. A gate implemented as a weight, or
checked after ranking, would have built the candidate pool, written the exposure
ledger and minted an impression token before deciding; the response would look
identical and the cost would already be paid. ``serve`` not being called is the
only observable that distinguishes them.

Why the route is driven with stubs instead of the ``market`` fixture
-------------------------------------------------------------------
``market`` drives ``engine.serve`` directly and therefore sits *below* the thing
under test. The route's helpers are stubbed one level up — ``_require_user``,
``_with_db``, ``_bot``, ``_rate_limited`` — so no database is touched and the
only real code between the request body and the assertion is the handler body
itself. That is deliberate: if this test needed a seeded catalogue to pass, it
would also pass with the gate deleted, because an empty catalogue returns no
placements for entirely unrelated reasons.
"""

import pytest
from flask import Flask

from services import commerce_discovery_routes as routes
from services.commerce_discovery import engine, suitability


#: The two posts measured against the shipped engine, quoted from the report.
#: The second is the one that matters: expressing grief *through an object* made
#: the sensitive post match better, so it cleared every floor in the system,
#: including reels' 0.55 — the strictest. Any gate that is a score penalty rather
#: than a refusal is defeated by exactly this input.
GRIEF = {"topic": "we lost my father this morning, rest in peace dad",
         "tags": ["memorial", "grief"]}
GRIEF_THROUGH_AN_OBJECT = {"topic": "wearing dad's old watch to the funeral today, miss you",
                           "tags": ["memorial", "watches"]}
BENIGN = {"topic": "finally found the perfect running shoes for marathon training",
          "tags": ["running", "sneakers", "marathon"]}


class ServeSpy:
    """Records whether retrieval happened, and what it was asked for."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, cur, user_id, surface, **kwargs):
        self.calls.append({"surface": surface, "context": kwargs.get("context")})
        return [{"placement_id": "p1", "listing_id": 7}]

    @property
    def called(self) -> bool:
        return bool(self.calls)


class StubBot:
    """Only the two callables the handler passes down to the engine."""

    def parse_price_label_to_cents(self, *_args, **_kwargs):  # pragma: no cover
        return 0

    def pulse_marketplace_listing_payload(self, *_args, **_kwargs):  # pragma: no cover
        return {}

    def db(self):  # pragma: no cover - `_with_db` is stubbed out
        raise AssertionError("no test here should open a database connection")


@pytest.fixture
def serve(monkeypatch) -> ServeSpy:
    spy = ServeSpy()
    monkeypatch.setattr(engine, "serve", spy)
    return spy


@pytest.fixture
def client(monkeypatch, serve):
    monkeypatch.setattr(routes, "_bot", lambda: StubBot())
    monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": 9001}, None))
    # Not a shortcut: the limiter is in-process and shared with every other test
    # in the session, so leaving it live would make these results depend on
    # execution order.
    monkeypatch.setattr(routes, "_rate_limited", lambda _ref: False)
    # `handler(cur, conn)` with neither. Nothing on the path under test reads a
    # cursor — `_anchor_context` does, and product_detail is asserted separately
    # to never reach the gate at all.
    monkeypatch.setattr(routes, "_with_db", lambda handler: handler(None, None))

    app = Flask(__name__)
    app.register_blueprint(routes.discovery_blueprint)
    return app.test_client()


def ask(client, surface: str, context=None, **extra):
    body = {"session_id": "s-1", **extra}
    if context is not None:
        body["context"] = context
    return client.post(f"{routes.API_PREFIX}/{surface}", json=body)


class TestTheGateIsOnTheRequestPath:
    """The module is imported, called, and reached by an HTTP body."""

    def test_the_route_imports_the_gate(self):
        # The cheapest possible regression check, and the one that would have
        # caught this module being dead code for as long as it was.
        assert routes.suitability is suitability

    @pytest.mark.parametrize("surface", sorted(suitability.CONTENT_SURFACES - {"post_detail"}))
    def test_a_sensitive_context_is_refused_before_retrieval(self, client, serve, surface):
        response = ask(client, surface, GRIEF)

        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "placements": []}
        assert not serve.called, (
            "the candidate pool was built for a post commerce must stay away "
            "from; a refusal that runs after retrieval has already written the "
            "exposure ledger and minted an impression token"
        )

    @pytest.mark.parametrize("surface", sorted(suitability.CONTENT_SURFACES - {"post_detail"}))
    def test_grief_expressed_through_an_object_is_refused_too(self, client, serve, surface):
        # The measured regression. This context out-scores the plain-grief one
        # because a watch matches watches, so every floor clears and a
        # score-based defence admits it. Ranking is never consulted.
        response = ask(client, surface, GRIEF_THROUGH_AN_OBJECT)

        assert response.get_json()["placements"] == []
        assert not serve.called

    def test_post_detail_is_gated_on_its_own_body(self, client, serve):
        # Split out from the parametrised cases above only because a
        # `post_detail` request carries no `listing_id`, so it must not be
        # mistaken for the anchor path.
        assert ask(client, "post_detail", GRIEF).get_json()["placements"] == []
        assert not serve.called


class TestTheGateDoesNotSwallowOrdinaryCommerce:
    """A suppression nobody can distinguish from an outage is not a feature."""

    @pytest.mark.parametrize("surface", sorted(suitability.CONTENT_SURFACES))
    def test_a_benign_context_still_retrieves(self, client, serve, surface):
        response = ask(client, surface, BENIGN)

        assert response.get_json()["placements"], (
            f"{surface} returned nothing for a running-shoes post; the gate is "
            f"refusing content it has no evidence about"
        )
        assert serve.calls[0]["surface"] == surface

    def test_the_context_reaches_the_engine_unmodified(self, client, serve):
        # The gate reads the context; it must not rewrite it. A gate that
        # stripped a field on the way through would silently degrade relevance
        # on every permitted request, which no assertion about placements counts
        # would notice.
        ask(client, "reels", BENIGN)

        passed = serve.calls[0]["context"]
        assert passed.get("topic") == BENIGN["topic"]
        assert list(passed.get("tags") or []) == BENIGN["tags"]


class TestTheSurfacesThatAreNotAssessed:
    """Three surfaces are exempt, each for a different and separate reason."""

    @pytest.mark.parametrize("surface", ["marketplace", "messenger"])
    def test_surfaces_with_no_surrounding_content_are_not_assessed(
        self, client, serve, surface
    ):
        # Neither has content to be unsuitable: Marketplace's shelves sit in a
        # shop, and the messenger strip must never have a conversation derived
        # into a context at all. Sending one anyway — which no client does —
        # must not start assessing private messages for grief.
        response = ask(client, surface, GRIEF)

        assert serve.called, (
            f"{surface} was refused on the strength of a context it has no "
            f"business sending; this gate is about a claim made over a post"
        )
        assert response.get_json()["placements"]

    def test_product_detail_never_consults_the_gate(self, client, serve, monkeypatch):
        # Its context is rebuilt server-side from the anchor listing, so by the
        # time the gate could run there is nothing of the client's left to judge
        # — and what replaced it describes a product in our own catalogue, which
        # is `eligibility.py`'s question and not this module's.
        monkeypatch.setattr(routes, "_anchor_context", lambda _cur, _id: {"category": "watches"})

        def explode(_context):  # pragma: no cover - the assertion is that it is not called
            raise AssertionError("product_detail must not be content-assessed")

        monkeypatch.setattr(suitability, "assess_context", explode)

        assert ask(client, "product_detail", GRIEF, listing_id=41).status_code == 200
        assert serve.calls[0]["context"] == {"category": "watches"}

    def test_the_assessed_surfaces_are_real_surfaces(self):
        # `CONTENT_SURFACES` is written by hand next to the rule rather than
        # derived from `schema.SURFACES`, so a typo in it would disable the gate
        # on a live surface in total silence — `surface in CONTENT_SURFACES` is
        # simply False and the request is served.
        from services.commerce_discovery import schema

        assert suitability.CONTENT_SURFACES <= set(schema.SURFACES)


class TestOnlySensitivityRefusesOnThisPath:
    """``NO_SUBJECT`` is answered and deliberately not acted on here.

    This is the narrowest the gate could be and still do its job, and the
    narrowness caught a regression that had already been written: refusing every
    verdict that was not ``PERMITTED`` looks obviously safer and is wrong.
    ``_context_from_request`` returns all four keys whenever the body carries a
    ``context`` object at all, so the "describes nothing" verdict lands on real,
    well-formed requests — and a client's omission of a caption is not evidence
    about the content it omitted.
    """

    def test_a_reel_with_only_a_category_is_still_served(self, client, serve):
        # The case that caught it. `reelContext.ts:125-131` returns a bare
        # `{category}` by design for a reel with no caption and no tags, and a
        # category is the single strongest signal `relevance` has. Treating that
        # as "no subject" would have switched commerce off for that whole
        # population of reels to protect nobody.
        response = ask(client, "reels", {"category": "watches"})

        assert response.get_json()["placements"], (
            "a reel whose context is a category was refused; that is a ranking "
            "signal, not an absence of one"
        )
        assert serve.called

    @pytest.mark.parametrize("thin", [None, {}, {"topic": "", "tags": []}])
    @pytest.mark.parametrize("surface", sorted(suitability.CONTENT_SURFACES))
    def test_a_request_describing_nothing_is_served_as_it_is_today(
        self, client, serve, surface, thin
    ):
        # The rule and the wire disagree here on purpose, and this records which
        # one the route follows. Acting on NO_SUBJECT would switch feed commerce
        # off outright — a product decision nobody made and no measurement
        # supports. The day someone closes it properly, this test fails and
        # points at the decision rather than letting it happen silently.
        assert ask(client, surface, thin).get_json()["placements"]
        assert serve.called

    @pytest.mark.parametrize("thin", [{}, {"topic": "", "tags": []}])
    def test_the_rule_itself_does_call_that_no_subject(self, thin):
        # Asserted separately so the exemption cannot be mistaken for the rule
        # agreeing. `assess`, which derives its own context and can therefore
        # treat an empty one as evidence, is the path that acts on this.
        assert suitability.assess_context(thin)["code"] == suitability.NO_SUBJECT

    def test_a_client_supplied_taxonomy_field_is_still_read(self, client, serve):
        # A legitimate client fills `category`/`subcategory` from our own tree,
        # where nothing sensitive lives. But on this path they are client-supplied
        # strings and this is the only check that sees the wire, so they are
        # scanned: a bereavement smuggled into a subcategory by a bug or on
        # purpose must not pass unread.
        response = ask(client, "reels",
                       {"category": "watches", "subcategory": "rest in peace dad"})

        assert response.get_json()["placements"] == []
        assert not serve.called


class TestTheRefusalIsInvisibleAndUnrevealing:
    """What a refused viewer is told, and what the logs are told."""

    def test_a_refusal_is_shaped_exactly_like_an_empty_result(self, client):
        # The client has one rendering path and no error branch. A distinct
        # status or an explanatory field would both break that and tell the
        # viewer of a bereavement that commerce was considered and declined.
        refused = ask(client, "reels", GRIEF)
        assert refused.status_code == 200
        assert refused.get_json() == {"ok": True, "placements": []}

    def test_the_log_line_carries_no_content(self, client, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger=routes.LOGGER.name):
            ask(client, "reels", GRIEF)

        records = [r for r in caplog.records
                   if "COMMERCE_DISCOVERY_SUITABILITY_REFUSED" in r.getMessage()]
        assert len(records) == 1, "a refusal that logs nothing cannot be audited"

        line = records[0].getMessage()
        assert suitability.SENSITIVE_CONTEXT in line
        assert "memorial" in line or "grief" in line, "the category is the auditable part"
        # The post's own words are not ours to put in a log line, and a log that
        # quotes the matched text turns every refusal into a copy of the content
        # it was protecting.
        assert "father" not in line
        assert "rest in peace" not in line
