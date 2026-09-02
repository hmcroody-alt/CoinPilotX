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
Four ``SELECT COUNT(*)`` over the private tables, plus whatever
``status.subsystem_status`` costs when included. The counts are unfiltered, so
on a large table PostgreSQL will do a sequential scan; that is why
:func:`private_office_health` takes ``include_counts`` and why the default is
to include them but the caller can turn them off for a liveness probe that runs
every few seconds. The retrieval section costs nothing at all — it reports the
configured bounds and the intent catalog, which are constants in code, because
"what would retrieval do" is answerable without asking the database anything.
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
