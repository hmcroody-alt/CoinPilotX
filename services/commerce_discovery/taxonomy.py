"""Canonical category identity for diversity counting.

Two questions the engine asks about a listing's category, and they are not the
same question:

* **Is this the same shelf as that one?** — ``category_key``. The whole path,
  folded. ``Jewelry & Watches > Fashion Jewelry > Rings`` and
  ``jewelry watches / fashion jewelry / rings`` are one shelf.
* **Is this the same aisle as that one?** — ``segment_root``. The first segment
  only. Three different leaf shelves under ``Women's Clothing`` are three
  different answers to the first question and one answer to this one.

Why both exist
--------------

Diversity caps counted on the leaf path alone cannot see coarse concentration,
and coarse concentration is what a user perceives as repetition. Measured on the
production catalogue (15 eligible listings, 2026-09-27): 11 distinct leaf paths
but 7 distinct roots, with ``womens clothing`` holding 6 of the 15. Marketplace's
leaf cap is 4 and the largest leaf bucket is 3, so the cap could not fire at all
while 40% of servable inventory sat in one aisle. A root counter is the smallest
thing that notices.

The reverse is also true and is why the leaf key survives: on ``product_detail``
relatedness *is* leaf similarity, so a root cap there would be measuring the
feature rather than an excess. ``router`` sets that cap to the whole row.

Why this module is not its own normalizer
-----------------------------------------

The folding rules — NFKC, apostrophe classes, the ``>``/``/`` delimiter
ambiguity in imported taxonomy strings, and the ``bag shoes``/``bags shoes``
alias — are already written, tested and in production use in
``services.marketplace_catalog``, which is what renders the shop's own category
navigation. A second implementation here would be a second set of buckets: the
shop would say two listings are one category and the engine would say they are
two, and the divergence would show up as a diversity cap that does not match the
page the user is looking at. So this delegates, and exists only to name the two
keys and to hold the single import.

The delimiter fold is not currently load-bearing on servable inventory — all
three eligible rings use one spelling — but 26 listings sit in review, and
approving an arrow-spelled ring would otherwise split the ring shelf in two and
double its allowance under every cap. Folding now is cheaper than noticing then.
"""

from __future__ import annotations

from typing import Any

from services.marketplace_catalog import category_path, segment_key

#: Separator for the canonical joined path. Single-spaced ``>`` regardless of
#: how the source spelled it, so the key is a pure function of identity.
_JOIN = " > "


def segments(raw: Any) -> tuple[str, ...]:
    """Every segment of one category, folded, empties dropped.

    ``"Women's Clothing / Outerwear & Jackets/ Basic Jacket"`` and
    ``"womens clothing > outerwear jackets > basic jacket"`` both give
    ``("womens clothing", "outerwear jackets", "basic jacket")``.
    """
    return tuple(key for key in (segment_key(part) for part in category_path(raw)) if key)


def category_key(raw: Any) -> str:
    """Canonical identity of the whole path. ``""`` for an uncategorised row.

    Returning empty rather than a sentinel is deliberate: ``router.admissible``
    and the selection counters both treat a falsy category as "no opinion", so an
    uncategorised listing is never counted against a cap and never blocks one.
    Two uncategorised listings are not thereby "the same category" — they are two
    rows we know nothing about, and inventing a shared bucket for them would cap
    the very rows most in need of exposure.
    """
    return _JOIN.join(segments(raw))


def segment_root(raw: Any) -> str:
    """The top-level aisle. ``""`` for an uncategorised row, as above.

    A single-segment category is its own root, so a flat taxonomy makes the root
    cap and the leaf cap agree rather than making one of them meaningless.
    """
    parts = segments(raw)
    return parts[0] if parts else ""
