"""Bounded, durable CJ background scheduler. No whole-catalog sweeps or funding.

Only explicitly selected resources and connected-account housekeeping are read.
Every network attempt still passes through the adapter's shared quota controller.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

from services import db
from services.business_os.payments import webhook_inbox
from . import fulfillment, policy

KINDS = frozenset({"health", "shops", "subscriptions", "product", "inventory", "order", "tracking"})
CADENCE = {"health": 900, "shops": 3600, "subscriptions": 3600, "product": 3600,
           "inventory": 900, "order": 300, "tracking": 900}


def ensure_schema(conn=None):
    owned = conn is None
    conn = conn or db.connect()
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS business_os_supplier_sync_jobs (
            id TEXT PRIMARY KEY, connection_id TEXT NOT NULL, business_id TEXT NOT NULL,
            store_id TEXT NOT NULL, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
            available_at DOUBLE PRECISION NOT NULL, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
            lease_token TEXT, failures INTEGER NOT NULL DEFAULT 0,
            last_verified_at DOUBLE PRECISION, evidence_hash TEXT, last_error TEXT,
            UNIQUE(connection_id,kind,resource_id))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_supplier_sync_due "
                     "ON business_os_supplier_sync_jobs(available_at,lease_until)")
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def schedule(*, connection_id, business_id, store_id, kind, resource_id="", now=None, dirty=False):
    """Internal-only; callers must establish ownership before scheduling IDs."""
    if kind not in KINDS or not isinstance(resource_id, str) or len(resource_id) > 200:
        raise fulfillment.FulfillmentError("invalid_sync_resource", 400)
    if kind in {"product", "inventory", "order", "tracking"} and not resource_id:
        raise fulfillment.FulfillmentError("missing_sync_resource", 400)
    now = time.time() if now is None else now
    ensure_schema()
    conn = db.connect()
    try:
        conn.execute("INSERT INTO business_os_supplier_sync_jobs "
             "(id,connection_id,business_id,store_id,kind,resource_id,available_at) VALUES(?,?,?,?,?,?,?) "
             "ON CONFLICT(connection_id,kind,resource_id) DO NOTHING",
             (uuid.uuid4().hex, connection_id, business_id, store_id, kind, resource_id, now))
        if dirty:
            # Webhook replays cannot cancel leases or produce a second worker claim.
            conn.execute("UPDATE business_os_supplier_sync_jobs SET available_at=? "
                         "WHERE connection_id=? AND kind=? AND resource_id=? AND available_at>?",
                         (now, connection_id, kind, resource_id, now))
        conn.commit()
    finally:
        conn.close()


def schedule_connection(connection_id, business_id, store_id, *, now=None):
    from . import connections
    connections.worker_connection(connection_id, business_id, store_id)
    for kind in ("health", "shops", "subscriptions"):
        schedule(connection_id=connection_id, business_id=business_id, store_id=store_id,
                 kind=kind, now=now)


def _claim(now):
    conn = db.connect()
    token = uuid.uuid4().hex
    try:
        webhook_inbox._begin(conn)
        row = conn.execute("SELECT * FROM business_os_supplier_sync_jobs WHERE available_at<=? "
             "AND lease_until<=? ORDER BY available_at,id LIMIT 1", (now, now)).fetchone()
        if row is None:
            webhook_inbox._commit(conn)
            return None
        result = dict(row)
        cursor = conn.execute("UPDATE business_os_supplier_sync_jobs SET lease_token=?,lease_until=? "
             "WHERE id=? AND lease_until<=?", (token, now + 120, result["id"], now))
        webhook_inbox._commit(conn)
        if cursor.rowcount != 1:
            return None
        result["lease_token"] = token
        return result
    except Exception:
        webhook_inbox._rollback(conn)
        raise
    finally:
        conn.close()


def _finish(job, *, now, value=None, retry_after=None):
    success = retry_after is None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str) if success else ""
    # No raw provider payload, customer address or credential is placed in job evidence.
    digest = hashlib.sha256(encoded.encode()).hexdigest() if success else None
    conn = db.connect()
    try:
        cursor = conn.execute("UPDATE business_os_supplier_sync_jobs SET lease_token=NULL,lease_until=0,"
             "available_at=?,failures=?,last_verified_at=COALESCE(?,last_verified_at),"
             "evidence_hash=COALESCE(?,evidence_hash),last_error=? WHERE id=? AND lease_token=?",
             (now + (CADENCE[job["kind"]] if success else retry_after),
              0 if success else job["failures"] + 1, now if success else None,
              digest, None if success else "provider_read_deferred", job["id"], job["lease_token"]))
        if success and cursor.rowcount == 1 and job["kind"] in {"product", "inventory", "tracking"}:
            # Only normalized adapter values. Never persist raw HTTP bodies.
            conn.execute("INSERT INTO supplier_snapshots (snapshot_id,connection_id,business_id,store_id,kind,resource_id,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, job["connection_id"], job["business_id"], job["store_id"], job["kind"], job["resource_id"], encoded, now))
        conn.commit()
    finally:
        conn.close()


def _adapter(bundle, adapter_factory):
    if adapter_factory:
        return adapter_factory(bundle)
    from . import connections
    meta = bundle["connection"]
    return connections.worker_adapter(meta["id"], meta["business_id"], meta["store_id"])


def _read_job(job, adapter, meta):
    kind, resource = job["kind"], job["resource_id"]
    if kind == "health":
        return adapter.connection_health(shop_id=meta["external_shop_id"])
    if kind == "shops":
        shops = adapter.get_shops()
        rows = shops.get("shops", []) if isinstance(shops, dict) else shops
        if not any(str(s.get("shop_id")) == str(meta["external_shop_id"]) and s.get("status") == 1 for s in rows):
            raise fulfillment.FulfillmentError("bound_shop_unverified")
        return {"bound_shop_verified": True}
    if kind == "subscriptions":
        # Read one bounded page only; absence is not interpreted as unsubscribe.
        return adapter.get_subscriptions(meta["external_shop_id"], page=1, size=20)
    if kind == "product":
        return adapter.get_product(resource)
    if kind == "inventory":
        return adapter.get_inventory(resource)
    if kind == "order":
        # Inbox-derived IDs are bound against local intents before scheduling.
        observed = adapter.get_fulfillment(order_id=resource)
        fulfillment.observe_linked(meta, resource, observed)
        return observed
    if kind == "tracking":
        return adapter.get_tracking(resource)
    raise fulfillment.FulfillmentError("invalid_sync_kind")


def _seed_jobs(limit, now):
    """Only persisted selected resources; never scan CJ's catalogue."""
    from . import gateway
    gateway.ensure_schema()
    conn = db.connect()
    try:
        accounts = conn.execute("SELECT c.id,c.business_id,c.store_id FROM business_os_supplier_connections c WHERE c.provider='CJ' AND NOT EXISTS (SELECT 1 FROM business_os_supplier_sync_jobs j WHERE j.connection_id=c.id AND j.kind='health') ORDER BY c.id LIMIT ?", (limit,)).fetchall()
        for account in accounts:
            for kind in ("health", "shops", "subscriptions"):
                schedule(connection_id=account["id"], business_id=account["business_id"], store_id=account["store_id"], kind=kind, now=now)
        links = conn.execute("SELECT DISTINCT l.connection_id,l.business_id,l.store_id,l.pid FROM supplier_product_links l WHERE NOT EXISTS (SELECT 1 FROM business_os_supplier_sync_jobs j WHERE j.connection_id=l.connection_id AND j.kind='inventory' AND j.resource_id=l.pid) ORDER BY l.connection_id,l.pid LIMIT ?", (limit,)).fetchall()
        for link in links:
            for kind in ("product", "inventory"):
                schedule(connection_id=link["connection_id"], business_id=link["business_id"], store_id=link["store_id"], kind=kind, resource_id=link["pid"], now=now)
        linked = conn.execute("SELECT i.connection_id,i.business_id,i.store_id,o.provider_order_id FROM business_os_supplier_intents i JOIN business_os_supplier_outbox o ON i.id=o.intent_id WHERE o.state='LINKED' AND NOT EXISTS (SELECT 1 FROM business_os_supplier_sync_jobs j WHERE j.connection_id=i.connection_id AND j.kind='tracking' AND j.resource_id=o.provider_order_id) ORDER BY o.updated_at LIMIT ?", (limit,)).fetchall()
        for intent in linked:
            for kind in ("order", "tracking"):
                schedule(connection_id=intent["connection_id"], business_id=intent["business_id"], store_id=intent["store_id"], kind=kind, resource_id=intent["provider_order_id"], now=now)
    finally:
        conn.close()


