"""Request-path bridge into Sentinel — Stage 3.

The bridge makes three promises, and each one is only worth as much as the
test that could catch it breaking:

1. It cannot amplify an attack. An emit must cost zero database connections,
   no matter how many arrive. :func:`test_emit_opens_no_database_connections`
   counts them rather than trusting the design note.
2. It is default-OFF, and the OFF test is paired with an ON test so it cannot
   pass merely because everything is broken.
3. Dropping is allowed; hiding a drop is not. Every drop path below asserts on
   the counter *and* on ``evidence_complete``.

The subtle one is :func:`test_burst_in_one_second_is_not_collapsed`. ``Event``'s
default ``dedupe_key`` is a hash that includes ``occurred_at``, which has
one-second resolution — so a naive bridge records one event per second during a
brute force and calls the loss "idempotency". That test fails if anyone
"simplifies" the explicit key away.
"""

import sqlite3

import pytest

from services.sentinel import bootstrap, events, request_bridge, store


@pytest.fixture()
def wired(monkeypatch):
    """Bridge ON, schema present, flushes landing in one real SQLite database.

    ``close()`` is neutered so the shared in-memory database survives a flush;
    everything else is the production path.
    """
    db = sqlite3.connect(":memory:")
    store.ensure_schema(db)

    class Handle:
        """A connection the bridge may close as often as it likes."""
        def __init__(self, inner):
            self._inner = inner
            self.closed = 0

        def cursor(self):
            return self._inner.cursor()

        def commit(self):
            return self._inner.commit()

        def close(self):
            self.closed += 1

    opens = {"count": 0}

    def connect():
        opens["count"] += 1
        return Handle(db)

    monkeypatch.setattr(store.platform_db, "connect", connect)
    monkeypatch.setenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "1")

    bootstrap.reset_for_tests()
    bootstrap.ensure_schema(db, force=True)
    request_bridge.reset_for_tests()
    # The buffer is what is under test; a background thread racing it turns
    # every count into a coin flip. Tests drive flush() explicitly, except the
    # one test whose subject is the worker.
    monkeypatch.setattr(request_bridge, "_ensure_worker", lambda: None)

    yield {"db": db, "opens": opens}

    request_bridge.reset_for_tests()
    bootstrap.reset_for_tests()
    db.close()


def an_event(status=401, path="/api/pulse/feed", **kw):
    kw.setdefault("ip_hash", "a" * 64)
    return request_bridge.build_request_event(status=status, path=path, **kw)


def stored(db):
    cur = db.cursor()
    cur.execute("SELECT category, event_type, actor_id, actor_type, network_ref, "
                "payload_json, source_trust, dedupe_key FROM sentinel_events ORDER BY id")
    return cur.fetchall()


# --- the reason this module exists -------------------------------------------


def test_emit_opens_no_database_connections(wired):
    """The load-bearing claim: emitting must not cost a connection.

    ``bot.db()`` opens a fresh connection per call and there is no per-request
    connection to lend ``events.ingest()``. An inline bridge would therefore
    turn each attacker request into a database connection at exactly the moment
    the database can least afford one — a security observer whose failure mode
    is helping the attacker. If this test ever fails, the bridge has become
    that.
    """
    for i in range(200):
        assert request_bridge.emit(an_event(path=f"/api/users/{i}")) is True

    assert wired["opens"]["count"] == 0, "emit reached the database"
    assert request_bridge.stats()["pending"] == 200

    assert request_bridge.flush() == 200
    assert wired["opens"]["count"] == 1, "a batch must cost exactly one connection"


def test_burst_in_one_second_is_not_collapsed(wired):
    """A brute force is a volume signal; dedupe must not eat it.

    Every event here shares source, category, type, subject and — because they
    are emitted together — ``occurred_at`` to the second. Under ``Event``'s
    default dedupe_key that is one stored event. Fifty distinct requests are
    fifty distinct facts.
    """
    for _ in range(50):
        request_bridge.emit(an_event(status=401, path="/api/mobile/auth/login"))
    assert request_bridge.flush() == 50

    rows = stored(wired["db"])
    assert len(rows) == 50, "requests collapsed into one event — volume signal lost"
    assert len({r[7] for r in rows}) == 50
    assert request_bridge.stats()["duplicates"] == 0


def test_dedupe_still_rejects_a_genuinely_repeated_key(wired):
    """The inverse: distinct keys must not mean dedupe was disabled outright.

    Without this, the test above could be satisfied by an ingest path that
    never dedupes anything, and idempotency would be silently gone.
    """
    first = an_event()
    twin = events.Event(**{**first.__dict__, "event_id": "different-event-id"})

    conn = store.platform_db.connect()
    assert events.ingest(first, conn) is True
    assert events.ingest(twin, conn) is False, "identical dedupe_key was stored twice"


# --- default OFF, and the control that proves the OFF test is not vacuous ----


def test_bridge_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SENTINEL_REQUEST_BRIDGE_ENABLED", raising=False)
    assert request_bridge.bridge_enabled() is False
    assert request_bridge.emit(an_event()) is False


