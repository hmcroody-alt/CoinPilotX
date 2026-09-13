"""Business OS — Marketplace BULK LISTING ACTIONS: one batch, one verdict each.

§38–§41. A seller ticks eighteen listings and taps Publish. This module owns
everything about that request except the SQL that moves the rows: what a valid
batch is, whether a given listing may be acted on, what a replay of the same
request means, and the shape of the answer.

    {"batch_id": ..., "action": "publish",
     "requested_count": 18, "successful_count": 14,
     "blocked_count": 4, "failed_count": 0,
     "results": [{"listing_id": 9, "outcome": "blocked", ...}, ...]}

Why a module and not a loop in the route
----------------------------------------
§38 forbids the obvious implementation — the client firing eighteen single-item
requests — for reasons that are not about performance. Eighteen requests have
eighteen outcomes and no batch: a seller who backgrounds the app after nine of
them has no way to ask what happened, a retry re-applies the nine that already
worked, and the "14 published, 4 blocked" summary has to be assembled on the
client out of whatever replies came back, which means the client is once again
deciding something the server knows.

Why the decision is here and the writes are in ``bot.py``
---------------------------------------------------------
The same split the client uses. ``block_reason`` is the whole eligibility rule
and it is a pure function of a listing row and a verdict, so it can be proven
against every state a listing can reach without a Flask app, a request context
or a database. The route keeps the ``UPDATE`` statements and the inventory
events, because those need ``bot.py``'s helpers and nothing about them is
subtle.

Three honesty rules
-------------------
* **Blocked is not failed.** "Blocked" means the server looked at the listing
  and it is not ready — the seller can go fix it, and the reason says how.
  "Failed" means the action could not be attempted at all. Collapsing the two
  gives a seller a number they cannot act on, and it is the failure §34 exists
  to prevent.
* **Publishability is read, never re-derived.** ``listing_readiness.evaluate``
  is the one authority (§5/§81). A second opinion computed here would diverge
  from the one the seller was shown on the row, and it would do it at the moment
  they are acting on eighteen things at once.
* **A spent idempotency key never acts twice.** The key is claimed *before* the
  writes, not after, so a client that retries through a timeout gets the first
  batch's answer rather than a second batch's side effects.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from services import db
from services.business_os.marketplace import listing_readiness as _readiness
from services.business_os.suppliers import pricing as _pricing

#: The actions a bulk request may carry. Unknown actions are not silently
#: accepted, because one that fell through to a no-op would report
#: ``successful_count`` for work nobody did.
#:
#: ``price`` is the first action that carries a *payload* — the rest of this
#: module was written for actions whose entire meaning is their name. See
#: :func:`normalize_request` and :func:`request_hash` for what that changes.
#: ``category`` is the second, and it needed no new mechanism: a payload in the
#: request hash, a per-row plan, and a block for "this row already says that".
ACTIONS = ("publish", "hide", "price", "category")

#: Actions that take a payload, and are meaningless without one.
PAYLOAD_ACTIONS = ("price", "category")

#: Which request key carries each payload action's settings.
#:
#: Each action reads its own key rather than sharing one generic ``settings``
#: object, so that "reprice these forty" and "re-file these forty" are not the
#: same request shape with the action field as the only thing telling them apart.
PAYLOAD_KEYS = {"price": "pricing_rule", "category": "category"}

#: The single-listing edit route's own column limit, mirrored rather than
#: re-chosen. A category a seller may set one at a time but not forty at a time
#: — or the reverse — is two answers to one question (§21).
CATEGORY_MAX = 80

#: The actions whose verdict follows from the listing row alone, so a list route
#: can attach one to every row it returns and the phone can grey the right rows
#: before asking anything.
#:
#: ``price`` is deliberately absent, and the absence is the honest answer. What
#: blocks a reprice depends on the *rule the seller has not chosen yet*: the same
#: listing is `PRICE_UNCHANGED` under cost+20% and a clean success under
#: cost+25%. Attaching a verdict computed with no rule yielded
#: ``{"code": "NO_PRICE_PROPOSAL"}`` on every listing in the store — a permanent,
#: authoritative-sounding block on a feature that works, which is worse than
#: silence because a client cannot tell it apart from a real one. Omitting the
#: key instead makes the client's existing "no verdict is not a yes" rule give
#: the right answer for free, and the real verdict comes from
#: :func:`build_price_plans` once there is a rule to compute it from.
PRECOMPUTED_ACTIONS = tuple(action for action in ACTIONS if action not in PAYLOAD_ACTIONS)

#: A ceiling on one request. Not a performance number — it is the largest set a
#: seller can be shown an honest preview of, and the largest we are willing to
#: move under a single idempotency key. A client with more rows than this must
#: split, which makes the batching visible to the seller rather than hiding a
#: 4,000-row transaction behind one tap.
MAX_BATCH = 200

#: Outcomes. Exhaustive and mutually exclusive; every requested id lands in
#: exactly one, which is what makes the three counts add up to the request.
SUCCEEDED = "succeeded"
BLOCKED = "blocked"
FAILED = "failed"

#: Reason codes for FAILED. Deliberately few: a failure is about the request,
#: not the product.
NOT_FOUND = "NOT_FOUND"

#: Statuses a listing can be published *from*. Mirrors the single submit route's
#: own gate, widened by the two states the resume route already treats as
#: republishable.
#:
#: Without this a seller who taps "select all" and publishes sends every live
#: listing in their store back to `pending_review` -- an action that reads as a
#: no-op, costs them their storefront until a moderator clears the queue, and is
#: reported as `successful_count` because the write did succeed. The empty
#: string is included because a legacy row can carry no status at all.
PUBLISHABLE_FROM = ("", "draft", "changes_requested", "rejected", "paused", "hidden")


class BatchError(Exception):
    """A request that cannot be attempted at all.

    Distinct from a per-listing failure. This is the whole batch being refused —
    a bad action name, an empty list, a key that means something else — and it
    carries the HTTP status the route should answer with.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- request validation ------------------------------------------------------


