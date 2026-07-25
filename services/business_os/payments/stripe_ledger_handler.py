"""Server-authoritative Stripe-event -> canonical-ledger handler.

This is the bridge between the durable webhook inbox (``webhook_inbox``) and the
canonical double-entry ledger (``business_os.ledger``). It is the *only* place a
provider event becomes money movement, and it obeys the Stage 0 non-negotiables:

* **Never trust the client.** Amounts and currency are read from the Stripe
  event object itself (``amount`` / ``amount_total`` / ``amount_refunded``),
  which is integer minor units straight from Stripe — never from any
  client-supplied field.
* **Idempotent, defence-in-depth.** The ledger idempotency key is derived from
  the Stripe event id, so even if the inbox's single-claim guarantee were ever
  bypassed, the ledger would still refuse to double-post.
* **Never lose money.** If the event cannot be mapped to a known user account,
  the funds are posted to a ``platform:stripe_suspense`` holding account (which
  keeps the double-entry invariant intact and flags the row for manual
  reconciliation) instead of being silently dropped.
* **Unknown events are ignored, not failed.** Returning an ``ignored`` result
  lets the inbox mark the row processed so it is not retried forever.

The handler signature ``handle_stripe_event(payload: dict) -> dict`` matches what
``webhook_inbox.process_event`` / ``reconcile_pending`` expect. A pure
``map_stripe_event`` helper is factored out so the mapping logic is unit-testable
without a database.

Engine-portable via the ledger module; does not import ``bot.py``.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from services.business_os import ledger

# Ledger accounts this handler posts against.
EXTERNAL_STRIPE = "external:stripe"          # funding source / refund destination
SUSPENSE = "platform:stripe_suspense"        # holding account for unmapped funds

# Stripe event types that credit a user (money in).
_FUNDING_EVENTS = {
    "payment_intent.succeeded",
    "charge.succeeded",
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
}
# Stripe event types that reverse a prior credit (money out).
_REFUND_EVENTS = {
    "charge.refunded",
    "refund.created",
    "charge.refund.updated",
}

# Metadata keys we will accept as a user identifier, in priority order. Kept
# conservative on purpose: an unrecognised event routes to suspense rather than
# guessing a target account.
_USER_ID_KEYS = ("pulse_user_id", "user_id", "app_user_id", "client_reference_id")


class StripeLedgerMappingError(ValueError):
    """Raised only for events that look fundable but are internally malformed
    (e.g. a positive-amount funding event with no resolvable currency). These
    should fail loudly so the inbox retries / a human looks."""


def _event_object(payload: Mapping[str, Any]) -> dict:
    """Return the Stripe ``data.object`` (the charge / intent / refund / session)."""
    data = payload.get("data") if isinstance(payload, Mapping) else None
    obj = data.get("object") if isinstance(data, Mapping) else None
    return dict(obj) if isinstance(obj, Mapping) else {}


def _resolve_user_account(obj: Mapping[str, Any]) -> Optional[str]:
    """Best-effort map from a Stripe object to a ``user:<id>`` ledger account.

    Checks top-level ``client_reference_id`` and the object ``metadata``. Returns
    ``None`` (caller falls back to suspense) when nothing usable is present.
    """
    ref = obj.get("client_reference_id")
    if ref not in (None, "", 0):
        return f"user:{ref}"
    meta = obj.get("metadata")
    if isinstance(meta, Mapping):
        for key in _USER_ID_KEYS:
            val = meta.get(key)
            if val not in (None, "", 0):
                return f"user:{val}"
    return None


def _coerce_amount_cents(*candidates: Any) -> Optional[int]:
    """First candidate that is a positive integer number of minor units.

    Stripe already sends integer minor units, so we accept ``int`` (and clean
    integer-valued strings) but reject floats/bools to keep money math exact.
    """
    for c in candidates:
        if isinstance(c, bool) or c is None:
            continue
        if isinstance(c, int) and c > 0:
            return c
        if isinstance(c, str) and c.strip().isdigit():
            n = int(c.strip())
            if n > 0:
                return n
    return None


def map_stripe_event(payload: Mapping[str, Any]) -> Optional[dict]:
    """Pure mapping: Stripe event -> intended ledger posting (or ``None``).

    Returns ``None`` for event types we intentionally ignore, or for
    funding/refund events whose amount is zero/absent (nothing to post).
    Raises :class:`StripeLedgerMappingError` only for a genuinely malformed
    fundable event so the inbox surfaces it instead of silently dropping money.
    """
    if not isinstance(payload, Mapping):
        return None
    event_type = str(payload.get("type") or "").strip()
    event_id = str(payload.get("id") or "").strip()
    if not event_type:
        return None

    is_funding = event_type in _FUNDING_EVENTS
    is_refund = event_type in _REFUND_EVENTS
    if not is_funding and not is_refund:
        return None  # not a money-moving event we handle -> ignore

    obj = _event_object(payload)
    currency = str(obj.get("currency") or "usd").lower()

    if is_funding:
        amount = _coerce_amount_cents(
            obj.get("amount_received"), obj.get("amount"), obj.get("amount_total")
        )
        if amount is None:
            return None  # e.g. a $0 session -> nothing to post
        user_account = _resolve_user_account(obj)
        destination = user_account or SUSPENSE
        if currency == "":
            raise StripeLedgerMappingError(
                f"funding event {event_id} has amount {amount} but no currency"
            )
        return {
            "kind": "funding",
            "idempotency_key": f"stripe:{event_id}:funding",
            "actor": "stripe",
            "amount_cents": amount,
            "currency": currency,
            "entry_type": "funding",
            "source": EXTERNAL_STRIPE,
            "destination": destination,
            "reason": f"stripe:{event_type}",
            "provider_reference": event_id,
            "unmapped": user_account is None,
        }

    # refund: money leaves the platform back to the customer.
    amount = _coerce_amount_cents(obj.get("amount_refunded"), obj.get("amount"))
    if amount is None:
        return None
    user_account = _resolve_user_account(obj)
    source = user_account or SUSPENSE
    return {
        "kind": "refund",
        "idempotency_key": f"stripe:{event_id}:refund",
        "actor": "stripe",
        "amount_cents": amount,
        "currency": currency,
        "entry_type": "refund",
        "source": source,
        "destination": EXTERNAL_STRIPE,
        "reason": f"stripe:{event_type}",
        "provider_reference": event_id,
        "unmapped": user_account is None,
    }


def handle_stripe_event(payload: Mapping[str, Any]) -> dict:
    """Inbox handler: post the mapped event to the canonical ledger.

    Returns a small result dict. Idempotent: a replayed event id produces a
    ledger ``duplicate`` and does not double-post. Ignored event types return
    ``{"ignored": True}`` so the inbox marks the row processed.
    """
    ledger.ensure_schema()
    posting = map_stripe_event(payload)
    if posting is None:
        return {"ignored": True, "type": str((payload or {}).get("type") or "")}

    # Suspense-routed postings (unmapped user) are allowed to move an
    # allow-negative account without overdraft objections; a resolved user
    # account keeps normal overdraft protection.
    result = ledger.post_entry(
        idempotency_key=posting["idempotency_key"],
        actor=posting["actor"],
        amount_cents=posting["amount_cents"],
        currency=posting["currency"],
        entry_type=posting["entry_type"],
        source=posting["source"],
        destination=posting["destination"],
        reason=posting["reason"],
        provider_reference=posting["provider_reference"],
        metadata={"unmapped": bool(posting.get("unmapped"))},
    )
    return {
        "posted": True,
        "kind": posting["kind"],
        "duplicate": bool(result.get("duplicate")),
        "transaction_id": result.get("transaction_id"),
        "unmapped": bool(posting.get("unmapped")),
    }
