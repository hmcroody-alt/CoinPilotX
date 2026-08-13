"""Sentinel runbook registry (Stage 14) + the few safe V1 runbooks (Stage 15).

Shape mirrors the UNDX CapabilitySpec (risk, authority, executor, verifier,
idempotency) without re-registering any UNDX capability. Hard rules:

- ``arbitrary_shell`` / ``arbitrary_sql`` style runbooks can NEVER be
  registered — rejected by name and by description scan (SC1/SC3).
- Every runbook declares a bounded RiskBudget (SC14).
- Execution requires the full kill-switch chain to be on (default OFF).
- The executor cannot declare its own success: status is COMPLETED only
  after independent verification (Stage 16, SC4).
- V1 ships observation-grade runbooks only: no refunds, no payouts,
  no rollbacks, no account enforcement (SC6/SC7).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable

from services.sentinel import evidence, killswitches, store
from services.sentinel.authority import AuthorityLevel
from services.sentinel.risk import BudgetTracker, RiskBudget

FORBIDDEN_NAME_PATTERNS = (
    "arbitrary_shell", "arbitrary_sql", "raw_shell", "raw_sql", "exec_shell",
    "shell_exec", "run_sql", "eval", "sudo",
)
_FORBIDDEN_DESC_RE = re.compile(r"\b(arbitrary|unrestricted|any)\s+(shell|sql|command)\b", re.I)


class ForbiddenRunbookError(ValueError):
    pass


@dataclass(frozen=True)
class RunbookSpec:
    name: str
    description: str
    domain: str                     # authority dimension gating this runbook
    required_level: AuthorityLevel
    budget: RiskBudget
    executor: Callable              # (params: dict) -> dict result
    verifier: Callable              # (params: dict, result: dict) -> bool
    reversible: bool = True

    def __post_init__(self):
        lowered = self.name.lower()
        if any(p in lowered for p in FORBIDDEN_NAME_PATTERNS):
            raise ForbiddenRunbookError(f"runbook name {self.name!r} is forbidden (SC1/SC3)")
        if _FORBIDDEN_DESC_RE.search(self.description or ""):
            raise ForbiddenRunbookError(f"runbook {self.name!r} description implies unbounded execution")
        if self.required_level >= AuthorityLevel.OWNER_ONLY:
            raise ForbiddenRunbookError("owner-only actions cannot be runbooks (SC10)")
        if not self.reversible and self.required_level < AuthorityLevel.ACT_SENSITIVE:
            raise ForbiddenRunbookError("irreversible runbooks require ACT_SENSITIVE at minimum")


_REGISTRY: dict[str, RunbookSpec] = {}
_TRACKERS: dict[str, BudgetTracker] = {}


def register(spec: RunbookSpec) -> RunbookSpec:
    if spec.name in _REGISTRY and _REGISTRY[spec.name] is not spec:
        raise ValueError(f"runbook {spec.name!r} already registered")
    _REGISTRY[spec.name] = spec
    _TRACKERS[spec.name] = BudgetTracker(spec.budget)
    return spec


def get(name: str) -> RunbookSpec | None:
    return _REGISTRY.get(name)


def all_runbooks() -> tuple[RunbookSpec, ...]:
    return tuple(_REGISTRY.values())


def execute(name: str, executor_id: str, params: dict | None = None,
            entity_ids: tuple[str, ...] = (), conn=None) -> dict:
    """Run a registered runbook through the full governance chain.

    Denials return {'status': 'DENIED', 'reason': ...} — they never raise,
    so callers can't accidentally bypass by swallowing exceptions. Success
    returns status EXECUTED_UNVERIFIED; only verification.verify_execution
    can advance it to COMPLETED (SC4).
    """
    params = dict(params or {})
    spec = _REGISTRY.get(name)
    if spec is None:
        return {"status": "DENIED", "reason": f"unknown runbook {name!r} (SC15)"}
    if not killswitches.runbook_enabled(spec.name, spec.domain):
        return {"status": "DENIED",
                "reason": "kill-switch chain not fully enabled (automation defaults OFF)"}
    tracker = _TRACKERS[spec.name]
    if not tracker.try_spend(entity_ids):
        return {"status": "DENIED", "reason": "risk budget exhausted (SC14)"}

    execution_id = str(uuid.uuid4())
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_runbook_executions
               (execution_id, runbook, executor_id, status, result_json)
               VALUES (?, ?, ?, 'RUNNING', '{}')""",
            (execution_id, spec.name, executor_id))
        evidence.append("runbook_started", executor_id,
                        {"execution_id": execution_id, "runbook": spec.name,
                         "params": params}, conn=c)
        try:
            result = spec.executor(params)
            status = "EXECUTED_UNVERIFIED"
        except Exception as exc:
            result = {"error": str(exc)[:500]}
            status = "FAILED"
        cur.execute(
            "UPDATE sentinel_runbook_executions SET status=?, finished_at=datetime('now'), "
            "result_json=? WHERE execution_id=?",
            (status, json.dumps(result, default=str), execution_id))
        evidence.append("runbook_finished", executor_id,
                        {"execution_id": execution_id, "runbook": spec.name,
                         "status": status}, conn=c)
    return {"status": status, "execution_id": execution_id, "result": result}


# ---------------------------------------------------------------------------
# Stage 15 — the few safe, observation-grade V1 runbooks.
# ---------------------------------------------------------------------------

def _rb_snapshot_provider_health(params: dict) -> dict:
    from services.sentinel import providers
    table = providers.health_table(conn=params.get("_conn"))
    return {"capabilities": len(table)}


def _rb_verify_snapshot(params: dict, result: dict) -> bool:
    return isinstance(result.get("capabilities"), int)


def _rb_run_invariants(params: dict) -> dict:
    from services.sentinel import invariants
    results = invariants.run_all(conn=params.get("_conn"))
    return {"checked": len(results),
            "violated": sum(1 for r in results if r.status == invariants.STATUS_VIOLATED)}


def _rb_verify_invariants(params: dict, result: dict) -> bool:
    return result.get("checked", 0) > 0


def _rb_verify_evidence_chain(params: dict) -> dict:
    from services.sentinel import evidence as ev
    return ev.verify_chain(conn=params.get("_conn"))


def _rb_verify_chain_result(params: dict, result: dict) -> bool:
    return "ok" in result


SAFE_RUNBOOKS = (
    register(RunbookSpec(
        "snapshot_provider_health", "Read provider capability health table",
        "OPERATIONAL", AuthorityLevel.ACT_REVERSIBLE,
        RiskBudget(actions_per_hour=12, max_affected_entities=50),
        _rb_snapshot_provider_health, _rb_verify_snapshot)),
    register(RunbookSpec(
        "run_financial_invariants", "Run read-only financial invariant checks",
        "OPERATIONAL", AuthorityLevel.ACT_REVERSIBLE,
        RiskBudget(actions_per_hour=12, max_affected_entities=10),
        _rb_run_invariants, _rb_verify_invariants)),
    register(RunbookSpec(
        "verify_evidence_chain", "Recompute the evidence hash chain",
        "SECURITY", AuthorityLevel.ACT_REVERSIBLE,
        RiskBudget(actions_per_hour=6, max_affected_entities=1),
        _rb_verify_evidence_chain, _rb_verify_chain_result)),
)
