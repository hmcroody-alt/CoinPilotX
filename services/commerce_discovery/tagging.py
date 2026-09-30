"""Products a creator attached to their own content.

This is the write path the rest of the package has been reserving vocabulary for.
``relationship.CREATOR_TAGGED`` existed, was documented as "the creator attached
this product to this post", and was listed in
``relationship.UNIMPLEMENTED_RELATIONSHIPS`` because nothing in the repository
could produce it: there was no post↔listing relation anywhere, under any name.

Why it matters more than one more retrieval source. Every other source in
``pool`` is an *inference* — this viewer likes cameras, this post mentions a
tripod, the crowd is looking at lenses. A creator tag is the one edge in the
system that is a *statement*: the person who made the post says this is the
product in it. That makes it both the most useful signal available and the one
with the least excuse for being wrong, which is why most of this module is
refusals.

Shaped after ``pulse_content_music`` deliberately — the same polymorphic
``(content_type, content_id)`` key, written by the composer, resolved by a
reader. Two departures, both stated in ``bot.init_db`` beside the table and
repeated here because they are the parts a reader will want to argue with:

* **``seller_user_id`` is stored** and re-checked against the live listing on
  every read. It is the seller the tag was *authorised against*. A listing that
  changes hands afterwards carries a permission its new owner never granted, and
  the read drops it rather than serving it.
* **Nothing else is snapshotted.** Music snapshots a licence because a stale song
  is still the song. Price, title and availability are read live from
  ``marketplace_listings`` on every serve, because a stale price is not a stale
  copy of the truth, it is a lie to a buyer.

What this module does not do, and the reason is not squeamishness:

**A creator may only tag a listing they own.** Tagging someone else's product is
affiliate marketing. It needs a commission model, a disclosure obligation that
differs by jurisdiction, and a decision about whether PulseSoc takes a cut —
none of which are engineering decisions, and all of which are much harder to
withdraw than to delay. So the check is ownership, the refusal is explicit
(:data:`REFUSED_NOT_OWNER`), and :data:`AUTHORITY_OWNER` is recorded on every row
so that the day a second authority exists, the rows written under this one are
still distinguishable. A test asserts nothing writes any other value.

On the honesty of the resulting label: the engine looks the tag up by the post id
the *client* supplied, and ``commerce_discovery_routes._content_post_id`` already
records that this id is forgeable. That argument was written for a restrictive
use — a forged id can only make the suitability gate answer about the wrong post
— and this use is additive, which is a different risk class and does not inherit
the conclusion. Re-derived: a forged id returns products genuinely tagged on
*that* post, so the server never invents an edge; it lets a hostile client
display a true edge next to the wrong content. A hostile client can draw that
carousel without asking us, and every listing returned still passes
``eligibility`` and ``promotion.assert_unpaid``. So the server grants no
capability a hostile client did not already have, and the label stays truthful
about what it claims: this product was tagged on post N.

The suitability gate is upstream of all of this. A creator tag cannot force
commerce onto a post ``suitability.assess`` refuses — bereavement, medical,
distress — because retrieval never runs on a refused post. That ordering is the
brief's whole point and there is a test for it here, not because the code is
subtle but because a future refactor that "optimises" tagged lookups to happen
before the gate would be a product failure with a green suite.
"""

from __future__ import annotations

import logging
from typing import Any

from . import subject

LOGGER = logging.getLogger(__name__)

#: The attachment table. Owned by ``bot.init_db`` beside ``pulse_content_music``,
#: not by this package's ``schema.ensure_schema`` — the writer is the composer,
#: and making a post save depend on the discovery package's schema guard would be
#: the wrong direction for that dependency.
TABLE = "pulse_content_products"

#: Content kinds that can carry an attachment. The same four
#: ``pulse_attach_music_to_content`` accepts, and for the same reason: these are
#: the things a composer creates that a viewer later reads.
CONTENT_TYPES = ("post", "video", "reel", "status")

