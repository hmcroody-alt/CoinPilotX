"""Apple StoreKit consumable -> PulseSoc Ad Wallet credit (provenance: cash_apple_iap).

The Stripe analogue is ``pulse_ad_payments.credit_wallet_from_stripe_session``;
this module mirrors its shape with the provider swapped, and reuses the same
wallet primitives so the two cash paths share one ledger, one idempotency
mechanism, and one reversal model.

Trust model
-----------
* The ONLY input trusted for money is a signed StoreKit 2 transaction JWS,
  cryptographically verified server-side (ES256 + x5c chain to the operator's
  injected Apple root anchors) via the same primitives the subscription path
  uses (``services.business_os.entitlements.iap_apple.verify_and_decode_jws``).
  There is no skip-verification path.
* The credited amount comes from the server-side catalog
  (``pulse_payment_router.APPLE_ADCREDIT_PRODUCTS``), never from the client and
  never from any price field inside the payload.
* ONE VERIFIED APPLE TRANSACTION = AT MOST ONE CREDIT, enforced at the database
  level: the wallet transaction idempotency key is
  ``apple_iap:txn:{transactionId}`` against the UNIQUE
  ``pulse_ad_wallet_transactions.idempotency_key`` column. Replays dedupe.
* Refunds are compensating reversal entries (never row edits), mirroring
  ``reverse_wallet_funding``: balance may go negative (that negative is the
  advertiser's debt), spendable is floored at zero, campaigns pause.
* Sandbox transactions are rejected unless ``APPLE_IAP_ALLOW_SANDBOX`` is on,
  so a sandbox purchase can never mint production balance by accident.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Optional

from services import pulse_ad_payments as wallet
from services import pulse_ads_service
from services import pulse_payment_router as router
from services.business_os.entitlements import iap_apple as _apple
from services.business_os.entitlements import iap_api as _iap_api


MAX_IAP_QUANTITY = 10  # Apple's own cap for a single purchase


class AppleIapCreditError(pulse_ads_service.PulseAdsError):
    """Raised for policy failures (bad bundle, unknown product, sandbox in prod)."""


def sandbox_allowed() -> bool:
    return (os.getenv("APPLE_IAP_ALLOW_SANDBOX", "") or "").strip().lower() in (
        "1", "true", "on", "yes")


def build_default_decoder() -> Optional[Callable[[str], dict]]:
    """A JWS decoder bound to the configured Apple root anchors, or None when
    ``APPLE_ROOT_CA_CERTS`` is not configured. Never returns a decoder that
    skips verification."""
    anchors = _iap_api._load_apple_anchors()
    if not anchors:
        return None

    def _decode(token: str) -> dict:
        return _apple.verify_and_decode_jws(token, trust_anchors=anchors)

    return _decode


# ---------------------------------------------------------------------------
# Transaction payload validation (post-verification policy checks)
# ---------------------------------------------------------------------------
def _validate_transaction(txn: Mapping[str, Any]) -> dict:
    """Policy-validate a *verified* transaction payload. Returns the normalized
    facts used for the credit; raises AppleIapCreditError on any violation."""
    bundle_id = str(txn.get("bundleId") or "")
    if bundle_id not in router.expected_bundle_ids():
        raise AppleIapCreditError("Transaction is not for this app.", 400)

    product_id = str(txn.get("productId") or "")
    unit_cents = router.APPLE_ADCREDIT_PRODUCTS.get(product_id)
    if unit_cents is None:
        raise AppleIapCreditError("Unknown ad credit product.", 400)

    txn_type = str(txn.get("type") or "")
    if txn_type and txn_type != "Consumable":
        raise AppleIapCreditError("Ad credit products must be consumables.", 400)

    environment = str(txn.get("environment") or "")
    if environment == "Sandbox" and not sandbox_allowed():
        raise AppleIapCreditError("Sandbox purchases are not accepted here.", 400)
    if environment == "Xcode":
        raise AppleIapCreditError("Local StoreKit-test purchases are not accepted.", 400)

    transaction_id = str(txn.get("transactionId") or "")
    if not transaction_id:
        raise AppleIapCreditError("Transaction id missing from payload.", 400)

    quantity = wallet.safe_int(txn.get("quantity"), 1, 1, MAX_IAP_QUANTITY)
    revoked = bool(txn.get("revocationDate") or txn.get("revocationReason") is not None)
    if revoked:
        raise AppleIapCreditError("This transaction has been revoked by Apple.", 400)

    return {
        "bundle_id": bundle_id,
        "product_id": product_id,
        "transaction_id": transaction_id,
        "original_transaction_id": str(txn.get("originalTransactionId") or transaction_id),
        "environment": environment or "Production",
        "quantity": quantity,
        "amount_cents": unit_cents * quantity,
        "app_account_token": str(txn.get("appAccountToken") or ""),
    }


# ---------------------------------------------------------------------------
# Credit path (mirrors credit_wallet_from_stripe_session)
# ---------------------------------------------------------------------------
def credit_ad_wallet_from_apple_transaction(
    conn,
    user_id,
    account_id,
    signed_transaction: str,
    *,
    decode_jws: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Verify a StoreKit 2 signed transaction and credit the Ad Wallet once.

    ``decode_jws`` is injectable for tests; production builds it from the
    configured Apple root anchors. When unconfigured this returns a clean
    ``setup_required`` response and credits nothing.
    """
    account = wallet._owner_account(conn, user_id, account_id)  # 404 if not owner
    decoder = decode_jws or build_default_decoder()
    if decoder is None:
        return {"ok": False, "status": "setup_required",
                "message": "Apple IAP trust anchors are not configured."}
    if not isinstance(signed_transaction, str) or signed_transaction.count(".") != 2:
        raise AppleIapCreditError("A signed transaction JWS is required.", 400)

    # Verification failures raise AppleJWSError -> surfaced by the route as a
    # flat 400; policy failures raise AppleIapCreditError above.
    txn = decoder(signed_transaction)
    facts = _validate_transaction(txn)

    account_id = wallet.safe_int(account_id, minimum=1)
    amount_cents = facts["amount_cents"]
    tx_key = f"apple_iap:txn:{facts['transaction_id']}"

    # DB-level single-credit guarantee: UNIQUE idempotency_key. The key is
    # global (not per-account) so the same Apple transaction can never credit
    # two different ad accounts either.
    tx = wallet._insert_transaction(
        conn,
        account_id,
        "funding",
        amount_cents,
        currency="usd",
        idempotency_key=tx_key,
        description="Apple in-app purchase ad credits",
        metadata={
            "provenance": router.PROVENANCE_APPLE_IAP,
            "product_id": facts["product_id"],
            "quantity": facts["quantity"],
            "environment": facts["environment"],
            "apple_transaction_hash": wallet.hash_value(facts["transaction_id"]),
        },
    )
    if tx.get("deduped"):
        return {"ok": True, "deduped": True, "account_id": account_id,
                "amount_cents": wallet.safe_int(tx.get("amount_cents"))}

    now = wallet.now_iso()
    cur = conn.cursor()
    # Funding-session row for symmetry with the Stripe path: it is what refund
    # notifications match against (provider_session_id = Apple transactionId)
    # and what the receipt/invoice hang off. Provider ids never reach clients.
    cur.execute(
        """
        INSERT INTO pulse_ad_wallet_funding_sessions
        (account_id, user_id, amount_cents, currency, provider, provider_session_id,
         status, idempotency_key, checkout_url, created_at, updated_at)
        VALUES (?, ?, ?, 'usd', 'apple_iap', ?, 'credited', ?, '', ?, ?)
        """,
        (account_id, wallet.safe_int(user_id, minimum=1), amount_cents,
         facts["transaction_id"], tx_key, now, now),
    )
    funding_session_id = cur.lastrowid

    w = wallet.ensure_wallet(conn, account_id, "usd")
    cur.execute(
        """
        UPDATE pulse_ad_wallets
        SET available_balance_cents=?, lifetime_funded_cents=?, updated_at=?
        WHERE id=?
        """,
        (
            wallet.safe_int(w.get("available_balance_cents")) + amount_cents,
            wallet.safe_int(w.get("lifetime_funded_cents")) + amount_cents,
            now,
            w.get("id"),
        ),
    )

    receipt_number = f"AD-RCPT-{funding_session_id}-{now[:10].replace('-', '')}"
    invoice_number = f"AD-INV-{funding_session_id}-{now[:10].replace('-', '')}"
    cur.execute(
        """
        INSERT INTO pulse_ad_receipts
        (account_id, funding_session_id, invoice_number, receipt_number, amount_cents,
         currency, status, provider, provider_reference_hash, created_at)
        VALUES (?, ?, ?, ?, ?, 'usd', 'paid', 'apple_iap', ?, ?)
        """,
        (account_id, funding_session_id, invoice_number, receipt_number,
         amount_cents, wallet.hash_value(facts["transaction_id"]), now),
    )
    try:
        wallet._write_funding_invoice(
            conn,
            account_id=account_id,
            funding_session_id=funding_session_id,
            amount_cents=amount_cents,
            currency="usd",
            receipt_number=receipt_number,
            now=now,
        )
    except Exception:
        # Same stance as the Stripe path: the invoice records money that has
        # already moved; failing to file it must never fail the credit.
        pass
    wallet._audit(
        conn, wallet.safe_int(user_id), "ad_wallet_funded_apple_iap",
        "pulse_ad_wallets", account_id,
        after={"amount_cents": amount_cents,
               "product_id": facts["product_id"],
               "environment": facts["environment"]},
    )
    conn.commit()
    return {
        "ok": True,
        "deduped": False,
        "account_id": account_id,
        "amount_cents": amount_cents,
        "product_id": facts["product_id"],
        "environment": facts["environment"],
        "provenance": router.PROVENANCE_APPLE_IAP,
    }


