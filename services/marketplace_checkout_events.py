"""Financial events for the card checkout path: what happened to the money.

Why this module exists
----------------------
The cart checkout handler is 600 lines with about thirty exits. Before this
module it logged twelve lines, and every one of them was a *failure* or a step
in the idempotency claim's lifecycle. Three things it never recorded:

1. **That a checkout was attempted at all.** Without that line there is no
   denominator. "Nobody tried" and "everybody was refused" produce identical
   logs, which is precisely why ``paid`` could sit at zero rows for the entire
   life of the card rail without anything anywhere looking wrong.
2. **Why a buyer was refused.** Around thirty refusal sites, none of them
   logged. A buyer reporting "it won't let me check out" left no server-side
   trace at all, so the only available diagnosis was to ask them to try again
   while someone watched.
3. **That a payable money surface was created.** The handler's failure path
   logs; its two success paths did not. For money that is backwards.

So the taxonomy is three events, and they are chosen to *close* the accounting:
every exit from the handler is now either a refusal or a payable, and both are
preceded by an attempt. ``attempted = refused + payable + crashed`` is a sum an
operator can check, and a drift in it is itself the alert.

On not inventing a vocabulary
-----------------------------
The obvious temptation is to enumerate the refusal reasons here. That would be
wrong, and this file has the receipts: ``_error``'s own docstring in
``marketplace_cart_routes`` documents a "fixed" vocabulary of sixteen codes, and
the code it actually ships disagrees — five codes are emitted that the list does
not mention (``INVALID_REQUEST``, ``ITEM_NEEDS_OWN_CHECKOUT``, ``MIXED_CURRENCY``,
``OWN_LISTING``, ``VARIANT_REQUIRED``/``VARIANT_UNAVAILABLE``), and ten refusal
sites pass a code *computed* by another module, so no list written here could
ever be complete.

:func:`refused` therefore carries whatever code the handler was actually given,
verbatim, as an opaque dimension. It is a recorder, not a validator. A second
authority on the refusal vocabulary would eventually disagree with the handler,
and then the logs would be confidently describing refusals that do not exist.

On redaction
------------
Nothing here may ever carry card data, a Stripe or webhook secret, a raw token,
a credential, a buyer's address, or private customer data. That is enforced
structurally rather than by review:

* The event functions take **named parameters**, so for most fields there is no
  channel through which a secret could arrive.
* The one function that must stay open-ended -- :func:`refused`, because its
  caller is a ``**extra`` funnel -- filters through :data:`REFUSAL_SAFE_FIELDS`
  and **names the keys it dropped** in the emitted line. A silent filter would
  hide a future leak; naming the dropped key (never its value) turns the same
  event into the notice that someone tried to log something new.

One specific hazard, because it is not obvious and it is real: a Stripe Checkout
Session's ``url`` embeds that session's client secret. It is a credential in the
shape of a link. It is never logged, there is no parameter for it, and
``checkout_url`` is listed in :data:`REFUSAL_DENIED_FIELDS` so that a future
refusal site passing it along is dropped loudly instead of quietly.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

LOGGER = logging.getLogger(__name__)

__all__ = [
    "ATTEMPTED",
    "REFUSED",
    "PAYABLE_CREATED",
    "EVENT_NAMES",
    "SURFACE_CHECKOUT_SESSION",
    "SURFACE_PAYMENT_INTENT",
    "SURFACE_CASH",
    "REFUSAL_SAFE_FIELDS",
    "REFUSAL_DENIED_FIELDS",
    "attempted",
    "refused",
    "payable_created",
    "safe_refusal_extra",
]

# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------

#: A buyer asked to check out. Emitted once per request, before any decision, so
#: it is the denominator for everything else.
ATTEMPTED = "CHECKOUT_ATTEMPTED"

#: The server declined, with the code the buyer's client received.
#:
#: Emitted from the single ``_error`` funnel in ``marketplace_cart_routes``,
#: which every refusal on that surface already routes through. One emit site
#: covers roughly thirty outcomes, a refusal site added next year is covered
#: without anyone remembering to, and -- the part that matters most -- there is
#: no way for a refusal to *skip* it, which eighteen hand-placed calls could not
#: have promised.
#:
#: That funnel serves the whole cart surface, not only checkout, so the name is
#: the surface's and the handler is a dimension (``op``). Naming it
#: ``CHECKOUT_REFUSED`` would have been a lie on every cart-add refusal, and
#: splitting the emit to avoid the lie would have given back the one property
#: worth having.
REFUSED = "MARKETPLACE_CART_REFUSED"

#: A chargeable object now exists at the provider. This is the moment the money
#: becomes real, and the last moment this process is involved: settlement is the
#: webhook's story, and a webhook that never arrives is the missed-payment
#: cycle's. Both of those already emit, which is why this taxonomy stops here
#: rather than growing names for states it cannot observe.
PAYABLE_CREATED = "CHECKOUT_PAYABLE_CREATED"

#: Names, in emission order. Exported for operators building queries, *not* as
#: something tests should assert against: a registry makes a name look present
#: to ``grep`` whether or not any code path emits it. The tests for this module
#: capture real log records instead.
EVENT_NAMES = (ATTEMPTED, REFUSED, PAYABLE_CREATED)

#: Where the resulting obligation lives. One event with a dimension rather than
#: three event names, because every question an operator asks ("how many
#: checkouts succeeded today", "what is the refusal rate") wants them summed,
#: and the questions that do not are a filter on this field. Three names would
#: make the common case a union and the rare case a single read, which is the
#: wrong way round.
#:
#: ``cash_on_fulfillment`` is the one that must not be folded into the others.
#: It is a real obligation -- the buyer owes money and the order is live -- but
#: no chargeable object exists at Stripe, so counting it as a card payable would
#: corrupt the single number this taxonomy exists to make readable: how many
#: times the card rail actually produced something payable. It is recorded under
#: its own surface so the sum still closes and the card figure stays honest.
SURFACE_CHECKOUT_SESSION = "checkout_session"
SURFACE_PAYMENT_INTENT = "payment_intent"
SURFACE_CASH = "cash_on_fulfillment"

#: Extra fields a refusal may carry into the log. An allowlist, so a refusal
#: site added later cannot widen what is logged by accident -- it has to come
#: here, which is a review this file can host.
#:
#: Every entry is either a server-computed identifier, a server-computed amount,
#: or a decision the client is already shown. None of them is buyer-supplied
#: free text.
REFUSAL_SAFE_FIELDS = frozenset({
    "blocking_line_ids",
    "price_changed_line_ids",
    "transaction_ids",
    "trace_id",
    "total_cents",
    "retryable",
    "cta",
    "field",
    "provider_error",
})

#: Fields that must never be logged even though a refusal payload may contain
#: them. Listed explicitly, and separately from "merely not allowlisted", so the
#: emitted line can say ``denied=`` rather than ``dropped=`` and an operator can
#: tell "someone added a field" from "someone tried to log a credential".
#:
#: ``checkout_url`` is here because a Stripe Checkout Session URL embeds the
#: session's client secret -- a credential that looks like a link.
REFUSAL_DENIED_FIELDS = frozenset({
    "checkout_url",
    "url",
    "idempotency_key",
    "client_secret",
    "payment_method",
    "card",
    "address",
    "shipping_address",
    "billing_address",
    "email",
    "phone",
    "token",
    "secret",
})


def safe_refusal_extra(extra: Mapping[str, Any] | None) -> tuple[dict, list, list]:
    """Split a refusal's extras into (kept, denied keys, unknown keys).

    Pure, and separated from the emit so it can be tested directly against a
    payload containing something it must refuse -- which is a test that should
    not have to assert on a formatted log string.

    Returns key *names* for the two rejected groups and never their values, so
    the diagnostic itself cannot become the leak.
    """
    kept: dict = {}
    denied: list = []
    unknown: list = []
    for key, value in (extra or {}).items():
        name = str(key)
        if name.lower() in REFUSAL_DENIED_FIELDS:
            denied.append(name)
        elif name in REFUSAL_SAFE_FIELDS:
            kept[name] = value
        else:
            unknown.append(name)
    return kept, sorted(denied), sorted(unknown)


def attempted(*, buyer_user_id: int, seller_user_id: int, payment_mode: str,
              lane: str = "cart", has_idempotency_key: bool = False) -> None:
    """A checkout request arrived.

    ``has_idempotency_key`` is a boolean on purpose. The key itself is chosen by
    the client, so it is an arbitrary buyer-controlled string that could hold
    anything; whether one was *supplied* is the only part of it this log needs,
    and it is the part that explains a later ``CHECKOUT_IDEMPOTENCY_*`` line.

    Never raises -- see :func:`refused`.
    """
    try:
        LOGGER.info(
            "%s lane=%s buyer=%s seller=%s payment_mode=%s idempotency_key_present=%s",
            ATTEMPTED, lane, int(buyer_user_id or 0), int(seller_user_id or 0),
            str(payment_mode or "unset"), bool(has_idempotency_key),
        )
    except Exception:  # noqa: BLE001 - observability must not break a checkout
        LOGGER.exception("CHECKOUT_ATTEMPTED_EMIT_FAILED")


def refused(*, code: str, status: int, op: str = "",
            lane: str = "cart", extra: Mapping[str, Any] | None = None) -> None:
    """The server declined, with the code the client was handed.

    ``code`` is recorded verbatim and never checked against a list -- see the
    module docstring on why a second authority on this vocabulary would be a
    liability rather than a safeguard.

    ``op`` is which handler refused. It is passed in rather than read from
    Flask here so this function stays importable and testable without a request
    context; the caller is the one that already has one.

    Never raises. It is called from the funnel every refusal passes through, on
    a money path, so a defect in this line must not be able to turn a 409 the
    buyer could act on into a 500 they cannot.
    """
    try:
        kept, denied, unknown = safe_refusal_extra(extra)
        LOGGER.info(
            "%s op=%s lane=%s code=%s status=%s detail=%s denied=%s unknown=%s",
            REFUSED, str(op or "unknown"), lane, str(code or "UNSET"),
            int(status or 0), kept, ",".join(denied), ",".join(unknown),
        )
    except Exception:  # noqa: BLE001 - observability must not break a refusal
        LOGGER.exception("MARKETPLACE_CART_REFUSED_EMIT_FAILED op=%s", op)


def payable_created(*, surface: str, amount_cents: int, currency: str,
                    transaction_ids, buyer_user_id: int, seller_user_id: int,
                    provider_object_id: str = "", livemode: Any = None,
                    lane: str = "cart") -> None:
    """The buyer was handed an obligation: this request produced an order.

    Emitted at the handler's success returns rather than the instant Stripe
    accepts the create call, which is a deliberate choice. A session has existed
    and then been expired inside this handler -- that is the production incident
    this file's neighbours were written for -- and emitting on provider
    acceptance would record a payable for a request that went on to refuse the
    buyer. Emitting at the return keeps ``attempted = refused + payable +
    crash`` exactly true, and the already-logged
    ``STRIPE_SESSION_EXPIRED_AFTER_FAILURE``/``_ORPHANED`` lines are what tell
    the story of the object that existed in between.

    ``provider_object_id`` and ``livemode`` are blank for
    :data:`SURFACE_CASH`, which has no provider object. Blank rather than
    omitted, so every line of this event has the same shape.

    ``livemode`` is carried because it is the one field that distinguishes a
    real charge from a test one in a log an operator reads months later. It is
    the provider's own answer, not a local inference from configuration, so a
    deployment whose keys disagree with its intent is visible here rather than
    only in the Stripe dashboard.

    ``amount_cents`` is the server's computed total. The buyer never supplies an
    amount, and logging the server's number keeps this line usable as evidence
    for that.

    There is deliberately no parameter for the session URL. See the module
    docstring: it embeds a client secret.
    """
    try:
        try:
            ids = ",".join(str(int(t)) for t in (transaction_ids or []))
        except (TypeError, ValueError):
            ids = ""
        LOGGER.info(
            "%s lane=%s surface=%s provider_object=%s buyer=%s seller=%s "
            "amount_cents=%s currency=%s livemode=%s transaction_ids=%s",
            PAYABLE_CREATED, lane, str(surface or "unknown"),
            str(provider_object_id or ""), int(buyer_user_id or 0),
            int(seller_user_id or 0), int(amount_cents or 0),
            str(currency or "").lower(), livemode, ids,
        )
    except Exception:  # noqa: BLE001 - observability must not break a checkout
        LOGGER.exception("CHECKOUT_PAYABLE_EMIT_FAILED surface=%s", surface)