#: How many products one piece of content may carry.
#:
#: Enforced on *both* sides, which is not redundancy. The write cap stops a
#: composer creating a spam post; the read limit stops rows that predate a cap
#: change — or were written by some future second writer — from filling a pool
#: that ``pool`` will hand ``tagged`` an unbounded quota for. A read path that
#: trusts a write-time invariant is trusting every past version of the writer.
MAX_TAGGED_PER_CONTENT = 5

#: Why a tag was allowed. One value today; see the module docstring.
AUTHORITY_OWNER = "owner"

REFUSED_BAD_INPUT = "invalid_reference"
REFUSED_UNKNOWN_CONTENT = "unknown_content_type"
REFUSED_NO_LISTING = "listing_not_found"
REFUSED_NOT_OWNER = "not_listing_owner"
REFUSED_CAP = "too_many_products"

#: Every refusal this module can return. Declared as a set so a test can assert
#: :func:`attach` never returns a reason outside it, rather than restating them.
REFUSALS = frozenset({
    REFUSED_BAD_INPUT, REFUSED_UNKNOWN_CONTENT, REFUSED_NO_LISTING,
    REFUSED_NOT_OWNER, REFUSED_CAP,
})


def normalize_content_type(value: Any) -> str:
    """Canonical content kind, or ``""`` when the value names none.

    Empty rather than a default, for ``relationship.normalize``'s reason: a
    fallback would file an attachment against whichever kind happened to be first
    in the tuple, and an attachment on the wrong kind is invisible rather than
    wrong-looking — ``content_id`` spaces overlap across these tables.
    """
    text = str(value or "").strip().lower()
    return text if text in CONTENT_TYPES else ""


def _int(value: Any) -> int:
    """A positive row id, or ``0``.

    A *lossy* coercion is rejected rather than truncated, which is the one thing
    here that is not the obvious `int()`. ``2.5`` is not post 2; it is a malformed
    reference, and the module docstring's whole argument for refusing rather than
    defaulting applies to it with more force than to a bad ``content_type``. A tag
    filed against a silently truncated id points at a stranger's content and shows
    no symptom to anybody — `content_id` spaces overlap across these tables, so the
    row is not even obviously orphaned.

    ``2.0`` is accepted, because JSON has no integer type and a client that sent
    ``2.0`` meant 2. The test is integrality, not type.
    """
    if isinstance(value, bool):
        # `True` is an `int` in Python and would otherwise be listing 1.
        return 0
    if isinstance(value, float):
        if value != int(value):
            return 0
        value = int(value)
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _refused(reason: str, message: str) -> dict:
    return {"ok": False, "reason": reason, "message": message}


