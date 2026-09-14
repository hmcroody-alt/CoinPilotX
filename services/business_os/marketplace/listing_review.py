"""Business OS — Marketplace REVIEW AUTHORITY: one verdict rule, one batch shape.

§1. What may happen to a listing under review, and why it may not, decided in
one place. Before this module the whole rule lived inline in the body of
``/admin/marketplace-command`` — a Flask route that also renders an HTML table.
That was survivable while a reviewer could only act on one listing at a time.
It stops being survivable the moment a second caller needs the same decision,
because the only way to add a bulk endpoint over an inline rule is to write the
rule again, and then "can this be approved" has two answers that drift.

Which is the actual §1 defect. Not that a review queue was missing — one exists
— but that the authority behind it was not addressable.

The three axes, kept apart
--------------------------
``review_state`` is the moderator's verdict. ``status`` is the merchant's
release. ``publication`` is what a buyer can reach, and it is a *derived*
answer owned by ``marketplace_listing_lifecycle``: five conditions, only two of
which a reviewer controls. Approving is therefore not publishing, and this
module never claims otherwise — :func:`publication_readback` asks the lifecycle
rules after the write and reports what it finds (§37).

Blocked is not failed
---------------------
Inherited verbatim from :mod:`listing_batch`, whose vocabulary and summary
shape this module reuses rather than restates. "Blocked" means the server
looked and refused: a reason the reviewer can read and act on. "Failed" means
the action could not be attempted. A batch that collapses the two reports a
number nobody can do anything with.

Why a second batch ledger
-------------------------
``marketplace_listing_batches`` is keyed ``UNIQUE (seller_user_id,
idempotency_key)``. Review batches are scoped to the *reviewer*, and both ids
are user ids drawn from the same sequence — so an admin who also sells would
share one key namespace between two unrelated features, and a collision would
hand them one feature's summary in answer to the other's request. A separate
table costs one ``CREATE TABLE IF NOT EXISTS`` and removes the whole class.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from services import db
from services import marketplace_goods_policy as _goods
from services import marketplace_listing_lifecycle as _lifecycle
from services import marketplace_variants as _variants
from services.business_os.marketplace.listing_batch import (
    BLOCKED,
    FAILED,
    SUCCEEDED,
    BatchError,
    result_entry,
    summarize,
)

__all__ = [
    "ACTIONS", "REASON_REQUIRED", "REASON_CODES", "TRANSITIONS", "MAX_BATCH",
    "BLOCKED", "FAILED", "SUCCEEDED", "BatchError", "result_entry", "summarize",
    "block_reason", "claim", "ensure_schema", "evaluate_rows", "finalize",
    "normalize_request", "normalize_reason", "publication_readback",
    "queue_sql", "request_hash", "seller_message",
    "MISSING_REVIEW_STATE", "UNKNOWN_REVIEW_STATE", "APPROVED_BUT_UNRELEASED",
    "KNOWN_REVIEW_STATES", "zombie_reason", "zombie_repair", "zombie_sql",
    "duplicate_decision_sql",
    "QUEUE_FILTERS", "QUEUE_SORTS", "PAGE_SIZE",
    "INTERNAL_SECTION", "GAP_NOTES", "SUPPLIER_STALE_DAYS", "inspection",
    "variant_economics", "media_summary", "seller_standing", "safety_signals",
    "normalize_query", "queue_where", "queue_order", "page_window",
]


# --- vocabulary ---------------------------------------------------------------

#: The four verdicts a reviewer may record (§7). Deliberately *not* the same set
#: as the admin page's buttons: ``suspend``, ``archive`` and ``feature`` are
#: lifecycle and merchandising actions on a listing whose review already
#: concluded, they answer a different question, and folding them in here would
#: make "has this been reviewed" unanswerable from the action name.
APPROVE = "approve"
REJECT = "reject"
REQUEST_CHANGES = "request_changes"
RESTRICT = "restrict"

ACTIONS = (APPROVE, REJECT, REQUEST_CHANGES, RESTRICT)

#: §9. Every negative verdict carries a reason, because the seller reads it.
#: Approval does not: "why was my compliant listing allowed" is not a question
#: anybody asks, and a required field nobody has an answer for gets filled with
#: a space.
REASON_REQUIRED = frozenset({REJECT, REQUEST_CHANGES, RESTRICT})

#: §36. Structured codes, not free text alone. Free text cannot be counted,
#: cannot be translated, and cannot be turned into the seller-facing sentence in
#: :func:`seller_message` — three reviewers describing the same problem three
#: ways is how a rejection reason stops being data. The note rides *alongside*.
PROHIBITED_PRODUCT = "PROHIBITED_PRODUCT"
RESTRICTED_CATEGORY = "RESTRICTED_CATEGORY"
INVALID_PRICE = "INVALID_PRICE"
CATEGORY_MISMATCH = "CATEGORY_MISMATCH"
MISLEADING_DESCRIPTION = "MISLEADING_DESCRIPTION"
INVALID_MEDIA = "INVALID_MEDIA"
DUPLICATE_LISTING = "DUPLICATE_LISTING"
SUPPLIER_UNAVAILABLE = "SUPPLIER_UNAVAILABLE"
SHIPPING_UNAVAILABLE = "SHIPPING_UNAVAILABLE"
POLICY_VIOLATION = "POLICY_VIOLATION"
MISSING_INFORMATION = "MISSING_INFORMATION"
OTHER = "OTHER"

REASON_CODES = (
    PROHIBITED_PRODUCT, RESTRICTED_CATEGORY, INVALID_PRICE, CATEGORY_MISMATCH,
    MISLEADING_DESCRIPTION, INVALID_MEDIA, DUPLICATE_LISTING,
    SUPPLIER_UNAVAILABLE, SHIPPING_UNAVAILABLE, POLICY_VIOLATION,
    MISSING_INFORMATION, OTHER,
)

#: What the seller is told, per code. Seller-safe by construction: none of these
#: sentences can carry a reviewer's private note, an internal risk score or a
#: supplier identifier, because none of them is assembled from the row (§43).
SELLER_MESSAGES = {
    PROHIBITED_PRODUCT: "This product is not permitted on PulseSoc.",
    RESTRICTED_CATEGORY: "This category needs extra approval before it can go live.",
    INVALID_PRICE: "The price does not meet marketplace pricing policy.",
    CATEGORY_MISMATCH: "The category does not match the product.",
    MISLEADING_DESCRIPTION: "The description needs to be corrected.",
    INVALID_MEDIA: "The product images need to be replaced.",
    DUPLICATE_LISTING: "This product is already listed in your store.",
    SUPPLIER_UNAVAILABLE: "The supplier for this product is unavailable.",
    SHIPPING_UNAVAILABLE: "Shipping could not be resolved for this product.",
    POLICY_VIOLATION: "This listing does not meet marketplace policy.",
    MISSING_INFORMATION: "Some required product information is missing.",
    OTHER: "This listing needs a change before it can go live.",
}

#: ``action -> (status, approval_status)``. Both columns move together and are
#: written from this one table, so the pair can never be assembled differently
#: by two callers.
#:
#: ``restrict`` parks the listing on the moderation axis while leaving ``status``
#: where the merchant put it. A restricted listing is not rejected — the verdict
#: is "not without further approval" — and overwriting the merchant's release
#: state would lose the fact that they had released it, which is what a later
#: approval needs in order to publish rather than silently do nothing.
TRANSITIONS = {
    APPROVE: (_lifecycle.PUBLISHED, _lifecycle.APPROVED),
    REJECT: (_lifecycle.REJECTED, _lifecycle.REJECTED),
    REQUEST_CHANGES: (_lifecycle.CHANGES_REQUESTED, _lifecycle.CHANGES_REQUESTED),
    RESTRICT: (None, "restricted"),
}

#: Enough for the largest realistic selection, small enough that one batch
#: cannot hold a transaction open across a whole catalogue (§42).
MAX_BATCH = 200


# --- why a listing may not be acted on ----------------------------------------

NOT_FOUND = "NOT_FOUND"
NOT_AWAITING_REVIEW = "NOT_AWAITING_REVIEW"
SELF_REVIEW = "SELF_REVIEW"
PROHIBITED = "PROHIBITED"

BLOCK_NOTES = {
    NOT_FOUND: "That listing no longer exists.",
    NOT_AWAITING_REVIEW: "Already decided — reload before deciding again.",
    SELF_REVIEW: "A reviewer cannot decide their own listing.",
    PROHIBITED: "Prohibited products cannot be approved.",
}


def block_reason(
    listing: Optional[Mapping[str, Any]],
    action: str,
    *,
    reviewer_id: Any,
) -> Optional[str]:
    """Why this reviewer may not record ``action`` on this listing, or ``None``.

    A pure function of a row, a verdict and an actor, which is the point: every
    state a listing can reach can be proven against it without a Flask app, a
    request context or a database — and the single-decision route and the batch
    endpoint ask this same function, so they cannot disagree about eligibility.

    The two that are not merely bookkeeping:

    ``SELF_REVIEW`` (§18). Admin rights and a seller account are not mutually
    exclusive here, and nothing else in the stack would have stopped a moderator
    from approving their own product. Enforced on the *server*, on every action
    rather than only on approve: a reviewer rejecting a competitor's listing is
    the same conflict wearing the other hat.

    ``PROHIBITED`` (§34). Checked on approve only, and it is the one block a
    human reviewer cannot talk their way past. ``marketplace_goods_policy`` is
    the standing rule about the category and about prohibited signals in the
    copy; a reviewer clicking Approve on a weapons listing is not evidence that
    the policy changed. Restricted categories are *not* blocked -- deciding them
    is exactly the reviewer's job, and blocking them would leave the listing
    with no reachable verdict at all.
    """
    if not listing:
        return NOT_FOUND
    if not _lifecycle.awaiting_moderation(listing):
        return NOT_AWAITING_REVIEW
    owner = str(listing.get("seller_user_id") or "").strip()
    if owner and reviewer_id is not None and owner == str(reviewer_id).strip():
        return SELF_REVIEW
    if action == APPROVE and _goods.evaluate(listing).get("decision") == "PROHIBITED":
        return PROHIBITED
    return None


def seller_message(reason_code: str) -> str:
    """The sentence the seller reads. Never the reviewer's internal note (§43)."""
    return SELLER_MESSAGES.get(str(reason_code or "").upper(), SELLER_MESSAGES[OTHER])