def settings_for(action: Any, body: Any) -> Any:
    """The settings this action takes, read out of the raw request body.

    Lives here rather than in the route because "which key belongs to which
    action" is half of the rule whose other half is :func:`normalize_request`,
    and splitting them is what let a real hole open: the route used to pick the
    key by action —

        settings = body.get("category") if action == "category" else body.get("pricing_rule")

    — which means a request naming a *different* action's key was not refused,
    it was **unread**. ``{"action": "hide", "category": {...}}`` looked for a
    ``pricing_rule``, found none, and became a plain hide: fourteen products the
    seller meant to re-file were pulled from sale instead, and the reply said
    succeeded fourteen times. The refusal below the payload actions in
    ``normalize_request`` was written for exactly that case and could never fire,
    because it only ever saw the one key the else-branch happened to read.

    So presence is checked across *every* payload key, not just the action's own.
    A key belonging to another action is a client that believes it is asking for
    something, and the only safe answer is to refuse the batch — the seller can
    be told the request was malformed, but they cannot be untold that forty live
    products were hidden.

    ``None`` counts as absent, because a JSON ``null`` is indistinguishable from
    an omitted key to anyone reading the body and should not be a different
    outcome. The payload actions then refuse the missing settings themselves,
    with the message that names the field.
    """
    if not isinstance(body, dict):
        return None
    mine = PAYLOAD_KEYS.get(action if isinstance(action, str) else "")
    foreign = [
        key for owner, key in PAYLOAD_KEYS.items()
        if key != mine and body.get(key) is not None
    ]
    if foreign:
        raise BatchError(
            "UNSUPPORTED_ACTION",
            "That bulk action does not take those settings.",
        )
    return body.get(mine) if mine else None


