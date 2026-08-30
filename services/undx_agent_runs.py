"""Durable agent runs: execute an approval a person already granted, after they leave.

The problem this solves is narrow and worth stating precisely, because a wider reading
of it would be dangerous. Today every governed capability completes inside the HTTP
request that asked for it. That is fine until the request ends before the work does — a
closed app, a backgrounded phone, a dropped connection — at which point the person has
approved something that then does not happen. This module is the durable half: the
request decides and the person approves; the worker executes.

**What is stored is an approval, not an intention.** A run may only be enqueued with a
``confirmation_id`` naming an approval the person granted in a request, over arguments a
*deterministic* resolver produced from their own words. The worker therefore never
selects a row, never widens a scope, and never re-decides anything. It presents the same
approval to the same gateway and lets the gateway do exactly what it does in-request.

That is why this module contains no policy. :func:`execute_claimed` calls
:func:`services.undx_tool_gateway.execute`, which runs authentication, allowlisting,
schema validation, ownership scope, deterministic policy, confirmation redemption,
idempotency, the audit reservation, the executor and the independent read-back, in that
order. A second authorisation path here — even a cautious one — would be a second thing
to keep correct, and the two would diverge on the day one of them was patched.

**No credential is stored at rest.** The run holds the confirmation *id*, never the
bearer token. The gateway's id route (``consume_approval``) applies the identical owner
scope, pending-and-unexpired predicate, action binding and argument-hash binding as the
token route; what it does not require is a secret. A stolen ``undx_agent_runs`` row is
therefore not an approval, because the id is only meaningful under the user id it was
issued to, and the worker supplies that from the row rather than from the caller.

**Failure is refusal, never a write.** An approval that expired before the worker reached
it, a run whose lease was lost, a run that has been claimed too many times — each ends
the run without executing. The one case that is genuinely ambiguous, a container dying
after the executor ran but before the outcome was recorded, is not resolved here either:
the run is re-claimed, the gateway's idempotency check finds the reserved ledger row, and
the person is told the outcome is unknown rather than having it done to them twice.

**Bounds are fixed at enqueue.** ``max_attempts`` is written onto the row when the run is
created and read from the row afterwards. Nothing at runtime may raise it — the same rule
``UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED`` exists to enforce for missions. A run
that exhausts its attempts is dead-lettered, which means it stops and stays readable,
not that it is retried more quietly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services import db as db_service
from services import undx_architecture
from services import undx_capability_registry
from services import undx_tool_gateway
from services.undx_agent_contracts import (
    AgentError, AgentOutcome, ConfirmationPolicy, RunConfirmation,
)
from services.undx_brain import config as brain_config


logger = logging.getLogger(__name__)

WORKER_NAME = "coinpilotx-undx-worker"
RUNTIME_VERSION = "durable-agent-run-v1"

#: A run is claimable in exactly these two states. ``queued`` has never been attempted;
#: ``running`` is only re-claimable once its lease has expired, which is the crashed-
#: container case rather than a second worker helping out.
CLAIMABLE_STATUSES = ("queued", "running")

#: Terminal. Nothing re-enters the queue from here, including ``dead_letter`` — a run
#: that exhausted its attempts is evidence to read, not work to retry.
#:
#: ``partial`` is terminal for the same reason and a stronger one. It records a run whose
#: executor ran and whose independent read-back did not confirm the result, so a retry
#: would not be a second try at an action that failed — it would be a second execution of
#: an action that may well have landed. The uncertainty is the finding; repeating the
#: write to resolve it is the one move guaranteed to make it worse.
TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled",
                              "dead_letter", "expired"})

#: The settled states that mean the executor was entered. Both are terminal and neither is
#: a failure; they differ only in whether the read-back could confirm what changed.
EXECUTED_STATUSES = frozenset({"succeeded", "partial"})

MAX_ARGUMENT_BYTES = 8000
MAX_ERROR_CHARS = 240

#: How long a queued run stays claimable before it lapses. Deliberately generous for a
#: read and irrelevant for a write, because a write's real deadline is its approval:
#: :func:`undx_architecture.create_confirmation` clamps every confirmation to at most 300
#: seconds, and :func:`enqueue` clamps the run to whichever of the two comes first. A run
#: that outlived its approval would be claimed, refused by the gateway and settled failed
#: — the same outcome, reached after burning an attempt and a worker pass.
DEFAULT_RUN_TTL_SECONDS = 3600

#: Bumped when the meaning of a stored row changes, not when a column is added. The
#: worker reads rows the web service wrote, possibly across a deploy, and needs to be
#: able to say "I do not understand this row" rather than to interpret it optimistically.
ENVELOPE_VERSION = "undx-run-envelope-1"

#: Aliases onto :class:`services.undx_agent_contracts.RunConfirmation`, kept because they
#: are the names the enqueue path and its tests already use. The values live in the
#: contracts module so that the projection can read the same set without importing this
#: one — see :data:`RunConfirmation.PENDING_STATES`.
CONFIRMATION_NOT_REQUIRED = RunConfirmation.NOT_REQUIRED
CONFIRMATION_GRANTED = RunConfirmation.GRANTED


@dataclass(frozen=True)
class RunSurface:
    """Whether durable runs may execute at all, and why not when they may not."""

    enabled: bool
    lease_seconds: int
    max_attempts: int
    reason: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _as_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def surface(env: Mapping[str, str] | None = None) -> RunSurface:
    """Resolve the run flags through the same catalog the web service reads.

    Deliberately not ``os.getenv`` here. The worker and the web service reaching
    different conclusions from the same deployment is the failure this whole config
    module exists to prevent, and a durable executor is the worst place to reintroduce
    it: a capability that is reachable in a request and unreachable in the worker
    produces a run that is enqueued and never executed, with nothing in either process
    saying why.
    """
    values = brain_config.resolve(dict(env) if env is not None else None).values
    lease = int(values.get("UNDX_AGENT_RUN_LEASE_SECONDS", 120))
    attempts = int(values.get("UNDX_AGENT_RUN_MAX_ATTEMPTS", 3))
    enabled = bool(values.get("UNDX_AGENT_RUNS_ENABLED", False))
    reason = ""
    # The two global stops outrank the feature flag, and are checked here as well as
    # inside the gateway. Not redundancy for its own sake: this check is what stops a
    # run being *claimed* during an emergency, so the row keeps its attempts and its
    # lease instead of burning them against a gateway that was always going to refuse.
    if bool(values.get("UNDX_EMERGENCY_KILL_SWITCH", False)):
        enabled, reason = False, "emergency_kill_switch"
    elif bool(values.get("UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED", False)):
        enabled, reason = False, "dynamic_limit_escalation_is_unsafe"
    elif not bool(values.get("UNDX_WORKER_ENABLED", False)):
        enabled, reason = False, "worker_disabled"
    elif not enabled:
        reason = "agent_runs_disabled"
    return RunSurface(enabled=enabled, lease_seconds=lease, max_attempts=attempts,
                      reason=reason)


def worker_identity() -> str:
    return f"{WORKER_NAME}:{socket.gethostname()}:{os.getpid()}"


# ---------------------------------------------------------------------------
# Versions — what the run was authorised under
# ---------------------------------------------------------------------------


_REGISTRY_VERSION: str = ""


def registry_version() -> str:
    """A fingerprint of the allowlist as this process understands it.

    Derived from the registry's own content rather than hand-maintained, because a
    hand-maintained version string is a promise somebody has to remember to keep, and the
    day it is forgotten is the day it starts lying about exactly the thing it exists to
    describe. This digest changes when — and only when — a capability's identity, risk
    class, confirmation policy, permission scope, verifier or target field changes. It
    does not change when a description is reworded, which is right: a run authorised
    under a reworded description was authorised under the same rules.

    Recorded on the row so a run executed by a different deploy than the one that queued
    it can be recognised as such. It is evidence, not a gate: refusing every run whose
    registry digest moved would make an ordinary deploy cancel work people had approved,
    and the substantive protection against drift is the risk re-check in
    :func:`execute_claimed`, which asks the live registry rather than comparing strings.
    """
    global _REGISTRY_VERSION
    if _REGISTRY_VERSION:
        return _REGISTRY_VERSION
    material = "\n".join(
        "|".join((
            spec.capability_id, spec.risk, spec.confirmation, spec.tool_name,
            spec.permission, spec.verifier, spec.target_field,
        ))
        for spec in sorted(undx_capability_registry.REGISTRY.values(),
                           key=lambda item: item.capability_id)
    )
    _REGISTRY_VERSION = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return _REGISTRY_VERSION


def policy_version() -> str:
    """The build whose policy code authorised this run.

    The deterministic policy engine is code, not data, so the honest version of it is the
    commit it was deployed from. Nothing here is computed from the flag *values*: those
    differ between the web service and the worker by configuration and would make two
    processes running identical code disagree about which policy they were running.
    """
    return (os.getenv("RAILWAY_GIT_COMMIT_SHA")
            or os.getenv("APP_BUILD_SHA")
            or "unknown")[:40]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def ensure_schema(cur) -> None:
    """Create the run table if it is absent. Idempotent, because there is no migration
    framework here and every schema change in this repo has to survive being run again.
    """
    cur.execute(
        """CREATE TABLE IF NOT EXISTS undx_agent_runs (
            run_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            capability_id TEXT NOT NULL,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            confirmation_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            correlation_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            lease_owner TEXT NOT NULL DEFAULT '',
            lease_expires_at TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            outcome TEXT NOT NULL DEFAULT '',
            receipt_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT NOT NULL DEFAULT ''
        )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_undx_agent_runs_claim "
        "ON undx_agent_runs(status, lease_expires_at, created_at)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_undx_agent_runs_owner "
        "ON undx_agent_runs(user_id, status, created_at)"
    )
    # One run per (owner, client request). The gateway already derives its idempotency
    # key from ``client_request_id``, so a duplicate row here could never write twice —
    # but it could produce two runs racing to redeem one single-use approval, and the
    # loser would report "that confirmation is no longer valid" for an action that in
    # fact succeeded. Uniqueness at the queue keeps that confusion out of the queue.
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_undx_agent_runs_request "
        "ON undx_agent_runs(user_id, client_request_id)"
    )
    # The envelope columns, added rather than redefined. Deployed databases already hold
    # rows under the original shape and there is no migration framework here, so every
    # addition carries a default and every existing row reads as "this predates the
    # envelope" instead of as a row with a missing binding. ``ENVELOPE_VERSION`` is what
    # distinguishes those two readings.
    columns = _existing_columns(cur, "undx_agent_runs")
    for name, definition in ENVELOPE_COLUMNS:
        if name not in columns:
            cur.execute(f"ALTER TABLE undx_agent_runs ADD COLUMN {name} {definition}")


