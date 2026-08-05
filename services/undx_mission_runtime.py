"""Durable worker coordination for the existing UNDX mission and task graph.

This module does not plan and it does not execute product capabilities. Plans remain
owned by :mod:`services.undx_architecture`, mutations remain owned by the governed
tool gateway, and storage remains the existing ``pulse_ai_missions`` /
``pulse_ai_task_nodes`` graph.  Its job is narrower: snapshot fixed bounds, lease one
eligible mission, advance lifecycle nodes whose evidence is already durable, and stop
honestly when a node would require request context or a governed tool invocation.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services import db as db_service
from services.undx_brain import bounds
from services.undx_brain import config as brain_config


WORKER_NAME = "coinpilotx-undx-worker"
RUNTIME_VERSION = "bounded-mission-v1"
ACTIVE_STATUSES = ("ready", "running")
TERMINAL_NODE_STATUSES = {"succeeded", "failed", "cancelled", "skipped", "rolled_back"}


@dataclass(frozen=True)
class RuntimeSurface:
    enabled: bool
    planner: bool
    task_graph: bool
    worker: bool
    fail_closed: bool
    reconciliation: bool
    dynamic_escalation: bool
    lease_seconds: int
    reason: str = ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _as_dict(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def surface(env: Mapping[str, str] | None = None) -> RuntimeSurface:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    dynamic = bool(values.get("UNDX_PLANNER_DYNAMIC_LIMIT_ESCALATION_ALLOWED", False))
    planner = bool(values.get("UNDX_PLANNER_ENABLED", False))
    graph = bool(values.get("UNDX_TASK_GRAPH_ENABLED", False))
    worker = bool(values.get("UNDX_WORKER_ENABLED", False))
    fail_closed = bool(values.get("UNDX_WORKER_FAIL_CLOSED", True))
    emergency = bool(values.get("UNDX_EMERGENCY_KILL_SWITCH", False))
    enabled = bool(values.get("UNDX_BRAIN_ENABLED", False)) and planner and graph and worker
    reason = ""
    if emergency:
        enabled, reason = False, "emergency_kill_switch"
    elif dynamic:
        enabled, reason = False, "dynamic_limit_escalation_is_unsafe"
    elif not fail_closed:
        enabled, reason = False, "worker_fail_closed_is_disabled"
    elif not enabled:
        reason = "runtime_flags_incomplete"
    return RuntimeSurface(
        enabled=enabled,
        planner=planner,
        task_graph=graph,
        worker=worker,
        fail_closed=fail_closed,
        reconciliation=(bool(values.get("UNDX_RECONCILIATION_ENABLED", True))
                        and bool(values.get("UNDX_WORKER_RECONCILIATION_ENABLED", True))),
        dynamic_escalation=dynamic,
        lease_seconds=int(values.get("UNDX_WORKER_LEASE_SECONDS", 90)),
        reason=reason,
    )


def prepare_plan(plan: dict[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Attach an immutable execution envelope before the mission is persisted."""
    prepared = dict(plan or {})
    runtime = surface(env)
    fixed = bounds.budget(dict(env) if env is not None else None)
    nodes = [dict(item) for item in (prepared.get("nodes") or []) if isinstance(item, dict)]
    prepared["nodes"] = nodes
    prepared["mission_runtime"] = {
        "version": RUNTIME_VERSION,
        "worker_managed": bool(runtime.enabled and prepared.get("status") == "ready"),
        "fixed_bounds": {
            "max_steps": fixed.effective_max_steps,
            "max_tool_calls": fixed.max_tool_calls,
            "max_retries": fixed.max_retries,
            "timeout_seconds": fixed.timeout_seconds,
            "max_node_advances": len(nodes),
            "multi_step": fixed.multi_step,
        },
        "node_advances": 0,
        "tool_calls": 0,
        "retries": 0,
        "retrieval_proof": bool(prepared.get("retrieval_proof")),
        "verification_ready": bool(prepared.get("verification_ready")),
        "runtime_reason": runtime.reason,
    }
    return prepared


def checkpoint_for(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "disable_config": str(plan.get("config_version") or ""),
        "preserve_records": True,
        "mission_runtime": dict(plan.get("mission_runtime") or {}),
    }


def _columns(cur, table: str) -> set[str]:
    if db_service.IS_POSTGRES:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in cur.fetchall()}


