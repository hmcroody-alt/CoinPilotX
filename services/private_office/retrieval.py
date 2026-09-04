"""Stages 15-17 — owner-scoped private context retrieval.

This is the only sanctioned way for anything outside this package — UNDX above
all — to obtain private context. It exists because the alternative that keeps
suggesting itself is "fetch the member's graph and let the model sort it out",
and that alternative is wrong in four separate ways at once: it is unbounded,
it ignores sensitivity, it joins domains that must not be joined, and it hands
a language model far more than the question needed. Stage 15 states the rule
plainly — *do not dump whole graph*.

What a retrieval is
-------------------
A request names an **owner**, an **actor**, an **intent**, and a **purpose**.
The intent is the load-bearing part: it selects which relations may be
traversed and which domains may be read, so the bound on the walk comes from
what was asked rather than from a caller-supplied number that nobody checks.

Five gates are applied, in this order, before anything is returned:

1. **Owner** — every underlying query is owner-scoped by construction; see
   ``facts.list_facts`` and ``graph.neighbors``.
2. **Authorization** — the actor must be the owner. There is no delegation
   model in this codebase yet, so there is none here; inventing one would mean
   inventing the audit trail and revocation path that go with it.
3. **Sensitivity** — the effective ceiling is the lower of what the caller
   asked for and what the intent allows, so a caller cannot widen its own
   allowance by naming a permissive ceiling.
4. **Domain** — Stage 17. Property, insurance and loan context belong
   together; health does not belong with either. See ``DOMAIN_JOIN_POLICY``.
5. **Purpose** — recorded on the audit row, and narrowed to the closed
   vocabulary in ``audit.PURPOSES``.

Bounds and honesty about them
-----------------------------
Stage 16 sets ``max_depth = 3``, ``max_nodes = 100``, ``max_edges = 250``. Those
are ceilings, not targets, and a caller may ask for less but never more. When a
bound bites, the result says so in ``truncated`` rather than returning a
partial answer that looks complete — a traversal that silently stops at 100
nodes and reports nothing will eventually cause someone to say "you have no
other properties" when the truth is "I stopped looking".

Conflicts and staleness travel with the context for the same reason. Stage 21
requires that UNDX be unable to quietly pick a winner between two contradictory
facts; the way to make that impossible is to never hand it a fact without also
handing it the knowledge that the fact is disputed.

Not in scope here
-----------------
This module reads. It does not write facts, edges or nodes, does not rank for
relevance beyond the filters above, and does not summarise. Ranking and
summarising belong to the caller, which knows the question; retrieval only
knows what the member is allowed to be told.
"""

from __future__ import annotations

from collections import deque
from time import perf_counter as _perf_counter
from typing import Any, Sequence

from . import audit as _audit
from . import contradictions as _contradictions
from . import facts as _facts
from . import graph as _graph
from . import model as _model
from . import records as _records
from . import schema as _schema
from . import telemetry as _telemetry

# ---------------------------------------------------------------------------
# Bounds (Stage 16)
# ---------------------------------------------------------------------------
MAX_DEPTH = 3
MAX_NODES = 100
MAX_EDGES = 250

#: Facts are bounded separately from nodes. A hundred nodes each carrying a
#: dozen assertions is twelve hundred rows, which is not a "context" by any
#: reading of the word.
MAX_FACTS = 400

#: How facts point at graph nodes. Stage 11: the node is the thing, the facts
#: are what is asserted about it.
SUBJECT_TYPE_NODE = "NODE"


# ---------------------------------------------------------------------------
# Intents (Stages 16-17)
# ---------------------------------------------------------------------------
INTENT_PROPERTY_PORTFOLIO = "property_portfolio"
INTENT_INSURANCE_COVERAGE = "insurance_coverage"
INTENT_BUSINESS_STRUCTURE = "business_structure"
INTENT_LEGAL_DOCUMENTS = "legal_documents"
INTENT_HEALTH_CONTEXT = "health_context"
INTENT_IDENTITY_CONTEXT = "identity_context"
INTENT_GENERAL = "general"