#: Added after the first release of this table. Order is only for readability; each is
#: applied independently and skipped if present.
ENVELOPE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("envelope_version", "TEXT NOT NULL DEFAULT ''"),
    # The identity of the thing acted on, read from the capability's own declared
    # ``target_field``. Stored beside the arguments rather than derived from them at
    # execution time so that a run naming a target is auditable without re-entering the
    # registry — and so that a reader can see what was approved without parsing JSON.
    ("canonical_target_id", "TEXT NOT NULL DEFAULT ''"),
    # The fingerprint the approval was bound to. This is the field that makes "the person
    # confirmed *this* body" checkable rather than assumed.
    ("arguments_hash", "TEXT NOT NULL DEFAULT ''"),
    # Computed by the same function the gateway will use, so the row records the key the
    # execution will actually be deduplicated on rather than a second guess at it.
    ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
    ("confirmation_state", "TEXT NOT NULL DEFAULT ''"),
    ("policy_version", "TEXT NOT NULL DEFAULT ''"),
    ("registry_version", "TEXT NOT NULL DEFAULT ''"),
    # When this run stops being claimable. For a confirmed write this is never later than
    # the approval's own expiry.
    ("expires_at", "TEXT NOT NULL DEFAULT ''"),
    # Why this action left the request at all, as one of the stable codes in
    # :mod:`services.undx_worker_dispatch`. Recorded because "should this have been
    # queued?" is a question that gets asked about individual rows after the fact.
    ("dispatch_reason", "TEXT NOT NULL DEFAULT ''"),
)


