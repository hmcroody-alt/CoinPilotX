"""Marketplace payment notifications — one event in, in-app + email + push out.

This module owns the mapping from a money event to *who* hears about it, *what*
they are told, *where* the link goes and *which key* makes the event idempotent.
It owns none of the delivery: every event is handed to
``pulsesoc_notification_system.intake_event``, which is PulseSoc's existing
notification engine, so payment mail inherits the same preferences, quiet hours,
dedupe table, delivery jobs and retry ledger as everything else.

Two properties are deliberate and load-bearing:

1. ``intake_event`` opens its own database connection. Callers must therefore
   emit **after** committing the transaction the event describes, never inside
   it — a second writer against an uncommitted row blocks on Postgres and
   raises "database is locked" on SQLite.

2. The email body is *not* carried in the notification. Notification metadata is
   persisted and served back to clients in the notification feed, so an 8 KB
   HTML document there would be both wasteful and leaky. Instead the event
   carries a template key plus a small context, and the email is rendered at
   send time. A retry re-renders rather than replaying a stale snapshot.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Sequence, Tuple

from services import payments_email_templates as templates

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

SELLER_APPLICATION_RECEIVED = "seller_application_received"
SELLER_INFORMATION_REQUESTED = "seller_information_requested"
SELLER_APPROVED = "seller_approved"
SELLER_DECLINED = "seller_declined"

STRIPE_VERIFICATION_REQUIRED = "stripe_verification_required"
STRIPE_ACCOUNT_READY = "stripe_account_ready"
CARD_PAYMENTS_ENABLED = "card_payments_enabled"
SELLER_ACCOUNT_RESTRICTED = "seller_account_restricted"

NEW_PAID_ORDER = "new_paid_order"
PAYOUT_PAID = "payout_paid"
PAYOUT_FAILED = "payout_failed"
REFUND_COMPLETED = "refund_completed"
PAYMENT_SUCCEEDED = "payment_succeeded"
ORDER_SHIPPED = "order_shipped"
DISPUTE_OPENED = "dispute_opened"
DISPUTE_ACTION_REQUIRED = "dispute_action_required"
DISPUTE_WON = "dispute_won"
DISPUTE_LOST = "dispute_lost"
DISPUTE_INQUIRY_CLOSED = "dispute_inquiry_closed"


#: Context keys permitted to cross into notification metadata and into a
#: rendered email. This is an allowlist rather than a denylist on purpose: it is
#: the structural reason a card number, CVV, bank credential, Stripe secret or
#: KYC document cannot reach a notification row even if a future caller passes
#: one. Anything not named here is dropped silently at the boundary.
#:
#: Every field a template reads must appear here or it renders blank, so
#: ``test_allowlist_covers_every_template_field`` asserts the two stay in step.
SAFE_CONTEXT_KEYS = frozenset({
    "amount_cents",
    "application_id",
    "application_url",
    "approval_date",
    "arrival_date",
    "buyer_first_name",
    "card_payment_status",
    "carrier",
    "currency",
    "destination_masked",
    "disabled_reason",
    "dispute_id",
    "dispute_reason",
    "evidence_due_by",
    "failed_at",
    "failure_reason",
    "item_summary",
    "order_id",
    "order_reference",
    "order_url",
    "paid_at",
    "payment_method_masked",
    "payout_id",
    "payout_reference",
    "payout_status",
    "placed_at",
    "receipt_url",
    "refunded_at",
    "requirements",
    "requirements_deadline",
    "reviewer_message",
    "seller_application_status",
    "seller_dashboard_url",
    "seller_first_name",
    "seller_net_cents",
    "seller_payments_url",
    "shipping_cents",
    "store_name",
    "stripe_connect_status",
    "stripe_onboarding_url",
    "subtotal_cents",
    "tax_cents",
    "tracking_reference",
})


def _spec(
    *,
    template: str,
    title: str,
    body: str,
    preview: str,
    deep_link: str,
    category: str,
    priority: str,
    urgency: str,
    source_type: str,
    dedupe_fields: Sequence[str],
) -> Dict[str, Any]:
    return {
        "template": template,
        "title": title,
        "body": body,
        "preview": preview,
        "deep_link": deep_link,
        "category": category,
        "priority": priority,
        "urgency": urgency,
        "source_type": source_type,
        "dedupe_fields": tuple(dedupe_fields),
    }


_APPLY = templates.SELLER_APPLY_PATH
_PAYOUTS = templates.SELLER_PAYMENTS_PATH
_SELLER_ORDERS = templates.SELLER_ORDERS_PATH
_BUYER_ORDERS = templates.BUYER_ORDERS_PATH


SPECS: Dict[str, Dict[str, Any]] = {
    SELLER_APPLICATION_RECEIVED: _spec(
        template="seller_application_received",
        title="Seller application received",
        body="Your seller application is in the review queue. We will tell you as soon as a reviewer decides.",
        preview="Your seller application is in the review queue.",
        deep_link=_APPLY,
        category="marketplace",
        priority="normal",
        urgency="standard",
        source_type="seller_application",
        dedupe_fields=("application_id",),
    ),
    SELLER_INFORMATION_REQUESTED: _spec(
        template="seller_more_info_required",
        title="Your seller application needs more information",
        body="A reviewer needs more information before they can decide on your seller application.",
        preview="A reviewer needs more information on your seller application.",
        deep_link=_APPLY,
        category="marketplace",
        priority="high",
        urgency="standard",
        source_type="seller_application",
        dedupe_fields=("application_id", "reviewer_message"),
    ),
    SELLER_APPROVED: _spec(
        template="seller_approved",
        title="Your seller application was approved",
        body=(
            "You are approved to sell on PulseSoc. Stripe still has to verify your identity and payout "
            "details before you can accept card payments."
        ),
        preview="Your seller application was approved. Set up payments to finish.",
        deep_link=_PAYOUTS,
        category="marketplace",
        priority="high",
        urgency="standard",
        source_type="seller_application",
        dedupe_fields=("application_id",),
    ),
    SELLER_DECLINED: _spec(
        template="seller_declined",
        title="Your seller application was declined",
        body="A reviewer was not able to approve your seller application.",
        preview="A reviewer was not able to approve your seller application.",
        deep_link=_APPLY,
        category="marketplace",
        priority="high",
        urgency="standard",
        source_type="seller_application",
        dedupe_fields=("application_id", "reviewer_message"),
    ),
    STRIPE_VERIFICATION_REQUIRED: _spec(
        template="stripe_verification_required",
        title="Stripe needs more information",
        body="Stripe needs more information before your store can accept card payments or receive payouts.",
        preview="Stripe needs more information to finish verifying your account.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="stripe_account",
        # A new requirement must be able to re-notify, so the outstanding
        # requirement set is part of the key. Without it, the second request
        # for a different document would dedupe against the first and the
        # seller would wait forever on a notification that never arrives.
        dedupe_fields=("requirements",),
    ),
    STRIPE_ACCOUNT_READY: _spec(
        template="stripe_ready",
        title="Your Stripe account is verified",
        body="Stripe finished verifying your account. Your payout details are in place.",
        preview="Stripe finished verifying your account.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="stripe_account",
        dedupe_fields=(),
    ),
    CARD_PAYMENTS_ENABLED: _spec(
        template="card_payments_enabled",
        title="Your store can accept card payments",
        body="Card payments are switched on for your store. Buyers can now check out.",
        preview="Card payments are switched on for your store.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="stripe_account",
        dedupe_fields=(),
    ),
    SELLER_ACCOUNT_RESTRICTED: _spec(
        template="seller_account_restricted",
        title="Your payment account is restricted",
        body="Stripe has restricted your payment account. Payouts are paused until it is resolved.",
        preview="Open PulseSoc to review a restriction on your payment account.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="urgent",
        urgency="immediate",
        source_type="stripe_account",
        dedupe_fields=("disabled_reason",),
    ),
    NEW_PAID_ORDER: _spec(
        template="new_paid_order",
        title="You have a new paid order",
        body="A buyer has paid for an order on your store.",
        preview="A buyer has paid for an order on your store.",
        deep_link=_SELLER_ORDERS,
        category="marketplace",
        priority="high",
        urgency="standard",
        source_type="marketplace_order",
        dedupe_fields=("order_id",),
    ),
    PAYOUT_PAID: _spec(
        template="payout_paid",
        title="Your payout is on the way",
        body="Stripe has sent a payout to your bank account.",
        preview="Open PulseSoc to review your latest payout.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="payout",
        dedupe_fields=("payout_id",),
    ),
    PAYOUT_FAILED: _spec(
        template="payout_failed",
        title="Your payout could not be completed",
        body="Stripe could not complete a payout to your bank account.",
        preview="Open PulseSoc to review a problem with your payout.",
        deep_link=_PAYOUTS,
        category="payments",
        priority="urgent",
        urgency="immediate",
        source_type="payout",
        dedupe_fields=("payout_id",),
    ),
    REFUND_COMPLETED: _spec(
        template="refund_completed",
        title="Your refund has been issued",
        body="A refund has been issued back to your original payment method.",
        preview="Open PulseSoc to review your refund.",
        deep_link=_BUYER_ORDERS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="marketplace_refund",
        dedupe_fields=("order_id", "amount_cents"),
    ),
    PAYMENT_SUCCEEDED: _spec(
        template="payment_succeeded",
        title="Your payment is confirmed",
        body="Your payment went through and the seller has been notified.",
        preview="Open PulseSoc to review your order.",
        deep_link=_BUYER_ORDERS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="marketplace_order",
        dedupe_fields=("order_id",),
    ),
    ORDER_SHIPPED: _spec(
        template="order_shipped",
        title="Your order is on its way",
        body="The seller has shipped your order.",
        preview="The seller has shipped your order.",
        deep_link=_BUYER_ORDERS,
        category="marketplace",
        priority="normal",
        urgency="standard",
        source_type="marketplace_order",
        dedupe_fields=("order_id", "tracking_reference"),
    ),
    DISPUTE_OPENED: _spec(
        template="dispute_opened",
        title="A payment on your store was disputed",
        body="A buyer's bank has disputed a payment. The payout for this order is on hold.",
        preview="Open PulseSoc to review a disputed payment.",
        deep_link=_SELLER_ORDERS,
        category="payments",
        priority="high",
        urgency="immediate",
        source_type="marketplace_dispute",
        dedupe_fields=("dispute_id",),
    ),
    DISPUTE_ACTION_REQUIRED: _spec(
        template="dispute_action_required",
        title="A dispute needs your evidence",
        body="Stripe needs your evidence for a disputed payment before the deadline.",
        preview="Open PulseSoc to respond to a disputed payment.",
        deep_link=_SELLER_ORDERS,
        category="payments",
        priority="urgent",
        urgency="immediate",
        source_type="marketplace_dispute",
        dedupe_fields=("dispute_id", "evidence_due_by"),
    ),
    # The close. A dispute is the one thing a seller is told about that can end
    # against them, so the three terminal statuses get three events rather than
    # one: a loss and a win must not be able to render the same sentence.
    DISPUTE_WON: _spec(
        template="dispute_won",
        title="A payment dispute closed in your favour",
        body="The buyer's bank decided a disputed payment in your favour. The payment stands.",
        preview="A payment dispute closed in your favour.",
        deep_link=_SELLER_ORDERS,
        category="payments",
        priority="high",
        urgency="standard",
        source_type="marketplace_dispute",
        dedupe_fields=("dispute_id",),
    ),
    DISPUTE_LOST: _spec(
        template="dispute_lost",
        title="A payment dispute closed in the buyer's favour",
        body="The buyer's bank decided a disputed payment for the buyer. The payment has been reversed.",
        preview="A payment dispute closed in the buyer's favour.",
        deep_link=_SELLER_ORDERS,
        category="payments",
        priority="high",
        urgency="immediate",
        source_type="marketplace_dispute",
        dedupe_fields=("dispute_id",),
    ),
    DISPUTE_INQUIRY_CLOSED: _spec(
        template="dispute_inquiry_closed",
        title="A payment inquiry on your store has closed",
        body="A bank's question about a payment closed without becoming a dispute. Nothing was decided.",
        preview="A payment inquiry closed without becoming a dispute.",
        deep_link=_SELLER_ORDERS,
        category="payments",
        priority="normal",
        urgency="standard",
        source_type="marketplace_dispute",
        dedupe_fields=("dispute_id",),
    ),
}


#: The engine's registry entry for each event, so ``intake_event`` resolves a
#: real category instead of falling back to ``system`` — which is not in
#: ``EMAIL_DEFAULT_CATEGORIES`` and would silently drop every payment email.
EVENT_DEFINITIONS: Dict[str, Dict[str, str]] = {
    event: {
        "category": spec["category"],
        "priority": spec["priority"],
        "urgency": spec["urgency"],
        "title": spec["title"],
    }
    for event, spec in SPECS.items()
}


CHANNELS: Tuple[str, ...] = ("in_app", "email", "push")


def safe_context(context: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Drop every key not on the allowlist, and coerce values to JSON-safe scalars."""
    out: Dict[str, Any] = {}
    for key, value in (context or {}).items():
        if key not in SAFE_CONTEXT_KEYS or value is None:
            continue
        if isinstance(value, bool) or isinstance(value, int) or isinstance(value, float):
            out[key] = value
        else:
            out[key] = str(value)[:500]
    return out


