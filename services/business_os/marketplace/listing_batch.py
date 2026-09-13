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

#: The two actions a bulk request may carry today. Bulk pricing, category and
#: visibility (§22–§33) will extend this; they are not silently accepted now,
#: because an unknown action that fell through to a no-op would report
#: ``successful_count`` for work nobody did.
ACTIONS = ("publish", "hide")

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


def normalize_request(action: Any, listing_ids: Any, idempotency_key: Any) -> dict:
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

    return {"action": action, "listing_ids": sorted(clean), "idempotency_key": key}


def request_hash(action: str, listing_ids: Iterable[int]) -> str:
    """A fingerprint of what the caller asked for.

    Exists so that reusing a key for a *different* request is caught. Without
    it, a client that recycles keys would receive the first batch's answer for
    the second batch's listings — the reply would name ids nobody asked about,
    and the rows the seller actually selected would never move.
    """
    payload = json.dumps(
        {"action": action, "listing_ids": sorted(int(i) for i in listing_ids)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- eligibility -------------------------------------------------------------


def block_reason(listing: dict, action: str, verdict: Optional[dict] = None) -> Optional[dict]:
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

    if status == "seller_deleted":
        return {"code": "DELETED", "reason": "Removed from your store"}

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
    """
    lookup = media_by_listing or {}
    decided = []
    for row in rows:
        if action == "publish":
            verdict = _readiness.evaluate(row, media=lookup.get(int(row.get("id") or 0)))
        else:
            verdict = None
        decided.append((row, block_reason(row, action, verdict)))
    return decided


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
    digest = request_hash(normalized["action"], normalized["listing_ids"])
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
