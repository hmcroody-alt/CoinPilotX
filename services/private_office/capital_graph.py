"""The Capital Graph — one read boundary over the private graph.

Why this module exists at all
-----------------------------
``graph.py`` stores nodes and edges. ``retrieval.py`` decides what a caller may
be told. Neither of them owns the *view*: the thing a member opens, which needs
a shape — a summary, an entity, that entity's relationships — and which has to
say honestly how sure it is of every line it draws.

Without this boundary that shaping goes into a route handler and, three weeks
later, into a second route handler and into the UNDX executor, and the three
disagree about whether a stale value is worth mentioning. Worse, the shortest
path from a route to a node is ``graph.get_node``, and a handler that reaches
for it has walked around every gate in ``retrieval`` — owner, authorization,
sensitivity, domain, purpose — without noticing, because the row comes back and
looks correct.

So: **every read in this module goes through** :func:`retrieval.retrieve`. There
is no import of ``graph`` here and there is no SQL here. That is not an
accident to be preserved by care; ``tests/private_office/test_capital_graph.py``
asserts the module names neither, and the repository-wide write-boundary guard
already forbids the tables.

What it will not do
-------------------
**No total.** There is no net worth, no portfolio value, no "your assets" sum,
and adding one is not a small feature. A total is a single number that a member
will read as fact and act on, assembled from rows whose provenance runs from a
bank read-back to a figure somebody typed in 2023, over a set that
:func:`retrieval.retrieve` explicitly bounds and marks ``truncated`` — so the
sum would be wrong in a way the arithmetic cannot show. :data:`NO_AGGREGATE_VALUE`
records that as a decision rather than an omission, and the surfaces carry
``counted`` and ``complete`` instead: how many of a thing were *seen*, and
whether that was all of them.

**No resolution.** A conflict is carried out to the caller with both competing
values intact and ``unresolved`` true. Picking the stronger provenance would be
one line and would destroy the only answer that is actually correct — "your
insurer's record says March and the policy document says April, which is
right?".

**No inference.** A truth state is derived from what the store holds, never from
what would be plausible. A node with no facts is ``MISSING``, which is a
sentence a screen can render; it is never quietly given the state of a
neighbour.

Views
-----
A view is a product name for one of the retrieval intents, and the mapping is
deliberately a mapping rather than a new intent. ``property_portfolio`` already
walks exactly the capital question — what is owned, what secures it, what covers
it — under a policy that has been reviewed, and inventing
``INTENT_CAPITAL_STRUCTURE`` beside it would mean a second allowlist to keep in
step with the first. Views may be added here freely; intents may not.

Truth states
------------
:func:`truth_state` collapses provenance, freshness and conflict into the seven
words the product speaks: KNOWN, INFERRED, ESTIMATED, STALE, MISSING,
CONFLICTING, PRO_REVIEW. The precedence is fixed and it never rounds up — a
disputed node is CONFLICTING even when one of the competing rows is VERIFIED,
because the dispute is the more important fact about it.
"""

from __future__ import annotations

from typing import Any, Sequence

from services.private_office import model as _model
from services.private_office import office as _office
from services.private_office import retrieval as _retrieval

#: The matrix row this view belongs to. Named once so the route gate, the UNDX
#: executor and the product state cannot drift onto different feature ids.
FEATURE_ID = "capital_graph"

#: Stated as a constant so that a future caller looking for a total finds the
#: reason there isn't one instead of adding it. See the module docstring.
NO_AGGREGATE_VALUE = (
    "The Capital Graph reports what is recorded and how well it is known. It "
    "does not compute a net worth or any other total: the underlying rows carry "
    "mixed provenance and the traversal is bounded, so a sum would be a "
    "confident number assembled from an admittedly partial set."
)

# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
VIEW_HOLDINGS = "holdings"
VIEW_COVERAGE = "coverage"
VIEW_STRUCTURE = "structure"
VIEW_DOCUMENTS = "documents"

#: Product view -> retrieval intent. Every value must be a key of
#: ``retrieval.INTENTS``; a test pins that so a renamed intent fails here rather
#: than at the first member request.
VIEW_INTENTS: dict[str, str] = {
    VIEW_HOLDINGS: _retrieval.INTENT_PROPERTY_PORTFOLIO,
    VIEW_COVERAGE: _retrieval.INTENT_INSURANCE_COVERAGE,
    VIEW_STRUCTURE: _retrieval.INTENT_BUSINESS_STRUCTURE,
    VIEW_DOCUMENTS: _retrieval.INTENT_LEGAL_DOCUMENTS,
}

