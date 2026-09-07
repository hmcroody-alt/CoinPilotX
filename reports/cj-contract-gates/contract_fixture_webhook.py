"""LOCAL SYNTHETIC contract proof, not a CJ receiver or adapter.

No network, application imports, credentials, persistent store, or money actions.
Only the first known-answer vector comes from CJ's official public example:
https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication
All other account, route, order, event and signature values are synthetic.
The in-memory inbox proves decision rules, NOT durability or concurrency safety.
"""

import base64
import hashlib
import hmac
import json
import unittest
from dataclasses import dataclass


OFFICIAL_BODY = b'{"messageId":"123111","messageType":"INSERT","params":"123","type":"PRODUCT"}'
OFFICIAL_SIGNATURE = "AHxoGFMoS/4mZfJ5vFes5//Pz2QibFQhh3GlrTtnWpk="


class Rejected(ValueError):
    pass


class Quarantined(ValueError):
    pass


def signature(saved_open_id, raw_body):
    if not isinstance(saved_open_id, str) or not isinstance(raw_body, bytes):
        raise TypeError("Use stored lossless openId string and untouched bytes")
    mac = hmac.new(saved_open_id.encode("utf-8"), raw_body, hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")


def verify(saved_open_id, raw_body, sign_headers):
    # A real HTTP adapter must collect header names case-insensitively, but reject
    # multiple values rather than pick an arbitrary duplicate header.
    if len(sign_headers) != 1 or not isinstance(sign_headers[0], str):
        raise Rejected("Exactly one signature required")
    supplied = sign_headers[0]
    if not supplied.isascii() or len(supplied) != 44:
        raise Rejected("Noncanonical signature")
    if not hmac.compare_digest(signature(saved_open_id, raw_body), supplied):
        raise Rejected("Signature mismatch")


def reject_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise Rejected("Ambiguous JSON keys")
        obj[key] = value
    return obj


@dataclass(frozen=True)
class Connection:
    environment: str
    merchant: str
    connection: str
    lineage: str
    open_id: str
    orders: frozenset


class SyntheticInbox:
    """Order-only toy for tenant binding/dedup; never applies provider state."""

    def __init__(self, routes):
        self.routes = routes
        self.receipts = {}
        self.dirty_orders = set()

    def receive(self, route, raw_body, sign_headers):
        if route not in self.routes:
            raise Rejected("Unknown route")
        connection = self.routes[route]  # Never selected from payload identity.
        if not isinstance(raw_body, bytes) or len(raw_body) > 65536:
            raise Rejected("Synthetic fixture body bound")
        verify(connection.open_id, raw_body, sign_headers)
        try:
            event = json.loads(raw_body.decode("utf-8"),
                               object_pairs_hook=reject_duplicate_keys)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise Rejected("Invalid JSON") from error
        if not isinstance(event, dict):
            raise Rejected("Invalid envelope")
        if "openId" in event:
            supplied_id = event["openId"]
            if type(supplied_id) not in (str, int):
                raise Rejected("Lossy account identity")
            if str(supplied_id) != connection.open_id:
                raise Rejected("Body account mismatch")
        if event.get("type") != "ORDER":
            raise Quarantined("Topic not enabled in this order-only fixture")
        if event.get("messageType") != "UPDATE":
            raise Quarantined("Unsupported fixture message type")
        event_id = event.get("messageId")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
            raise Quarantined("Missing or invalid event identity")
        params = event.get("params")
        if not isinstance(params, dict):
            raise Quarantined("Invalid order params")
        order_id = params.get("cjOrderId")
        if order_id not in connection.orders:
            raise Rejected("Unowned order")
        key = (connection.environment, connection.merchant,
               connection.connection, connection.lineage, event["type"], event_id)
        digest = hashlib.sha256(raw_body).hexdigest()
        if key in self.receipts:
            if self.receipts[key] != digest:
                raise Quarantined("Same event identity with different raw digest")
            return "DUPLICATE"
        self.receipts[key] = digest
        # No financial/canonical status effects; later authoritative read needed.
        self.dirty_orders.add((connection.connection, order_id))
        return "ACCEPTED_DIRTY_ONLY"


def order_body(open_id="90000000000000001", order_id="synthetic-order-a",
               event_id="synthetic-event-1", status="PAID"):
    return json.dumps({"messageId": event_id, "messageType": "UPDATE",
                       "openId": open_id, "params": {"cjOrderId": order_id,
                       "orderStatus": status}, "type": "ORDER"},
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class WebhookContractProof(unittest.TestCase):
    def setUp(self):
        self.a = Connection("SYNTHETIC", "merchant-a", "connection-a", "lineage-a",
                            "90000000000000001", frozenset({"synthetic-order-a"}))
        self.b = Connection("SYNTHETIC", "merchant-b", "connection-b", "lineage-b",
                            "90000000000000002", frozenset({"synthetic-order-b"}))
        self.inbox = SyntheticInbox({"opaque-a": self.a, "opaque-b": self.b})

    def deliver(self, raw=None, route="opaque-a", key=None):
        body = order_body() if raw is None else raw
        return self.inbox.receive(route, body, [signature(key or self.a.open_id, body)])

    def test_official_known_answer(self):
        self.assertEqual(signature("123", OFFICIAL_BODY), OFFICIAL_SIGNATURE)
        verify("123", OFFICIAL_BODY, [OFFICIAL_SIGNATURE])

    def test_raw_whitespace_cannot_reuse_signature(self):
        changed = OFFICIAL_BODY.replace(b'"messageType":', b'"messageType": ')
        with self.assertRaises(Rejected):
            verify("123", changed, [OFFICIAL_SIGNATURE])
        verify("123", changed, [signature("123", changed)])

    def test_reserialization_cannot_reuse_signature(self):
        changed = json.dumps(json.loads(OFFICIAL_BODY)).encode()
        with self.assertRaises(Rejected):
            verify("123", changed, [OFFICIAL_SIGNATURE])

    def test_utf8_raw_body(self):
        raw = b'{"value":"caf\xc3\xa9"}'
        verify("123", raw, [signature("123", raw)])
        self.assertNotEqual(signature("123", raw), signature("123", b'{"value":"caf\\u00e9"}'))

    def test_modified_payload_rejected(self):
        raw = order_body()
        with self.assertRaises(Rejected):
            self.inbox.receive("opaque-a", raw.replace(b"PAID", b"SHIPPED"),
                               [signature(self.a.open_id, raw)])

    def test_noncanonical_and_duplicate_headers_rejected(self):
        for headers in ([], [OFFICIAL_SIGNATURE] * 2, [OFFICIAL_SIGNATURE.rstrip("=")],
                        [OFFICIAL_SIGNATURE.replace("/", "_")], [" " + OFFICIAL_SIGNATURE],
                        ["\u00e9" * 44]):
            with self.subTest(headers=headers), self.assertRaises(Rejected):
                verify("123", OFFICIAL_BODY, headers)

    def test_merchant_b_signature_cannot_use_merchant_a_route(self):
        raw = order_body(open_id=self.b.open_id, order_id="synthetic-order-b")
        with self.assertRaises(Rejected):
            self.deliver(raw, key=self.b.open_id)

    def test_valid_a_signature_with_body_b_identity_rejected(self):
        with self.assertRaises(Rejected):
            self.deliver(order_body(open_id=self.b.open_id))

    def test_valid_a_signature_cannot_target_b_order(self):
        with self.assertRaises(Rejected):
            self.deliver(order_body(order_id="synthetic-order-b"))

    def test_identical_message_ids_do_not_cross_tenants(self):
        self.assertEqual(self.deliver(), "ACCEPTED_DIRTY_ONLY")
        raw = order_body(open_id=self.b.open_id, order_id="synthetic-order-b")
        self.assertEqual(self.deliver(raw, "opaque-b", self.b.open_id), "ACCEPTED_DIRTY_ONLY")
        self.assertEqual(len(self.inbox.receipts), 2)

    def test_replay_has_no_second_effect(self):
        self.assertEqual(self.deliver(), "ACCEPTED_DIRTY_ONLY")
        self.assertEqual(self.deliver(), "DUPLICATE")
        self.assertEqual(len(self.inbox.receipts), 1)
        self.assertEqual(len(self.inbox.dirty_orders), 1)

    def test_same_id_different_signed_digest_quarantined(self):
        self.deliver()
        with self.assertRaises(Quarantined):
            self.deliver(order_body(status="SHIPPED"))
        self.assertEqual(len(self.inbox.receipts), 1)

    def test_missing_message_id_quarantined(self):
        with self.assertRaises(Quarantined):
            self.deliver(order_body(event_id=""))

    def test_signed_duplicate_json_keys_rejected(self):
        raw = order_body().replace(b'"type":"ORDER"', b'"type":"ORDER","type":"ORDER"')
        with self.assertRaises(Rejected):
            self.deliver(raw)

    def test_shared_account_key_still_requires_order_ownership(self):
        b_shared = Connection("SYNTHETIC", "merchant-b", "connection-b", "lineage-b",
                              self.a.open_id, frozenset({"synthetic-order-b"}))
        self.inbox.routes["opaque-b"] = b_shared
        with self.assertRaises(Rejected):
            self.deliver(order_body(), "opaque-b")

    def test_no_freshness_guarantee_is_invented(self):
        # An old but unseen valid payload cannot be aged from this protocol.
        raw = order_body(event_id="synthetic-unseen-old-id", status="CREATED")
        self.assertEqual(self.deliver(raw), "ACCEPTED_DIRTY_ONLY")
        self.assertEqual(self.inbox.dirty_orders, {("connection-a", "synthetic-order-a")})
        self.assertFalse(hasattr(self.inbox, "payments"))


if __name__ == "__main__":
    print("LOCAL SYNTHETIC ONLY; no CJ runtime, delivery, persistence or payment proof.")
    unittest.main(verbosity=2)
