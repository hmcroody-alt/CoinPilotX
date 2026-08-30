"""What the run queue and its worker are doing, in counts, with nothing about anybody.

Stage 32 asks for a health surface that can answer four questions from outside the
system: is the worker configured, is it alive, is it running the same code as the web
service, and is work piling up. ``/health/undx`` already answers a version of the second
one — it reports ``worker.online`` from a heartbeat freshness check — and answers none of
the others. This module computes all four.

**Why a separate module rather than more lines in the existing handler.** Three reasons,
in ascending order of importance. It keeps :func:`bot.undx_policy_health_check` at the
size it is. It makes the computation testable without standing up Flask, which is what
lets the tests here assert on payload *shape* rather than on a rendered response. And it
leaves the snapshot importable by the worker itself, which does not import ``bot`` and
must not start.

**The privacy rule this module is written around: counts, never contents.** Every field
below is an integer, a boolean, a duration, a git sha, or a member of a fixed status
vocabulary. There are no run ids, no user ids, no capability arguments, no target ids —
and specifically no ``last_error``.

That last exclusion is the one worth stating, because ``last_error`` is exactly the field
an operator would want and exactly the field that cannot be published.
:func:`services.undx_agent_runs.execute_claimed` writes
``f"{getattr(exc, 'code', 'agent_error')}: {exc}"``, and the message half of that comes
from whatever the gateway raised. An :class:`~services.undx_agent_contracts.AgentError`
about an ownership failure can name the row it refused to touch. Publishing it on an
unauthenticated health route would turn a liveness check into a slow enumeration of other
people's data. The stable code half would be safe on its own, but this module cannot
separate the two halves from the outside without parsing a string that was never promised
to have that shape. So the payload carries the *outcome vocabulary* — the fixed set of
settled statuses — and how many runs are in each, which answers "is something failing"
without answering "what was it".

**Absent is not the same as unhealthy, and unknown is not the same as mismatched.** A
worker that has never written a heartbeat reads as ``heartbeat_present: false`` with
``online: false`` and no age, not as an error and not as an age of zero. Two shas that
cannot be compared because one of them is the string ``unknown`` read as ``sha_match:
null``, not as ``false``. Both distinctions exist because the alarming reading and the
uninformative reading are different facts, and collapsing them means the first one stops
being believed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from services import undx_agent_runs
from services import undx_worker_runtime


logger = logging.getLogger(__name__)

#: How stale a heartbeat may be before the worker is reported offline. Matches the
#: threshold ``/health/undx`` already uses, so the two surfaces cannot disagree about
#: whether the same worker is up — which they would, visibly and confusingly, on any
#: worker whose heartbeat landed between two different windows.
ONLINE_WINDOW_SECONDS = 180

#: The storage statuses reported as queue depth, in the order a reader wants them: the
#: two that mean work is outstanding first, then the settled ones. Taken from the
#: vocabularies in :mod:`services.undx_agent_runs` rather than restated, so a new status
#: there cannot silently fall out of this count.
DEPTH_STATUSES: tuple[str, ...] = (
    undx_agent_runs.CLAIMABLE_STATUSES
    + tuple(sorted(undx_agent_runs.TERMINAL_STATUSES))
)

#: The subset of terminal statuses that mean something went wrong. ``partial`` is here
#: deliberately even though it is not a failure: it records an executor that ran and a
#: read-back that could not confirm it, which is the state most worth an operator's
#: attention and the one least likely to page anybody if it were filed under success.
FAILURE_STATUSES: tuple[str, ...] = ("failed", "dead_letter", "expired", "partial")

#: Bound on the metadata blob parsed out of a heartbeat row. The worker writes it, so it
#: is not hostile input, but it is input, and a health route that can be made to parse an
#: unbounded string is a health route that can be made to stop answering.
MAX_METADATA_BYTES = 20000


def build_sha(env: Mapping[str, str] | None = None) -> str:
    """The web service's release lineage, computed exactly as the worker computes its own.

    Duplicated from :func:`undx_worker._build_sha` rather than imported, because importing
    it would pull ``undx_router`` and the provider clients into the web request that
    serves this route. The duplication is the thing the comparison is *for*: if the two
    ever diverge in how they read the environment, ``sha_match`` would report a mismatch
    between two identical deploys, and a test in ``tests/undx_agent/test_run_health.py``
    asserts the two functions read the same variables in the same order.
    """
    source = env if env is not None else os.environ
    value = source.get("RAILWAY_GIT_COMMIT_SHA") or source.get("APP_BUILD_SHA") or "unknown"
    return str(value)[:40]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Any) -> datetime | None:
    """Read one of this codebase's timestamps, or return ``None`` rather than raise.

    Timestamps here are written by two processes across possibly two backends, and one
    unparseable string must not be able to take down the health route that would tell you
    about it.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_seconds(value: Any, now: datetime) -> int | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _heartbeat_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = str(row.get("metadata_json") or "")[:MAX_METADATA_BYTES]
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _counts_by_status(cur) -> dict[str, int]:
    """One grouped read rather than one statement per status.

    The claim index is ``(status, lease_expires_at, created_at)``, so this is a scan the
    planner can satisfy from the index on both backends. Statuses absent from the table
    are filled with zero afterwards, because a health payload whose keys change depending
    on what happened to be in the queue is one nobody can build a dashboard on.
    """
    counts = {status: 0 for status in DEPTH_STATUSES}
    try:
        cur.execute("SELECT status, COUNT(*) FROM undx_agent_runs GROUP BY status")
        rows = cur.fetchall() or []
    except Exception:
        logger.debug("run health status counts failed", exc_info=True)
        return counts
    for row in rows:
        status = str(row[0] or "")
        try:
            total = int(row[1] or 0)
        except (TypeError, ValueError):
            total = 0
        # A status this module does not know about is still counted, under its own name.
        # Dropping it would make the depth total disagree with the table, and the whole
        # point of a queue depth is that it is the size of the queue.
        counts[status] = counts.get(status, 0) + total
    return counts


