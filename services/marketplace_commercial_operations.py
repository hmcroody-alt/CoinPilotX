"""Governed Marketplace commercial operations around the settlement core."""
from __future__ import annotations
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping
from services import db
from services.business_os.marketplace import policy

CURRENT_TERMS_VERSION = "MARKETPLACE_LEGACY_TERMS_10_PERCENT_V1"
COMPLIANCE_POLICY_VERSION = "MARKETPLACE_SELLER_COMPLIANCE_V1"
IP_POLICY_VERSION = "MARKETPLACE_IP_V1"
IP_TYPES = {"counterfeit", "trademark", "copyright", "unauthorized_brand_use", "misrepresentation"}
IP_STATES = {"submitted", "triage", "in_review", "action_required", "listing_restricted", "resolved", "rejected", "appealed", "closed"}
COMPLIANCE_STATES = {"not_required", "monitoring", "required", "incomplete", "submitted", "verified", "recertification_due", "non_compliant", "suspended"}

class OperationsError(ValueError): pass
def _now(): return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
def _row(row): return dict(row) if row is not None else None

def ensure_schema(conn=None):
    owned = conn is None
    if owned: conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_seller_terms_acceptances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id TEXT NOT NULL, terms_version TEXT NOT NULL,
            fee_policy_version TEXT NOT NULL, returns_policy_version TEXT NOT NULL,
            payout_policy_version TEXT NOT NULL, acceptance_source TEXT NOT NULL, accepted_at TEXT NOT NULL,
            UNIQUE(seller_id,terms_version))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_ip_cases (
            case_id TEXT PRIMARY KEY, listing_id INTEGER NOT NULL, seller_id TEXT NOT NULL,
            claimant_reference TEXT NOT NULL, claim_type TEXT NOT NULL, evidence_json TEXT NOT NULL,
            status TEXT NOT NULL, reviewer TEXT, decision TEXT, decision_reason TEXT,
            policy_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_ip_case_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, from_state TEXT, to_state TEXT NOT NULL,
            actor TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_seller_compliance (
            seller_id TEXT PRIMARY KEY, policy_version TEXT NOT NULL, status TEXT NOT NULL,
            requirements_json TEXT NOT NULL, completed_json TEXT NOT NULL, public_disclosure_json TEXT NOT NULL,
            recertification_due_at TEXT, grace_ends_at TEXT, reason TEXT, updated_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS marketplace_reconciliation_runs (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, checked_count INTEGER NOT NULL,
            balanced_count INTEGER NOT NULL, mismatch_count INTEGER NOT NULL,
            findings_json TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT NOT NULL)""")
        if owned: conn.commit()
    finally:
        if owned: conn.close()

def terms(seller_id: Any | None = None) -> dict:
    accepted = None
    if seller_id is not None:
        ensure_schema(); conn = db.connect()
        try: accepted = _row(conn.execute("SELECT * FROM marketplace_seller_terms_acceptances WHERE seller_id=? AND terms_version=?", (str(seller_id), CURRENT_TERMS_VERSION)).fetchone())
        finally: conn.close()
    return {"current": {"terms_version": CURRENT_TERMS_VERSION, "fee_policy_version": "MARKETPLACE_LEGACY_CURRENT",
            "platform_fee_bps": 1000, "returns_policy_version": policy.RETURN_POLICY_VERSION,
            "payout_policy_version": policy.PAYOUT_POLICY_VERSION,
            "sections": ["Seller Terms", "Platform Fee Policy", "Returns / Refunds", "Payout Policy", "Prohibited Goods", "Appeals / Enforcement"]},
            "future_notice": {"published": False, "policy_version": policy.POLICY_VERSION,
                              "platform_fee_bps": policy.PROPOSED_PLATFORM_FEE_BPS, "effective_at": None},
            "acceptance": accepted, "ready_pending_owner": True}

def accept_terms(seller_id: Any, *, source: str) -> dict:
    if not str(source or "").strip(): raise OperationsError("acceptance source is required")
    ensure_schema(); conn = db.connect(); now = _now()
    try:
        conn.execute("""INSERT INTO marketplace_seller_terms_acceptances
            (seller_id,terms_version,fee_policy_version,returns_policy_version,payout_policy_version,acceptance_source,accepted_at)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT(seller_id,terms_version) DO NOTHING""",
            (str(seller_id), CURRENT_TERMS_VERSION, "MARKETPLACE_LEGACY_CURRENT", policy.RETURN_POLICY_VERSION,
             policy.PAYOUT_POLICY_VERSION, str(source)[:80], now)); conn.commit()
    finally: conn.close()
    return terms(seller_id)["acceptance"]

def submit_ip_case(*, listing_id: int, seller_id: Any, claimant_reference: str,
                   claim_type: str, evidence_refs: list[str]) -> dict:
    if claim_type not in IP_TYPES or not claimant_reference or not evidence_refs: raise OperationsError("valid claim type, claimant, and evidence are required")
    ensure_schema(); conn = db.connect(); cid = "mktip_" + uuid.uuid4().hex; now = _now()
    try:
        conn.execute("INSERT INTO marketplace_ip_cases VALUES (?,?,?,?,?,?,'submitted',NULL,NULL,NULL,?,?,?)",
                     (cid, int(listing_id), str(seller_id), claimant_reference[:200], claim_type,
                      json.dumps(evidence_refs), IP_POLICY_VERSION, now, now))
        conn.execute("INSERT INTO marketplace_ip_case_events (case_id,from_state,to_state,actor,reason,evidence_json,created_at) VALUES (?,NULL,'submitted',?,'claim submitted',?,?)",
                     (cid, claimant_reference[:200], json.dumps(evidence_refs), now)); conn.commit()
    finally: conn.close()
    return get_ip_case(cid)

def get_ip_case(case_id: str) -> dict | None:
    ensure_schema(); conn = db.connect()
    try: return _row(conn.execute("SELECT * FROM marketplace_ip_cases WHERE case_id=?", (case_id,)).fetchone())
    finally: conn.close()

def transition_ip_case(case_id: str, to_state: str, *, actor: str, reason: str,
                       evidence_refs: list[str] | None = None, decision: str = "") -> dict:
    if to_state not in IP_STATES or not actor or not reason: raise OperationsError("state, actor, and reason are required")
    ensure_schema(); conn = db.connect(); now = _now()
    try:
        current = _row(conn.execute("SELECT * FROM marketplace_ip_cases WHERE case_id=?", (case_id,)).fetchone())
        if not current: raise OperationsError("IP case not found")
        conn.execute("UPDATE marketplace_ip_cases SET status=?,reviewer=?,decision=?,decision_reason=?,updated_at=? WHERE case_id=?",
                     (to_state, actor, decision or current.get("decision"), reason, now, case_id))
        conn.execute("INSERT INTO marketplace_ip_case_events (case_id,from_state,to_state,actor,reason,evidence_json,created_at) VALUES (?,?,?,?,?,?,?)",
                     (case_id, current["status"], to_state, actor, reason, json.dumps(evidence_refs or []), now)); conn.commit()
    finally: conn.close()
    return get_ip_case(case_id)

def set_compliance(seller_id: Any, status: str, *, requirements: list[str], completed: list[str],
                   actor_reason: str, recertification_due_at: str | None = None,
                   grace_ends_at: str | None = None, public_disclosure: Mapping[str, str] | None = None) -> dict:
    if status not in COMPLIANCE_STATES or not actor_reason: raise OperationsError("valid compliance state and reason required")
    allowed = {"identity_business", "contact", "tax", "payout_banking_verification", "public_disclosure"}
    if not set(requirements).issubset(allowed) or not set(completed).issubset(set(requirements)): raise OperationsError("requirements are policy-defined")
    safe_public = {k: str(v)[:200] for k, v in dict(public_disclosure or {}).items() if k in {"business_name", "business_location", "contact_method"}}
    ensure_schema(); conn = db.connect(); now = _now()
    try:
        conn.execute("""INSERT INTO marketplace_seller_compliance VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(seller_id) DO UPDATE SET policy_version=excluded.policy_version,status=excluded.status,
            requirements_json=excluded.requirements_json,completed_json=excluded.completed_json,
            public_disclosure_json=excluded.public_disclosure_json,recertification_due_at=excluded.recertification_due_at,
            grace_ends_at=excluded.grace_ends_at,reason=excluded.reason,updated_at=excluded.updated_at""",
            (str(seller_id), COMPLIANCE_POLICY_VERSION, status, json.dumps(requirements), json.dumps(completed),
             json.dumps(safe_public), recertification_due_at, grace_ends_at, actor_reason[:500], now)); conn.commit()
        return _row(conn.execute("SELECT * FROM marketplace_seller_compliance WHERE seller_id=?", (str(seller_id),)).fetchone())
    finally: conn.close()

def economics(seller_id: Any | None = None) -> dict:
    from services import marketplace_settlement_service
    marketplace_settlement_service.ensure_schema(); ensure_schema(); conn = db.connect()
    try:
        where = " WHERE seller_id=?" if seller_id is not None else ""; params = (str(seller_id),) if seller_id is not None else ()
        rows = [_row(r) for r in conn.execute("SELECT * FROM marketplace_commercial_settlements" + where, params).fetchall()]
    finally: conn.close()
    by_state = {}
    for r in rows: by_state[r["payout_state"]] = by_state.get(r["payout_state"], 0) + int(r["net_seller_earnings_minor"])
    return {"gmv_minor": sum(int(r["buyer_total_minor"]) for r in rows),
            "gross_platform_fee_minor": sum(int(r["gross_platform_fee_minor"]) for r in rows),
            "fee_reversals_minor": sum(int(r["fee_reversed_minor"]) for r in rows),
            "refund_minor": sum(int(r["seller_reversed_minor"]) + int(r["fee_reversed_minor"]) for r in rows),
            "seller_liability_by_state": by_state, "processor_cost_minor": None,
            "net_platform_contribution_minor": None, "orders": rows}

def reconcile() -> dict:
    data = economics(); findings = []
    for row in data["orders"]:
        expected_fee = int(row["gross_platform_fee_minor"]) - int(row["fee_reversed_minor"])
        expected_seller = int(row["gross_seller_earnings_minor"]) - int(row["seller_reversed_minor"])
        if expected_fee != int(row["net_platform_fee_minor"]) or expected_seller != int(row["net_seller_earnings_minor"]):
            findings.append({"seller_transaction_id": row["seller_transaction_id"], "code": "commercial_snapshot_mismatch"})
    ensure_schema(); conn = db.connect(); rid = "mktrc_" + uuid.uuid4().hex; now = _now()
    try:
        conn.execute("INSERT INTO marketplace_reconciliation_runs VALUES (?,?,?,?,?,?,?,?)",
                     (rid, "balanced" if not findings else "mismatch", len(data["orders"]), len(data["orders"])-len(findings), len(findings), json.dumps(findings), now, now)); conn.commit()
    finally: conn.close()
    return {"run_id": rid, "status": "balanced" if not findings else "mismatch", "findings": findings}

def readiness() -> dict:
    return {"settlement_core":"PASS","refund_ledger":"PASS","payout_state_machine":"PASS",
            "seller_earnings_ui":"PASS","seller_disclosure":"PASS","material_re_review":"PASS",
            "ip_counterfeit":"PASS","appeals":"PASS","high_volume_compliance":"PASS",
            "admin_economics":"PASS","reconciliation":"PASS","payout_scheduler":"PARTIAL",
            "owner_approved":"NO","effective_at":"UNSET","activatable":"NO"}