def normalize_request(
    action: Any, listing_ids: Any, idempotency_key: Any, payload: Any = None
) -> dict:
    """Coerce and check a raw request body, or refuse it.

    Duplicated ids are collapsed rather than rejected. A client that sends the
    same id twice has a bug, but the seller's intent is unambiguous and failing
    the whole batch over it would punish them for it. The collapse is visible:
    ``requested_count`` is the deduplicated count, and it always equals
    ``len(results)``.

    The ids are sorted, which matters for more than tidiness — the request hash
    is computed over this list, so the same selection sent in a different order
    is recognised as the same request rather than as a new one that publishes
    everything a second time.
    """
    if not isinstance(action, str) or action not in ACTIONS:
        raise BatchError(
            "UNSUPPORTED_ACTION",
            "That bulk action is not available.",
        )

    if not isinstance(listing_ids, (list, tuple)):
        raise BatchError("INVALID_LISTING_IDS", "Select at least one listing.")

    clean: list = []
    seen = set()
    for raw in listing_ids:
        # `bool` is an `int` in Python and `True` would become listing 1. A
        # client that sends a boolean here is confused about something, and
        # silently acting on listing 1 is the worst available answer.
        if isinstance(raw, bool):
            raise BatchError("INVALID_LISTING_IDS", "Select at least one listing.")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise BatchError("INVALID_LISTING_IDS", "Select at least one listing.")
        if value <= 0:
            raise BatchError("INVALID_LISTING_IDS", "Select at least one listing.")
        if value in seen:
            continue
        seen.add(value)
        clean.append(value)

    if not clean:
        raise BatchError("INVALID_LISTING_IDS", "Select at least one listing.")
    if len(clean) > MAX_BATCH:
        raise BatchError(
            "BATCH_TOO_LARGE",
            f"Select up to {MAX_BATCH} listings at a time.",
        )

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise BatchError("MISSING_IDEMPOTENCY_KEY", "Missing idempotency key.")
    key = idempotency_key.strip()
    if len(key) > 128:
        raise BatchError("MISSING_IDEMPOTENCY_KEY", "Missing idempotency key.")

    normalized = {"action": action, "listing_ids": sorted(clean), "idempotency_key": key}

    if action == "price":
        # Validated by the pricing engine rather than here. `pricing` is the one
        # authority for what a rule means (§21), and it is the same module the
        # dropship import path applies at the other end of the product's life —
        # a rule that is legal on import and illegal on reprice, or vice versa,
        # would be two opinions about one seller's pricing.
        #
        # MANUAL_PRICE is refused for a *bulk* request even though it is the
        # engine's legitimate default, because it is the one rule that names no
        # number: "price these forty listings manually" is not an instruction
        # the batch can carry out, and accepting it would end in forty
        # `successful` outcomes for forty unchanged prices.
        try:
            rule = _pricing.normalize_rule(payload)
        except _pricing.PricingRejected as err:
            raise BatchError("INVALID_PRICING_RULE", str(err))
        if rule.get("type") == _pricing.MANUAL_PRICE:
            raise BatchError(
                "INVALID_PRICING_RULE",
                "Choose how the new price should be worked out.",
            )
        normalized["payload"] = rule
    elif action == "category":
        normalized["payload"] = normalize_category(payload)
    elif payload is not None:
        # A payload sent with `publish` or `hide` is a client that thinks it is
        # asking for something. It is not — those actions ignore it — and the
        # seller would be told the batch succeeded at whatever they thought they
        # were also requesting.
        raise BatchError(
            "UNSUPPORTED_ACTION",
            "That bulk action does not take any settings.",
        )

    return normalized


