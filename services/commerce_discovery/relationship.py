"""How a product got next to this content — the third axis, and the missing one.

This package already records two things about every card, and they are often
mistaken for each other:

``promotion_class`` (``promotion.py``)
    Who *funded* it. organic / house / paid.

``reason_code`` (``ranking.py``)
    What the buyer is *told*. "Related to this post", "Because you viewed Rings".
    A user-visible claim, chosen as the most specific true statement available.

Neither one answers the question the brief's §6 asks, which is **how the product
came to be here**. That is a third, independent axis, and until this module it was
not recorded anywhere:

* ``reason_code`` is computed in :func:`ranking.choose_reason` from *signal
  thresholds*, with no knowledge of which retrieval question produced the row. So
  a listing pulled from the untargeted ``rotation`` source earns
  ``related_to_this_post`` whenever its relevance happens to clear the bar, and is
  afterwards indistinguishable from one retrieved *because* it matched the post.
* ``candidate_source`` (``pool.py``) does know, and it is stamped on every pool
  row — but it is aggregated to a per-surface count by
  :func:`metrics.observe_sources` and never persisted per placement. That
  function's own docstring says so, and names the reason: this package's schema
  layer was ``CREATE TABLE IF NOT EXISTS`` with no ALTER path, so a new column
  would apply on a fresh database and silently not apply to production.

§6 says the relationship types "MUST NOT be silently conflated". They were, and
not through carelessness: the axis that would keep them apart did not exist.

What each value means
---------------------

``contextual``
    The content on screen matched. Earned, not assumed — the relevance signal
    cleared the same threshold :func:`ranking.choose_reason` requires before it
    will make a relatedness claim out loud.

``personalized``
    The *viewer* matched: their affinities or the sellers they follow brought this
    row back, and the content on screen did not match it.

``similar``
    Like the product being viewed. The product-page form of ``contextual`` — the
    subject is a listing rather than a post.

``catalogue``
    Nothing about the content and nothing about the viewer. The ranker chose it on
    catalogue merit: trending, or the untargeted rotation.

``creator_tagged``
    The creator attached this product to this post. **Not producible today** — see
    below.

``complementary``
    Goes *with* the product being viewed, rather than competing with it. **Not
    producible today.**

``pulsedrop_curated``
    Selected by the PulseDrop curator rather than by this engine. **Not producible
    today.**

``sponsored``
    Seller-funded. **Never producible here at all**, and :func:`assert_servable`
    raises on it — the wall ``promotion.assert_unpaid`` guards, said on this axis
    too.

On ``catalogue``, which §6 does not list
----------------------------------------

Two of the four live retrieval sources — ``trending`` and ``rotation`` — are
neither about the content nor about the viewer. §6's vocabulary has no name for
that, and the available moves were:

1. Map them onto ``contextual`` or ``personalized``. This is precisely the silent
   conflation §6 forbids, and it would do the most damage to the one audit the
   axis exists to enable: every untargeted card would be counted as a contextual
   match, and "is personalization overriding context?" would answer *no* by
   construction.
2. Leave them unclassified (``""``). Then the majority of placements on a cold
   viewer carry no relationship at all, and an empty string reads as a bug rather
   than as a fact.
3. Name the case.

Third. Extending the vocabulary to cover a real case is the rule-respecting move;
squeezing a real case into a name that does not fit is the violation. Flagged in
the report rather than decided quietly, because §6 enumerated seven types and this
is an eighth.

On the three that are declared but cannot be produced
-----------------------------------------------------

They are constants with no code path, deliberately. Each needs a store this
product does not have — ``creator_tagged`` a post↔listing relation (there is no
such table anywhere in the repo; ``pulse_content_music`` is the nearest shape),
``complementary`` a complement graph, ``pulsedrop_curated`` a bridge from the
other curator, which is off in production and shares no ledger with this one.

Declaring them without producing them is not dead code. It fixes the wire values
now, while nothing depends on them, so that the write paths land against a
vocabulary rather than inventing one each. The alternative — adding names later,
alongside the features — is how ``creator_tagged`` and ``creator-tagged`` and
``tagged_by_creator`` end up in the same column.

:func:`classify` never returns one of the three, and a test pins that, so an
unimplemented value cannot leak onto a placement row and be read as a feature
that works.
"""

from __future__ import annotations

from typing import Any, Optional

from . import ranking

#: The creator attached this product to this post. Not producible yet.
CREATOR_TAGGED = "creator_tagged"

#: The content on screen matched.
CONTEXTUAL = "contextual"

#: The viewer matched; the content did not.
PERSONALIZED = "personalized"

#: Like the product being viewed.
SIMILAR = "similar"

#: Goes with the product being viewed. Not producible yet.
COMPLEMENTARY = "complementary"

#: Chosen by the PulseDrop curator. Not producible yet.
PULSEDROP_CURATED = "pulsedrop_curated"

#: Seller-funded. Never produced by this package — see :func:`assert_servable`.
SPONSORED = "sponsored"

#: Catalogue merit alone: neither the content nor the viewer. See the module
#: docstring for why this is named rather than folded into one of §6's seven.
CATALOGUE = "catalogue"