def _oldest_queued_age(cur, now: datetime) -> int | None:
    try:
        cur.execute(
            "SELECT created_at FROM undx_agent_runs WHERE status='queued' "
            "ORDER BY created_at ASC LIMIT 1"
        )
        row = cur.fetchone()
    except Exception:
        logger.debug("run health oldest queued read failed", exc_info=True)
        return None
    if not row:
        return None
    return _age_seconds(row[0], now)


def _active_lease_count(cur, now: datetime) -> int:
    """Runs a worker is holding *right now*, as opposed to rows that say ``running``.

    These differ precisely in the case that matters: a container that died mid-run leaves
    ``status='running'`` behind until its lease lapses. Counting those as active would
    report a busy worker on a service that is not running at all.
    """
    try:
        cur.execute(
            "SELECT lease_expires_at FROM undx_agent_runs "
            "WHERE status='running' AND lease_owner<>''"
        )
        rows = cur.fetchall() or []
    except Exception:
        logger.debug("run health active lease read failed", exc_info=True)
        return 0
    active = 0
    for row in rows:
        expires = _parse_iso(row[0])
        if expires is not None and expires > now:
            active += 1
    return active


def worker_snapshot(cur, *, env: Mapping[str, str] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Configured, alive, and running which build.

    ``configured`` is the flag surface's answer and ``online`` is the database's; they are
    reported separately because the interesting production failure is the pair
    ``configured: true, online: false`` — a service that is supposed to be draining the
    queue and is not. Collapsing them into one boolean would make that state
    indistinguishable from a feature that is simply switched off.
    """
    moment = now or _now()
    surface = undx_agent_runs.surface(env)
    row = undx_worker_runtime.read_worker_heartbeat(cur, undx_agent_runs.WORKER_NAME)
    metadata = _heartbeat_metadata(row) if row else {}
    age = _age_seconds(row.get("last_seen_at"), moment) if row else None

    # The worker's own build, as it reported it. Compared against the web service's in
    # :func:`snapshot`; two ``unknown`` values are two absences rather than a match.
    worker_sha = str(metadata.get("deployed_sha") or "unknown")[:40]

    return {
        "name": undx_agent_runs.WORKER_NAME,
        "configured": bool(surface.enabled),
        "configured_reason": str(surface.reason or ""),
        "heartbeat_present": bool(row),
        "online": bool(row) and age is not None and age <= ONLINE_WINDOW_SECONDS,
        "heartbeat_age_seconds": age,
        "heartbeat_status": str(row.get("status") or "") if row else "",
        # From the heartbeat metadata the worker itself writes, which is the only place
        # the worker's build is observable from the web service.
        "sha": worker_sha,
        "runs_enabled": bool(metadata.get("agent_runs_enabled")) if metadata else None,
        "lease_seconds": int(surface.lease_seconds),
        "max_attempts": int(surface.max_attempts),
    }


def queue_snapshot(cur, *, now: datetime | None = None) -> dict[str, Any]:
    """Depth by status, plus the two derived numbers that make depth mean something.

    A queue depth on its own does not distinguish "ten runs arrived this second" from
    "one run has been stuck since Tuesday", and the second is the outage. So the oldest
    queued run's age travels with the count.
    """
    moment = now or _now()
    counts = _counts_by_status(cur)
    outstanding = sum(counts.get(status, 0)
                      for status in undx_agent_runs.CLAIMABLE_STATUSES)
    failures = sum(counts.get(status, 0) for status in FAILURE_STATUSES)
    return {
        "depth": counts,
        "outstanding": outstanding,
        "queued": counts.get("queued", 0),
        "active_leases": _active_lease_count(cur, moment),
        "oldest_queued_age_seconds": _oldest_queued_age(cur, moment),
        "settled_needing_attention": failures,
        "total": sum(counts.values()),
    }


def snapshot(cur, *, env: Mapping[str, str] | None = None,
             now: datetime | None = None) -> dict[str, Any]:
    """The whole surface. Total, and never raises on a bad row.

    ``ok`` is deliberately narrow: it is true when the worker is either switched off on
    purpose or switched on and alive. It is *not* an assertion that the queue is empty or
    that nothing has failed, because a health check that goes red on a single failed run
    is one that gets muted, and a muted check is worse than no check.
    """
    moment = now or _now()
    web_sha = build_sha(env)
    worker = worker_snapshot(cur, env=env, now=moment)
    queue = queue_snapshot(cur, now=moment)

    worker_sha = str(worker.get("sha") or "unknown")
    comparable = worker_sha not in ("", "unknown") and web_sha not in ("", "unknown")
    sha_match: bool | None = (worker_sha == web_sha) if comparable else None

    # A configured worker that is not answering is the one condition here worth an
    # alert. Everything else on this payload is context for reading that one bit.
    ok = (not worker["configured"]) or bool(worker["online"])

    return {
        "surface": "undx-run-health-1",
        "generated_at": moment.isoformat(timespec="seconds"),
        "ok": ok,
        "web": {"sha": web_sha},
        "worker": worker,
        "sha_match": sha_match,
        "queue": queue,
    }


__all__ = [
    "DEPTH_STATUSES",
    "FAILURE_STATUSES",
    "ONLINE_WINDOW_SECONDS",
    "build_sha",
    "queue_snapshot",
    "snapshot",
    "worker_snapshot",
]
