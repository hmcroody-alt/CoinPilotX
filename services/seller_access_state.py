"""One authoritative answer to "may this account sell, and what should it see?".

Why this module exists
----------------------
Before it, every seller surface decided for itself. ``bot.py`` had
``approved_marketplace_seller_for_user`` on create-listing, an inline
``seller.get("status") != "approved"`` on edit-listing, nothing at all on
publish/pause/delete, and the Business Profile routes checked only that somebody
was logged in. The native client gated nothing: ``BusinessOsScreen`` pushed
straight to the Store dashboard and ``MarketplaceManagerScreen`` mounted the
Selling pane for anyone who tapped it. Four different opinions, one of which was
"no opinion", and a client that held none.

That is not a cosmetic inconsistency. In production right now there is one
approved seller and *four applications still in draft*. Those four accounts can
open a full Store dashboard today.

The authority
-------------
``marketplace_sellers.status`` — already documented in
``services/marketplace_card_capability.py`` as "the one authority on whether
someone may sell". This module does not introduce a second one. It reads that
column, joins the application row for the *pre-approval* states the seller row
cannot express, and returns a single structured verdict every caller shares.

Deliberately read from both rows, in that order:

* ``marketplace_sellers.status`` carries the *current standing* of an account
  that has one. An application can be approved and the seller later suspended;
  the seller row is what carries the suspension.
* ``marketplace_merchant_applications.status`` carries the *journey* of an
  account that has no seller row yet — draft, submitted, information requested.
  This is what tells a non-seller which screen to see.

Two axes that must not be conflated
-----------------------------------
``seller_approved`` decides access to Store and Marketplace Selling.
``card_payment_status`` decides whether a checkout may offer a card.

They are independent, and the ordering matters: an approved seller with no
Stripe account still gets the whole Store, and only sees ``SETUP_REQUIRED``
against card payments. Gating the Store on Stripe would be the same mistake in
the other direction — it would mean a seller had to hand over bank details
before they could so much as name their shop.

Fails closed
------------
Any unreadable row yields ``NO_APPLICATION`` with ``store_access`` false. The
cost of a wrong "no" is a seller who sees an application screen and complains.
The cost of a wrong "yes" is an unapproved account managing a storefront.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

from services import seller_lifecycle

# ---------------------------------------------------------------------------
# Access states. These are the *client-facing* names: one per distinct screen
# the app must show. They are a deliberate narrowing of seller_lifecycle's ten
# statuses, because several of those route to the same place.
# ---------------------------------------------------------------------------

NO_APPLICATION = "NO_APPLICATION"
DRAFT = "DRAFT"
SUBMITTED = "SUBMITTED"
UNDER_REVIEW = "UNDER_REVIEW"
MORE_INFORMATION_REQUIRED = "MORE_INFORMATION_REQUIRED"
APPROVED = "APPROVED"
DECLINED = "DECLINED"
SUSPENDED = "SUSPENDED"

ALL_ACCESS_STATES = (
    NO_APPLICATION, DRAFT, SUBMITTED, UNDER_REVIEW,
    MORE_INFORMATION_REQUIRED, APPROVED, DECLINED, SUSPENDED,
)

#: lifecycle status -> access state. ``resubmitted`` collapses into
#: UNDER_REVIEW because it is the same screen to the applicant: they have sent
#: it back and are waiting. ``withdrawn`` and ``expired`` collapse into
#: NO_APPLICATION because the honest next step for both is "apply".
_LIFECYCLE_TO_ACCESS = {
    seller_lifecycle.DRAFT: DRAFT,
    seller_lifecycle.SUBMITTED: SUBMITTED,
    seller_lifecycle.UNDER_REVIEW: UNDER_REVIEW,
    seller_lifecycle.RESUBMITTED: UNDER_REVIEW,
    seller_lifecycle.INFORMATION_REQUESTED: MORE_INFORMATION_REQUIRED,
    seller_lifecycle.APPROVED: APPROVED,
    seller_lifecycle.REJECTED: DECLINED,
    seller_lifecycle.SUSPENDED: SUSPENDED,
    seller_lifecycle.WITHDRAWN: NO_APPLICATION,
    seller_lifecycle.EXPIRED: NO_APPLICATION,
}

# ---------------------------------------------------------------------------
# Stripe readiness. Distinct vocabulary from the access states on purpose: if
# they shared words, a caller could compare the wrong pair and not notice.
# ---------------------------------------------------------------------------

CARD_SETUP_REQUIRED = "SETUP_REQUIRED"
CARD_SETUP_IN_PROGRESS = "SETUP_IN_PROGRESS"
CARD_ACTION_REQUIRED = "ACTION_REQUIRED"
CARD_UNDER_REVIEW = "UNDER_REVIEW"
CARD_READY = "READY"
CARD_RESTRICTED = "RESTRICTED"
#: The platform kill switch is off. Not a seller problem, and must not be
#: presented to a seller as one — there is nothing for them to do about it.
CARD_UNAVAILABLE = "UNAVAILABLE"

ALL_CARD_STATES = (
    CARD_SETUP_REQUIRED, CARD_SETUP_IN_PROGRESS, CARD_ACTION_REQUIRED,
    CARD_UNDER_REVIEW, CARD_READY, CARD_RESTRICTED, CARD_UNAVAILABLE,
)

#: Which card states the seller can act on. Drives whether the client shows a
#: "Set Up Payments" button or a passive status line.
_CARD_ACTIONABLE = {
    CARD_SETUP_REQUIRED, CARD_SETUP_IN_PROGRESS, CARD_ACTION_REQUIRED,
}

# Reason codes. Stable strings for the client to branch on; the human copy
# lives in the client so it can be translated.
REASON_NO_APPLICATION = "no_application"
REASON_APPLICATION_IN_PROGRESS = "application_in_progress"
REASON_AWAITING_REVIEW = "awaiting_review"
REASON_INFORMATION_REQUESTED = "information_requested"
REASON_DECLINED = "declined"
REASON_SUSPENDED = "suspended"
REASON_APPROVED = "approved"
REASON_UNREADABLE = "unreadable"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on", "t"}


def _row(cur, sql: str, params: tuple) -> Dict[str, Any]:
    cur.execute(sql, params)
    return dict(cur.fetchone() or {})


def _seller_row(cur, user_id: int) -> Dict[str, Any]:
    return _row(
        cur,
        "SELECT status, display_name, business_name, seller_type, "
        "verification_status FROM marketplace_sellers WHERE user_id=? LIMIT 1",
        (int(user_id),),
    )


def _application_row(cur, user_id: int) -> Dict[str, Any]:
    """The newest application for this user.

    ``ORDER BY id DESC`` rather than by ``updated_at``: timestamps here are
    TEXT and written by several code paths with different formats, so ordering
    on them sorts lexically and puts a 2026 row before a 2026-01 one. The
    autoincrement id is the only monotonic column on this table.
    """
    return _row(
        cur,
        "SELECT id, status, display_name, business_name "
        "FROM marketplace_merchant_applications WHERE user_id=? "
        "ORDER BY id DESC LIMIT 1",
        (int(user_id),),
    )


def _payout_row(cur, user_id: int) -> Dict[str, Any]:
    """Stripe Connect state. An absent table is not a verdict — see below."""
    try:
        return _row(
            cur,
            "SELECT connected_account_id, provider_account_id, onboarding_status, "
            "payouts_enabled, charges_enabled, missing_requirements_json "
            "FROM seller_payout_accounts WHERE user_id=? "
            "ORDER BY id DESC LIMIT 1",
            (int(user_id),),
        )
    except Exception as exc:  # noqa: BLE001
        logging.debug("SELLER_ACCESS_PAYOUT_UNREADABLE user=%s error=%s", user_id, exc)
        return {}


def _requirements_outstanding(row: Mapping[str, Any]) -> bool:
    import json

    for key in ("missing_requirements_json", "requirements_json"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 - malformed JSON is not "nothing due"
            return True
        if isinstance(parsed, dict):
            for bucket in ("currently_due", "past_due", "eventually_due", "missing"):
                if parsed.get(bucket):
                    return True
        elif isinstance(parsed, (list, tuple)) and parsed:
            return True
    return False


def card_payment_status(cur, user_id: int, *, seller_approved: bool) -> str:
    """Stripe readiness for one seller, as a state the UI can render.

    Mirrors the decision order in ``marketplace_card_capability._decide`` on
    purpose — that module decides whether a *checkout* may charge a card, this
    one decides what the *seller* is told about why. If they disagreed, a seller
    would read READY on their dashboard while buyers were refused at checkout.

    The one place they differ: this returns ``SETUP_REQUIRED`` where the
    capability module returns ``SELLER_NOT_APPROVED``, because an unapproved
    seller should be told about the approval, not about Stripe.
    """
    from services import marketplace_payment_pause

    if marketplace_payment_pause.marketplace_card_payments_paused():
        return CARD_UNAVAILABLE
    if not seller_approved:
        return CARD_SETUP_REQUIRED

    row = _payout_row(cur, user_id)
    account_id = str(
        row.get("connected_account_id") or row.get("provider_account_id") or ""
    ).strip()
    if not account_id:
        # No connected account at all. This is the state production is in, and
        # the state the checkout screen renders as "Temporarily Unavailable".
        # The seller's next step is to start onboarding.
        return CARD_SETUP_REQUIRED

    onboarding = str(row.get("onboarding_status") or "").strip().lower()
    if onboarding in {"restricted", "disabled", "rejected", "disconnected"}:
        return CARD_RESTRICTED
    if _requirements_outstanding(row):
        return CARD_ACTION_REQUIRED
    if onboarding in {"pending", "in_progress", "started"}:
        return CARD_SETUP_IN_PROGRESS

    charges = _truthy(row.get("charges_enabled"))
    payouts = _truthy(row.get("payouts_enabled"))
    if charges and payouts:
        return CARD_READY
    if charges or payouts:
        # Half-enabled is Stripe still deciding, not a seller error.
        return CARD_UNDER_REVIEW
    if onboarding in {"completed", "complete", "done"}:
        # Onboarding finished but neither capability granted: Stripe is
        # reviewing. Telling the seller to "set up payments" again would send
        # them round a loop they have already completed.
        return CARD_UNDER_REVIEW
    return CARD_SETUP_IN_PROGRESS


def get_seller_access_state(cur, user_id: Optional[int]) -> Dict[str, Any]:
    """The single verdict. Every seller surface, client and server, reads this.

    Takes a cursor rather than opening its own connection: this runs on the
    Business OS launch path and on every seller mutation, and a module that
    opens its own ``db()`` per call is how the Status rail came to hold 22
    connections against a pool of 8.
    """
    blank = _denied(NO_APPLICATION, REASON_NO_APPLICATION)
    if not user_id:
        return blank

    try:
        seller = _seller_row(cur, user_id)
        application = _application_row(cur, user_id)
    except Exception as exc:  # noqa: BLE001 - a read failure must refuse, not raise
        logging.warning("SELLER_ACCESS_UNREADABLE user=%s error=%s", user_id, exc)
        state = _denied(NO_APPLICATION, REASON_UNREADABLE)
        state["degraded"] = True
        return state

    seller_status = seller_lifecycle.normalize_status(seller.get("status")) if seller else ""
    app_status = seller_lifecycle.normalize_status(application.get("status")) if application else ""

    # Seller row wins when it carries a *standing* — approved or suspended.
    # Otherwise the application row describes where the user actually is.
    if seller_status == seller_lifecycle.SUSPENDED:
        access = SUSPENDED
    elif seller_status == seller_lifecycle.APPROVED:
        access = APPROVED
    elif app_status:
        access = _LIFECYCLE_TO_ACCESS.get(app_status, NO_APPLICATION)
    elif seller_status:
        access = _LIFECYCLE_TO_ACCESS.get(seller_status, NO_APPLICATION)
    else:
        access = NO_APPLICATION

    approved = access == APPROVED
    reason = {
        NO_APPLICATION: REASON_NO_APPLICATION,
        DRAFT: REASON_APPLICATION_IN_PROGRESS,
        SUBMITTED: REASON_AWAITING_REVIEW,
        UNDER_REVIEW: REASON_AWAITING_REVIEW,
        MORE_INFORMATION_REQUIRED: REASON_INFORMATION_REQUESTED,
        APPROVED: REASON_APPROVED,
        DECLINED: REASON_DECLINED,
        SUSPENDED: REASON_SUSPENDED,
    }[access]

    card_state = card_payment_status(cur, user_id, seller_approved=approved)

    state: Dict[str, Any] = {
        "seller_application_status": access,
        "seller_approved": approved,
        # Store and Selling share one boolean by construction. Two booleans is
        # how they drifted apart in the first place.
        "store_access": approved,
        "marketplace_selling_access": approved,
        "stripe_connect_status": card_state,
        "card_payment_status": card_state,
        "card_setup_actionable": card_state in _CARD_ACTIONABLE,
        "reason_code": reason,
        "application_id": application.get("id") if application else None,
        "lifecycle_status": app_status or seller_status or "",
        "degraded": False,
    }

    # A suspended seller keeps read access to existing obligations. Orders,
    # refunds and disputes are duties, not privileges, and locking someone out
    # of them strands their buyers.
    state["can_manage_existing_orders"] = approved or access == SUSPENDED
    return state


def _denied(access: str, reason: str) -> Dict[str, Any]:
    return {
        "seller_application_status": access,
        "seller_approved": False,
        "store_access": False,
        "marketplace_selling_access": False,
        "stripe_connect_status": CARD_SETUP_REQUIRED,
        "card_payment_status": CARD_SETUP_REQUIRED,
        "card_setup_actionable": True,
        "reason_code": reason,
        "application_id": None,
        "lifecycle_status": "",
        "can_manage_existing_orders": False,
        "degraded": False,
    }


def require_seller_access(cur, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """``None`` when the caller may manage seller-owned things, else a refusal body.

    Route helper. Returns the refusal *body* rather than raising so the caller
    keeps control of the response shape, and carries the full access state so
    the client can route straight to the right screen instead of having to make
    a second call to find out why it was refused.
    """
    state = get_seller_access_state(cur, user_id)
    if state.get("seller_approved"):
        return None
    return {
        "ok": False,
        "error": "Seller approval required.",
        "error_code": "seller_not_approved",
        "seller_access": state,
    }
