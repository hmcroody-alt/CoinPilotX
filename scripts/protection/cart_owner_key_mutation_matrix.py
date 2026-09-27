#!/usr/bin/env python3
"""Prove each cart-ownership control is observed, by deleting it.

``tests/marketplace/test_cart_owner_key.py`` is green. That says it passes, not
that it would notice if the thing it is named after stopped working — and this
increment has one blind spot so large it is worth naming before anything else:

**Every route control here is invisible to a signed-in shopper.** For an account,
``ON CONFLICT(owner_key, listing_id)`` and ``ON CONFLICT(user_id, listing_id)``
say precisely the same thing, because ``owner_key`` is a pure function of
``user_id``. So a suite that only ever signs in would pass with the *old* target
restored — the exact bug this module exists to remove. Only a row with a NULL
``user_id`` can tell them apart, which is why
``test_a_guest_cart_through_the_real_route_does_not_duplicate_its_lines`` flips
``GUEST_CARTS_ENABLED`` for its own duration, and why
``conflict-target-back-to-user-id`` is the first mutation below. If that one
survives, the whole module is decoration and the second tap on Add to Cart
silently makes a second line in production the day guests are admitted.

The mutations fall into four groups:

* **The conflict target and the columns written.** The storage half. These are
  the ones only a NULL ``user_id`` can observe.
* **The owner key as an authorisation check.** Every cart write is
  ``WHERE id=? AND owner_key=?``. Line ids are sequential integers, so dropping
  the second half leaves a working cart and an IDOR. Mutated as
  ``OR 1=1`` rather than by deleting the clause, so the parameter count still
  matches and the mutant is a behaviour change rather than a crash.
* **The guest handle.** A key derived from a hash of a server-signed random
  token, with a length floor. ``guest-key-is-the-raw-token`` and
  ``guest-key-not-prefixed`` are the two that produce a *well-formed* key and so
  would look like a working feature.
* **The guarded DDL.** Copied in shape from
  ``services/account_email_uniqueness.py`` for the reasons its docstring gives.
  ``collision-check-removed`` is the one that cannot be killed by a return value:
  the function answers ``False`` either way and SQLite will happily build the
  index, so the test asserts through a recording cursor that no DDL was *issued*.

What this does not claim: killing a mutant shows the control is observed, not
that it is correct.

Usage:  python3 scripts/protection/cart_owner_key_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_harness import run_matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
IDENT = "services/commerce_identity.py"
ROUTES = "services/marketplace_cart_routes.py"
SUITE = "tests/marketplace/test_cart_owner_key.py"

MUTATIONS = [
    # --- the conflict target and the columns written ------------------------
    dict(
        name="conflict-target-back-to-user-id",
        control=(
            "THE PRODUCTION BUG. NULL is not equal to NULL inside a unique index on "
            "either engine, so for a guest this target matches nothing: the second "
            "tap on Add to Cart inserts a second line instead of incrementing the "
            "first, and the MAX_QTY_PER_LINE clamp in the DO UPDATE goes with it. "
            "Indistinguishable from the fix for every signed-in shopper, which is "
            "why one test has to run with guest carts on."
        ),
        path=ROUTES,
        old="            ON CONFLICT(owner_key, listing_id)",
        new="            ON CONFLICT(user_id, listing_id)",
        suites=[SUITE],
    ),
    dict(
        name="owner-key-not-written-on-insert",
        control=(
            "A row with no owner key sits outside the unique index entirely — NULLs "
            "are exempt — so the constraint exists and constrains nothing. The line "
            "is also unreadable, because every read is keyed on owner_key."
        ),
        path=ROUTES,
        old="                owner[\"owner_key\"], owner[\"user_id\"], listing_id, qty, price_minor,",
        new="                None, owner[\"user_id\"], listing_id, qty, price_minor,",
        suites=[SUITE],
    ),
    dict(
        name="user-id-no-longer-written",
        control=(
            "CAPABILITY-shaped: the new key works and the old one is abandoned. "
            "bot.py:114166 and bot.py:114542 clear checked-out lines with "
            "`AND user_id=?`, and UNIQUE(user_id, listing_id) is still on the live "
            "table. Writing both is what lets this change add a constraint without "
            "invalidating anything that reads the old one."
        ),
        path=ROUTES,
        old="                owner[\"owner_key\"], owner[\"user_id\"], listing_id, qty, price_minor,",
        new="                owner[\"owner_key\"], None, listing_id, qty, price_minor,",
        suites=[SUITE],
    ),

    # --- the owner key as an authorisation check ---------------------------
    dict(
        name="read-path-ignores-the-owner",
        control=(
            "One shopper would see every shopper's cart. The owner key is the "
            "lookup key and the authorisation check at once; there is no second "
            "check behind it."
        ),
        path=ROUTES,
        old="        WHERE c.owner_key = ?",
        new="        WHERE (c.owner_key = ? OR 1=1)",
        suites=[SUITE],
    ),
    dict(
        name="quantity-update-drops-the-owner",
        control=(
            "IDOR. Line ids are sequential integers, so anyone could set the "
            "quantity on anyone's cart line. The route still answers 200 and the "
            "cart still works."
        ),
        path=ROUTES,
        old='            "UPDATE marketplace_cart_items SET qty=?, updated_at=? WHERE id=? AND owner_key=?",',
        new='            "UPDATE marketplace_cart_items SET qty=?, updated_at=? WHERE id=? AND (owner_key=? OR 1=1)",',
        suites=[SUITE],
    ),
    dict(
        name="delete-drops-the-owner",
        control="IDOR, destructive half: emptying a stranger's cart one line at a time.",
        path=ROUTES,
        old='            "DELETE FROM marketplace_cart_items WHERE id=? AND owner_key=?",',
        new='            "DELETE FROM marketplace_cart_items WHERE id=? AND (owner_key=? OR 1=1)",',
        suites=[SUITE],
    ),
    dict(
        name="confirm-price-drops-the-owner",
        control=(
            "The quietest of the three: confirming a price change on somebody "
            "else's line re-snapshots it, so the next thing they do is buy at a "
            "price they never saw."
        ),
        path=ROUTES,
        old="            WHERE c.id=? AND c.owner_key=? LIMIT 1",
        new="            WHERE c.id=? AND (c.owner_key=? OR 1=1) LIMIT 1",
        suites=[SUITE],
    ),

    # --- the guest handle --------------------------------------------------
    dict(
        name="guest-key-is-the-raw-token",
        control=(
            "The database stores a digest, never the handle. The token is the "
            "bearer credential for a cart; storing it means a dump, a log line or a "
            "BI export hands over the ability to *be* that shopper. One-way, "
            "following mobile_security_sessions' own precedent."
        ),
        path=IDENT,
        old='    return GUEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()',
        new="    return GUEST_PREFIX + text",
        suites=[SUITE],
    ),
    dict(
        name="guest-key-not-prefixed",
        control=(
            "The prefix is what keeps the two namespaces from ever meeting. "
            "Without it a guest key is 64 hex characters in the same column as "
            "`u:7`, and the only thing preventing a collision is that no user id "
            "is 64 hex characters — a coincidence, not a constraint."
        ),
        path=IDENT,
        old='    return GUEST_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()',
        new='    return hashlib.sha256(text.encode("utf-8")).hexdigest()',
        suites=[SUITE],
    ),
    dict(
        name="guest-token-length-check-removed",
        control=(
            "A short token hashes to a perfectly well-formed owner key, so the "
            "weakness is invisible downstream: the cart works, and it is guessable. "
            "Checked where the key is made rather than trusted from the caller."
        ),
        path=IDENT,
        old="    if len(text) < MIN_GUEST_TOKEN_LENGTH:",
        new="    if False:",
        suites=[SUITE],
    ),
    dict(
        name="peek-accepts-a-token-too-short-to-trust",
        control=(
            "A session carrying a short token — from an older format, or a "
            "truncated write — is re-minted, not used. Without this the length "
            "floor is enforced only on fresh tokens."
        ),
        path=IDENT,
        old="    return text if len(text) >= MIN_GUEST_TOKEN_LENGTH else None",
        new="    return text or None",
        suites=[SUITE],
    ),
    dict(
        name="read-path-mints-a-guest",
        control=(
            "A GET must not create an identity. Minting on reads hands a session "
            "cookie to every crawler, link-preview fetcher and uptime probe that "
            "touches a product page, and leaves a guest handle in the session of "
            "somebody who never got a cart."
        ),
        path=IDENT,
        old="    token = ensure_guest_token(session) if minting else peek_guest_token(session)",
        new="    token = ensure_guest_token(session)",
        suites=[SUITE],
    ),
    dict(
        name="account-loses-to-a-stale-guest-token",
        control=(
            "A signed-in user always wins over a guest handle in the same session. "
            "The inverse serves somebody who has just logged in an anonymous cart "
            "on their own machine — and, worse, keeps writing to it."
        ),
        path=IDENT,
        old="    if account_user:\n        try:",
        new="    if account_user and not (allow_guest and peek_guest_token(session)):\n        try:",
        suites=[SUITE],
    ),
    dict(
        name="unresolvable-user-falls-through-to-guest",
        control=(
            "A signed-in user whose id will not resolve is not a guest. Falling "
            "through silently moves a real shopper into an anonymous cart and loses "
            "their lines; refusing is the answer they can act on."
        ),
        path=IDENT,
        old='            LOGGER.exception("COMMERCE_OWNER_KEY_USER_UNRESOLVABLE")\n            return None, None',
        new='            LOGGER.exception("COMMERCE_OWNER_KEY_USER_UNRESOLVABLE")',
        suites=[SUITE],
    ),
    dict(
        name="a-missing-user-id-keys-a-shared-cart",
        control=(
            "The f-string form of this is `u:None` — one cart that every failed "
            "lookup falls into, shared by strangers, and shaped exactly like a "
            "working feature."
        ),
        path=IDENT,
        old="        raise ValueError(f\"refusing to key a cart to a non-numeric user_id {user_id!r}\")",
        new="        return f\"{USER_PREFIX}{user_id}\"",
        suites=[SUITE],
    ),

    # --- the guarded DDL ---------------------------------------------------
    dict(
        name="index-never-created",
        control="The invariant exists at all. Everything else here is about how.",
        path=IDENT,
        old="        cur.execute(CREATE_INDEX_SQL)",
        new="        pass",
        suites=[SUITE],
    ),
    dict(
        name="backfill-removed",
        control=(
            "Every cart line that predates this change would become unreadable: the "
            "read path is keyed on owner_key, so a returning shopper's cart would "
            "silently appear empty while the rows sit in the table."
        ),
        path=IDENT,
        old="        cur.execute(BACKFILL_SQL)",
        new="        pass",
        suites=[SUITE],
    ),
    dict(
        name="backfill-overwrites-existing-keys",
        control=(
            "The `owner_key IS NULL` half makes the backfill a one-time repair "
            "rather than a rewrite on every boot. Without it a guest row matches, "
            "and `'u:' || CAST(NULL AS TEXT)` is NULL on both engines — so the boot "
            "path itself erases the guest's claim on their own cart."
        ),
        path=IDENT,
        old=f'    f"WHERE {{OWNER_COLUMN}} IS NULL AND user_id IS NOT NULL"',
        new=f'    f"WHERE user_id IS NOT NULL OR {{OWNER_COLUMN}} IS NOT NULL"',
        suites=[SUITE],
    ),
    dict(
        name="catalogue-check-skipped",
        control=(
            "The steady state issues no DDL and so takes no lock. CREATE INDEX IF "
            "NOT EXISTS still takes a ShareLock on PostgreSQL when the index "
            "already exists, and ShareLock conflicts with the RowExclusiveLock an "
            "INSERT needs — the pattern that once hung half of production."
        ),
        path=IDENT,
        old="        if index_exists(cur):\n            return True",
        new="        if False:\n            return True",
        suites=[SUITE],
    ),
    dict(
        name="collision-check-removed",
        control=(
            "Refuses to *attempt* a build the data would reject. A failed CREATE "
            "UNIQUE INDEX poisons the open transaction on PostgreSQL, so the "
            "add-to-cart that called it fails for a reason having nothing to do "
            "with carts. Note the return value is False either way and SQLite "
            "builds the index happily — only a recording cursor can see this one."
        ),
        path=IDENT,
        old="        collisions = colliding_lines(cur)\n        if collisions:",
        new="        collisions = []\n        if collisions:",
        suites=[SUITE],
    ),
    dict(
        name="failure-raises-into-the-boot-path",
        control=(
            "Never stops a boot. This is reached from init_db, where a raise does "
            "not fail loudly — it truncates the schema at that line. bot.py:119868 "
            "records the time that left 49 tables of 586."
        ),
        path=IDENT,
        old="    except Exception as exc:",
        new="    except _NeverRaised as exc:",
        suites=[SUITE],
    ),
]

if __name__ == "__main__":
    sys.exit(run_matrix(ROOT, MUTATIONS))