def test_bridge_accepts_when_switched_on(wired):
    """Pairs with the test above: OFF must be a decision, not a broken emit."""
    assert request_bridge.bridge_enabled() is True
    assert request_bridge.emit(an_event()) is True
    assert request_bridge.stats()["emitted"] == 1


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "enabled", "TRUE"])
def test_switch_accepts_the_usual_spellings(monkeypatch, value):
    monkeypatch.setenv("SENTINEL_REQUEST_BRIDGE_ENABLED", value)
    assert request_bridge.bridge_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_switch_rejects_everything_else(monkeypatch, value):
    monkeypatch.setenv("SENTINEL_REQUEST_BRIDGE_ENABLED", value)
    assert request_bridge.bridge_enabled() is False


def test_absent_schema_blocks_emit_even_when_switched_on(wired):
    """Writing into unverified tables would fail once per event in the flush
    loop. Refusing at the door keeps the failure legible."""
    bootstrap.reset_for_tests()
    assert bootstrap.schema_ready() is False
    assert request_bridge.emit(an_event()) is False


# --- kill switches -----------------------------------------------------------


def test_killswitch_blocks_emit(wired, monkeypatch):
    monkeypatch.setenv("SENTINEL_INGEST_ENABLED", "0")
    assert request_bridge.emit(an_event()) is False


def test_kill_thrown_mid_flight_does_not_book_a_lost_batch_as_success(wired, monkeypatch):
    """``events.ingest()`` returns False when ingest is killed — the same value
    it returns for a dedupe rejection. Draining the buffer through it would
    record a killed batch as ``duplicates``, i.e. as idempotency working. The
    events must stay buffered instead."""
    for _ in range(5):
        request_bridge.emit(an_event())

    monkeypatch.setenv("SENTINEL_INGEST_ENABLED", "0")
    assert request_bridge.flush() == 0

    s = request_bridge.stats()
    assert s["duplicates"] == 0, "a killed batch was counted as duplicates"
    assert s["pending"] == 5, "events were drained into a disabled ingest"
    assert stored(wired["db"]) == []

    monkeypatch.setenv("SENTINEL_INGEST_ENABLED", "1")
    assert request_bridge.flush() == 5


# --- bounded memory, and the honesty that has to come with it ---------------


def test_overflow_drops_oldest_and_admits_it(wired, monkeypatch):
    monkeypatch.setattr(request_bridge, "MAX_BUFFER", 10)
    for i in range(25):
        request_bridge.emit(an_event(path=f"/marker/{i}/x"))

    s = request_bridge.stats()
    assert s["pending"] == 10
    assert s["dropped"] == 15
    assert s["evidence_complete"] is False, "a known-incomplete record claimed completeness"

    # Oldest-first: what survives is the newest window, which describes the
    # attack in progress rather than already-understood history.
    request_bridge.flush()
    rows = stored(wired["db"])
    assert len(rows) == 10


def test_evidence_complete_is_true_only_with_zero_drops(wired):
    assert request_bridge.stats()["evidence_complete"] is True
    request_bridge.emit(an_event())
    assert request_bridge.stats()["evidence_complete"] is True


# --- failure is survivable and never silent ---------------------------------


def test_flush_survives_a_dead_database_and_records_why(wired, monkeypatch):
    for _ in range(3):
        request_bridge.emit(an_event())

    def refuse():
        raise OSError("connection refused")

    monkeypatch.setattr(store.platform_db, "connect", refuse)
    assert request_bridge.flush() == 0  # must not raise

    s = request_bridge.stats()
    assert s["flush_failures"] >= 1
    assert s["last_error_type"] == "OSError"
    assert "connection refused" in s["last_error"]
    assert s["dropped"] == 3, "a lost batch must be counted as lost"
    assert s["evidence_complete"] is False


def test_a_malformed_event_does_not_discard_the_rest_of_the_batch(wired, monkeypatch):
    good = [an_event(path=f"/api/a/{i}") for i in range(3)]
    for e in good:
        request_bridge.emit(e)

    real_ingest = events.ingest
    calls = {"n": 0}

    def flaky(event, conn=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise events.EventRejected("malformed")
        return real_ingest(event, conn)

    monkeypatch.setattr(events, "ingest", flaky)
    assert request_bridge.flush() == 2

    s = request_bridge.stats()
    assert s["rejected"] == 1
    assert s["written"] == 2


def test_emit_never_raises(wired, monkeypatch):
    """Emit sits in the request path. It may refuse; it may not throw."""
    def explode():
        raise RuntimeError("switch lookup blew up")

    monkeypatch.setattr(request_bridge, "bridge_enabled", explode)
    assert request_bridge.emit(an_event()) is False


# --- path normalisation ------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("/api/users/8412", "/api/users/:id"),
    ("/api/users/8412/posts/91", "/api/users/:id/posts/:id"),
    ("/api/o/f47ac10b-58cc-4372-a567-0e02b2c3d479", "/api/o/:id"),
    ("/api/s/eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", "/api/s/:id"),
    ("/api/pulse/feed?token=secret&q=alice", "/api/pulse/feed"),
])
def test_normalize_path_removes_identifiers(raw, expected):
    assert request_bridge.normalize_path(raw) == expected


