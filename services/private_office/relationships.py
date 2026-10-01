"""Relationship Intelligence — people, read out of the substrate they live in.

What this module is
-------------------
The Private Office's view of *people*: the advisors, family members, partners
and providers a member's affairs actually involve. It is deliberately a
composition layer, not a store:

* A person **is a PERSON node** in the private graph, created through
  ``graph.upsert_node`` — the only sanctioned node creator.
* What is known about a person — their name, their role, anything else — **is
  a private fact** about that node, written through ``facts.record_fact`` with
  ``USER_ASSERTED`` provenance. There is no ``people`` table with a ``name``
  column, because the moment a person's details live outside the fact store
  they stop carrying provenance, sensitivity and staleness, and "why does
  PulseSoc know this?" stops having an answer for exactly the data it matters
  most for.
* A person's **commitments** are the OBLIGATION and REQUEST records whose
  ``related_entity_ids`` cite the person's node ref; their **timeline** is the
  merge of their facts, their edges and every record that cites them. Nothing
  here is inferred, scored, or guessed — every line traces to a row the member
  (or a reviewed extraction) put there, and every payload carries the evidence
  refs to prove it.

This module therefore contains **no INSERT statements at all**. The write
boundary test keeps that true structurally: a people-store that grew its own
tables would be the second fact store the package's standing rules exist to
prevent.

``prepare_briefing`` is the deterministic "before you meet them" aggregation:
identity, open commitments, recent activity, connections — each section built
from rows, each row cited. It is a *view*; the Private Briefings capability
may later persist one, but preparing it asserts nothing and writes nothing.
"""

from __future__ import annotations

from typing import Any

from services.private_office import audit
from services.private_office import evidence
from services.private_office import facts as facts_mod
from services.private_office import graph as graph_mod
from services.private_office import model
from services.private_office import records as records_mod

#: Identity facts. Closed vocabulary so a directory read is two fact types,
#: not a scan; anything else a member records about a person is still shown on
#: the profile, just not treated as identity.
FACT_NAME = "name"
FACT_ROLE = "relationship_role"
IDENTITY_FACT_TYPES: tuple[str, ...] = (FACT_NAME, FACT_ROLE)

#: The record primitives a person can be committed through, and the evidence
#: kind each serializes to.
RECORD_KINDS: dict[str, str] = {
    records_mod.TYPE_OBLIGATION: "obligation",
    records_mod.TYPE_EVENT: "event",
    records_mod.TYPE_DECISION: "decision",
    records_mod.TYPE_REQUEST: "request",
    records_mod.TYPE_RISK: "risk",
    records_mod.TYPE_OPPORTUNITY: "opportunity",
}
COMMITMENT_TYPES: tuple[str, ...] = (records_mod.TYPE_OBLIGATION, records_mod.TYPE_REQUEST)

MAX_DIRECTORY = 200
MAX_TIMELINE = 50
MAX_NAME_CHARS = 120


class PrivateRelationshipRejected(ValueError):
    """A person write or read this module refuses."""


# ---------------------------------------------------------------------------
# Writes — everything through the canonical writers
# ---------------------------------------------------------------------------

