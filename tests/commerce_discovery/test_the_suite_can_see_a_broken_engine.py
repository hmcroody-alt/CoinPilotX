"""A green test that measured the fail-safe instead of the engine is a lie.

`engine.serve` ends in ``except Exception: return []``. That is right and must
stay — §82: the post renders even when commerce does not. The cost is that a
crash and a decision are the same value to the caller, and most of this package's
assertions are about that value.

Measured 2026-09-27 by putting ``raise TypeError`` on `_serve`'s first line, so
the engine could not answer anything at all:

    110 failed, 558 passed

Some of those 558 never call `serve` and are honestly green. The rest are every
*negative* assertion in the package — the opted-out viewer, the reached cap, the
suppressed surface, the empty pool — and each one passes exactly as well when the
engine is a smoking hole, because ``[] == []``. The suite could not distinguish
"correctly showed nothing" from "was unable to show anything", which is the same
confusion §18.6 found one layer up and the same one `schema.py`'s docstring
describes for a rolled-back CREATE: *a shop that is permanently, quietly shut*
looks like a shop with nothing to sell.

`conftest`'s guard closes it by reading the signal the fail-safe already emits.
This file tests the guard, because a guard nobody tested is a comment.

Deliberately *not* fixed by adding a strict mode that re-raises under test: that
would make the tested path differ from the shipped one, and §18.6 is a whole
section about what happens when the test double is kinder than production.

**There were two fail-safes, not one.** The first version of this file watched
only `engine.serve` and said so in a "what this does not fix" note. The route
layer has its own ``except Exception`` above the engine's, answering HTTP 200 with
``{"ok": True, "placements": []}``, and watching it turned another 29 tests red —
the suitability tests, which are the ones that matter most.
`TestTheRouteFailSafeIsWatchedToo` has the measurement and the two traps that made
the extension less obvious than it sounds.
"""

from __future__ import annotations

import inspect
import logging
import os

import pytest

from services.commerce_discovery import engine


@pytest.fixture(scope="module")
def guard(request):
    """The `conftest` module itself, fetched from the plugin manager.

    Not ``import conftest``: this directory has no ``__init__.py``, so the name
    that module ends up under in ``sys.modules`` is an implementation detail of
    pytest's import mode. Conftests are registered plugins, so ask the registry.
    """
    for plugin in request.config.pluginmanager.get_plugins():
        path = (getattr(plugin, "__file__", "") or "").replace(os.sep, "/")
        if path.endswith("tests/commerce_discovery/conftest.py"):
            return plugin
    pytest.fail("this package's conftest is not a registered plugin")


class _FakeItem:
    """The two things :func:`swallowed_failure_report` asks an item for."""

    def __init__(self, *, marked=False, recorder=None, key=None):
        self.stash = pytest.Stash()
        self._marked = marked
        if recorder is not None:
            self.stash[key] = recorder

    def get_closest_marker(self, name):
        return object() if self._marked else None


class _FakeReport:
    def __init__(self, *, when="call", passed=True):
        self.when = when
        self.passed = passed


class TestTheFailSafeIsStillThere:
    """If any of this changes, the guard is watching for something that no longer
    happens and the blind spot is back with a green suite over it."""

    @pytest.mark.commerce_serve_may_fail
    def test_a_crash_inside_serve_comes_back_as_an_empty_list(self, market, monkeypatch):
        def explode(*_args, **_kwargs):
            raise TypeError("as if a signature had drifted")

        monkeypatch.setattr(engine, "_serve", explode)
        assert market.serve("feed") == [], (
            "the fail-safe is gone; a commerce bug now breaks the feed itself"
        )

    @pytest.mark.commerce_serve_may_fail
    def test_the_crash_is_logged_with_the_prefix_the_guard_watches(
        self, market, monkeypatch, swallowed_serve_failures, guard
    ):
        """Pins the log message to the guard's literal.

        The guard matches on a string. Renaming the log line without renaming
        `SERVE_FAILED_PREFIX` would disarm every assertion in this package at
        once, silently, and with no test failing — so the rename has to break
        *here*.
        """
        def explode(*_args, **_kwargs):
            raise TypeError("as if a signature had drifted")

        monkeypatch.setattr(engine, "_serve", explode)
        market.serve("feed")

        assert swallowed_serve_failures.records, (
            f"`serve` swallowed an exception without logging a line starting "
            f"{guard.SERVE_FAILED_PREFIX!r}"
        )
        assert "TypeError" in swallowed_serve_failures.detail(), (
            "the record carries no traceback, so the guard can report that a test "
            "was invalid but not why"
        )

    def test_a_healthy_serve_records_nothing(self, market, swallowed_serve_failures):
        """The other direction. A recorder that fired on every request would make
        the guard fail the whole suite, which is indistinguishable from the guard
        not existing."""
        assert market.serve("feed")
        assert swallowed_serve_failures.records == []


