"""13 of `commerce_discovery_routes.py`'s 18 fail-soft handlers had never run.

§20 audited `services/commerce_discovery/` and found 25 of 56 handlers unreached.
The audit stopped at the package boundary, and §21 is what that cost: the request
layer holds a *second* fail-safe above the engine's, and 29 suitability tests were
green against it. Widening the audit to
`services/commerce_discovery_routes.py` moves the figure to **74 handlers, 31
never reached**, and makes the route module the worst in the feature:

    commerce_discovery_routes.py   13 / 18 never reached

That is not a tidy-up backlog. Three of those thirteen are load-bearing, and each
one is a *specific, confident, written* claim that nothing had checked:

* **`_with_db`'s four handlers.** Its docstring: "``close()`` is in a ``finally``,
  not on the success path. A close reachable only when nothing raised leaks one
  pooled connection per failure, and this pool is 8+8 with a 3s timeout — a few
  dozen failures is an outage on every other feature sharing it." That is a
  precise prediction about taking down unrelated features, and the handlers it
  describes had never executed in a test. §20 found the same shape in `_persist`
  ("the blast radius is one dropped placement") and the lesson did not generalise
  far enough: I checked the package's confident sentences and not the route's.

* **`commerce_discovery_serve`'s own fail-safe, line 654.** §21 extended the
  conftest guard to watch the log line this handler emits — and the handler had
  never run, so the guard was watching for a string no test had ever caused to be
  emitted. A guard keyed to an unexercised log line is a guard that fails open on
  a typo. This file makes it fire.

* **`_event_route`'s error vocabulary.** Its docstring says three copies "would be
  three places to forget that a ``DiscoveryEventError`` is a 400 and everything
  else is a 500 that must not leak its message." The not-leaking half is a
  security property stated in a comment, and both handlers were unreached.

What this file deliberately does **not** do is chase the other ten. `_anchor_*`
and `_content_post_id` are `int()`-shaped coercions whose failure is meant to be
indistinguishable from an absent field — §20's reasoning about the nine `silent`
handlers applies unchanged, and §11a's applies to the rest.
"""

from __future__ import annotations

import logging

import pytest
from flask import Flask

from services import commerce_discovery_routes as routes
from services.commerce_discovery import engine, events, schema


class RecordingConnection:
    """A connection that remembers the order it was asked to do things.

    The order is the point, not the counts. "Rolled back and closed" and "closed
    and rolled back" are different bugs: the second one rolls back on a closed
    connection, which on some drivers raises and on others silently does nothing.
    """

    def __init__(self, *, close_raises=False, rollback_raises=False):
        self.events: list[str] = []
        self._close_raises = close_raises
        self._rollback_raises = rollback_raises
        self.row_factory = None

    def cursor(self):
        self.events.append("cursor")
        return object()

    def commit(self):
        self.events.append("commit")

    def rollback(self):
        self.events.append("rollback")
        if self._rollback_raises:
            raise RuntimeError("as if the connection were already gone")

    def close(self):
        self.events.append("close")
        if self._close_raises:
            raise RuntimeError("as if the pool had been shut down under us")


@pytest.fixture
def conn(monkeypatch):
    """A connection `_with_db` will be handed, installed as `bot.db()`."""
    connection = RecordingConnection()
    monkeypatch.setattr(routes, "_bot", lambda: _StubBot(connection))
    return connection


class _StubBot:
    """Only what the route reaches for. `bot` is 111k lines and importing it to
    answer a question about connection discipline would make every test in this
    file pay for it."""

    def __init__(self, connection):
        self._connection = connection

    def db(self):
        return self._connection

    def parse_price_label_to_cents(self, label, currency=None):
        return 100, (currency or "USD")

    def pulse_marketplace_listing_payload(self, *_args, **_kwargs):
        return {}


