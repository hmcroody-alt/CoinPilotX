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


#: The measured truncation, quoted in full. Every word the rule needs is past
#: `postContext.ts`'s 80-character cut, and nothing about the shape is contrived —
#: a bereavement post that opens by thanking people is the ordinary case.
BEREAVEMENT_BODY = (
    "Thank you all so much for the kind words these past few days, it has meant "
    "more to us than I can say. We lost my father on Tuesday morning. Rest in "
    "peace dad."
)
#: What `postCommerceContext` puts on the wire for that post: `trimmed(body, 80)`.
BEREAVEMENT_AS_TRUNCATED = {
    "topic": BEREAVEMENT_BODY[:80],
    "tags": [],
}


class PostRows:
    """A stand-in `cur` holding `pulse_posts` rows, keyed by id.

    A cursor rather than a monkeypatch of `_content_post`, so the SQL, the column
    list and the tuple-vs-mapping unpacking are all on the path. `execute` records
    the statement it was given, which is how the `deleted_at` filter and the
    absence of `SELECT *` are asserted rather than assumed.
    """

    def __init__(self, rows: dict[int, dict] | None = None, *, fail: bool = False):
        self.rows = rows or {}
        self.fail = fail
        self.statements: list[str] = []
        self._pending = None

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if self.fail:
            raise RuntimeError("the database is having a bad minute")
        if "pulse_posts" not in sql:  # pragma: no cover - no other read on this path
            raise AssertionError(f"unexpected query on this path: {sql}")
        row = self.rows.get(int(params[0]))
        # Column order follows the module's own list, which is what the real
        # query's projection order is. Returned as a tuple: this is the SQLite
        # shape, and `row_values` is what makes the Postgres shape work too.
        self._pending = None if row is None else tuple(
            row.get(name) for name in routes._POST_COLUMNS
        )

    def fetchone(self):
        return self._pending


def post_row(**fields) -> dict:
    """A cleared, unremarkable post, overridden field by field.

    Explicit rather than defaulted inside `PostRows`, because "cleared moderation"
    is the state that lets the row *pass*, and a helper that supplied it silently
    would make every test here depend on a default it never states.
    """
    base = {"post_type": "post", "moderation_status": "approved", "risk_score": 0,
            "title": "", "body": "", "ai_summary": "",
            "tags_json": "[]", "ai_tags_json": "[]"}
    base.update(fields)
    return base


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
def rows() -> PostRows:
    """The `pulse_posts` table this module's requests read. Empty by default.

    Empty is the honest default: a request that names no post never reaches it,
    and a request that names one the table does not hold is a real case with its
    own test.
    """
    return PostRows()


@pytest.fixture
def client(monkeypatch, serve, rows):
    monkeypatch.setattr(routes, "_bot", lambda: StubBot())
    monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": 9001}, None))
    # Not a shortcut: the limiter is in-process and shared with every other test
    # in the session, so leaving it live would make these results depend on
    # execution order.
    monkeypatch.setattr(routes, "_rate_limited", lambda _ref: False)
    # `handler(cur, conn)` with a cursor that answers one query and no connection.
    # Still no database: `PostRows` is a dict with an `execute`, so the SQL string
    # and the row unpacking are exercised while nothing is opened.
    monkeypatch.setattr(routes, "_with_db", lambda handler: handler(rows, None))

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