#: Health and identity are reachable through ``retrieval`` and are deliberately
#: absent here. The Capital Graph is a financial and legal surface, and Stage 17
#: exists precisely to stop a health record arriving in the same payload as a
#: property portfolio. Adding either to :data:`VIEW_INTENTS` would put the join
#: policy's whole purpose behind one dictionary entry.
VIEWS: tuple[str, ...] = (VIEW_HOLDINGS, VIEW_COVERAGE, VIEW_STRUCTURE,
                          VIEW_DOCUMENTS)

DEFAULT_VIEW = VIEW_HOLDINGS

DENIED_UNKNOWN_VIEW = "unknown_view"
DENIED_NODE_NOT_FOUND = "node_not_found"


def normalize_view(value: object) -> str | None:
    """Canonical view name, or ``None``.

    Returns ``None`` rather than defaulting, for the reason ``model`` gives at
    length: a caller who named a view this module has never heard of has a bug,
    and quietly serving them a different view hides it.
    """
    name = str(value or "").strip().lower()
    return name if name in VIEW_INTENTS else None


# ---------------------------------------------------------------------------
# Truth states
# ---------------------------------------------------------------------------
TRUTH_KNOWN = "KNOWN"
TRUTH_INFERRED = "INFERRED"
TRUTH_ESTIMATED = "ESTIMATED"
TRUTH_STALE = "STALE"
TRUTH_MISSING = "MISSING"
TRUTH_CONFLICTING = "CONFLICTING"
TRUTH_PRO_REVIEW = "PRO_REVIEW"

TRUTH_STATES: tuple[str, ...] = (
    TRUTH_KNOWN, TRUTH_INFERRED, TRUTH_ESTIMATED, TRUTH_STALE,
    TRUTH_MISSING, TRUTH_CONFLICTING, TRUTH_PRO_REVIEW,
)

#: Provenance that supports a plain "we know this". The three sourced kinds and
#: the member's own statement all qualify: a member saying what they own is a
#: perfectly good reason to believe it, and the *reason* stays visible in the
#: per-fact provenance either way. What does not qualify is anything the system
#: worked out for itself.
_KNOWN_PROVENANCE = frozenset({
    _model.PROVENANCE_VERIFIED,
    _model.PROVENANCE_PROVIDER_ASSERTED,
    _model.PROVENANCE_DOCUMENT_EXTRACTED,
    _model.PROVENANCE_USER_ASSERTED,
})

_TRUTH_BY_PROVENANCE: dict[str, str] = {
    _model.PROVENANCE_INFERRED: TRUTH_INFERRED,
    _model.PROVENANCE_ESTIMATED: TRUTH_ESTIMATED,
    _model.PROVENANCE_STALE: TRUTH_STALE,
    _model.PROVENANCE_CONFLICTING: TRUTH_CONFLICTING,
}


def truth_state(projected_facts: Sequence[dict], *, conflicted: bool) -> str:
    """One word for how well a subject is known.

    Precedence, and none of it rounds up:

    1. ``CONFLICTING`` — a live disagreement outranks everything, including a
       VERIFIED row on one side of it. A screen that showed "verified" over two
       contradictory values would be reporting the half of the store it
       preferred.
    2. ``MISSING`` — nothing is recorded. Never inferred from a neighbour.
    3. ``STALE`` — recorded, past its freshness horizon. The value is still
       shown; the state says not to rely on it.
    4. Otherwise the *weakest* provenance present, so a node whose ownership is
       verified and whose value was estimated reads ESTIMATED. Reporting the
       strongest would let one good row vouch for the rest.
    5. ``PRO_REVIEW`` for anything this module has not been taught, which is by
       definition a claim nobody here has reasoned about.
    """
    if conflicted:
        return TRUTH_CONFLICTING
    rows = [row for row in (projected_facts or ()) if isinstance(row, dict)]
    if not rows:
        return TRUTH_MISSING
    if any((row.get("freshness") or {}).get("stale") for row in rows):
        return TRUTH_STALE

    states: set[str] = set()
    for row in rows:
        raw = (row.get("provenance") or {}).get("provenance_type")
        known = _model.normalize_provenance(raw)
        if known is None:
            states.add(TRUTH_PRO_REVIEW)
        elif known in _KNOWN_PROVENANCE:
            states.add(TRUTH_KNOWN)
        else:
            states.add(_TRUTH_BY_PROVENANCE.get(known, TRUTH_PRO_REVIEW))

    # Weakest wins. The order is the answer to "what is the least this subject
    # is", and PRO_REVIEW sits at the bottom because an unrecognised claim is
    # weaker evidence than one we can name.
    for candidate in (TRUTH_PRO_REVIEW, TRUTH_CONFLICTING, TRUTH_STALE,
                      TRUTH_ESTIMATED, TRUTH_INFERRED, TRUTH_KNOWN):
        if candidate in states:
            return candidate
    return TRUTH_PRO_REVIEW


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------
#: Node columns that may reach a client. Allowlist, for the reason
#: ``office._PROJECTED_FIELDS`` gives: a projection built by removing known-bad
#: fields leaks the next column somebody adds.
#:
#: ``owner_user_id`` is absent because the caller is the owner and echoing it
#: back is an identifier nobody needed. ``node_key`` is absent because it is a
#: derived storage handle; ``external_ref`` carries the reference the member
#: themselves supplied, which is the part they can recognise.
_NODE_FIELDS: tuple[str, ...] = (
    "node_type",
    "external_ref",
    "lifecycle_state",
    "sensitivity",
    "domain",
    "created_at",
    "updated_at",
)

