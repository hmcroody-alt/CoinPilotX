"""One answer to "can this checkout take a card?", and one place it is decided.

Before this module the question was asked in three places — ``bot.py``'s
checkout view, the cart route and the offers route — and each one asked only
half of it. They all consulted the global pause and none of them consulted the
seller. With the pause hardcoded on that was harmless, because no card checkout
could start regardless. The moment the pause became a flag it stopped being
harmless: turning the flag on would have opened the rail for every seller at
once, including sellers with no connected account, no ``charges_enabled``, and
no way to be paid what they had just been allowed to collect.

So the global flag is the outer gate and this is the inner one. Both must pass.

Three properties this file exists to hold:

1. **One evaluation order, applied everywhere.** The checks are ordered, and the
   first failure is the answer. A caller cannot reorder them, skip one, or get a
   different verdict than another caller would for the same seller.

2. **It never raises.** A checkout must not 500 because a capability read
   failed. Every unexpected failure resolves to ``STRIPE_UNAVAILABLE``, which is
   a refusal — the failure mode is "no card today", never "charge anyway".

3. **A buyer is never told a seller's business.** ``SELLER_NOT_APPROVED`` and
   ``STRIPE_REQUIREMENTS_DUE`` are facts about the seller's account, and the
   person trying to buy a lamp is not entitled to them. ``buyer_view`` collapses
   every seller-state reason to one opaque code. The detailed codes are for the
   seller's own surfaces, for support, and for logs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from services import marketplace_payment_pause

# --------------------------------------------------------------------------- #
# Reason codes
# --------------------------------------------------------------------------- #

AVAILABLE = "AVAILABLE"

FEATURE_DISABLED = "FEATURE_DISABLED"
STRIPE_UNAVAILABLE = "STRIPE_UNAVAILABLE"
SELLER_NOT_APPROVED = "SELLER_NOT_APPROVED"
STRIPE_NOT_CONNECTED = "STRIPE_NOT_CONNECTED"
STRIPE_REQUIREMENTS_DUE = "STRIPE_REQUIREMENTS_DUE"
CARD_CAPABILITY_DISABLED = "CARD_CAPABILITY_DISABLED"
PAYOUTS_DISABLED = "PAYOUTS_DISABLED"
LISTING_INELIGIBLE = "LISTING_INELIGIBLE"
INVENTORY_UNAVAILABLE = "INVENTORY_UNAVAILABLE"

#: The order the checks run in. Also the order of severity: a platform-wide
#: refusal outranks a seller one, which outranks a listing one, because telling
#: a seller to finish onboarding while the rail is switched off platform-wide
#: would send them to fix something that is not what is stopping them.
EVALUATION_ORDER = (
    FEATURE_DISABLED,
    STRIPE_UNAVAILABLE,
    SELLER_NOT_APPROVED,
    STRIPE_NOT_CONNECTED,
    STRIPE_REQUIREMENTS_DUE,
    CARD_CAPABILITY_DISABLED,
    PAYOUTS_DISABLED,
    LISTING_INELIGIBLE,
    INVENTORY_UNAVAILABLE,
)

#: Reasons that describe the *seller's* account rather than the platform or the
#: listing. A buyer never sees these; ``buyer_view`` replaces them.
SELLER_PRIVATE_REASONS = frozenset({
    SELLER_NOT_APPROVED,
    STRIPE_NOT_CONNECTED,
    STRIPE_REQUIREMENTS_DUE,
    CARD_CAPABILITY_DISABLED,
    PAYOUTS_DISABLED,
})

#: What the seller is told, and what they can do about it. Every one of these
#: names an action or explicitly says there is none, because a seller who cannot
#: take card payments and is not told why will open a support ticket.
SELLER_MESSAGES = {
    AVAILABLE: "Card payments are available for this listing.",
    FEATURE_DISABLED: (
        "Card payments are not switched on yet. This is a PulseSoc setting, not "
        "anything about your account — there is nothing for you to fix."
    ),
    STRIPE_UNAVAILABLE: (
        "We can't confirm your payment status right now. Cash, local pickup and "
        "in-person payment are unaffected. Try again shortly."
    ),
    SELLER_NOT_APPROVED: (
        "Your seller application hasn't been approved yet. Card payments open "
        "once it is."
    ),
    STRIPE_NOT_CONNECTED: (
        "You haven't finished connecting a payout account. Card payments need "
        "somewhere to send the money."
    ),
    STRIPE_REQUIREMENTS_DUE: (
        "Stripe still needs information from you before you can take card "
        "payments. Open your payout settings to see what's outstanding."
    ),
    CARD_CAPABILITY_DISABLED: (
        "Stripe hasn't enabled card payments on your account yet. This usually "
        "clears on its own once verification finishes."
    ),
    PAYOUTS_DISABLED: (
        "Payouts are disabled on your account, so we can't take card payments we "
        "wouldn't be able to pay out to you."
    ),
    LISTING_INELIGIBLE: "This listing isn't set up to take card payments.",
    INVENTORY_UNAVAILABLE: "This listing is out of stock.",
}

#: What a buyer is told. Every seller-private reason lands on the same sentence,
#: which is also the sentence the cash lane has always used.
_BUYER_FALLBACK = marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_MESSAGE

BUYER_MESSAGES = {
    AVAILABLE: SELLER_MESSAGES[AVAILABLE],
    INVENTORY_UNAVAILABLE: "This item is out of stock.",
}


class _Unreadable(Exception):
    """A capability fact could not be read. Resolves to a refusal, never a pass."""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "t", "on"}


def _requirements_outstanding(row: Mapping[str, Any]) -> bool:
    """Does Stripe still want something from this seller?

    Reads both column spellings. ``missing_requirements_json`` is the original;
    ``requirements_json`` was added later by ``add_columns_if_missing`` and is
    what the ``account.updated`` webhook writes. A row can carry either, and
    checking only one of them would read a seller with outstanding requirements
    as clear.
    """
    for column in ("requirements_json", "missing_requirements_json"):
        raw = row.get(column)
        if raw in (None, "", b""):
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (TypeError, ValueError):
            # Unparseable is not the same as empty. Something was written here,
            # and we cannot show that it was nothing, so treat it as outstanding.
            return True
        if isinstance(parsed, dict):
            # Stripe's shape: currently_due / past_due / eventually_due lists.
            for key in ("currently_due", "past_due", "errors"):
                if parsed.get(key):
                    return True
            continue
        if parsed:
            return True
    return False


def _seller_row(cur, seller_user_id: int) -> dict:
    cur.execute(
        "SELECT connected_account_id, provider_account_id, charges_enabled, "
        "payouts_enabled, onboarding_status, requirements_json, "
        "missing_requirements_json FROM seller_payout_accounts WHERE user_id=? "
        "ORDER BY updated_at DESC, id DESC LIMIT 1",
        (seller_user_id,),
    )
    return dict(cur.fetchone() or {})


def _canonical_row(cur, connected_account_id: str) -> dict:
    """What Stripe last said about this account, from the canonical projection.

    ``seller_payout_accounts`` (read above) is the legacy table; the
    authoritative record of Stripe's own words is ``connect_account_state``,
    written by ``services.business_os.payments.connect_accounts`` from both the
    ``account.updated`` webhook and explicit server-side refreshes. The two are
    kept equal by one sync path, and this second read is what makes that
    property *enforced* rather than merely intended: if they ever disagree, the
    caller below takes the more restrictive answer, so no stale column in the
    legacy table can open the card rail on its own.

    Read through the caller's cursor so it sees the same transaction it is
    about to charge in, and so a checkout costs no extra pooled connection.

    Returns ``{}`` — "no opinion" — when the projection has nothing for this
    account, which is also what an absent table looks like. Failing open *here*
    is not a permissive default: the legacy checks have already run and already
    refused if they should, so no opinion is exactly today's behaviour.
    """
    try:
        cur.execute(
            "SELECT charges_enabled, payouts_enabled, disabled_reason, "
            "requirements_json FROM connect_account_state "
            "WHERE connected_account_id=? LIMIT 1",
            (str(connected_account_id),),
        )
        return dict(cur.fetchone() or {})
    except Exception as exc:  # noqa: BLE001 - absent table is not a verdict
        logging.debug(
            "MARKETPLACE_CARD_CANONICAL_STATE_UNREADABLE account=%s error=%s",
            connected_account_id, exc,
        )
        return {}


def _seller_approved(cur, seller_user_id: int) -> bool:
    """``marketplace_sellers.status`` is the one authority on whether someone may sell.

    Deliberately not read from the application row: an application can be
    approved and the seller later suspended, and it is the seller record that
    carries that.
    """
    cur.execute(
        "SELECT status FROM marketplace_sellers WHERE user_id=? LIMIT 1",
        (seller_user_id,),
    )
    row = dict(cur.fetchone() or {})
    return str(row.get("status") or "").strip().lower() == "approved"


def _stripe_configured() -> bool:
    from services import payment_provider

    return bool(payment_provider.provider_status().get("secret_key_loaded"))


def _decide(cur, seller_user_id: int, listing: Mapping[str, Any] | None,
            requested_quantity: int | None) -> str:
    if marketplace_payment_pause.marketplace_card_payments_paused():
        return FEATURE_DISABLED

    try:
        if not _stripe_configured():
            return STRIPE_UNAVAILABLE
        if not _seller_approved(cur, seller_user_id):
            return SELLER_NOT_APPROVED
        row = _seller_row(cur, seller_user_id)
    except Exception as exc:  # noqa: BLE001 - a read failure must refuse, not raise
        raise _Unreadable(str(exc)) from exc

    account_id = str(
        row.get("connected_account_id") or row.get("provider_account_id") or ""
    ).strip()
    if not account_id:
        return STRIPE_NOT_CONNECTED
    if str(row.get("onboarding_status") or "").strip().lower() in {
        "restricted", "disabled", "rejected", "disconnected"
    }:
        return STRIPE_REQUIREMENTS_DUE
    if _requirements_outstanding(row):
        return STRIPE_REQUIREMENTS_DUE
    if not _truthy(row.get("charges_enabled")):
        return CARD_CAPABILITY_DISABLED
    if not _truthy(row.get("payouts_enabled")):
        return PAYOUTS_DISABLED

    # The legacy row says yes. Ask the canonical projection the same questions
    # and let it veto. These repeat the four checks above deliberately: the two
    # tables are kept equal by one sync path, so agreeing is the normal case and
    # this block normally changes nothing. It exists for the case the sync path
    # missed — an onboarding write that never got a webhook, a mirror UPDATE
    # that matched no row, a column added to one table and not the other. The
    # reason codes are the same ones, so the verdict a seller is shown does not
    # depend on which table noticed.
    #
    # Only ever a refusal. The canonical row cannot promote a "no" from the
    # legacy row into a "yes", because both must pass.
    canonical = _canonical_row(cur, account_id)
    if canonical:
        if str(canonical.get("disabled_reason") or "").strip():
            return STRIPE_REQUIREMENTS_DUE
        if _requirements_outstanding(canonical):
            return STRIPE_REQUIREMENTS_DUE
        if not _truthy(canonical.get("charges_enabled")):
            return CARD_CAPABILITY_DISABLED
        if not _truthy(canonical.get("payouts_enabled")):
            return PAYOUTS_DISABLED

    if listing is not None:
        status = str(listing.get("status") or "").strip().lower()
        if status and status not in {"active", "published", "live"}:
            return LISTING_INELIGIBLE
        stock = listing.get("quantity_available", listing.get("stock_quantity"))
        if stock is not None:
            try:
                available = int(stock)
            except (TypeError, ValueError):
                available = 0
            wanted = max(1, int(requested_quantity or 1))
            if available < wanted:
                return INVENTORY_UNAVAILABLE

    return AVAILABLE


def evaluate(cur, *, seller_user_id: Any, listing: Mapping[str, Any] | None = None,
             requested_quantity: Any = None) -> dict:
    """The authoritative verdict. Never raises.

    ``cur`` is a cursor rather than a connection so a checkout route can ask
    this inside the transaction it is already holding, and see the same seller
    state it is about to charge against.
    """
    try:
        seller_id = int(seller_user_id or 0)
    except (TypeError, ValueError):
        seller_id = 0

    if seller_id <= 0:
        reason = LISTING_INELIGIBLE
    else:
        try:
            reason = _decide(cur, seller_id, listing, requested_quantity)
        except _Unreadable as exc:
            logging.warning(
                "MARKETPLACE_CARD_CAPABILITY_READ_FAILED seller=%s error=%s",
                seller_id, exc,
            )
            reason = STRIPE_UNAVAILABLE
        except Exception as exc:  # noqa: BLE001 - belt and braces; still a refusal
            logging.warning(
                "MARKETPLACE_CARD_CAPABILITY_FAILED seller=%s error=%s", seller_id, exc
            )
            reason = STRIPE_UNAVAILABLE

    available = reason == AVAILABLE
    return {
        "card_payments_available": available,
        "reason_code": reason,
        "message": SELLER_MESSAGES[reason],
        "badge": None if available else marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_BADGE,
    }


def buyer_view(decision: Mapping[str, Any]) -> dict:
    """The same verdict, with the seller's account state removed.

    The verdict itself is identical — a buyer blocked here is blocked for the
    same reason — but the *reason* is replaced. Whether a seller finished their
    Stripe onboarding is not a buyer's business, and a code the buyer's client
    could log or display would make it one.
    """
    reason = str(decision.get("reason_code") or STRIPE_UNAVAILABLE)
    if reason in SELLER_PRIVATE_REASONS:
        reason = marketplace_payment_pause.MARKETPLACE_CARD_UNAVAILABLE_CODE
    return {
        "card_payments_available": bool(decision.get("card_payments_available")),
        "reason_code": reason,
        "message": BUYER_MESSAGES.get(reason, _BUYER_FALLBACK),
        "badge": decision.get("badge"),
    }
