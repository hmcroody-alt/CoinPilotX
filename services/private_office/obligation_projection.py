"""The Obligations → Capital Graph projection.

Authority, stated once
----------------------
``services.private_office.records`` owns obligations: their identity, their
amounts, their due dates, their lifecycle, and the OPEN → RESOLVED/DISMISSED
state machine. This module owns none of that — it owns the *projection*: an
evidence-backed mirror of each open obligation as a LIABILITY node in the
member's private capital graph, so the graph can answer "what does this
member owe" without becoming a second ledger. Nothing here can create,
resolve, or reprice an obligation; edits route through ``records`` and the
projection converges on whatever the record store says.

Stable identity across revisions
--------------------------------
``records.revise_record`` supersedes a row with a new one and recomputes the
row's ``record_key`` (the revision number is part of the key), so the row id
and key both churn as an obligation is revised. The *lineage* does not: every
revision points back through ``supersedes_id`` to the first row ever written.
The projection therefore keys each LIABILITY node on the **root of the
supersedes chain** (``obligation:{root_id}``): revising an amount updates the
same logical node, it never mints a sibling. This is the graph-side twin of
the portfolio rule that a re-bought symbol reuses its node.

Convergence instead of deltas
-----------------------------
:func:`project_user` re-projects the member's open obligations from current
record-store state every time it runs. Running it twice, out of order, or
after any number of missed changes lands on the same answer. The record store
has no outbox (writes are synchronous), so there is nothing to drain;
read paths call :func:`sweep` before reading, which is the lazy equivalent.

What the projection writes (through canonical writers only)
-----------------------------------------------------------
* one PERSON node for the member (``user:{id}``, GENERAL/CONFIDENTIAL) —
  the same node the portfolio projection maintains,
* one LIABILITY node per open obligation lineage
  (``obligation:{root_id}``, FINANCIAL/CONFIDENTIAL),
* an OWNS edge between them — the vocabulary already lets OWNS carry a
  LIABILITY target, and "OWES" would be a redundant synonym of that pairing,
* facts in the ``obligation.*`` namespace: title, kind, and — only when the
  record actually states them — amount, currency, and due date. An
  obligation whose amount is unknown gets **no** amount fact: an unknown
  liability is not a zero-dollar liability, and the read side must see the
  absence, not a fabrication.

Provenance passes through, never upgraded: a record captured as
USER_ASSERTED projects as USER_ASSERTED; one extracted from a document keeps
DOCUMENT_EXTRACTED. The projection adds no confidence the source did not
have.

A resolved or dismissed obligation retires the OWNS edge, supersedes the
projected facts, and archives the node — archived, not deleted, so "you owed
this until March" remains answerable. Reopening (a new revision back to OPEN)
reactivates the same logical node.
"""

from __future__ import annotations

import logging

from services.private_office import audit as _audit
from services.private_office import facts as _facts
from services.private_office import graph as _graph
from services.private_office import model as _model
from services.private_office import records as _records
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.obligation_projection")

#: The projection's fact namespace. Nothing else writes ``obligation.*``
#: facts, which is what makes ``supersede_facts`` safe to aim at it.
FACT_TITLE = "obligation.title"
FACT_KIND = "obligation.kind"
FACT_AMOUNT = "obligation.amount"
FACT_CURRENCY = "obligation.currency"
FACT_DUE_AT = "obligation.due_at"

PROJECTED_FACT_TYPES: tuple[str, ...] = (
    FACT_TITLE, FACT_KIND, FACT_AMOUNT, FACT_CURRENCY, FACT_DUE_AT,
)

#: LIABILITY nodes this module created are recognisable by this external_ref
#: prefix, which is how a re-projection finds its own prior work without
#: touching nodes other writers made.
LIABILITY_REF_PREFIX = "obligation:"

PURPOSE = "system_maintenance"

#: More open obligations than this and the projection stops early and says
#: so, rather than half-writing. Mirrors the portfolio projection's cap.
MAX_OBLIGATIONS = 200

#: Longest supersedes chain the root walk will follow before treating the
#: last id it reached as the root. A cycle cannot happen through
#: ``revise_record`` (it only ever points a new row at an older one), but the
#: walk must not trust that with an unbounded loop.
MAX_CHAIN_DEPTH = 50

#: Record provenance states the projection will pass through to facts. A
#: blank or unrecognised state projects as USER_ASSERTED — the floor, never
#: an upgrade. Degraded states (STALE/CONFLICTING) cannot appear on a record
#: (``records._prepare`` rejects them as write origins) and are not listed.
_PASSTHROUGH_PROVENANCE = frozenset({
    _model.PROVENANCE_VERIFIED,
    _model.PROVENANCE_PROVIDER_ASSERTED,
    _model.PROVENANCE_DOCUMENT_EXTRACTED,
    _model.PROVENANCE_USER_ASSERTED,
    _model.PROVENANCE_INFERRED,
    _model.PROVENANCE_ESTIMATED,
})