# --- request validation -------------------------------------------------------


def normalize_reason(action: str, reason_code: Any, note: Any = None) -> dict:
    """Validate the reason half of a request.

    §9 forbids silent rejection, and "silent" includes a reason field the client
    filled with an empty string, a code this build does not recognise, or the
    word ``approve``. An unrecognised code is refused rather than coerced to
    ``OTHER``: a typo that lands in the audit trail as a real category is worse
    than one that fails loudly at the moment the reviewer can still fix it.
    """
    code = str(reason_code or "").strip().upper()
    text = str(note or "").strip()[:1200]
    if action not in REASON_REQUIRED:
        # An approval carries no reason code. Accepting one would put a
        # rejection category on an approved listing's audit row.
        return {"reason_code": "", "note": text}
    if not code:
        raise BatchError("REASON_REQUIRED", "This decision needs a reason.", 400)
    if code not in REASON_CODES:
        raise BatchError("UNKNOWN_REASON", f"Unknown reason code {code}.", 400)
    return {"reason_code": code, "note": text}


def normalize_request(action: Any, listing_ids: Any, *, idempotency_key: Any,
                      reason_code: Any = None, note: Any = None) -> dict:
    """The whole of what a valid review batch is.

    Duplicate ids are collapsed and order is preserved. A client that ticks a
    row twice means one decision, and a list that reached here with a repeat
    would otherwise produce two ``results`` entries for one listing — a summary
    whose ``requested_count`` disagrees with the number of products the reviewer
    selected.
    """
    verb = str(action or "").strip().lower()
    if verb not in ACTIONS:
        raise BatchError("UNKNOWN_ACTION", f"Unknown review action {action!r}.", 400)

    key = str(idempotency_key or "").strip()
    if not key:
        raise BatchError("IDEMPOTENCY_KEY_REQUIRED", "An idempotency key is required.", 400)

    seen: list = []
    for raw in listing_ids or []:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise BatchError("INVALID_LISTING_ID", f"Not a listing id: {raw!r}.", 400)
        if value > 0 and value not in seen:
            seen.append(value)
    if not seen:
        raise BatchError("NO_LISTINGS", "Select at least one listing.", 400)
    if len(seen) > MAX_BATCH:
        raise BatchError("BATCH_TOO_LARGE", f"At most {MAX_BATCH} listings per batch.", 400)

    reason = normalize_reason(verb, reason_code, note)
    return {"action": verb, "listing_ids": seen, "idempotency_key": key, **reason}