class TestTheConnectionIsAlwaysReturnedToThePool:
    """`_with_db`'s docstring predicts an outage in *other* features. Checked.

    Each test here would pass against a `close()` on the success path only, except
    the ones that raise — which is the whole point, and why "it works" was never
    evidence for the claim.
    """

    def test_a_successful_handler_commits_and_closes(self, conn, monkeypatch):
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(conn))
        assert routes._with_db(lambda cur, c: "result") == "result"
        assert conn.events == ["cursor", "commit", "close"]

    def test_a_failing_handler_still_closes(self, conn, monkeypatch):
        """The leak, stated as a test.

        The pool is 8+8 with a 3s timeout. Sixteen unclosed connections is not a
        degraded commerce strip, it is every other feature sharing the pool
        blocking for three seconds and then failing.
        """
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(conn))
        with pytest.raises(RuntimeError):
            routes._with_db(_boom)
        assert "close" in conn.events, (
            "a failed commerce request leaked a pooled connection; sixteen of "
            "these is an outage in features that have nothing to do with commerce"
        )

    def test_a_failing_handler_rolls_back_before_closing(self, conn, monkeypatch):
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(conn))
        with pytest.raises(RuntimeError):
            routes._with_db(_boom)
        assert conn.events.index("rollback") < conn.events.index("close"), conn.events
        assert "commit" not in conn.events, (
            "a handler that raised had its partial work committed"
        )

    def test_the_exception_reaches_the_caller(self, conn, monkeypatch):
        """`_with_db` re-raises rather than returning a sentinel.

        This is the line that makes the route's own fail-safe reachable at all. A
        `_with_db` that swallowed would put a third indistinguishable empty result
        one layer further down, and §21 was about how expensive the second one was.
        """
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(conn))
        with pytest.raises(RuntimeError, match="as if the handler had a bad call"):
            routes._with_db(_boom)

    def test_a_rollback_that_itself_fails_does_not_hide_the_real_error(self, monkeypatch):
        """Diagnosing the wrong exception is how a one-line bug costs a day.

        The inner ``except Exception: pass`` around `rollback` exists for this, and
        it was one of the four unreached handlers.
        """
        connection = RecordingConnection(rollback_raises=True)
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(connection))
        with pytest.raises(RuntimeError, match="as if the handler had a bad call"):
            routes._with_db(_boom)
        assert "close" in connection.events, (
            "a failing rollback skipped the close, which is the leak this "
            "function's docstring is about, reached by a different route"
        )

    def test_a_close_that_itself_fails_does_not_hide_the_real_error(self, monkeypatch):
        connection = RecordingConnection(close_raises=True)
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(connection))
        with pytest.raises(RuntimeError, match="as if the handler had a bad call"):
            routes._with_db(_boom)

    def test_a_close_that_fails_on_the_success_path_does_not_fail_the_request(self, monkeypatch):
        """The user's placements were already computed and committed. Throwing
        them away because returning the connection went wrong turns a pool problem
        into a blank shelf."""
        connection = RecordingConnection(close_raises=True)
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(connection))
        assert routes._with_db(lambda cur, c: "result") == "result"


def _boom(cur, conn):
    raise RuntimeError("as if the handler had a bad call")