def _person_ref(owner: int) -> str:
    return f"user:{owner}"


def _liability_ref(root_id: int) -> str:
    return f"{LIABILITY_REF_PREFIX}{root_id}"


def _provenance(root_id: int, record_id: int) -> _facts.ProvenanceRef:
    """A resolvable pointer at the authoritative record, not at this module."""
    return _facts.ProvenanceRef(
        source_type="obligation",
        source_id=str(root_id),
        locator=f"record:{record_id}",
        confidence=1.0,
    )


def _provenance_state(record: dict) -> str:
    state = str(record.get("provenance_state") or "").strip().upper()
    if state in _PASSTHROUGH_PROVENANCE:
        return state
    return _model.PROVENANCE_USER_ASSERTED


def _root_id(cur, *, owner: int, record: dict) -> int:
    """Walk ``supersedes_id`` back to the first row of this lineage.

    The root id is the one identifier that survives every revision, which is
    why it — and not the current row id or the revision-bearing record_key —
    names the LIABILITY node. If a link in the chain has vanished, the id the
    pointer named is still the lineage's name; the walk stops and uses it.
    """
    current = record
    for _ in range(MAX_CHAIN_DEPTH):
        supersedes = int(current.get("supersedes_id") or 0)
        if supersedes <= 0:
            return int(current.get("id") or 0)
        prior = _records.get_record(
            cur, record_type=_records.TYPE_OBLIGATION, owner_user_id=owner,
            record_id=supersedes, audit=False,
        )
        if prior is None:
            return supersedes
        current = prior
    return int(current.get("id") or 0)


def read_ledger(cur, *, user_id: int) -> dict[int, dict]:
    """Open obligations keyed by lineage root, straight from the record store.

    Uses the record store's own read API — no raw SQL against its tables —
    so owner scoping, lifecycle filtering, and serialization stay in one
    place. Returns ``{root_id: record}`` where each record is the current
    ACTIVE, OPEN row of its lineage. If two active rows ever claimed the same
    root (which ``revise_record`` prevents), the newer row wins.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {}
    rows = _records.list_records(
        cur, record_type=_records.TYPE_OBLIGATION, owner_user_id=owner,
        statuses=["OPEN"], limit=_records.MAX_LIMIT,
    )
    ledger: dict[int, dict] = {}
    for record in rows:
        root = _root_id(cur, owner=owner, record=record)
        if root <= 0:
            continue
        held = ledger.get(root)
        if held is None or int(record.get("id") or 0) > int(held.get("id") or 0):
            ledger[root] = record
    return ledger


def _project_fact(cur, *, owner: int, node_id: int, fact_type: str,
                  value: object, value_type: str, provenance_type: str,
                  root_id: int, record_id: int) -> None:
    """Record one projected fact and supersede whatever it replaces."""
    written = _facts.record_fact(
        cur,
        owner_user_id=owner,
        subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id,
        fact_type=fact_type,
        value=value,
        value_type=value_type,
        provenance_type=provenance_type,
        provenance=_provenance(root_id, record_id),
        sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
        domain=_model.DOMAIN_FINANCIAL,
        actor_user_id=owner,
        purpose=PURPOSE,
    )
    _facts.supersede_facts(
        cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id, fact_type=fact_type,
        keep_fact_id=int(written.get("fact_id") or 0),
        actor_user_id=owner, purpose=PURPOSE,
    )


def _retire_fact(cur, *, owner: int, node_id: int, fact_type: str) -> None:
    """Supersede every ACTIVE row of one projected fact type, keeping none."""
    _facts.supersede_facts(
        cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
        subject_id=node_id, fact_type=fact_type, keep_fact_id=0,
        actor_user_id=owner, purpose=PURPOSE,
    )


def _projected_liability_nodes(cur, *, owner: int) -> dict[int, dict]:
    """This owner's projection-made LIABILITY nodes by root id, any lifecycle."""
    rows = _graph.list_nodes(
        cur, owner_user_id=owner, node_types=[_model.NODE_LIABILITY],
        include_inactive=True, limit=500,
    )
    out: dict[int, dict] = {}
    for row in rows:
        ref = str(row.get("external_ref") or "")
        if not ref.startswith(LIABILITY_REF_PREFIX):
            continue
        try:
            root = int(ref[len(LIABILITY_REF_PREFIX):])
        except ValueError:
            continue
        out[root] = row
    return out