#: Each intent declares what it may walk and what it may read. Both lists are
#: allowlists: a relation or domain that nobody thought about is excluded,
#: which is the failure direction that loses information rather than leaking
#: it. Adding an entry here is a deliberate act and should be reviewed as one.
INTENTS: dict[str, dict[str, Any]] = {
    INTENT_PROPERTY_PORTFOLIO: {
        "relations": (_model.RELATION_OWNS, _model.RELATION_COVERED_BY,
                      _model.RELATION_SECURED_BY),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_FINANCIAL,
                    _model.DOMAIN_LEGAL),
        "sensitivity_ceiling": _model.SENSITIVITY_CONFIDENTIAL,
    },
    INTENT_INSURANCE_COVERAGE: {
        "relations": (_model.RELATION_OWNS, _model.RELATION_COVERED_BY),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_FINANCIAL,
                    _model.DOMAIN_LEGAL),
        "sensitivity_ceiling": _model.SENSITIVITY_CONFIDENTIAL,
    },
    INTENT_BUSINESS_STRUCTURE: {
        "relations": (_model.RELATION_OWNS, _model.RELATION_ADVISED_BY,
                      _model.RELATION_GOVERNED_BY),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_FINANCIAL,
                    _model.DOMAIN_LEGAL),
        "sensitivity_ceiling": _model.SENSITIVITY_CONFIDENTIAL,
    },
    INTENT_LEGAL_DOCUMENTS: {
        "relations": (_model.RELATION_GOVERNED_BY, _model.RELATION_DESCRIBES),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_LEGAL),
        "sensitivity_ceiling": _model.SENSITIVITY_CONFIDENTIAL,
    },
    # Health context is walkable but narrow, and it cannot reach financial or
    # legal material. See DOMAIN_JOIN_POLICY for why that is a rule about the
    # combination rather than about either domain on its own.
    INTENT_HEALTH_CONTEXT: {
        "relations": (_model.RELATION_ADVISED_BY, _model.RELATION_DESCRIBES),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_HEALTH),
        "sensitivity_ceiling": _model.SENSITIVITY_HIGHLY_SENSITIVE,
    },
    INTENT_IDENTITY_CONTEXT: {
        "relations": (_model.RELATION_DESCRIBES,),
        "domains": (_model.DOMAIN_GENERAL, _model.DOMAIN_IDENTITY),
        "sensitivity_ceiling": _model.SENSITIVITY_HIGHLY_SENSITIVE,
    },
    # The catch-all is deliberately the most restricted intent, not the least.
    # A caller who has not said what they are asking about has not earned a
    # wider view than one who has.
    INTENT_GENERAL: {
        "relations": (_model.RELATION_OWNS,),
        "domains": (_model.DOMAIN_GENERAL,),
        "sensitivity_ceiling": _model.SENSITIVITY_INTERNAL,
    },
}

#: Domains that must not be joined to unrelated context (Stage 17). Each of
#: these is a domain where the *combination* is the disclosure: that a member
#: has a health record is one thing, that a health record sits beside their
#: net worth in the same prompt is another, and the second is what turns a
#: model's summary into a profile nobody consented to.
ISOLATED_DOMAINS = frozenset({
    _model.DOMAIN_HEALTH,
    _model.DOMAIN_IDENTITY,
    _model.DOMAIN_SECURITY,
})

#: Domains that may accompany an isolated one. GENERAL only — it is the domain
#: for material that carries no sensitivity of its own.
ISOLATION_COMPANIONS = frozenset({_model.DOMAIN_GENERAL})

DENIED_NOT_OWNER = "actor_is_not_owner"
DENIED_DOMAIN_JOIN = "domain_join_not_permitted"
DENIED_UNKNOWN_INTENT = "unknown_intent"
DENIED_NO_OWNER = "no_owner"