class TestTheRouteFailSafeActuallyFires:
    """§21's guard watches a log line. Nothing had ever emitted it.

    The guard added in §21 flips a passing test to failed when
    ``COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`` is logged. That is only protection
    if the handler that logs it works, and the audit says it had never run — so
    the guard was keyed to a string whose spelling nothing checked. A typo in the
    handler and the guard silently protects nothing.

    These tests are marked `commerce_serve_may_fail` because they deliberately
    cause the very log line the guard exists to catch. Without the marker the
    guard fires on its own proof, which is correct behaviour and exactly what
    happened to §19's test file.
    """

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": 9001}, None))
        monkeypatch.setattr(routes, "_rate_limited", lambda _ref: False)
        monkeypatch.setattr(routes, "_with_db", _boom_with_db)
        routes.reset_rate_limiter()
        app = Flask(__name__)
        app.register_blueprint(routes.discovery_blueprint)
        return app.test_client()

    @pytest.mark.commerce_serve_may_fail
    def test_a_broken_request_path_answers_two_hundred_and_empty(self, client):
        """§82. Not a complaint — the post must still render."""
        response = client.post(
            f"{routes.API_PREFIX}/feed", json={"session_id": "s-1"}
        )
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "placements": []}

    @pytest.mark.commerce_serve_may_fail
    def test_it_logs_the_prefix_the_guard_watches(self, client, caplog):
        """The loop §21 left open, closed.

        Pins the conftest guard's literal to the message this handler really
        emits, by making it really emit one.
        """
        with caplog.at_level(logging.ERROR, logger=routes.__name__):
            client.post(f"{routes.API_PREFIX}/feed", json={"session_id": "s-1"})

        messages = [record.getMessage() for record in caplog.records]
        assert any(
            message.startswith("COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED")
            for message in messages
        ), (
            "the route's fail-safe no longer emits the prefix conftest's "
            f"WATCHED_FAILSAFES matches on, so §21's guard protects nothing: "
            f"{messages}"
        )

    @pytest.mark.commerce_serve_may_fail
    def test_the_record_carries_the_traceback(self, client, caplog):
        """`LOGGER.exception`, not `LOGGER.error`. Without `exc_info` the guard can
        say a test was vacuous but not why, and an operator gets a prefix and no
        stack."""
        with caplog.at_level(logging.ERROR, logger=routes.__name__):
            client.post(f"{routes.API_PREFIX}/feed", json={"session_id": "s-1"})
        relevant = [
            record for record in caplog.records
            if record.getMessage().startswith("COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED")
        ]
        assert relevant and all(record.exc_info for record in relevant)

    @pytest.mark.commerce_serve_may_fail
    def test_the_guard_saw_it(self, client, swallowed_serve_failures):
        """End to end on the guard itself: a real route crash lands in the
        recorder the report hook reads. Every other test of the guard builds a
        `LogRecord` by hand."""
        client.post(f"{routes.API_PREFIX}/feed", json={"session_id": "s-1"})
        assert swallowed_serve_failures.records, (
            "a genuine route crash did not reach the recorder, so the guard "
            "works only against hand-built records"
        )
        assert any(
            record.name == "services.commerce_discovery_routes"
            for record in swallowed_serve_failures.records
        )


def _boom_with_db(handler):
    raise RuntimeError("as if bot.db() had no pool left")


class TestAnUnknownSurfaceIsNotAnOutage:
    """Three unrelated conditions return the same value. At least pin which.

    `_empty()` is returned for an unknown surface, for a rate-limited client, and
    for a crash. §21 argues that collapse is expensive; it is also §82, so it
    stays. What can be checked is that the two *decisions* do not log the
    fail-safe's line — otherwise the guard would flip every test that exercises a
    suppressed surface, which is the noise problem §19 designed three conditions
    to avoid.
    """

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": 9001}, None))
        routes.reset_rate_limiter()
        app = Flask(__name__)
        app.register_blueprint(routes.discovery_blueprint)
        return app.test_client()

    def test_an_unknown_surface_is_empty_and_silent(self, client, swallowed_serve_failures):
        response = client.post(
            f"{routes.API_PREFIX}/a_surface_nobody_has_built", json={"session_id": "s-1"}
        )
        assert response.status_code == 200
        assert response.get_json() == {"ok": True, "placements": []}
        assert not swallowed_serve_failures.records, (
            "a surface name that is simply wrong logged a fail-safe line, which "
            "would flip every test that asks about an unknown surface"
        )

    def test_a_rate_limited_client_is_empty_and_silent(self, client, monkeypatch, swallowed_serve_failures):
        monkeypatch.setattr(routes, "_rate_limited", lambda _ref: True)
        monkeypatch.setattr(routes, "_with_db", _never_called)
        response = client.post(f"{routes.API_PREFIX}/feed", json={"session_id": "s-1"})
        assert response.get_json() == {"ok": True, "placements": []}
        assert not swallowed_serve_failures.records

    def test_the_surface_check_is_case_insensitive_and_the_normalised_name_is_used(
        self, client, monkeypatch
    ):
        """Guards the fix recorded in the route's own comment: the check used to
        lowercase a copy and leave the original in play, so `/REELS` passed the
        gate and was then served feed cadence under a surface name nothing else
        writes."""
        seen = {}

        def capture(handler):
            return handler(None, None)

        def spy(cur, user_id, surface, **_kwargs):
            # `surface` is the third *positional* argument, not a keyword. Reading
            # it out of `**kwargs` returns None and the assertion below then fails
            # for a reason that has nothing to do with normalisation.
            seen["surface"] = surface
            return []

        monkeypatch.setattr(routes, "_rate_limited", lambda _ref: False)
        monkeypatch.setattr(routes, "_with_db", capture)
        monkeypatch.setattr(engine, "serve", spy)
        monkeypatch.setattr(routes, "_bot", lambda: _StubBot(RecordingConnection()))

        client.post(f"{routes.API_PREFIX}/REELS", json={"session_id": "s-1"})
        assert seen.get("surface") == "reels", seen

    def test_every_surface_the_router_knows_clears_the_name_check(self, client):
        """The other half: the check must not reject a surface that exists."""
        for surface in sorted(schema.SURFACES):
            assert surface == surface.strip().lower(), (
                f"{surface!r} is declared in a form the route normalises away, so "
                "it can never be requested"
            )


