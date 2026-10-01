"""Stages 8-10 — the only way a private graph node or edge is written or read.

What belongs here and what does not
-----------------------------------
Stage 11 draws the line this module is built around: **the graph holds
relationships, the fact store holds assertions.** A property is a node; its
address, purchase date and estimated value are facts about that node; the
sentence "Business A owns Property A" is an edge.

The failure mode on the other side of that line is worth naming because it is
the natural one. Encoding every fact as an edge produces a graph where
``PROPERTY --has_address--> "12 Rue Test"`` is a node, and then the address is a
node type, and then every traversal has to know which relations mean "connected
to another entity" and which mean "carries a value". Traversal depth stops
meaning anything, because two hops might be two relationships or one
relationship and one attribute. The bound in Stage 16 becomes unenforceable in
practice — ``max_depth = 3`` is a different amount of graph depending on how the
data was written.

So: relations are limited to the six in :data:`model.RELATION_TYPES`, endpoints
are constrained by :data:`model.RELATION_ENDPOINTS`, and there is no relation
that points at a literal.

Owner equality, and why it is checked three times
-------------------------------------------------
An edge carries ``owner_user_id`` even though both of its endpoints already do.
:func:`record_edge` enforces that all three agree, and it does so by resolving
both endpoints through owner-scoped queries — so a node belonging to another
member is not "rejected", it is *not found*. That distinction is Stage 14: a
caller who names User B's property id must get the same answer as a caller who
names an id that was never issued, because "you do not have access to that"
tells an attacker the row exists.

The redundant column is what lets a traversal step filter on the owner in the
same ``WHERE`` clause it filters on ``source_node_id``, rather than through two
joins that a future query could forget one of. The writer is what stops that
redundancy from ever drifting into a disagreement.

Lifecycle instead of deletion
-----------------------------
Nothing here deletes. A property that was sold moves to ``SUPERSEDED``; the
edges that referenced it stay, and stay findable, because an edge pointing at a
row that no longer exists is how a traversal starts returning ids it cannot
describe. Reads exclude non-active rows by default, so the practical effect is
the same as deletion without the dangling references — and the history survives
for the audit questions that arrive later.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from services.private_office import audit as _audit
from services.private_office import facts as _facts
from services.private_office import model as _model
from services.private_office import schema as _schema
from services.private_office import telemetry as _telemetry

# `ProvenanceRef` and the timestamp helpers are imported rather than restated.
# Stage 12 defines one provenance shape for the whole substrate, and an edge
# whose provenance encoded differently from a fact's would mean the retrieval
# layer had two ways to read the same field — which is the drift this package
# exists to prevent, applied to itself.
from services.private_office.facts import ProvenanceRef, decode_provenance_ref

LOGGER = logging.getLogger("private_office.graph")

MAX_EXTERNAL_REF = 128

STATUS_CREATED = "created"
STATUS_EXISTING = "existing"
STATUS_WRITTEN = "written"
STATUS_REFRESHED = "refreshed"

#: Ceiling on a single neighbour expansion. Stage 16 sets the traversal budget
#: (``max_depth``, ``max_nodes``, ``max_edges``) at the retrieval layer; this is
#: the per-step guard underneath it, so that a traversal which somehow lost its
#: budget still cannot pull an entire graph in one hop.
MAX_NEIGHBOURS = 200

DIRECTION_OUT = "out"
DIRECTION_IN = "in"
DIRECTION_BOTH = "both"

_EXTERNAL_REF_RE = re.compile(r"^[A-Za-z0-9_:.\-/]{1,128}$")


class PrivateGraphRejected(ValueError):
    """A node or edge write that would break an invariant.

    Raised rather than returned, for the same reason
    :class:`facts.PrivateFactRejected` is: an unknown relation or a
    cross-owner endpoint is a bug in the caller, and a caller that ignores a
    ``{"status": "rejected"}`` dictionary is how a graph quietly stops being
    written while every call still looks like it succeeded.
    """


@dataclass(frozen=True)
class NodeSpec:
    """A node named by what it is rather than by its row id.

    Callers usually know "the property whose marketplace listing is 4471", not
    "node 88". Passing that description to :func:`record_edge` lets the writer
    resolve or create the node inside the same owner-scoped transaction, which
    is the only place the owner check can be made once and trusted.
    """

    node_type: str
    external_ref: str = ""
    sensitivity: str | None = None
    domain: str | None = None


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def node_key(node_type: str, external_ref: str) -> str:
    """Stable identity of a node within one owner.

    Two nodes are the same node when they are the same type and point at the
    same external row. A node with **no** ``external_ref`` describes something
    the platform has no row for — an off-platform business, a policy the member
    described — and gets a random key, because there is nothing to match on and
    collapsing every unreferenced PROPERTY into one node would silently merge a
    member's houses.
    """
    ref = str(external_ref or "").strip()
    if not ref:
        return f"anon:{uuid.uuid4().hex}"
    raw = f"{node_type}\x1f{ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def edge_key(
    *,
    source_node_id: int,
    relation_type: str,
    target_node_id: int,
    provenance_type: str,
    provenance_ref: str,
    valid_from: str,
) -> str:
    """Stable identity of an edge.

    Provenance is part of the key for the reason it is part of ``fact_key``:
    two sources independently asserting that a business owns a property is
    corroboration, and a dedupe that collapsed them would destroy exactly the
    signal that makes the relationship trustworthy. A repeat from the *same*
    source is a newer look at one relationship and refreshes rather than
    inserting.

    ``valid_from`` is the *explicitly requested* window start, matching
    ``facts.fact_key``: pass ``""`` when the caller stated no window. Hashing
    the stored value instead would fold in the wall clock and give every write
    a unique identity, so nothing would ever dedupe.
    """
    raw = "\x1f".join(
        (
            str(int(source_node_id)), relation_type, str(int(target_node_id)),
            provenance_type, provenance_ref, valid_from,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _row(row) -> dict:
    return dict(row)


def _row_value(row, key: str, index: int):
    return row[key] if hasattr(row, "keys") else row[index]


def _validated_provenance(provenance_type: object) -> str:
    source = _model.normalize_provenance(provenance_type)
    if not source:
        raise PrivateGraphRejected(f"unknown provenance_type: {provenance_type!r}")
    if source in _model.DEGRADED_PROVENANCE:
        # STALE and CONFLICTING are states this package moves a row into, never
        # origins. An edge born CONFLICTING would be an assertion that already
        # ranks at zero and can therefore never lose an argument to a better one.
        raise PrivateGraphRejected(
            f"{source} is a derived state, not a source of a new edge")
    return source


# ---------------------------------------------------------------------------
# Nodes (Stage 8)
# ---------------------------------------------------------------------------
def resolve_node(
    cur, *, owner_user_id: int, node_type: str, external_ref: str
) -> dict | None:
    """Find an existing node by type and external reference, within one owner.

    Returns ``None`` both when the node does not exist and when it exists under
    a different owner. Those two cases are deliberately indistinguishable to the
    caller — see the module docstring.
    """
    owner = int(owner_user_id or 0)
    kind = _model.normalize_node_type(node_type)
    ref = str(external_ref or "").strip()[:MAX_EXTERNAL_REF]
    if owner <= 0 or not kind or not ref:
        return None
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT * FROM {_schema.NODES_TABLE} "
        f"WHERE owner_user_id = ? AND node_key = ?",
        (owner, node_key(kind, ref)),
    )
    found = cur.fetchone()
    return _row(found) if found is not None else None


def get_node(cur, *, owner_user_id: int, node_id: object) -> dict | None:
    """One node by row id, owner-scoped. ``None`` for absent *and* for foreign."""
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return None
    try:
        row_id = int(node_id)
    except (TypeError, ValueError):
        # A malformed id is a miss, not an error. Stage 30 seeds fabricated
        # object ids specifically to see whether a bad id produces a different
        # response shape than a valid-but-foreign one; both land here.
        return None
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT * FROM {_schema.NODES_TABLE} WHERE owner_user_id = ? AND id = ?",
        (owner, row_id),
    )
    found = cur.fetchone()
    return _row(found) if found is not None else None


def upsert_node(
    cur,
    *,
    owner_user_id: int,
    node_type: str,
    external_ref: str = "",
    sensitivity: object = None,
    domain: object = None,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Create a node, or return the existing one. The only supported creator.

    Returns ``{"status", "node_id", "node_key", "node_type", "sensitivity",
    "domain"}`` with status ``created`` or ``existing``.

    On an existing node the sensitivity may be **raised** and never lowered. A
    later writer who omits the argument, or who classifies the same entity more
    loosely than the first writer did, must not be able to declassify it: the
    write that lowers a classification is exactly the one nobody reviews, and
    its effect is invisible until the retrieval ceiling starts releasing rows it
    used to withhold.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateGraphRejected("owner_user_id is required")

    kind = _model.normalize_node_type(node_type)
    if not kind:
        raise PrivateGraphRejected(f"unknown node_type: {node_type!r}")

    ref = str(external_ref or "").strip()[:MAX_EXTERNAL_REF]
    if ref and not _EXTERNAL_REF_RE.match(ref):
        # An external reference is an identifier into another system. Free text
        # here would make it a place to park a value, which is the same mistake
        # the audit table's missing `detail_json` column exists to prevent.
        raise PrivateGraphRejected(f"external_ref is not identifier-shaped: {ref!r}")

    resolved_domain = _model.normalize_domain(domain or _model.DEFAULT_DOMAIN)
    if not resolved_domain:
        raise PrivateGraphRejected(f"unknown domain: {domain!r}")
    resolved_sensitivity = _model.normalize_sensitivity(
        sensitivity or _model.DEFAULT_SENSITIVITY)
    if not resolved_sensitivity:
        raise PrivateGraphRejected(f"unknown sensitivity: {sensitivity!r}")

    _schema.require_private_schema(cur)

    if ref:
        existing = resolve_node(
            cur, owner_user_id=owner, node_type=kind, external_ref=ref)
        if existing is not None:
            stored = _model.normalize_sensitivity(existing.get("sensitivity")) or ""
            if _model.SENSITIVITY_RANK.get(resolved_sensitivity, 0) > _model.SENSITIVITY_RANK.get(stored, -1):
                cur.execute(
                    f"UPDATE {_schema.NODES_TABLE} "
                    f"SET sensitivity = ?, updated_at = ? WHERE id = ?",
                    (resolved_sensitivity, _facts._now_iso(), int(existing["id"])),
                )
                stored = resolved_sensitivity
            return {
                "status": STATUS_EXISTING,
                "node_id": int(existing["id"]),
                "node_key": str(existing["node_key"]),
                "node_type": str(existing["node_type"]),
                "sensitivity": stored,
                "domain": str(existing["domain"]),
            }

    key = node_key(kind, ref)
    now_iso = _facts._now_iso()
    cur.execute(
        f"""INSERT INTO {_schema.NODES_TABLE}
        (owner_user_id, node_key, node_type, external_ref, lifecycle_state,
         sensitivity, domain, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, key, kind, ref, _model.LIFECYCLE_ACTIVE,
         resolved_sensitivity, resolved_domain, now_iso, now_iso),
    )
    cur.execute(
        f"SELECT id FROM {_schema.NODES_TABLE} WHERE owner_user_id = ? AND node_key = ?",
        (owner, key),
    )
    inserted = cur.fetchone()
    row_id = int(_row_value(inserted, "id", 0)) if inserted is not None else 0

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_GRAPH_WRITE, object_type=kind,
        object_id=row_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    # Stage 38. `relation_type` is absent from a node event and so resolves to
    # `other` by the spec's rules — every declared field is always emitted, so
    # the event shape stays constant and the counter stays aggregatable.
    _telemetry.emit(
        _telemetry.EVENT_GRAPH_WRITE, outcome=STATUS_CREATED, node_type=kind,
        domain=resolved_domain, sensitivity=resolved_sensitivity)
    return {"status": STATUS_CREATED, "node_id": row_id, "node_key": key,
            "node_type": kind, "sensitivity": resolved_sensitivity,
            "domain": resolved_domain}