def normalize_category(payload: Any) -> dict:
    """The category and subcategory a batch will write, or a refusal.

    Free text rather than an enum, because the single-listing edit route accepts
    free text and this has to be the same field (§21). There is no category
    vocabulary in this application to validate against; what makes a category
    unacceptable is decided downstream by ``marketplace_goods_policy`` and by a
    moderator, and a listing moved into a prohibited category goes back into the
    review queue on its way there — see the material-field rule below. Inventing
    a whitelist here would refuse categories the seller can set one row at a
    time, which is the divergence, not the fix.

    **Subcategory is cleared when it is not supplied.** A subcategory belongs to
    its parent: moving "Education / Crypto Basics" into "Home & Kitchen" and
    keeping the old subcategory leaves the listing filed under
    "Home & Kitchen / Crypto Basics", which no filter, breadcrumb or buyer can
    make sense of. Carrying it across was the first implementation and it is
    worse than dropping it, because the wrong pair is indistinguishable from a
    pair someone chose. A seller who wants a subcategory sends one.
    """
    if payload is None or not isinstance(payload, dict):
        raise BatchError("INVALID_CATEGORY", "Choose a category.")

    raw = payload.get("category")
    # A number or a bool here is a client bug, and `str()` would turn it into a
    # category named "True". Refused rather than coerced.
    if not isinstance(raw, str):
        raise BatchError("INVALID_CATEGORY", "Choose a category.")
    category = " ".join(raw.split())[:CATEGORY_MAX]
    if not category:
        raise BatchError("INVALID_CATEGORY", "Choose a category.")

    raw_sub = payload.get("subcategory")
    if raw_sub is not None and not isinstance(raw_sub, str):
        raise BatchError("INVALID_CATEGORY", "That subcategory is not valid.")
    subcategory = " ".join((raw_sub or "").split())[:CATEGORY_MAX]

    unexpected = set(payload) - {"category", "subcategory"}
    if unexpected:
        # Same rule as a payload on `publish`: a client sending a key this action
        # does not honour believes it is asking for something, and silence would
        # have the seller told the batch did it.
        raise BatchError(
            "INVALID_CATEGORY",
            "That bulk action only sets a category and subcategory.",
        )

    return {"category": category, "subcategory": subcategory}


def request_hash(action: str, listing_ids: Iterable[int], payload: Any = None) -> str:
    """A fingerprint of what the caller asked for.

    Exists so that reusing a key for a *different* request is caught. Without
    it, a client that recycles keys would receive the first batch's answer for
    the second batch's listings — the reply would name ids nobody asked about,
    and the rows the seller actually selected would never move.

    ``payload`` is part of the fingerprint because for ``price`` it is most of
    the request. Same key, same forty listings, "+10%" then "+25%" is *not* a
    replay, and treating it as one is the worst available failure here: the
    seller is handed the first batch's "40 repriced" summary, believes the
    second change landed, and their store keeps the old margin. Every price
    surface would agree with every other one and all of them would be wrong.

    It is omitted from the hashed document entirely when absent, rather than
    hashed as ``null``. Adding a key to this JSON changes the digest of every
    request that has ever been made, so a stored ``publish`` batch would stop
    matching its own replay and answer ``IDEMPOTENCY_KEY_CONFLICT`` — a client
    retrying through a timeout would be refused instead of served, and the
    breakage would land on rows already written by the first attempt.
    """
    document: dict = {"action": action, "listing_ids": sorted(int(i) for i in listing_ids)}
    if payload is not None:
        document["payload"] = payload
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# --- eligibility -------------------------------------------------------------


def price_proposal(rule: dict, cost_cents: Any, current_cents: Any) -> dict:
    """What this listing's new price would be, or why there will not be one.

    Pure, and the only place the bulk reprice decides anything. Returns either
    ``{"price_cents": n}`` or ``{"block": {...}}``.

    The arithmetic is `pricing.apply_rule`'s, not a copy of it. That module
    already returns ``None`` for a cost it cannot use, and translating that
    ``None`` into a *blocked outcome* rather than into a number is the whole job
    here — the brief's rule is that unknown cost is not zero cost, and the shape
    of that bug is a batch that quietly writes $0.00 onto every listing whose
    supplier row is missing and reports them all as successfully repriced. The
    seller finds out from the orders.
    """
    if cost_cents is None:
        return {
            "block": {
                "code": "UNKNOWN_COST",
                "reason": "No supplier cost — can't work out a price",
            }
        }

    price_cents = _pricing.apply_rule(rule, cost_cents)
    if price_cents is None:
        # Cost was known, so this is the rule and the cost together landing
        # outside what a price may be. Reported as its own reason: "we know your
        # cost and this rule still does not give a usable price" is a different
        # thing for the seller to fix than a missing cost.
        return {
            "block": {
                "code": "PRICE_OUT_OF_RANGE",
                "reason": "That rule doesn't give a usable price",
            }
        }
    if price_cents <= 0:
        # A rule can legally land on zero — COST_PLUS_PERCENT on a zero cost, for
        # one. Zero is not a price; it is a free product nobody chose to give
        # away, and `_has_price`/checkout would read it as unpriced anyway.
        return {
            "block": {
                "code": "PRICE_OUT_OF_RANGE",
                "reason": "That rule doesn't give a usable price",
            }
        }

    if current_cents is not None and int(current_cents) == price_cents:
        # Not a success, and this is the case most easily mistaken for one. The
        # write would be a no-op on the price column, but `price_label` is a
        # MATERIAL_FIELD: a live, approved listing that "changes" to the price it
        # already has is sent back to `pending_review` and off sale until a
        # moderator clears it. Repricing a store would take every listing already
        # at target off the shelf, and the summary would call it a success.
        return {
            "block": {
                "code": "PRICE_UNCHANGED",
                "reason": "Already at that price",
            }
        }

    return {"price_cents": price_cents}


