"""Payment provider boundary for CoinPlotXAI creator economy.

All Stripe calls should live here so route handlers and ledger code do not
grow provider-specific branches. Missing Stripe configuration returns explicit
setup-required responses instead of crashing the app.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import stripe

from services.marketplace_payment_errors import (
    classify_provider_exception,
    stripe_response_dict,
    stripe_response_value,
)


def _base_url() -> str:
    return (os.getenv("APP_BASE_URL") or os.getenv("BASE_URL") or "https://pulsesoc.com").rstrip("/")


def _stripe_ready() -> bool:
    key = (os.getenv("STRIPE_SECRET_KEY") or "").strip()
    if key:
        stripe.api_key = key
    return bool(key)


def provider_status() -> dict[str, Any]:
    return {
        "provider": "stripe",
        "secret_key_loaded": bool(os.getenv("STRIPE_SECRET_KEY")),
        "publishable_key_loaded": bool(os.getenv("STRIPE_PUBLISHABLE_KEY")),
        "webhook_secret_loaded": bool(os.getenv("STRIPE_WEBHOOK_SECRET")),
        "connect_client_id_loaded": bool(os.getenv("STRIPE_CONNECT_CLIENT_ID")),
        "base_url": _base_url(),
        "mode": "live" if (os.getenv("STRIPE_SECRET_KEY") or "").startswith("sk_live_") else "test" if (os.getenv("STRIPE_SECRET_KEY") or "").startswith("sk_test_") else "not_configured",
    }


def setup_required(message: str = "Stripe is not configured yet.") -> dict[str, Any]:
    return {"ok": False, "status": "setup_required", "message": message, "provider": "stripe", "provider_status": provider_status()}


# Seller-facing copy for a failed Connect call. Deliberately not the buyer copy
# in ``marketplace_payment_errors`` — nothing is being charged here, so "No card
# was charged" would describe an event that never happened.
#
# ``_PLATFORM_MESSAGE`` is the one that must not say "try again": when the
# platform has not been signed up for Connect, every retry fails identically and
# forever, so telling the seller to retry sends them into a loop over a blocker
# only PulseSoc can clear.
_PLATFORM_MESSAGE = (
    "Payout setup isn't open yet. PulseSoc is still finishing the payment-provider "
    "setup that has to exist before sellers can connect a bank account. Nothing is "
    "wrong with your account, and retrying won't change it until we finish."
)
_CONFIG_MESSAGE = (
    "Payout setup couldn't start because of a problem on PulseSoc's side, not with "
    "your account. We've recorded the details."
)
_NETWORK_MESSAGE = "We couldn't reach the payout provider. Try again in a moment."
_UNAVAILABLE_MESSAGE = "Payout setup is temporarily unavailable. Try again in a moment."

CONNECT_PLATFORM_CODE = "CONNECT_PLATFORM_NOT_ENABLED"

# Stripe answers a platform that never enabled Connect with a plain
# ``InvalidRequestError`` whose only distinguishing mark is its message — there
# is no ``code`` on it. Matched here purely to choose honest copy; the provider's
# message itself is never returned to the seller.
_PLATFORM_MARKERS = (
    "signed up for connect",
    "only stripe connect platforms",
)


def _is_platform_not_enabled(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PLATFORM_MARKERS)


def connect_failure(exc: Exception, operation: str) -> dict[str, Any]:
    """Turn a raised Stripe Connect error into one seller-safe descriptor.

    Reuses :func:`classify_provider_exception` for the class/status decision so
    payouts and checkout agree on what each Stripe error class means, then
    swaps in payout copy. ``provider_error`` stays the non-sensitive
    ``{type, code, param}`` fingerprint, never the raw message.
    """
    classified = classify_provider_exception(exc)
    code = classified["code"]
    status = classified["status"]

    if _is_platform_not_enabled(exc):
        code, status, message = CONNECT_PLATFORM_CODE, 503, _PLATFORM_MESSAGE
    elif code == "PAYMENT_CONFIGURATION_ERROR":
        message = _CONFIG_MESSAGE
    elif code == "NETWORK_ERROR":
        message = _NETWORK_MESSAGE
    else:
        message = _UNAVAILABLE_MESSAGE

    # The provider's own message is the only place the real cause is written,
    # and the route's ``logging.exception`` does not survive to production logs.
    # Print so it lands on stdout the way ``services/db.py`` errors already do.
    print(
        f"CONNECT_{operation.upper()}_FAILED code={code} "
        f"provider={classified['provider_error']} detail={str(exc)[:400]}",
        flush=True,
    )
    logging.error("CONNECT_%s_FAILED code=%s", operation.upper(), code)

    return {
        "ok": False,
        "status": "provider_error",
        "code": code,
        "http_status": status,
        "message": message,
        "provider_error": classified["provider_error"],
        "retryable": code in {"NETWORK_ERROR", "PAYMENT_UNAVAILABLE"},
    }


def create_connected_account(user: dict[str, Any], seller_type: str) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe Connect cannot start until STRIPE_SECRET_KEY is configured.")
    user_id = str(user.get("user_id") or "")
    try:
        account = stripe.Account.create(
            type="express",
            email=user.get("email") or None,
            metadata={"user_id": user_id, "seller_type": seller_type},
            capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
            # Guards the double tap that lands before the first response is
            # persisted; the stored row is the durable guard once it exists.
            idempotency_key=f"connect-account:{user_id}:{seller_type}",
        )
    except Exception as exc:
        return connect_failure(exc, "account_create")
    return {
        "ok": True,
        "provider_account_id": stripe_response_value(account, "id"),
        "account": stripe_response_dict(account),
    }


def create_onboarding_link(provider_account_id: str, refresh_url: str = "", return_url: str = "") -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe Connect onboarding cannot start until Stripe is configured.")
    if not provider_account_id:
        return {"ok": False, "message": "Connected account id is required."}
    try:
        link = stripe.AccountLink.create(
            account=provider_account_id,
            refresh_url=refresh_url or f"{_base_url()}/payments/cancel",
            return_url=return_url or f"{_base_url()}/payments/success",
            type="account_onboarding",
        )
    except Exception as exc:
        return connect_failure(exc, "account_link")
    return {"ok": True, "url": stripe_response_value(link, "url")}


def get_account_status(provider_account_id: str) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe account status is unavailable until Stripe is configured.")
    if not provider_account_id:
        return {"ok": False, "message": "Connected account id is required."}
    try:
        account = stripe.Account.retrieve(provider_account_id)
    except Exception as exc:
        return connect_failure(exc, "account_retrieve")
    payouts_enabled = bool(stripe_response_value(account, "payouts_enabled", False))
    charges_enabled = bool(stripe_response_value(account, "charges_enabled", False))
    return {
        "ok": True,
        "provider_account_id": provider_account_id,
        "payouts_enabled": payouts_enabled,
        "charges_enabled": charges_enabled,
        "details_submitted": bool(stripe_response_value(account, "details_submitted", False)),
        "disabled_reason": str(stripe_response_value(account, "disabled_reason", "") or ""),
        "onboarding_status": "enabled" if payouts_enabled and charges_enabled else "restricted",
        "requirements": stripe_response_dict(stripe_response_value(account, "requirements", {})),
        "account": stripe_response_dict(account),
    }


def create_checkout_session(
    *,
    buyer_user_id: int,
    seller_user_id: int,
    seller_type: str,
    item_type: str,
    item_id: int | str,
    title: str,
    amount_cents: int,
    currency: str,
    platform_fee_cents: int,
    transaction_id: int,
    connected_account_id: str = "",
    success_url: str = "",
    cancel_url: str = "",
) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Checkout is safely disabled until STRIPE_SECRET_KEY is configured.")
    amount_cents = int(amount_cents or 0)
    if amount_cents <= 0:
        return {"ok": False, "message": "Checkout amount must be greater than zero."}
    metadata = {
        "transaction_id": str(transaction_id),
        "buyer_user_id": str(buyer_user_id),
        "seller_user_id": str(seller_user_id),
        "seller_type": seller_type,
        "item_type": item_type,
        "item_id": str(item_id),
    }
    payment_intent_data: dict[str, Any] = {"metadata": metadata}
    if connected_account_id:
        payment_intent_data["application_fee_amount"] = int(platform_fee_cents or 0)
        payment_intent_data["transfer_data"] = {"destination": connected_account_id}
    session = stripe.checkout.Session.create(
        mode="payment",
        client_reference_id=str(buyer_user_id),
        line_items=[{
            "quantity": 1,
            "price_data": {
                "currency": (currency or "usd").lower(),
                "unit_amount": amount_cents,
                "product_data": {"name": title[:180] or "CoinPlotXAI purchase"},
            },
        }],
        metadata=metadata,
        payment_intent_data=payment_intent_data,
        success_url=success_url or f"{_base_url()}/payments/success?transaction_id={transaction_id}",
        cancel_url=cancel_url or f"{_base_url()}/payments/cancel?transaction_id={transaction_id}",
    )
    return {
        "ok": True,
        "checkout_url": stripe_response_value(session, "url"),
        "provider_checkout_id": stripe_response_value(session, "id"),
        "session": stripe_response_dict(session),
    }


def create_payment_intent(**kwargs) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Payment intents are unavailable until Stripe is configured.")
    intent = stripe.PaymentIntent.create(**kwargs)
    return {
        "ok": True,
        "payment_intent": stripe_response_dict(intent),
        "provider_payment_id": stripe_response_value(intent, "id"),
    }


def create_transfer(**kwargs) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Transfers are unavailable until Stripe is configured.")
    transfer = stripe.Transfer.create(**kwargs)
    return {
        "ok": True,
        "transfer": stripe_response_dict(transfer),
        "provider_transfer_id": stripe_response_value(transfer, "id"),
    }


def create_payout(*, stripe_account: str, idempotency_key: str = "", **kwargs) -> dict[str, Any]:
    """Create a payout from a connected account's Stripe balance to its bank."""
    if not _stripe_ready():
        return setup_required("Payouts are unavailable until Stripe is configured.")
    if not stripe_account:
        return {"ok": False, "message": "Connected account id is required."}
    extra: dict[str, Any] = {"stripe_account": stripe_account}
    if idempotency_key:
        extra["idempotency_key"] = idempotency_key
    payout = stripe.Payout.create(**kwargs, **extra)
    return {
        "ok": True,
        "payout": stripe_response_dict(payout),
        "provider_payout_id": stripe_response_value(payout, "id"),
    }


def create_refund(provider_payment_id: str, amount_cents: int | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Refunds are unavailable until Stripe is configured.")
    payload: dict[str, Any] = {"payment_intent": provider_payment_id, "metadata": metadata or {}}
    if amount_cents is not None:
        payload["amount"] = int(amount_cents)
    refund = stripe.Refund.create(**payload)
    return {
        "ok": True,
        "refund": stripe_response_dict(refund),
        "provider_refund_id": stripe_response_value(refund, "id"),
    }


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> dict[str, Any]:
    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        return setup_required("Stripe webhook secret is missing.")
    try:
        event = stripe.Webhook.construct_event(payload, signature_header, secret)
        return {"ok": True, "event": event}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "status": "invalid_signature"}


def parse_webhook_event(payload: bytes, signature_header: str | None = None) -> dict[str, Any]:
    verified = verify_webhook_signature(payload, signature_header)
    if verified.get("ok"):
        return verified
    return verified
