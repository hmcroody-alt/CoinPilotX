"""Sentinel schema bootstrap — Stage 2.

These tests exist because the bootstrap's job is to be *honest*, and honesty is
exactly the property a happy-path test cannot demonstrate. Every assertion here
is paired with its negative: the probe is shown detecting absence, not just
confirming presence; FAILED is shown being reached, not just defined.

The specific vacuity trap worth naming, because it caught the first draft of
this file: "drop a table, re-run, expect FAILED" proves nothing, because
``ensure_schema`` recreates the table on the way past. It tests recovery while
appearing to test detection. The probe is therefore tested on its own below.
"""

import sqlite3

import pytest

from services.sentinel import bootstrap, store


@pytest.fixture()
def fresh():
    """Pristine module state; bootstrap keeps per-process state by design."""
    bootstrap.reset_for_tests()
    yield
    bootstrap.reset_for_tests()


@pytest.fixture()
def empty_conn():
    """A live connection with **no** Sentinel tables on it."""
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


# --- the probe, in isolation -------------------------------------------------


def test_probe_detects_absent_tables(empty_conn):
    """The load-bearing negative: absence must be observable.

    If this ever returns [] against an empty database, every other test in this
    file becomes vacuous and READY becomes a lie.
    """
    missing = bootstrap._missing_tables(empty_conn)
    assert set(missing) == set(bootstrap.REQUIRED_TABLES)


def test_probe_confirms_present_tables(empty_conn):
    store.ensure_schema(empty_conn)
    assert bootstrap._missing_tables(empty_conn) == []


# --- state machine -----------------------------------------------------------


def test_never_attempted_is_not_healthy(fresh):
    """Absence of evidence is not health (Hard Rule #4)."""
    state = bootstrap.schema_state()
    assert state["state"] == bootstrap.NEVER_ATTEMPTED
    assert state["healthy"] is False
    assert bootstrap.schema_ready() is False


def test_ensure_schema_reaches_ready(fresh, empty_conn):
    assert bootstrap.ensure_schema(empty_conn) == bootstrap.READY
    state = bootstrap.schema_state()
    assert state["healthy"] is True
    assert state["missing_tables"] == []
    assert state["statements_executed"] > 0
    assert bootstrap.schema_ready() is True


def test_ready_is_idempotent_and_does_not_re_attempt(fresh, empty_conn):
    bootstrap.ensure_schema(empty_conn)
    attempts = bootstrap.schema_state()["attempts"]
    for _ in range(3):
        assert bootstrap.ensure_schema(empty_conn) == bootstrap.READY
    assert bootstrap.schema_state()["attempts"] == attempts


# --- failure is reachable, loud, and non-fatal -------------------------------


def test_unreachable_database_fails_closed_without_raising(fresh, monkeypatch):
    """Sentinel observes; it must never be able to take the platform down.

    ``_init_db_impl`` has no ``except`` around it and is reached from ordinary
    route handlers, so raising here would turn a security-package problem into
    a product outage.
    """
    def refuse(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(store.platform_db, "connect", refuse)

    assert bootstrap.ensure_schema() == bootstrap.FAILED
    state = bootstrap.schema_state()
    assert state["healthy"] is False
    assert state["last_error"], "a failure must say why"
    assert set(state["missing_tables"]) == set(bootstrap.REQUIRED_TABLES)


def test_failure_records_cause_rather_than_swallowing_it(fresh, monkeypatch):
    def boom(_conn=None):
        raise RuntimeError("disk is on fire")

    monkeypatch.setattr(store, "ensure_schema", boom)
    monkeypatch.setattr(
        bootstrap, "_missing_tables", lambda conn=None: list(bootstrap.REQUIRED_TABLES)
    )

    assert bootstrap.ensure_schema() == bootstrap.FAILED
    state = bootstrap.schema_state()
    assert state["last_error_type"] == "RuntimeError"
    assert "disk is on fire" in state["last_error"]


def test_error_text_never_carries_database_credentials(fresh, monkeypatch):
    """Bootstrap state is rendered into health output; a DSN must not ride along."""
    def boom(_conn=None):
        raise RuntimeError(
            "could not connect: postgresql://sentinel:sup3rs3cret@db.internal:5432/pulse"
        )

    monkeypatch.setattr(store, "ensure_schema", boom)
    monkeypatch.setattr(
        bootstrap, "_missing_tables", lambda conn=None: list(bootstrap.REQUIRED_TABLES)
    )

    bootstrap.ensure_schema()
    recorded = bootstrap.schema_state()["last_error"]
    assert "sup3rs3cret" not in recorded
    assert "sentinel:" not in recorded
    assert "db.internal" in recorded, "scrubbing must not destroy the diagnostic"


def test_retry_is_rate_limited_after_failure(fresh, monkeypatch):
    """A caller may retry freely without hammering a database that is already ill."""
    monkeypatch.setattr(
        bootstrap, "_missing_tables", lambda conn=None: list(bootstrap.REQUIRED_TABLES)
    )
    monkeypatch.setattr(store, "ensure_schema", lambda _conn=None: 0)

    assert bootstrap.ensure_schema() == bootstrap.FAILED
    assert bootstrap.schema_state()["attempts"] == 1

    for _ in range(5):
        assert bootstrap.ensure_schema() == bootstrap.FAILED
    assert bootstrap.schema_state()["attempts"] == 1, "cooldown was not honoured"

    assert bootstrap.ensure_schema(force=True) == bootstrap.FAILED
    assert bootstrap.schema_state()["attempts"] == 2, "force must override cooldown"


# --- the concurrent-create race ---------------------------------------------


def test_lost_create_race_still_reports_ready(fresh, empty_conn, monkeypatch):
    """Four gunicorn workers boot at once and race on CREATE TABLE IF NOT EXISTS.

    The loser sees a duplicate-table error. But the invariant we actually care
    about — the tables exist — is true for it too, so it must report READY.
    This is why READY is decided by the probe and not by whether the DDL threw.
    """
    store.ensure_schema(empty_conn)

    def lost_the_race(_conn=None):
        raise Exception('relation "sentinel_events" already exists')

    monkeypatch.setattr(store, "ensure_schema", lost_the_race)

    assert bootstrap.ensure_schema(empty_conn) == bootstrap.READY
    assert bootstrap.schema_state()["healthy"] is True


def test_ddl_success_with_missing_tables_is_not_ready(fresh, monkeypatch):
    """The inverse guard: a clean DDL return does not buy READY on its own."""
    monkeypatch.setattr(store, "ensure_schema", lambda _conn=None: 51)
    monkeypatch.setattr(
        bootstrap, "_missing_tables", lambda conn=None: ["sentinel_evidence"]
    )

    assert bootstrap.ensure_schema() == bootstrap.FAILED
    assert bootstrap.schema_state()["missing_tables"] == ["sentinel_evidence"]


# --- switch ------------------------------------------------------------------


def test_bootstrap_defaults_on_but_is_overridable(monkeypatch):
    """Creating empty tables is inert; *writing* to them is the gated decision."""
    monkeypatch.delenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED", raising=False)
    assert bootstrap.bootstrap_enabled() is True

    monkeypatch.setenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED", "")
    assert bootstrap.bootstrap_enabled() is True

    monkeypatch.setenv("SENTINEL_SCHEMA_BOOTSTRAP_ENABLED", "0")
    assert bootstrap.bootstrap_enabled() is False