# ---------------------------------------------------------------------------
# Refund path (App Store Server Notifications v2 -> compensating reversal)
# ---------------------------------------------------------------------------
_REFUND_TYPES = {"REFUND", "REVOKE"}
_CREDIT_NOTIFICATION_TYPES = {"ONE_TIME_CHARGE"}


def handle_apple_notification(conn, verified: Mapping[str, Any]) -> dict:
    """Project an already-verified ASSN v2 notification onto the Ad Wallet.

    Handles ONLY ad-credit consumable products; everything else returns
    ``{"handled": False}`` so the subscription pipeline stays authoritative for
    its own products. The caller owns verification (same verifier as the
    subscription path) — this function trusts its input only because the
    webhook verified the JWS first.
    """
    data = verified.get("data") if isinstance(verified, Mapping) else None
    txn = data.get("transactionInfo") if isinstance(data, Mapping) else None
    if not isinstance(txn, Mapping):
        return {"handled": False, "reason": "no transaction info"}
    product_id = str(txn.get("productId") or "")
    if product_id not in router.APPLE_ADCREDIT_PRODUCTS:
        return {"handled": False, "reason": "not an ad credit product"}

    ntype = str(verified.get("notificationType") or "")
    transaction_id = str(txn.get("transactionId") or "")
    if not transaction_id:
        return {"handled": False, "reason": "missing transaction id"}

    if ntype in _REFUND_TYPES:
        return _reverse_apple_funding(conn, verified, txn, transaction_id)

    if ntype in _CREDIT_NOTIFICATION_TYPES:
        # A purchase Apple saw but the client never submitted for verification
        # (crash before the verify call, network loss). We do not auto-credit
        # here — there is no authenticated ad-account context — but the money
        # must not vanish silently: if no funding session exists, finance gets
        # an incident to reconcile against App Store Connect.
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM pulse_ad_wallet_funding_sessions "
            "WHERE provider='apple_iap' AND provider_session_id=?",
            (transaction_id,),
        )
        if wallet.row_to_dict(cur.fetchone()):
            return {"handled": True, "noop": True, "reason": "already credited"}
        wallet._open_wallet_incident(
            "ORPHAN_STRIPE_OBJECT",  # generic orphan-provider-object incident type
            severity="warning",
            summary=(
                "Apple ONE_TIME_CHARGE for ad credit product "
                f"{product_id} has no matching funding session; the buyer may "
                "not have received their credits."
            ),
            details={
                "product_id": product_id,
                "apple_transaction_hash": wallet.hash_value(transaction_id),
                "environment": str(txn.get("environment") or ""),
            },
            related_object="pulse_ad_wallet_funding_sessions:unmatched",
            incident_key=f"apple_iap_uncredited:{wallet.hash_value(transaction_id)}",
        )
        return {"handled": True, "noop": True, "reason": "uncredited purchase flagged"}

    return {"handled": False, "reason": f"notification type {ntype} not wallet-relevant"}