def domain_join_permitted(domains: Sequence[str]) -> tuple[bool, str]:
    """Stage 17. Whether this set of domains may appear in one context.

    Returns ``(permitted, reason)``. Two rules, both about combination:

    * At most one isolated domain per retrieval. Health and identity material
      may each be read; they may not be read *together*, because the join is a
      richer disclosure than either part.
    * An isolated domain may only be accompanied by GENERAL. Property plus
      insurance plus loan is the join Stage 17 explicitly allows; health plus
      any of them is the join it explicitly restricts.
    """
    resolved = {d for d in (_model.normalize_domain(x) for x in domains) if d}
    isolated = resolved & ISOLATED_DOMAINS
    if not isolated:
        return True, ""
    if len(isolated) > 1:
        return False, "multiple_isolated_domains"
    companions = resolved - isolated
    if companions - ISOLATION_COMPANIONS:
        return False, "isolated_domain_joined_to_unrelated_context"
    return True, ""


def _lower_ceiling(left: object, right: object) -> str | None:
    """The more restrictive of two sensitivity ceilings, or ``None``.

    ``None`` when either side is unrecognised. An unknown ceiling releases
    nothing here, matching ``model.sensitivity_within`` — the alternative is a
    typo in a caller silently granting the widest possible view.
    """
    a = _model.normalize_sensitivity(left)
    b = _model.normalize_sensitivity(right)
    if not a or not b:
        return None
    return a if _model.SENSITIVITY_RANK[a] <= _model.SENSITIVITY_RANK[b] else b


def _empty(owner: int, intent: str, *, denied: str, domains: Sequence[str] = ()) -> dict:
    return {
        "owner_user_id": owner,
        "intent": intent,
        "domains": list(domains),
        "sensitivity_ceiling": "",
        "relevant_facts": [],
        "relevant_nodes": [],
        "relevant_edges": [],
        "provenance": [],
        "conflicts": [],
        "stale_flags": [],
        "bounds": {"max_depth": 0, "max_nodes": 0, "max_edges": 0},
        "truncated": {"nodes": False, "edges": False, "facts": False,
                      "depth_reached": 0},
        "denied": denied,
    }


def _refuse(
    cur,
    *,
    owner: int,
    actor: int,
    intent: str,
    reason: str,
    purpose: str,
    domains: Sequence[str] = (),
    audit_it: bool = True,
) -> dict:
    """Record a refusal — audit row, Stage 38 metric — and return the empty result.

    Every ``denied`` exit in :func:`retrieve` goes through here rather than
    calling ``_empty`` directly. Five refusal points that each had to remember
    to audit *and* emit is five chances to add a sixth that does neither, and
    the refusal rate is the one number on this surface that should be flat at
    zero: ``cross_account`` going positive is a member's store being read by
    somebody else, and it has to be visible without waiting for anyone to open
    the audit table.

    ``audit_it`` is false only for the no-owner case, where there is no owner to
    file the row against.
    """
    if audit_it:
        _audit.record_denied(cur, actor_user_id=actor, owner_user_id=owner,
                             object_type="CONTEXT", purpose=purpose)
    _telemetry.emit(
        _telemetry.EVENT_CONTEXT_DENIED,
        intent=intent, reason=reason, cross_account=(actor != owner))
    return _empty(owner, intent, denied=reason, domains=domains)


def _seed_nodes(
    cur,
    *,
    owner: int,
    seed_node_ids: Sequence[object] | None,
    seed_node_types: Sequence[str] | None,
    domains: Sequence[str],
    ceiling: str,
    limit: int,
) -> list[dict]:
    """Where the walk starts.

    Explicit ids win. They are resolved one at a time through
    ``graph.get_node``, which returns ``None`` for absent, foreign and
    malformed alike — so a caller passing another member's node id gets the
    same empty seed as one passing a number that was never issued, and cannot
    learn which it was.
    """
    if seed_node_ids:
        found = []
        for node_id in list(seed_node_ids)[:limit]:
            node = _graph.get_node(cur, owner_user_id=owner, node_id=node_id)
            if node is not None:
                found.append(node)
        return found
    return _graph.list_nodes(
        cur, owner_user_id=owner, node_types=seed_node_types or None,
        domains=domains, sensitivity_ceiling=ceiling, limit=limit)