def _existing_columns(cur, table: str) -> set[str]:
    """The column names of ``table``, on either backend.

    Mirrors :func:`services.undx_mission_runtime._columns`, including its placeholder
    style, which ``services.db`` translates per backend. Duplicated rather than imported
    because importing it would make the run queue depend on the mission runtime for a
    fact about its own table, and the two are deliberately independent kinds of work —
    see the split ``try`` blocks in ``undx_worker.py``.
    """
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# Enqueue — the request half
# ---------------------------------------------------------------------------


def approval_required(spec) -> bool:
    """Whether a run of this capability may only exist with an approval attached.

    The invariant this module enforces is not "every run was confirmed" — it is *the
    run must carry the authorisation the gateway is going to demand of it*. Those two
    read the same until you notice that 72 of the 120 declared capabilities are
    read-only with ``confirmation=never``, which means the gateway will never ask for
    an approval on them and no honest request could ever mint one. Requiring a
    confirmation id there does not make a read safer; it makes reads unqueueable, and
    a rule that can only be satisfied by inventing an approval is worse than no rule.

    So the question is asked of the capability descriptor, deterministically, at the
    only two places that matter: here, where the run is created, and again in
    :func:`execute_claimed` before the gateway is called. Asking twice is not
    belt-and-braces — the registry can be redeployed between the two, and a run
    enqueued when a capability was read-only must not execute after it became a write.
    """
    return bool(spec.is_write) or spec.confirmation == ConfirmationPolicy.ALWAYS


def _bound_approval(cur, user_id: int, confirmation_id: str, *, capability_id: str,
                    canonical_target: str, arguments_hash: str) -> dict[str, Any]:
    """The named approval, if it binds to exactly this action. Otherwise ``{}``.

    A pre-check, not a redemption, and the distinction matters in both directions. It
    does not consume the grant — the gateway does that, later, under its own rules, and
    duplicating the consuming read here would burn approvals for actions that never ran.
    And it does not replace the gateway's binding check, which is the one that decides.

    What it buys is that a mismatch is caught while the person is still present. Without
    it, an approval minted for "pause alert 12" attached to a run for "pause alert 99"
    is a row that sits in the queue looking valid, gets claimed, and fails at the gateway
    minutes later with nobody watching. The person's last signal was a confirmation card
    they tapped; the next thing they would learn is that something they approved did not
    happen, for reasons they cannot see. Refusing at enqueue turns that into an error in
    the request, which is where errors belong.

    Reads through :func:`services.undx_architecture.pending_approvals` rather than with a
    fresh query, so the owner scope, the pending-and-unexpired predicate and the
    continuation-namespace exclusion are the ones that module already enforces. A second
    hand-written ``WHERE`` here would be a second thing to keep in step with it.
    """
    wanted = str(confirmation_id or "")
    for grant in undx_architecture.pending_approvals(cur, int(user_id)):
        if str(grant.get("confirmation_id") or "") != wanted:
            continue
        if str(grant.get("action_id") or "") != str(capability_id):
            return {}
        if str(grant.get("target_id") or "") != str(canonical_target or "")[:160]:
            return {}
        if undx_architecture.argument_hash(grant.get("arguments") or {}) != arguments_hash:
            return {}
        return grant
    return {}