class TestTheGateReadsThePostAndNotItsSummary:
    """The hole the wire check cannot cover, and the id that closes it.

    ``postContext.ts`` caps every field it sends at ``MAX_FIELD_CHARS = 80`` and
    derives ``topic`` from the post's title or, failing that, its body. That cap is
    correct — an unbounded free-text field arriving from a client has no business
    being unbounded — and it means the gate was judging a *summary* of the post.

    A bereavement post that opens by thanking people for their kindness is the
    ordinary shape of a bereavement post, not a contrived one, and its first
    eighty characters contain nothing for the rule to find. Every test below
    carries its own control: the identical request without the id, so what is
    being measured is the id and not the fixture.
    """

    def test_the_truncated_wire_value_alone_is_permitted(self):
        # The premise, asserted against the rule directly so the rest of the class
        # cannot be passing for some unrelated reason. If this ever fails because
        # the rule learned to read a thank-you preamble, these tests should be
        # re-read rather than deleted — the id is still the trustworthy path.
        assert suitability.assess_context(BEREAVEMENT_AS_TRUNCATED)["code"] == (
            suitability.PERMITTED
        )

    def test_without_the_id_the_bereavement_is_served(self, client, serve):
        # The control, and the bug as it shipped. Stated as a passing assertion
        # rather than left implicit, because "the gate now refuses this" means
        # nothing unless the same request used to be served.
        assert ask(client, "post_detail", BEREAVEMENT_AS_TRUNCATED).get_json()["placements"]
        assert serve.called

    def test_with_the_id_the_full_body_refuses_before_retrieval(self, client, serve, rows):
        rows.rows[77] = post_row(body=BEREAVEMENT_BODY)

        response = ask(client, "post_detail", BEREAVEMENT_AS_TRUNCATED, post_id=77)

        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "placements": []}
        assert not serve.called, (
            "the candidate pool was built beside a bereavement; a refusal that "
            "runs after retrieval has already written the exposure ledger and "
            "minted an impression token"
        )

    def test_the_refusal_is_reported_as_what_it_is(self, client, caplog, rows):
        import logging

        rows.rows[77] = post_row(body=BEREAVEMENT_BODY)
        with caplog.at_level(logging.INFO, logger=routes.LOGGER.name):
            ask(client, "post_detail", BEREAVEMENT_AS_TRUNCATED, post_id=77)

        lines = [r.getMessage() for r in caplog.records
                 if "COMMERCE_DISCOVERY_SUITABILITY_REFUSED" in r.getMessage()]
        assert len(lines) == 1
        assert suitability.SENSITIVE_CONTEXT in lines[0]
        assert "grief" in lines[0], "the category is the auditable part"
        # The body is the whole reason this path exists, so it is also the thing
        # most likely to end up in the log line by accident.
        assert "father" not in lines[0]
        assert "Thank you" not in lines[0]

    @pytest.mark.parametrize("column,value", [
        ("body", BEREAVEMENT_BODY),
        ("title", BEREAVEMENT_BODY),
        ("ai_summary", BEREAVEMENT_BODY),
        ("tags_json", '["memorial"]'),
        ("ai_tags_json", '["memorial"]'),
    ])
    def test_each_text_column_named_in_the_query_is_actually_read(
        self, client, serve, rows, column, value
    ):
        # `_POST_COLUMNS` is a hand-written projection, so a column could be
        # selected and never looked at — or, worse, looked at under a name the
        # query does not select, which reads as a clean post forever. One case per
        # column, each with only that column set, is the only way to tell.
        rows.rows[5] = post_row(**{column: value})

        assert ask(client, "post_detail", None, post_id=5).get_json()["placements"] == []
        assert not serve.called, f"{column} is selected but not assessed"

    @pytest.mark.parametrize("column,value", [
        ("post_type", "memorial"),
        ("moderation_status", "pending"),
        ("moderation_status", "a state nobody has heard of"),
        ("risk_score", 90),
    ])
    def test_the_structural_gates_are_invisible_to_the_wire_and_live_here(
        self, client, serve, rows, column, value
    ):
        # None of these three is a field a client sends, or should be trusted to
        # send. Before the id they were unreachable from this surface: a
        # `memorial`, an unmoderated post and a high-risk post were all served.
        rows.rows[5] = post_row(**{column: value})

        assert ask(client, "post_detail", BENIGN, post_id=5).get_json()["placements"] == []
        assert not serve.called

    def test_a_cleared_benign_post_is_still_served(self, client, serve, rows):
        # A suppression nobody can distinguish from an outage is not a feature.
        rows.rows[5] = post_row(body="finally found the perfect running shoes")

        assert ask(client, "post_detail", BENIGN, post_id=5).get_json()["placements"]
        assert serve.calls[0]["surface"] == "post_detail"

    @pytest.mark.parametrize("thin", [None, {}, {"topic": "", "tags": []}])
    def test_a_captionless_post_is_served_on_the_strength_of_its_row(
        self, client, serve, rows, thin
    ):
        """The row is read to answer *adjacency*, never *subject*.

        The distinction that makes this whole change safe, and the one a later
        tidy-up is most likely to erase: ``assess`` looks like the more thorough
        entry point and swapping it in here compiles, passes a casual read, and
        switches commerce off for every post with no readable text — a photo with
        no caption is the single commonest post on the platform. ``postContext.ts``
        returns null for exactly those posts, so the request arrives with a row and
        no context, which is the combination nothing else in this file covers.

        ``suitability.assess_adjacency`` and not ``suitability.assess``, the same
        choice ``suitability.annotate`` makes for the feed, for the reason that
        function's docstring gives: a client's silence about a post is not evidence
        about the post.
        """
        rows.rows[5] = post_row(body="")

        assert ask(client, "post_detail", thin, post_id=5).get_json()["placements"], (
            "a post with nothing to say was refused because it said nothing; "
            "NO_SUBJECT is not an adjacency verdict"
        )
        assert serve.called

    def test_the_context_still_reaches_the_engine_unmodified(self, client, serve, rows):
        # The row read must not become a source of ranking signal. It answers one
        # yes/no question; the context is what the ranker scores against, and a
        # row that quietly overwrote a field here would change relevance on every
        # permitted request with nothing failing.
        rows.rows[5] = post_row(body="running shoes")
        ask(client, "post_detail", BENIGN, post_id=5)

        passed = serve.calls[0]["context"]
        assert passed.get("topic") == BENIGN["topic"]
        assert list(passed.get("tags") or []) == BENIGN["tags"]

    def test_a_post_id_naming_nothing_is_refused(self, client, serve, rows):
        # Deleted, tombstoned, or an id from a table this is not. All three mean
        # the server has no evidence about what is on the screen, and the reason
        # for accepting the id was to stop depending on the client's account of
        # it — so falling back to that account here would undo the change for
        # exactly the requests where it is least trustworthy.
        assert ask(client, "post_detail", BENIGN, post_id=404).get_json()["placements"] == []
        assert not serve.called

    def test_a_deleted_post_is_filtered_in_the_query_not_afterwards(self, client, rows):
        rows.rows[5] = post_row(body="running shoes")
        ask(client, "post_detail", BENIGN, post_id=5)

        statement = rows.statements[0]
        assert "deleted_at IS NULL" in statement, (
            "a tombstoned post must be absent rather than assessed; a post whose "
            "author deleted it is not content we may decorate"
        )
        assert "FROM pulse_posts" in statement
        assert "SELECT *" not in statement, (
            "a star select makes the gate's inputs whatever `add_columns_if_missing` "
            "last appended to the widest table in the product"
        )

    @pytest.mark.parametrize("bad", ["", None, 0, -4, "seven", {"id": 5}, [5]])
    def test_an_unusable_post_id_falls_back_rather_than_refusing(
        self, client, serve, rows, bad
    ):
        # A malformed id is a client bug, and the harm of treating it as evidence
        # is a commerce blackout for whoever shipped that build. It degrades to
        # the wire check — which is what every request did before this existed.
        assert ask(client, "post_detail", BENIGN, post_id=bad).get_json()["placements"]
        assert serve.called
        assert not rows.statements, "an unusable id must not cost a query"


