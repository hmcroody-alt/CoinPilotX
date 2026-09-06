"""Structural diagnostics over one member's capital graph. It finds; it never fixes.

What this module is
-------------------
Every other read surface in this package assumes the store is well-formed: that
an edge's endpoints belong to the owner it names, that a node key is unique,
that a projection matches the ledger it was projected from. Those assumptions
are enforced by the writers and by the table constraints, and they are almost
always true. This module is what asks whether they are true *today*, for this
member, and says so in a shape a screen and a test can both read.

Why it is not part of the views
-------------------------------
A view that quietly repaired what it found would make the damage invisible: the
member would see a correct screen, the cause would never be investigated, and
the next read would repair it again. Worse, a repair inside a read path runs
without anyone having decided to run it. So :func:`diagnose` opens no writer,
imports none, and :data:`NO_REPAIR` states that as a decision rather than an
omission. ``portfolio_projection.reconcile`` already knows how to repair drift
and is called here with ``repair=False`` — the repair path exists, it is simply
not this one.

Why it reads the tables directly
--------------------------------
This is the one module in the capital family that does not read exclusively
through the owner-scoped accessors, and the reason is the first check itself.
``graph.list_nodes`` and ``graph.neighbors`` apply ``owner_user_id = ?`` to
every query, which is precisely what makes them safe — and precisely what makes
them blind to an edge whose endpoint has escaped the owner. A diagnostic built
on them could never report a cross-owner leak, because the leak is filtered out
before it is counted. So the entry is still owner-scoped — every statement here
begins from ``owner_user_id = ?`` on the member's own rows — and the check is
whether anything reachable from those rows *fails* to be theirs.

What a finding may say
----------------------
A finding names the check, the row identity, and nothing else. It never carries
the other party's user id, the node's external reference, a fact value, or a
title. A diagnostic that quoted the foreign row in order to prove the leak
would be a second leak, in the one payload most likely to be pasted into a
support ticket.

Honesty
-------
``healthy`` is True only when every check ran and every check was clean. A check
that could not run — because the schema predates it, or because the underlying
reconcile refused — is reported as ``inconclusive``, never as clean. The
findings list is bounded at :data:`MAX_FINDINGS` and reports its own truncation,
because a store that is broken in ten thousand places must not produce a payload
that cannot be rendered.
"""

from __future__ import annotations

import logging

from services.private_office import audit as _audit
from services.private_office import capital_graph as _capital
from services.private_office import model as _model
from services.private_office import portfolio_projection as _portfolio
from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.integrity")

#: Same matrix row as every other capital read, named from ``capital_graph`` so
#: a rename cannot strand this surface on a feature id nothing else gates on.
FEATURE_ID = _capital.FEATURE_ID

#: Spelled the way every other capital surface spells it. A second spelling
#: would let a client special-case one refusal and miss the other.
DENIED_NOT_OWNER = "actor_is_not_owner"

#: Stated in the payload. A diagnostics screen that does not say this invites
#: the reading that the problems it lists have been dealt with.
NO_REPAIR = (
    "Diagnostic only. Nothing here has been altered, corrected or re-projected. "
    "A finding is a description of the store as it stands, not a record of "
    "something that was fixed."
)

#: Stated in the payload for the same reason: a clean report over a member with
#: no capital records is not evidence of a healthy graph, and the count of rows
#: actually examined is what tells the two apart.
SCOPE_BASIS = (
    "Checks run over this member's own rows only. A clean result means nothing "
    "was found in what was examined; `examined` reports how much that was."
)

#: Findings returned. Beyond this the payload reports ``truncated`` rather than
#: growing without bound — the first hundred structural faults are enough to
#: act on, and the count is preserved even when the rows are not.
MAX_FINDINGS = 100

#: Rows read per structural check. Independent of the finding cap: a store may
#: be scanned widely and report few faults, and the two numbers must not be
#: confused for one another in the payload.
MAX_SCAN_ROWS = 5000

#: Every check this module knows how to run. Published in the payload so a
#: client can tell "clean" from "this build did not look".
#:
#: The names are product vocabulary, not column names. ``duplicate_node_identity``
#: rather than ``duplicate_node_keys`` because the payload leak guard scans for
#: private column names and is right to: a response that echoes the schema's
#: field names teaches a reader what to ask for next, and the check is about
#: identity being unique, which is the thing that matters rather than the column
#: that happens to store it.
CHECKS: tuple[str, ...] = (
    "cross_owner_edges",
    "orphan_edges",
    "duplicate_node_identity",
    "unknown_vocabulary",
    "edges_into_retired_nodes",
    "portfolio_projection_drift",
)

#: Findings a member's own data can legitimately produce (a projection can drift
#: because a write failed) versus findings that mean an invariant the database
#: is supposed to guarantee has been violated. They are separated because the
#: response to them differs: one is re-projected, the other is escalated.
INVARIANT_CHECKS = frozenset({
    "cross_owner_edges", "orphan_edges", "duplicate_node_identity",
})