def run_once(*, adapter_factory=None, limit=20, now=None):
    """A bounded worker tick; return counts only, no credentials or provider bodies."""
    policy.require_enabled()
    policy.require_network()
    limit = max(1, min(int(limit), 50))
    now = time.time() if now is None else now
    from . import connections, webhooks
    ensure_schema()
    fulfillment.ensure_schema()
    webhook_inbox.ensure_schema()
    _seed_jobs(limit, now)
    # Existing inbox handler only performs idempotent scheduling; no financial write.
    inbox = webhook_inbox.reconcile_pending(webhooks.mark_dirty, provider="cj", limit=limit)
    counts = {"intents": 0, "reads": 0, "deferred": 0, "inbox": inbox["examined"]}
    for _ in range(limit):
        intent = fulfillment.claim(now=now)
        if intent is None:
            break
        try:
            bundle = connections.worker_connection(intent["connection_id"], intent["business_id"], intent["store_id"])
            adapter = _adapter(bundle, adapter_factory)
            adapter.background = False
            # Hydration may refresh tokens and repair stale connection status.
            bundle = connections.worker_connection(intent["connection_id"], intent["business_id"], intent["store_id"])
            state = fulfillment.dispatch(intent, adapter, bundle["connection"], now=now)
            if state in {"READY", "UNKNOWN", "BLOCKED"}:
                counts["deferred"] += 1
        except Exception:
            # An unknown write stays unknown even if credentials become unavailable.
            state = "UNKNOWN" if intent["state"] in {"UNKNOWN", "RECONCILE"} else "READY"
            fulfillment.settle(intent, state, now=now, delay=300, error="connection_unavailable")
            counts["deferred"] += 1
        counts["intents"] += 1
    for _ in range(limit - counts["intents"]):
        job = _claim(now)
        if job is None:
            break
        adapter = None
        try:
            bundle = connections.worker_connection(job["connection_id"], job["business_id"], job["store_id"])
            adapter = _adapter(bundle, adapter_factory)
            bundle = connections.worker_connection(job["connection_id"], job["business_id"], job["store_id"])
            adapter.background = True
            value = _read_job(job, adapter, bundle["connection"])
            _finish(job, now=now, value=value)
            connections.record_activity(job["connection_id"], job["business_id"], job["store_id"], adapter=adapter, synced=True)
        except Exception as exc:
            try:
                connections.record_activity(job["connection_id"], job["business_id"], job["store_id"], adapter=adapter, error=exc)
            except Exception:
                pass  # Failure evidence must not reveal credentials or mask retry.
            advised = getattr(exc, "retry_after", None)
            delay = max(30, float(advised)) if advised else min(3600, 30 * 2 ** min(job["failures"], 7))
            _finish(job, now=now, retry_after=delay)
            counts["deferred"] += 1
        counts["reads"] += 1
    return counts