def retrieve(
    cur,
    *,
    owner_user_id: int,
    actor_user_id: int | None = None,
    intent: str = INTENT_GENERAL,
    purpose: str = "undx_context",
    seed_node_ids: Sequence[object] | None = None,
    seed_node_types: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    sensitivity_ceiling: object = None,
    fact_types: Sequence[str] | None = None,
    max_depth: int = MAX_DEPTH,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    include_conflicts: bool = True,
) -> dict:
    """Assemble owner-scoped private context. See the module docstring.

    Returns the Stage 15 contract — ``relevant_facts``, ``relevant_nodes``,
    ``relevant_edges``, ``provenance``, ``conflicts``, ``stale_flags`` — plus
    the bounds that were applied, what was truncated, and ``denied``.

    ``denied`` is a string rather than an exception because a refusal is a
    normal outcome that the caller has to be able to describe to the member.
    It is never a reason the *member* did not ask for: a denial says which
    policy stopped the read, and the empty result that comes with it is
    indistinguishable from the result for data that does not exist.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id if actor_user_id is not None else owner)
    wanted_intent = str(intent or "").strip().lower()

    if owner <= 0:
        return _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                       reason=DENIED_NO_OWNER, purpose=purpose, audit_it=False)

    if wanted_intent not in INTENTS:
        # Not defaulted to `general`. A caller naming an intent this module has
        # never heard of has a bug or is probing, and quietly substituting a
        # different policy for the one they named would hide both.
        return _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                       reason=DENIED_UNKNOWN_INTENT, purpose=purpose)

    policy = INTENTS[wanted_intent]

    # Gate 2 — authorization. Recorded as a denial before returning, because
    # an actor reading another member's store is the single most important
    # event this table can hold.
    if actor != owner:
        return _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                       reason=DENIED_NOT_OWNER, purpose=purpose)

    # Gate 4 — domain. The caller may narrow the intent's domains, never widen
    # them: the intersection is taken, and an empty intersection is a request
    # for material this intent does not cover.
    allowed_domains = tuple(policy["domains"])
    if domains:
        asked = [d for d in (_model.normalize_domain(x) for x in domains) if d]
        resolved_domains = tuple(d for d in asked if d in allowed_domains)
    else:
        resolved_domains = allowed_domains

    permitted, reason = domain_join_permitted(resolved_domains)
    if not permitted or not resolved_domains:
        return _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                       reason=reason or DENIED_DOMAIN_JOIN, purpose=purpose,
                       domains=resolved_domains)

    # Gate 3 — sensitivity. The caller's ceiling and the intent's ceiling are
    # both applied; whichever is lower wins.
    ceiling = _lower_ceiling(
        sensitivity_ceiling if sensitivity_ceiling is not None
        else policy["sensitivity_ceiling"],
        policy["sensitivity_ceiling"])
    if not ceiling:
        return _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                       reason="unknown_sensitivity_ceiling", purpose=purpose,
                       domains=resolved_domains)

    _schema.require_private_schema(cur)

    depth_cap = max(0, min(int(max_depth), MAX_DEPTH))
    node_cap = max(1, min(int(max_nodes), MAX_NODES))
    edge_cap = max(0, min(int(max_edges), MAX_EDGES))
    relations = tuple(policy["relations"])

    seeds = _seed_nodes(
        cur, owner=owner, seed_node_ids=seed_node_ids,
        seed_node_types=seed_node_types, domains=resolved_domains,
        ceiling=ceiling, limit=node_cap)

    # Breadth-first, so the bound cuts the *far* material rather than an
    # arbitrary branch. A depth-first walk that hits 100 nodes has explored one
    # corner exhaustively and the rest not at all, which is a worse answer to
    # the same question at the same cost.
    nodes: dict[int, dict] = {}
    edges: dict[int, dict] = {}
    queue: deque[tuple[int, int]] = deque()
    truncated_nodes = False
    truncated_edges = False
    depth_reached = 0

    for node in seeds:
        node_id = int(node["id"])
        if node_id in nodes:
            continue
        if len(nodes) >= node_cap:
            truncated_nodes = True
            break
        nodes[node_id] = node
        queue.append((node_id, 0))

    while queue:
        current_id, depth = queue.popleft()
        if depth >= depth_cap:
            continue
        if len(edges) >= edge_cap:
            truncated_edges = True
            break

        step = _graph.neighbors(
            cur, owner_user_id=owner, node_id=current_id,
            relations=relations, direction=_graph.DIRECTION_BOTH,
            limit=min(_graph.MAX_NEIGHBOURS, edge_cap - len(edges) + 1))

        for edge in step:
            edge_id = int(edge["id"])
            if edge_id not in edges:
                if len(edges) >= edge_cap:
                    truncated_edges = True
                    break
                edges[edge_id] = edge

            other_id = edge.get("other_node_id")
            if other_id is None:
                # `graph.neighbors` already logged the dangling edge. Keeping
                # the edge and skipping the node is right: the relationship is
                # real, the far end is not visible, and pretending the edge
                # does not exist would hide a data defect.
                continue
            other_id = int(other_id)
            if other_id in nodes:
                continue
            if len(nodes) >= node_cap:
                truncated_nodes = True
                continue

            node = _graph.get_node(cur, owner_user_id=owner, node_id=other_id)
            if node is None:
                continue
            # Domain and sensitivity are enforced on traversal too, not only on
            # the seed query. Otherwise a permitted starting point would be a
            # doorway into material the intent excludes — reachability is not
            # authorization.
            if node.get("domain") not in resolved_domains:
                continue
            if not _model.sensitivity_within(node.get("sensitivity"), ceiling):
                continue
            nodes[other_id] = node
            depth_reached = max(depth_reached, depth + 1)
            queue.append((other_id, depth + 1))

    subject_ids = [str(node_id) for node_id in nodes]
    found_facts = _facts.list_facts_for_subjects(
        cur, owner_user_id=owner, subject_type=SUBJECT_TYPE_NODE,
        subject_ids=subject_ids, fact_types=fact_types,
        domains=resolved_domains, sensitivity_ceiling=ceiling,
        limit=MAX_FACTS + 1) if subject_ids else []

    truncated_facts = len(found_facts) > MAX_FACTS
    found_facts = found_facts[:MAX_FACTS]

    provenance = [
        {
            "fact_id": fact.get("id"),
            "subject_id": fact.get("subject_id"),
            "fact_type": fact.get("fact_type"),
            "provenance_type": fact.get("provenance_type"),
            "confidence": fact.get("confidence"),
            "observed_at": fact.get("observed_at"),
            "ref": fact.get("provenance"),
        }
        for fact in found_facts
    ]

    stale_flags = [
        {
            "fact_id": fact.get("id"),
            "subject_id": fact.get("subject_id"),
            "fact_type": fact.get("fact_type"),
            **fact.get("freshness", {}),
        }
        for fact in found_facts
        if fact.get("freshness", {}).get("stale")
    ]

    # Stage 37. One read covering every traversed subject, not one per subject.
    # `detect_conflicts` groups by subject internally, so the loop this
    # replaced was issuing up to `MAX_NODES` queries to compute what a single
    # batch already answers — the N+1 that stage exists to forbid. Conflicts
    # come back keyed by (subject, fact_type) and so are already distinct; the
    # deduplication the loop needed goes with it.
    conflicts: list[dict] = []
    if include_conflicts and subject_ids:
        conflicts = _contradictions.detect_conflicts(
            cur, owner_user_id=owner, subject_type=SUBJECT_TYPE_NODE,
            subject_ids=subject_ids)

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_CONTEXT_RETRIEVED, object_type="CONTEXT",
        object_id=wanted_intent, purpose=purpose,
        outcome=_audit.OUTCOME_OK,
        result_count=len(found_facts) + len(nodes) + len(edges))

    # Stage 38. Sizes and policy names only — no subject, no owner, no value.
    # `truncated` is collapsed to one flag because the useful question is "did
    # a bound bite", and which of the three bit is a debugging detail that the
    # returned contract already carries to the caller.
    _telemetry.emit(
        _telemetry.EVENT_CONTEXT_RETRIEVED,
        intent=wanted_intent,
        sensitivity_ceiling=ceiling,
        domain_count=len(resolved_domains),
        node_count=len(nodes),
        edge_count=len(edges),
        fact_count=len(found_facts),
        conflict_count=len(conflicts),
        stale_count=len(stale_flags),
        depth_reached=depth_reached,
        truncated=(truncated_nodes or truncated_edges or truncated_facts))

    return {
        "owner_user_id": owner,
        "intent": wanted_intent,
        "domains": list(resolved_domains),
        "sensitivity_ceiling": ceiling,
        "relevant_facts": found_facts,
        "relevant_nodes": list(nodes.values()),
        "relevant_edges": list(edges.values()),
        "provenance": provenance,
        "conflicts": conflicts,
        "stale_flags": stale_flags,
        "bounds": {"max_depth": depth_cap, "max_nodes": node_cap,
                   "max_edges": edge_cap},
        "truncated": {"nodes": truncated_nodes, "edges": truncated_edges,
                      "facts": truncated_facts, "depth_reached": depth_reached},
        "denied": "",
    }


# ---------------------------------------------------------------------------
# Batch C — typed record views
# ---------------------------------------------------------------------------
# The six record primitives are read through this module for the same reason
# facts and the graph are: this is the sanctioned door, and a caller that can
# reach `records.list_records` directly can reach it without an intent, without
# a sensitivity ceiling, and without leaving an audit row.
#
# `retrieve` itself is deliberately unchanged. Records are not graph material —
# they do not participate in the walk, they have no edges, and folding them into
# the same result would mean every existing caller's payload silently grew. A
# second entry point that runs the *same five gates* is the honest shape: the
# gates are the contract, not the function name.
VIEW_OBLIGATIONS = "obligations"
VIEW_EVENTS = "events"
VIEW_DECISIONS = "decisions"
VIEW_REQUESTS = "requests"
VIEW_RISKS = "risks"
VIEW_OPPORTUNITIES = "opportunities"

#: View name -> record type. The view names are the public vocabulary; the
#: table names appear nowhere in this mapping and nowhere in the result, which
#: is what "do not expose raw tables directly" means in practice.
RECORD_VIEWS: dict[str, str] = {
    VIEW_OBLIGATIONS: _records.TYPE_OBLIGATION,
    VIEW_EVENTS: _records.TYPE_EVENT,
    VIEW_DECISIONS: _records.TYPE_DECISION,
    VIEW_REQUESTS: _records.TYPE_REQUEST,
    VIEW_RISKS: _records.TYPE_RISK,
    VIEW_OPPORTUNITIES: _records.TYPE_OPPORTUNITY,
}

DENIED_UNKNOWN_VIEW = "unknown_view"

MAX_RECORDS = 200


def _empty_records(owner: int, intent: str, view: str, *, denied: str,
                   domains: Sequence[str] = ()) -> dict:
    return {
        "owner_user_id": owner,
        "intent": intent,
        "view": view,
        "domains": list(domains),
        "sensitivity_ceiling": "",
        "records": [],
        "counts": {"returned": 0},
        "truncated": False,
        "denied": denied,
    }


def retrieve_records(
    cur,
    *,
    owner_user_id: int,
    view: str,
    actor_user_id: int | None = None,
    intent: str = INTENT_GENERAL,
    purpose: str = "undx_context",
    statuses: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    sensitivity_ceiling: object = None,
    due_before: object = None,
    include_superseded: bool = False,
    limit: int = 50,
    before_id: int = 0,
) -> dict:
    """One typed view over the record primitives, owner-scoped and gated.

    Same five gates as :func:`retrieve`, in the same order — owner,
    authorization, sensitivity, domain, purpose — and the same contract for
    ``denied``: a string, not an exception, and the empty result that comes with
    it is indistinguishable from the result for records that do not exist.

    The extra gate is the view name. An unknown view is refused rather than
    defaulted, for the same reason an unknown intent is: substituting a
    different collection for the one the caller named hides both the bug and the
    probe.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id if actor_user_id is not None else owner)
    wanted_intent = str(intent or "").strip().lower()
    wanted_view = str(view or "").strip().lower()

    if owner <= 0:
        _telemetry.emit(_telemetry.EVENT_CONTEXT_DENIED, intent=wanted_intent,
                        reason=DENIED_NO_OWNER, cross_account=False)
        return _empty_records(owner, wanted_intent, wanted_view, denied=DENIED_NO_OWNER)

    if wanted_intent not in INTENTS:
        _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                reason=DENIED_UNKNOWN_INTENT, purpose=purpose)
        return _empty_records(owner, wanted_intent, wanted_view,
                              denied=DENIED_UNKNOWN_INTENT)

    if actor != owner:
        # Recorded before returning. An actor reading another member's records
        # is the single most important row this audit table can hold, and a
        # concierge queue is exactly the surface where a support tool would
        # plausibly try.
        _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                reason=DENIED_NOT_OWNER, purpose=purpose)
        return _empty_records(owner, wanted_intent, wanted_view,
                              denied=DENIED_NOT_OWNER)

    if wanted_view not in RECORD_VIEWS:
        _audit.record_denied(cur, actor_user_id=actor, owner_user_id=owner,
                             object_type="RECORD_VIEW", purpose=purpose)
        _telemetry.emit(_telemetry.EVENT_CONTEXT_DENIED, intent=wanted_intent,
                        reason=DENIED_UNKNOWN_VIEW, cross_account=False)
        return _empty_records(owner, wanted_intent, wanted_view,
                              denied=DENIED_UNKNOWN_VIEW)

    policy = INTENTS[wanted_intent]

    allowed_domains = tuple(policy["domains"])
    if domains:
        asked = [d for d in (_model.normalize_domain(x) for x in domains) if d]
        resolved_domains = tuple(d for d in asked if d in allowed_domains)
    else:
        resolved_domains = allowed_domains

    permitted, reason = domain_join_permitted(resolved_domains)
    if not permitted or not resolved_domains:
        _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                reason=reason or DENIED_DOMAIN_JOIN, purpose=purpose,
                domains=resolved_domains)
        return _empty_records(owner, wanted_intent, wanted_view,
                              denied=reason or DENIED_DOMAIN_JOIN,
                              domains=resolved_domains)

    ceiling = _lower_ceiling(
        sensitivity_ceiling if sensitivity_ceiling is not None
        else policy["sensitivity_ceiling"],
        policy["sensitivity_ceiling"])
    if not ceiling:
        _refuse(cur, owner=owner, actor=actor, intent=wanted_intent,
                reason="unknown_sensitivity_ceiling", purpose=purpose,
                domains=resolved_domains)
        return _empty_records(owner, wanted_intent, wanted_view,
                              denied="unknown_sensitivity_ceiling",
                              domains=resolved_domains)

    record_type = RECORD_VIEWS[wanted_view]
    bounded = max(1, min(int(limit or 50), MAX_RECORDS))

    started = _perf_counter()
    rows = _records.list_records(
        cur, record_type=record_type, owner_user_id=owner,
        statuses=statuses, domains=resolved_domains,
        sensitivity_ceiling=ceiling, due_before=due_before,
        include_superseded=include_superseded,
        limit=bounded, before_id=before_id)
    elapsed_ms = int((_perf_counter() - started) * 1000)

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_CONTEXT_RETRIEVED, object_type="RECORD_VIEW",
        object_id=wanted_view, purpose=purpose, outcome=_audit.OUTCOME_OK,
        result_count=len(rows))

    # Counts and timings only. Not a title, not a question, not a description —
    # see the note on these three events in `telemetry`.
    _telemetry.emit(
        _telemetry.EVENT_RECORDS_RETRIEVED,
        record_type=record_type, intent=wanted_intent,
        sensitivity_ceiling=ceiling, record_count=len(rows),
        latency_ms=elapsed_ms, truncated=(len(rows) >= bounded))

    return {
        "owner_user_id": owner,
        "intent": wanted_intent,
        "view": wanted_view,
        "domains": list(resolved_domains),
        "sensitivity_ceiling": ceiling,
        "records": rows,
        "counts": {"returned": len(rows)},
        "truncated": len(rows) >= bounded,
        "denied": "",
    }