@pytest.mark.parametrize("raw", [
    "/api/pulse/feed",
    "/api/mobile/auth/refresh",
    "/api/business-os/dashboard",
])
def test_normalize_path_leaves_real_shapes_alone(raw):
    """The anti-vacuity pair: a normaliser that returned ':id' for everything
    would satisfy the test above and destroy all correlation value."""
    assert request_bridge.normalize_path(raw) == raw


def test_normalize_path_is_bounded_and_total():
    assert len(request_bridge.normalize_path("/a" * 500)) <= request_bridge._PATH_LIMIT
    assert request_bridge.normalize_path("") == ""
    assert request_bridge.normalize_path(None) == ""


def test_query_string_secrets_never_reach_storage(wired):
    request_bridge.emit(an_event(
        path="/api/pulse/feed?access_token=sk_live_51H8xQ2&email=alice@example.com"))
    request_bridge.flush()

    blob = " ".join(str(r[5]) for r in stored(wired["db"]))
    assert "sk_live_51H8xQ2" not in blob
    assert "alice@example.com" not in blob
    assert "/api/pulse/feed" in blob, "scrubbing must not destroy the signal"


# --- what an event says ------------------------------------------------------


@pytest.mark.parametrize("status,category,event_type", [
    (401, "AUTH", "request.unauthenticated"),
    (403, "SECURITY", "request.forbidden"),
    (423, "SECURITY", "request.locked"),
    (429, "SECURITY", "request.rate_limited"),
])
def test_observed_statuses_map_to_events(status, category, event_type):
    ev = an_event(status=status)
    assert ev.category == category
    assert ev.event_type == event_type


@pytest.mark.parametrize("status", [200, 201, 204, 302, 400, 404, 500, 502])
def test_ordinary_statuses_produce_no_event(status):
    """The bridge observes security outcomes, not traffic. A 404 firehose must
    not become a Sentinel firehose."""
    assert an_event(status=status) is None


def test_authenticated_request_is_attributed_to_the_user(wired):
    ev = an_event(user_id=4211)
    assert ev.actor_id == "user:4211"
    assert ev.actor_type == "USER"


def test_unauthenticated_request_is_a_device_not_a_system_actor(wired):
    """'Everything is SYSTEM' makes actor_type meaningless. An unnamed client
    is a DEVICE identified by the network it came from."""
    ev = an_event(ip_hash="f" * 64)
    assert ev.actor_type == "DEVICE"
    assert ev.actor_id.startswith("device:")
    assert ev.network_ref == "ip:" + "f" * 64


def test_attribution_gap_is_admitted_rather_than_invented(wired):
    ev = request_bridge.build_request_event(status=401, path="/api/x", ip_hash="")
    assert ev.actor_id == "device:unattributed"
    assert ev.network_ref is None


def test_event_claims_authority_over_the_fact_not_the_inference(wired):
    """The platform is the system of record for what status it returned. It is
    not authoritative about what that means — severity stays low and the event
    asserts no conclusion."""
    ev = an_event(status=401)
    assert ev.source_trust == "AUTHORITATIVE"
    assert ev.severity == "low"
    assert ev.confidence == 1.0


def test_events_survive_the_round_trip_intact(wired):
    request_bridge.emit(an_event(status=429, path="/api/users/77/follow",
                                 method="POST", user_id=77))
    request_bridge.flush()

    (row,) = stored(wired["db"])
    category, event_type, actor_id, actor_type, network_ref, payload, trust, _ = row
    assert (category, event_type) == ("SECURITY", "request.rate_limited")
    assert (actor_id, actor_type) == ("user:77", "USER")
    assert trust == "AUTHORITATIVE"
    assert "/api/users/:id/follow" in payload
    assert "POST" in payload


# --- the worker --------------------------------------------------------------


def test_worker_flushes_without_being_asked(monkeypatch):
    """Low-volume events must not sit in the buffer forever waiting for a
    threshold that never arrives."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    store.ensure_schema(db)

    class Handle:
        def __init__(self, inner):
            self._inner = inner

        def cursor(self):
            return self._inner.cursor()

        def commit(self):
            return self._inner.commit()

        def close(self):
            pass

    monkeypatch.setattr(store.platform_db, "connect", lambda: Handle(db))
    monkeypatch.setenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "1")
    monkeypatch.setattr(request_bridge, "FLUSH_INTERVAL_SECONDS", 0.05)

    bootstrap.reset_for_tests()
    bootstrap.ensure_schema(db, force=True)
    request_bridge.reset_for_tests()
    try:
        assert request_bridge.emit(an_event(status=403)) is True

        import time
        deadline = time.time() + 10
        while time.time() < deadline and request_bridge.stats()["written"] == 0:
            time.sleep(0.02)

        s = request_bridge.stats()
        assert s["written"] == 1, "the background worker never ran"
        assert s["worker_alive"] is True
        assert s["last_flush_at"] is not None
    finally:
        request_bridge.shutdown(timeout=2.0)
        request_bridge.reset_for_tests()
        bootstrap.reset_for_tests()
        db.close()