_EDGE_FIELDS: tuple[str, ...] = (
    "relation_type",
    "lifecycle_state",
    "created_at",
    "updated_at",
)


def project_node(node: dict, *, facts: Sequence[dict] = (),
                 conflicted: bool = False) -> dict:
    """One graph node, shaped for display, with its truth state attached.

    ``id`` is included for the same reason ``office.project_fact`` includes it:
    every read is owner-scoped, so an id the caller was not given returns
    nothing. It is a handle, not an address.
    """
    if not isinstance(node, dict):
        return {}
    out: dict[str, Any] = {"id": int(node.get("id") or 0)}
    for name in _NODE_FIELDS:
        value = node.get(name)
        out[name] = "" if value is None else str(value)
    out["truth"] = truth_state(facts, conflicted=conflicted)
    out["fact_count"] = len([f for f in facts if isinstance(f, dict)])
    return out


def project_edge(edge: dict) -> dict:
    """One relationship, shaped for display.

    Both endpoint ids are carried — a relationship the client cannot place
    between two nodes is a row, not an edge — and the provenance is carried in
    the same shape a fact's is, because "why do you think I own this" is the
    same question as "why do you think this is worth that".
    """
    if not isinstance(edge, dict):
        return {}
    out: dict[str, Any] = {
        "id": int(edge.get("id") or 0),
        "source_node_id": int(edge.get("source_node_id") or 0),
        "target_node_id": int(edge.get("target_node_id") or 0),
    }
    for name in _EDGE_FIELDS:
        value = edge.get(name)
        out[name] = "" if value is None else str(value)
    ref = dict(edge.get("provenance") or {})
    provenance_type = str(edge.get("provenance_type") or "")
    out["provenance"] = {
        "source_type": str(ref.get("source_type") or ""),
        "source_id": str(ref.get("source_id") or ""),
        "has_source_document": bool(str(ref.get("locator") or "").strip()),
        "provenance_type": provenance_type,
        "verification": _office.verification_state(provenance_type),
    }
    return out


