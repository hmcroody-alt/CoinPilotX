"""App Store Server API client (pull side of Apple IAP verification).

The push side is complete elsewhere: clients submit StoreKit 2 signed
transactions and Apple POSTs signed notifications, both verified offline
against injected trust anchors (`iap_apple.verify_and_decode_jws`). This
module adds the *pull* direction — asking Apple about a transaction id — which
exists for exactly one job: reconciling orphans. When a notification arrives
for an ad-credit purchase that has no funding session (user bought, app died
before verify), the incident row holds only a transaction id. This client
lets reconciliation fetch the authoritative transaction record for that id.

Security posture, same rules as the rest of the payments mission:

  * The response's ``signedTransactionInfo`` is a JWS and is verified against
    the SAME injected trust anchors as client submissions. TLS alone is not
    trusted; an unverifiable answer is an error, never a fallback decode.
  * Secrets never appear in errors, logs, or return values — configuration
    reporting is name + PRESENT/MISSING only.
  * Nothing in this module writes money. It returns verified facts; crediting
    stays with `pulse_apple_iap_credits` and its DB-unique idempotency key.

Configuration (all owner-provisioned in Railway; missing → clean
``setup_required``, never a crash):

  APPLE_IAP_ISSUER_ID    App Store Connect issuer id (UUID)
  APPLE_IAP_KEY_ID       Key id of the In-App Purchase API key
  APPLE_IAP_PRIVATE_KEY  The .p8 private key — PEM content, or a file path
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Callable, Optional

APPLE_PROD_BASE = "https://api.storekit.itunes.apple.com"
APPLE_SANDBOX_BASE = "https://api.storekit-sandbox.itunes.apple.com"

CONFIG_VARS = ("APPLE_IAP_ISSUER_ID", "APPLE_IAP_KEY_ID", "APPLE_IAP_PRIVATE_KEY")


class AppleServerApiError(Exception):
    """Operational failure. Message is always secret-free."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def config_report() -> dict:
    """Names + PRESENT/MISSING only — safe for logs and admin surfaces."""
    return {name: ("PRESENT" if (os.getenv(name) or "").strip() else "MISSING")
            for name in CONFIG_VARS}


def is_configured() -> bool:
    return all((os.getenv(name) or "").strip() for name in CONFIG_VARS)


def _load_private_key():
    """Accept the .p8 as inline PEM or as a path to a PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = (os.getenv("APPLE_IAP_PRIVATE_KEY") or "").strip()
    if not raw:
        raise AppleServerApiError("App Store Server API is not configured.", 503)
    if "-----BEGIN" not in raw and os.path.isfile(raw):
        with open(raw, "rb") as fh:
            pem = fh.read()
    else:
        pem = raw.replace("\\n", "\n").encode("utf-8")
    try:
        return load_pem_private_key(pem, password=None)
    except Exception as exc:  # noqa: BLE001 - message must stay secret-free
        raise AppleServerApiError("App Store Server API key could not be loaded.", 503) from exc


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_client_jwt(*, now: Optional[int] = None, private_key=None) -> str:
    """ES256 client token per Apple's App Store Server API spec.

    5-minute lifetime (Apple allows up to 60; shorter is strictly safer —
    a leaked token dies fast). ``bid`` is the production bundle id: the API
    key is scoped to the app, and dev-client bundle ids are a StoreKit
    concern, not a server-API one.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    from services import pulse_payment_router as router

    issuer = (os.getenv("APPLE_IAP_ISSUER_ID") or "").strip()
    key_id = (os.getenv("APPLE_IAP_KEY_ID") or "").strip()
    if not issuer or not key_id:
        raise AppleServerApiError("App Store Server API is not configured.", 503)
    key = private_key or _load_private_key()

    issued = int(now if now is not None else time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    claims = {
        "iss": issuer,
        "iat": issued,
        "exp": issued + 300,
        "aud": "appstoreconnect-v1",
        "bid": router.APPLE_BUNDLE_ID,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." +
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    ).encode("ascii")

    der_sig = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der_sig)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")  # JOSE raw, not DER
    return signing_input.decode("ascii") + "." + _b64url(raw_sig)


def _default_transport(url: str, token: str) -> tuple[int, dict]:
    """GET with bearer auth; injectable for tests. 10s timeout — this runs in
    reconciliation, never in a user request path."""
    import requests

    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {}


def get_transaction_info(
    transaction_id: str,
    *,
    environment: str = "Production",
    transport: Optional[Callable[[str, str], tuple[int, dict]]] = None,
    decode_jws: Optional[Callable[[str], dict]] = None,
    token: Optional[str] = None,
) -> dict:
    """Fetch and cryptographically verify one transaction record.

    Returns the decoded, signature-verified transaction payload. Raises
    AppleServerApiError for config/network/HTTP problems and lets
    ``AppleJWSError`` from the verifier propagate — a 200 body whose JWS does
    not verify is an attack surface, not a soft failure.
    """
    transaction_id = str(transaction_id or "").strip()
    if not transaction_id or not transaction_id.replace("_", "").isalnum():
        raise AppleServerApiError("A transaction id is required.", 400)

    if decode_jws is None:
        from services import pulse_apple_iap_credits as credits

        decode_jws = credits.build_default_decoder()
        if decode_jws is None:
            raise AppleServerApiError("Apple IAP trust anchors are not configured.", 503)

    base = APPLE_SANDBOX_BASE if str(environment).strip().lower() == "sandbox" else APPLE_PROD_BASE
    url = f"{base}/inApps/v1/transactions/{transaction_id}"
    send = transport or _default_transport
    bearer = token or build_client_jwt()

    try:
        status, body = send(url, bearer)
    except AppleServerApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppleServerApiError("Apple could not be reached.", 502) from exc

    if status == 404:
        raise AppleServerApiError("Apple has no record of this transaction.", 404)
    if status == 401:
        raise AppleServerApiError("Apple rejected the API credentials.", 502)
    if status != 200:
        raise AppleServerApiError(f"Apple answered HTTP {int(status)}.", 502)

    signed = body.get("signedTransactionInfo")
    if not isinstance(signed, str) or signed.count(".") != 2:
        raise AppleServerApiError("Apple's answer did not contain a signed transaction.", 502)
    return decode_jws(signed)  # AppleJWSError propagates on tamper


def describe_orphan(
    transaction_id: str,
    *,
    environment: str = "Production",
    **kwargs: Any,
) -> dict:
    """Reconciliation helper: verified facts about an orphaned transaction id.

    Deliberately returns facts only (product, quantity, purchase date, revocation)
    — deciding WHICH account to credit requires an authenticated owner and stays
    a human/authenticated-client step, exactly like the original mission rule
    for orphan notifications.
    """
    from services import pulse_payment_router as router

    txn = get_transaction_info(transaction_id, environment=environment, **kwargs)
    product_id = str(txn.get("productId") or "")
    return {
        "transaction_id": str(txn.get("transactionId") or transaction_id),
        "product_id": product_id,
        "is_ad_credit_product": product_id in router.APPLE_ADCREDIT_PRODUCTS,
        "catalog_amount_cents": router.APPLE_ADCREDIT_PRODUCTS.get(product_id),
        "type": str(txn.get("type") or ""),
        "quantity": txn.get("quantity"),
        "environment": str(txn.get("environment") or ""),
        "bundle_id": str(txn.get("bundleId") or ""),
        "purchase_date_ms": txn.get("purchaseDate"),
        "revocation_date_ms": txn.get("revocationDate"),
        "app_account_token": str(txn.get("appAccountToken") or "") or None,
    }