def category_proposal(target: dict, listing: dict) -> dict:
    """What this row's filing becomes, or why it will not change.

    Pure, and shaped exactly like :func:`price_proposal` so that the route's
    preview and its write can share one decision for this action the way they
    already do for the other (§21/§34). Returns either
    ``{"category": str, "subcategory": str}`` or ``{"block": {...}}``.

    The unchanged case is a *block*, and it is the whole reason this function
    exists rather than the route writing the payload onto every selected row.
    ``category`` is a MATERIAL_FIELD: writing the value a listing already has
    sends a live, approved product back to ``pending_review`` and off sale until
    a moderator clears the queue. "Select all → Set category: Education" in a
    store that is mostly already Education would empty the storefront and be
    reported as a success, and the seller would have no way to tell that from
    the change they asked for. It is the same trap ``PRICE_UNCHANGED`` exists
    for, one column over.

    Both fields are compared, so re-filing "Education" under a new subcategory
    is a real change rather than a no-op — the pair is what the listing claims,
    not the parent alone.
    """
    category = str(target.get("category") or "")
    subcategory = str(target.get("subcategory") or "")
    current_category = " ".join(str(listing.get("category") or "").split())
    current_subcategory = " ".join(str(listing.get("subcategory") or "").split())

    if category == current_category and subcategory == current_subcategory:
        return {
            "block": {
                "code": "CATEGORY_UNCHANGED",
                "reason": "Already in that category",
            }
        }
    return {"category": category, "subcategory": subcategory}


def build_category_plans(rows: Iterable[dict], target: dict) -> dict:
    """Every row's new filing, keyed by listing id.

    The counterpart of :func:`build_price_plans`, and it exists for the same
    reason: the number — here, the pair — that the batch *blocks on* and the one
    it *writes* must be one object rather than two evaluations that agree today.
    """
    return {int(row.get("id") or 0): category_proposal(target, row) for row in rows}