def request_hash(action: str, listing_ids: Iterable[int], reason_code: Any = "") -> str:
    """Fingerprint of what a request *asked for*, for the idempotency check.

    The reason code is inside the hash. Two batches over the same listings that
    reject for different reasons are different requests, and replaying the first
    one's answer for the second would file the wrong category against every row.
    """
    payload = json.dumps(
        {"action": action,
         "listing_ids": sorted(int(v) for v in listing_ids),
         "reason_code": str(reason_code or "").upper()},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- the queue ----------------------------------------------------------------


def queue_sql(alias: str = "l") -> str:
    """§38. The predicate that defines "in review", borrowed rather than written.

    Every listing this returns must be reachable in the Review Queue and every
    listing the queue shows must satisfy it, which is only guaranteed while the
    count, the list, the ordering and the Approve gate all ask the same
    question. They do, because they all call this.
    """
    return _lifecycle.awaiting_moderation_sql(alias)


#: §26. The named slices of the queue. ``pending`` is the queue proper -- the
#: predicate the Approve gate uses -- and the rest exist so a reviewer can find
#: what they already decided, which is how a wrong decision gets corrected.
QUEUE_FILTERS = ("pending", "changes_requested", "rejected", "restricted",
                 "approved", "all")

#: §28. ``sort -> ORDER BY``, as a whitelist rather than a format string.
#:
#: ``oldest`` is the default and that is a policy choice, not an accident.
#: Newest-first is what every other admin table in this application does and it
#: is exactly wrong for a work queue: the backlog at the bottom is never reached,
#: so the listings that have waited longest are the ones a reviewer never sees.
#: A seller waiting three weeks is the failure the mission opens with.
QUEUE_SORTS = {
    "oldest": "{a}.created_at ASC, {a}.id ASC",
    "newest": "{a}.created_at DESC, {a}.id DESC",
    "risk": "COALESCE({a}.safety_score,0) DESC, {a}.id DESC",
    "seller": "LOWER(COALESCE({a}.seller_user_id,'')) ASC, {a}.id DESC",
}

#: Big enough that a reviewer is not paging constantly, small enough that the
#: page renders and that one screenful of checkboxes cannot exceed
#: :data:`MAX_BATCH` when they tick every row.
PAGE_SIZE = 50


def normalize_query(params: Mapping[str, Any]) -> dict:
    """Read the queue controls off a request, refusing anything not on a list.

    Every value here reaches SQL. ``sort`` and ``filter`` are looked up in
    dictionaries rather than interpolated, so an unrecognised one falls back to
    the default instead of becoming an ``ORDER BY`` clause; ``search`` is bound
    as a parameter and never formatted in.

    An unrecognised filter falls back to ``pending`` rather than to ``all``: the
    failure mode of a typo should be "the reviewer sees their work queue", not
    "the reviewer sees the whole catalogue and assumes it is the work queue".
    """
    filter_key = str(params.get("filter") or "").strip().lower()
    if filter_key not in QUEUE_FILTERS:
        filter_key = "pending"
    sort_key = str(params.get("sort") or "").strip().lower()
    if sort_key not in QUEUE_SORTS:
        sort_key = "oldest"
    search = str(params.get("q") or params.get("search") or "").strip()[:120]
    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(params.get("page_size") or PAGE_SIZE)
    except (TypeError, ValueError):
        page_size = PAGE_SIZE
    page_size = max(1, min(page_size, MAX_BATCH))
    return {"filter": filter_key, "sort": sort_key, "search": search,
            "page": page, "page_size": page_size}


def queue_where(query: Mapping[str, Any], alias: str = "l") -> tuple:
    """``(sql, params)`` for one slice of the queue.

    §29. The count and the page are built from this same pair. A badge counted
    with one predicate over a list built with another is the stale-count defect:
    "340 pending" above a table that ends at row 100, and no way to tell whether
    the missing 240 are filtered out or simply unreachable.

    §27. Search is a SQL ``LIKE`` and not a pass over the rows already fetched.
    Filtering the current page is not search -- it finds a listing only when the
    reviewer had already scrolled to it, which is when they did not need to
    search.
    """
    filter_key = str(query.get("filter") or "pending")
    clauses: list = []
    params: list = []

    if filter_key == "pending":
        clauses.append(f"({queue_sql(alias)})")
    elif filter_key == "restricted":
        clauses.append(f"LOWER(COALESCE({alias}.approval_status,''))='restricted'")
    elif filter_key in {"changes_requested", "rejected", "approved"}:
        clauses.append(f"LOWER(COALESCE({alias}.approval_status,''))=?")
        params.append(filter_key)

    search = str(query.get("search") or "").strip()
    if search.isdigit():
        # A bare number is an identifier lookup, not a text search. A reviewer
        # pasting "4172" means listing 4172 or seller 4172 -- running it as a
        # LIKE as well buries the one row they asked for underneath every
        # product whose description happens to contain that digit, which for a
        # single-digit id is most of the catalogue.
        clauses.append(f"(CAST({alias}.id AS TEXT)=? "
                       f"OR CAST(COALESCE({alias}.seller_user_id,0) AS TEXT)=?)")
        params.extend([search, search])
    elif search:
        needle = f"%{search.lower()}%"
        clauses.append(
            f"(LOWER(COALESCE({alias}.title,'')) LIKE ? "
            f"OR LOWER(COALESCE({alias}.description,'')) LIKE ? "
            f"OR LOWER(COALESCE({alias}.category,'')) LIKE ?)")
        params.extend([needle, needle, needle])

    return (" AND ".join(clauses) or "1=1"), params


def queue_order(query: Mapping[str, Any], alias: str = "l") -> str:
    """``ORDER BY`` body for the chosen sort, from the whitelist only."""
    return QUEUE_SORTS[str(query.get("sort") or "oldest")].format(a=alias)


def page_window(query: Mapping[str, Any], total: int) -> dict:
    """Where the reviewer is in the queue, and whether anything is unreachable.

    ``pages`` is derived from the same ``total`` the badge shows, so "page 3 of
    7" and "340 pending" cannot tell different stories. ``offset`` is clamped
    into range: a reviewer sitting on page 7 who approves the last of them must
    land on a page that exists rather than on an empty table that reads as "no
    work left".
    """
    page_size = int(query.get("page_size") or PAGE_SIZE)
    total = max(0, int(total or 0))
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, int(query.get("page") or 1)), pages)
    return {"page": page, "pages": pages, "page_size": page_size, "total": total,
            "offset": (page - 1) * page_size,
            "has_prev": page > 1, "has_next": page < pages}