def _never_called(handler):  # pragma: no cover - asserting it is not reached
    raise AssertionError("a rate-limited request opened a database connection")


class TestTheEventRoutesDoNotLeakTheirFailures:
    """`_event_route`'s docstring: a `DiscoveryEventError` is a 400 and
    "everything else is a 500 that must not leak its message". Both handlers were
    unreached, and the second half is a security property written as a comment."""

    @pytest.fixture
    def client(self, monkeypatch):
        monkeypatch.setattr(routes, "_require_user", lambda: ({"user_id": 9001}, None))
        app = Flask(__name__)
        app.register_blueprint(routes.discovery_blueprint)
        return app.test_client()

    def _post(self, client, **body):
        return client.post(f"{routes.API_PREFIX}/events/impression", json=body)

    def test_a_known_event_error_is_a_four_hundred_carrying_its_code(
        self, client, monkeypatch
    ):
        # Positional, and note the order: `DiscoveryEventError(code, message)`.
        # Code first reads backwards next to every other exception in the repo,
        # which is worth a comment rather than a silent `code=` keyword that would
        # bind to `message`.
        error = events.DiscoveryEventError("STALE_TOKEN", "That placement has expired.")

        def refuse(handler):
            raise error

        monkeypatch.setattr(routes, "_with_db", refuse)
        response = self._post(client, placement_id="p1", impression_token="t1")
        assert response.status_code == 400
        body = response.get_json()
        assert body["ok"] is False
        assert body["error_code"] == "STALE_TOKEN"
        assert body["error"] == "STALE_TOKEN", (
            "only `error_code` was set; the older web handlers read `error` and "
            "would collapse this to a generic message"
        )
        assert body["message"] == "That placement has expired."

    def test_an_unexpected_failure_is_a_five_hundred_that_says_nothing(
        self, client, monkeypatch
    ):
        """The security half. A route that echoes `str(exc)` hands a client the
        SQL, the table names, or a connection string."""
        secret = "no such column: marketplace_sellers.internal_risk_note"

        def explode(handler):
            raise RuntimeError(secret)

        monkeypatch.setattr(routes, "_with_db", explode)
        response = self._post(client, placement_id="p1", impression_token="t1")
        assert response.status_code == 500
        body = response.get_json()
        assert secret not in repr(body), body
        assert "marketplace_sellers" not in repr(body), body
        assert body["error_code"] == "NETWORK_ERROR"

    def test_the_unexpected_failure_is_logged_even_though_it_is_not_returned(
        self, client, monkeypatch, caplog
    ):
        """Not returning it to the client is only correct if somebody can still
        see it. A 500 that is silent on both sides is unfixable."""
        def explode(handler):
            raise RuntimeError("as if a migration had not run")

        monkeypatch.setattr(routes, "_with_db", explode)
        with caplog.at_level(logging.ERROR, logger=routes.__name__):
            self._post(client, placement_id="p1", impression_token="t1")
        assert any(
            "COMMERCE_DISCOVERY_EVENT_FAILED" in record.getMessage()
            for record in caplog.records
        )

    def test_an_event_failure_does_not_trip_the_serve_guard(
        self, client, monkeypatch, swallowed_serve_failures
    ):
        """`COMMERCE_DISCOVERY_EVENT_FAILED` is on a watched *logger* but is not a
        watched *prefix*. An event write failing is a 500 the client can see and
        retry — it is not a silently empty shelf, so it must not flip an otherwise
        passing test."""
        def explode(handler):
            raise RuntimeError("as if a migration had not run")

        monkeypatch.setattr(routes, "_with_db", explode)
        self._post(client, placement_id="p1", impression_token="t1")
        assert not swallowed_serve_failures.records, (
            "the guard fired on an error that is already visible to the client, "
            "which is the noise that makes a guard get deleted"
        )
