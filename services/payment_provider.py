"""Payment provider boundary for CoinPilotXAI creator economy.

All Stripe calls should live here so route handlers and ledger code do not
grow provider-specific branches. Missing Stripe configuration returns explicit
setup-required responses instead of crashing the app.
"""

from __future__ import annotations

import os
from typing import Any

import stripe


def _base_url() -> str:
    return (os.getenv("APP_BASE_URL") or os.getenv("BASE_URL") or "https://coinpilotx.app").rstrip("/")


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


def create_connected_account(user: dict[str, Any], seller_type: str) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe Connect cannot start until STRIPE_SECRET_KEY is configured.")
    account = stripe.Account.create(
        type="express",
        email=user.get("email") or None,
        metadata={"user_id": str(user.get("user_id") or ""), "seller_type": seller_type},
        capabilities={"card_payments": {"requested": True}, "transfers": {"requested": True}},
    )
    return {"ok": True, "provider_account_id": account.get("id"), "account": dict(account)}


def create_onboarding_link(provider_account_id: str, refresh_url: str = "", return_url: str = "") -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe Connect onboarding cannot start until Stripe is configured.")
    if not provider_account_id:
        return {"ok": False, "message": "Connected account id is required."}
    link = stripe.AccountLink.create(
        account=provider_account_id,
        refresh_url=refresh_url or f"{_base_url()}/payments/cancel",
        return_url=return_url or f"{_base_url()}/payments/success",
        type="account_onboarding",
    )
    return {"ok": True, "url": link.get("url")}


def get_account_status(provider_account_id: str) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Stripe account status is unavailable until Stripe is configured.")
    if not provider_account_id:
        return {"ok": False, "message": "Connected account id is required."}
    account = stripe.Account.retrieve(provider_account_id)
    requirements = dict(account.get("requirements") or {})
    return {
        "ok": True,
        "provider_account_id": provider_account_id,
        "payouts_enabled": bool(account.get("payouts_enabled")),
        "charges_enabled": bool(account.get("charges_enabled")),
        "onboarding_status": "enabled" if account.get("payouts_enabled") and account.get("charges_enabled") else "restricted",
        "requirements": requirements,
        "account": dict(account),
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
                "product_data": {"name": title[:180] or "CoinPilotXAI purchase"},
            },
        }],
        metadata=metadata,
        payment_intent_data=payment_intent_data,
        success_url=success_url or f"{_base_url()}/payments/success?transaction_id={transaction_id}",
        cancel_url=cancel_url or f"{_base_url()}/payments/cancel?transaction_id={transaction_id}",
    )
    return {"ok": True, "checkout_url": session.get("url"), "provider_checkout_id": session.get("id"), "session": dict(session)}


def create_payment_intent(**kwargs) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Payment intents are unavailable until Stripe is configured.")
    intent = stripe.PaymentIntent.create(**kwargs)
    return {"ok": True, "payment_intent": dict(intent), "provider_payment_id": intent.get("id")}


def create_transfer(**kwargs) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Transfers are unavailable until Stripe is configured.")
    transfer = stripe.Transfer.create(**kwargs)
    return {"ok": True, "transfer": dict(transfer), "provider_transfer_id": transfer.get("id")}


def create_refund(provider_payment_id: str, amount_cents: int | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if not _stripe_ready():
        return setup_required("Refunds are unavailable until Stripe is configured.")
    payload: dict[str, Any] = {"payment_intent": provider_payment_id, "metadata": metadata or {}}
    if amount_cents is not None:
        payload["amount"] = int(amount_cents)
    refund = stripe.Refund.create(**payload)
    return {"ok": True, "refund": dict(refund), "provider_refund_id": refund.get("id")}


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
