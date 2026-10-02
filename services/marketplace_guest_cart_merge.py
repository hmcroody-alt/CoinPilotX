"""What happens to a visitor's cart at the moment they sign in.

The rule
--------
Nothing the shopper chose is lost, and nothing they did not choose is invented.

Those two together decide every case below, and they are the reason this is a
module and not three lines inside a login handler. A cart arriving from a guest
session is the shopper's stated intent; a cart already on the account is also
their stated intent, possibly from another device five minutes ago. Signing in
is not an instruction to discard either one.

Why quantities take the larger, not the sum
-------------------------------------------
The same item in both carts is the interesting case, and summing is the obvious
answer that is wrong. Somebody who put one lamp in a guest cart on their phone
and one lamp in their account cart on a laptop wants *a lamp*; handing them two
is an invented intent, invented in the direction that takes more of their money.

The larger of the two is the only choice that cannot reduce what either side
asked for while never exceeding what either side asked for. It is also, usefully,
closed: both inputs were capped at ``MAX_QTY_PER_LINE`` when they were written,
so their maximum is too and this module needs no cap of its own.

Why the line cap is not enforced here
-------------------------------------
``MAX_LINES`` is a guard on *adding*, and a merge is not an add. A member with a
full cart who then fills a guest cart can union past the cap, and the honest
answer is to let them: refusing would mean silently dropping lines a shopper
picked, which is the one outcome this module exists to prevent. The cap keeps
applying to every subsequent add, so the overflow drains rather than grows.

Failure must not look like success
----------------------------------
This is the whole reason :func:`merge` reports ``ok`` separately from what it
did. Claiming the token and moving the rows are two writes, and a failure
between them would leave a spent token above an un-moved cart -- lines nobody
can reach, which is cart destruction wearing a clean exit code. So a failure is
*returned*, the caller rolls its transaction back, and the cookie is left in
place so the next request tries again from the state it started in.

For the same reason a token that does not resolve is reported as ``stale``
rather than as a failure. Those are the two ways to get zero rows moved and they
call for opposite handling: one means drop the cookie, the other means keep it.

What this does not do
---------------------
It does not re-price. Snapshots stay as they were written and
``_serialize_lines`` compares every one of them against the live price on the
next read, flagging ``price_changed`` for the buyer to confirm. A merge that
quietly re-snapshotted would be moving the price the shopper last saw without
telling them.
"""

from __future__ import annotations

import logging

from services import marketplace_cart_schema as cart_schema
from services import marketplace_guest_customer as guest_customer

LOGGER = logging.getLogger(__name__)


def _rows(cur, owner_id: int) -> list[dict]:
    cur.execute(
        f"SELECT id, listing_id, variant_id, qty FROM {cart_schema.CART_TABLE} "
        "WHERE user_id=? ORDER BY id",
        (owner_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def _key(row: dict) -> tuple[int, int]:
    return (
        int(row.get("listing_id") or 0),
        int(row.get("variant_id") or cart_schema.NO_VARIANT),
    )


def merge(cur, token, user_id: int, now: str) -> dict:
    """Fold the cart behind ``token`` into ``user_id``'s. Never raises.

    ``ok`` is false only when a write failed partway: the caller must roll back
    and keep the cookie. ``stale`` means the token resolved to nothing, which is
    a success with nothing to do -- the cookie should be dropped. The counts are
    returned so a deployment can read what a merge actually did rather than
    infer it, and so "no guest cart" stays distinguishable from "a guest cart
    that turned out to be empty".
    """
    outcome = {"ok": True, "stale": False, "moved": 0, "combined": 0, "raised": 0}
    try:
        user_id = int(user_id or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        outcome["stale"] = True
        return outcome
    try:
        # Resolved before claiming so the two reasons `claim_for_member` can
        # answer zero stay apart. A token nobody holds a cart for is stale; a
        # token that resolves here and then fails to claim is a write that did
        # not happen, and the two must not share an answer.
        owner_id = guest_customer.owner_id_for_token(cur, token)
        if not owner_id:
            outcome["stale"] = True
            return outcome
        if guest_customer.claim_for_member(cur, token, user_id) != owner_id:
            outcome["ok"] = False
            return outcome
        guest_lines = _rows(cur, owner_id)
        if not guest_lines:
            return outcome
        # Keyed by what the unique index is keyed by, so "the member already has
        # this" means exactly what the database would mean by it. Anything looser
        # and the UPDATE below hits a constraint instead of a branch.
        mine = {_key(row): row for row in _rows(cur, user_id)}
        for line in guest_lines:
            existing = mine.get(_key(line))
            if existing is None:
                cur.execute(
                    f"UPDATE {cart_schema.CART_TABLE} SET user_id=?, updated_at=? WHERE id=?",
                    (user_id, now, int(line["id"])),
                )
                outcome["moved"] += 1
                continue
            was = int(existing.get("qty") or 1)
            target = max(was, int(line.get("qty") or 1))
            if target != was:
                cur.execute(
                    f"UPDATE {cart_schema.CART_TABLE} SET qty=?, updated_at=? WHERE id=?",
                    (target, now, int(existing["id"])),
                )
                outcome["raised"] += 1
            # The guest row goes, not the member's. Both describe the same
            # choice and only one may exist under the unique key; keeping the
            # row that already belongs to the account means the line's
            # `added_at` still says when this shopper first wanted it.
            cur.execute(
                f"DELETE FROM {cart_schema.CART_TABLE} WHERE id=?", (int(line["id"]),)
            )
            outcome["combined"] += 1
    except Exception:
        # Returned rather than raised because this runs on the way into a
        # signed-in session and a sign-in that 500s over a cart is a worse
        # outcome than a cart that merges one request later. The caller rolls
        # back on `ok: False`, so the half-done state never reaches disk.
        LOGGER.exception("GUEST_CART_MERGE_FAILED user_id=%s", user_id)
        outcome["ok"] = False
    return outcome
