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
"""

from __future__ import annotations

import inspect
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