def ensure_schema(cur) -> None:
    columns = _columns(cur, "pulse_ai_missions")
    additions = {
        "lease_owner": "TEXT",
        "lease_expires_at": "TEXT",
        "attempt_count": "INTEGER DEFAULT 0",
        "last_error": "TEXT",
        "heartbeat_at": "TEXT",
        "completed_at": "TEXT",
        "paused_at": "TEXT",
        "cancel_requested_at": "TEXT",
        "worker_state_json": "TEXT DEFAULT '{}'",
    }
    for name, definition in additions.items():
        if name not in columns:
            cur.execute(f"ALTER TABLE pulse_ai_missions ADD COLUMN {name} {definition}")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_pulse_ai_mission_lease "
        "ON pulse_ai_missions(status, lease_expires_at, updated_at)"
    )


def _runtime_state(mission: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    checkpoint = _json(mission.get("checkpoint_json"), {})
    runtime = _json(checkpoint.get("mission_runtime"), {})
    return checkpoint, runtime


def _lease_expired(raw: Any, now: datetime) -> bool:
    if not raw:
        return True
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= now
    except ValueError:
        return True


def claim_next(cur, worker_id: str, *, env: Mapping[str, str] | None = None,
               now: datetime | None = None) -> dict[str, Any] | None:
    runtime_surface = surface(env)
    if not runtime_surface.enabled:
        return None
    ensure_schema(cur)
    moment = now or _now()
    cur.execute(
        "SELECT * FROM pulse_ai_missions WHERE status IN ('ready','running') "
        "ORDER BY updated_at ASC LIMIT 25"
    )
    for raw in cur.fetchall():
        mission = _as_dict(raw)
        _checkpoint, runtime = _runtime_state(mission)
        if not runtime.get("worker_managed"):
            continue
        if mission.get("status") == "running" and not _lease_expired(mission.get("lease_expires_at"), moment):
            continue
        if mission.get("status") == "running" and not runtime_surface.reconciliation:
            continue
        expiry = _iso(moment + timedelta(seconds=runtime_surface.lease_seconds))
        cur.execute(
            "UPDATE pulse_ai_missions SET status='running', lease_owner=?, "
            "lease_expires_at=?, heartbeat_at=?, attempt_count=COALESCE(attempt_count,0)+1, "
            "updated_at=? WHERE mission_id=? AND status=? AND updated_at=?",
            (worker_id, expiry, _iso(moment), _iso(moment), mission["mission_id"],
             mission["status"], mission.get("updated_at")),
        )
        if cur.rowcount == 1:
            mission.update({"status": "running", "lease_owner": worker_id,
                            "lease_expires_at": expiry, "heartbeat_at": _iso(moment)})
            return mission
    return None


def _persist_runtime(cur, mission_id: str, checkpoint: dict[str, Any], runtime: dict[str, Any],
                     *, status: str, worker_id: str, error: str = "") -> None:
    checkpoint["mission_runtime"] = runtime
    completed = _iso() if status in {"succeeded", "failed", "cancelled"} else None
    cur.execute(
        "UPDATE pulse_ai_missions SET status=?, checkpoint_json=?, worker_state_json=?, "
        "last_error=?, completed_at=COALESCE(?,completed_at), lease_owner='', "
        "lease_expires_at='', updated_at=? WHERE mission_id=? AND lease_owner=?",
        (status, json.dumps(checkpoint, sort_keys=True), json.dumps(runtime, sort_keys=True),
         error[:240], completed, _iso(), mission_id, worker_id),
    )


def advance_claimed(cur, mission: Mapping[str, Any], worker_id: str) -> dict[str, Any]:
    """Advance exactly one durable node, never a product capability."""
    mission_id = str(mission.get("mission_id") or "")
    if not mission_id or str(mission.get("lease_owner") or "") != worker_id:
        return {"advanced": False, "reason": "lease_not_owned"}
    checkpoint, runtime = _runtime_state(mission)
    fixed = _json(runtime.get("fixed_bounds"), {})
    max_advances = max(0, int(fixed.get("max_node_advances") or 0))
    advances = max(0, int(runtime.get("node_advances") or 0))
    if advances >= max_advances:
        _persist_runtime(cur, mission_id, checkpoint, runtime, status="failed",
                         worker_id=worker_id, error="fixed_node_bound_exhausted")
        return {"advanced": False, "mission_id": mission_id,
                "status": "failed", "reason": "fixed_node_bound_exhausted"}

    cur.execute(
        "SELECT * FROM pulse_ai_task_nodes WHERE mission_id=? ORDER BY id",
        (mission_id,),
    )
    nodes = [_as_dict(row) for row in cur.fetchall()]
    next_node = next((node for node in nodes if node.get("status") not in TERMINAL_NODE_STATUSES), None)
    if next_node is None:
        _persist_runtime(cur, mission_id, checkpoint, runtime, status="succeeded",
                         worker_id=worker_id)
        return {"advanced": False, "mission_id": mission_id, "status": "succeeded"}

    node_id = str(next_node.get("node_id") or "")
    kind = str(next_node.get("node_type") or "")
    prior = nodes[:nodes.index(next_node)]
    if any(node.get("status") != "succeeded" for node in prior):
        _persist_runtime(cur, mission_id, checkpoint, runtime, status="blocked",
                         worker_id=worker_id, error="dependency_not_satisfied")
        return {"advanced": False, "mission_id": mission_id, "status": "blocked",
                "reason": "dependency_not_satisfied"}

    result: dict[str, Any]
    if kind == "understand":
        result = {"bounded_goal_recorded": True}
    elif kind == "retrieve" and runtime.get("retrieval_proof"):
        result = {"authorized_retrieval_proof_present": True}
    elif kind == "verify" and runtime.get("verification_ready"):
        result = {"independent_verification_ready": True}
    else:
        reason = ("governed_tool_requires_request_context" if kind == "call_tool"
                  else "retrieval_proof_missing" if kind == "retrieve"
                  else "verification_evidence_missing")
        cur.execute(
            "UPDATE pulse_ai_task_nodes SET status='blocked', result_json=?, updated_at=? "
            "WHERE node_id=?",
            (json.dumps({"reason": reason}, sort_keys=True), _iso(), node_id),
        )
        _persist_runtime(cur, mission_id, checkpoint, runtime, status="blocked",
                         worker_id=worker_id, error=reason)
        return {"advanced": False, "mission_id": mission_id, "node_id": node_id,
                "status": "blocked", "reason": reason}

    runtime["node_advances"] = advances + 1
    cur.execute(
        "UPDATE pulse_ai_task_nodes SET status='succeeded', result_json=?, updated_at=? "
        "WHERE node_id=?",
        (json.dumps(result, sort_keys=True), _iso(), node_id),
    )
    remaining = [node for node in nodes if node.get("node_id") != node_id
                 and node.get("status") not in TERMINAL_NODE_STATUSES]
    status = "succeeded" if not remaining else "ready"
    _persist_runtime(cur, mission_id, checkpoint, runtime, status=status, worker_id=worker_id)
    return {"advanced": True, "mission_id": mission_id, "node_id": node_id,
            "node_type": kind, "status": status}


def request_pause(cur, user_id: int, mission_id: str) -> bool:
    cur.execute(
        "UPDATE pulse_ai_missions SET status='paused', paused_at=?, lease_owner='', "
        "lease_expires_at='', updated_at=? WHERE mission_id=? AND user_id=? "
        "AND status IN ('ready','running','blocked')",
        (_iso(), _iso(), mission_id, int(user_id)),
    )
    return cur.rowcount == 1


def resume(cur, user_id: int, mission_id: str) -> bool:
    cur.execute(
        "UPDATE pulse_ai_missions SET status='ready', paused_at='', last_error='', "
        "updated_at=? WHERE mission_id=? AND user_id=? AND status IN ('paused','blocked')",
        (_iso(), mission_id, int(user_id)),
    )
    return cur.rowcount == 1


def cancel(cur, user_id: int, mission_id: str) -> bool:
    cur.execute(
        "UPDATE pulse_ai_missions SET status='cancelled', cancel_requested_at=?, "
        "completed_at=?, lease_owner='', lease_expires_at='', updated_at=? "
        "WHERE mission_id=? AND user_id=? AND status NOT IN ('succeeded','failed','cancelled')",
        (_iso(), _iso(), _iso(), mission_id, int(user_id)),
    )
    return cur.rowcount == 1


def worker_identity() -> str:
    return f"{WORKER_NAME}:{socket.gethostname()}:{os.getpid()}"


def poll_once(*, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    runtime = surface(env)
    if not runtime.enabled:
        return {"enabled": False, "reason": runtime.reason, "advanced": False}
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        mission = claim_next(cur, worker_identity(), env=env)
        if not mission:
            conn.commit()
            return {"enabled": True, "advanced": False, "reason": "no_eligible_mission"}
        outcome = advance_claimed(cur, mission, str(mission["lease_owner"]))
        conn.commit()
        return {"enabled": True, **outcome}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

