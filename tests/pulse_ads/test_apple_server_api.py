"""App Store Server API client — the pull side of Apple verification.

Pinned behaviors:
  1. A 200 from Apple is NOT trusted by itself — the signedTransactionInfo JWS
     must verify against the injected anchors, and a tampered one raises.
  2. Unconfigured environments degrade to clean errors (503), never crashes,
     and no error message ever contains key material.
  3. The client JWT is a real ES256 JOSE token with Apple's required claims.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "business_os"))
from _iap_jws_util import Chain  # noqa: E402

from services import pulse_apple_server_api as api  # noqa: E402
from services.business_os.entitlements import iap_apple as apple  # noqa: E402


def _b64url_json(segment: str) -> dict:
    pad = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + pad))


class ClientJwtTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "APPLE_IAP_ISSUER_ID": "issuer-uuid-1234",
            "APPLE_IAP_KEY_ID": "ABC123DEFG",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        from cryptography.hazmat.primitives.asymmetric import ec
        self.key = ec.generate_private_key(ec.SECP256R1())

    def test_jwt_has_apple_required_shape_and_verifies(self):
        token = api.build_client_jwt(now=1_700_000_000, private_key=self.key)
        head, claims, sig = token.split(".")
        self.assertEqual(_b64url_json(head), {"alg": "ES256", "kid": "ABC123DEFG", "typ": "JWT"})
        decoded = _b64url_json(claims)
        self.assertEqual(decoded["iss"], "issuer-uuid-1234")
        self.assertEqual(decoded["aud"], "appstoreconnect-v1")
        self.assertEqual(decoded["bid"], "com.pulsesoc.app")
        self.assertEqual(decoded["exp"] - decoded["iat"], 300)
        # signature is raw JOSE (64 bytes), verifiable with the public key
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        self.assertEqual(len(raw), 64)
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        self.key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))

    def test_unconfigured_is_a_clean_503_without_key_material(self):
        with mock.patch.dict(os.environ, {"APPLE_IAP_ISSUER_ID": "", "APPLE_IAP_KEY_ID": ""}):
            with self.assertRaises(api.AppleServerApiError) as ctx:
                api.build_client_jwt(private_key=self.key)
        self.assertEqual(ctx.exception.status, 503)
        self.assertNotIn("BEGIN", str(ctx.exception))

    def test_config_report_is_names_and_presence_only(self):
        with mock.patch.dict(os.environ, {"APPLE_IAP_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----x"}):
            report = api.config_report()
        self.assertEqual(report["APPLE_IAP_PRIVATE_KEY"], "PRESENT")
        self.assertEqual(set(report.values()) - {"PRESENT", "MISSING"}, set())
        self.assertNotIn("BEGIN", json.dumps(report))


class TransactionInfoTests(unittest.TestCase):
    def setUp(self):
        self.chain = Chain()
        self.decoder = lambda token: apple.verify_and_decode_jws(
            token, trust_anchors=[self.chain.root_der()])

    def _payload(self, **overrides):
        payload = {
            "transactionId": "990001",
            "productId": "com.pulsesoc.adcredits.tier2",
            "bundleId": "com.pulsesoc.app",
            "type": "Consumable",
            "quantity": 1,
            "environment": "Production",
            "purchaseDate": 1_700_000_000_000,
        }
        payload.update(overrides)
        return payload

    def test_verified_transaction_round_trip(self):
        signed = self.chain.sign_jws(self._payload())
        transport = lambda url, tok: (200, {"signedTransactionInfo": signed})
        txn = api.get_transaction_info(
            "990001", transport=transport, decode_jws=self.decoder, token="t")
        self.assertEqual(txn["productId"], "com.pulsesoc.adcredits.tier2")

    def test_tampered_body_from_apple_still_raises(self):
        signed = self.chain.sign_jws(self._payload())
        head, body, sig = signed.split(".")
        evil = base64.urlsafe_b64encode(json.dumps(
            self._payload(productId="com.pulsesoc.adcredits.tier5")
        ).encode()).rstrip(b"=").decode()
        transport = lambda url, tok: (200, {"signedTransactionInfo": f"{head}.{evil}.{sig}"})
        with self.assertRaises(apple.AppleJWSError):
            api.get_transaction_info(
                "990001", transport=transport, decode_jws=self.decoder, token="t")

    def test_missing_jws_in_200_is_an_error_not_a_fallback(self):
        transport = lambda url, tok: (200, {"transaction": self._payload()})
        with self.assertRaises(api.AppleServerApiError):
            api.get_transaction_info(
                "990001", transport=transport, decode_jws=self.decoder, token="t")

    def test_http_errors_map_cleanly(self):
        for status, expected in ((404, 404), (401, 502), (500, 502)):
            transport = lambda url, tok, s=status: (s, {})
            with self.assertRaises(api.AppleServerApiError) as ctx:
                api.get_transaction_info(
                    "990001", transport=transport, decode_jws=self.decoder, token="t")
            self.assertEqual(ctx.exception.status, expected)

    def test_environment_selects_base_url(self):
        seen = []
        signed = self.chain.sign_jws(self._payload(environment="Sandbox"))
        transport = lambda url, tok: (seen.append(url), (200, {"signedTransactionInfo": signed}))[1]
        api.get_transaction_info(
            "990001", environment="Sandbox", transport=transport,
            decode_jws=self.decoder, token="t")
        self.assertTrue(seen[0].startswith(api.APPLE_SANDBOX_BASE))

    def test_garbage_transaction_id_is_rejected_before_any_network(self):
        transport = lambda url, tok: (_ for _ in ()).throw(AssertionError("no network expected"))
        with self.assertRaises(api.AppleServerApiError) as ctx:
            api.get_transaction_info(
                "../../etc", transport=transport, decode_jws=self.decoder, token="t")
        self.assertEqual(ctx.exception.status, 400)

    def test_describe_orphan_reports_catalog_facts_but_credits_nothing(self):
        signed = self.chain.sign_jws(self._payload())
        transport = lambda url, tok: (200, {"signedTransactionInfo": signed})
        facts = api.describe_orphan(
            "990001", transport=transport, decode_jws=self.decoder, token="t")
        self.assertTrue(facts["is_ad_credit_product"])
        self.assertEqual(facts["catalog_amount_cents"], 999)
        self.assertNotIn("account_id", facts)  # crediting stays human/owner-gated


if __name__ == "__main__":
    unittest.main()