def dedupe_key(event: str, recipient_user_id: int, context: Mapping[str, Any]) -> str:
    spec = SPECS[event]
    parts = [event, str(int(recipient_user_id or 0))]
    parts.extend(str(context.get(field) or "") for field in spec["dedupe_fields"])
    return ":".join(parts)


def build_event(
    event: str,
    recipient_user_id: int,
    context: Mapping[str, Any] | None = None,
    email_only: bool = False,
) -> Dict[str, Any]:
    """The full ``intake_event`` keyword payload for an event, without sending it.

    Split out from :func:`emit` so the mapping can be asserted on directly
    rather than through a mocked database.
    """
    spec = SPECS[event]
    ctx = safe_context(context)
    return {
        "event_type": event,
        "recipient_user_id": int(recipient_user_id),
        "actor_user_id": 0,
        "source_type": spec["source_type"],
        "source_id": str(
            ctx.get("order_id")
            or ctx.get("payout_id")
            or ctx.get("dispute_id")
            or ctx.get("application_id")
            or ""
        )[:160],
        "title": spec["title"],
        "body": spec["body"],
        "preview": spec["preview"],
        "deep_link": spec["deep_link"],
        "category": spec["category"],
        "priority": spec["priority"],
        "urgency": spec["urgency"],
        "channels": ["email"] if email_only else list(CHANNELS),
        "dedupe_key": dedupe_key(event, recipient_user_id, ctx),
        "metadata": {
            "email_template": spec["template"],
            "email_context": ctx,
            "email_allowed": True,
            "payments_event": event,
            # An order that already has its in-app row from the checkout event
            # path must not gain a second one in the same feed.
            "skip_pulse_legacy_mirror": bool(email_only),
        },
    }