def set_node_lifecycle(
    cur,
    *,
    owner_user_id: int,
    node_id: object,
    lifecycle_state: str,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> bool:
    """Move a node between ACTIVE / SUPERSEDED / ARCHIVED. Owner-scoped.

    Returns False for an unknown state and for a node this owner does not have,
    without saying which — a lifecycle endpoint that answered "no such node"
    differently from "not yours" would be a second existence oracle sitting
    beside the read path Stage 14 protects.
    """
    owner = int(owner_user_id or 0)
    state = _model.normalize_lifecycle(lifecycle_state)
    if owner <= 0 or not state:
        return False
    node = get_node(cur, owner_user_id=owner, node_id=node_id)
    if node is None:
        return False
    cur.execute(
        f"UPDATE {_schema.NODES_TABLE} "
        f"SET lifecycle_state = ?, updated_at = ? WHERE owner_user_id = ? AND id = ?",
        (state, _facts._now_iso(), owner, int(node["id"])),
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_GRAPH_WRITE, object_type=str(node["node_type"]),
        object_id=int(node["id"]), purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return True


# ---------------------------------------------------------------------------
# Edges (Stages 9-10)
# ---------------------------------------------------------------------------
def _resolve_endpoint(
    cur, *, owner_user_id: int, endpoint: object, role: str,
    actor_user_id: int, purpose: str,
) -> dict:
    """A node row for an endpoint given as a row id or a :class:`NodeSpec`.

    Raises :class:`PrivateGraphRejected` with a message that names the *role*
    and never the owner. "source node not found" is what a caller gets for an id
    that was never issued, for an id belonging to another member, and for a
    malformed id — one sentence for three situations, on purpose.
    """
    if isinstance(endpoint, NodeSpec):
        created = upsert_node(
            cur, owner_user_id=owner_user_id, node_type=endpoint.node_type,
            external_ref=endpoint.external_ref, sensitivity=endpoint.sensitivity,
            domain=endpoint.domain, actor_user_id=actor_user_id, purpose=purpose,
        )
        node = get_node(cur, owner_user_id=owner_user_id, node_id=created["node_id"])
        if node is None:
            raise PrivateGraphRejected(f"{role} node could not be created")
        return node

    node = get_node(cur, owner_user_id=owner_user_id, node_id=endpoint)
    if node is None:
        _audit.record_denied(
            cur, actor_user_id=actor_user_id, owner_user_id=owner_user_id,
            object_type="NODE", object_id=endpoint, purpose=purpose,
        )
        raise PrivateGraphRejected(f"{role} node not found")
    if int(node.get("owner_user_id") or 0) != int(owner_user_id):
        # Unreachable through `get_node`, which is owner-scoped. Kept because
        # the cost is one integer comparison and the thing it guards is the P0
        # gate: if a future refactor ever widens that query, this fails the write
        # instead of silently writing a cross-owner edge.
        raise PrivateGraphRejected(f"{role} node not found")
    return node


def record_edge(
    cur,
    *,
    owner_user_id: int,
    source: object,
    relation_type: str,
    target: object,
    provenance_type: str,
    provenance: ProvenanceRef | None = None,
    confidence: float | None = None,
    valid_from: object = None,
    valid_to: object = None,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Write one relationship. The only supported way to create an edge.

    ``source`` and ``target`` are each either an existing node row id or a
    :class:`NodeSpec` to resolve-or-create. Returns ``{"status", "edge_id",
    "edge_key", "source_node_id", "relation_type", "target_node_id"}`` with
    status ``written`` or ``refreshed``.

    The checks, in the order Stage 10 lists them: the owner exists, both
    endpoints resolve *within that owner*, the relation is one of the six and is
    permitted between those two node types, the provenance is a real source, the
    validity window opens before it closes, and the edge is deduped on its key.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateGraphRejected("owner_user_id is required")
    actor = int(actor_user_id or owner)

    relation = _model.normalize_relation(relation_type)
    if not relation:
        raise PrivateGraphRejected(f"unknown relation_type: {relation_type!r}")

    source_prov = _validated_provenance(provenance_type)

    _schema.require_private_schema(cur)

    source_node = _resolve_endpoint(
        cur, owner_user_id=owner, endpoint=source, role="source",
        actor_user_id=actor, purpose=purpose)
    target_node = _resolve_endpoint(
        cur, owner_user_id=owner, endpoint=target, role="target",
        actor_user_id=actor, purpose=purpose)

    if int(source_node["id"]) == int(target_node["id"]):
        # A node that owns, covers or advises itself is never a real assertion,
        # and a self-loop is the cheapest way to make a bounded traversal spin
        # against its node budget without making progress.
        raise PrivateGraphRejected("an edge may not connect a node to itself")

    if not _model.relation_permits(
        relation, source_node["node_type"], target_node["node_type"]
    ):
        # Without this, nothing stops `INSURANCE_POLICY OWNS PERSON`, and once
        # one such edge exists every traversal that follows OWNS has to defend
        # against it — which in practice means every traversal quietly stops
        # trusting its own edges.
        raise PrivateGraphRejected(
            f"{relation} may not connect "
            f"{source_node['node_type']} to {target_node['node_type']}"
        )

    ref = (provenance or ProvenanceRef()).encoded()

    now_iso = _facts._now_iso()
    # Identity uses what the caller stated; storage falls back to now so the
    # edge is always placeable in time. See `edge_key`.
    explicit_from = _facts._iso(valid_from, default=None)
    from_iso = explicit_from or now_iso
    to_iso = _facts._iso(valid_to, default=None)
    if to_iso and to_iso < from_iso:
        raise PrivateGraphRejected("valid_to precedes valid_from")

    try:
        score = 1.0 if confidence is None else float(confidence)
    except (TypeError, ValueError):
        raise PrivateGraphRejected(f"confidence is not a number: {confidence!r}")
    score = max(0.0, min(score, 1.0))

    key = edge_key(
        source_node_id=int(source_node["id"]), relation_type=relation,
        target_node_id=int(target_node["id"]), provenance_type=source_prov,
        provenance_ref=ref, valid_from=explicit_from or "",
    )

    cur.execute(
        f"SELECT id, confidence FROM {_schema.EDGES_TABLE} "
        f"WHERE owner_user_id = ? AND edge_key = ?",
        (owner, key),
    )
    existing = cur.fetchone()
    if existing is not None:
        row_id = int(_row_value(existing, "id", 0))
        try:
            prior = float(_row_value(existing, "confidence", 1) or 0.0)
        except (TypeError, ValueError):
            prior = 0.0
        cur.execute(
            f"UPDATE {_schema.EDGES_TABLE} "
            f"SET confidence = ?, lifecycle_state = ?, valid_to = ?, updated_at = ? "
            f"WHERE owner_user_id = ? AND id = ?",
            (max(score, prior), _model.LIFECYCLE_ACTIVE, to_iso, now_iso, owner, row_id),
        )
        _audit.record(
            cur, actor_user_id=actor, owner_user_id=owner,
            action=_audit.ACTION_GRAPH_WRITE, object_type=relation,
            object_id=row_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
        )
        _telemetry.emit(
            _telemetry.EVENT_GRAPH_WRITE, outcome=STATUS_REFRESHED,
            relation_type=relation)
        return {"status": STATUS_REFRESHED, "edge_id": row_id, "edge_key": key,
                "source_node_id": int(source_node["id"]), "relation_type": relation,
                "target_node_id": int(target_node["id"])}

    cur.execute(
        f"""INSERT INTO {_schema.EDGES_TABLE}
        (owner_user_id, edge_key, source_node_id, relation_type, target_node_id,
         provenance_type, provenance_ref, confidence, valid_from, valid_to,
         lifecycle_state, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (owner, key, int(source_node["id"]), relation, int(target_node["id"]),
         source_prov, ref, score, from_iso, to_iso,
         _model.LIFECYCLE_ACTIVE, now_iso, now_iso),
    )
    cur.execute(
        f"SELECT id FROM {_schema.EDGES_TABLE} WHERE owner_user_id = ? AND edge_key = ?",
        (owner, key),
    )
    inserted = cur.fetchone()
    row_id = int(_row_value(inserted, "id", 0)) if inserted is not None else 0

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_GRAPH_WRITE, object_type=relation,
        object_id=row_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    _telemetry.emit(
        _telemetry.EVENT_GRAPH_WRITE, outcome=STATUS_WRITTEN,
        relation_type=relation)
    return {"status": STATUS_WRITTEN, "edge_id": row_id, "edge_key": key,
            "source_node_id": int(source_node["id"]), "relation_type": relation,
            "target_node_id": int(target_node["id"])}


def retire_edge(
    cur,
    *,
    owner_user_id: int,
    edge_id: object,
    valid_to: object = None,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> bool:
    """Close a relationship without deleting it. Owner-scoped.

    ``valid_to`` records *when* it stopped being true, which is the field that
    makes a retired edge still useful: "the business owned this property until
    March" is an answer, and a deleted row is not.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return False
    try:
        row_id = int(edge_id)
    except (TypeError, ValueError):
        return False
    _schema.require_private_schema(cur)
    now_iso = _facts._now_iso()
    closed = _facts._iso(valid_to, default=now_iso) or now_iso
    cur.execute(
        f"SELECT id, relation_type FROM {_schema.EDGES_TABLE} "
        f"WHERE owner_user_id = ? AND id = ?",
        (owner, row_id),
    )
    found = cur.fetchone()
    if found is None:
        return False
    cur.execute(
        f"UPDATE {_schema.EDGES_TABLE} "
        f"SET lifecycle_state = ?, valid_to = ?, updated_at = ? "
        f"WHERE owner_user_id = ? AND id = ?",
        (_model.LIFECYCLE_SUPERSEDED, closed, now_iso, owner, row_id),
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_GRAPH_WRITE,
        object_type=str(_row_value(found, "relation_type", 1)),
        object_id=row_id, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def _edge_to_dict(row) -> dict:
    data = dict(row)
    data["provenance"] = decode_provenance_ref(data.get("provenance_ref")).__dict__.copy()
    return data


def list_nodes(
    cur,
    *,
    owner_user_id: int,
    node_types: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    sensitivity_ceiling: object = _model.SENSITIVITY_RESTRICTED,
    include_inactive: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Nodes belonging to ``owner_user_id``. Never crosses an owner boundary.

    Like :func:`facts.list_facts`, the owner is a required positional-by-keyword
    argument rather than an optional filter, so there is no code path in this
    module that can produce a query without it.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    _schema.require_private_schema(cur)

    clauses = ["owner_user_id = ?"]
    params: list[Any] = [owner]

    if not include_inactive:
        clauses.append("lifecycle_state = ?")
        params.append(_model.LIFECYCLE_ACTIVE)

    wanted_types = [t for t in (_model.normalize_node_type(x) for x in (node_types or ())) if t]
    if node_types and not wanted_types:
        # Asked for types, named none this package knows. Returning everything
        # would be the inversion of the request, which is the dangerous
        # direction for a private store.
        return []
    if wanted_types:
        clauses.append(f"node_type IN ({','.join('?' * len(wanted_types))})")
        params.extend(wanted_types)

    wanted_domains = [d for d in (_model.normalize_domain(x) for x in (domains or ())) if d]
    if domains and not wanted_domains:
        return []
    if wanted_domains:
        clauses.append(f"domain IN ({','.join('?' * len(wanted_domains))})")
        params.extend(wanted_domains)

    ceiling = _model.normalize_sensitivity(sensitivity_ceiling)
    if not ceiling:
        return []
    releasable = [
        name for name in _model.SENSITIVITIES
        if _model.SENSITIVITY_RANK[name] <= _model.SENSITIVITY_RANK[ceiling]
    ]
    clauses.append(f"sensitivity IN ({','.join('?' * len(releasable))})")
    params.extend(releasable)

    bounded = max(1, min(int(limit or 100), 500))
    params.extend([bounded, max(0, int(offset or 0))])

    cur.execute(
        f"SELECT * FROM {_schema.NODES_TABLE} WHERE {' AND '.join(clauses)} "
        f"ORDER BY id ASC LIMIT ? OFFSET ?",
        params,
    )
    return [_row(row) for row in cur.fetchall()]


def neighbors(
    cur,
    *,
    owner_user_id: int,
    node_id: object,
    relations: Sequence[str] | None = None,
    direction: str = DIRECTION_OUT,
    include_inactive: bool = False,
    limit: int = 50,
) -> list[dict]:
    """One traversal step from ``node_id``, owner-scoped and bounded.

    Returns edge rows augmented with ``other_node_id`` and ``other_node_type``,
    which is what a traversal actually needs — the id to visit next and enough
    type information to decide whether visiting it is in scope.

    ``relations`` is the allowlist Stage 16 asks for. It is applied here rather
    than filtered out afterwards, because a step that fetches every edge and
    then discards most of them has already paid the cost the bound exists to
    prevent, and has already had those rows in memory.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    node = get_node(cur, owner_user_id=owner, node_id=node_id)
    if node is None:
        # Absent, foreign, or malformed — one empty list for all three.
        return []
    row_id = int(node["id"])

    wanted = [r for r in (_model.normalize_relation(x) for x in (relations or ())) if r]
    if relations and not wanted:
        return []

    heading = str(direction or DIRECTION_OUT).strip().lower()
    if heading not in (DIRECTION_OUT, DIRECTION_IN, DIRECTION_BOTH):
        heading = DIRECTION_OUT

    clauses = ["owner_user_id = ?"]
    params: list[Any] = [owner]
    if heading == DIRECTION_OUT:
        clauses.append("source_node_id = ?")
        params.append(row_id)
    elif heading == DIRECTION_IN:
        clauses.append("target_node_id = ?")
        params.append(row_id)
    else:
        clauses.append("(source_node_id = ? OR target_node_id = ?)")
        params.extend([row_id, row_id])

    if not include_inactive:
        clauses.append("lifecycle_state = ?")
        params.append(_model.LIFECYCLE_ACTIVE)
    if wanted:
        clauses.append(f"relation_type IN ({','.join('?' * len(wanted))})")
        params.extend(wanted)

    bounded = max(1, min(int(limit or 50), MAX_NEIGHBOURS))
    params.append(bounded)

    cur.execute(
        f"SELECT * FROM {_schema.EDGES_TABLE} WHERE {' AND '.join(clauses)} "
        f"ORDER BY id ASC LIMIT ?",
        params,
    )
    edges = [_edge_to_dict(row) for row in cur.fetchall()]

    out: list[dict] = []
    for edge in edges:
        other_id = (
            int(edge["target_node_id"])
            if int(edge["source_node_id"]) == row_id
            else int(edge["source_node_id"])
        )
        other = get_node(cur, owner_user_id=owner, node_id=other_id)
        if other is None:
            # The far end is not visible to this owner. That should be
            # impossible — `record_edge` resolves both endpoints under one owner
            # — so it means the row predates this writer or was inserted around
            # it. Dropping it is the fail-closed reading, and the log line is
            # how the anomaly gets noticed rather than traversed.
            LOGGER.warning(
                "PRIVATE_GRAPH_DANGLING_EDGE owner=%s edge=%s", owner, edge.get("id"))
            continue
        edge["other_node_id"] = other_id
        edge["other_node_type"] = str(other.get("node_type") or "")
        edge["other_lifecycle_state"] = str(other.get("lifecycle_state") or "")
        edge["direction"] = (
            DIRECTION_OUT if int(edge["source_node_id"]) == row_id else DIRECTION_IN)
        out.append(edge)
    return out


def count_nodes(cur, *, owner_user_id: int) -> int:
    """Active node count for one owner. Owner-scoped for the Stage 14 reason.

    A count is the cheapest existence oracle there is: ``COUNT(*)`` without the
    owner predicate reports the size of every member's graph, and a count that
    responds to a node id reports whether that node exists. Both are answered
    here only within one owner.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {_schema.NODES_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ?",
        (owner, _model.LIFECYCLE_ACTIVE),
    )
    row = cur.fetchone()
    return int(_row_value(row, "n", 0)) if row is not None else 0


def count_edges(cur, *, owner_user_id: int) -> int:
    """Active edge count for one owner."""
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {_schema.EDGES_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ?",
        (owner, _model.LIFECYCLE_ACTIVE),
    )
    row = cur.fetchone()
    return int(_row_value(row, "n", 0)) if row is not None else 0