def _ensure_active(cur, *, owner: int, node: dict) -> None:
    if str(node.get("lifecycle_state") or "") != _model.LIFECYCLE_ACTIVE:
        _graph.set_node_lifecycle(
            cur, owner_user_id=owner, node_id=int(node["id"]),
            lifecycle_state=_model.LIFECYCLE_ACTIVE,
            actor_user_id=owner, purpose=PURPOSE,
        )


def project_user(cur, *, user_id: int) -> dict:
    """Re-project one member's open obligations into their capital graph.

    Full-state and convergent: reads the record store, makes the graph match,
    and returns ``{"ok", "obligations", "retired", "skipped"}``. Raises only
    on the substrate invariants the canonical writers enforce — a rejected
    write is a projector bug, and surfacing it beats a mirror that silently
    diverges.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "obligations": 0, "retired": 0, "skipped": 0}
    _schema.require_private_schema(cur)

    ledger = read_ledger(cur, user_id=owner)
    skipped = 0
    if len(ledger) > MAX_OBLIGATIONS:
        skipped = len(ledger) - MAX_OBLIGATIONS
        ledger = dict(sorted(ledger.items())[:MAX_OBLIGATIONS])

    person = _graph.upsert_node(
        cur, owner_user_id=owner, node_type=_model.NODE_PERSON,
        external_ref=_person_ref(owner),
        sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
        domain=_model.DOMAIN_GENERAL, actor_user_id=owner, purpose=PURPOSE,
    )
    person_node = _graph.get_node(cur, owner_user_id=owner,
                                  node_id=person["node_id"])
    if person_node is not None:
        _ensure_active(cur, owner=owner, node=person_node)

    existing = _projected_liability_nodes(cur, owner=owner)

    for root, record in sorted(ledger.items()):
        record_id = int(record.get("id") or 0)
        node_row = existing.get(root)
        if node_row is None:
            created = _graph.upsert_node(
                cur, owner_user_id=owner, node_type=_model.NODE_LIABILITY,
                external_ref=_liability_ref(root),
                sensitivity=_model.SENSITIVITY_CONFIDENTIAL,
                domain=_model.DOMAIN_FINANCIAL, actor_user_id=owner,
                purpose=PURPOSE,
            )
            node_row = _graph.get_node(cur, owner_user_id=owner,
                                       node_id=created["node_id"])
            if node_row is None:
                continue
        # resolve/upsert finds archived nodes but never reactivates them; a
        # reopened obligation must, or the node stays invisible to every
        # ACTIVE-only read.
        _ensure_active(cur, owner=owner, node=node_row)
        node_id = int(node_row["id"])
        provenance_type = _provenance_state(record)

        # record_edge dedupes on its key and re-activates a retired edge, so
        # this is one call for "owe it, still owe it, and owe it again".
        _graph.record_edge(
            cur, owner_user_id=owner, source=int(person["node_id"]),
            relation_type=_model.RELATION_OWNS, target=node_id,
            provenance_type=provenance_type,
            provenance=_provenance(root, record_id),
            actor_user_id=owner, purpose=PURPOSE,
        )

        _project_fact(cur, owner=owner, node_id=node_id,
                      fact_type=FACT_TITLE,
                      value=str(record.get("title") or "").strip() or "Obligation",
                      value_type=_model.VALUE_STRING,
                      provenance_type=provenance_type,
                      root_id=root, record_id=record_id)

        kind = str(record.get("obligation_type") or "").strip()
        if kind:
            _project_fact(cur, owner=owner, node_id=node_id,
                          fact_type=FACT_KIND, value=kind,
                          value_type=_model.VALUE_STRING,
                          provenance_type=provenance_type,
                          root_id=root, record_id=record_id)
        else:
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=FACT_KIND)

        amount = record.get("amount_number")
        if amount is not None:
            _project_fact(cur, owner=owner, node_id=node_id,
                          fact_type=FACT_AMOUNT, value=amount,
                          value_type=_model.VALUE_MONEY,
                          provenance_type=provenance_type,
                          root_id=root, record_id=record_id)
            currency = str(record.get("currency") or "").strip().upper()
            if currency:
                _project_fact(cur, owner=owner, node_id=node_id,
                              fact_type=FACT_CURRENCY, value=currency,
                              value_type=_model.VALUE_STRING,
                              provenance_type=provenance_type,
                              root_id=root, record_id=record_id)
            else:
                _retire_fact(cur, owner=owner, node_id=node_id,
                             fact_type=FACT_CURRENCY)
        else:
            # An unknown amount retires any previously-known one rather than
            # leaving a stale number ACTIVE — an obligation of unknown size
            # is not an obligation of size zero, and never becomes one here.
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=FACT_AMOUNT)
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=FACT_CURRENCY)

        due_at = str(record.get("due_at") or "").strip()
        if due_at:
            _project_fact(cur, owner=owner, node_id=node_id,
                          fact_type=FACT_DUE_AT, value=due_at,
                          value_type=_model.VALUE_DATE,
                          provenance_type=provenance_type,
                          root_id=root, record_id=record_id)
        else:
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=FACT_DUE_AT)

    retired = 0
    for root, node_row in sorted(existing.items()):
        if root in ledger:
            continue
        node_id = int(node_row["id"])
        still_active = (
            str(node_row.get("lifecycle_state") or "") == _model.LIFECYCLE_ACTIVE)
        for edge in _graph.neighbors(
            cur, owner_user_id=owner, node_id=node_id,
            relations=[_model.RELATION_OWNS], direction=_graph.DIRECTION_IN,
        ):
            _graph.retire_edge(cur, owner_user_id=owner,
                               edge_id=int(edge["id"]),
                               actor_user_id=owner, purpose=PURPOSE)
        for fact_type in PROJECTED_FACT_TYPES:
            _retire_fact(cur, owner=owner, node_id=node_id,
                         fact_type=fact_type)
        if still_active:
            _graph.set_node_lifecycle(
                cur, owner_user_id=owner, node_id=node_id,
                lifecycle_state=_model.LIFECYCLE_ARCHIVED,
                actor_user_id=owner, purpose=PURPOSE,
            )
            retired += 1

    return {"ok": True, "obligations": len(ledger), "retired": retired,
            "skipped": skipped}


def sweep(cur, *, user_id: int) -> dict:
    """One lazy convergence pass that degrades instead of raising.

    The record store has no outbox, so read paths cannot drain one — they
    call this before reading, and a failed projection leaves them an older
    but true mirror rather than a broken page. The failure is logged and
    reported, never swallowed into a fake success.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "obligations": 0, "retired": 0, "skipped": 0}
    try:
        return project_user(cur, user_id=owner)
    except Exception as exc:  # noqa: BLE001 — reads degrade, never break
        LOGGER.warning("OBLIGATION_PROJECTION_FAILED user=%s error=%s",
                       owner, exc)
        return {"ok": False, "obligations": 0, "retired": 0, "skipped": 0}


