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
    "QUEUE_FILTERS", "QUEUE_SORTS", "PAGE_SIZE",
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