#: What a conflict may say to the member. The competing *values* are included
#: on purpose — this is the owner's own data and "two sources disagree" without
#: saying what they disagree about is an alarm with no action attached — while
#: ``fact_key`` and the raw locator stay behind the projection.
def project_conflict(conflict: dict) -> dict:
    """One unresolved contradiction, shaped for display and left unresolved."""
    if not isinstance(conflict, dict):
        return {}
    competing = []
    for entry in conflict.get("competing") or ():
        if not isinstance(entry, dict):
            continue
        provenance_type = str(entry.get("provenance_type") or "")
        competing.append({
            "fact_id": int(entry.get("fact_id") or 0),
            "value": "" if entry.get("typed_value") is None
                     else str(entry.get("typed_value")),
            "value_type": str(entry.get("value_type") or ""),
            "provenance_type": provenance_type,
            "verification": _office.verification_state(provenance_type),
            "observed_at": str(entry.get("observed_at") or ""),
            "valid_from": str(entry.get("valid_from") or ""),
            "valid_to": str(entry.get("valid_to") or ""),
            "stale": bool((entry.get("freshness") or {}).get("stale")),
        })
    return {
        "conflict_id": str(conflict.get("conflict_id") or ""),
        "subject_id": str(conflict.get("subject_id") or ""),
        "fact_type": str(conflict.get("fact_type") or ""),
        "reason": str(conflict.get("reason") or ""),
        "competing": competing,
        # Hard-coded, mirroring ``contradictions.detect_conflicts``. There is no
        # code path in this package that sets it false, and this module must not
        # become the first one.
        "unresolved": True,
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _facts_by_subject(projected: Sequence[dict],
                      raw: Sequence[dict]) -> dict[str, list[dict]]:
    """Group projected facts by the node they are about.

    ``office.project_fact`` deliberately drops ``subject_id`` — it is an
    internal handle that a client has no use for — so the grouping is done here
    against the raw rows, positionally, and the subject never reaches the
    output.

    The raw side is filtered with ``office.project_facts``'s own predicate
    before zipping. That is not defensive tidying: ``project_facts`` silently
    drops anything that is not a dict, so a single stray row would shift every
    later pairing by one and attach each node's facts to its neighbour — a
    corruption that produces a plausible screen and no error.
    """
    rows = [row for row in (raw or ()) if isinstance(row, dict)]
    grouped: dict[str, list[dict]] = {}
    for row, shaped in zip(rows, projected):
        if not isinstance(shaped, dict):
            continue
        grouped.setdefault(str(row.get("subject_id") or ""), []).append(shaped)
    return grouped


def _denied(view: str, reason: str) -> dict:
    """The refusal shape, identical for every reason and for empty data.

    A denial and an empty store return the same envelope with a different
    ``denied`` string. Nothing here says whether the thing asked for exists —
    that is the property that keeps this surface from being an oracle for
    another member's node ids.
    """
    return {
        "view": view,
        "intent": VIEW_INTENTS.get(view, ""),
        "nodes": [],
        "edges": [],
        "facts": [],
        "conflicts": [],
        "stale": [],
        "counted": {},
        "truth_counts": {},
        "bounds": {},
        "truncated": {},
        "complete": False,
        "denied": reason,
    }


def _assemble(context: dict, view: str) -> dict:
    """Shape one ``retrieval.retrieve`` result into a Capital Graph payload."""
    denied = str(context.get("denied") or "")
    if denied:
        return _denied(view, denied)

    raw_facts = list(context.get("relevant_facts") or ())
    projected_facts = _office.project_facts(raw_facts)
    by_subject = _facts_by_subject(projected_facts, raw_facts)

    conflicts = [project_conflict(c) for c in (context.get("conflicts") or ())]
    conflicted_subjects = {c["subject_id"] for c in conflicts if c.get("subject_id")}

    nodes = []
    for node in context.get("relevant_nodes") or ():
        if not isinstance(node, dict):
            continue
        subject = str(int(node.get("id") or 0))
        nodes.append(project_node(
            node,
            facts=by_subject.get(subject, ()),
            conflicted=subject in conflicted_subjects,
        ))
    nodes.sort(key=lambda row: (row.get("node_type", ""), row.get("id", 0)))

    edges = [project_edge(edge) for edge in (context.get("relevant_edges") or ())
             if isinstance(edge, dict)]
    edges.sort(key=lambda row: row.get("id", 0))

    counted: dict[str, int] = {}
    for row in nodes:
        counted[row["node_type"]] = counted.get(row["node_type"], 0) + 1

    truth_counts: dict[str, int] = {name: 0 for name in TRUTH_STATES}
    for row in nodes:
        truth_counts[row["truth"]] = truth_counts.get(row["truth"], 0) + 1

    truncated = dict(context.get("truncated") or {})
    complete = not any(bool(truncated.get(key))
                       for key in ("nodes", "edges", "facts"))

    return {
        "view": view,
        "intent": str(context.get("intent") or ""),
        "domains": list(context.get("domains") or ()),
        "sensitivity_ceiling": str(context.get("sensitivity_ceiling") or ""),
        "nodes": nodes,
        "edges": edges,
        "facts": projected_facts,
        "conflicts": conflicts,
        # Carried separately from the per-node truth state because a screen
        # needs both "which things are shaky" and "which single readings are
        # past their horizon", and deriving the second from the first is not
        # possible once the facts are grouped.
        "stale": [
            {
                "fact_id": int(entry.get("fact_id") or 0),
                "fact_type": str(entry.get("fact_type") or ""),
                "age_days": entry.get("age_days"),
                "horizon_days": entry.get("horizon_days"),
            }
            for entry in (context.get("stale_flags") or ())
            if isinstance(entry, dict)
        ],
        "counted": counted,
        "truth_counts": truth_counts,
        "bounds": dict(context.get("bounds") or {}),
        "truncated": truncated,
        # The honest replacement for a total: whether what is shown is all of
        # it. A client may say "3 properties" only while this is true, and
        # "3 properties so far" otherwise.
        "complete": complete,
        "denied": "",
    }


# ---------------------------------------------------------------------------
# The three reads
# ---------------------------------------------------------------------------
def summary(
    cur,
    *,
    owner_user_id: int,
    actor_user_id: int | None = None,
    view: object = DEFAULT_VIEW,
    purpose: str = "user_request",
    max_nodes: int = _retrieval.MAX_NODES,
) -> dict:
    """The Capital Graph overview for one owner and one view.

    Everything is derived from a single :func:`retrieval.retrieve` call, so the
    counts, the conflicts and the truth states all describe the same traversal.
    Two calls would let them describe different ones, and the visible symptom
    would be a summary claiming four properties above a list of three.
    """
    resolved_view = normalize_view(view)
    if resolved_view is None:
        return _denied(str(view or ""), DENIED_UNKNOWN_VIEW)

    context = _retrieval.retrieve(
        cur,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        intent=VIEW_INTENTS[resolved_view],
        purpose=purpose,
        max_nodes=max_nodes,
    )
    return _assemble(context, resolved_view)


def entity(
    cur,
    *,
    owner_user_id: int,
    node_id: object,
    actor_user_id: int | None = None,
    view: object = DEFAULT_VIEW,
    purpose: str = "user_request",
) -> dict:
    """One entity, its immediate neighbourhood, and everything asserted about it.

    Seeded on the requested node at depth 1: the member opening a property wants
    the policy that covers it and the loan it secures, not the whole estate.

    A node that is absent, belongs to somebody else, or is outside this view's
    domain and sensitivity all produce the same ``node_not_found`` refusal.
    ``retrieval`` already collapses the first two — ``graph.get_node`` returns
    ``None`` for both — and this function must not un-collapse them by reporting
    the third differently, or the difference between the answers becomes a way
    to test whether an id exists.
    """
    resolved_view = normalize_view(view)
    if resolved_view is None:
        return _denied(str(view or ""), DENIED_UNKNOWN_VIEW)

    wanted = str(node_id or "").strip()
    if not wanted:
        return _denied(resolved_view, DENIED_NODE_NOT_FOUND)

    context = _retrieval.retrieve(
        cur,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        intent=VIEW_INTENTS[resolved_view],
        purpose=purpose,
        seed_node_ids=[wanted],
        max_depth=1,
    )
    payload = _assemble(context, resolved_view)
    if payload["denied"]:
        return payload

    subject = next((row for row in payload["nodes"]
                    if str(row.get("id")) == wanted), None)
    if subject is None:
        return _denied(resolved_view, DENIED_NODE_NOT_FOUND)

    payload["entity"] = subject
    # The neighbours, without the subject repeated inside its own list.
    payload["related"] = [row for row in payload["nodes"]
                          if str(row.get("id")) != wanted]
    return payload


def relationships(
    cur,
    *,
    owner_user_id: int,
    node_id: object,
    actor_user_id: int | None = None,
    view: object = DEFAULT_VIEW,
    purpose: str = "user_request",
) -> dict:
    """The edges touching one entity, with the far end named.

    A thin projection of :func:`entity` rather than a second traversal, so the
    relationship list and the entity screen can never show different edges. The
    far end is resolved from the same node set for the same reason: an edge
    whose other end the caller was not shown is an id with nothing attached, and
    rendering it would invite a client to go and ask for it.
    """
    payload = entity(
        cur,
        owner_user_id=owner_user_id,
        node_id=node_id,
        actor_user_id=actor_user_id,
        view=view,
        purpose=purpose,
    )
    if payload["denied"]:
        return payload

    subject_id = int(payload["entity"]["id"])
    by_id = {int(row["id"]): row for row in payload["nodes"]}

    out = []
    for edge in payload["edges"]:
        source = int(edge["source_node_id"])
        target = int(edge["target_node_id"])
        if subject_id not in (source, target):
            continue
        other_id = target if source == subject_id else source
        other = by_id.get(other_id)
        if other is None:
            continue
        out.append({
            **edge,
            "direction": ("out" if source == subject_id else "in"),
            "other": other,
        })

    payload["relationships"] = out
    return payload


__all__ = [
    "FEATURE_ID", "NO_AGGREGATE_VALUE",
    "VIEW_HOLDINGS", "VIEW_COVERAGE", "VIEW_STRUCTURE", "VIEW_DOCUMENTS",
    "VIEWS", "VIEW_INTENTS", "DEFAULT_VIEW", "normalize_view",
    "TRUTH_KNOWN", "TRUTH_INFERRED", "TRUTH_ESTIMATED", "TRUTH_STALE",
    "TRUTH_MISSING", "TRUTH_CONFLICTING", "TRUTH_PRO_REVIEW", "TRUTH_STATES",
    "truth_state", "project_node", "project_edge", "project_conflict",
    "summary", "entity", "relationships",
    "DENIED_UNKNOWN_VIEW", "DENIED_NODE_NOT_FOUND",
]