# ---------------------------------------------------------------------------
# Reconciliation — prove the mirror matches the record store, and repair it
# ---------------------------------------------------------------------------
def reconcile(cur, *, user_id: int, repair: bool = False) -> dict:
    """Compare the record store against the projection, lineage by lineage.

    Drift is reported as data — ``{"root_id", "field", "ledger",
    "projected"}`` — and, with ``repair=True``, fixed by the same
    :func:`project_user` every other path uses, then re-checked. There is no
    separate repair writer to drift from the projector.
    """
    owner = int(user_id or 0)
    if owner <= 0:
        return {"ok": False, "drift": [], "repaired": False}
    _schema.require_private_schema(cur)

    ledger = read_ledger(cur, user_id=owner)
    projected = _projected_liability_nodes(cur, owner=owner)

    drift: list[dict] = []
    for root, record in sorted(ledger.items()):
        node = projected.get(root)
        if node is None or str(node.get("lifecycle_state")) != _model.LIFECYCLE_ACTIVE:
            drift.append({"root_id": root, "field": "node",
                          "ledger": "open", "projected": "missing"})
            continue
        rows = _facts.list_facts(
            cur, owner_user_id=owner, subject_type=_facts.SUBJECT_NODE,
            subject_id=int(node["id"]), fact_types=[FACT_AMOUNT], limit=5,
        )
        fact = next((row for row in rows
                     if row.get("fact_type") == FACT_AMOUNT), None)
        recorded = (fact or {}).get("value_number")
        expected = record.get("amount_number")
        if expected is None:
            if recorded is not None:
                drift.append({"root_id": root, "field": "amount",
                              "ledger": None, "projected": recorded})
        elif recorded is None or abs(float(recorded) - float(expected)) > 1e-9:
            drift.append({"root_id": root, "field": "amount",
                          "ledger": expected, "projected": recorded})
    for root, node in sorted(projected.items()):
        if root not in ledger and (
            str(node.get("lifecycle_state")) == _model.LIFECYCLE_ACTIVE
        ):
            drift.append({"root_id": root, "field": "node",
                          "ledger": "not_open", "projected": "active"})

    repaired = False
    if drift and repair:
        project_user(cur, user_id=owner)
        repaired = True
        follow_up = reconcile(cur, user_id=owner, repair=False)
        return {"ok": True, "drift": follow_up["drift"], "repaired": True,
                "drift_before_repair": drift}
    return {"ok": True, "drift": drift, "repaired": repaired}