# --- §40/§41: listings that are in review according to nobody -----------------

#: The merchant released it and the moderation column is blank. Nothing decided
#: it, nothing can: :func:`awaiting_moderation` needs a recognised awaiting
#: state on the approval axis, and an empty string is not one.
MISSING_REVIEW_STATE = "MISSING_REVIEW_STATE"

#: The moderation column holds a word this build does not know -- ``in_review``,
#: ``submitted``, ``needs_review``, whatever an older route or an import script
#: wrote. It reads as "in review" to a human scanning the table and as nothing
#: at all to every predicate.
UNKNOWN_REVIEW_STATE = "UNKNOWN_REVIEW_STATE"

#: A decision exists, but on the wrong axis to matter: approved while the
#: merchant axis still says pending. Reported, never repaired — see
#: :func:`zombie_repair`.
APPROVED_BUT_UNRELEASED = "APPROVED_BUT_UNRELEASED"

#: Every value either axis may legitimately hold on the moderation side. A row
#: outside this set is not "some other state", it is a row no code path in this
#: application can act on.
KNOWN_REVIEW_STATES = frozenset(
    set(_lifecycle.AWAITING_DECISION_STATES)
    | {_lifecycle.APPROVED, _lifecycle.REJECTED, _lifecycle.CHANGES_REQUESTED,
       "restricted", _lifecycle.SUSPENDED, _lifecycle.ARCHIVED, _lifecycle.DRAFT}
)