def _denied(reason: str) -> dict:
    """The refusal shape — never a clean report.

    A refused diagnostic and a healthy one must not look alike: the whole
    purpose of this surface is to be believed when it says nothing is wrong.
    """
    return {
        "ok": False,
        "denied": {"reason": reason},
        "healthy": None,
        "findings": [],
        "checks": {},
        "examined": {},
        "totals": {},
    }


def _finding(check: str, subject: str, subject_id: object,
             detail: str) -> dict:
    """One fault, named by row identity and nothing else.

    ``detail`` is a fixed phrase chosen from this module, never a value read out
    of the store. That is what keeps a diagnostics payload safe to hand to
    someone who is not the member.
    """
    return {
        "check": check,
        "subject": subject,
        "subject_id": subject_id,
        "detail": detail,
        "severity": "invariant" if check in INVARIANT_CHECKS else "drift",
    }


def _edge_checks(cur, *, owner: int) -> tuple[list[dict], int]:
    """Cross-owner endpoints, orphan endpoints, and edges into retired nodes.

    One pass, three checks, because they all need the same join and running it
    three times would let the three disagree about which edges exist.
    """
    cur.execute(
        f"""
        SELECT e.id, e.source_node_id, e.target_node_id, e.lifecycle_state,
               s.owner_user_id AS source_owner, t.owner_user_id AS target_owner,
               s.lifecycle_state AS source_state, t.lifecycle_state AS target_state
          FROM {_schema.EDGES_TABLE} e
          LEFT JOIN {_schema.NODES_TABLE} s ON s.id = e.source_node_id
          LEFT JOIN {_schema.NODES_TABLE} t ON t.id = e.target_node_id
         WHERE e.owner_user_id = ?
         ORDER BY e.id ASC
         LIMIT ?
        """,
        [owner, MAX_SCAN_ROWS],
    )
    rows = cur.fetchall()
    findings: list[dict] = []

    for row in rows:
        row = dict(row)
        edge_id = row.get("id")
        for side in ("source", "target"):
            node_id = row.get(f"{side}_node_id")
            node_owner = row.get(f"{side}_owner")
            if node_owner is None:
                # The endpoint does not exist. Reported as an orphan rather than
                # as a cross-owner fault: "points at nothing" and "points at
                # somebody else" call for different responses.
                findings.append(_finding(
                    "orphan_edges", "edge", edge_id,
                    f"{side} endpoint {node_id} has no node row"))
                continue
            if int(node_owner) != owner:
                # Deliberately does not name the other owner. Proving the leak
                # by quoting the leaked identifier would be a second leak.
                findings.append(_finding(
                    "cross_owner_edges", "edge", edge_id,
                    f"{side} endpoint belongs to a different owner"))
                continue
            if (str(row.get("lifecycle_state") or "") == _model.LIFECYCLE_ACTIVE
                    and str(row.get(f"{side}_state") or "")
                    != _model.LIFECYCLE_ACTIVE):
                findings.append(_finding(
                    "edges_into_retired_nodes", "edge", edge_id,
                    f"active edge into a non-active {side} node"))

    return findings, len(rows)


def _node_checks(cur, *, owner: int) -> tuple[list[dict], int]:
    """Duplicate keys and vocabulary the model does not recognise.

    The duplicate check exists even though ``UNIQUE(owner_user_id, node_key)``
    is supposed to make it impossible. That is the point: an invariant the
    database guarantees is exactly the one nobody notices has stopped holding,
    because every reader assumes it. Asserting it here costs one scan and turns
    a silent corruption into a named finding.
    """
    cur.execute(
        f"""
        SELECT id, node_key, node_type, domain, sensitivity, lifecycle_state
          FROM {_schema.NODES_TABLE}
         WHERE owner_user_id = ?
         ORDER BY id ASC
         LIMIT ?
        """,
        [owner, MAX_SCAN_ROWS],
    )
    rows = [dict(row) for row in cur.fetchall()]
    findings: list[dict] = []

    seen: dict[str, object] = {}
    for row in rows:
        key = str(row.get("node_key") or "")
        if key in seen:
            findings.append(_finding(
                "duplicate_node_identity", "node", row.get("id"),
                "a second node carries a key that must be unique"))
        else:
            seen[key] = row.get("id")

        # A value the model cannot normalise is not a new feature that has not
        # been taught to the reader yet; it is a row no gate can classify. An
        # unrecognised sensitivity in particular means the ceiling comparison in
        # every read has no defined answer for this row.
        for field, normalise in (
            ("node_type", _model.normalize_node_type),
            ("domain", _model.normalize_domain),
            ("sensitivity", _model.normalize_sensitivity),
        ):
            raw = row.get(field)
            if not normalise(raw):
                findings.append(_finding(
                    "unknown_vocabulary", "node", row.get("id"),
                    f"{field} is not a value this package recognises"))

        if str(row.get("lifecycle_state") or "") not in _model.LIFECYCLE_STATES:
            findings.append(_finding(
                "unknown_vocabulary", "node", row.get("id"),
                "lifecycle_state is not a value this package recognises"))

    return findings, len(rows)


