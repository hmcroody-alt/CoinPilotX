"""Exact raw-byte HMAC plus real durable inbox/tenant binding regression tests."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from services import db
from services.business_os.payments import webhook_inbox
from services.business_os.suppliers import webhooks as w
from tests.business_os.test_cj_connections import database, SECRETS
from tests.business_os.test_cj_fulfillment import ready, PID, VID, attempt

OFFICIAL_BODY = b'{"messageId":"123111","messageType":"INSERT","params":"123","type":"PRODUCT"}'
OFFICIAL_SIGNATURE = "AHxoGFMoS/4mZfJ5vFes5//Pz2QibFQhh3GlrTtnWpk="


def event(**overrides):
    value = {"messageId": "fixture-1", "messageType": "UPDATE", "type": "PRODUCT", "openId": SECRETS["open_id"], "params": {"pid": PID}}
    value.update(overrides)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def receive(ready, raw=None, key=None):
    raw = event() if raw is None else raw
    return w.receive(ready[1]["id"], raw, [w.signature(key or SECRETS["open_id"], raw)])


def test_official_known_answer_matches_raw_bytes():
    assert w.signature("123", OFFICIAL_BODY) == OFFICIAL_SIGNATURE
    w.verify("123", OFFICIAL_BODY, [OFFICIAL_SIGNATURE])


@pytest.mark.parametrize("headers", [[], [OFFICIAL_SIGNATURE, OFFICIAL_SIGNATURE], ["bad"], [OFFICIAL_SIGNATURE[:-1]], [" " + OFFICIAL_SIGNATURE]])
def test_noncanonical_or_duplicate_header_rejected(headers):
    with pytest.raises(w.WebhookError):
        w.verify("123", OFFICIAL_BODY, headers)


def test_signature_is_not_reserialized_or_normalized():
    changed = json.dumps(json.loads(OFFICIAL_BODY)).encode()
    with pytest.raises(w.WebhookError):
        w.verify("123", changed, [OFFICIAL_SIGNATURE])
    utf8 = '{"text":"café"}'.encode()
    assert w.signature("123", utf8) != w.signature("123", b'{"text":"caf\\u00e9"}')


def test_spoof_rejected_before_persistence(ready):
    with pytest.raises(w.WebhookError):
        receive(ready, key="another-merchant-openid")
    webhook_inbox.ensure_schema()
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM provider_webhook_events").fetchone()[0] == 0
    conn.close()


def test_valid_receipt_durable_redacted_deduplicated(ready):
    assert receive(ready) == {"accepted": True, "duplicate": False}
    assert receive(ready) == {"accepted": True, "duplicate": True}
    conn = db.connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM provider_webhook_events")]
    conn.close()
    assert len(rows) == 1 and rows[0]["status"] == "received"
    assert rows[0]["signature_verified"] == 1
    assert all(v not in json.dumps(rows) for v in SECRETS.values())
    assert "raw_body_sha256" in json.loads(rows[0]["payload_json"])
    result = webhook_inbox.reconcile_pending(w.mark_dirty, provider="cj", limit=1)
    assert result["processed"] == 1
    conn = db.connect()
    job = conn.execute("SELECT * FROM business_os_supplier_sync_jobs").fetchone()
    assert job["kind"] == "product" and job["resource_id"] == PID
    conn.close()


def test_concurrent_duplicates_create_one_durable_receipt(ready):
    webhook_inbox.ensure_schema()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: receive(ready), range(2)))
    assert sum(not r["duplicate"] for r in result) == 1


def test_same_event_identity_different_raw_body_is_conflict(ready):
    receive(ready)
    with pytest.raises(w.WebhookError, match="conflicting"):
        receive(ready, event(params={"pid": PID, "productNameEn": "changed"}))


@pytest.mark.parametrize("change", [{"openId": "wrong-account"}, {"params": {"pid": "another-product"}},
    {"type": "VARIANT", "params": {"vid": "another-variant"}}, {"type": "STOCK", "params": {"another-vid": []}},
    {"type": "ORDER", "params": {"cjOrderId": "another-order"}}, {"messageId": ""}])
def test_authenticated_cross_account_or_unselected_resource_denied(ready, change):
    with pytest.raises(w.WebhookError):
        receive(ready, event(**change))


def test_unknown_connection_does_not_reveal_existence(ready):
    with pytest.raises(w.WebhookError) as failure:
        w.receive("unknown", event(), [w.signature(SECRETS["open_id"], event())])
    assert failure.value.http_status == 403


def test_duplicate_json_keys_rejected_even_with_valid_signature(ready):
    raw = event().replace(b'"messageId":"fixture-1"', b'"messageId":"fixture-1","messageId":"fixture-2"')
    with pytest.raises(w.WebhookError):
        receive(ready, raw)


def test_delayed_and_out_of_order_events_only_schedule_readback(ready):
    receive(ready, event(messageId="new-event", params={"pid": PID, "status": "DELETED"}, updatedAt=2000))
    receive(ready, event(messageId="old-event", params={"pid": PID, "status": "ACTIVE"}, updatedAt=1000))
    webhook_inbox.reconcile_pending(w.mark_dirty, provider="cj", limit=10)
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM business_os_supplier_sync_jobs").fetchone()[0] == 1
    assert conn.execute("SELECT title FROM business_os_mkt_products WHERE product_id='product-a'").fetchone()[0] == "Retail owned title"
    conn.close()


def test_shared_inbox_never_records_arbitrary_exception_text(ready, monkeypatch):
    receive(ready)
    from services.business_os.suppliers import worker
    def explode(**kwargs):
        raise RuntimeError(SECRETS["access_token"])
    monkeypatch.setattr(worker, "schedule", explode)
    webhook_inbox.reconcile_pending(w.mark_dirty, provider="cj", limit=1)
    conn = db.connect()
    error = conn.execute("SELECT last_error FROM provider_webhook_events").fetchone()[0]
    conn.close()
    assert error == "supplier_reconciliation_deferred" and SECRETS["access_token"] not in error