def zombie_reason(listing: Mapping[str, Any]) -> Optional[str]:
    """Why this row is telling its seller "in review" with nobody able to review it.

    The §40 defect is not a listing in the wrong state. It is a listing in a
    state that *no* screen contradicts: the seller's store shows "In review —
    not live yet" because the row is released and not public, the review queue
    does not show it because :func:`_lifecycle.awaiting_moderation` is false,
    and buyer discovery does not show it because approval never happened. Three
    surfaces agree, all three are correct, and the product sits there forever.

    Deliberately narrow on one point: a ``draft`` with a blank approval column
    is *not* a zombie. Its merchant has not released it, so nobody is waiting
    and nothing is stuck. Sweeping drafts into ``pending_review`` would push
    every unfinished product a seller ever started into the moderation queue —
    the reviewer's backlog would fill with listings whose own authors are not
    done with them.
    """
    if not listing:
        return None
    status = _lifecycle.normalized(listing.get("status"))
    approval = _lifecycle.normalized(listing.get("approval_status"))

    if status not in _lifecycle.MERCHANT_RELEASED_STATUSES:
        # Not released. Whatever the approval column says, no seller is being
        # told this is in review and no reviewer is missing work.
        return None
    if approval in _lifecycle.AWAITING_DECISION_STATES:
        return None  # Reachable. This is the healthy case.
    if not approval:
        return MISSING_REVIEW_STATE
    if approval not in KNOWN_REVIEW_STATES:
        return UNKNOWN_REVIEW_STATE
    if (approval in _lifecycle.APPROVED_STATES
            and status in _lifecycle.AWAITING_DECISION_STATES):
        return APPROVED_BUT_UNRELEASED
    return None


def zombie_repair(listing: Mapping[str, Any]) -> Optional[dict]:
    """The single column write that makes a stuck listing decidable again.

    ``{"approval_status": "pending_review", "reason": ...}`` or ``None``.

    What this deliberately does not do is decide anything. The repair puts the
    row *into* the queue and leaves the verdict to a human, because the states
    it is repairing from carry no information about whether the product is
    acceptable — that is precisely what went missing. A backfill that inferred
    "released and unblank, so presumably fine, mark it approved" would publish
    an unreviewed catalogue in one transaction and every §34 guard in this
    module would have been bypassed by a maintenance script.

    ``APPROVED_BUT_UNRELEASED`` returns ``None`` for the mirror-image reason.
    Its fix is on the merchant axis, and writing ``status='published'`` here
    would publish a listing whose merchant never released it.
    """
    reason = zombie_reason(listing)
    if reason in (MISSING_REVIEW_STATE, UNKNOWN_REVIEW_STATE):
        return {"approval_status": _lifecycle.PENDING_REVIEW, "reason": reason}
    return None


def zombie_sql(alias: str = "l") -> str:
    """The sweep's predicate: released, and not in any state the queue accepts.

    Broader than :func:`zombie_reason` by exactly one case (it also matches
    genuinely decided rows), because a SQL ``NOT IN`` cannot tell
    ``APPROVED_BUT_UNRELEASED`` from a normal approval without repeating the
    whole rule. The sweep re-checks every row it fetches through
    :func:`zombie_reason`, so the SQL only has to be a superset that is small —
    and this one is bounded by "released but not awaiting", which on a healthy
    catalogue is the decided rows and nothing else.
    """
    released = "', '".join(sorted(_lifecycle.MERCHANT_RELEASED_STATUSES))
    awaiting = "', '".join(sorted(_lifecycle.AWAITING_DECISION_STATES))
    return (f"LOWER(COALESCE({alias}.status,'')) IN ('{released}') "
            f"AND LOWER(COALESCE({alias}.approval_status,'')) NOT IN ('{awaiting}')")


def duplicate_decision_sql(alias: str = "b") -> str:
    """§41. Two ledger rows claiming the same key for the same reviewer.

    The unique index makes this impossible going forward, which is exactly why
    it is worth asking: an index added after the fact does not clean up what
    landed before it, and a silently-deduplicated replay is indistinguishable
    from a decision that never happened.
    """
    return (f"SELECT {alias}.reviewer_user_id, {alias}.idempotency_key, COUNT(*) AS copies "
            f"FROM marketplace_review_batches {alias} "
            f"GROUP BY {alias}.reviewer_user_id, {alias}.idempotency_key HAVING COUNT(*) > 1")


def evaluate_rows(rows: Iterable[Mapping[str, Any]], listing_ids: Iterable[int],
                  action: str, *, reviewer_id: Any) -> dict:
    """Split the requested ids into what will be acted on and what is blocked.

    Returns ``{"eligible": [id, ...], "blocked": [result_entry, ...]}``.

    Ids with no row are blocked as ``NOT_FOUND`` rather than dropped. A batch
    that silently shrinks reports ``requested_count`` smaller than the reviewer's
    selection, and the two products that vanished are exactly the ones they
    needed told about (§15).
    """
    by_id = {int(row.get("id") or 0): row for row in rows}
    eligible: list = []
    blocked: list = []
    for listing_id in listing_ids:
        reason = block_reason(by_id.get(int(listing_id)), action, reviewer_id=reviewer_id)
        if reason is None:
            eligible.append(int(listing_id))
        else:
            blocked.append(result_entry(
                listing_id, BLOCKED, error_code=reason, blockers=[reason],
                note=BLOCK_NOTES.get(reason)))
    return {"eligible": eligible, "blocked": blocked}


# --- what actually became of it ------------------------------------------------


def publication_readback(listing: Mapping[str, Any]) -> dict:
    """§37. Read the listing back and say what a buyer can now reach.

    Approving writes two of the five conditions ``marketplace_listing_lifecycle``
    requires for discovery; the other three belong to the seller record and to
    stock. So an approval can succeed completely and leave the product invisible
    — a suspended seller, an unnamed storefront, zero quantity — and reporting
    "Live" from the fact that the UPDATE returned would be a claim about buyers
    that nobody checked.

    The verdict is not recomputed here. It comes from the same rule table that
    decides discovery, so this sentence cannot drift away from the predicate
    that actually hides the row.
    """
    blocker = _lifecycle.publication_blocker(listing)
    return {
        "review_state": _lifecycle.normalized(listing.get("approval_status")),
        "publication_state": _lifecycle.normalized(listing.get("status")),
        "live": not blocker,
        "blockers": [blocker] if blocker else [],
        "note": _lifecycle.blocker_note(blocker) if blocker else "",
    }


