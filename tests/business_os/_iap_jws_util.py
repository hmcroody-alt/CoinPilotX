"""Test-only helpers: build an EC certificate chain and sign Apple-style JWS.

Generates a self-signed root, an intermediate signed by the root, and a leaf
signed by the intermediate (all P-256/ES256), then mints JWS tokens signed by the
leaf with the x5c chain in the JOSE header — exactly the shape Apple produces.
Because the trust anchor is injected, the production verifier accepts these tokens
when handed our root as the anchor, and rejects them otherwise. No network, no
Apple secrets — the whole IAP verification path is provable offline.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.serialization import Encoding


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _mk_cert(subject_cn, subject_key, issuer_cn, issuer_key, *,
             is_ca=True, not_before=None, not_after=None):
    now = datetime.now(timezone.utc)
    na = not_after or (now + timedelta(days=3650))
    # default not_before to a day before now, but never after not_after (so an
    # intentionally-expired cert still has a valid before<after ordering)
    nb = not_before or min(now - timedelta(days=1), na - timedelta(days=1))
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject_cn))
        .issuer_name(_name(issuer_cn))
        .public_key(subject_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb.replace(tzinfo=None))
        .not_valid_after(na.replace(tzinfo=None))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), critical=True)
    )
    return builder.sign(private_key=issuer_key, algorithm=hashes.SHA256())


class Chain:
    """A generated root/intermediate/leaf EC chain plus JWS signing helpers."""

    def __init__(self, *, leaf_not_after=None):
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.int_key = ec.generate_private_key(ec.SECP256R1())
        self.leaf_key = ec.generate_private_key(ec.SECP256R1())

        self.root = _mk_cert("Test Root CA", self.root_key,
                             "Test Root CA", self.root_key)
        self.intermediate = _mk_cert("Test Intermediate", self.int_key,
                                     "Test Root CA", self.root_key)
        self.leaf = _mk_cert("Test Leaf", self.leaf_key,
                             "Test Intermediate", self.int_key,
                             is_ca=False, not_after=leaf_not_after)

    def root_der(self) -> bytes:
        return self.root.public_bytes(Encoding.DER)

    def x5c(self) -> list[str]:
        return [base64.b64encode(c.public_bytes(Encoding.DER)).decode("ascii")
                for c in (self.leaf, self.intermediate, self.root)]

    def sign_jws(self, payload: dict, *, sign_key=None, x5c=None,
                 alg="ES256") -> str:
        """Produce an ES256 JWS (compact) with x5c header, R||S signature."""
        header = {"alg": alg, "x5c": x5c if x5c is not None else self.x5c()}
        h = _b64url(json.dumps(header, separators=(",", ":")).encode())
        p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = f"{h}.{p}".encode("ascii")
        key = sign_key or self.leaf_key
        der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_sig)
        raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return f"{h}.{p}.{_b64url(raw)}"


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def build_notification(chain: Chain, *, notification_type="SUBSCRIBED",
                       subtype=None, product_id="com.pulsesoc.premium.monthly",
                       app_account_token="42",
                       original_transaction_id="1000000123456789",
                       expires_dt=None, auto_renew_status=1,
                       original_purchase_dt=None,
                       include_renewal_info=True) -> str:
    """Build a full App Store Server Notification v2 signedPayload (nested JWS).

    ``auto_renew_status`` and ``include_renewal_info`` are deliberately separate
    knobs. Apple omits ``signedRenewalInfo`` from some notifications entirely, so
    "auto-renew is off" and "this notification is silent about auto-renew" have
    to stay distinguishable here — the adapter treats them very differently, and
    conflating them is how a paying member gets told their plan is ending.
    """
    expires_dt = expires_dt or (datetime.now(timezone.utc) + timedelta(days=30))
    txn = {
        "transactionId": original_transaction_id,
        "originalTransactionId": original_transaction_id,
        "productId": product_id,
        "appAccountToken": app_account_token,
        "expiresDate": ms(expires_dt),
        "type": "Auto-Renewable Subscription",
    }
    if original_purchase_dt is not None:
        txn["originalPurchaseDate"] = ms(original_purchase_dt)
    renewal = {
        "autoRenewProductId": product_id,
        "autoRenewStatus": auto_renew_status,
        "originalTransactionId": original_transaction_id,
    }
    signed_txn = chain.sign_jws(txn)
    payload = {
        "notificationType": notification_type,
        "notificationUUID": "uuid-" + original_transaction_id,
        "version": "2.0",
        "signedDate": ms(datetime.now(timezone.utc)),
        "data": {
            "bundleId": "com.pulsesoc.app",
            "environment": "Sandbox",
            "appAppleId": 111,
            "signedTransactionInfo": signed_txn,
        },
    }
    if include_renewal_info:
        payload["data"]["signedRenewalInfo"] = chain.sign_jws(renewal)
    if subtype is not None:
        payload["subtype"] = subtype
    return chain.sign_jws(payload)