class TestTheGuardFlipsAPassingReport:
    """:func:`swallowed_failure_report` is the decision; the hook is three lines
    of glue around it."""

    # Marked because this test breaks `serve` for real in order to produce a
    # genuine record to report on — so the guard fires on it, correctly. It did,
    # the first time this file was run, which is the least ceremonious possible
    # demonstration that the guard works.
    @pytest.mark.commerce_serve_may_fail
    def test_a_passing_test_that_swallowed_a_failure_is_reported(self, guard, market, monkeypatch):
        def explode(*_args, **_kwargs):
            raise TypeError("as if a signature had drifted")

        monkeypatch.setattr(engine, "_serve", explode)
        recorder = guard._SwallowedServeFailures()
        import logging

        logging.getLogger(engine.__name__).addHandler(recorder)
        try:
            market.serve("feed")
        finally:
            logging.getLogger(engine.__name__).removeHandler(recorder)
        assert recorder.records, "nothing to report on"

        item = _FakeItem(recorder=recorder, key=guard._RECORDER_KEY)
        detail = guard.swallowed_failure_report(item, _FakeReport())
        assert detail, "a vacuously green test would have stayed green"
        assert "TypeError" in detail, detail

    def test_the_marker_is_the_way_out(self, guard):
        recorder = guard._SwallowedServeFailures()
        recorder.records.append(
            __import__("logging").LogRecord(
                "x", 40, "x", 1, "COMMERCE_DISCOVERY_SERVE_FAILED surface=feed", (), None
            )
        )
        marked = _FakeItem(marked=True, recorder=recorder, key=guard._RECORDER_KEY)
        assert guard.swallowed_failure_report(marked, _FakeReport()) == ""

        unmarked = _FakeItem(recorder=recorder, key=guard._RECORDER_KEY)
        assert guard.swallowed_failure_report(unmarked, _FakeReport()) != "", (
            "the marker is not what made the difference, so it is not an opt-out"
        )

    def test_an_already_failing_test_is_left_alone(self, guard):
        """Otherwise a run where the engine is genuinely broken reports every
        failure twice, and the second copy says something less useful than the
        first."""
        recorder = guard._SwallowedServeFailures()
        recorder.records.append(
            __import__("logging").LogRecord(
                "x", 40, "x", 1, "COMMERCE_DISCOVERY_SERVE_FAILED surface=feed", (), None
            )
        )
        item = _FakeItem(recorder=recorder, key=guard._RECORDER_KEY)
        assert guard.swallowed_failure_report(item, _FakeReport(passed=False)) == ""

    @pytest.mark.parametrize("phase", ["setup", "teardown"])
    def test_only_the_call_phase_is_judged(self, guard, phase):
        recorder = guard._SwallowedServeFailures()
        recorder.records.append(
            __import__("logging").LogRecord(
                "x", 40, "x", 1, "COMMERCE_DISCOVERY_SERVE_FAILED surface=feed", (), None
            )
        )
        item = _FakeItem(recorder=recorder, key=guard._RECORDER_KEY)
        assert guard.swallowed_failure_report(item, _FakeReport(when=phase)) == ""

    def test_a_clean_test_is_not_touched(self, guard):
        item = _FakeItem(recorder=guard._SwallowedServeFailures(), key=guard._RECORDER_KEY)
        assert guard.swallowed_failure_report(item, _FakeReport()) == ""

    def test_a_test_with_no_recorder_at_all_is_not_touched(self, guard):
        """The autouse fixture should always have run, but a missing stash entry
        must mean "nothing observed", never a crash inside the reporting hook —
        an exception here would take down report generation for the whole run."""
        assert guard.swallowed_failure_report(_FakeItem(), _FakeReport()) == ""


class TestTheHookIsWiredToTheDecision:
    """The one link the tests above cannot reach: pytest calls the hook, and the
    hook has to call the function. Pinned from source rather than left implied."""

    def test_the_report_hook_delegates_and_flips_the_outcome(self, guard):
        source = inspect.getsource(guard.pytest_runtest_makereport)
        assert "swallowed_failure_report" in source, (
            "the hook no longer consults the decision function, so every test "
            "above is testing something pytest does not run"
        )
        assert 'report.outcome = "failed"' in source, source
        assert "wrapper=True" in inspect.getsource(guard).split(
            "def pytest_runtest_makereport"
        )[0][-200:], (
            "the hook is not declared as a wrapper, so `report = yield` will not "
            "receive the report"
        )

    def test_the_marker_is_registered(self, guard, request):
        """An unregistered marker is a warning today and an error under
        ``--strict-markers``, which would turn the opt-out into a failure."""
        registered = "\n".join(request.config.getini("markers"))
        assert "commerce_serve_may_fail" in registered


