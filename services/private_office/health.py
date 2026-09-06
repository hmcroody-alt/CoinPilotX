"""Stage 36 — the production health surface for the Private Office.

What this answers, and why those four things
--------------------------------------------
An operator looking at a Private Office incident has to be able to separate
four failures that all present to a member as "it did not know that":

* the **entitlement resolver** is down, so the member is being treated as FREE;
* the **schema** is not on this database, so there is nowhere to look;
* the **fact store** or **graph** is empty, so there is nothing to look at;
* **retrieval** itself is refusing, so there is something to look at and a
  policy is correctly declining to show it.

Those need four different responses and only the third is ever "wait". Stage
176B is the reason this is stated so bluntly: a sweeper reporting
``{'scanned': 0, 'released': 0, 'failed': 1}`` looked exactly like a healthy
sweep of an empty queue while an inventory leak ran for weeks. So every count
here is either an integer or ``None``, and ``None`` means *we could not count*,
never *there are none*. The two are never collapsed.

What it deliberately will not tell you
--------------------------------------
:func:`private_office_health` takes **no user identifier**, and there is no
argument that would make it take one. That is the same shape as
``status.subsystem_status`` and for the same reason: a health endpoint that
accepts a user id is an oracle, and an oracle behind an admin check is one
credential away from being an oracle. Nothing here returns a fact value, a
subject id, a node id, an external reference, an email, a name, or a row of any
kind. Totals across all owners are aggregates — "there are 4,102 private facts"
says nothing about any member — and even those are only reachable by an
operator, because volume is commercially sensitive.

It is also strictly read-only. There is no ensure, no repair, no backfill and
no write path. A health check that fixes things cannot be run safely during an
incident, which is the one time anyone wants to run it.

Cost
----
One ``SELECT COUNT(*)`` per private table — the substrate tables, the six
meetings tables and the six operations tables — plus whatever
``status.subsystem_status`` costs when included. The counts are unfiltered, so
on a large table PostgreSQL will do a sequential scan; that is why
:func:`private_office_health` takes ``include_counts`` and why the default is
to include them but the caller can turn them off for a liveness probe that runs
every few seconds. The retrieval section and the operations *policy* block cost
nothing at all — they report the configured bounds, the intent catalog, the
deadline semantics and the attention ranking, all of which are constants in
code, because "what would this deployment do" is answerable without asking the
database anything. Those two survive ``include_counts=False`` and a dead
database alike, which is deliberate: during an incident they are the half of
the payload most likely to identify the fault.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from services import db as _db
from services.private_office import facts as _facts
from services.private_office import feature_matrix as _fm
from services.private_office import model as _model
from services.private_office import retrieval as _retrieval
from services.private_office import schema as _schema
from services.private_office import status as _status
from services.private_office import telemetry as _telemetry
from services.private_office import tiers as _tiers

_log = logging.getLogger("private_office.health")

STATE_HEALTHY = "healthy"
STATE_DEGRADED = "degraded"
STATE_UNAVAILABLE = "unavailable"

#: Implementation state of the substrate itself, as opposed to of a feature.
#: Reported so a reader is never left inferring "is this built?" from a count
#: of zero — which is the inference Stage 176B punished.
IMPL_LIVE = "LIVE"
IMPL_NOT_READY = "NOT_READY"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count(cur, table: str) -> int | None:
    """Total rows in ``table``, or ``None`` if the count did not happen.

    Read-only and deliberately unqualified by owner: this is a volume figure,
    not a lookup. There is no argument that would let a caller narrow it to a
    person, which is what keeps it an aggregate rather than a probe.
    """
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        if row is None:
            return None
        value = row["count"] if hasattr(row, "keys") and "count" in row.keys() else row[0]
        return int(value or 0)
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_COUNT_FAILED table=%s", table)
        return None


def _schema_section(cur) -> dict:
    """Stage 34/35 — is the schema on *this* database, from *this* process?

    Calls ``ensure_private_schema`` rather than ``require_private_schema``
    because a health check must report a broken schema, not raise on it. The
    ensure is idempotent and its DDL is all ``IF NOT EXISTS``, so this is a
    probe rather than a repair — but it is honest that it is not purely passive,
    which is why ``bootstrapped_by`` is reported alongside.
    """
    try:
        result = _schema.ensure_private_schema(cur)
    except Exception as exc:  # noqa: BLE001 — ensure is documented never to raise.
        _log.exception("PRIVATE_HEALTH_SCHEMA_PROBE_RAISED")
        return {
            "state": _schema.STATUS_ERROR,
            "usable": False,
            "tables_present": [],
            "missing": {},
            "bootstrapped_by": _schema.process_role(),
            "error_type": type(exc).__name__,
        }

    return {
        "state": result["status"],
        "usable": result["status"] == _schema.STATUS_READY,
        "tables_present": sorted(result.get("tables") or {}),
        # Column names of a table this package owns — schema, not member data.
        "missing": result.get("missing") or {},
        "bootstrapped_by": _schema.process_role(),
        # The message itself is a database error string and may name a role, a
        # host or a path, so only its presence is reported.
        "error_type": "error" if result.get("error") else "",
    }


def _substrate_section(cur, *, schema_usable: bool, include_counts: bool) -> dict:
    """Fact store and graph: are they built, and how much is in them.

    Counts are skipped — reported as ``None`` — when the schema is not usable,
    because a count against a table that may not exist would either raise or
    return 0, and a 0 here is the exact lie this module exists to prevent.
    """
    tables = {
        "facts": _schema.FACTS_TABLE,
        "nodes": _schema.NODES_TABLE,
        "edges": _schema.EDGES_TABLE,
        "audit_events": _schema.AUDIT_TABLE,
    }
    counts: dict[str, int | None] = {name: None for name in tables}
    if schema_usable and include_counts:
        for name, table in tables.items():
            counts[name] = _count(cur, table)

    counted = [v for v in counts.values() if v is not None]
    return {
        "implementation": IMPL_LIVE if schema_usable else IMPL_NOT_READY,
        "counts": counts,
        "counts_included": bool(include_counts and schema_usable),
        "count_note": (
            "" if include_counts and schema_usable
            else "not counted: schema not usable" if not schema_usable
            else "not counted: caller passed include_counts=False"
        ),
        # A row total that is None is "we could not count". A partial answer is
        # therefore reported as partial rather than summed into a number that
        # would read as a total.
        "total_rows": sum(counted) if len(counted) == len(tables) else None,
        "vocabulary": {
            "domains": len(_model.DOMAINS),
            "sensitivities": len(_model.SENSITIVITIES),
            "node_types": len(_model.NODE_TYPES),
            "relation_types": len(_model.RELATION_TYPES),
            "provenance_types": len(_model.PROVENANCE_TYPES),
        },
    }


def _retrieval_section() -> dict:
    """What retrieval would do, answered from code rather than the database.

    No query is issued and no context is assembled. Running a real retrieval to
    prove retrieval works would mean picking an owner, and this surface does not
    get to pick an owner. What it can report honestly is the policy: the
    intents that exist, the bounds that will be applied, and the domain
    combinations that are refused — which is enough to tell "retrieval is
    configured to refuse this" apart from "retrieval is broken".
    """
    return {
        "implementation": IMPL_LIVE,
        "intents": sorted(_retrieval.INTENTS),
        "bounds": {
            "max_depth": _retrieval.MAX_DEPTH,
            "max_nodes": _retrieval.MAX_NODES,
            "max_edges": _retrieval.MAX_EDGES,
            "max_facts": _retrieval.MAX_FACTS,
            "max_subject_batch": _facts.MAX_SUBJECT_BATCH,
        },
        "isolated_domains": sorted(_retrieval.ISOLATED_DOMAINS),
        "denial_reasons": sorted(_telemetry.DENIAL_VOCAB),
    }


def _meetings_section(cur, *, include_counts: bool) -> dict:
    """Private Meetings volume and kill-switch state — aggregates only.

    Imported lazily because ``meetings`` pulls in the communications engine,
    which a liveness probe should not pay for and this surface must not fail
    on. An import or schema failure is reported as "could not probe"
    (``implementation: NOT_READY``, counts ``None``) — never as zero, per the
    Stage 176B rule the module docstring opens with. ``enabled`` is the
    PRIVATE_MEETINGS_ENABLED kill switch (fail-closed, default off), so an
    operator can tell "dark on purpose" apart from "broken" — the exact
    distinction the entitlement section makes for the resolver.
    """
    section: dict = {
        "implementation": IMPL_NOT_READY,
        "enabled": None,
        "counts": {},
        "counts_included": False,
    }
    try:
        from services.private_office import meetings as _meetings
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_MEETINGS_IMPORT_FAILED")
        return section
    section["enabled"] = bool(_meetings.meetings_enabled())
    tables = {
        "meetings": _meetings.MEETINGS_TABLE,
        "participants": _meetings.PARTICIPANTS_TABLE,
        "invites": _meetings.INVITES_TABLE,
        "messages": _meetings.MESSAGES_TABLE,
        "recordings": _meetings.RECORDINGS_TABLE,
        "artifacts": _meetings.MEETING_ARTIFACT_TABLE,
    }
    section["counts"] = {name: None for name in tables}
    if cur is None:
        return section
    try:
        # Same posture as `_schema_section`: an idempotent IF NOT EXISTS
        # ensure, used as a probe. Without it, counting on a database the
        # meetings routes have never touched would log six failures and
        # (on PostgreSQL) abort the shared transaction.
        _meetings.ensure_meetings_schema(cur)
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_MEETINGS_SCHEMA_PROBE_FAILED")
        return section
    section["implementation"] = IMPL_LIVE
    if include_counts:
        for name, table in tables.items():
            section["counts"][name] = _count(cur, table)
        section["counts_included"] = True
    return section


def _operations_policy() -> dict:
    """What the lifecycle would do, answered from code rather than the database.

    Separated from the volume probe on purpose and computed even when the
    database is unreachable, because this is the half an operator needs during
    an incident. "Is this deployment's due-soon window what I think it is" and
    "does this build rank HIGH_RISK above DUE_SOON" are answerable without
    asking the database anything, and the answers stop a config question from
    being mistaken for a data problem. Same posture, and the same reasoning, as
    :func:`_retrieval_section`.

    Every value is read from the owning module's constants rather than
    restated. A second copy of the due-soon windows that could drift from the
    ones the classifier actually applies would make this surface worse than
    having no surface: it would be confidently wrong at exactly the moment
    someone trusted it.
    """
    policy: dict = {"available": False}
    try:
        from services.private_office import operations as _operations
        from services.private_office import records as _records
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_OPERATIONS_POLICY_IMPORT_FAILED")
        return policy
    try:
        policy.update({
            "available": True,
            "record_types": list(_records.RECORD_TYPES),
            # Which types can be late at all, and — stated rather than left as
            # an absence — why the other three cannot. An operator reading
            # "zero overdue events" should be able to see from here that EVENT
            # has no deadline semantics, not conclude the sweep is broken.
            "deadline_fields": dict(_records.DEADLINE_FIELDS),
            "no_deadline_reason": dict(_records.NO_DEADLINE_REASON),
            "due_soon_days": {
                rtype: int(window.total_seconds() // 86400)
                for rtype, window in _records.DUE_SOON_WINDOWS.items()
            },
            "derived_states": [_records.DERIVED_DUE_SOON, _records.DERIVED_OVERDUE],
            "reopenable": {k: list(v) for k, v in _records.REOPENABLE.items()},
            # Strongest first. Reported as an ordered list because the order is
            # the policy; sorting it here would destroy the only information it
            # carries.
            "attention_reasons": list(_operations.REASON_RANK),
            "unsupported_reasons": dict(_operations.UNSUPPORTED_REASONS),
            "attention_severities": sorted(_operations.ATTENTION_SEVERITIES),
            "bounds": {
                "max_attention_items": _operations.MAX_ATTENTION_ITEMS,
                "max_recent_activity": _operations.MAX_RECENT_ACTIVITY,
                "attention_scan_per_type": _operations.ATTENTION_SCAN_PER_TYPE,
                "recent_window_days": int(
                    _operations.RECENT_WINDOW.total_seconds() // 86400),
            },
        })
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_OPERATIONS_POLICY_FAILED")
        return {"available": False}
    return policy


def _operations_section(cur, *, include_counts: bool) -> dict:
    """Operations volume, kill-switch state and lifecycle policy.

    Three questions that present identically to a member as "my Overview is
    empty", and need three different responses:

    * the feature is **off** — ``availability`` is FEATURE_DISABLED, and the
      answer is to check PRIVATE_OPERATIONS_ENABLED, not to page anyone;
    * the **schema** is not on this database — ``implementation`` stays
      NOT_READY and every count is ``None``;
    * the tables are **genuinely empty** — ``implementation`` is LIVE and the
      counts are integers that happen to be zero.

    Only the third is a member with nothing recorded. Collapsing the first two
    into a zero is the Stage 176B failure this module opens by naming, so a
    count here is an integer or ``None`` and never both meanings at once.

    ``availability`` is asked of ``feature_matrix`` at the top tier rather than
    by reading the environment variable here. The gate on every operations
    route resolves the kill switch through that same function, so this reports
    what the routes will actually do; a second ``os.getenv`` parse in this file
    could disagree with the gate, and a health surface that disagrees with the
    thing it describes is worse than none. Asking at PRIVATE_OFFICE tier is
    what makes NOT_ENTITLED impossible in the answer — entitlement is a
    property of a member, this surface has no member, and the only reason this
    call can come back refused is one that belongs to the deployment.

    Aggregates only, and no owner may be named — the same rule as every other
    section here. Imported lazily so an operations import error degrades this
    section rather than taking down the health payload.
    """
    section: dict = {
        "implementation": IMPL_NOT_READY,
        "enabled": None,
        "availability": None,
        "counts": {},
        "counts_included": False,
        "policy": _operations_policy(),
    }

    try:
        gate = _fm.availability(
            "private_office.operations", _tiers.TIER_PRIVATE_OFFICE)
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_OPERATIONS_GATE_FAILED")
        gate = None
    if gate is not None:
        section["availability"] = gate.get("availability")
        section["enabled"] = bool(
            gate.get("availability") == _fm.AVAIL_ENTITLED)

    try:
        from services.private_office import records as _records
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_OPERATIONS_IMPORT_FAILED")
        return section

    tables = {
        rtype: _records.SPECS[rtype]["table"] for rtype in _records.RECORD_TYPES
    }
    section["counts"] = {name: None for name in tables}
    if cur is None:
        return section
    try:
        # Idempotent IF NOT EXISTS ensure used as a probe, exactly as
        # `_schema_section` and `_meetings_section` do. Without it, counting on
        # a database the operations routes have never touched would log six
        # failures and, on PostgreSQL, abort the shared transaction — taking
        # the sections after this one down with it.
        _records.ensure_records_schema(cur)
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_OPERATIONS_SCHEMA_PROBE_FAILED")
        return section
    section["implementation"] = IMPL_LIVE
    if include_counts:
        for name, table in tables.items():
            section["counts"][name] = _count(cur, table)
        section["counts_included"] = True
    return section


def _telemetry_section() -> dict:
    """Stage 38 — is the event table itself sound?

    ``spec_sound`` false is a deployment-blocking defect rather than a metrics
    inconvenience: an unsound spec is one where a field could carry something
    it should not, so it is surfaced here rather than only at import time where
    a warning in a boot log is easy to miss.
    """
    problems = _telemetry.spec_is_sound()
    return {
        "implementation": IMPL_LIVE,
        "events": sorted(_telemetry.EVENTS),
        "spec_sound": not problems,
        "spec_problems": problems,
    }


def _overall(schema_state: str, resolver_healthy: bool, spec_sound: bool) -> str:
    """One word, chosen so that it is never more optimistic than its parts."""
    if schema_state == _schema.STATUS_ERROR:
        return STATE_UNAVAILABLE
    if schema_state != _schema.STATUS_READY:
        return STATE_DEGRADED
    if not resolver_healthy or not spec_sound:
        return STATE_DEGRADED
    return STATE_HEALTHY


def private_office_health(
    *,
    include_counts: bool = True,
    include_entitlement: bool = True,
    include_free_count: bool = False,
) -> dict:
    """The Stage 36 health payload. Never raises; degrades into the payload.

    Accepts no user identifier and returns no user data. Safe behind an
    operator/admin check; not safe to expose publicly, because row volumes and
    tier counts are commercially sensitive even though they identify nobody.

    ``include_entitlement`` embeds ``status.subsystem_status``, which is the
    canonical entitlement/resolver surface — this module deliberately does not
    restate any of that logic, because a second resolver health check that
    could disagree with the first is worse than not having one.
    """
    generated_at = _utc_now_iso()
    conn = None
    cur = None
    schema_section = {
        "state": _schema.STATUS_ERROR, "usable": False, "tables_present": [],
        "missing": {}, "bootstrapped_by": _schema.process_role(),
        "error_type": "connect_failed",
    }

    try:
        conn = _db.connect()
        cur = conn.cursor()
    except Exception:  # noqa: BLE001
        _log.exception("PRIVATE_HEALTH_CONNECT_FAILED")

    if cur is not None:
        schema_section = _schema_section(cur)

    substrate = _substrate_section(
        cur, schema_usable=bool(cur is not None and schema_section["usable"]),
        include_counts=include_counts,
    ) if cur is not None else _substrate_section(
        None, schema_usable=False, include_counts=False)

    meetings_section = _meetings_section(cur, include_counts=include_counts)
    operations_section = _operations_section(cur, include_counts=include_counts)

    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            _log.exception("PRIVATE_HEALTH_CLOSE_FAILED")

    entitlement: dict = {}
    resolver_healthy = True
    if include_entitlement:
        try:
            entitlement = _status.subsystem_status(
                include_free_count=include_free_count)
            resolver_healthy = bool(
                (entitlement.get("resolver") or {}).get("healthy"))
        except Exception as exc:  # noqa: BLE001
            _log.exception("PRIVATE_HEALTH_ENTITLEMENT_FAILED")
            entitlement = {"error_type": type(exc).__name__}
            resolver_healthy = False

    telemetry_section = _telemetry_section()

    return {
        "generated_at": generated_at,
        "engine": getattr(_db, "ENGINE_NAME", "unknown"),
        "state": _overall(schema_section["state"], resolver_healthy,
                          telemetry_section["spec_sound"]),
        "schema": schema_section,
        "substrate": substrate,
        "meetings": meetings_section,
        "operations": operations_section,
        "retrieval": _retrieval_section(),
        "telemetry": telemetry_section,
        # The feature census, so a reader can see at a glance how much of the
        # Private Office is actually built versus entitled. `status` owns this;
        # it is surfaced rather than recomputed.
        "features": {
            "total": len(_fm.FEATURES),
            "live": len(list(_fm.implemented_feature_ids())),
        },
        "entitlement": entitlement,
    }


__all__ = [
    "IMPL_LIVE",
    "IMPL_NOT_READY",
    "STATE_DEGRADED",
    "STATE_HEALTHY",
    "STATE_UNAVAILABLE",
    "private_office_health",
]