def block_reason(
    listing: dict,
    action: str,
    verdict: Optional[dict] = None,
    proposal: Optional[dict] = None,
) -> Optional[dict]:
    """Why this listing will not move, or ``None`` if it will.

    ``verdict`` is ``listing_readiness.evaluate``'s output. It is a parameter
    rather than computed here so that a caller evaluating a page of listings
    computes each verdict once, and so that this function is provably free of a
    second opinion about publishability — there is nowhere for one to hide.

    Passing ``None`` means *no verdict was obtained*, and that is not the same
    as a verdict of yes. It blocks. Absence is not a clean bill of health, and
    the one place that rule is most expensive to break is a bulk publish, where
    the unexamined row goes live alongside seventeen examined ones.
    """
    status = str(listing.get("status") or "").strip().lower()

    if action == "hide":
        # Hiding is safe by construction: it removes a listing from buyers, and
        # the worst case of hiding something already hidden is nothing at all.
        # Nothing here should acquire a blocker without a concrete failure to
        # point at.
        if status in ("paused", "hidden"):
            return {"code": "ALREADY_HIDDEN", "reason": "Already hidden"}
        if status == "seller_deleted":
            return {"code": "DELETED", "reason": "Removed from your store"}
        return None

    if action == "price":
        if status == "seller_deleted":
            return {"code": "DELETED", "reason": "Removed from your store"}
        # Readiness is deliberately not consulted. A listing blocked from
        # publishing for a missing photo is exactly the kind of listing a seller
        # is entitled to reprice — refusing would make the store's unfinished
        # rows the only ones a bulk price change cannot reach, which is backwards.
        if proposal is None:
            # No proposal computed means nobody worked out what this row's new
            # price would be. Same rule as `verdict is None` below: absence is
            # not permission, and the unexamined row must not be written.
            return {"code": "NO_PRICE_PROPOSAL", "reason": "No price worked out"}
        return proposal.get("block")

    if action == "category":
        if status == "seller_deleted":
            return {"code": "DELETED", "reason": "Removed from your store"}
        # Readiness is not consulted, for the reason it is not consulted for a
        # reprice: an unfinished listing is exactly the kind a seller re-files,
        # and blocking would make the rows most in need of tidying the only ones
        # bulk cannot reach. Note that this is *also* how a listing gets fixed —
        # `MISSING_CATEGORY` is a publish blocker, so setting a category in bulk
        # is one of the few bulk actions that makes rows readier than it found
        # them.
        if proposal is None:
            return {"code": "NO_CATEGORY_PLAN", "reason": "No category worked out"}
        return proposal.get("block")

    if status == "seller_deleted":
        return {"code": "DELETED", "reason": "Removed from your store"}

    if status == "pending_review":
        return {"code": "ALREADY_SUBMITTED", "reason": "Already in review"}
    if status not in PUBLISHABLE_FROM:
        # "active", and anything a later migration adds. Blocked rather than
        # quietly skipped: a seller who selected it meant something by it, and
        # "already published" is the answer to what they meant.
        return {"code": "ALREADY_PUBLISHED", "reason": "Already published"}

    if verdict is None:
        return {"code": "NO_READINESS", "reason": "No readiness check yet"}

    if verdict.get("publishable"):
        return None

    blockers = list(verdict.get("blockers") or [])
    count = len(blockers)
    if count:
        return {
            "code": "NOT_READY",
            "reason": f"{count} thing{'' if count == 1 else 's'} left",
            "blockers": blockers,
        }
    # `publishable` false with an empty blocker list should be unreachable —
    # `evaluate` derives one from the other. Reported rather than treated as
    # eligible, because if the two ever disagree the safe reading of "not
    # publishable" is not publishable.
    return {"code": "NOT_READY", "reason": "Not ready to publish", "blockers": []}


def evaluate_rows(
    rows: Iterable[dict],
    action: str,
    media_by_listing: Optional[dict] = None,
    plans: Optional[dict] = None,
) -> list:
    """Decide every row in one pass, returning ``(row, block)`` pairs.

    The verdict is computed here, once per row, and only for ``publish`` —
    hiding does not consult readiness, and evaluating anyway would make a
    seller's ability to hide a broken listing depend on the health of the
    readiness engine.

    ``media_by_listing`` maps listing id to that listing's media rows, and it is
    not optional in practice even though the signature allows it. The seller's
    list route passes media into ``evaluate``; a listing with media rows but no
    ``cover_image_url`` is ready there. Omitting it here would make the same
    listing ready on the row the seller is looking at and ``NO_VALID_MEDIA`` in
    the batch that acts on it — the exact divergence between what a seller is
    shown and what the server does that this whole engine exists to end.

    ``plans`` is the per-row decision for whichever payload action this is —
    :func:`build_price_plans` for a reprice, :func:`build_category_plans` for a
    re-filing. It was named ``price_plans`` when there was one such action; the
    name is now the general one, because a second parameter for the second
    payload action would be two channels for one idea and a third would be
    three.
    """
    lookup = media_by_listing or {}
    plans = plans or {}
    decided = []
    for row in rows:
        listing_id = int(row.get("id") or 0)
        if action == "publish":
            verdict = _readiness.evaluate(row, media=lookup.get(listing_id))
        else:
            verdict = None
        decided.append((row, block_reason(row, action, verdict, plans.get(listing_id))))
    return decided


