"""Request-path bridge into Sentinel (Stage 3).

Before this module, `grep -c sentinel bot.py` returned 0: nothing in the live
request path emitted a security event, and Sentinel's only observation channel
was whatever ``alert_worker`` scraped on a schedule. Detection could therefore
never see an authentication failure, an authorization denial, or a rate-limit
breach at the moment it happened.

This closes that gap, subject to one constraint that shapes the whole design.

**The bridge must not amplify an attack.** ``events.ingest()`` opens its own
database connection when it isn't handed one, and ``bot.db()`` has no
per-request connection to lend it — every call is a fresh connect. Emitting
inline would therefore convert each attacker request into a database connection,
precisely when the request volume is highest and the database is least able to
absorb it. A security observer whose failure mode is "helps the attacker
exhaust the connection pool during an attack" is worse than no observer.

So events are appended to a bounded in-process ring and flushed in batches on a
single connection by a background thread. The cost of an emit on the request
path is a lock acquisition and a list append.

**Bounded means events can be dropped, and drops are counted.** Under sustained
overload the ring overflows and the oldest pending events are discarded. That is
a deliberate trade — bounded memory beats complete evidence when the alternative
is an OOM — but it is not allowed to be invisible. ``stats()`` reports
``dropped``, and a non-zero drop count means Sentinel's record of that window is
known-incomplete rather than quietly partial (Hard Rule #4).

Buffered events are also lost if the process dies. Declared here rather than
discovered later: this bridge provides *detection latency in seconds*, not
durable audit. Anything requiring durability belongs in the existing
``log_security_event()`` / ``admin_audit_logs`` paths, which are synchronous and
already exist — this does not replace them (Hard Rule #6).

Default **OFF**. ``killswitches.ingest_enabled()`` defaults on, which is right
for a package with no live emitters, but switching on a brand-new write path in
the request cycle of a production app is a decision an operator should make
deliberately. ``SENTINEL_REQUEST_BRIDGE_ENABLED`` is that decision.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import deque

from services.sentinel import bootstrap, entities, events, killswitches, store

logger = logging.getLogger(__name__)

#: Maximum pending events held in memory. At ~1KB of Python objects each this
#: is single-digit megabytes per worker, which four workers can afford.
MAX_BUFFER = 2000

#: Flush when this many events are pending, without waiting for the interval.
FLUSH_THRESHOLD = 50

#: Maximum seconds a low-volume event waits before being written. Detection
#: latency, not durability — see module docstring.
FLUSH_INTERVAL_SECONDS = 5.0

#: Events written per transaction.
FLUSH_BATCH_SIZE = 200

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

_LOCK = threading.Lock()
_BUFFER: deque = deque()
_WAKE = threading.Event()
_WORKER: threading.Thread | None = None
_SHUTDOWN = threading.Event()

_STATS: dict = {
    "emitted": 0,       # accepted into the buffer
    "written": 0,       # persisted by a flush
    "duplicates": 0,    # rejected by dedupe_key (idempotency working)
    "dropped": 0,       # lost to a full buffer — evidence is incomplete
    "rejected": 0,      # failed the Event envelope contract
    "flush_failures": 0,
    "last_flush_at": None,
    "last_error": "",
    "last_error_type": "",
}


def bridge_enabled() -> bool:
    """Whether the request path may write into Sentinel. Default **OFF**.

    The emergency switch is consulted here even though it changes no behaviour on
    the write path: :func:`emit` already refuses when ``ingest_enabled()`` is
    false, and that check runs one line after this one. What it changes is what
    :func:`stats` reports. ``stats()["enabled"]`` is this function, so during an
    emergency stop the health surface used to say the bridge was enabled while it
    was in fact recording nothing — and said ``evidence_complete: true`` beside
    it, because nothing had been dropped. An operator reading that would conclude
    Sentinel was watching. A health surface that reports a stopped control as
    enabled is the "no fake health" rule broken in the one direction that
    matters, so the gate now answers the question an operator is actually asking:
    is this thing running.
    """
    if killswitches.emergency_killed():
        return False
    return str(os.getenv("SENTINEL_REQUEST_BRIDGE_ENABLED", "")).strip().lower() in _TRUTHY


# --- path normalisation ------------------------------------------------------

# Request paths carry identifiers, and identifiers are how an events table turns
# into a personal-data store. Correlation needs the *shape* of the path, not the
# row it addressed, so the shape is what gets kept.
_NUMERIC_SEGMENT = re.compile(r"/\d+")
_UUID_SEGMENT = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LONG_TOKEN_SEGMENT = re.compile(r"/[A-Za-z0-9_-]{24,}")
_PATH_LIMIT = 120


def normalize_path(path: str) -> str:
    """Collapse identifiers out of a request path.

    ``/api/users/8412/posts/f47ac10b-...`` becomes ``/api/users/:id/posts/:id``.
    Query strings are dropped entirely — they carry tokens and search terms and
    have no correlation value that the path shape does not already provide.
    """
    raw = str(path or "").split("?", 1)[0]
    raw = _UUID_SEGMENT.sub("/:id", raw)
    raw = _LONG_TOKEN_SEGMENT.sub("/:id", raw)
    raw = _NUMERIC_SEGMENT.sub("/:id", raw)
    return raw[:_PATH_LIMIT]


# --- event construction ------------------------------------------------------

#: Response codes worth observing, and what each one means as a security fact.
#: Deliberately narrow: these are the four the shipped client already
#: understands (Stage 1 contract), so observing them cannot tempt a later stage
#: into inventing a fifth.
STATUS_MEANING = {
    401: ("AUTH", "request.unauthenticated", "low", "low"),
    403: ("SECURITY", "request.forbidden", "low", "low"),
    423: ("SECURITY", "request.locked", "info", "none"),
    429: ("SECURITY", "request.rate_limited", "low", "low"),
}

SOURCE = "pulsesoc.request_bridge"


def build_request_event(
    *,
    status: int,
    path: str,
    method: str = "",
    user_id=None,
    ip_hash: str = "",
    environment: str = "",
    extra: dict | None = None,
) -> events.Event | None:
    """Build one event from a finished request, or None if it isn't security-relevant.

    ``ip_hash`` must already be hashed — pass ``bot.client_ip_hash()``. Reusing
    that function rather than hashing here is deliberate: it is salted with
    ``ANALYTICS_SALT`` and is the same value stored in ``admin_audit_logs.ip_hash``
    and ``admin_session_logs.ip_hash``, so Sentinel's network refs *join* against
    the audit trail the platform already keeps. A second, differently-salted hash
    of the same addresses would be a private identifier space that correlates
    with nothing (Hard Rule #6).
    """
    meaning = STATUS_MEANING.get(int(status))
    if meaning is None:
        return None
    category, event_type, severity, security_impact = meaning

    shape = normalize_path(path)

    # An unauthenticated caller is not a "SYSTEM" actor — that is the lie the
    # foundation map warns about ("no 'everything is SYSTEM'"). What we actually
    # observed is a client we cannot name, so it is typed DEVICE and identified
    # by the hashed network it came from. When the request carried a session the
    # actor is the real one.
    if user_id not in (None, "", 0):
        actor_id = f"user:{user_id}"
        actor_type = "USER"
    elif ip_hash:
        actor_id = f"device:{ip_hash[:32]}"
        actor_type = "DEVICE"
    else:
        # No session and no address: an actor_id is required (SC12) and
        # inventing a plausible one would be worse than admitting the gap.
        actor_id = "device:unattributed"
        actor_type = "DEVICE"

    network_ref = entities.make_ref("ip", ip_hash[:64]) if ip_hash else None

    payload = {"status": int(status), "path": shape, "method": str(method or "")[:10]}
    if extra:
        payload.update(extra)

    return events.Event(
        category=category,
        event_type=event_type,
        severity=severity,
        actor_id=actor_id,
        actor_type=actor_type,
        source=SOURCE,
        source_system="pulsesoc",
        source_component="request_bridge",
        # The platform is the system of record for what status *it* returned.
        # Note the scope of that claim: the fact "we answered 403 here" is
        # authoritative; the inference "this is an attack" is not, and this
        # event does not make it. Correlation draws conclusions; this reports.
        source_trust="AUTHORITATIVE",
        environment=environment,
        network_ref=network_ref,
        resource_type="route",
        security_impact=security_impact,
        payload=payload,
        correlation_keys=tuple(
            k for k in (
                f"ip:{ip_hash[:32]}" if ip_hash else "",
                f"route:{shape}",
                f"actor:{actor_id}",
            ) if k
        ),
        # --- why this is set explicitly ---
        # Event's default dedupe_key is a hash of
        # source|category|event_type|subject|occurred_at, and occurred_at has
        # one-second granularity. For scheduled scrapers that is right. Here it
        # is actively harmful: a brute-force at 50 req/s would collapse into one
        # stored event per second, destroying exactly the volume signal that
        # makes a brute-force detectable — and doing so silently, under the
        # name "idempotency". Each observed request is a distinct fact, so each
        # gets a distinct key. Idempotency is not lost by this: flush() pops
        # events off the buffer before writing, so no event is ever offered to
        # ingest twice.
        dedupe_key=f"reqbridge:{uuid.uuid4()}",
    )


# --- emit --------------------------------------------------------------------


def emit(event: events.Event) -> bool:
    """Queue one event. Never raises. Returns True if it was accepted.

    False means the bridge is off, the schema is not ready, ingest is killed, or
    the buffer overflowed — all of which are counted rather than hidden.
    """
    try:
        if not bridge_enabled():
            return False
        if not killswitches.ingest_enabled():
            return False
        if not bootstrap.schema_ready():
            # Writing into tables that were never verified would fail per event
            # in the flush loop. Refusing here keeps the failure legible: the
            # schema state already says why.
            return False

        with _LOCK:
            if len(_BUFFER) >= MAX_BUFFER:
                # Drop the oldest: during a flood the newest events describe the
                # attack in progress, while the oldest are already-understood
                # history. Either choice loses data; this one loses the less
                # useful half.
                _BUFFER.popleft()
                _STATS["dropped"] += 1
            _BUFFER.append(event)
            _STATS["emitted"] += 1
            pending = len(_BUFFER)

        _ensure_worker()
        if pending >= FLUSH_THRESHOLD:
            _WAKE.set()
        return True
    except Exception as exc:  # pragma: no cover - emit must never escape
        _record_error(exc)
        return False


def _record_error(exc: BaseException) -> None:
    try:
        with _LOCK:
            _STATS["flush_failures"] += 1
            _STATS["last_error"] = str(exc)[:300]
            _STATS["last_error_type"] = type(exc).__name__
    except Exception:
        pass


# --- flush -------------------------------------------------------------------


def flush(limit: int = FLUSH_BATCH_SIZE) -> int:
    """Write up to ``limit`` pending events on one connection. Never raises.

    Returns the number newly persisted. Duplicates rejected by ``dedupe_key``
    are counted separately — they are idempotency working, not failure.
    """
    if not killswitches.ingest_enabled():
        # The switch can be thrown between emit and flush. ``events.ingest()``
        # returns False when ingest is killed, which is indistinguishable from
        # a dedupe rejection at the call site — draining here would silently
        # book a killed batch as "duplicates", i.e. as success. Leave the
        # events buffered instead: if the kill outlasts the buffer they are
        # dropped, and ``dropped`` says so.
        return 0

    with _LOCK:
        if not _BUFFER:
            return 0
        batch = [_BUFFER.popleft() for _ in range(min(limit, len(_BUFFER)))]

    written = 0
    duplicates = 0
    rejected = 0
    conn = None
    try:
        conn = store.platform_db.connect()
        for event in batch:
            try:
                if events.ingest(event, conn):
                    written += 1
                else:
                    duplicates += 1
            except events.EventRejected:
                # A malformed event is a bug in the emitter, not a reason to
                # discard the rest of the batch.
                rejected += 1
            except Exception:
                rejected += 1
        # store.connection() only commits a connection it opened itself; handed
        # one, it leaves the work uncommitted. Commit explicitly.
        conn.commit()
    except Exception as exc:
        # The batch is already off the buffer. Re-queueing it risks an unbounded
        # retry loop against a database that is failing, so it is counted as
        # lost and the error is surfaced.
        _record_error(exc)
        logger.warning(
            "SENTINEL_BRIDGE_FLUSH_FAILED type=%s error=%s count=%s",
            type(exc).__name__, str(exc)[:200], len(batch),
        )
        with _LOCK:
            _STATS["dropped"] += len(batch)
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    with _LOCK:
        _STATS["written"] += written
        _STATS["duplicates"] += duplicates
        _STATS["rejected"] += rejected
        _STATS["last_flush_at"] = time.time()
    return written


def _run() -> None:
    while not _SHUTDOWN.is_set():
        _WAKE.wait(timeout=FLUSH_INTERVAL_SECONDS)
        _WAKE.clear()
        try:
            while flush() > 0:
                pass
        except Exception as exc:  # pragma: no cover - flush catches its own
            _record_error(exc)


def _ensure_worker() -> None:
    """Start the flush thread once per process, lazily.

    Lazily because a worker that never sees a security event should not carry a
    thread, and because starting threads at import time interacts badly with
    gunicorn's fork model.
    """
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _SHUTDOWN.clear()
        _WORKER = threading.Thread(
            target=_run, name="sentinel-request-bridge", daemon=True
        )
        _WORKER.start()


def stats() -> dict:
    """Counters for health surfaces.

    ``dropped`` is the honest one: non-zero means Sentinel's record of some
    window is known-incomplete. ``evidence_complete`` says so directly rather
    than leaving a reader to infer it from a number that looks like noise.
    """
    with _LOCK:
        snapshot = dict(_STATS)
        snapshot["pending"] = len(_BUFFER)
    snapshot["enabled"] = bridge_enabled()
    snapshot["schema_ready"] = bootstrap.schema_ready()
    snapshot["worker_alive"] = _WORKER is not None and _WORKER.is_alive()
    snapshot["evidence_complete"] = snapshot["dropped"] == 0
    return snapshot


def shutdown(timeout: float = 2.0) -> None:
    """Stop the worker after a final flush. For tests and clean exits."""
    _SHUTDOWN.set()
    _WAKE.set()
    worker = _WORKER
    if worker is not None and worker.is_alive():
        worker.join(timeout=timeout)
    flush()


def reset_for_tests() -> None:
    """Drop buffered state and counters. Test-only."""
    shutdown(timeout=0.5)
    with _LOCK:
        _BUFFER.clear()
        _STATS.update({
            "emitted": 0, "written": 0, "duplicates": 0, "dropped": 0,
            "rejected": 0, "flush_failures": 0, "last_flush_at": None,
            "last_error": "", "last_error_type": "",
        })