class TestAFailedReadIsNotAVerdict:
    """The distinction that keeps one bad minute from becoming an outage.

    A post that is *gone* is evidence. A post the server could not *read* is not,
    and conflating them would mean a database hiccup removed commerce from every
    post page — while the posts that genuinely needed suppressing carried on being
    served for as long as the query happened to work.
    """

    @pytest.fixture
    def rows(self) -> PostRows:
        return PostRows(fail=True)

    def test_a_read_failure_serves_as_before(self, client, serve):
        assert ask(client, "post_detail", BENIGN, post_id=5).get_json()["placements"]
        assert serve.called

    def test_the_wire_check_still_refuses_when_the_read_fails(self, client, serve):
        # Degrading to the weaker check is not the same as degrading to no check.
        # A sensitive context is refused on its own strength, whatever the
        # database is doing.
        assert ask(client, "post_detail", GRIEF, post_id=5).get_json()["placements"] == []
        assert not serve.called

    def test_the_failure_is_logged_as_a_failure(self, client, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger=routes.LOGGER.name):
            ask(client, "post_detail", BENIGN, post_id=5)

        assert any("COMMERCE_DISCOVERY_POST_READ_FAILED" in r.getMessage()
                   for r in caplog.records), (
            "a check that silently stopped running is the failure mode this whole "
            "file exists for"
        )


class TestTheExemptSurfacesDoNotReadAPost:
    """Sending the id somewhere it does not belong must not start a read.

    Asserted on the *cursor* rather than on the response, because the response is
    identical either way. The question is whether a private conversation or a shop
    shelf caused a post row to be fetched and content-assessed, and only the
    absence of a query answers that.
    """

    @pytest.mark.parametrize("surface", ["messenger", "marketplace"])
    def test_a_surface_with_no_surrounding_content_ignores_the_id(
        self, client, serve, rows, surface
    ):
        rows.rows[5] = post_row(body=BEREAVEMENT_BODY)

        assert ask(client, surface, BENIGN, post_id=5).get_json()["placements"]
        assert serve.called
        assert not rows.statements, (
            f"{surface} read a post row; the messenger strip in particular must "
            f"never have content derived into a judgement about a conversation"
        )

    def test_product_detail_ignores_the_id(self, client, serve, rows, monkeypatch):
        monkeypatch.setattr(routes, "_anchor_context", lambda _cur, _id: {"category": "watches"})
        rows.rows[5] = post_row(body=BEREAVEMENT_BODY)

        assert ask(client, "product_detail", BENIGN, listing_id=41, post_id=5).status_code == 200
        assert serve.called
        assert not rows.statements

    @pytest.mark.parametrize("surface", sorted(suitability.CONTENT_SURFACES))
    def test_every_content_surface_honours_the_id(self, client, serve, rows, surface):
        # The inverse, so the exemption above stays an exemption rather than
        # becoming the rule. `feed` sends no id today — its commerce row is a
        # sibling between posts, so there is no single post to name — but the
        # gate is written per-surface-class, not per-client, and a surface that
        # starts naming its post must be judged on it.
        rows.rows[5] = post_row(body=BEREAVEMENT_BODY)

        assert ask(client, surface, BENIGN, post_id=5).get_json()["placements"] == []
        assert not serve.called