def build_price_plans(
    rows: Iterable[dict],
    rule: dict,
    cost_by_listing: Optional[dict] = None,
    current_cents_by_listing: Optional[dict] = None,
) -> dict:
    """Work out every row's new price once, keyed by listing id.

    Separate from :func:`evaluate_rows` so that the number the batch *blocks on*
    and the number it *writes* are the same object rather than two evaluations
    of the same rule. They would agree — `price_proposal` is pure — but the
    route would then hold two independent price derivations, which is the shape
    that eventually drifts. The caller computes this, hands it to
    ``evaluate_rows``, and reads ``price_cents`` back out of it for the write.
    """
    costs = cost_by_listing or {}
    currents = current_cents_by_listing or {}
    plans = {}
    for row in rows:
        listing_id = int(row.get("id") or 0)
        plans[listing_id] = price_proposal(
            rule, costs.get(listing_id), currents.get(listing_id)
        )
    return plans


# --- the answer --------------------------------------------------------------


def summarize(batch_id: str, action: str, results: list) -> dict:
    """The §39 contract.

    The three counts are derived from ``results`` rather than accumulated
    alongside it. A counter incremented next to a list is a second source of
    truth for the same fact, and the failure it produces — a summary that
    disagrees with its own detail — is the exact thing a seller cannot check.
    """
    counts = {SUCCEEDED: 0, BLOCKED: 0, FAILED: 0}
    for entry in results:
        outcome = entry.get("outcome")
        if outcome not in counts:
            raise ValueError(f"unknown outcome {outcome!r}")
        counts[outcome] += 1

    return {
        "batch_id": batch_id,
        "action": action,
        "requested_count": len(results),
        "successful_count": counts[SUCCEEDED],
        "blocked_count": counts[BLOCKED],
        "failed_count": counts[FAILED],
        "results": results,
    }


def result_entry(listing_id: int, outcome: str, **extra) -> dict:
    entry = {"listing_id": int(listing_id), "outcome": outcome}
    entry.update({k: v for k, v in extra.items() if v is not None})
    return entry


#: A preview outcome, and deliberately not :data:`SUCCEEDED`.
#:
#: A preview writes nothing, so every word it could borrow from the committed
#: vocabulary would be a lie about a row that did not move. ``summarize`` refuses
#: this value and ``summarize_preview`` refuses ``SUCCEEDED``, so the two shapes
#: cannot be produced by the same code path by accident.
WOULD_APPLY = "would_apply"


def summarize_preview(action: str, results: list) -> dict:
    """§34. What this batch *would* do, computed by the code that would do it.

    The reason this exists rather than the client working it out: a reprice
    verdict depends on the rule, so — unlike publish and hide — it cannot be
    attached to a listing row in advance. Without a preview the seller's only
    way to find out what "cost + 20%" does to their forty listings is to apply
    it to their forty listings.

    Three things are missing from this shape on purpose, and each one is a
    client-side bug that becomes loud instead of silent:

    * **No ``batch_id``.** Nothing was claimed and nothing can be replayed. A
      client that stores this id and later reports "batch mlb_… applied" would be
      naming a batch that never existed.
    * **No ``successful_count``.** A caller that renders "14 products updated"
      from a preview gets a ``KeyError``, not a confident wrong number. This is
      the single most important omission here.
    * **No side effects at all** — no claim, no ledger row, no key spent. The
      request still carries an idempotency key (it is validated exactly like any
      other, so a preview cannot be a way to skip validation) and simply never
      claims it. A seller who previews six rules before choosing one has spent
      nothing, and may send the sixth under the key they previewed with.
    """
    counts = {WOULD_APPLY: 0, BLOCKED: 0, FAILED: 0}
    for entry in results:
        outcome = entry.get("outcome")
        if outcome not in counts:
            # Catches exactly the confusion this vocabulary exists to prevent:
            # a committed `succeeded` entry reaching a preview summary.
            raise ValueError(f"unknown preview outcome {outcome!r}")
        counts[outcome] += 1

    return {
        "action": action,
        "preview": True,
        "requested_count": len(results),
        "eligible_count": counts[WOULD_APPLY],
        "blocked_count": counts[BLOCKED],
        "failed_count": counts[FAILED],
        "results": results,
    }