# --- §7: what a reviewer needs on the screen before deciding --------------------

#: §43. Everything under this key is priced from supplier data. It is legitimate
#: on the admin dossier and forbidden on anything a merchant or a buyer can read,
#: so it lives under one name that is greppable rather than being spread across
#: sibling fields that each have to be remembered separately.
INTERNAL_SECTION = "internal_economics"

#: A gap is something the reviewer should look at. It is *not* a verdict --
#: `block_reason` is the only thing that decides, and a dossier that also decided
#: would be the §1 defect rebuilt one module further in.
GAP_NOTES = {
    "NO_MEDIA": "No images or video. A buyer sees an empty product page.",
    "NO_COVER": "No cover image chosen, so the listing has nothing to show in a feed.",
    "MEDIA_PENDING": "Some media has not cleared media moderation yet.",
    "MEDIA_REJECTED": "Some media was rejected and will not render for buyers.",
    "NO_DESCRIPTION": "No description. Nothing distinguishes this from a duplicate.",
    "THIN_DESCRIPTION": "Description is under 40 characters.",
    "NO_PRICE": "No price on the listing or on any variant.",
    "NO_STOCK": "Quantity is zero, so approving cannot make it buyable.",
    "COST_UNKNOWN": "No supplier cost recorded, so margin cannot be checked.",
    "SELLING_BELOW_COST": "Price is at or under supplier cost on at least one variant.",
    "SUPPLIER_NEVER_SYNCED": "No variant has ever synced with the supplier.",
    "SUPPLIER_STALE": "Supplier stock was last synced more than 7 days ago.",
    "NO_SHIPPING": "No delivery type or estimate for a physical product.",
    "SELLER_UNVERIFIED": "Seller has not completed verification.",
    "SELLER_NOT_APPROVED": "Seller account is not in good standing.",
    "SELLER_HIGH_RISK": "Seller risk score is 60 or above.",
    "SAFETY_FLAGGED": "Automated safety scoring flagged this listing.",
}

#: Older than this and "in stock" is a claim about a supplier nobody has spoken
#: to in a week.
SUPPLIER_STALE_DAYS = 7


def _int_or_none(value: Any) -> Optional[int]:
    """``None`` for absent, which is not the same number as zero.

    A missing ``cost_cents`` read as ``0`` reports a 100%% margin, and it reports
    it on exactly the listings where the supplier data is thinnest -- so the rows
    the reviewer knows least about are the ones that look most attractive.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def variant_economics(variant: Mapping[str, Any]) -> dict:
    """Price, cost and margin for one variant. Unknown stays unknown."""
    price = _int_or_none(variant.get("price_cents"))
    cost = _int_or_none(variant.get("cost_cents"))
    # Delegated, not restated. `marketplace_variants.margin_cents` already owns
    # this subtraction and already makes the two calls that matter -- unknown
    # cost returns None rather than a 100% margin, and a negative result is
    # returned rather than clamped. A second copy here would agree on the day it
    # was written and be the place the seller dashboard and the review page
    # start disagreeing about the same product's margin.
    margin_cents = _variants.margin_cents({"cost_cents": cost}, price)
    margin_pct = None
    if margin_cents is not None and price:
        margin_pct = round(margin_cents * 100.0 / price, 1)
    return {
        "variant_key": str(variant.get("variant_key") or ""),
        "sku": str(variant.get("sku") or ""),
        "currency": str(variant.get("currency") or "").upper(),
        "price_cents": price,
        "cost_cents": cost,
        "margin_cents": margin_cents,
        "margin_pct": margin_pct,
        "stock_quantity": _int_or_none(variant.get("stock_quantity")),
        "stock_state": str(variant.get("stock_state") or ""),
        "stock_synced_at": str(variant.get("stock_synced_at") or ""),
        "status": str(variant.get("status") or ""),
    }


def _days_since(stamp: Any, *, now: Optional[datetime] = None) -> Optional[float]:
    text = str(stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - parsed).total_seconds() / 86400.0


def media_summary(media: Iterable[Mapping[str, Any]]) -> dict:
    """§7 gallery. Counts by moderation state, because "has 8 images" and "has 8
    images, 3 of which buyers will never see" are different products."""
    items = []
    for entry in media or ():
        items.append({
            "media_type": str(entry.get("media_type") or "image"),
            "media_url": str(entry.get("media_url") or ""),
            "thumbnail_url": str(entry.get("thumbnail_url") or ""),
            "is_cover": bool(entry.get("is_cover")),
            "moderation_status": str(entry.get("moderation_status") or "").lower(),
            "position": _int_or_none(entry.get("position")) or 0,
        })
    items.sort(key=lambda row: (not row["is_cover"], row["position"]))
    states = [row["moderation_status"] for row in items]
    return {
        "items": items,
        "total": len(items),
        "has_cover": any(row["is_cover"] for row in items),
        "approved": sum(1 for state in states if state == "approved"),
        "pending": sum(1 for state in states if state in ("", "pending", "pending_review")),
        "rejected": sum(1 for state in states if state in ("rejected", "removed")),
    }


