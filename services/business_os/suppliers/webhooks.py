"""CJ raw-body HMAC receiver; authenticated hints never change commerce state."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from services import db
from services.business_os.payments import webhook_inbox
from . import fulfillment, policy


class WebhookError(ValueError):
    def __init__(self, code="invalid_webhook", http_status=403):
        self.code, self.http_status = code, http_status
        super().__init__(code)


def signature(open_id, raw_body):
    if not isinstance(open_id, str) or not open_id or not isinstance(raw_body, bytes):
        raise WebhookError()
    return base64.b64encode(hmac.new(open_id.encode("utf-8"), raw_body, hashlib.sha256).digest()).decode("ascii")


def verify(open_id, raw_body, sign_headers):
    if not isinstance(sign_headers, (list, tuple)) or len(sign_headers) != 1:
        raise WebhookError()
    supplied = sign_headers[0]
    if (not isinstance(supplied, str) or not supplied.isascii() or len(supplied) != 44
            or not hmac.compare_digest(signature(open_id, raw_body), supplied)):
        raise WebhookError()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WebhookError()
        result[key] = value
    return result


def _targets(meta, event):
    """Bind an authenticated event to already selected products or local orders."""
    kind, params = event.get("type"), event.get("params")
    if not isinstance(params, dict):
        raise WebhookError("unsupported_webhook", 422)
    conn = db.connect()
    try:
        if kind in {"ORDER", "LOGISTIC"}:
            ref = params.get("cjOrderId") if kind == "ORDER" else params.get("orderId")
            if not isinstance(ref, str) or not ref or len(ref) > 200:
                raise WebhookError()
            rows = conn.execute("SELECT i.id,i.external_order_ref,o.provider_order_id FROM business_os_supplier_intents i "
                "JOIN business_os_supplier_outbox o ON i.id=o.intent_id WHERE i.connection_id=? "
                "AND i.business_id=? AND i.store_id=? AND i.external_account_id=? "
                "AND (o.provider_order_id=? OR i.external_order_ref=?)",
                (meta["id"], meta["business_id"], meta["store_id"], meta["external_account_id"],
                 ref, str(params.get("orderNumber") or ""))).fetchall()
            if len(rows) != 1:
                raise WebhookError()
            row = dict(rows[0])
            if row["provider_order_id"] and row["provider_order_id"] != ref:
                raise WebhookError()
            # Never trust an event to establish a provider order ID after UNKNOWN.
            return [{"kind": "intent", "resource_id": row["id"]}]
        query = "SELECT DISTINCT pid,vid FROM supplier_product_links WHERE connection_id=? AND business_id=? AND store_id=?"
        args = [meta["id"], meta["business_id"], meta["store_id"]]
        if kind == "PRODUCT":
            query += " AND pid=?"
            args.append(params.get("pid"))
        elif kind == "VARIANT":
            query += " AND vid=?"
            args.append(params.get("vid"))
        elif kind == "STOCK" and 1 <= len(params) <= 100:
            query += " AND vid IN (" + ",".join("?" for _ in params) + ")"
            args.extend(params)
        else:
            raise WebhookError("unsupported_webhook", 422)
        query += " LIMIT 101"
        links = [dict(r) for r in conn.execute(query, tuple(args)).fetchall()]
        if kind == "PRODUCT":
            selected = {r["pid"] for r in links if r["pid"] == params.get("pid")}
        elif kind == "VARIANT":
            selected = {r["pid"] for r in links if r["vid"] == params.get("vid")}
        elif kind == "STOCK":
            if not 1 <= len(params) <= 100:
                raise WebhookError("unsupported_webhook", 422)
            if not set(params).issubset({r["vid"] for r in links}):
                raise WebhookError()
            selected = {r["pid"] for r in links if r["vid"] in params}
        else:
            raise WebhookError("unsupported_webhook", 422)
        if not selected or len(selected) > 100:
            raise WebhookError()
        return [{"kind": "inventory" if kind == "STOCK" else "product", "resource_id": pid}
                for pid in sorted(selected)]
    finally:
        conn.close()


def receive(connection_id, raw_body, sign_headers):
    """Route by opaque saved connection, verify exact bytes before any persistence.

    Raw CJ bodies may contain openId, itself the signing secret. The ordinary
    durable inbox receives only an allowlisted routing hint + exact-body hash.
    This intentionally does NOT retain reconstructible provider-body evidence.
    """
    policy.require_enabled()
    if not isinstance(raw_body, bytes) or not 0 < len(raw_body) <= 65536:
        raise WebhookError()
    from . import connections
    try:
        secret = connections.internal_webhook_secret(connection_id)
    except Exception:
        raise WebhookError() from None
    meta, open_id = secret["connection"], secret["open_id"]
    verify(open_id, raw_body, sign_headers)
    try:
        event = json.loads(raw_body.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError):
        raise WebhookError() from None
    if not isinstance(event, dict):
        raise WebhookError()
    if "openId" in event and (type(event["openId"]) not in (str, int) or str(event["openId"]) != open_id):
        raise WebhookError()
    message_id = event.get("messageId")
    if not isinstance(message_id, str) or not 1 <= len(message_id) <= 200:
        raise WebhookError()
    if event.get("messageType") not in {"INSERT", "UPDATE", "DELETE", "ORDER_CONNNECTED"}:
        raise WebhookError("unsupported_webhook", 422)
    fulfillment.ensure_schema()
    targets = _targets(meta, event)
    key = ["SANDBOX", meta["id"], meta["external_account_id"], event.get("type"), message_id]
    event_id = hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest()
    digest = hashlib.sha256(raw_body).hexdigest()
    payload = {"connection_id": meta["id"], "business_id": meta["business_id"],
               "store_id": meta["store_id"], "account_ref": meta["external_account_id"],
               "raw_body_sha256": digest, "targets": targets}
    webhook_inbox.ensure_schema()
    row = webhook_inbox.enqueue_event(provider="cj", provider_event_id=event_id,
        event_type=event["type"], payload=payload, signature_verified=True)
    if row.get("duplicate"):
        previous = json.loads(row["payload_json"])
        if previous.get("raw_body_sha256") != digest:
            raise WebhookError("conflicting_webhook_replay", 409)
    return {"accepted": True, "duplicate": bool(row.get("duplicate"))}


def mark_dirty(payload):
    try:
        _mark_dirty(payload)
    except Exception:
        # The shared inbox records str(exc); do not let arbitrary DB/transport
        # diagnostics cross from this supplier handler into that general store.
        raise WebhookError("supplier_reconciliation_deferred", 503) from None


def _mark_dirty(payload):
    """Idempotent async consumer; never apply webhook status or money directly."""
    from . import connections, worker
    bundle = connections.worker_connection(payload["connection_id"], payload["business_id"], payload["store_id"])
    if bundle["connection"]["external_account_id"] != payload["account_ref"]:
        raise WebhookError()
    for target in payload["targets"]:
        if target["kind"] == "intent":
            conn = db.connect()
            try:
                conn.execute("UPDATE business_os_supplier_outbox SET state='RECONCILE',available_at=0 "
                    "WHERE intent_id IN (SELECT id FROM business_os_supplier_intents WHERE id=? AND connection_id=?) "
                    "AND state='LINKED'", (target["resource_id"], payload["connection_id"]))
                conn.commit()
            finally:
                conn.close()
        else:
            worker.schedule(connection_id=payload["connection_id"], business_id=payload["business_id"],
                            store_id=payload["store_id"], kind=target["kind"], resource_id=target["resource_id"], dirty=True)