#: Every relationship that exists anywhere in the product.
ALL_RELATIONSHIPS = frozenset({
    CREATOR_TAGGED, CONTEXTUAL, PERSONALIZED, SIMILAR,
    COMPLEMENTARY, PULSEDROP_CURATED, SPONSORED, CATALOGUE,
})

#: What this package may serve and record. ``SPONSORED`` is absent for
#: ``promotion.assert_unpaid``'s reason; the other three are absent because
#: nothing can produce them yet, and admitting them would let a typo'd or hostile
#: value claim a provenance no code path can create.
SERVABLE_RELATIONSHIPS = frozenset({CONTEXTUAL, PERSONALIZED, SIMILAR, CATALOGUE})

#: Declared, intended, and not yet producible. Named as a set so a test can assert
#: :func:`classify` never returns one, rather than restating the list.
UNIMPLEMENTED_RELATIONSHIPS = frozenset({
    CREATOR_TAGGED, COMPLEMENTARY, PULSEDROP_CURATED,
})

#: Retrieval sources that are about the *viewer*. Both are ``pool`` source names;
#: kept as a set here rather than imported so that adding a source to ``pool``
#: cannot silently reclassify it — a new source falls through to
#: :data:`CATALOGUE`, which understates rather than overstates what we know.
VIEWER_SOURCES = frozenset({"affinity", "followed"})


class RelationshipError(ValueError):
    """A relationship crossed a boundary it is not allowed to cross."""


def normalize(value: Any) -> str:
    """Canonical relationship name, or ``""`` when the value names none.

    Unknown input returns empty rather than falling back to :data:`CATALOGUE`.
    A default would make the axis useless in exactly the case it matters: a
    malformed value would be silently accounted as "the ranker's own choice",
    which is the one value that claims no relationship to anything and therefore
    raises no eyebrow in a report.
    """
    text = str(value or "").strip().lower()
    return text if text in ALL_RELATIONSHIPS else ""


def is_servable(value: Any) -> bool:
    """True when this package may serve and record a card with this provenance."""
    return normalize(value) in SERVABLE_RELATIONSHIPS


def assert_servable(value: Any) -> str:
    """Return the relationship, or raise if this package must not record it.

    Rejects :data:`SPONSORED` explicitly and for ``promotion.assert_unpaid``'s
    reason: a sponsored placement arriving here would go unbilled *and* inflate
    organic reach. Rejects the three unimplemented values too, because a row
    claiming ``creator_tagged`` when no tagging feature exists is a fabricated
    provenance — worse than an unknown one, since it would be believed.
    """
    normalized = normalize(value)
    if normalized == SPONSORED:
        raise RelationshipError(
            "sponsored placements are served by services/business_os/advertising, "
            "never by commerce_discovery — recording one here would skip billing "
            "and overstate unpaid reach"
        )
    if normalized in UNIMPLEMENTED_RELATIONSHIPS:
        raise RelationshipError(
            f"{normalized!r} is a declared relationship with no write path yet; "
            "a placement claiming it would report a provenance nothing produced"
        )
    if normalized not in SERVABLE_RELATIONSHIPS:
        raise RelationshipError(f"unknown relationship: {value!r}")
    return normalized


def classify(
    *,
    candidate_source: Any,
    signals: Optional[dict] = None,
    subject_is_product: bool = False,
    context_offered: bool = False,
) -> str:
    """How this row came to be here, from what retrieval and ranking actually did.

    ``context_offered`` is whether a context reached the ranker *at all* — note
    that ``engine.serve`` passes ``context=None`` when policy forbids
    personalization, so a request can carry a context that was deliberately not
    used. Deriving from the ranker's inputs rather than from the request keeps
    this honest about that.

    Ordering is the policy, and it is §44's
    ---------------------------------------

    A row can be true on two axes at once: retrieved by ``affinity`` *and* a good
    match for the post. One value has to be recorded, and context wins.

    That is not arbitrary. §44 requires that personalization must not override
    context, and the only way to audit it is to be able to ask "how many cards on
    a content surface matched nothing about the content?". For that question to
    have a true answer, :data:`PERSONALIZED` must mean *the content did not
    match* — not merely "affinity retrieved it". Ordered the other way, an
    affinity-retrieved card that also matched the post would be counted as
    personalization winning, and the metric would report a violation that did not
    happen while hiding ones that did.

    The relevance threshold is :data:`ranking.CONTEXT_CLAIM_MIN_RELEVANCE`, read
    from there rather than restated. ``choose_reason`` uses the same constant to
    decide whether it may say "related to this post" out loud, and the two must
    agree: a card whose label claims a contextual match while its recorded
    relationship says otherwise would put the conflation this module exists to
    remove back in, one layer down.
    """
    relevance = float((signals or {}).get("relevance") or 0.0)
    matched_context = (
        context_offered and relevance >= ranking.CONTEXT_CLAIM_MIN_RELEVANCE
    )

    if matched_context:
        # The same signal, named for what it is related *to*. A product page has
        # no post, so "contextual" there would describe nothing on screen — the
        # same distinction `REASON_CONTEXT` and `REASON_SIMILAR_PRODUCT` draw.
        return SIMILAR if subject_is_product else CONTEXTUAL
    if str(candidate_source or "").strip().lower() in VIEWER_SOURCES:
        return PERSONALIZED
    return CATALOGUE