def seller_standing(seller: Optional[Mapping[str, Any]]) -> dict:
    """§7. Who is behind the listing, from the seller record rather than from the
    listing's own copy of it."""
    seller = seller or {}
    return {
        "user_id": _int_or_none(seller.get("user_id")),
        "display_name": str(seller.get("display_name") or ""),
        "status": str(seller.get("status") or "").lower(),
        "verification_status": str(seller.get("verification_status") or "").lower(),
        "risk_score": _int_or_none(seller.get("risk_score")),
        "seller_type": str(seller.get("seller_type") or ""),
        "country": str(seller.get("country") or ""),
        "created_at": str(seller.get("created_at") or ""),
    }


def safety_signals(listing: Mapping[str, Any]) -> dict:
    """Automated scoring plus the goods-policy decision, side by side.

    The policy decision is repeated here even though `block_reason` already
    consults it, because a reviewer looking at a row that Approve refuses is
    entitled to see *which* rule refused it rather than being told no twice.
    """
    flags: list = []
    raw = listing.get("safety_flags_json")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            flags = [str(flag) for flag in parsed]
        elif isinstance(parsed, dict):
            flags = [str(key) for key, value in parsed.items() if value]
    # `evaluate` returns a plain dict. Reading it with `getattr(..., "")`
    # compiles, never raises, and yields "" for every listing -- so the policy
    # section renders blank on exactly the prohibited products it exists to
    # explain, and nothing anywhere reports an error.
    verdict = _goods.evaluate(listing) or {}
    return {
        "score": _int_or_none(listing.get("safety_score")) or 0,
        "flags": flags,
        "policy_decision": str(verdict.get("decision") or ""),
        "policy_reason": str(verdict.get("reason_code") or ""),
        "policy_version": str(verdict.get("policy_version") or ""),
    }


def inspection(listing: Optional[Mapping[str, Any]], *,
               variants: Iterable[Mapping[str, Any]] = (),
               media: Iterable[Mapping[str, Any]] = (),
               seller: Optional[Mapping[str, Any]] = None,
               reviewer_id: Any = None,
               now: Optional[datetime] = None) -> dict:
    """§7. Everything the reviewer needs on one screen, and no verdict of its own.

    The verdicts come back from :func:`block_reason` -- the same call the queue
    row, the bulk bar and the batch endpoint make -- so the detail page cannot
    offer a decision the endpoint would refuse, which is the failure this
    codebase has now produced twice on two different surfaces.

    ``gaps`` are advisory and deliberately separate from ``verdicts``. Most of
    them describe a listing that is perfectly approvable and merely bad; folding
    them into the block reasons would let the queue accumulate rows no reviewer
    is permitted to clear, which is worse than the backlog it replaces.
    """
    if not listing:
        return {"found": False, "listing_id": 0}

    listing = dict(listing)
    priced = [variant_economics(row) for row in (variants or ())]
    gallery = media_summary(media)
    standing = seller_standing(seller)
    safety = safety_signals(listing)

    gaps: list = []

    def flag(code: str) -> None:
        if code not in gaps:
            gaps.append(code)

    if gallery["total"] == 0:
        flag("NO_MEDIA")
    else:
        if not gallery["has_cover"] and not str(listing.get("cover_image_url") or ""):
            flag("NO_COVER")
        if gallery["pending"]:
            flag("MEDIA_PENDING")
        if gallery["rejected"]:
            flag("MEDIA_REJECTED")

    description = str(listing.get("description") or "").strip()
    if not description:
        flag("NO_DESCRIPTION")
    elif len(description) < 40:
        flag("THIN_DESCRIPTION")

    known_prices = [row["price_cents"] for row in priced if row["price_cents"] is not None]
    if not known_prices and not str(listing.get("price_label") or "").strip():
        flag("NO_PRICE")

    quantity = _int_or_none(listing.get("quantity"))
    variant_stock = [row["stock_quantity"] for row in priced
                     if row["stock_quantity"] is not None]
    if not (quantity or 0) and not any(variant_stock):
        flag("NO_STOCK")

    if priced and all(row["cost_cents"] is None for row in priced):
        flag("COST_UNKNOWN")
    if any(row["margin_cents"] is not None and row["margin_cents"] <= 0 for row in priced):
        flag("SELLING_BELOW_COST")

    if priced:
        ages = [_days_since(row["stock_synced_at"], now=now) for row in priced]
        if all(age is None for age in ages):
            flag("SUPPLIER_NEVER_SYNCED")
        elif min(age for age in ages if age is not None) > SUPPLIER_STALE_DAYS:
            flag("SUPPLIER_STALE")

    physical = str(listing.get("product_type") or listing.get("listing_type")
                   or "").lower() in ("", "physical", "goods")
    if physical and not (str(listing.get("delivery_type") or "").strip()
                         or str(listing.get("estimated_delivery") or "").strip()):
        flag("NO_SHIPPING")

    if standing["status"] and standing["status"] != "approved":
        flag("SELLER_NOT_APPROVED")
    if standing["verification_status"] not in ("verified", "approved"):
        flag("SELLER_UNVERIFIED")
    if (standing["risk_score"] or 0) >= 60:
        flag("SELLER_HIGH_RISK")

    if safety["score"] >= 30 or safety["flags"]:
        flag("SAFETY_FLAGGED")

    margins = [row["margin_cents"] for row in priced if row["margin_cents"] is not None]
    pcts = [row["margin_pct"] for row in priced if row["margin_pct"] is not None]

    return {
        "found": True,
        "listing_id": int(listing.get("id") or 0),
        "title": str(listing.get("title") or ""),
        "description": description,
        "category": str(listing.get("category") or ""),
        "review_state": _lifecycle.normalized(listing.get("approval_status")),
        "publication_state": _lifecycle.normalized(listing.get("status")),
        "awaiting_moderation": _lifecycle.awaiting_moderation(listing),
        "review_version": _int_or_none(listing.get("review_version")) or 0,
        "submitted_at": str(listing.get("submitted_at") or ""),
        "reviewed_at": str(listing.get("reviewed_at") or ""),
        "reviewed_by": _int_or_none(listing.get("reviewed_by")),
        "moderation_reason": str(listing.get("moderation_reason") or ""),
        "moderation_category": str(listing.get("moderation_category") or ""),
        # One call per action, so the page's buttons and the endpoint's gate are
        # the same function evaluated once each rather than two rule sets.
        "verdicts": {action: block_reason(listing, action, reviewer_id=reviewer_id)
                     for action in ACTIONS},
        "media": gallery,
        "seller": standing,
        "safety": safety,
        "fulfilment": {
            "product_type": str(listing.get("product_type") or ""),
            "listing_type": str(listing.get("listing_type") or ""),
            "delivery_type": str(listing.get("delivery_type") or ""),
            "estimated_delivery": str(listing.get("estimated_delivery") or ""),
            "refund_policy": str(listing.get("refund_policy") or ""),
            "quantity": quantity,
        },
        INTERNAL_SECTION: {
            # §43. Supplier cost and margin. Admin dossier only -- never assemble
            # a merchant or buyer payload from this dict.
            "variants": priced,
            "variant_count": len(priced),
            "currency": (priced[0]["currency"] if priced else
                         str(listing.get("currency") or "").upper()),
            "price_label": str(listing.get("price_label") or ""),
            "min_price_cents": min(known_prices) if known_prices else None,
            "max_price_cents": max(known_prices) if known_prices else None,
            "min_margin_cents": min(margins) if margins else None,
            "min_margin_pct": min(pcts) if pcts else None,
            "cost_known": any(row["cost_cents"] is not None for row in priced),
        },
        "publication_if_approved": publication_readback(dict(
            listing, status=_lifecycle.PUBLISHED, approval_status=_lifecycle.APPROVED)),
        "gaps": gaps,
        "gap_notes": [{"code": code, "note": GAP_NOTES.get(code, code)} for code in gaps],
    }