def attach(cur, *, content_type: Any, content_id: Any, listing_id: Any, user_id: Any) -> dict:
    """Attach one owned listing to one piece of content.

    Returns ``{"ok": True, ...}`` or ``{"ok": False, "reason": <one of
    :data:`REFUSALS`>, "message": ...}``. Never raises for a refusal, because the
    caller is a post-create route and a rejected product tag must not lose the
    post — the same fail-soft shape ``pulse_attach_music_to_content`` uses for a
    track that is not creator-safe.

    Exceptions from the database are *not* swallowed here. A refusal is a
    judgement this function made and can explain; a driver error is not, and the
    composer's own handler is where that decision belongs.
    """
    kind = normalize_content_type(content_type)
    if not kind:
        return _refused(
            REFUSED_UNKNOWN_CONTENT,
            "Products can only be attached to a post, video, reel or status.",
        )

    content_ref = _int(content_id)
    listing_ref = _int(listing_id)
    owner_ref = _int(user_id)
    if not content_ref or not listing_ref or not owner_ref:
        return _refused(REFUSED_BAD_INPUT, "A product tag needs content, a product and an owner.")

    cur.execute(
        "SELECT seller_user_id FROM marketplace_listings WHERE id=? LIMIT 1",
        (listing_ref,),
    )
    row = cur.fetchone()
    if not row:
        return _refused(REFUSED_NO_LISTING, "That product could not be found.")
    seller_ref = _int(_column(row, "seller_user_id", 0))

    # Ownership, not moderation. Whether the *listing* may be shown is
    # `eligibility`'s question and is asked again on every serve; whether this
    # person may point at it is this one, and it is asked once, here.
    if not seller_ref or seller_ref != owner_ref:
        return _refused(
            REFUSED_NOT_OWNER,
            "You can only attach products from your own store.",
        )

    cur.execute(
        f"SELECT COUNT(*) AS n FROM {TABLE} WHERE content_type=? AND content_id=?",
        (kind, content_ref),
    )
    existing = _int(_column(cur.fetchone(), "n", 0))
    if existing >= MAX_TAGGED_PER_CONTENT:
        return _refused(
            REFUSED_CAP,
            f"A post can carry at most {MAX_TAGGED_PER_CONTENT} products.",
        )

    # `INSERT OR IGNORE` against the UNIQUE key, so re-tagging the same product is
    # idempotent rather than an error. A composer that retries a save must not
    # turn a duplicate into a failed post.
    cur.execute(
        f"INSERT OR IGNORE INTO {TABLE} "
        "(content_type, content_id, listing_id, attached_by_user_id, seller_user_id, authority, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, content_ref, listing_ref, owner_ref, seller_ref, AUTHORITY_OWNER, subject.now_iso()),
    )
    return {
        "ok": True,
        "content_type": kind,
        "content_id": content_ref,
        "listing_id": listing_ref,
        "authority": AUTHORITY_OWNER,
    }


def tagged_listing_ids(cur, *, content_type: Any, content_id: Any) -> tuple[int, ...]:
    """Listings the creator attached to this content, oldest first.

    Oldest first because the order is the creator's: the first product they
    attached is the one the post is about. Ranking may reorder within the pool,
    but the retrieval order should not start by discarding the only ordering
    anybody intended.

    Returns ``()`` on any failure, and says so in the log. Fail-soft per §82 —
    commerce must never break the post — but *not* silently: this is the one
    source whose absence downgrades an explicit creator statement to a guess, and
    the symptom (a tagged post showing unrelated products) looks exactly like a
    ranking complaint. An operator chasing that needs to be able to find this
    line.
    """
    kind = normalize_content_type(content_type)
    content_ref = _int(content_id)
    if not kind or not content_ref:
        return ()

    try:
        cur.execute(
            f"SELECT p.listing_id AS listing_id FROM {TABLE} p "
            "JOIN marketplace_listings l ON l.id = p.listing_id "
            # The stale-authorisation drop, in the join rather than in Python so
            # a transferred listing costs nothing to exclude. `p.seller_user_id`
            # is who owned it when the tag was made; `l.seller_user_id` is who
            # owns it now. When they differ, nobody living granted this.
            "WHERE p.content_type=? AND p.content_id=? "
            "AND l.seller_user_id = p.seller_user_id "
            "ORDER BY p.id ASC LIMIT ?",
            (kind, content_ref, MAX_TAGGED_PER_CONTENT),
        )
        rows = cur.fetchall() or []
    except Exception:
        LOGGER.warning(
            "COMMERCE_DISCOVERY_TAGGED_LOOKUP_FAILED content_type=%s content_id=%s",
            kind, content_ref, exc_info=True,
        )
        return ()

    found: list[int] = []
    for row in rows:
        listing_ref = _int(_column(row, "listing_id", 0))
        if listing_ref and listing_ref not in found:
            found.append(listing_ref)
    return tuple(found)


def _column(row: Any, name: str, index: int) -> Any:
    """One column from a row that may be a mapping or a sequence.

    Both shapes occur: the app's connections return mappings, and a hand-rolled
    test cursor or a raw driver returns tuples. Iterating a row is *not* an option
    here — on SQLite that yields values and on Postgres it yields column names,
    which is a repo-wide trap — so the access is explicit both ways.
    """
    if row is None:
        return None
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        pass
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return None
