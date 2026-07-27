"""Business OS — In-App Purchase webhooks: framework-agnostic controller (Stage 4).

bot.py owns the raw request, signature *transport* (Apple posts a JWS body; Google
posts a Pub/Sub JSON envelope), and turns the returned ``(status, body)`` tuple into
a Flask response. All decision logic lives here so it is unit-testable without Flask
(bot.py is not importable in the hermetic sandbox).

Contract (mirrors the marketplace/advertising controllers exactly):

  * every handler returns ``(int status, dict body)``; ``body`` always has an ``ok`` bool;
  * the whole surface is DARK when ``BUSINESS_OS_IAP`` is off — every handler returns 404;
  * verification is authoritative and server-side: Apple JWS is cryptographically
    verified against injected trust anchors; Google RTDN is only trusted after the
    injected purchase-verifier confirms it. Neither path can be coerced into a grant
    from unverified input;
  * only curated error messages are surfaced — never an internal exception string.

The verifier objects are *injected* (``apple_verifier`` / ``google_purchase_verifier``)
so tests pass self-generated chains / stub verifiers, and production wires the real
Apple trust anchors and Play Developer API caller. When not injected, the controller
builds them from configuration and returns a clean ``503 not_configured`` if the
operator hasn't supplied the anchors yet — never a fabricated success.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from services.business_os.entitlements import iap_apple as _apple
from services.business_os.entitlements import iap_google as _google


FLAG_ENV = "BUSINESS_OS_IAP"


def is_enabled() -> bool:
    raw = (os.getenv(FLAG_ENV, "") or "").strip().lower()
    return raw in ("1", "true", "on", "yes", "enabled", "canonical")


def _dark():
    return (404, {"ok": False, "error": "Not found."})


# --- Apple trust-anchor loading (config-driven, overridable) ----------------
def _load_apple_anchors() -> list[bytes]:
    """Load Apple root CA DER cert(s) from ``APPLE_ROOT_CA_CERTS`` (path(s), os.pathsep
    separated; .der/.cer read as DER, .pem parsed). Empty when unconfigured."""
    spec = (os.getenv("APPLE_ROOT_CA_CERTS", "") or "").strip()
    if not spec:
        return []
    anchors: list[bytes] = []
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    for path in spec.split(os.pathsep):
        path = path.strip()
        if not path or not os.path.exists(path):
            continue
        data = open(path, "rb").read()
        try:
            if b"-----BEGIN" in data:
                cert = x509.load_pem_x509_certificate(data)
            else:
                cert = x509.load_der_x509_certificate(data)
            anchors.append(cert.public_bytes(Encoding.DER))
        except Exception:  # noqa: BLE001 - a bad file must not grant access
            continue
    return anchors


def _apple_verifier_or_error(injected):
    if injected is not None:
        return injected, None
    anchors = _load_apple_anchors()
    if not anchors:
        return None, (503, {"ok": False, "code": "not_configured",
                            "error": "Apple IAP trust anchors are not configured."})
    return _apple.AppleNotificationVerifier(trust_anchors=anchors), None


# ---------------------------------------------------------------------------
# Apple App Store Server Notifications v2
# ---------------------------------------------------------------------------
def apple_notification(body: Any, *, apple_verifier=None, subject_type: str = "user") -> tuple:
    """Handle an Apple ASSN v2 webhook. ``body`` is the raw ``{"signedPayload": "<JWS>"}``
    (or the bare JWS string). Verifies + projects; DARK when the flag is off."""
    if not is_enabled():
        return _dark()
    signed = None
    if isinstance(body, str):
        signed = body
    elif isinstance(body, dict):
        signed = body.get("signedPayload")
    if not signed or not isinstance(signed, str):
        return (400, {"ok": False, "code": "missing_payload",
                      "error": "Expected a signedPayload JWS."})

    verifier, err = _apple_verifier_or_error(apple_verifier)
    if err is not None:
        return err
    try:
        result = _apple.apply_apple_notification(
            signed, verifier=verifier, subject_type=subject_type)
    except _apple.AppleJWSError:
        # Do not echo crypto internals; a failed verification is a flat rejection.
        return (400, {"ok": False, "code": "verification_failed",
                      "error": "Notification signature could not be verified."})
    return (200, {"ok": True, "result": result})


# ---------------------------------------------------------------------------
# Google Play Real-Time Developer Notifications
# ---------------------------------------------------------------------------
def google_rtdn(body: Any, *, google_purchase_verifier=None,
                subject_type: str = "user") -> tuple:
    """Handle a Google Play RTDN Pub/Sub push (``{"message": {"data": ...}}``).
    Requires an injected purchase-verifier (the Play Developer API boundary); if
    none is configured we acknowledge without granting rather than fabricate one."""
    if not is_enabled():
        return _dark()
    if not isinstance(body, dict):
        return (400, {"ok": False, "code": "missing_payload",
                      "error": "Expected a Pub/Sub push envelope."})

    verifier = google_purchase_verifier
    if verifier is None:
        # Without the Play Developer API caller we cannot verify — acknowledge the
        # push (so Pub/Sub stops retrying) but grant nothing and say so.
        return (200, {"ok": True, "result": {
            "recorded": False, "projected": False,
            "reason": "purchase verifier not configured; access unchanged"}})
    try:
        result = _google.apply_rtdn(
            body, purchase_verifier=verifier, subject_type=subject_type)
    except _google.GoogleRTDNError:
        return (400, {"ok": False, "code": "malformed_rtdn",
                      "error": "Malformed RTDN envelope."})
    return (200, {"ok": True, "result": result})
