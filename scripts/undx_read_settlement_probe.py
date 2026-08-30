"""Measure what the real gateway hands the run queue for a real read.

The Stage 25 QA run settled a read that verifiably executed as ``failed``, with the
client-facing description "This did not happen." This script isolates the disagreement to
a single set of printed field values so the cause can be read off rather than argued.

It calls :func:`services.undx_tool_gateway.execute` on ``activity.daily_summary`` with the
same fixture the QA run uses, then prints every field the settlement rule in
:func:`services.undx_agent_runs._settled_status` consults, plus the fields the gateway
computes them from. The write stop stays engaged; this runs a read.

Usage::

    python3 scripts/undx_read_settlement_probe.py
"""

from __future__ import annotations

import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

QA_USER_ID = 7
QA_CAPABILITY = "activity.daily_summary"

FLAGS = {
    "UNDX_AGENT_ENABLED": "1",
    "UNDX_AGENT_READS_ENABLED": "1",
    "UNDX_AGENT_RUNS_ENABLED": "1",
    "UNDX_WORKER_ENABLED": "1",
    "UNDX_BRAIN_ENABLED": "1",
    "UNDX_AGENT_QA_USER_IDS": "7,8",
    "UNDX_AGENT_WRITES_ENABLED": "0",
}

PRODUCT_DDL = (
    "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, "
    "email TEXT, account_status TEXT DEFAULT 'active')",
    "CREATE TABLE IF NOT EXISTS pulse_messages (id INTEGER PRIMARY KEY, "
    "conversation_id INTEGER, sender_user_id INTEGER, recipient_user_id INTEGER, "
    "body TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_posts (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "title TEXT, body TEXT, created_at TEXT, deleted_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_reels (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "status TEXT DEFAULT 'active', created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_statuses (id INTEGER PRIMARY KEY, user_id INTEGER, "
    "created_at TEXT, deleted_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_follows (id INTEGER PRIMARY KEY, "
    "follower_user_id INTEGER, followed_user_id INTEGER, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS pulse_conversation_participants (id INTEGER PRIMARY KEY, "
    "conversation_id INTEGER, user_id INTEGER)",
    "CREATE TABLE IF NOT EXISTS push_subscriptions (id INTEGER PRIMARY KEY, "
    "user_id INTEGER, endpoint TEXT, is_active INTEGER DEFAULT 1)",
)


def main() -> int:
    from services import undx_agent_policy

    flags = {**FLAGS, undx_agent_policy.GLOBAL_WRITE_KILL_SWITCH_ENV: "1"}
    path = tempfile.mkstemp(prefix="undx_probe_", suffix=".db")[1]
    saved = {key: os.environ.get(key) for key in [*flags, "DATABASE_URL"]}
    os.environ.update(flags)
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"

    try:
        from services import (alert_engine, undx_agent_runs, undx_architecture,
                              undx_capability_registry, undx_tool_gateway)
        from services import db as db_module

        conn = db_module.connect()
        cur = conn.cursor()
        undx_architecture.ensure_schema(cur)
        undx_agent_runs.ensure_schema(cur)
        for ddl in PRODUCT_DDL:
            cur.execute(ddl)
        cur.execute("INSERT OR IGNORE INTO users (user_id, username, email) VALUES (?,?,?)",
                    (QA_USER_ID, "qa_user", "qa@example.test"))
        alert_engine._ALERT_SCHEMA_READY = False
        alert_engine.ensure_alert_schema(conn)
        conn.commit()

        spec = undx_capability_registry.get(QA_CAPABILITY)
        print("== the registry's declaration")
        print(f"capability_id      : {spec.capability_id}")
        print(f"risk               : {spec.risk}")
        print(f"is_write           : {spec.is_write}")
        print(f"verifier           : {spec.verifier!r}   ('' means no read-back path)")

        outcome = undx_tool_gateway.execute(
            cur, user_id=QA_USER_ID, capability_id=QA_CAPABILITY, proposed_arguments={},
            request_id="probe-1", explicit_request=True, target_chosen_by_agent=False,
        )
        conn.commit()
        receipt = outcome.receipt

        print("\n== what the gateway produced")
        print(f"receipt.status             : {receipt.status}")
        print(f"receipt.verification_state : {receipt.verification_state}")
        print(f"receipt.may_claim_completed: {receipt.may_claim_completed}")
        print(f"outcome.is_write           : {outcome.is_write}")
        print(f"outcome.succeeded          : {outcome.succeeded}")
        print(f"outcome.may_claim_done     : {outcome.may_claim_done}")

        print("\n== what the settlement rule reads")
        print(f"hasattr(outcome, 'may_claim_completed'): "
              f"{hasattr(outcome, 'may_claim_completed')}")
        print(f"getattr(outcome, 'may_claim_completed', False): "
              f"{getattr(outcome, 'may_claim_completed', False)}")
        print(f"GatewayOutcome.__slots__: {undx_tool_gateway.GatewayOutcome.__slots__}")
        print(f"_settled_status(...) -> {undx_agent_runs._settled_status(outcome, receipt.status)!r}")

        print("\n== reading")
        if not hasattr(outcome, "may_claim_completed"):
            print("DEFECT A: GatewayOutcome has no 'may_claim_completed'. The settlement "
                  "rule's getattr default makes every run fall through to failed/partial, "
                  "for writes as well as reads.")
        if not receipt.may_claim_completed and receipt.status == "verified_success":
            print("DEFECT B: a read reaches verified_success with verification_state "
                  f"{receipt.verification_state!r}, so may_claim_completed — a write-"
                  "completion predicate — is false for a read that succeeded.")
        return 0
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