def emit(
    event: str,
    recipient_user_id: int,
    context: Mapping[str, Any] | None = None,
    email_only: bool = False,
) -> Dict[str, Any]:
    """Send one payment notification. Never raises.

    A failure to notify must not roll back the money transaction that caused
    it, so every error is logged and returned rather than propagated. The
    delivery ledger in ``notification_delivery_jobs`` is the durable record;
    this return value is only for the immediate caller.

    ``email_only`` is for the order lifecycle, where ``notify_user`` already
    wrote the in-app row and the push before this module existed. Emitting the
    full set there would show the buyer the same order twice; restricting to
    email adds the missing channel without disturbing the one that works.
    """
    if event not in SPECS:
        logger.warning("PAYMENTS_NOTIFY_UNKNOWN_EVENT event=%s", event)
        return {"ok": False, "error": "unknown_event", "event": event}
    if not recipient_user_id or int(recipient_user_id) <= 0:
        logger.warning("PAYMENTS_NOTIFY_NO_RECIPIENT event=%s", event)
        return {"ok": False, "error": "no_recipient", "event": event}

    payload = build_event(event, recipient_user_id, context, email_only=email_only)
    try:
        from services import pulsesoc_notification_system as engine

        result = engine.intake_event(**payload)
        if not result.get("ok"):
            logger.warning("PAYMENTS_NOTIFY_REJECTED event=%s result=%s", event, result)
        return dict(result, event=event)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.exception("PAYMENTS_NOTIFY_FAILED event=%s error=%s", event, exc)
        return {"ok": False, "error": str(exc), "event": event}


def render_email(metadata: Mapping[str, Any] | None) -> Dict[str, str] | None:
    """The rendered email for a notification, or ``None`` if it carries no template.

    Called by the notification engine's email dispatcher at send time. Returns
    ``None`` rather than raising for anything it does not recognise, so a
    malformed event degrades to the engine's generic email instead of losing
    the notification entirely.
    """
    if not isinstance(metadata, Mapping):
        return None
    key = str(metadata.get("email_template") or "")
    if key not in templates.TEMPLATES:
        return None
    context = metadata.get("email_context")
    try:
        return templates.render(key, context if isinstance(context, Mapping) else {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("PAYMENTS_EMAIL_RENDER_FAILED template=%s error=%s", key, exc)
        return None