class TestTheRouteFailSafeIsWatchedToo:
    """There are *two* fail-safes, and the guard above only knew about one.

    `commerce_discovery_routes.commerce_discovery_serve` wraps the engine's
    fail-safe in another one, and the outer one is worse: it returns
    ``{"ok": True, "placements": []}`` with **HTTP 200**. A crashed route is
    therefore indistinguishable from "no products for you" not only to a test but
    to the client and to any dashboard counting empty responses.

    Measured 2026-09-27 by raising ``TypeError`` on the route handler's first line,
    before and after watching the second logger:

        48 failed, 649 passed   (engine logger only)
        77 failed, 620 passed   (both)

    The 29 that changed sides are the suitability tests — the ones certifying that
    PulseSoc does not put a shopping card beside a bereavement post. The 8 that
    stayed green call `suitability.assess_adjacency` directly and never touch the
    route, which is §11a's point: the goal is not zero survivors.

    What makes this worth its own class is the *shape* of the near-miss. Those
    tests were not lazy. They assert the empty body **and** ``not serve.called`` —
    a second, stronger assertion written specifically to catch a refusal that
    happens too late. A crash before retrieval satisfies it perfectly. A stronger
    assertion in the same direction is still the same direction, and no amount of
    care inside a test can substitute for something watching from outside it.
    """

    def test_both_fail_safes_are_watched(self, guard):
        watched = dict(guard.WATCHED_FAILSAFES)
        assert "services.commerce_discovery.engine" in watched
        assert "services.commerce_discovery_routes" in watched, (
            "the route's own fail-safe returns HTTP 200 with an empty list and is "
            "not covered by the engine's logger; 29 suitability tests were green "
            "against a route that could not run"
        )

    def test_the_route_prefix_is_not_caught_by_the_engine_prefix(self, guard):
        """The trap that made this an increment rather than a one-word change.

        ``COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`` looks like it extends
        ``COMMERCE_DISCOVERY_SERVE_FAILED`` and does not — the words diverge
        immediately after ``SERVE_``. Anyone "simplifying" the two prefixes into
        one `startswith` would silently stop watching the route.
        """
        assert not guard.ROUTE_FAILED_PREFIX.startswith(guard.SERVE_FAILED_PREFIX)
        assert not guard.SERVE_FAILED_PREFIX.startswith(guard.ROUTE_FAILED_PREFIX)

    def test_the_route_still_logs_that_prefix(self, guard):
        """Pins the guard's literal to the route's real message, the same way
        `TestTheFailSafeIsStillThere` pins the engine's."""
        from services import commerce_discovery_routes as routes

        source = inspect.getsource(routes.commerce_discovery_serve)
        assert guard.ROUTE_FAILED_PREFIX in source, (
            "the route's fail-safe no longer logs "
            f"{guard.ROUTE_FAILED_PREFIX!r}, so the guard is watching for a "
            "message nothing emits"
        )

    def test_the_route_fail_safe_returns_two_hundred_and_ok_true(self, guard):
        """Not a complaint — it is §82, and it is why the guard has to exist.

        Recorded as a test so that if anyone ever *does* make the route answer 5xx
        on a crash, this fails and points them at the guard they can then delete.
        """
        from services import commerce_discovery_routes as routes

        source = inspect.getsource(routes._empty)
        assert '"ok": True' in source and '"placements": []' in source, source

    def test_a_route_record_flips_a_passing_report(self, guard):
        recorder = guard._SwallowedServeFailures()
        recorder.emit(
            logging.LogRecord(
                "services.commerce_discovery_routes", 40, "x", 1,
                guard.ROUTE_FAILED_PREFIX + " surface=reels", (), None,
            )
        )
        assert recorder.records, (
            "the recorder ignored the route's prefix, so the whole extension is "
            "inert"
        )
        item = _FakeItem(recorder=recorder, key=guard._RECORDER_KEY)
        detail = guard.swallowed_failure_report(item, _FakeReport())
        assert detail
        assert "services.commerce_discovery_routes" in detail, (
            "the report does not say which module swallowed it, which sent "
            "someone to read engine.py the first time the route fail-safe fired"
        )

    def test_a_record_reaching_two_watched_loggers_is_reported_once(self, guard):
        """One handler instance is attached to several loggers. Without the
        identity dedupe the same failure would be listed twice, which reads like
        two bugs."""
        recorder = guard._SwallowedServeFailures()
        record = logging.LogRecord(
            "services.commerce_discovery.engine", 40, "x", 1,
            guard.SERVE_FAILED_PREFIX + " surface=feed", (), None,
        )
        recorder.emit(record)
        recorder.emit(record)
        assert len(recorder.records) == 1

    def test_an_unrelated_error_on_a_watched_logger_is_ignored(self, guard):
        """The guard must not fire on every ERROR the route happens to log — the
        route logs several, including an auth-lookup failure that is a legitimate
        401 path."""
        recorder = guard._SwallowedServeFailures()
        recorder.emit(
            logging.LogRecord(
                "services.commerce_discovery_routes", 40, "x", 1,
                "COMMERCE_DISCOVERY_AUTH_LOOKUP_FAILED", (), None,
            )
        )
        assert recorder.records == []

    def test_the_fixture_attaches_to_every_watched_logger(self, guard, swallowed_serve_failures):
        """The list is only useful if the fixture reads it. Asserted through the
        live handler set rather than from source, because the failure mode here is
        a fixture that iterates one entry and stops."""
        attached = {
            name
            for name, _prefix in guard.WATCHED_FAILSAFES
            if swallowed_serve_failures in logging.getLogger(name).handlers
        }
        assert attached == {name for name, _prefix in guard.WATCHED_FAILSAFES}
