"""Business OS — Advertising: canonical notification emission.

Thin, typed adapters over the EXISTING notification system
(``services.notification_orchestrator.send_user_alert``) — this module builds no
competing delivery path, no second queue, no side table. It only:

  1. turns an advertising lifecycle/billing fact into a canonical alert
     (category + title + body + deep link + priority), and
  2. hands it to the orchestrator, which owns preferences, rate-limiting, channel
     fan-out, and logging.

Two invariants:

  * **Never breaks the caller.** A notification is a side effect of a money/lifecycle
    decision, never a precondition. Every emit is wrapped so a delivery/import failure
    returns ``{"ok": False, ...}`` instead of raising into billing or review code.
  * **Server-derived content only.** Titles/bodies/deep links are built here from the
    canonical ids the caller passes; no client string is echoed into an alert.

``build_notification`` is a pure function (category/title/body/data/deep_link/priority)
so content is unit-testable without delivering anything. ``set_sender`` swaps the
delivery function for tests.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


# Canonical advertising notification categories (namespaced so they never collide
# with existing app categories).
CATEGORY = "advertising"

# Deep-link paths (path-style, matching the app's existing "/pulse/..." convention).
def _campaign_link(campaign_id: Any) -> str:
    return f"/ads/campaigns/{campaign_id}"


def _creative_link(campaign_id: Any, creative_id: Any) -> str:
    return f"/ads/campaigns/{campaign_id}/creatives/{creative_id}"


def _billing_link(campaign_id: Any) -> str:
    return f"/ads/campaigns/{campaign_id}/billing"


def _account_link() -> str:
    return "/ads/account"


def _cents(amount: Any) -> str:
    try:
        return f"${int(amount) / 100:.2f}"
    except Exception:
        return "your balance"


# --- notification-type registry ---------------------------------------------
# Each entry maps a canonical type to (priority, builder). The builder returns
# (title, body, deep_link) from the server-supplied context.
def _b_campaign_approved(ctx):
    return ("Campaign approved",
            "Your campaign is approved and ready to run.",
            _campaign_link(ctx.get("campaign_id")))


def _b_campaign_rejected(ctx):
    reason = ctx.get("reason") or "See the review notes for details."
    return ("Campaign not approved",
            f"Your campaign was not approved. {reason}",
            _campaign_link(ctx.get("campaign_id")))


def _b_creative_approved(ctx):
    return ("Ad creative approved",
            "A creative in your campaign passed review.",
            _creative_link(ctx.get("campaign_id"), ctx.get("creative_id")))


def _b_creative_rejected(ctx):
    reason = ctx.get("reason") or "See the review notes for details."
    return ("Ad creative not approved",
            f"A creative in your campaign was not approved. {reason}",
            _creative_link(ctx.get("campaign_id"), ctx.get("creative_id")))


def _b_campaign_activated(ctx):
    return ("Campaign is live",
            "Your campaign is now active and eligible to be delivered.",
            _campaign_link(ctx.get("campaign_id")))


def _b_campaign_paused(ctx):
    reason = ctx.get("reason") or ""
    tail = f" {reason}" if reason else ""
    return ("Campaign paused",
            f"Your campaign has been paused.{tail}",
            _campaign_link(ctx.get("campaign_id")))


def _b_budget_approaching(ctx):
    remaining = _cents(ctx.get("remaining_cents"))
    pct = ctx.get("pct_spent")
    pct_txt = f" ({pct}% of budget spent)" if pct is not None else ""
    return ("Budget almost used up",
            f"Your campaign is close to its budget{pct_txt}. "
            f"About {remaining} remains before delivery stops.",
            _billing_link(ctx.get("campaign_id")))


def _b_budget_exhausted(ctx):
    return ("Budget exhausted",
            "Your campaign has spent its budget and has stopped delivering. "
            "Add funds to resume.",
            _billing_link(ctx.get("campaign_id")))


def _b_billing_failure(ctx):
    reason = ctx.get("reason") or "A billing charge could not be completed."
    return ("Billing issue on your campaign",
            f"{reason} Delivery may be affected until this is resolved.",
            _billing_link(ctx.get("campaign_id")))


def _b_account_restricted(ctx):
    reason = ctx.get("reason") or "Your advertising account has been restricted."
    return ("Advertising account restricted",
            f"{reason} Some actions are unavailable until this is resolved.",
            _account_link())


_REGISTRY: dict = {
    "campaign_approved": ("normal", _b_campaign_approved),
    "campaign_rejected": ("high", _b_campaign_rejected),
    "creative_approved": ("normal", _b_creative_approved),
    "creative_rejected": ("high", _b_creative_rejected),
    "campaign_activated": ("normal", _b_campaign_activated),
    "campaign_paused": ("high", _b_campaign_paused),
    "budget_approaching": ("normal", _b_budget_approaching),
    "budget_exhausted": ("high", _b_budget_exhausted),
    "billing_failure": ("high", _b_billing_failure),
    "account_restricted": ("high", _b_account_restricted),
}


# --- pure builder -----------------------------------------------------------
def build_notification(notif_type: str, **ctx) -> dict:
    """Build the canonical alert payload for a type (no delivery). Raises ValueError
    for an unknown type so a typo is caught, not silently swallowed."""
    entry = _REGISTRY.get(notif_type)
    if entry is None:
        raise ValueError(f"Unknown advertising notification type: {notif_type!r}")
    priority, builder = entry
    title, body, deep_link = builder(ctx)
    data = {
        "notif_type": notif_type,
        "deep_link": deep_link,
        "campaign_id": ctx.get("campaign_id"),
    }
    if ctx.get("creative_id") is not None:
        data["creative_id"] = ctx.get("creative_id")
    return {
        "category": CATEGORY,
        "notif_type": notif_type,
        "title": title,
        "body": body,
        "deep_link": deep_link,
        "priority": priority,
        "data": data,
    }


# --- delivery seam (swappable for tests) ------------------------------------
_SENDER: Optional[Callable] = None


def set_sender(fn: Optional[Callable]) -> None:
    """Override the delivery function (tests). Pass None to restore the orchestrator."""
    global _SENDER
    _SENDER = fn


def _deliver(user_id, payload) -> dict:
    """Send via the injected sender, else the canonical orchestrator. Import of the
    orchestrator is lazy so this module loads even where it is unavailable."""
    sender = _SENDER
    if sender is None:
        from services import notification_orchestrator as _orch  # lazy
        sender = _orch.send_user_alert
    return sender(
        user_id, payload["category"], payload["title"], payload["body"],
        data=payload["data"], priority=payload["priority"])


def emit(advertiser_user_id: Any, notif_type: str, **ctx) -> dict:
    """Build + deliver one advertising notification. NEVER raises into the caller:
    a delivery/import error is captured and returned as ``ok=False``."""
    try:
        payload = build_notification(notif_type, **ctx)
    except ValueError as exc:
        return {"ok": False, "status": "invalid_type", "error": str(exc)}
    if advertiser_user_id in (None, "", 0, "0"):
        return {"ok": False, "status": "no_recipient", "notif_type": notif_type}
    try:
        result = _deliver(advertiser_user_id, payload)
        return {"ok": True, "notif_type": notif_type, "delivery": result,
                "deep_link": payload["deep_link"]}
    except Exception as exc:  # noqa: BLE001 — notifications never break the caller
        return {"ok": False, "status": "delivery_error", "notif_type": notif_type,
                "error": str(exc)[:300]}


# --- typed convenience wrappers ---------------------------------------------
def notify_campaign_approved(advertiser_user_id, campaign_id):
    return emit(advertiser_user_id, "campaign_approved", campaign_id=campaign_id)


def notify_campaign_rejected(advertiser_user_id, campaign_id, reason=None):
    return emit(advertiser_user_id, "campaign_rejected", campaign_id=campaign_id,
                reason=reason)


def notify_creative_approved(advertiser_user_id, campaign_id, creative_id):
    return emit(advertiser_user_id, "creative_approved", campaign_id=campaign_id,
                creative_id=creative_id)


def notify_creative_rejected(advertiser_user_id, campaign_id, creative_id, reason=None):
    return emit(advertiser_user_id, "creative_rejected", campaign_id=campaign_id,
                creative_id=creative_id, reason=reason)


def notify_campaign_activated(advertiser_user_id, campaign_id):
    return emit(advertiser_user_id, "campaign_activated", campaign_id=campaign_id)


def notify_campaign_paused(advertiser_user_id, campaign_id, reason=None):
    return emit(advertiser_user_id, "campaign_paused", campaign_id=campaign_id,
                reason=reason)


def notify_budget_approaching(advertiser_user_id, campaign_id, *, remaining_cents=None,
                              pct_spent=None):
    return emit(advertiser_user_id, "budget_approaching", campaign_id=campaign_id,
                remaining_cents=remaining_cents, pct_spent=pct_spent)


def notify_budget_exhausted(advertiser_user_id, campaign_id):
    return emit(advertiser_user_id, "budget_exhausted", campaign_id=campaign_id)


def notify_billing_failure(advertiser_user_id, campaign_id, reason=None):
    return emit(advertiser_user_id, "billing_failure", campaign_id=campaign_id,
                reason=reason)


def notify_account_restricted(advertiser_user_id, reason=None, *, campaign_id=None):
    return emit(advertiser_user_id, "account_restricted", campaign_id=campaign_id,
                reason=reason)