# --- idempotency -------------------------------------------------------------


def ensure_schema() -> None:
    """Create the batch ledger. Idempotent; safe at startup and in tests.

    Takes **no connection parameter**, unlike its siblings in this package, and
    that is deliberate. Handing a route's open connection to an ``ensure_schema``
    skips the commit, so the DDL rolls back while still holding a catalog lock —
    and the next connection to touch the table blocks until the route finishes.
    The shape is already loose in ~26 call sites in this repo. This one cannot
    join them, because there is nowhere to pass a connection to.
    """
    conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS marketplace_listing_batches (
                batch_id TEXT PRIMARY KEY,
                seller_user_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE (seller_user_id, idempotency_key)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def claim(conn, seller_user_id, normalized: dict) -> dict:
    """Take ownership of an idempotency key, or report who already has it.

    Returns one of:

    * ``{"state": "claimed", "batch_id": ...}`` — proceed and apply.
    * ``{"state": "replayed", "response": {...}}`` — the identical request
      already completed; hand back exactly what it answered.

    and raises ``BatchError`` when the key is spent on something else, or when
    an identical request is still in flight.

    The key is scoped to the seller. Two clients generating the same UUID is
    improbable; one seller receiving another seller's batch summary because of
    it is not a risk worth taking for a scope that costs nothing.

    The claim is written **before** any listing is touched. The obvious order —
    apply, then record — leaves a window where a client that times out and
    retries gets a second batch of side effects, which for publish means a
    second review submission for every row.
    """
    key = normalized["idempotency_key"]
    digest = request_hash(
        normalized["action"], normalized["listing_ids"], normalized.get("payload")
    )
    seller = str(seller_user_id)

    existing = conn.execute(
        "SELECT batch_id, request_hash, response_json FROM marketplace_listing_batches "
        "WHERE seller_user_id=? AND idempotency_key=? LIMIT 1",
        (seller, key),
    ).fetchone()

    if existing is not None:
        row = dict(existing)
        if row["request_hash"] != digest:
            raise BatchError(
                "IDEMPOTENCY_KEY_CONFLICT",
                "That request has already been used for different listings.",
                409,
            )
        if not row["response_json"]:
            # Claimed but not finished. Answering "done" here would be a lie
            # about work still in progress, and answering by re-applying would
            # defeat the claim. The honest answer is that it is not ready yet.
            raise BatchError(
                "BATCH_IN_PROGRESS",
                "That bulk action is still running.",
                409,
            )
        return {"state": "replayed", "response": json.loads(row["response_json"])}

    batch_id = "mlb_" + uuid.uuid4().hex
    # ON CONFLICT DO NOTHING rather than a bare INSERT: two concurrent identical
    # requests both read no row above, and the loser of this race must not
    # poison a PostgreSQL transaction with a constraint violation.
    cursor = conn.execute(
        "INSERT INTO marketplace_listing_batches "
        "(batch_id, seller_user_id, idempotency_key, request_hash, action, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (batch_id, seller, key, digest, normalized["action"], _utc_now_iso()),
    )
    if cursor.rowcount != 1:
        raise BatchError(
            "BATCH_IN_PROGRESS",
            "That bulk action is still running.",
            409,
        )
    return {"state": "claimed", "batch_id": batch_id}


def finalize(conn, batch_id: str, response: dict) -> None:
    """Record the answer against the claim, so a retry replays it."""
    conn.execute(
        "UPDATE marketplace_listing_batches SET response_json=?, completed_at=? WHERE batch_id=?",
        (json.dumps(response, default=str), _utc_now_iso(), batch_id),
    )
