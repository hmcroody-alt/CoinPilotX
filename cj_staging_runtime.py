"""Isolated Railway staging entry points; never used by production Procfile."""
import argparse
from datetime import datetime, timezone
import json
import os
import re
import time
import uuid
from urllib.parse import urlparse

PROJECT = "34d4cb5c-f3db-40bf-926e-2eaa80a91659"
ENVIRONMENT = "3a3f2632-bfc1-4ef4-b95a-e99e278d0fc1"
ORIGIN = "https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app"


def guard():
    from services.business_os.suppliers import policy, vault
    assert os.getenv("RAILWAY_PROJECT_ID") == PROJECT, "staging project required"
    assert os.getenv("RAILWAY_ENVIRONMENT_ID") == ENVIRONMENT, "staging environment required"
    assert os.getenv("CJ_STAGING_ACCEPTANCE") == "1"
    parsed = urlparse(os.getenv("DATABASE_URL", ""))
    assert parsed.scheme in {"postgres", "postgresql"} and parsed.hostname.endswith(".railway.internal")
    assert os.getenv("COINPILOTX_DISABLE_LOCAL_ENV") == "1"
    assert os.getenv("COINPILOTX_INIT_DB_ON_IMPORT") == "0"
    assert not os.getenv("CJ_API_KEY"), "merchant credentials cannot be shared environment variables"
    policy.require_sandbox({"isSandbox": 1})
    assert not policy.enabled("REAL_CJ_FUNDING_ENABLED")
    assert not policy.enabled("CJ_SUBSCRIPTION_MUTATIONS_ENABLED")
    vault.require_available()
    vault.require_index_available()


def ensure_runtime_schema():
    from services import db
    assert db.IS_POSTGRES
    conn = db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS cj_staging_worker_heartbeat (
            worker_name TEXT PRIMARY KEY, boot_id TEXT NOT NULL, sequence BIGINT NOT NULL,
            observed_at DOUBLE PRECISION NOT NULL, state TEXT NOT NULL, deployed_sha TEXT NOT NULL)""")
        conn.commit()
    finally:
        conn.close()


def bootstrap():
    guard()
    import bot
    bot.init_db()
    assert bot.INIT_DB_COMPLETED
    from services.business_os.business import schema as business
    from services.business_os.store import schema as store
    from services.business_os.marketplace import schema as marketplace
    from services.business_os.suppliers import schema, gateway, fulfillment, quota, worker
    from services.business_os.payments import webhook_inbox
    for module in (business, store, marketplace, schema, gateway, fulfillment, quota, worker, webhook_inbox):
        module.ensure_schema()
    ensure_runtime_schema()
    print(json.dumps({"staging_bootstrap": "PASS", "engine": "postgresql"}), flush=True)


def worker_main(once=False):
    guard()
    ensure_runtime_schema()
    from services import db
    from supplier_worker import run_tick
    boot_id = uuid.uuid4().hex
    sequence = 0
    while True:
        started = time.monotonic()
        result = run_tick()
        sequence += 1
        conn = db.connect()
        try:
            conn.execute("""INSERT INTO cj_staging_worker_heartbeat
                (worker_name,boot_id,sequence,observed_at,state,deployed_sha) VALUES(?,?,?,?,?,?)
                ON CONFLICT(worker_name) DO UPDATE SET boot_id=excluded.boot_id, sequence=excluded.sequence,
                observed_at=excluded.observed_at,state=excluded.state,deployed_sha=excluded.deployed_sha""",
                ("supplier", boot_id, sequence, time.time(), result["status"], os.getenv("CJ_DEPLOYED_SHA", "unknown")))
            conn.commit()
        finally:
            conn.close()
        print(json.dumps({"supplier_tick": result, "sequence": sequence,
                          "duration_ms": round((time.monotonic() - started) * 1000),
                          "postgres_heartbeat": "committed"}), flush=True)
        if once:
            return
        time.sleep(60)


def health():
    from services import db
    from services.business_os.suppliers import policy, vault
    payload = {"environment": "isolated-cj-staging", "deployed_sha": os.getenv("CJ_DEPLOYED_SHA", "unknown"),
               "postgresql": False, "supplier_schema": False, "vault": vault.available(),
               "provider_approval_recorded": policy.enabled("CJ_HOSTED_CREDENTIALS_APPROVED"),
               "production_fulfillment": False, "real_funding": False, "worker": {"observed": False}}
    required = {"business_os_supplier_connections", "business_os_supplier_credential_vault",
                "business_os_supplier_intents", "business_os_supplier_outbox", "business_os_supplier_sync_jobs"}
    try:
        guard()
        conn = db.connect()
        try:
            payload["postgresql"] = bool(conn.execute("SELECT 1").fetchone()[0]) and db.IS_POSTGRES
            names = {row[0] for row in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()")}
            payload["supplier_schema"] = required <= names
            if "cj_staging_worker_heartbeat" in names:
                row = conn.execute("SELECT sequence,observed_at,state FROM cj_staging_worker_heartbeat WHERE worker_name='supplier'").fetchone()
                if row:
                    payload["worker"] = {"observed": True, "sequence": row[0], "state": row[2],
                                         "age_seconds": max(0, round(time.time() - row[1]))}
        finally:
            conn.close()
        ready = payload["postgresql"] and payload["supplier_schema"] and payload["vault"]
    except Exception:
        ready = False  # Never expose database URL, exception, keys, or provider payload.
    payload["infrastructure_ready"] = ready
    payload["live_cj_accepted"] = False
    return payload, 200 if ready else 503


def configure_http(bot_module):
    """Keep redirects local to staging and permit Railway's plain-HTTP probes.

    Only exact read-only health paths bypass HTTPS redirection. All merchant
    routes retain canonical authentication, CSRF and TLS handling.
    """
    from flask import jsonify, request
    bot_module.CANONICAL_HTTPS_ORIGIN = ORIGIN
    bot_module.CANONICAL_HTTPS_HOSTS = {urlparse(ORIGIN).hostname}
    bot_module.APP_BASE_URL = bot_module.BASE_URL = ORIGIN

    def readiness():
        if request.method != "GET" or request.path not in {"/health/ready", "/health/cj-staging"}:
            return None
        payload, status = health()
        failed = sorted(name for name, state in bot_module.ROUTE_PACK_STATUS.items() if not state.get("registered"))
        if failed:
            payload["infrastructure_ready"], status = False, 503
        payload["failed_route_packs"] = failed
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response, status

    bot_module.app.before_request_funcs.setdefault(None, []).insert(0, readiness)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "worker"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.action == "bootstrap":
        bootstrap()
    else:
        worker_main(args.once)