def _reverse_apple_funding(conn, verified: Mapping[str, Any],
                           txn: Mapping[str, Any], transaction_id: str) -> dict:
    """Compensating reversal for a refunded/revoked Apple ad-credit purchase.

    Consumable refunds are whole-transaction: the reversal target is the full
    funded amount. Mirrors ``reverse_wallet_funding`` semantics — negative
    balances allowed (debt), spendable floored elsewhere, campaigns paused,
    idempotent on the notification, pulse_ad_refunds row filed.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_wallet_funding_sessions "
        "WHERE provider='apple_iap' AND provider_session_id=? "
        "AND status IN ('credited','reversed','partially_reversed') "
        "ORDER BY id DESC LIMIT 1",
        (transaction_id,),
    )
    funding = wallet.row_to_dict(cur.fetchone())
    if not funding:
        # A verified Apple refund for a credit this database never granted.
        wallet._open_wallet_incident(
            "ORPHAN_STRIPE_OBJECT",
            severity="warning",
            summary=("Apple refund notification references an ad-credit "
                     "transaction with no funding session."),
            details={"apple_transaction_hash": wallet.hash_value(transaction_id)},
            related_object="pulse_ad_wallet_funding_sessions:unmatched",
            incident_key=f"apple_iap_orphan_refund:{wallet.hash_value(transaction_id)}",
        )
        return {"handled": True, "ok": False, "ignored": True,
                "reason": "no matching apple funding session"}

    account_id = wallet.safe_int(funding.get("account_id"), minimum=1)
    funded_cents = wallet.safe_int(funding.get("amount_cents"), 0)
    already_reversed = wallet.safe_int(funding.get("reversed_cents"), 0)
    delta = max(0, funded_cents - already_reversed)
    if delta <= 0:
        return {"handled": True, "ok": True, "noop": True, "account_id": account_id}

    notification_uuid = str(verified.get("notificationUUID") or transaction_id)
    tx_key = f"apple_iap:refund:{transaction_id}:{notification_uuid}"
    tx = wallet._insert_transaction(
        conn,
        account_id,
        "refund",
        delta,
        currency="usd",
        idempotency_key=tx_key,
        description="Apple in-app purchase refunded",
        metadata={
            "provenance": router.PROVENANCE_APPLE_IAP,
            "apple_transaction_hash": wallet.hash_value(transaction_id),
            "revocation_reason": str(txn.get("revocationReason") or ""),
            "funding_session_id": wallet.safe_int(funding.get("id")),
        },
    )
    if tx.get("deduped"):
        return {"handled": True, "ok": True, "deduped": True, "account_id": account_id}

    w = wallet.ensure_wallet(conn, account_id, "usd")
    now = wallet.now_iso()
    new_available = wallet.safe_int(w.get("available_balance_cents")) - delta
    new_lifetime = max(0, wallet.safe_int(w.get("lifetime_funded_cents")) - delta)
    cur.execute(
        "UPDATE pulse_ad_wallets SET available_balance_cents=?, "
        "lifetime_funded_cents=?, updated_at=? WHERE id=?",
        (new_available, new_lifetime, now, w.get("id")),
    )
    total_reversed = already_reversed + delta
    cur.execute(
        "UPDATE pulse_ad_wallet_funding_sessions SET reversed_cents=?, status=?, "
        "updated_at=? WHERE id=?",
        (total_reversed,
         "reversed" if total_reversed >= funded_cents else "partially_reversed",
         now, funding.get("id")),
    )
    cur.execute(
        """
        INSERT INTO pulse_ad_refunds
        (account_id, funding_session_id, amount_cents, currency, status, reason,
         provider_reference_hash, created_at, updated_at)
        VALUES (?, ?, ?, 'usd', 'refunded', ?, ?, ?, ?)
        """,
        (account_id, funding.get("id"), delta,
         wallet.clean_text(f"apple:{verified.get('notificationType')}", 400),
         wallet.hash_value(transaction_id), now, now),
    )
    paused = wallet._pause_campaigns_without_balance(conn, account_id)
    wallet._audit(
        conn, wallet.safe_int(funding.get("user_id")),
        "ad_wallet_funding_reversed_apple_iap", "pulse_ad_wallets", account_id,
        after={"reversed_cents": delta, "available_balance_cents": new_available,
               "campaigns_paused": paused},
    )
    conn.commit()
    if new_available < 0:
        wallet._open_wallet_incident(
            "NEGATIVE_BALANCE_DETECTED",
            severity="warning",
            summary=(f"Ad wallet for account {account_id} went negative "
                     f"({new_available}c) after an Apple IAP refund."),
            details={"account_id": account_id,
                     "available_balance_cents": new_available,
                     "reversal_delta_cents": delta},
            related_object=f"pulse_ad_wallets:{wallet.safe_int(w.get('id'))}",
            incident_key=f"negative_balance_detected:ad_wallet:{account_id}:{new_available}",
        )
    return {"handled": True, "ok": True, "account_id": account_id,
            "reversed_cents": delta, "available_balance_cents": new_available,
            "campaigns_paused": paused}


def handle_webhook_signed_payload(signed_payload: str, *, verifier=None, conn=None) -> dict:
    """Webhook entry: verify the ASSN v2 envelope (same anchors as the
    subscription path) then project ad-credit effects. Safe no-op for every
    non-ad-credit notification."""
    if verifier is None:
        verifier, err = _iap_api._apple_verifier_or_error(None)
        if err is not None:
            return {"handled": False, "reason": "trust anchors not configured"}
    try:
        verified = verifier.verify(signed_payload)
    except _apple.AppleJWSError:
        return {"handled": False, "reason": "verification failed"}
    owned = conn is None
    if owned:
        from services import db as _db
        conn = _db.connect()
    try:
        return handle_apple_notification(conn, verified)
    finally:
        if owned:
            conn.close()