def add_person(
    cur,
    *,
    owner_user_id: int,
    name: str,
    role: str = "",
    domain: object = None,
    sensitivity: object = None,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """One new PERSON node plus its identity facts. Returns the profile summary.

    Every call creates a new person — two advisors who share a name are two
    people, and merging them because their names collide would be the graph
    silently rewriting the member's world. Dedupe is the member's decision,
    made on a screen that shows both.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateRelationshipRejected("owner_user_id is required")
    clean_name = " ".join(str(name or "").split())[:MAX_NAME_CHARS]
    if not clean_name:
        raise PrivateRelationshipRejected("a person needs a name")
    clean_role = " ".join(str(role or "").split())[:MAX_NAME_CHARS]

    node = graph_mod.upsert_node(
        cur, owner_user_id=owner, node_type=model.NODE_PERSON,
        external_ref="", sensitivity=sensitivity, domain=domain,
        actor_user_id=actor_user_id or owner, purpose="user_request",
    )
    node_id = int(node["node_id"])

    facts_mod.record_fact(
        cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
        subject_id=str(node_id), fact_type=FACT_NAME, value=clean_name,
        value_type=model.VALUE_STRING,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=domain, sensitivity=sensitivity,
        actor_user_id=actor_user_id or owner, purpose="user_request",
    )
    if clean_role:
        facts_mod.record_fact(
            cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
            subject_id=str(node_id), fact_type=FACT_ROLE, value=clean_role,
            value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED,
            domain=domain, sensitivity=sensitivity,
            actor_user_id=actor_user_id or owner, purpose="user_request",
        )

    return {
        "node_id": node_id,
        "ref": evidence.format_ref("node", node_id),
        "name": clean_name,
        "role": clean_role,
        "domain": node["domain"],
        "sensitivity": node["sensitivity"],
    }


def record_person_fact(
    cur,
    *,
    owner_user_id: int,
    node_id: int,
    fact_type: str,
    value: object,
    value_type: str = model.VALUE_STRING,
    actor_user_id: int | None = None,
) -> dict[str, Any]:
    """A member-asserted fact about one of their own people.

    The person check runs first, so a fact cannot be attached to a node that
    is not the caller's or is not a person — the canonical writer would accept
    any node id, and "facts about node 7" quietly meaning a property would be
    a category error nobody could see on the screen that made it.
    """
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        raise PrivateRelationshipRejected("person not found")
    return facts_mod.record_fact(
        cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
        subject_id=str(int(node_id)), fact_type=fact_type, value=value,
        value_type=value_type,
        provenance_type=model.PROVENANCE_USER_ASSERTED,
        domain=person.get("domain"), sensitivity=person.get("sensitivity"),
        actor_user_id=actor_user_id or owner, purpose="user_request",
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _person_node(cur, *, owner_user_id: int, node_id: object) -> dict | None:
    node = graph_mod.get_node(cur, owner_user_id=int(owner_user_id or 0), node_id=node_id)
    if node is None or str(node.get("node_type")) != model.NODE_PERSON:
        # Absent, foreign, or not a person — one answer for all three.
        return None
    return node


def _identity_map(cur, *, owner_user_id: int, node_ids: list[int]) -> dict[int, dict]:
    """node_id → {"name", "role", "name_fact_id"} from the fact store."""
    if not node_ids:
        return {}
    rows = facts_mod.list_facts_for_subjects(
        cur, owner_user_id=owner_user_id, subject_type=facts_mod.SUBJECT_NODE,
        subject_ids=[str(n) for n in node_ids], fact_types=IDENTITY_FACT_TYPES,
    )
    out: dict[int, dict] = {}
    # Rows arrive newest-first; the first name seen per node is the current one.
    for row in rows:
        try:
            subject = int(str(row.get("subject_id") or "0"))
        except ValueError:
            continue
        entry = out.setdefault(subject, {"name": "", "role": "", "name_fact_id": 0})
        if row.get("fact_type") == FACT_NAME and not entry["name"]:
            entry["name"] = str(row.get("typed_value") or "")
            entry["name_fact_id"] = int(row.get("id") or 0)
        elif row.get("fact_type") == FACT_ROLE and not entry["role"]:
            entry["role"] = str(row.get("typed_value") or "")
    return out


def _records_citing(cur, *, owner_user_id: int, node_ref: str,
                    record_types: tuple[str, ...]) -> list[dict]:
    """Active records whose ``related_entity_ids`` cite ``node_ref``.

    A linear filter over the owner's bounded record lists rather than a LIKE
    over the comma-joined column: the lists are already capped, and a LIKE on
    ``node:1`` would also match ``node:12``.
    """
    found: list[dict] = []
    for record_type in record_types:
        for row in records_mod.list_records(
                cur, record_type=record_type, owner_user_id=owner_user_id,
                limit=records_mod.MAX_LIMIT):
            if node_ref in (row.get("related_entity_ids") or []):
                row = dict(row)
                row["record_type"] = record_type
                row["ref"] = evidence.format_ref(RECORD_KINDS[record_type], int(row["id"]))
                found.append(row)
    return found


def _is_open(record: dict) -> bool:
    spec_closing = records_mod.SPECS[record["record_type"]]["closing"]
    return record.get("status") not in spec_closing


def directory(cur, *, owner_user_id: int, limit: int = MAX_DIRECTORY) -> list[dict[str, Any]]:
    """Every person the member has, newest first, with counts that are counts.

    ``open_commitments`` and ``connections`` are computed from the rows this
    module can show on the profile — never an estimate, so tapping through
    always finds exactly what the number promised.
    """
    owner = int(owner_user_id or 0)
    nodes = graph_mod.list_nodes(
        cur, owner_user_id=owner, node_types=[model.NODE_PERSON],
        limit=max(1, min(int(limit or MAX_DIRECTORY), MAX_DIRECTORY)))
    # list_nodes returns id ASC; the directory promises newest first.
    nodes = list(reversed(nodes))
    node_ids = [int(n["id"]) for n in nodes]
    identity = _identity_map(cur, owner_user_id=owner, node_ids=node_ids)

    commitments_by_node: dict[int, int] = {}
    for record_type in COMMITMENT_TYPES:
        for row in records_mod.list_records(
                cur, record_type=record_type, owner_user_id=owner,
                limit=records_mod.MAX_LIMIT):
            row = dict(row)
            row["record_type"] = record_type
            if not _is_open(row):
                continue
            for ref in row.get("related_entity_ids") or []:
                parsed = evidence.parse_ref(ref)
                if parsed and parsed[0] == "node":
                    commitments_by_node[parsed[1]] = commitments_by_node.get(parsed[1], 0) + 1

    out = []
    for node in nodes:
        node_id = int(node["id"])
        who = identity.get(node_id, {})
        out.append({
            "node_id": node_id,
            "ref": evidence.format_ref("node", node_id),
            "name": who.get("name") or "",
            "role": who.get("role") or "",
            "domain": node.get("domain") or "",
            "sensitivity": node.get("sensitivity") or "",
            "created_at": node.get("created_at") or "",
            "open_commitments": commitments_by_node.get(node_id, 0),
            "connections": len(graph_mod.neighbors(
                cur, owner_user_id=owner, node_id=node_id,
                direction=graph_mod.DIRECTION_BOTH)),
        })
    return out


def _fact_view(row: dict) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "ref": evidence.format_ref("fact", int(row.get("id") or 0)),
        "fact_type": row.get("fact_type") or "",
        "value": str(row.get("typed_value") or ""),
        "value_type": row.get("value_type") or "",
        "provenance_type": row.get("provenance_type") or "",
        "observed_at": row.get("observed_at") or "",
        "freshness": row.get("freshness") or {},
    }


def _record_view(row: dict) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "ref": row.get("ref") or "",
        "record_type": row.get("record_type") or "",
        "title": row.get("title") or "",
        "status": row.get("status") or "",
        "due_at": row.get("due_at") or row.get("deadline_at") or None,
        "created_at": row.get("created_at") or "",
        "open": _is_open(row),
    }


def profile(cur, *, owner_user_id: int, node_id: object) -> dict[str, Any] | None:
    """Everything the Office holds about one person, each line cited."""
    owner = int(owner_user_id or 0)
    person = _person_node(cur, owner_user_id=owner, node_id=node_id)
    if person is None:
        return None
    person_id = int(person["id"])
    node_ref = evidence.format_ref("node", person_id)

    fact_rows = facts_mod.list_facts_for_subjects(
        cur, owner_user_id=owner, subject_type=facts_mod.SUBJECT_NODE,
        subject_ids=[str(person_id)])
    identity = _identity_map(cur, owner_user_id=owner, node_ids=[person_id])
    who = identity.get(person_id, {})

    edges = graph_mod.neighbors(
        cur, owner_user_id=owner, node_id=person_id,
        direction=graph_mod.DIRECTION_BOTH)
    citing = _records_citing(
        cur, owner_user_id=owner, node_ref=node_ref,
        record_types=tuple(RECORD_KINDS))
    commitments = [r for r in citing if r["record_type"] in COMMITMENT_TYPES]

    timeline: list[dict[str, Any]] = []
    for row in fact_rows:
        view = _fact_view(row)
        timeline.append({"at": view["observed_at"], "kind": "fact",
                         "ref": view["ref"],
                         "label": f"{view['fact_type']}: {view['value']}"})
    for edge in edges:
        timeline.append({
            "at": str(edge.get("created_at") or ""), "kind": "edge",
            "ref": evidence.format_ref("edge", int(edge.get("id") or 0)),
            "label": f"{edge.get('relation_type') or ''} "
                     f"{edge.get('other_node_type') or ''}".strip(),
        })
    for row in citing:
        timeline.append({"at": str(row.get("created_at") or ""),
                         "kind": row["record_type"].lower(), "ref": row["ref"],
                         "label": row.get("title") or ""})
    timeline.sort(key=lambda item: item["at"], reverse=True)

    audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=audit.ACTION_GRAPH_READ, object_type=model.NODE_PERSON,
        object_id=str(person_id), purpose="user_request",
        result_count=len(fact_rows) + len(edges) + len(citing),
    )

    return {
        "node_id": person_id,
        "ref": node_ref,
        "name": who.get("name") or "",
        "role": who.get("role") or "",
        "domain": person.get("domain") or "",
        "sensitivity": person.get("sensitivity") or "",
        "created_at": person.get("created_at") or "",
        "facts": [_fact_view(row) for row in fact_rows],
        "connections": [{
            "edge_id": int(edge.get("id") or 0),
            "ref": evidence.format_ref("edge", int(edge.get("id") or 0)),
            "relation_type": edge.get("relation_type") or "",
            "other_node_id": int(edge.get("other_node_id") or 0),
            "other_node_type": edge.get("other_node_type") or "",
        } for edge in edges],
        "commitments": [_record_view(row) for row in commitments],
        "records": [_record_view(row) for row in citing],
        "timeline": timeline[:MAX_TIMELINE],
    }


def prepare_briefing(cur, *, owner_user_id: int, node_id: object) -> dict[str, Any] | None:
    """The deterministic "before you meet them" view, every section cited.

    Built entirely from :func:`profile`; persists nothing, asserts nothing.
    The evidence list is the union of every ref the sections quote, so a
    reader can walk from any line to the row behind it.
    """
    owner = int(owner_user_id or 0)
    data = profile(cur, owner_user_id=owner, node_id=node_id)
    if data is None:
        return None

    open_commitments = [c for c in data["commitments"] if c["open"]]
    recent = data["timeline"][:10]
    refs = evidence.normalize_refs(
        [data["ref"]]
        + [f["ref"] for f in data["facts"]]
        + [c["ref"] for c in open_commitments]
        + [item["ref"] for item in recent])

    audit.record(
        cur, actor_user_id=owner, owner_user_id=owner,
        action=audit.ACTION_CONTEXT_RETRIEVED, object_type=model.NODE_PERSON,
        object_id=str(data["node_id"]), purpose="briefing_candidate",
        result_count=len(refs),
    )

    return {
        "person": {
            "node_id": data["node_id"], "ref": data["ref"],
            "name": data["name"], "role": data["role"],
            "domain": data["domain"],
        },
        "known_facts": data["facts"],
        "open_commitments": open_commitments,
        "recent_activity": recent,
        "connections": data["connections"],
        "evidence": refs,
        "generated_from": "private_office_records",
    }