# --- idempotency ---------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema() -> None:
    """Create the review batch ledger. Idempotent; safe at startup and in tests.

    Takes no connection, for the reason ``listing_batch.ensure_schema`` spells
    out: handing a route's open connection to DDL skips the commit, so the
    statement rolls back while still holding a catalog lock and the next
    connection blocks until the route finishes.
    """
    conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS marketplace_review_batches (
                batch_id TEXT PRIMARY KEY,
                reviewer_user_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                action TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE (reviewer_user_id, idempotency_key)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def claim(conn, reviewer_user_id, normalized: dict) -> dict:
    """Take ownership of an idempotency key, or report who already has it.

    Returns ``{"state": "claimed", "batch_id": ...}`` or
    ``{"state": "replayed", "response": {...}}``, and raises ``BatchError`` when
    the key is spent on a different request or an identical one is in flight.

    Claimed **before** the writes. §17: the obvious order — decide, then record —
    leaves a window in which a reviewer who double-clicks Approve, or whose
    client retries through a timeout, publishes every listing twice, emits every
    notification twice and files every audit row twice. The claim is what makes
    the second attempt a replay instead of a second batch of side effects.
    """
    key = normalized["idempotency_key"]
    digest = request_hash(normalized["action"], normalized["listing_ids"],
                          normalized.get("reason_code"))
    reviewer = str(reviewer_user_id)

    existing = conn.execute(
        "SELECT batch_id, request_hash, response_json FROM marketplace_review_batches "
        "WHERE reviewer_user_id=? AND idempotency_key=? LIMIT 1",
        (reviewer, key),
    ).fetchone()

    if existing is not None:
        row = dict(existing)
        if row["request_hash"] != digest:
            raise BatchError("IDEMPOTENCY_KEY_CONFLICT",
                             "That request has already been used for different listings.", 409)
        if not row["response_json"]:
            raise BatchError("BATCH_IN_PROGRESS", "That review batch is still running.", 409)
        return {"state": "replayed", "response": json.loads(row["response_json"])}

    batch_id = "mrb_" + uuid.uuid4().hex
    # ON CONFLICT DO NOTHING rather than a bare INSERT: two concurrent identical
    # requests both read no row above, and the loser of that race must not
    # poison a PostgreSQL transaction with a constraint violation.
    cursor = conn.execute(
        "INSERT INTO marketplace_review_batches "
        "(batch_id, reviewer_user_id, idempotency_key, request_hash, action, created_at) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
        (batch_id, reviewer, key, digest, normalized["action"], _utc_now_iso()),
    )
    if cursor.rowcount != 1:
        raise BatchError("BATCH_IN_PROGRESS", "That review batch is still running.", 409)
    return {"state": "claimed", "batch_id": batch_id}


def finalize(conn, batch_id: str, response: dict) -> None:
    """Record the answer against the claim, so a retry replays it."""
    conn.execute(
        "UPDATE marketplace_review_batches SET response_json=?, completed_at=? WHERE batch_id=?",
        (json.dumps(response, default=str), _utc_now_iso(), batch_id),
    )