def enqueue(cur, *, user_id: int, capability_id: str, arguments: dict[str, Any],
            confirmation_id: str = "", client_request_id: str, correlation_id: str = "",
            dispatch_reason: str = "", env: Mapping[str, str] | None = None) -> str:
    """Record an approved action for later execution. Returns the run id.

    Called from a request, after a person has approved the action and while their own
    words are still available to the resolver that produced ``arguments``. Every
    argument to this function is load-bearing and none of it may come from a model:

    ``arguments`` must be the output of the deterministic resolver, not a planner's
    advisory suggestion. This function cannot tell the difference and does not try —
    the guarantee lives at the call site, and the reason it lives there is that the
    resolver has the person's sentence and this function does not.

    ``confirmation_id`` must name an approval that already exists whenever the capability
    is one the gateway will demand an approval for, and that approval must be bound to
    *this* capability, *this* canonical target and *this* argument hash. That binding is
    checked here without being consumed here — the gateway still redeems it later, under
    its own rules, and that second reading remains the one that decides.

    Checking early does not weaken the late check; it changes what a mismatch costs. An
    approval bound to a different target, discovered at execution, has already spent a
    claim and an attempt and is reported to a person who is no longer present. Discovered
    at enqueue, it is a refusal returned into the request that caused it, while they can
    still confirm the right thing.

    What is enforced either way is that there is no shape of this row that represents
    "write to this person's account, nobody approved it".
    """
    if int(user_id or 0) <= 0:
        raise AgentError("unauthenticated", "Sign in to let UNDX do that.",
                         outcome=AgentOutcome.PERMISSION_DENIED)
    capability_id = str(capability_id or "").strip()
    spec = undx_capability_registry.get(capability_id) if capability_id else None
    if spec is None:
        # Refused at the queue as well as at the gateway. A row naming a capability
        # that does not exist is a row that can only ever be executed by a registry
        # that gains it later, which is precisely the drift this refusal prevents.
        raise AgentError("unsupported_capability", "UNDX cannot do that.",
                         outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
                         details={"capability_id": capability_id})
    confirmation_id = str(confirmation_id or "").strip()
    if approval_required(spec) and not confirmation_id:
        # The invariant of this module, stated as a refusal rather than a default. An
        # unconfirmed write is not a run with a missing field; it is a request to write
        # to somebody's account because software decided to.
        raise AgentError(
            "unconfirmed_run",
            "UNDX will not queue that without your confirmation.",
            outcome=AgentOutcome.PERMISSION_DENIED,
            details={"capability_id": capability_id},
        )
    client_request_id = str(client_request_id or "").strip()
    if not client_request_id:
        raise AgentError(
            "missing_request_id",
            "UNDX could not queue that safely.",
            outcome=AgentOutcome.PERMISSION_DENIED,
            details={"capability_id": capability_id},
        )

    encoded = json.dumps(dict(arguments or {}), sort_keys=True)
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise AgentError(
            "arguments_too_large", "UNDX could not queue that safely.",
            outcome=AgentOutcome.PERMISSION_DENIED,
            details={"capability_id": capability_id, "bytes": len(encoded)},
        )

    ensure_schema(cur)

    # The envelope, assembled from the capability's own declarations rather than from
    # anything the caller passed alongside the arguments. A caller that could supply the
    # canonical target directly could name a different row than the one the arguments act
    # on, and every downstream reader — the confirmation binding, the idempotency key,
    # the audit trail — would agree with the caller against the action.
    canonical_target = spec.canonical_target(arguments or {})
    arguments_hash = undx_architecture.argument_hash(arguments or {})
    prepared = undx_architecture.prepare_tool_operation(
        int(user_id), spec.tool_name, client_request_id, canonical_target,
    )

    confirmation_state = CONFIRMATION_NOT_REQUIRED
    deadline = _now() + timedelta(seconds=DEFAULT_RUN_TTL_SECONDS)
    if confirmation_id:
        grant = _bound_approval(cur, int(user_id), confirmation_id,
                                capability_id=capability_id,
                                canonical_target=canonical_target,
                                arguments_hash=arguments_hash)
        if not grant:
            # Indistinguishable on purpose, exactly as the redemption paths are: unknown,
            # expired, spent, foreign and bound-to-something-else all arrive here as one
            # refusal, so a caller probing with guessed ids learns nothing about which.
            raise AgentError(
                "confirmation_not_bound",
                "That confirmation does not match what UNDX was about to do. "
                "Ask again and confirm the new one.",
                outcome=AgentOutcome.PERMISSION_DENIED,
                details={"capability_id": capability_id},
            )
        confirmation_state = CONFIRMATION_GRANTED
        # A run must not outlive the approval that authorises it. Confirmations are
        # clamped to 300 seconds at mint, so this is usually the binding deadline; taking
        # the earlier of the two rather than the confirmation's alone keeps the rule
        # correct if that clamp ever changes.
        deadline = min(deadline, _parse(grant.get("expires_at")) or deadline)

    # The same tap, arriving twice. A phone that retries a request whose response it
    # never saw must not acquire a second run: the first one is still queued and will
    # still execute, so a second row would be the same action performed twice, out of
    # sight, with the person having asked once.
    #
    # The unique index alone would prevent the row but would surface as an integrity
    # error — a fault, which callers treat as "queueing is broken" and fall back from.
    # Returning the run that already exists is the answer that matches what actually
    # happened, and it is the definition of the idempotency ``client_request_id`` is
    # carried for.
    #
    # The envelope is compared before that answer is given. A reused
    # ``client_request_id`` naming a *different* capability or a different body is not a
    # retry, it is a collision, and handing back the earlier run would report progress on
    # an action nobody asked for the second time.
    existing = find_by_request(cur, int(user_id), client_request_id)
    if existing:
        if (str(existing.get("capability_id") or "") == capability_id
                and str(existing.get("arguments_hash") or "") == arguments_hash):
            logger.info("UNDX_RUN_DEDUPED run_id=%s user_id=%s capability_id=%s",
                        existing.get("run_id"), int(user_id), capability_id)
            return str(existing.get("run_id") or "")
        raise AgentError(
            "request_id_reused",
            "UNDX could not queue that safely.",
            outcome=AgentOutcome.PERMISSION_DENIED,
            details={"capability_id": capability_id},
        )

    limits = surface(env)
    run_id = f"run_{uuid.uuid4().hex}"
    moment = _iso()
    cur.execute(
        """INSERT INTO undx_agent_runs
        (run_id, user_id, capability_id, arguments_json, confirmation_id,
         client_request_id, correlation_id, status, max_attempts, created_at, updated_at,
         envelope_version, canonical_target_id, arguments_hash, idempotency_key,
         confirmation_state, policy_version, registry_version, expires_at, dispatch_reason)
        VALUES (?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, int(user_id), capability_id, encoded, confirmation_id,
         client_request_id, str(correlation_id or ""), int(limits.max_attempts),
         moment, moment,
         ENVELOPE_VERSION, canonical_target, arguments_hash,
         str(prepared["idempotency_key"]), confirmation_state,
         policy_version(), registry_version(), _iso(deadline),
         str(dispatch_reason or "")),
    )
    logger.info("UNDX_RUN_ENQUEUED run_id=%s user_id=%s capability_id=%s target=%s "
                "confirmation=%s expires_at=%s",
                run_id, int(user_id), capability_id, canonical_target,
                confirmation_state, _iso(deadline))
    return run_id


# ---------------------------------------------------------------------------
# Claim — the worker half
# ---------------------------------------------------------------------------


def claim_next(cur, worker_id: str, *, env: Mapping[str, str] | None = None,
               now: datetime | None = None) -> dict[str, Any] | None:
    """Take ownership of exactly one run, or return ``None``.

    The claim is a compare-and-swap on ``(run_id, status, updated_at)``, matching the
    pattern :mod:`services.undx_mission_runtime` already uses. Two containers reaching
    the same row produce one ``rowcount == 1`` and one ``rowcount == 0``; the loser
    moves on rather than waiting, so neither blocks. ``FOR UPDATE SKIP LOCKED`` would
    be the Postgres-native spelling and is deliberately not used: it does not exist in
    SQLite, and a claim path that only runs one way in production is a claim path whose
    race is only ever tested in production.
    """
    limits = surface(env)
    if not limits.enabled:
        return None
    ensure_schema(cur)
    moment = now or _now()
    cur.execute(
        "SELECT * FROM undx_agent_runs WHERE status IN ('queued','running') "
        "ORDER BY created_at ASC LIMIT 25"
    )
    for raw in cur.fetchall():
        run = _as_dict(raw)
        status = str(run.get("status") or "")
        if status == "running" and not _lease_expired(run.get("lease_expires_at"), moment):
            continue
        if _run_expired(run.get("expires_at"), moment):
            # Settled before the attempt is spent, for the same reason the dead-letter
            # branch below settles before executing: a run whose approval has lapsed will
            # be refused by the gateway, and reaching that refusal by way of a claim
            # costs an attempt and tells the person nothing the deadline did not already.
            #
            # Ahead of the parked check on purpose. A run waiting on a person still has a
            # deadline, and a deadline that a parked run were exempt from would leave it
            # in the scan window forever — waiting, unanswerable, and taking up one of the
            # twenty-five slots this query looks at.
            _settle(cur, run, worker_id="", status="expired",
                    error="run_deadline_passed", require_lease=False)
            continue
        if awaiting_person(run):
            # Stage 18, enforced at the only place that could violate it. A run parked on
            # an unanswered confirmation is not slow work, it is *not work* — there is
            # nothing a worker can advance until a person acts. Claiming it would take a
            # lease, spend one of a fixed allowance of attempts, and hand the gateway an
            # approval that does not exist, so the run would burn itself down to
            # ``dead_letter`` while the person it is waiting on is still deciding.
            #
            # Skipped rather than settled: unlike the branches on either side, nothing
            # about this row is wrong. It is passed over and left exactly as it is, which
            # is what "occupies no worker" has to mean if the person is ever going to get
            # the chance to answer.
            continue
        attempts = int(run.get("attempt_count") or 0)
        max_attempts = max(1, int(run.get("max_attempts") or limits.max_attempts))
        if attempts >= max_attempts:
            # Dead-lettered on the claim rather than after another execution. A run
            # that has already been attempted its full allowance has, by definition,
            # possibly executed — retrying it is the one thing that could turn an
            # uncertain single write into a certain double one.
            _settle(cur, run, worker_id="", status="dead_letter",
                    error="max_attempts_exhausted", require_lease=False)
            continue
        expiry = _iso(moment + timedelta(seconds=limits.lease_seconds))
        cur.execute(
            "UPDATE undx_agent_runs SET status='running', lease_owner=?, "
            "lease_expires_at=?, attempt_count=attempt_count+1, updated_at=? "
            "WHERE run_id=? AND status=? AND updated_at=?",
            (worker_id, expiry, _iso(moment), run["run_id"], status,
             run.get("updated_at")),
        )
        if cur.rowcount == 1:
            run.update({"status": "running", "lease_owner": worker_id,
                        "lease_expires_at": expiry,
                        "attempt_count": attempts + 1})
            return run
    return None


def awaiting_person(run: Mapping[str, Any]) -> bool:
    """Whether this run is stopped on a person rather than on capacity.

    Public because two very different modules need the same answer and must not each
    write it: this one, to refuse the claim, and
    :mod:`services.undx_run_status`, to say "waiting for you" instead of "queued". The
    spellings themselves live in :class:`RunConfirmation` so neither module owns them.
    """
    return (str(run.get("confirmation_state") or "").strip().lower()
            in RunConfirmation.PENDING_STATES)


def _parse(raw: Any) -> datetime | None:
    """A stored timestamp as an aware datetime, or ``None`` if it cannot be read."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _lease_expired(raw: Any, now: datetime) -> bool:
    """An unreadable or absent lease counts as expired.

    Failing the other way would strand a run permanently on a single malformed
    timestamp, and the reclaim is not itself dangerous: it leads to the gateway, which
    refuses a spent approval and reports an unsettled ledger row rather than repeating
    a write.
    """
    parsed = _parse(raw)
    return True if parsed is None else parsed <= now


def _run_expired(raw: Any, now: datetime) -> bool:
    """Whether the run's own deadline has passed.

    The opposite default to :func:`_lease_expired`, and deliberately so. An unreadable
    lease should be treated as free, because the cost of being wrong is a re-claim that
    the gateway will police. An unreadable *run* deadline treated as lapsed would cancel
    work a person approved on the strength of a malformed string, so an unreadable
    deadline is treated as no deadline and the run proceeds to the gateway, which knows
    when the approval expires and refuses on that.
    """
    parsed = _parse(raw)
    return False if parsed is None else parsed <= now


def _settle(cur, run: Mapping[str, Any], *, worker_id: str, status: str,
            error: str = "", outcome: str = "",
            receipt: Mapping[str, Any] | None = None,
            require_lease: bool = True) -> None:
    """Move a run to a terminal state and release its lease.

    ``require_lease`` scopes the update to the holder, so a worker whose lease expired
    mid-execution cannot overwrite the state written by whoever reclaimed the run. The
    dead-letter path on claim is the one caller that legitimately holds no lease.
    """
    completed = _iso() if status in TERMINAL_STATUSES else ""
    params: list[Any] = [
        status, str(outcome or ""),
        json.dumps(dict(receipt or {}), sort_keys=True),
        str(error or "")[:MAX_ERROR_CHARS], completed, _iso(), run["run_id"],
    ]
    sql = ("UPDATE undx_agent_runs SET status=?, outcome=?, receipt_json=?, "
           "last_error=?, completed_at=?, lease_owner='', lease_expires_at='', "
           "updated_at=? WHERE run_id=?")
    if require_lease:
        sql += " AND lease_owner=?"
        params.append(worker_id)
    cur.execute(sql, tuple(params))


# ---------------------------------------------------------------------------
# Execute — through the one gateway, with nothing added
# ---------------------------------------------------------------------------


def execute_claimed(cur, run: Mapping[str, Any], worker_id: str) -> dict[str, Any]:
    """Run one claimed action through the governed gateway.

    Everything this function decides is bookkeeping. The action itself is decided by
    :func:`services.undx_tool_gateway.execute`, which is handed the owner from the row,
    the capability from the row, the arguments from the row and the approval id from
    the row — and reaches its own conclusion about every one of them.
    """
    run_id = str(run.get("run_id") or "")
    if not run_id or str(run.get("lease_owner") or "") != worker_id:
        return {"executed": False, "reason": "lease_not_owned"}

    user_id = int(run.get("user_id") or 0)
    capability_id = str(run.get("capability_id") or "")
    try:
        arguments = json.loads(str(run.get("arguments_json") or "{}"))
    except (TypeError, ValueError):
        arguments = None
    if not isinstance(arguments, dict):
        # Unparseable stored arguments cannot be repaired and must not be guessed at.
        # An empty dict would validate cleanly against a schema whose fields are all
        # optional and execute a different action than the one approved.
        _settle(cur, run, worker_id=worker_id, status="failed",
                error="arguments_unreadable")
        return {"executed": False, "run_id": run_id, "status": "failed",
                "reason": "arguments_unreadable"}

    # Re-asked here, against the registry this process has loaded, rather than trusted
    # from the enqueue that wrote the row. The two are separated by an unbounded amount
    # of time and at least one deploy. A run queued while ``reels.get`` was read-only
    # must not execute if a later release made it a write, and the row itself cannot
    # know that happened — it only stores the capability id.
    spec = undx_capability_registry.get(capability_id)
    if spec is None:
        _settle(cur, run, worker_id=worker_id, status="failed",
                error="unsupported_capability")
        return {"executed": False, "run_id": run_id, "status": "failed",
                "reason": "unsupported_capability"}

    confirmation_id = str(run.get("confirmation_id") or "")
    if approval_required(spec) and not confirmation_id:
        _settle(cur, run, worker_id=worker_id, status="failed",
                error="unconfirmed_run")
        return {"executed": False, "run_id": run_id, "status": "failed",
                "reason": "unconfirmed_run"}

    try:
        outcome = undx_tool_gateway.execute(
            cur,
            user_id=user_id,
            capability_id=capability_id,
            proposed_arguments=arguments,
            # The run id is the request id. It is stable across every re-claim, which
            # is what makes a crashed run resumable instead of repeatable: the gateway
            # derives its idempotency key from ``client_request_id`` and the canonical
            # target, so the second attempt meets its own reserved ledger row.
            request_id=run_id,
            task_id=run_id,
            client_request_id=str(run.get("client_request_id") or run_id),
            correlation_id=str(run.get("correlation_id") or run_id),
            # The id, never a token. Nothing in this table is a credential.
            confirmation_id=confirmation_id,
            # Truthful: a person pressed Confirm on this exact action. The flag says
            # the action was asked for explicitly, and it was — in the request that
            # enqueued this run.
            explicit_request=True,
            # Also truthful, and the more important of the two. A deterministic
            # resolver chose this target in the request; had a model chosen it, this
            # would be ``True`` and the policy engine would treat the whole run as the
            # riskier thing it is.
            target_chosen_by_agent=False,
        )
    except AgentError as exc:
        # A typed refusal is a settled answer, not an incident. Retrying an
        # ``unsupported_capability`` or a ``permission_denied`` produces the same
        # refusal and burns an attempt.
        _settle(cur, run, worker_id=worker_id, status="failed",
                error=f"{getattr(exc, 'code', 'agent_error')}: {exc}",
                outcome=getattr(exc, "outcome", "") or AgentOutcome.PERMISSION_DENIED)
        return {"executed": False, "run_id": run_id, "status": "failed",
                "reason": clean_code(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        # Untyped and therefore not known to be safe to repeat. The lease is released
        # so the run can be re-claimed, and the gateway's idempotency check — not this
        # handler — decides whether the retry is allowed to reach an executor.
        logger.exception("UNDX_RUN_UNEXPECTED_ERROR run_id=%s", run_id)
        cur.execute(
            "UPDATE undx_agent_runs SET status='queued', lease_owner='', "
            "lease_expires_at='', last_error=?, updated_at=? "
            "WHERE run_id=? AND lease_owner=?",
            (f"{exc.__class__.__name__}"[:MAX_ERROR_CHARS], _iso(), run_id, worker_id),
        )
        return {"executed": False, "run_id": run_id, "status": "queued",
                "reason": "unexpected_error"}

    receipt = outcome.receipt
    status_word = str(getattr(receipt, "status", "") or "")
    settled = _settled_status(outcome, status_word)
    _settle(cur, run, worker_id=worker_id, status=settled,
            outcome=status_word,
            receipt={"status": status_word,
                     "capability_id": capability_id,
                     "explanation": str(getattr(receipt, "explanation", "") or "")[:400]},
            # ``partial`` carries the outcome in ``last_error`` alongside ``failed``,
            # because "we could not confirm this" is the thing a support engineer needs
            # to see first and an empty error field reads as "nothing went wrong".
            error="" if settled == "succeeded" else status_word)
    logger.info("UNDX_RUN_SETTLED run_id=%s user_id=%s capability_id=%s status=%s outcome=%s",
                run_id, user_id, capability_id, settled, status_word)
    return {"executed": True, "run_id": run_id, "status": settled,
            "outcome": status_word, "capability_id": capability_id}


def _settled_status(outcome: Any, status_word: str) -> str:
    """Which terminal state a finished gateway call lands in. Three, not two.

    **Stage 21 — only a verified success is a success.** ``succeeded`` is reached from
    exactly one place: :attr:`~services.undx_tool_gateway.GatewayOutcome.succeeded`, which
    the gateway computes after reconciling its executor's answer against an independent
    read of the account. Recomputing that here from the status string would be a second
    rule for the same question, and the two would disagree the first time either was
    patched.

    **Read as an attribute, not through ``getattr``.** This line used to be
    ``getattr(outcome, "may_claim_completed", False)``, naming a field ``GatewayOutcome``
    does not have — it carries ``succeeded`` and ``may_claim_done``, and its ``__slots__``
    make the absence permanent. The default silently answered "no" for every run the queue
    ever settled, so no run of any kind could reach ``succeeded``: a verified write and a
    clean read alike were written down as ``failed`` and reported to the person as "This
    did not happen." The lifecycle tests did not catch it because their stub outcome was
    built around the missing name rather than the real one, which is the general argument
    against a defensive ``getattr`` across a contract boundary — it converts a loud
    ``AttributeError`` into a quiet wrong answer, and here the quiet wrong answer was the
    system telling users their completed work had failed.

    **Stage 20 — and a failure is not the only other answer.** Before this, everything
    that was not a completion was written down as ``failed``, which for
    ``accepted_unverified`` is a false statement in the direction nobody watches for. That
    outcome means the executor ran and the read-back could not confirm what changed: the
    write may well have landed. Recording it as ``failed`` tells the person their request
    did not happen, invites them to do it again by hand, and turns an uncertain single
    write into a probable double one. ``partial`` is the honest row — it ran, and what it
    did is unconfirmed — and it is terminal, because a retry is precisely the wrong way to
    resolve that uncertainty.

    Only ``ACCEPTED_UNVERIFIED`` reaches ``partial``. That is one named constant rather
    than a family test, on purpose: every other non-success outcome in the enum is a
    refusal that happened *before* an executor ran — permission denied, unsupported
    capability, a confirmation that had already lapsed — and calling any of those partial
    would claim an execution that never occurred. The rounding has to be unavailable in
    both directions or it is not a rule.
    """
    if outcome.succeeded:
        return "succeeded"
    if status_word == AgentOutcome.ACCEPTED_UNVERIFIED:
        return "partial"
    # ``CONFIRMATION_REQUIRED`` arrives here and settles as ``failed`` rather than parking
    # the run for an answer. The approval this run carried expired, was revoked, or was
    # spent elsewhere; the person is no longer standing behind the action, and a run that
    # waited would be waiting for an approval that is never coming.
    return "failed"


def clean_code(exc: Exception) -> str:
    return str(getattr(exc, "code", "") or exc.__class__.__name__)[:60]


# ---------------------------------------------------------------------------
# Cancel — the person changing their mind
# ---------------------------------------------------------------------------


#: What :func:`cancel_for_user` did, as a stable code. Returned rather than raised because
#: none of these are errors — three of the four are ordinary things that happen when
#: somebody taps Cancel a moment later than they meant to.
CANCEL_DONE = "cancelled"
CANCEL_NOT_FOUND = "not_found"
CANCEL_ALREADY_SETTLED = "already_settled"
CANCEL_IN_FLIGHT = "in_flight"


def cancel_for_user(cur, user_id: int, run_id: str) -> str:
    """Stop a run this person owns, before anything has been executed.

    Stage 19, and the whole of the design is in what it refuses.

    **Only a run that has never been claimed can be cancelled.** The compare-and-swap
    below matches ``status='queued'`` and nothing else. A run a worker is executing right
    now cannot be recalled — there is no message that reaches inside
    :func:`services.undx_tool_gateway.execute` and un-sends a request that is already in
    flight — and answering "cancelled" to somebody whose block or payment then lands is a
    worse outcome than answering "too late". So a claimed run returns
    :data:`CANCEL_IN_FLIGHT` and the person is told to watch it instead.

    **A lapsed lease is still in flight.** A row that says ``running`` with a dead lease
    looks abandoned and is exactly the case where cancelling is most tempting and least
    safe: the container may have died *after* the executor returned and before the outcome
    was written. Nothing here can distinguish that from a container that died before
    starting, so both are refused, and the reclaim path settles them on evidence instead.

    **The owner is in the statement.** Same rule as every other accessor on this table: a
    valid run id under the wrong account updates zero rows rather than one, and a run that
    does not exist and a run belonging to somebody else are the same answer.

    Returns one of the four ``CANCEL_*`` codes. Nothing is raised, because "you were a
    second too late" is not an exception.
    """
    ensure_schema(cur)
    wanted = str(run_id or "")
    cur.execute(
        "UPDATE undx_agent_runs SET status='cancelled', last_error='cancelled_by_user', "
        "completed_at=?, lease_owner='', lease_expires_at='', updated_at=? "
        "WHERE run_id=? AND user_id=? AND status='queued'",
        (_iso(), _iso(), wanted, int(user_id)),
    )
    if cur.rowcount == 1:
        logger.info("UNDX_RUN_CANCELLED run_id=%s user_id=%s", wanted, int(user_id))
        return CANCEL_DONE
    # Zero rows updated has three meanings and the person is owed the difference: there
    # is nothing to cancel, it already finished, or it is happening now. Read back
    # through the owner-scoped accessor rather than with a second hand-written ``WHERE``,
    # so the scoping rule is stated in one place.
    row = get_for_user(cur, int(user_id), wanted)
    if not row:
        return CANCEL_NOT_FOUND
    stored = str(row.get("status") or "")
    if stored in TERMINAL_STATUSES:
        return CANCEL_ALREADY_SETTLED
    return CANCEL_IN_FLIGHT


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------


def poll_once(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Claim and execute at most one run. One per pass, deliberately.

    A loop here would hold a database connection across an unbounded number of real
    mutations and would make a SIGTERM arriving mid-batch ambiguous. One run per pass
    costs latency, which is the cheap resource in a flow whose premise is that nobody
    is waiting.
    """
    limits = surface(env)
    if not limits.enabled:
        return {"enabled": False, "reason": limits.reason, "executed": False}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        run = claim_next(cur, worker_identity(), env=env)
        if not run:
            conn.commit()
            return {"enabled": True, "executed": False, "reason": "no_eligible_run"}
        # The claim is committed before the executor is reached. Without this, a
        # container that dies mid-execution rolls the claim back too, and the run
        # returns to the queue as ``queued`` with attempt_count zero — an unbounded
        # crash loop over a write that may already have landed.
        conn.commit()
        outcome = execute_claimed(cur, run, str(run["lease_owner"]))
        conn.commit()
        return {"enabled": True, **outcome}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Read-back for the client
# ---------------------------------------------------------------------------


def find_by_request(cur, user_id: int, client_request_id: str) -> dict[str, Any] | None:
    """The run this person already has for this client request, if any.

    Owner-scoped in the statement, like everything else that reads this table. The pair
    ``(user_id, client_request_id)`` is the table's unique key, so this returns at most
    one row without needing a ``LIMIT`` to make that true.

    Returns the envelope fields a caller needs to decide whether a repeat is the *same*
    request rather than a reused id, which is a question no caller should answer by
    assuming.
    """
    ensure_schema(cur)
    cur.execute(
        "SELECT run_id, capability_id, canonical_target_id, arguments_hash, status, "
        "outcome, confirmation_state, dispatch_reason, created_at, expires_at "
        "FROM undx_agent_runs WHERE user_id=? AND client_request_id=?",
        (int(user_id), str(client_request_id or "")),
    )
    row = cur.fetchone()
    return _as_dict(row) if row else None


#: What a client is allowed to read off a run. Named once and shared by both readers so
#: that "the detail view shows more than the list" can never quietly become "the detail
#: view shows the confirmation id".
#:
#: The omissions are the point, and they are the same three in both views.
#: ``confirmation_id`` is a handle to a live approval — possessing it is most of what it
#: takes to redeem one, and no view a browser can reach has a reason to hold it.
#: ``lease_owner`` names a container and is operator information, not the person's.
#: ``arguments_json`` is the body the person's own words produced, but it is echoed back
#: through a different trust boundary than the one it entered by, so it stays server-side
#: and ``arguments_hash`` carries the identity instead.
READABLE_COLUMNS = (
    "run_id", "capability_id", "canonical_target_id", "arguments_hash", "status",
    "outcome", "confirmation_state", "dispatch_reason", "last_error", "attempt_count",
    "max_attempts", "envelope_version", "policy_version", "registry_version",
    "lease_expires_at", "created_at", "updated_at", "completed_at", "expires_at",
)


def get_for_user(cur, user_id: int, run_id: str) -> dict[str, Any] | None:
    """One run, or ``None`` — and ``None`` for somebody else's run too.

    The owner is part of the ``WHERE`` clause rather than a check applied to a row that
    was already fetched. The difference matters: a filter written afterwards is a filter
    somebody can forget, and its failure mode is returning a stranger's row. Here there
    is no arrangement of arguments that loads the row at all.

    A run that does not exist and a run that belongs to another account are deliberately
    the same answer. Distinguishing them would turn this endpoint into an oracle for
    whether a given run id is real, which is a small leak that costs nothing to close.
    """
    ensure_schema(cur)
    cur.execute(
        f"SELECT {', '.join(READABLE_COLUMNS)} "
        "FROM undx_agent_runs WHERE run_id=? AND user_id=?",
        (str(run_id or ""), int(user_id)),
    )
    row = cur.fetchone()
    return _as_dict(row) if row else None


def for_user(cur, user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """The runs a person may see: their own, newest first.

    Scoped by ``user_id`` in the statement rather than filtered afterwards, so there is
    no arrangement of arguments that returns somebody else's row to be discarded later.
    """
    ensure_schema(cur)
    cur.execute(
        # A named column list, not ``SELECT *``, and shared with :func:`get_for_user` so
        # that adding a column to this table cannot silently add it to what a client can
        # read. See :data:`READABLE_COLUMNS` for what is withheld and why.
        f"SELECT {', '.join(READABLE_COLUMNS)} "
        "FROM undx_agent_runs WHERE user_id=? "
        "ORDER BY created_at DESC LIMIT ?",
        (int(user_id), max(1, min(int(limit or 20), 100))),
    )
    return [_as_dict(row) for row in cur.fetchall()]


__all__ = [
    "CANCEL_ALREADY_SETTLED",
    "CANCEL_DONE",
    "CANCEL_IN_FLIGHT",
    "CANCEL_NOT_FOUND",
    "CLAIMABLE_STATUSES",
    "CONFIRMATION_GRANTED",
    "CONFIRMATION_NOT_REQUIRED",
    "DEFAULT_RUN_TTL_SECONDS",
    "ENVELOPE_COLUMNS",
    "ENVELOPE_VERSION",
    "EXECUTED_STATUSES",
    "READABLE_COLUMNS",
    "RUNTIME_VERSION",
    "RunSurface",
    "TERMINAL_STATUSES",
    "WORKER_NAME",
    "approval_required",
    "awaiting_person",
    "cancel_for_user",
    "claim_next",
    "ensure_schema",
    "enqueue",
    "execute_claimed",
    "find_by_request",
    "for_user",
    "get_for_user",
    "poll_once",
    "policy_version",
    "registry_version",
    "surface",
    "worker_identity",
]