def _projection_check(cur, *, owner: int) -> tuple[list[dict], bool]:
    """Ledger versus projection, via the existing reconcile, with no repair.

    Returns ``(findings, ran)``. ``ran`` is False when reconcile refused, and a
    refusal is reported as inconclusive rather than folded into a clean result —
    "we could not check" and "we checked and it was fine" are different
    sentences and only one of them is reassuring.
    """
    result = _portfolio.reconcile(cur, user_id=owner, repair=False)
    if not result.get("ok"):
        return [], False
    findings = [
        _finding("portfolio_projection_drift", "symbol",
                 str(item.get("symbol") or ""),
                 f"{item.get('field')} disagrees between ledger and projection")
        for item in result.get("drift") or []
    ]
    return findings, True


def diagnose(cur, *, owner_user_id: int, actor_user_id: int) -> dict:
    """Run every structural check over the member's own capital rows.

    Owner-only, and the gate is here rather than borrowed, because this module
    is the one that reads the tables directly. A denial writes the same audit
    row every other refused private read writes.

    Never repairs. Never raises for a check it cannot run: an individual check
    that fails is recorded as ``inconclusive`` and drops ``healthy`` to False,
    because a diagnostic that reports success when part of it did not execute is
    worse than one that does not run at all.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id or 0)
    if owner <= 0 or actor != owner:
        _audit.record_denied(
            cur, actor_user_id=actor, owner_user_id=owner,
            object_type="CAPITAL_INTEGRITY", purpose="user_request",
        )
        return _denied(DENIED_NOT_OWNER)

    _schema.require_private_schema(cur)

    findings: list[dict] = []
    checks: dict[str, str] = {name: "clean" for name in CHECKS}
    examined = {"edges": 0, "nodes": 0}

    def _mark(names: tuple[str, ...], state: str) -> None:
        for name in names:
            checks[name] = state

    edge_checks = ("cross_owner_edges", "orphan_edges",
                   "edges_into_retired_nodes")
    try:
        edge_findings, examined["edges"] = _edge_checks(cur, owner=owner)
        findings.extend(edge_findings)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_INTEGRITY_EDGE_CHECK_FAILED")
        _mark(edge_checks, "inconclusive")
        edge_findings = []

    node_checks = ("duplicate_node_identity", "unknown_vocabulary")
    try:
        node_findings, examined["nodes"] = _node_checks(cur, owner=owner)
        findings.extend(node_findings)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_INTEGRITY_NODE_CHECK_FAILED")
        _mark(node_checks, "inconclusive")

    try:
        drift_findings, ran = _projection_check(cur, owner=owner)
        findings.extend(drift_findings)
        if not ran:
            checks["portfolio_projection_drift"] = "inconclusive"
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_OFFICE_INTEGRITY_PROJECTION_CHECK_FAILED")
        checks["portfolio_projection_drift"] = "inconclusive"

    for item in findings:
        name = str(item.get("check"))
        if checks.get(name) == "clean":
            checks[name] = "findings"

    findings.sort(key=lambda item: (item["check"], str(item["subject_id"])))
    total_findings = len(findings)
    truncated = total_findings > MAX_FINDINGS
    findings = findings[:MAX_FINDINGS]

    inconclusive = sorted(
        name for name, state in checks.items() if state == "inconclusive")
    # `healthy` requires both halves: nothing found, and nothing skipped. A
    # store with zero findings and one check that could not run is not healthy,
    # it is unexamined, and the two must not render the same.
    healthy = total_findings == 0 and not inconclusive

    return {
        "ok": True,
        "denied": {},
        "healthy": healthy,
        "findings": findings,
        "checks": checks,
        "examined": examined,
        "totals": {
            "findings": total_findings,
            "invariant_violations": sum(
                1 for item in findings
                if item.get("severity") == "invariant"),
            "checks_run": len(CHECKS) - len(inconclusive),
            "checks_total": len(CHECKS),
            "inconclusive": inconclusive,
            "truncated": truncated,
            # False whenever the scan hit its row cap: past that point the
            # absence of a finding says nothing about the rows never read.
            "complete": (not truncated
                         and not inconclusive
                         and examined["edges"] < MAX_SCAN_ROWS
                         and examined["nodes"] < MAX_SCAN_ROWS),
        },
        "basis": {
            "repair": NO_REPAIR,
            "scope": SCOPE_BASIS,
            "checks": list(CHECKS),
        },
    }


__all__ = [
    "CHECKS", "DENIED_NOT_OWNER", "FEATURE_ID", "INVARIANT_CHECKS",
    "MAX_FINDINGS", "MAX_SCAN_ROWS", "NO_REPAIR", "SCOPE_BASIS", "diagnose",
]
