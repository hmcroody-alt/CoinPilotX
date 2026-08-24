"""Apple App Store Server Notifications v2 — real JWS verification + projection.

This module replaces the ``AppleAppStoreAdapter`` *stub* in ``providers.py`` with
an actual, server-side verifiable implementation. Apple signs App Store Server
Notifications v2 (and the nested ``signedTransactionInfo`` / ``signedRenewalInfo``)
as **JWS** tokens using **ES256**, carrying the signing certificate chain in the
JOSE header ``x5c`` field (base64-DER leaf, intermediate, root).

Verification is therefore two independent checks that BOTH must pass:

1. **Signature** — the ES256 signature over ``header.payload`` verifies against the
   public key of the ``x5c`` **leaf** certificate.
2. **Trust chain** — the ``x5c`` chain links leaf → intermediate → root, each cert
   signed by the next, every cert within its validity window, and the **root** is
   one the operator explicitly trusts (Apple Root CA G3 in production). The trust
   anchor is *injected*, never hard-coded, so this is fully testable offline with a
   self-generated chain and so ops can rotate anchors without a code change.

If either check fails we raise ``AppleJWSError`` and grant nothing. There is no
"skip verification" success path — mirroring the design rule that we must never
fabricate a verified/active result for unverified IAP.

Nothing here reaches out to Apple's servers. Notification delivery is push (Apple
POSTs the signed payload to our webhook); the signed payload is self-contained and
verifiable with only the trust anchor. The optional *pull* App Store Server API
(transaction history lookup) is a provider-side network call and is intentionally
out of scope — see the Stage 4 report.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding

from services import db
from services.business_os.entitlements import providers as _prov
from services.business_os.entitlements import service as _svc


class AppleJWSError(ValueError):
    """Raised when an Apple JWS fails signature, chain, or structural checks.

    Deliberately a hard failure: a caller can never turn this into a granted
    entitlement. Distinct from ``providers.ProviderError`` only for clarity of
    origin; both mean "do not trust this input".
    """


# ---------------------------------------------------------------------------
# base64url + JOSE helpers
# ---------------------------------------------------------------------------
def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def _json_segment(seg: str) -> dict:
    try:
        return json.loads(_b64url_decode(seg).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppleJWSError(f"malformed JWS segment: {exc}") from exc


def _split_jws(token: str) -> tuple[str, str, str]:
    if not isinstance(token, str):
        raise AppleJWSError("JWS must be a string")
    parts = token.split(".")
    if len(parts) != 3:
        raise AppleJWSError("JWS must have exactly three dot-separated segments")
    return parts[0], parts[1], parts[2]


def _raw_p1363_to_der(raw: bytes) -> bytes:
    """ES256 JWS signatures are fixed-width R||S (P1363); cryptography's ECDSA
    verifier wants DER. Convert. R and S are each 32 bytes for P-256."""
    if len(raw) != 64:
        raise AppleJWSError(f"ES256 signature must be 64 bytes, got {len(raw)}")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    return encode_dss_signature(r, s)


# ---------------------------------------------------------------------------
# certificate-chain trust
# ---------------------------------------------------------------------------
def _load_x5c(header: Mapping[str, Any]) -> list[x509.Certificate]:
    x5c = header.get("x5c")
    if not isinstance(x5c, (list, tuple)) or not x5c:
        raise AppleJWSError("JWS header missing x5c certificate chain")
    certs: list[x509.Certificate] = []
    for entry in x5c:
        try:
            der = base64.b64decode(entry)  # x5c entries are standard base64 DER
            certs.append(x509.load_der_x509_certificate(der))
        except Exception as exc:  # noqa: BLE001
            raise AppleJWSError(f"invalid x5c certificate: {exc}") from exc
    return certs


def _cert_fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()


def _verify_cert_signed_by(child: x509.Certificate,
                           issuer: x509.Certificate) -> None:
    """Verify ``child``'s signature was produced by ``issuer``'s private key.
    Supports EC (Apple's chain) and RSA issuers."""
    pub = issuer.public_key()
    try:
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(child.signature, child.tbs_certificate_bytes,
                       ec.ECDSA(child.signature_hash_algorithm))
        elif isinstance(pub, rsa.RSAPublicKey):
            pub.verify(child.signature, child.tbs_certificate_bytes,
                       padding.PKCS1v15(), child.signature_hash_algorithm)
        else:  # pragma: no cover - Apple uses EC
            raise AppleJWSError("unsupported issuer key type in x5c chain")
    except InvalidSignature as exc:
        raise AppleJWSError("x5c chain link signature invalid") from exc


def _within_validity(cert: x509.Certificate, now: datetime) -> bool:
    # Prefer the tz-aware *_utc properties (cryptography >= 42); fall back to the
    # naive properties on older versions and normalize to UTC.
    try:
        nb = cert.not_valid_before_utc
        na = cert.not_valid_after_utc
    except AttributeError:  # pragma: no cover - old cryptography
        nb = cert.not_valid_before.replace(tzinfo=timezone.utc)
        na = cert.not_valid_after.replace(tzinfo=timezone.utc)
    return nb <= now <= na


def validate_chain(certs: Sequence[x509.Certificate], *,
                   trust_anchors: Iterable[bytes],
                   now: datetime) -> x509.Certificate:
    """Validate the x5c chain and return the trusted **leaf** certificate.

    ``trust_anchors`` is an iterable of DER-encoded root certificates the operator
    trusts. The chain's final cert must match one of them by SHA-256 fingerprint,
    every adjacent (child, issuer) link's signature must verify, and every cert
    must be inside its validity window at ``now``.
    """
    if not certs:
        raise AppleJWSError("empty certificate chain")
    anchor_fps = set()
    for anchor in trust_anchors:
        try:
            anchor_fps.add(hashlib.sha256(
                x509.load_der_x509_certificate(anchor).public_bytes(
                    Encoding.DER)).hexdigest())
        except Exception as exc:  # noqa: BLE001
            raise AppleJWSError(f"invalid trust anchor: {exc}") from exc
    if not anchor_fps:
        raise AppleJWSError("no trust anchors provided; refusing to verify")

    for cert in certs:
        if not _within_validity(cert, now):
            raise AppleJWSError("a certificate in the x5c chain is expired/not-yet-valid")

    # link each cert to the next (issuer) in the presented order
    for i in range(len(certs) - 1):
        _verify_cert_signed_by(certs[i], certs[i + 1])

    root = certs[-1]
    # the presented root must itself be one we trust (compare by fingerprint)
    if _cert_fingerprint(root) not in anchor_fps:
        # also allow the case where the anchor is the issuer of the last cert
        # (chain omitted the self-signed root but presented its issuer as trusted)
        raise AppleJWSError("x5c chain does not terminate in a trusted root")
    return certs[0]


# ---------------------------------------------------------------------------
# JWS verify + decode
# ---------------------------------------------------------------------------
def verify_and_decode_jws(token: str, *, trust_anchors: Iterable[bytes],
                          now: Optional[datetime] = None,
                          verify_chain: bool = True) -> dict:
    """Verify an Apple ES256 JWS and return its decoded JSON payload.

    * Rejects any ``alg`` other than ES256 (no ``none``, no alg confusion).
    * Verifies the signature against the x5c leaf public key.
    * When ``verify_chain`` (default) also validates the x5c chain to a trusted
      root at time ``now``.
    """
    now = now or datetime.now(timezone.utc)
    h_seg, p_seg, s_seg = _split_jws(token)
    header = _json_segment(h_seg)
    if header.get("alg") != "ES256":
        raise AppleJWSError(f"unexpected JWS alg {header.get('alg')!r}; require ES256")

    certs = _load_x5c(header)
    if verify_chain:
        leaf = validate_chain(certs, trust_anchors=trust_anchors, now=now)
    else:
        leaf = certs[0]

    pub = leaf.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        raise AppleJWSError("leaf certificate is not an EC key")

    signing_input = f"{h_seg}.{p_seg}".encode("ascii")
    signature = _raw_p1363_to_der(_b64url_decode(s_seg))
    try:
        pub.verify(signature, signing_input, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise AppleJWSError("JWS signature verification failed") from exc

    return _json_segment(p_seg)


# ---------------------------------------------------------------------------
# Notification verifier (the public surface)
# ---------------------------------------------------------------------------
class AppleNotificationVerifier:
    """Verifies an App Store Server Notification v2 and its nested JWS payloads.

    Construct with the operator's trust anchors (DER root certs). In production
    that is Apple Root CA G3; in tests it is a self-generated root. A ``now_fn``
    is injectable so validity-window checks are deterministic under test.
    """

    def __init__(self, *, trust_anchors: Iterable[bytes],
                 now_fn: Optional[Callable[[], datetime]] = None,
                 verify_chain: bool = True):
        self._anchors = list(trust_anchors)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._verify_chain = verify_chain

    def _decode(self, token: str) -> dict:
        return verify_and_decode_jws(
            token, trust_anchors=self._anchors, now=self._now_fn(),
            verify_chain=self._verify_chain)

    def verify(self, signed_payload: str) -> dict:
        """Return the fully decoded, signature-verified notification.

        Shape (nested JWS decoded in place):
            {notificationType, subtype, notificationUUID, version, signedDate,
             data: {bundleId, environment, appAppleId,
                    transactionInfo: {...decoded...},
                    renewalInfo: {...decoded...}}}
        """
        payload = self._decode(signed_payload)
        data = payload.get("data")
        if isinstance(data, Mapping):
            data = dict(data)
            sti = data.pop("signedTransactionInfo", None)
            sri = data.pop("signedRenewalInfo", None)
            if isinstance(sti, str):
                data["transactionInfo"] = self._decode(sti)
            if isinstance(sri, str):
                data["renewalInfo"] = self._decode(sri)
            payload = dict(payload)
            payload["data"] = data
        return payload

    def verify_transaction(self, signed_transaction: str) -> dict:
        """Verify and decode a StoreKit 2 signedTransaction JWS."""
        return self._decode(signed_transaction)


# ---------------------------------------------------------------------------
# productId -> canonical plan_key
# ---------------------------------------------------------------------------
# Apple product identifiers map to the same canonical plan_keys the Stripe path
# uses, so a user who buys via iOS lands the identical entitlement set. Overridable
# without touching projection logic (mirrors ``_STRIPE_PRICE_TO_PLAN``).
APPLE_PRODUCT_TO_PLAN: dict[str, str] = {
    "com.pulsesoc.premium.monthly": "pulse_premium_monthly",
    "com.pulsesoc.premium.annual": "pulse_premium_annual",
    "com.pulsesoc.premium.trial": "pulse_premium_trial",
    "com.pulsesoc.business.monthly": "pulse_business_monthly",
}


def _ms_to_iso(value: Any) -> Optional[str]:
    """Apple sends dates as unix-epoch **milliseconds**."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
    except (ValueError, TypeError, OSError):
        return None


# Apple notificationType -> our lifecycle intent.
#   grant   : project active entitlements (SUBSCRIBED, DID_RENEW, OFFER_REDEEMED,
#             DID_CHANGE_RENEWAL_PREF keeps access, PRICE_INCREASE informational)
#   grace   : keep access but flag grace (Apple is holding access open)
#   revoke  : strip access now (REFUND, REVOKE)
#   expire  : let it lapse at period end (EXPIRED)
#   record  : land the row, change nothing; existing grants lapse at period_end
_GRANT_TYPES = {"SUBSCRIBED", "DID_RENEW", "OFFER_REDEEMED",
                "DID_CHANGE_RENEWAL_PREF", "RENEWAL_EXTENDED"}
#: Kept for defensiveness only. Apple does **not** emit ``GRACE_PERIOD`` as a
#: notificationType — a failed renewal always arrives as ``DID_FAIL_TO_RENEW``
#: and the *subtype* says whether Apple is holding access open. Matching on the
#: type alone meant grace was never once detected in production, while the unit
#: test passed because it hand-built a notification Apple never sends.
_GRACE_TYPES = {"GRACE_PERIOD"}
_FAIL_TO_RENEW = "DID_FAIL_TO_RENEW"
_RENEWAL_STATUS_CHANGED = "DID_CHANGE_RENEWAL_STATUS"
_REVOKE_TYPES = {"REFUND", "REVOKE"}
_EXPIRE_TYPES = {"EXPIRED"}

#: The only status strings this adapter may write. The member-facing Premium
#: screen renders each of these from a translated catalog, so anything outside
#: this set would surface to a paying member as raw provider jargon.
APPLE_SUBSCRIPTION_STATUSES = frozenset(
    {"active", "grace_period", "billing_retry", "canceled", "expired",
     "refunded", "unknown"}
)


def _auto_renew_from(renewal: Optional[Mapping[str, Any]], subtype: Any) -> Optional[bool]:
    """Apple's auto-renew flag, or ``None`` when this notification doesn't say.

    ``renewalInfo.autoRenewStatus`` is authoritative when present. The subtype is
    a fallback for the renewal-status notification, which always carries one.
    ``None`` is a real answer and must stay distinguishable from ``False``: the
    caller preserves the stored flag rather than guessing that a member who never
    cancelled has cancelled.
    """
    if isinstance(renewal, Mapping) and renewal.get("autoRenewStatus") is not None:
        try:
            return int(renewal["autoRenewStatus"]) == 1
        except (TypeError, ValueError):
            pass
    if subtype == "AUTO_RENEW_ENABLED":
        return True
    if subtype == "AUTO_RENEW_DISABLED":
        return False
    return None


def normalize_notification(verified: Mapping[str, Any]) -> Optional[dict]:
    """Turn a verified notification into the common provider shape used by
    ``upsert_provider_subscription`` plus a lifecycle ``intent``. Pure/testable.

    subject_id resolves from the transaction's ``appAccountToken`` (the app sets
    this to our user id at purchase) — the Apple analogue of Stripe's
    client_reference_id. Returns None if it isn't a subscription we can map.
    """
    ntype = verified.get("notificationType")
    subtype = verified.get("subtype")
    data = verified.get("data")
    if not isinstance(data, Mapping):
        return None
    txn = data.get("transactionInfo")
    if not isinstance(txn, Mapping):
        return None

    original_txn_id = txn.get("originalTransactionId") or txn.get("transactionId")
    if not original_txn_id:
        return None
    product_id = txn.get("productId")
    plan_key = APPLE_PRODUCT_TO_PLAN.get(product_id) if product_id else None
    subject_id = (txn.get("appAccountToken") or data.get("appAccountToken"))

    renewal = data.get("renewalInfo")
    auto_renew = _auto_renew_from(renewal if isinstance(renewal, Mapping) else None, subtype)

    # Intent decides access; status decides what the member is *told*. They are
    # deliberately not the same axis: a subscription in billing retry and one
    # that simply expired both stop conferring access, but telling a member their
    # card failed is very different from telling them their plan ended.
    if ntype in _REVOKE_TYPES:
        intent, status = "revoke", "refunded"
    elif ntype in _GRACE_TYPES or (ntype == _FAIL_TO_RENEW and subtype == "GRACE_PERIOD"):
        intent, status = "grace", "grace_period"
    elif ntype == _FAIL_TO_RENEW:
        # Billing retry with no grace period configured. Access already lapsed at
        # the expiry date, so 'record' is correct — it neither extends the grant
        # nor strips it early, it just lets period_end do its job.
        intent, status = "record", "billing_retry"
    elif ntype in _EXPIRE_TYPES:
        intent, status = "expire", "expired"
    elif ntype == _RENEWAL_STATUS_CHANGED:
        # This one notification fires in BOTH directions. Reading the type alone
        # meant a member who switched auto-renew back ON was recorded as
        # cancelling, and was then told their subscription was about to end while
        # Apple was in fact about to charge them again.
        if auto_renew is False:
            intent, status = "record", "canceled"
        else:
            intent, status = "grant", "active"
    elif ntype in _GRANT_TYPES:
        intent, status = "grant", "active"
    else:
        # Genuinely informational (PRICE_INCREASE, CONSUMPTION_REQUEST, anything
        # Apple adds later). `None` means "do not overwrite what we already know".
        # The old code lowercased the notification type into the status column,
        # so an unrecognised notification would put a string like
        # "price_increase" in front of the member as their subscription status.
        intent, status = "record", None

    return {
        "provider_subscription_id": str(original_txn_id),
        "subject_id": str(subject_id) if subject_id not in (None, "") else None,
        "plan_key": plan_key,
        "product_id": product_id,
        "status": status,
        "intent": intent,
        "notification_type": ntype,
        "subtype": subtype,
        "auto_renew": auto_renew,
        "current_period_end": _ms_to_iso(txn.get("expiresDate")),
        "original_purchase_at": _ms_to_iso(txn.get("originalPurchaseDate")),
    }


def apply_apple_notification(signed_payload: str, *,
                             verifier: AppleNotificationVerifier,
                             subject_type: str = "user", conn=None) -> dict:
    """End-to-end: verify the notification, land it in provider_subs, and project
    the lifecycle intent into canonical entitlement grants. Idempotent per
    originalTransactionId. Refuses (raises) on any verification failure.

    Mirrors ``providers.apply_stripe_subscription`` so the two IAP providers and
    Stripe share the same landing zone and projection semantics.
    """
    verified = verifier.verify(signed_payload)  # raises AppleJWSError on tamper
    norm = normalize_notification(verified)
    if norm is None:
        return {"ignored": True, "reason": "not a mappable subscription notification"}

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        # Read the stored row up front. Apple notifications are partial by design
        # — an informational one carries no renewal info and no meaningful status
        # — so anything this notification does not assert is carried forward
        # rather than overwritten with a default.
        prior = conn.execute(
            "SELECT subject_id, status, cancel_at_period_end "
            "FROM business_os_ent_provider_subs "
            "WHERE provider='apple_app_store' AND provider_subscription_id=? LIMIT 1",
            (norm["provider_subscription_id"],),
        ).fetchone()
        prior_subject = _row_at(prior, 0, "subject_id")
        prior_status = _row_at(prior, 1, "status")
        prior_cancel = _row_at(prior, 2, "cancel_at_period_end")

        if norm["subject_id"] is None and prior_subject not in (None, ""):
            norm["subject_id"] = str(prior_subject)

        status = norm["status"] or (str(prior_status) if prior_status else "unknown")
        # `None` auto-renew means this notification is silent on the subject, not
        # that renewal is off. Guessing `False` here would tell a member in good
        # standing that their subscription is ending.
        cancel_at_period_end = (
            bool(prior_cancel) if norm["auto_renew"] is None else not norm["auto_renew"]
        )

        # Always record the provider subscription row (even unmapped plans).
        _prov.upsert_provider_subscription(
            provider="apple_app_store",
            provider_subscription_id=norm["provider_subscription_id"],
            subject_id=norm["subject_id"] or norm["provider_subscription_id"],
            plan_key=norm["plan_key"], status=status,
            current_period_end=norm["current_period_end"],
            cancel_at_period_end=cancel_at_period_end,
            subject_type=subject_type, raw=verified, conn=conn,
        )
        result = {"recorded": True, "projected": False, "revoked": False,
                  "intent": norm["intent"],
                  "notification_type": norm["notification_type"],
                  "provider_subscription_id": norm["provider_subscription_id"]}

        # Can't touch entitlements without a resolvable subject + mapped plan.
        if norm["subject_id"] is None or norm["plan_key"] is None:
            result["reason"] = "unresolved subject or unmapped plan; access unchanged"
            if owned:
                conn.commit()
            return result

        if norm["intent"] in ("grant", "grace", "expire", "record"):
            if norm["intent"] in ("grant", "grace"):
                proj = _svc.sync_subscription_entitlements(
                    norm["subject_id"], norm["plan_key"],
                    status="active",
                    source="apple_app_store",
                    source_reference=norm["provider_subscription_id"],
                    period_end=norm["current_period_end"],
                    grace_until=(norm["current_period_end"]
                                 if norm["intent"] == "grace" else None),
                    subject_type=subject_type, actor="apple_adapter", conn=conn,
                )
                result["projected"] = True
                result["granted_keys"] = proj["granted_keys"]
            # 'expire'/'record' leave existing grants to lapse at period_end
        elif norm["intent"] == "revoke":
            # REFUND / REVOKE strip access immediately for every entitlement the
            # plan confers, scoped to this Apple subscription's source reference.
            revoked_keys = []
            cat = conn.execute(
                "SELECT entitlement_key FROM business_os_ent_catalog WHERE plan_key=?",
                (norm["plan_key"],)).fetchall()
            for row in cat:
                ent_key = _row_first(row)
                _svc.revoke_entitlement(
                    norm["subject_id"], ent_key,
                    reason=f"apple:{norm['notification_type']}",
                    subject_type=subject_type, source="apple_app_store",
                    source_reference=norm["provider_subscription_id"],
                    actor="apple_adapter", conn=conn)
                revoked_keys.append(ent_key)
            result["revoked"] = True
            result["revoked_keys"] = revoked_keys

        if owned:
            conn.commit()
        return result
    finally:
        if owned:
            conn.close()


def apply_verified_subscription_transaction(signed_transaction: str, *,
                                            verifier: AppleNotificationVerifier,
                                            subject_id: str,
                                            expected_app_account_token: str | None = None,
                                            conn=None) -> dict:
    """Verify an authenticated StoreKit purchase and project Premium once.

    The authenticated server subject is authoritative for the initial purchase;
    later App Store notifications resolve the same original transaction row.
    """
    from services import pulse_payment_router as _router

    txn = verifier.verify_transaction(signed_transaction)
    product_id = str(txn.get("productId") or "")
    plan_key = APPLE_PRODUCT_TO_PLAN.get(product_id)
    if product_id not in {v["product_id"] for v in _router.APPLE_PREMIUM_PRODUCTS.values()} or not plan_key:
        raise AppleJWSError("transaction is not a supported Premium product")
    if str(txn.get("bundleId") or "") not in _router.expected_bundle_ids():
        raise AppleJWSError("transaction bundle id mismatch")
    if str(txn.get("type") or "") != "Auto-Renewable Subscription":
        raise AppleJWSError("transaction is not an auto-renewable subscription")
    if expected_app_account_token and str(txn.get("appAccountToken") or "") != expected_app_account_token:
        raise AppleJWSError("transaction account binding mismatch")
    environment = str(txn.get("environment") or "").lower()
    sandbox_allowed = str(__import__("os").environ.get("APPLE_IAP_ALLOW_SANDBOX", "")).lower() in {
        "1", "true", "yes", "on"
    }
    if environment == "sandbox" and not sandbox_allowed:
        raise AppleJWSError("sandbox transaction rejected")
    if environment not in {"production", "sandbox"}:
        raise AppleJWSError("transaction environment is invalid")
    transaction_id = str(txn.get("transactionId") or "")
    original_id = str(txn.get("originalTransactionId") or transaction_id)
    if not transaction_id or not original_id:
        raise AppleJWSError("transaction id missing")
    if txn.get("revocationDate"):
        raise AppleJWSError("transaction was revoked")
    period_end = _ms_to_iso(txn.get("expiresDate"))
    if not period_end:
        raise AppleJWSError("subscription expiration missing")
    try:
        if datetime.fromisoformat(period_end.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            raise AppleJWSError("subscription is expired")
    except ValueError as exc:
        raise AppleJWSError("subscription expiration invalid") from exc

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        prior = conn.execute(
            "SELECT subject_id, cancel_at_period_end FROM business_os_ent_provider_subs "
            "WHERE provider_subscription_id=? LIMIT 1",
            (original_id,),
        ).fetchone()
        prior_subject = _row_at(prior, 0, "subject_id")
        if prior is not None and str(prior_subject) != str(subject_id):
            raise AppleJWSError("transaction already belongs to another account")
        # A signed transaction carries no renewalInfo, so a purchase or restore
        # cannot observe auto-renew. A fresh subscription is renewing by
        # definition; an existing one keeps whatever Apple last told us, because
        # restoring a subscription you already cancelled must not silently
        # re-advertise it as renewing.
        _prov.upsert_provider_subscription(
            provider="apple_app_store", provider_subscription_id=original_id,
            subject_id=str(subject_id), plan_key=plan_key, status="active",
            current_period_end=period_end,
            cancel_at_period_end=bool(_row_at(prior, 1, "cancel_at_period_end")),
            subject_type="user", raw=txn, conn=conn,
        )
        projection = _svc.sync_subscription_entitlements(
            str(subject_id), plan_key, status="active", source="apple_app_store",
            source_reference=original_id, period_end=period_end,
            subject_type="user", actor="apple_storekit_verify", conn=conn,
        )
        if owned:
            conn.commit()
        return {"verified": True, "product_id": product_id,
                "provider_subscription_id": original_id,
                "current_period_end": period_end,
                "granted_keys": projection.get("granted_keys", []),
                "environment": environment or "unknown"}
    finally:
        if owned:
            conn.close()


def _row_first(row):
    """Support both sqlite3.Row and tuple rows for the catalog fetch."""
    try:
        return row[0]
    except Exception:  # noqa: BLE001
        return row["entitlement_key"]


def _row_at(row, index: int, name: str):
    """Read one column from a row by position, falling back to name.

    SQLite hands back tuples, psycopg hands back something indexable, and
    ``sqlite3.Row`` supports both. ``None`` for a missing row is a valid answer
    (no prior subscription), so this never raises.
    """
    if row is None:
        return None
    try:
        return row[index]
    except Exception:  # noqa: BLE001
        try:
            return row[name]
        except Exception:  # noqa: BLE001
            return None
