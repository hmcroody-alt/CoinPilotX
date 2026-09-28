"""Canonical publication and inventory policy for PulseSoc Marketplace listings.

Publication has a fourth condition alongside listing status, moderation state and
stock: the seller must have a public store name. A buyer has to know who they are
buying from, and "who" is the storefront — see
``services/marketplace_seller_identity``. Allowing a nameless seller to sell would
force every buyer surface to invent an identity, and the only name lying around
is the account holder's personal one. Better to hold the listing back and repair
the seller record (``scripts/marketplace_store_identity_audit.py``).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, NamedTuple, Optional

from services import marketplace_seller_identity as seller_identity


DRAFT = "draft"
PENDING_REVIEW = "pending_review"
#: The merchant has released this listing and no decision has been recorded yet.
#:
#: Spelled out as a constant because it reaches ``status`` and ``approval_status``
#: alike and was a bare literal at every site that wrote it. The distinction it
#: carries is the one :func:`awaiting_moderation` rests on: ``status`` is the
#: merchant's axis, and this is the only value on that axis that means *they have
#: asked*. ``approval_status='pending_review'`` cannot mean it -- the column is
#: ``DEFAULT 'pending_review'``, so every untouched draft is born carrying it.
REVIEW_READY = "review_ready"
CHANGES_REQUESTED = "changes_requested"
APPROVED = "approved"
PUBLISHED = "published"
REJECTED = "rejected"
SUSPENDED = "suspended"
ARCHIVED = "archived"

# ``active`` is the only legacy public value retained. It is still gated by an
# explicit approved moderation state and approved seller, so review-ready rows
# cannot leak into buyer discovery.
PUBLIC_STATUSES = frozenset({PUBLISHED, "live", "active"})
APPROVED_STATES = frozenset({APPROVED})

# The vocabulary both axes use for "no decision recorded yet". ``review_ready``
# reaches both columns: ``revenue_safety_engine`` returns it as an approval
# state, and the seller resume route copies it onto ``status`` as well.
AWAITING_DECISION_STATES = frozenset({PENDING_REVIEW, REVIEW_READY})

# The statuses that mean the merchant has released the listing for review --
# either by submitting it or by publishing it. A ``draft`` is not among them,
# which is the whole protection in :func:`awaiting_moderation`.
MERCHANT_RELEASED_STATUSES = AWAITING_DECISION_STATES | PUBLIC_STATUSES
STOCKLESS_TYPES = frozenset({"digital", "course", "service", "event", "booking"})

#: ``price_label`` values that name no price, normalised by :func:`normalized`.
#:
#: ``marketplace_listings.price_label`` is free text and ``DEFAULT 'Request
#: access'``, so "has a price" is not a NOT NULL question -- an untouched row is
#: born carrying a phrase where a number belongs, and the CJ supplier imports
#: land with the column blank.
#:
#: ``price_label`` rather than ``price_minor`` is deliberate and is the whole
#: reason this rule can exist safely. The label is what the buyer is *charged
#: from*: every money path resolves an amount by parsing it (``bot`` is the only
#: owner of that parser -- see ``parse_price_label_to_cents``, reached through
#: ``marketplace_cart_routes._listing_price_minor`` and again at checkout).
#: ``price_minor`` is a sort key (``marketplace_catalog._PRICED_FIRST``) that no
#: charge is computed from, so gating on it would have let a row with a number
#: there and a blank label past this gate and straight into the same dead end.
#: It is also absent from two of the projections that call :func:`is_public`
#: (``pulsedrop.hydration._LISTING_COLUMNS`` and
#: ``commerce_discovery.eligibility.CANDIDATE_COLUMNS``), both of which do select
#: ``price_label`` -- so a label-based rule is answerable everywhere the gate is
#: actually asked, and a ``price_minor``-based one would have read as "unknown"
#: on those paths and taken healthy listings off sale.
#:
#: Membership, not parsing, is the test. ``public_sql`` has to stay exactly
#: equivalent to :func:`is_public` and cannot run a Python parser, and this
#: module must not import ``bot`` (111k lines, and importing it runs
#: ``init_db()``). The residual gap is a non-empty label that parses to zero:
#: that reads as priced here and is still refused by the cart and by checkout.
#: Being wrong in that direction is deliberate -- it withholds nothing from a
#: healthy product, which is the failure mode a publication gate cannot have.
UNPRICED_LABELS = frozenset({
    "", "request access", "contact", "contact seller", "enquire", "inquire",
})
MATERIAL_FIELDS = frozenset({
    "title", "description", "short_description", "category", "subcategory",
    "price_label", "currency", "cover_image_url", "gallery_json", "video_url",
    "product_type", "listing_type", "listing_metadata_json",
})


def normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def inventory_available(listing: Mapping[str, Any], quantity: int = 1) -> bool:
    product_type = normalized(listing.get("product_type") or listing.get("listing_type"))
    if product_type in STOCKLESS_TYPES:
        return True
    raw = listing.get("quantity")
    if raw is None:
        return False
    try:
        return int(raw) >= max(1, int(quantity))
    except (TypeError, ValueError):
        return False


def seller_identity_missing(listing: Mapping[str, Any]) -> bool:
    """True only when the row proves the seller has no public store name.

    A row that was never projected with a store-name column proves nothing, so it
    is not treated as a failure here; the SQL predicate in :func:`public_sql` is
    where the invariant binds for discovery.
    """
    return (
        seller_identity.store_identity_known(listing)
        and not seller_identity.has_store_identity(listing)
    )


#: Sentinel for "the row was never projected with this column", which is not the
#: same fact as "the column is NULL or empty". ``None`` cannot serve as the
#: sentinel: queries here select ``COALESCE(ms.status,'missing')``, so a falsy
#: value is real evidence while an absent key is no evidence at all.
_UNPROJECTED = object()


def _seller_is_approved(listing: Mapping[str, Any], quantity: int) -> Optional[bool]:
    raw = listing.get("seller_status", _UNPROJECTED)
    if raw is _UNPROJECTED:
        return None
    return normalized(raw) == "approved"


def _seller_is_named(listing: Mapping[str, Any], quantity: int) -> Optional[bool]:
    if not seller_identity.store_identity_known(listing):
        return None
    return seller_identity.has_store_identity(listing)


def _is_released(listing: Mapping[str, Any], quantity: int) -> Optional[bool]:
    if "status" not in listing or "approval_status" not in listing:
        return None
    return (
        normalized(listing.get("status")) in PUBLIC_STATUSES
        and normalized(listing.get("approval_status")) in APPROVED_STATES
    )


def _is_in_stock(listing: Mapping[str, Any], quantity: int) -> Optional[bool]:
    if normalized(listing.get("product_type") or listing.get("listing_type")) in STOCKLESS_TYPES:
        return True
    if "quantity" not in listing:
        return None
    return inventory_available(listing, quantity)


def _is_priced(listing: Mapping[str, Any], quantity: int) -> Optional[bool]:
    """Whether the row names a price a buyer could be charged.

    Unprojected is ``None`` on the key itself rather than via ``_UNPROJECTED``:
    the queries that select this column select it bare, so ``''`` here is a real
    blank label and not a coalesced stand-in for a missing one.
    """
    if "price_label" not in listing:
        return None
    return normalized(listing.get("price_label")) not in UNPRICED_LABELS


class PublicationRule(NamedTuple):
    """One condition a listing must satisfy before a buyer can reach it.

    ``satisfied`` returns three answers, not two: ``True`` (met), ``False`` (the
    row *proves* it is not met), and ``None`` (the row was not projected with
    the columns needed to judge). That third answer is why this table exists at
    all -- see :data:`PUBLICATION_RULES`.
    """

    key: str
    #: Buyer-facing code. Already in the wire contract, so several rules share
    #: one: a buyer's next move after "suspended seller" and after "seller has
    #: no store name" is identical, namely none.
    denial_code: str
    #: The chip the owning merchant sees in place of "Live".
    seller_label: str
    #: Clause a moderation response uses, after "still not visible to buyers:".
    moderator_note: str
    satisfied: Callable[[Mapping[str, Any], int], Optional[bool]]
    #: What a *gate* does when ``satisfied`` answers ``None``.
    passes_when_unknown: bool
    #: Whether this rule bears on *buying* only, and not on whether the row may
    #: be shown at all. Default ``False``: almost every condition here is both.
    #:
    #: The distinction is not new -- ``marketplace_seo.eligibility`` already
    #: states it ("Path-level and record-level eligibility are different
    #: questions and answering them in one place is how a rule for the section
    #: ends up deciding a fact about a row"). Only ``priced`` sets it, and
    #: :data:`VISIBILITY_RULES` explains why.
    purchase_only: bool = False


#: Every publication condition, in the order the reason is reported.
#:
#: There is one table because there are now three consumers that must agree
#: about what "purchasable" means, and the way they disagreed was the defect.
#: :func:`seller_label` checked two of these conditions and answered "Live";
#: :func:`is_public` checked five and answered no. A moderator could approve a
#: listing, get HTTP 200 and "Listing updated.", and leave it invisible to every
#: buyer while its own merchant's dashboard said Live and nothing anywhere said
#: why. Approving is not the same act as publishing, and the difference has to
#: be derived once or the surfaces drift again.
#:
#: The three consumers need the same conditions in the same order but must treat
#: an unanswerable rule differently:
#:
#:   * :func:`public_denial_code` / :func:`is_public` guard a sale, so silence
#:     takes ``passes_when_unknown`` -- almost always ``False``, because the safe
#:     default for a gate is "no".
#:   * :func:`publication_blocker` *describes* a listing to the person who owns
#:     it, so silence is never a blocker. The safe default for a description is
#:     not only "do not claim what you cannot see" but its mirror, "do not deny
#:     what you cannot see": half the queries that build a merchant payload never
#:     select ``seller_status``, and reading that absence as failure would strip
#:     "Live" from every healthy listing on those paths.
#:
#: ``seller_named`` is the one rule a gate lets pass while unknown, and that is
#: pre-existing deliberate behaviour, not an oversight: the store-name invariant
#: binds in SQL (:func:`public_sql`), and treating an unprojected name column as
#: "no store" would take healthy listings off sale on every path that fetches
#: fewer columns. :func:`seller_identity_missing` documents the same rule.
PUBLICATION_RULES: tuple[PublicationRule, ...] = (
    PublicationRule(
        key="seller_approved",
        denial_code="SELLER_UNAVAILABLE",
        seller_label="Store offline",
        moderator_note="the seller account is not approved",
        satisfied=_seller_is_approved,
        passes_when_unknown=False,
    ),
    PublicationRule(
        key="seller_named",
        denial_code="SELLER_UNAVAILABLE",
        seller_label="Store name needed",
        moderator_note="the seller has no public store name",
        satisfied=_seller_is_named,
        passes_when_unknown=True,
    ),
    PublicationRule(
        key="released",
        denial_code="ITEM_UNAVAILABLE",
        seller_label="Not published",
        moderator_note="the listing is not both published and approved",
        satisfied=_is_released,
        passes_when_unknown=False,
    ),
    PublicationRule(
        key="in_stock",
        denial_code="OUT_OF_STOCK",
        seller_label="Out of stock",
        moderator_note="the listing has no stock",
        satisfied=_is_in_stock,
        passes_when_unknown=False,
    ),
    # Last on purpose, and the position is the only thing about this rule that is
    # a judgement call. Every rule above it already reports a reason for some
    # listing in production; inserting this one earlier would relabel rows that
    # are held by an earlier condition anyway, silently changing the reason 62
    # out-of-stock CJ imports give their own merchant without changing whether
    # anybody can buy them. A listing that is both unpriced and sold out is more
    # usefully described as sold out -- the same ordering argument
    # ``pulsedrop.hydration.state`` already makes for ``NOT_PRICED``.
    PublicationRule(
        key="priced",
        # Shared with ``released``, per the field's own contract: the buyer's next
        # move is identical, and "this seller forgot to type a price" is the
        # merchant's business and not a stranger's.
        denial_code="ITEM_UNAVAILABLE",
        seller_label="Needs a price",
        moderator_note="the listing has no price",
        satisfied=_is_priced,
        passes_when_unknown=False,
        purchase_only=True,
    ),
)

RULES_BY_KEY = {rule.key: rule for rule in PUBLICATION_RULES}

#: The rules that bear on whether a row may be *shown*, as opposed to bought.
#:
#: Everything except ``priced``, and the exclusion is load-bearing rather than
#: cautious. An unpriced listing is a perfectly good public web page: the product
#: page renders it with no price pill and no schema.org ``Offer`` (an ``Offer``
#: without a price is a malformed claim, not an absent one), and it stays in the
#: sitemap. ``marketplace_seo.eligibility`` encodes that as ``indexable=True,
#: feed_eligible=False`` and says why -- filtering pages out of Search to satisfy
#: a rule Search does not have withholds real pages for nothing. Putting the
#: price condition into :func:`is_public` would 404 those pages and pull their
#: URLs from the sitemap, which is a worse defect than the one it fixes.
#:
#: So the price is a condition on *buying*: :func:`is_purchasable` and
#: :func:`purchasable_sql` apply it, :func:`is_public` and :func:`public_sql` do
#: not, and the description-side readers (:func:`publication_blocker`,
#: :func:`seller_label`, :func:`live_blocker`) always do -- the merchant who owns
#: an unpriced listing is the one person who can fix it, and they are told.
VISIBILITY_RULES: tuple[PublicationRule, ...] = tuple(
    rule for rule in PUBLICATION_RULES if not rule.purchase_only
)


def _first_unmet(
    rules: tuple[PublicationRule, ...], listing: Mapping[str, Any], quantity: int
) -> Optional[PublicationRule]:
    for rule in rules:
        verdict = rule.satisfied(listing, quantity)
        if verdict is None:
            verdict = rule.passes_when_unknown
        if not verdict:
            return rule
    return None


def failing_rule(listing: Mapping[str, Any], quantity: int = 1) -> Optional[PublicationRule]:
    """The first rule a *visibility* gate considers unmet, else ``None``.

    Unchanged in meaning: ``priced`` is ``purchase_only`` and so is not consulted
    here. Use :func:`failing_purchase_rule` before taking money.
    """
    return _first_unmet(VISIBILITY_RULES, listing, quantity)


def failing_purchase_rule(
    listing: Mapping[str, Any], quantity: int = 1
) -> Optional[PublicationRule]:
    """The first rule standing between this row and a completed purchase.

    Every visibility rule plus the price. This is the predicate a Buy affordance
    belongs behind: the money paths already refuse an unpriced row -- the cart
    with 400 ``no_price`` and checkout with "currently free or not priced for
    checkout" -- so a surface that offers one is promising something the till has
    already decided to decline.
    """
    return _first_unmet(PUBLICATION_RULES, listing, quantity)


def publication_blocker(listing: Mapping[str, Any], quantity: int = 1) -> str:
    """The first rule this row *proves* is unmet, as a rule key, else ``""``.

    The description-side reading of :data:`PUBLICATION_RULES`: a rule that the
    row cannot answer is skipped rather than counted against the listing. Use
    this anywhere the audience owns the listing -- the merchant's own status
    chip, a moderator's confirmation -- and :func:`is_public` anywhere the
    question is whether a stranger may buy it.
    """
    for rule in PUBLICATION_RULES:
        if rule.satisfied(listing, quantity) is False:
            return rule.key
    return ""


def live_blocker(listing: Mapping[str, Any], quantity: int = 1) -> str:
    """The blocker on a listing whose own two columns say it should be live.

    ``""`` both when the listing really is reachable and when it was never
    supposed to be -- a draft, a paused listing, one still in review. Those
    already have an accurate label of their own, and running them through the
    publication rules would replace "Draft" with "Out of stock": true, useless,
    and hiding the thing the merchant actually needs to act on.

    So this answers one question only, the surprising one: *the merchant
    published it and a moderator approved it, so why can nobody buy it?* Both
    the merchant's status chip and the client payload derive from this, which is
    what stops the native app from re-deriving publication out of ``status``
    and reaching a different answer than the server.
    """
    if (
        normalized(listing.get("status")) in PUBLIC_STATUSES
        and normalized(listing.get("approval_status")) in APPROVED_STATES
    ):
        return publication_blocker(listing, quantity)
    return ""


def blocker_note(blocker_key: str) -> str:
    """Clause naming a blocker, for a moderator. ``""`` for an unknown key."""
    rule = RULES_BY_KEY.get(blocker_key)
    return rule.moderator_note if rule else ""


def is_public(listing: Mapping[str, Any]) -> bool:
    """Whether a stranger may be shown this row at all. See :data:`VISIBILITY_RULES`."""
    return failing_rule(listing) is None


def is_purchasable(listing: Mapping[str, Any], quantity: int = 1) -> bool:
    """Whether a stranger may be offered this row for sale. Implies :func:`is_public`."""
    return failing_purchase_rule(listing, quantity) is None


def public_denial_code(listing: Mapping[str, Any], quantity: int = 1) -> str:
    """Why this listing is not purchasable, as a stable client-facing code.

    Returns ``""`` when the listing *is* purchasable. Buyer clients branch on
    this rather than on prose, and the three outcomes are genuinely different
    next moves: a suspended seller is nobody's fault and nothing the buyer can
    retry, an unavailable listing may come back, and out-of-stock means lower
    the quantity or wait for a restock. Collapsing them into one "unavailable"
    message is what makes a marketplace feel broken.
    """
    rule = failing_purchase_rule(listing, quantity)
    return rule.denial_code if rule else ""


def _unpriced_labels_sql() -> str:
    """The ``UNPRICED_LABELS`` set as a SQL ``IN`` list.

    Rendered from the frozenset rather than written out beside it so the SQL half
    of the ``priced`` rule cannot drift from the Python half -- adding a phrase in
    one place and not the other is precisely the class of bug this whole module
    exists to prevent. Sorted for a deterministic predicate, and quotes are
    doubled so a future entry containing an apostrophe cannot break out of the
    literal.
    """
    return ", ".join("'%s'" % label.replace("'", "''") for label in sorted(UNPRICED_LABELS))


def price_sql(alias: str = "l") -> str:
    """SQL equivalent of the ``priced`` rule.

    ``LOWER(TRIM(COALESCE(...)))`` is :func:`normalized` spelled in SQL, which is
    what makes this clause and :func:`_is_priced` the same test rather than two
    similar ones. Exposed on its own because :func:`public_sql` deliberately
    excludes it -- see :data:`VISIBILITY_RULES` -- so a surface that gates a Buy
    affordance in SQL has something to add.
    """
    return (
        f"LOWER(TRIM(COALESCE({alias}.price_label,''))) "
        f"NOT IN ({_unpriced_labels_sql()})"
    )


def purchasable_sql(alias: str = "l", seller_alias: str = "ms") -> str:
    """SQL equivalent of :func:`is_purchasable`. Use this to gate a sale."""
    return f"{public_sql(alias, seller_alias)} AND {price_sql(alias)}"


def public_sql(alias: str = "l", seller_alias: str = "ms") -> str:
    """SQL equivalent of :func:`is_public` for buyer discovery surfaces.

    Does not require a price; :func:`purchasable_sql` does.
    """
    return (
        f"LOWER(COALESCE({alias}.status,'')) IN ('published','live','active') "
        f"AND LOWER(COALESCE({alias}.approval_status,''))='approved' "
        f"AND LOWER(COALESCE({seller_alias}.status,''))='approved' "
        # The store-name invariant. Every caller of this predicate already joins
        # the seller row for its status, so this costs no extra join.
        f"AND {seller_identity.store_name_sql(seller_alias)} IS NOT NULL "
        f"AND (LOWER(COALESCE({alias}.product_type,{alias}.listing_type,'')) "
        "IN ('digital','course','service','event','booking') "
        f"OR COALESCE({alias}.quantity,0)>0)"
    )


def awaiting_moderation(listing: Mapping[str, Any]) -> bool:
    """True when a moderator may still record a first decision on this listing.

    Two independent axes, and the question needs both. ``approval_status`` is
    the moderation axis and answers "has a decision been recorded"; ``status``
    is the merchant's axis and answers "has the merchant released this". A
    moderator may act only where the answer is no-and-yes.

    Asking only ``status`` -- which is what ``/admin/marketplace-command`` did,
    and the reason this function exists -- gets the dropship path wrong. The
    supplier package publishes through ``drafts.publish``, which sets
    ``status='published'`` and deliberately leaves moderation untouched,
    because :func:`is_public` requires *both* axes and so a published,
    unapproved listing is correctly invisible. The admin guard read
    ``status='published'`` as "already decided" and returned 409, which made
    approval unreachable: the merchant could not submit their way back to
    ``pending_review`` and the moderator could not approve. Every CJ listing
    published this way needed a hand-written UPDATE to go live.

    Asking only ``approval_status`` swaps one bug for a worse one. The column
    is ``DEFAULT 'pending_review'``, so an untouched draft -- unpriced, no
    cover, quantity 0, never seen by its own merchant's publish validation --
    would read as awaiting review and a moderator could publish it in one
    click. Hence the conjunction.
    """
    return (
        normalized(listing.get("approval_status")) in AWAITING_DECISION_STATES
        and normalized(listing.get("status")) in MERCHANT_RELEASED_STATUSES
    )


def awaiting_moderation_sql(alias: str = "l") -> str:
    """SQL equivalent of :func:`awaiting_moderation` for the review queue."""
    approval = "', '".join(sorted(AWAITING_DECISION_STATES))
    released = "', '".join(sorted(MERCHANT_RELEASED_STATUSES))
    return (
        f"LOWER(COALESCE({alias}.approval_status,'')) IN ('{approval}') "
        f"AND LOWER(COALESCE({alias}.status,'')) IN ('{released}')"
    )


def requires_rereview(changed_fields: set[str]) -> bool:
    return bool(MATERIAL_FIELDS.intersection(changed_fields))


def seller_label(listing: Mapping[str, Any]) -> str:
    """The publication chip on the merchant's own copy of their listing.

    "Live" is a claim about what buyers can reach, so it is answered from
    :data:`PUBLICATION_RULES` rather than from the two columns a merchant
    happens to control. Those two -- ``status`` and ``approval_status`` -- are
    what a merchant and a moderator between them can set, and setting both used
    to be enough to print "Live". It is not: publication also needs an approved,
    named seller and stock. A merchant whose store was suspended, or who never
    finished naming their storefront, was shown "Live" on a listing no buyer
    query would return, and the only remaining explanation for zero orders was
    that nobody wanted the product.

    The downgrade is deliberately narrow. It fires only on a rule the row
    *proves* unmet (:func:`publication_blocker`), never on one the row is merely
    silent about, because a payload assembled from a query that did not join
    ``marketplace_sellers`` knows nothing about the seller and must not turn
    that ignorance into an accusation.
    """
    blocker = live_blocker(listing)
    if blocker:
        return RULES_BY_KEY[blocker].seller_label
    status = normalized(listing.get("status")) or DRAFT
    approval = normalized(listing.get("approval_status"))
    if status in PUBLIC_STATUSES and approval == APPROVED:
        return "Live"
    return {
        DRAFT: "Draft",
        "submitted": "Submitted",
        PENDING_REVIEW: "In review",
        "review_ready": "In review",
        CHANGES_REQUESTED: "Changes requested",
        APPROVED: "Approved",
        REJECTED: "Rejected",
        SUSPENDED: "Suspended",
        ARCHIVED: "Archived",
        "paused": "Paused",
    }.get(status, "In review" if approval in {"review_ready", "needs_review"} else status.replace("_", " ").title())
