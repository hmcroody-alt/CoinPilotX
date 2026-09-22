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


def schedule(*, connection_id, business_id, store_id, kind, resource_id="", now=None, dirty=False,
             conn=None):
    """Internal-only; callers must establish ownership before scheduling IDs.

    ``conn`` lets a caller enqueueing many resources at once do it on one
    connection. Without it, a merchant asking to refresh a catalogue of two
    hundred products opens four hundred connections against a pool of eight, and
    the request that was meant to help them times out instead. The caller that
    passes a connection owns the commit, so the whole batch lands or none of it
    does. ``ensure_schema`` is still called on its own connection either way --
    running that DDL inside a borrowed transaction is what leaves it uncommitted
    and blocks the next connection on the lock it took.
    """
    if kind not in KINDS or not isinstance(resource_id, str) or len(resource_id) > 200:
        raise fulfillment.FulfillmentError("invalid_sync_resource", 400)
    if kind in {"product", "inventory", "order", "tracking"} and not resource_id:
        raise fulfillment.FulfillmentError("missing_sync_resource", 400)
    now = time.time() if now is None else now
    owned = conn is None
    if owned:
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
        if owned:
            conn.commit()
    finally:
        if owned:
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
        # A connection with no bound CJ shop has no binding to re-prove, and
        # spending a call to confirm that would burn quota to learn nothing.
        if not meta["external_shop_id"]:
            return {"bound_shop_verified": None, "shop_bound": False}
        shops = adapter.get_shops()
        rows = shops.get("shops", []) if isinstance(shops, dict) else shops
        if not any(str(s.get("shop_id")) == str(meta["external_shop_id"]) and s.get("status") == 1 for s in rows):
            raise fulfillment.FulfillmentError("bound_shop_unverified")
        return {"bound_shop_verified": True}
    if kind == "subscriptions":
        # Product subscriptions are addressed to a CJ shop; without one there is
        # nothing to subscribe on, so this is genuinely not applicable rather
        # than a failure to retry every hour.
        if not meta["external_shop_id"]:
            return {"subscriptions": [], "shop_bound": False}
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


def _apply(job, value, now, counts):
    """Let a completed product/inventory read reach the listings it describes. §23/§24.

    Placed *after* ``_finish``, deliberately. The read succeeded and the evidence
    of that belongs in the job row whatever happens next; scheduling the retry on
    the outcome of the write instead would re-read the supplier — spending quota
    — to fix something that was never a read problem.

    Which is also why every failure here is swallowed. This is a consumer bolted
    onto a scheduler that worked without one for its whole life, and a reconciler
    that can abort a worker tick would take fulfilment down with it. The cost of
    swallowing is one stale listing until the next cadence; the cost of raising
    is a supplier order that never dispatches.
    """
    if job["kind"] not in {"product", "inventory"}:
        return
    try:
        from . import revisions
        result = revisions.apply_supplier_read(
            connection_id=job["connection_id"], business_id=job["business_id"],
            store_id=job["store_id"], kind=job["kind"],
            resource_id=job["resource_id"], payload=value, now=now)
    except Exception:
        counts["revision_failures"] += 1
        return
    counts["revisions"] += result["variants"]


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
        # Seeded from the canonical mapping, not the retired supplier_product_links.
        # ``supplier_connection_id IS NOT NULL`` is what keeps this to provider-backed
        # listings: a merchant-authored source row has no connection to sync against,
        # and scheduling a CJ read for one would be a job that can never succeed.
        links = conn.execute("SELECT DISTINCT l.supplier_connection_id AS connection_id,l.business_id AS business_id,l.store_id AS store_id,l.provider_product_id AS pid FROM marketplace_product_sources l WHERE l.supplier_connection_id IS NOT NULL AND l.business_id IS NOT NULL AND l.store_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM business_os_supplier_sync_jobs j WHERE j.connection_id=l.supplier_connection_id AND j.kind='inventory' AND j.resource_id=l.provider_product_id) ORDER BY connection_id,pid LIMIT ?", (limit,)).fetchall()
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
    # Recorded before any work, and after the policy gates above, so the latch
    # means "a process allowed to drain this outbox got here" -- which is the
    # question the merchant-facing notice asks. Recording it only on success
    # would leave a worker that crashes every tick indistinguishable from no
    # worker at all; `fulfillment.drain_status` separates those two, and it can
    # only do so if this write happens even when the tick below does not finish.
    fulfillment.record_drain_tick(now=now)
    _seed_jobs(limit, now)
    # Existing inbox handler only performs idempotent scheduling; no financial write.
    inbox = webhook_inbox.reconcile_pending(webhooks.mark_dirty, provider="cj", limit=limit)
    counts = {"intents": 0, "reads": 0, "deferred": 0, "inbox": inbox["examined"],
              # Variants whose stock or cost a supplier read actually changed,
              # and reads whose application failed. Reported separately from
              # `reads` because a tick that reads twenty products and revises
              # nothing is healthy, and one that reads twenty and fails to apply
              # twenty is not — and both have `reads: 20`.
              "revisions": 0, "revision_failures": 0}
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
            _apply(job, value, now, counts)
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
    # Only reached when the tick completed. Every per-item failure above is
    # already caught and settled, so arriving here means the loop ran to its
    # bound rather than that nothing went wrong.
    fulfillment.record_drain_tick(now=now, completed=True)
    return counts
