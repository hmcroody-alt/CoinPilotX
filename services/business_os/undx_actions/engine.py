"""Governed UNDX business actions engine — deterministic governance decision
projection over the append-only logs (Stage 6).

Records governance policies and proposed action requests (idempotently), then computes
a per-org projection:

  * for each pending action request, the highest-priority **active** policy whose
    ``action_type`` matches the request (an exact ``action_type`` match beats the ``*``
    wildcard; among equal specificity, higher ``priority`` wins, then ``policy_id``
    ascending) resolves an **effect** — ``allow`` / ``deny`` / ``require_approval``;
  * a policy's optional ``max_risk`` ceiling escalates an otherwise-``allow`` decision to
    ``require_approval`` when the request's declared ``risk`` exceeds the ceiling;
  * when no policy matches, the default effect is ``require_approval`` (safe governance
    default — never a silent allow).

Determinism discipline: no randomness. Decisions are ordered by an explicit tie-break —
effect (``deny`` < ``require_approval`` < ``allow``), then ``action_type`` ascending,
then ``request_id`` ascending — so the output is fully reproducible. The decision table
is a *projection*: recomputing an org is deterministic and idempotent (it replaces that
org's rows, and the UNIQUE ``request_id`` key guarantees exactly-one decision per
request).

Hard boundary — nothing here executes an action. A decision is a governance *label*
summarizing what governance would permit; it is not an instruction and takes no side
effect. No tool runs, no message sends, no content posts, no money moves.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import db
from services.business_os.undx_actions import schema as _schema


VALID_EFFECTS = ("allow", "deny", "require_approval")

# Transparent ordered risk enum. A request's risk "exceeds" a policy ceiling when its
# rank is strictly greater.
_RISK_ORDER = {"read_only": 0, "low": 1, "medium": 2, "high": 3}

# Deterministic decision ordering: deny surfaces first, then approvals, then allows.
_EFFECT_ORDER = {"deny": 0, "require_approval": 1, "allow": 2}

_WILDCARD = "*"


class UndxActionsError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _norm_ts(value: Any) -> str:
    if value in (None, ""):
        return _now()
    return str(value)


def _norm_risk(value: Any, field: str = "risk") -> str:
    r = str(value or "").strip().lower()
    if r == "":
        return "low"
    if r not in _RISK_ORDER:
        raise UndxActionsError(f"unknown {field}: {r!r}")
    return r


def _norm_effect(value: Any) -> str:
    e = str(value or "").strip().lower()
    if e not in VALID_EFFECTS:
        raise UndxActionsError(f"unknown effect: {e!r}")
    return e


def _meta_json(meta: Any) -> Optional[str]:
    if meta in (None, ""):
        return None
    try:
        return json.dumps(meta, sort_keys=True)[:4000]
    except Exception:
        return None


def _json_obj(meta: Any) -> dict:
    if not meta:
        return {}
    if isinstance(meta, dict):
        return dict(meta)
    try:
        parsed = json.loads(str(meta))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _norm_bool(value: Any, default: bool = True) -> int:
    if value is None:
        return 1 if default else 0
    return 1 if (value is True or str(value).strip().lower() in
                 ("1", "true", "yes", "on", "enabled")) else 0


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_policy(org_id: str, action_type: str, effect: str, *,
                  name: Optional[str] = None, max_risk: Any = None, active: Any = True,
                  priority: Any = 0, source: str = "manual",
                  external_ref: Optional[str] = None, meta: Any = None,
                  conn=None) -> dict:
    """Declare a governance policy. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt)."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise UndxActionsError("org_id is required")
    action_type = str(action_type or "").strip()
    if not action_type:
        raise UndxActionsError("action_type is required")
    effect = _norm_effect(effect)
    max_risk_norm = None
    if max_risk not in (None, ""):
        max_risk_norm = _norm_risk(max_risk, "max_risk")
    try:
        priority_i = int(priority)
    except (TypeError, ValueError):
        raise UndxActionsError("priority must be an integer")
    active_i = 1 if (active is True or str(active).strip().lower() in
                     ("1", "true", "yes", "on")) else 0

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT policy_id FROM business_os_undx_policies "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"policy_id": dup["policy_id"], "recorded": False,
                        "deduped": True}
        policy_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_policies "
            "(policy_id,org_id,name,action_type,effect,max_risk,active,priority,"
            "source,external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (policy_id, org_id, name, action_type, effect, max_risk_norm, active_i,
             priority_i, source, external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"policy_id": policy_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_action_request(org_id: str, actor: str, action_type: str, *,
                          subject_ref: Optional[str] = None, risk: Any = "low",
                          params: Any = None, requested_at: Any = None,
                          source: str = "manual", external_ref: Optional[str] = None,
                          meta: Any = None, conn=None) -> dict:
    """Append one proposed action-request fact. Idempotent on ``(source,
    external_ref)`` (NULL ref exempt). Records a *proposal* — nothing is executed
    here."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise UndxActionsError("org_id is required")
    actor = str(actor or "").strip()
    if not actor:
        raise UndxActionsError("actor is required")
    action_type = str(action_type or "").strip()
    if not action_type:
        raise UndxActionsError("action_type is required")
    risk_norm = _norm_risk(risk)

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT request_id FROM business_os_undx_action_requests "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"request_id": existing["request_id"], "recorded": False,
                        "deduped": True}
        rid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_action_requests "
            "(request_id,org_id,actor,action_type,subject_ref,risk,params_json,"
            "requested_at,source,external_ref,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, org_id, actor, action_type, subject_ref, risk_norm,
             _meta_json(params), _norm_ts(requested_at), source, external_ref,
             _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"request_id": rid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def register_tool(tool_name: str, action_type: str, *, version: str = "v1",
                  product_area: Optional[str] = None, risk: Any = "low",
                  confirmation_required: Any = False,
                  feature_flag: Optional[str] = None, enabled: Any = True,
                  allowed_modes: Any = None, meta: Any = None, conn=None) -> dict:
    """Register or update a canonical UNDX tool descriptor.

    This catalog is descriptive. It does not execute the tool; product services still
    own mutations, provider calls, and read-after-write verification.
    """
    tool_name = str(tool_name or "").strip()
    action_type = str(action_type or "").strip()
    version = str(version or "v1").strip() or "v1"
    if not tool_name:
        raise UndxActionsError("tool_name is required")
    if not action_type:
        raise UndxActionsError("action_type is required")
    risk_norm = _norm_risk(risk)
    now = _now()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        row = conn.execute(
            "SELECT tool_id FROM business_os_undx_tool_registry "
            "WHERE tool_name = ? AND version = ?", (tool_name, version)).fetchone()
        if row is None:
            tool_id = _schema.new_id()
            conn.execute(
                "INSERT INTO business_os_undx_tool_registry "
                "(tool_id,tool_name,version,product_area,action_type,risk,"
                "confirmation_required,feature_flag,enabled,allowed_modes_json,"
                "meta_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tool_id, tool_name, version, product_area, action_type, risk_norm,
                 _norm_bool(confirmation_required, False), feature_flag,
                 _norm_bool(enabled), _meta_json(allowed_modes), _meta_json(meta),
                 now, now))
            recorded = True
        else:
            tool_id = row["tool_id"]
            conn.execute(
                "UPDATE business_os_undx_tool_registry SET product_area=?,"
                "action_type=?,risk=?,confirmation_required=?,feature_flag=?,"
                "enabled=?,allowed_modes_json=?,meta_json=?,updated_at=? "
                "WHERE tool_id=?",
                (product_area, action_type, risk_norm,
                 _norm_bool(confirmation_required, False), feature_flag,
                 _norm_bool(enabled), _meta_json(allowed_modes), _meta_json(meta),
                 now, tool_id))
            recorded = False
        if owned:
            conn.commit()
        return {"tool_id": tool_id, "recorded": recorded, "updated": not recorded}
    finally:
        if owned:
            conn.close()


def grant_permission(org_id: str, actor: str, action_type: str, effect: str = "allow",
                     *, scope_ref: Optional[str] = None, max_risk: Any = None,
                     active: Any = True, priority: Any = 0, source: str = "manual",
                     external_ref: Optional[str] = None, expires_at: Optional[str] = None,
                     meta: Any = None, conn=None) -> dict:
    """Append an actor-scoped permission fact.

    Use ``effect='deny'`` for scoped denials and ``effect='require_approval'`` when
    the actor may propose but must get a human confirmation. ``actor='*'`` and
    ``action_type='*'`` are supported wildcards.
    """
    org_id = str(org_id or "").strip()
    actor = str(actor or "").strip()
    action_type = str(action_type or "").strip()
    if not org_id:
        raise UndxActionsError("org_id is required")
    if not actor:
        raise UndxActionsError("actor is required")
    if not action_type:
        raise UndxActionsError("action_type is required")
    effect_norm = _norm_effect(effect)
    max_risk_norm = None
    if max_risk not in (None, ""):
        max_risk_norm = _norm_risk(max_risk, "max_risk")
    try:
        priority_i = int(priority)
    except (TypeError, ValueError):
        raise UndxActionsError("priority must be an integer")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT permission_id FROM business_os_undx_permissions "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"permission_id": dup["permission_id"], "recorded": False,
                        "deduped": True}
        pid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_permissions "
            "(permission_id,org_id,actor,action_type,effect,scope_ref,max_risk,"
            "active,priority,source,external_ref,expires_at,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, org_id, actor, action_type, effect_norm, scope_ref, max_risk_norm,
             _norm_bool(active), priority_i, source, external_ref, expires_at,
             _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"permission_id": pid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_confirmation(org_id: str, request_id: str, actor: str, payload_hash: str,
                        *, status: str = "pending", expires_at: Optional[str] = None,
                        confirmed_at: Optional[str] = None, meta: Any = None,
                        conn=None) -> dict:
    org_id = str(org_id or "").strip()
    request_id = str(request_id or "").strip()
    actor = str(actor or "").strip()
    payload_hash = str(payload_hash or "").strip()
    status_norm = str(status or "pending").strip().lower()
    if status_norm not in {"pending", "confirmed", "expired", "cancelled"}:
        raise UndxActionsError("unknown confirmation status")
    if not org_id or not request_id or not actor or not payload_hash:
        raise UndxActionsError("org_id, request_id, actor and payload_hash are required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        cid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_confirmations "
            "(confirmation_id,org_id,request_id,actor,status,payload_hash,expires_at,"
            "confirmed_at,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, org_id, request_id, actor, status_norm, payload_hash, expires_at,
             confirmed_at, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"confirmation_id": cid, "recorded": True, "status": status_norm}
    finally:
        if owned:
            conn.close()


def record_receipt(org_id: str, action_type: str, actor: str, status: str,
                   *, request_id: Optional[str] = None, canonical_ref: Optional[str] = None,
                   verification: Any = None, result: Any = None, conn=None) -> dict:
    org_id = str(org_id or "").strip()
    action_type = str(action_type or "").strip()
    actor = str(actor or "").strip()
    status_norm = str(status or "").strip().lower()
    if status_norm not in {"verified", "failed", "cancelled", "blocked"}:
        raise UndxActionsError("unknown receipt status")
    if not org_id or not action_type or not actor:
        raise UndxActionsError("org_id, action_type and actor are required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_action_receipts "
            "(receipt_id,org_id,request_id,action_type,actor,status,canonical_ref,"
            "verification_json,result_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, org_id, request_id, action_type, actor, status_norm, canonical_ref,
             _meta_json(verification), _meta_json(result), _now()))
        if owned:
            conn.commit()
        return {"receipt_id": rid, "recorded": True, "status": status_norm}
    finally:
        if owned:
            conn.close()


def activate_emergency_stop(org_id: str, actor: str, reason: str, *,
                            action_type: str = _WILDCARD, active: Any = True,
                            meta: Any = None, conn=None) -> dict:
    org_id = str(org_id or "").strip()
    actor = str(actor or "").strip()
    reason = str(reason or "").strip()
    action_type = str(action_type or _WILDCARD).strip() or _WILDCARD
    if not org_id or not actor or not reason:
        raise UndxActionsError("org_id, actor and reason are required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        sid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_undx_emergency_stops "
            "(stop_id,org_id,action_type,active,actor,reason,created_at,meta_json) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, org_id, action_type, _norm_bool(active), actor, reason, _now(),
             _meta_json(meta)))
        if owned:
            conn.commit()
        return {"stop_id": sid, "recorded": True, "active": bool(_norm_bool(active))}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def _active_policies(conn, org_id: str) -> list:
    rows = conn.execute(
        "SELECT policy_id,action_type,effect,max_risk,priority "
        "FROM business_os_undx_policies WHERE org_id = ? AND active = 1",
        (org_id,)).fetchall()
    return [dict(r) for r in rows]


def _active_stops(conn, org_id: str, action_type: str) -> list:
    rows = conn.execute(
        "SELECT stop_id,action_type,reason FROM business_os_undx_emergency_stops "
        "WHERE org_id = ? AND active = 1 AND action_type IN (?, ?) "
        "ORDER BY CASE WHEN action_type = ? THEN 0 ELSE 1 END, created_at DESC",
        (org_id, action_type, _WILDCARD, action_type)).fetchall()
    return [dict(r) for r in rows]


def _permission_rows(conn, org_id: str, actor: str, action_type: str) -> list:
    now = _now()
    rows = conn.execute(
        "SELECT permission_id,actor,action_type,effect,max_risk,priority,expires_at "
        "FROM business_os_undx_permissions "
        "WHERE org_id = ? AND active = 1 "
        "AND actor IN (?, ?) AND action_type IN (?, ?) "
        "AND (expires_at IS NULL OR expires_at = '' OR expires_at >= ?)",
        (org_id, actor, _WILDCARD, action_type, _WILDCARD, now)).fetchall()
    out = [dict(r) for r in rows]
    out.sort(key=lambda p: (
        0 if p["actor"] == actor else 1,
        0 if p["action_type"] == action_type else 1,
        -int(p.get("priority") or 0),
        str(p.get("permission_id") or ""),
    ))
    return out


def _resolve_permission(conn, org_id: str, actor: str, action_type: str,
                        risk: str) -> Optional[tuple]:
    rows = _permission_rows(conn, org_id, actor, action_type)
    if not rows:
        return None
    winner = rows[0]
    effect = winner["effect"]
    matched = winner["permission_id"]
    ceiling = winner.get("max_risk")
    if effect == "allow" and ceiling:
        if _RISK_ORDER.get(risk, 0) > _RISK_ORDER.get(ceiling, 0):
            return ("require_approval", matched,
                    f"permission {matched} risk {risk} exceeds ceiling {ceiling}")
    return (effect, matched, f"matched actor permission {matched} (effect={effect})")


def _resolve(policies: list, action_type: str, risk: str) -> tuple:
    """Pick the governing policy for one request and resolve the effect.

    Returns ``(effect, matched_policy_id_or_None, reason)``. Exact ``action_type``
    matches beat the ``*`` wildcard; among equal specificity, higher priority wins,
    then policy_id ascending. No match -> require_approval (safe default)."""
    exact = [p for p in policies if p["action_type"] == action_type]
    wild = [p for p in policies if p["action_type"] == _WILDCARD]
    pool = exact if exact else wild
    if not pool:
        return ("require_approval", None,
                "no matching policy — default require_approval")
    # Deterministic winner: highest priority, then policy_id ascending.
    winner = sorted(pool, key=lambda p: (-int(p["priority"] or 0),
                                         str(p["policy_id"])))[0]
    effect = winner["effect"]
    matched = winner["policy_id"]
    reason = f"matched policy {matched} (effect={effect})"
    # Risk ceiling escalation: an allow with an exceeded ceiling becomes approval.
    ceiling = winner.get("max_risk")
    if effect == "allow" and ceiling:
        if _RISK_ORDER.get(risk, 0) > _RISK_ORDER.get(ceiling, 0):
            return ("require_approval", matched,
                    f"risk {risk} exceeds ceiling {ceiling} — require_approval")
    return (effect, matched, reason)


def evaluate_org(org_id: str, *, conn=None) -> dict:
    """Compute (and persist) the governance decision projection for one org. Idempotent:
    replaces the org's rows. Returns the ranked decision list. Nothing is executed —
    a decision is a governance label."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise UndxActionsError("org_id is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        policies = _active_policies(conn, org_id)
        requests = conn.execute(
            "SELECT request_id,actor,action_type,risk FROM "
            "business_os_undx_action_requests WHERE org_id = ?", (org_id,)).fetchall()

        decided = []
        for r in requests:
            d = dict(r)
            stops = _active_stops(conn, org_id, d["action_type"])
            if stops:
                stop = stops[0]
                effect, matched, reason = (
                    "deny",
                    None,
                    f"emergency stop {stop['stop_id']} active for {stop['action_type']}: "
                    f"{stop['reason']}",
                )
            else:
                permission = _resolve_permission(
                    conn, org_id, d["actor"], d["action_type"], d["risk"])
                if permission is not None:
                    effect, matched, reason = permission
                    if matched:
                        matched = f"permission:{matched}"
                else:
                    effect, matched, reason = _resolve(
                        policies, d["action_type"], d["risk"])
            decided.append({"request_id": d["request_id"], "actor": d["actor"],
                            "action_type": d["action_type"], "risk": d["risk"],
                            "effect": effect, "matched_policy_id": matched,
                            "reason": reason})

        # Deterministic ordering: effect (deny < approval < allow), then action_type
        # asc, then request_id asc.
        decided.sort(key=lambda x: (_EFFECT_ORDER.get(x["effect"], 9),
                                    x["action_type"], x["request_id"]))

        conn.execute(
            "DELETE FROM business_os_undx_decisions WHERE org_id = ?", (org_id,))

        now = _now()
        out = []
        for rank, d in enumerate(decided, start=1):
            conn.execute(
                "INSERT INTO business_os_undx_decisions "
                "(row_id,org_id,request_id,action_type,actor,risk,effect,"
                "matched_policy_id,reason,rank,computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), org_id, d["request_id"], d["action_type"],
                 d["actor"], d["risk"], d["effect"], d["matched_policy_id"],
                 d["reason"], rank, now))
            d2 = dict(d)
            d2["rank"] = rank
            out.append(d2)
        if owned:
            conn.commit()
        return {"org_id": org_id, "count": len(out), "decisions": out}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_decisions(org_id: str, *, limit: int = 200, conn=None) -> list:
    """Read the stored decision projection for an org, best rank first."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT request_id,action_type,actor,risk,effect,matched_policy_id,reason,"
            "rank FROM business_os_undx_decisions WHERE org_id = ? "
            "ORDER BY rank ASC LIMIT ?", (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_policies(org_id: str, *, limit: int = 200, conn=None) -> list:
    """The declared policies for an org (active first, then priority desc)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT policy_id,name,action_type,effect,max_risk,active,priority,"
            "created_at FROM business_os_undx_policies WHERE org_id = ? "
            "ORDER BY active DESC, priority DESC, policy_id ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_requests(org_id: str, *, limit: int = 500, conn=None) -> list:
    """The proposed action requests for an org (most recent first)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT request_id,actor,action_type,subject_ref,risk,requested_at "
            "FROM business_os_undx_action_requests WHERE org_id = ? "
            "ORDER BY requested_at DESC, request_id ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_tools(*, product_area: Optional[str] = None, limit: int = 200,
               conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = ("SELECT tool_id,tool_name,version,product_area,action_type,risk,"
             "confirmation_required,feature_flag,enabled,updated_at "
             "FROM business_os_undx_tool_registry")
        params: list[Any] = []
        if product_area:
            q += " WHERE product_area = ?"
            params.append(product_area)
        q += " ORDER BY product_area ASC, tool_name ASC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_permissions(org_id: str, *, actor: Optional[str] = None, limit: int = 200,
                     conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = ("SELECT permission_id,actor,action_type,effect,scope_ref,max_risk,"
             "active,priority,expires_at,created_at "
             "FROM business_os_undx_permissions WHERE org_id = ?")
        params: list[Any] = [str(org_id)]
        if actor:
            q += " AND actor = ?"
            params.append(str(actor))
        q += " ORDER BY active DESC, priority DESC, created_at DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_stops(org_id: str, *, active_only: bool = True, limit: int = 100,
               conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        q = ("SELECT stop_id,action_type,active,actor,reason,created_at,cleared_at "
             "FROM business_os_undx_emergency_stops WHERE org_id = ?")
        params: list[Any] = [str(org_id)]
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        return [dict(r) for r in conn.execute(q, tuple(params)).fetchall()]
    finally:
        if owned:
            conn.close()


def list_confirmations(org_id: str, *, limit: int = 100, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT confirmation_id,request_id,actor,status,expires_at,confirmed_at,"
            "created_at FROM business_os_undx_confirmations WHERE org_id = ? "
            "ORDER BY created_at DESC LIMIT ?", (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_receipts(org_id: str, *, limit: int = 100, conn=None) -> list:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT receipt_id,request_id,action_type,actor,status,canonical_ref,"
            "created_at FROM business_os_undx_action_receipts WHERE org_id = ? "
            "ORDER BY created_at DESC LIMIT ?", (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def action_center(org_id: str, *, limit: int = 100, conn=None) -> dict:
    """Read one compact, operator-facing action-center snapshot."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise UndxActionsError("org_id is required")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        return {
            "org_id": org_id,
            "active_stops": list_stops(org_id, active_only=True, limit=limit, conn=conn),
            "decisions": get_decisions(org_id, limit=limit, conn=conn),
            "requests": list_requests(org_id, limit=limit, conn=conn),
            "confirmations": list_confirmations(org_id, limit=limit, conn=conn),
            "receipts": list_receipts(org_id, limit=limit, conn=conn),
            "permissions": list_permissions(org_id, limit=limit, conn=conn),
        }
    finally:
        if owned:
            conn.close()
