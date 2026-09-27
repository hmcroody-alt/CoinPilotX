"""Server-side discovery for the PulseSoc Marketplace storefront.

Taxonomy, facets, search, sorting and pagination for the buyer-facing catalogue.
Every answer in here is computed from rows that are actually in the database: the
category tree is derived from the categories real listings carry, and a facet
that no live listing matches is not offered. Nothing in this module invents a
category, a count, a rating or a badge.

Three things about the data this was written against are worth knowing before
changing it.

**Categories arrive dirty.** They are imported CJ taxonomy strings and they use
two different delimiters -- ``"Women's Clothing > Tops & Sets > Sweaters"`` and
``"Jewelry & Watches / Fashion Jewelry / Rings"`` are both in production right
now -- with case, apostrophe, spacing and fullwidth-punctuation variants of the
same segment ("Mens Clothing" vs "Men's Clothing", "Toys， Kids & Baby" with a
U+FF0C comma). :func:`segment_key` folds all of that away, and the *slug* is
derived from the folded key rather than from any one observed spelling. That is
deliberate: a slug is a public URL, so it must not move when inventory changes.
The human-readable label may drift with the data; the URL may not.

**Price cannot be sorted in SQL from ``price_label``.** It is TEXT, and
``services/db.py`` does not translate ``CAST``, so a cast-based ``ORDER BY``
passes on SQLite and raises on Postgres. Price sorting reads ``price_minor``,
the integer column maintained alongside ``price_label``; see
``tests/test_marketplace_price_minor.py`` for the invariant that keeps the two
in step.

**Visibility is not this module's decision.** Buyer visibility is
``marketplace_listing_lifecycle.public_sql`` plus
``discovery_visibility.discovery_visible_sql``, exactly as the search endpoint
and the product page apply them. :func:`base_where` is the single place they are
combined so a new discovery surface cannot accidentally apply only one -- which
is the bug the grid used to have.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping, NamedTuple, Optional, Sequence

from services import marketplace_listing_lifecycle as lifecycle
from services import marketplace_seller_identity as seller_identity
from services.discovery_visibility import discovery_visible_sql


PAGE_SIZE = 24

#: Deep pagination is ``LIMIT/OFFSET``, which degrades linearly with depth. A
#: crawler following "next" forever would otherwise walk the whole table one
#: expensive page at a time. Buyers do not browse past page 40 of anything; the
#: ones who would are served by search and facets instead.
MAX_PAGE = 40

MAX_SEARCH_CHARS = 120

#: Both delimiters seen in imported taxonomy strings.
_PATH_SPLIT = re.compile(r"\s*[>/]\s*")

_APOSTROPHES = "'’ʼ´`"
_NON_KEY = re.compile(r"[^a-z0-9]+")

#: Segments that are the same category under two spellings that folding alone
#: cannot merge, because they differ by a letter rather than by punctuation or
#: case. Keys and values are both :func:`segment_key` output. Add to this only
#: with a row in production to point at.
SEGMENT_ALIASES = {
    "bag shoes": "bags shoes",
}

#: Display spellings for canonical keys, where the observed data is not a good
#: label on its own. A key absent from here is labelled from the data.
SEGMENT_LABELS = {
    "bags shoes": "Bags & Shoes",
    "mens clothing": "Men's Clothing",
    "womens clothing": "Women's Clothing",
}


def segment_key(raw: Any) -> str:
    """Fold one taxonomy segment to its stable identity.

    Case, apostrophes, ampersands, punctuation and fullwidth forms all fold
    away, so ``"Men's Clothing"``, ``"Mens Clothing"`` and ``"MENS  CLOTHING"``
    are one category, and ``"Toys， Kids & Baby"`` (U+FF0C) is the same category
    as ``"Toys, Kids & Baby"``. The result is also the slug source, so it must
    stay a pure function of the text -- never of what else is in the table.
    """
    text = unicodedata.normalize("NFKC", str(raw or ""))
    for mark in _APOSTROPHES:
        text = text.replace(mark, "")
    text = _NON_KEY.sub(" ", text.casefold()).strip()
    return SEGMENT_ALIASES.get(text, text)


def slugify(raw: Any) -> str:
    """URL slug for a taxonomy segment. Stable across spelling drift."""
    return segment_key(raw).replace(" ", "-")


def category_path(raw: Any) -> tuple[str, ...]:
    """Split a raw category string into its display segments, in order.

    Returns the segments as written (minus surrounding whitespace), not folded:
    folding is for identity, and this is what a buyer reads.
    """
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    if not text:
        return ()
    return tuple(
        part for part in (seg.strip() for seg in _PATH_SPLIT.split(text)) if part
    )


def path_slugs(raw: Any) -> tuple[str, ...]:
    """The slug for each segment of a category path."""
    return tuple(slugify(seg) for seg in category_path(raw) if segment_key(seg))


class Sort(NamedTuple):
    key: str
    label: str
    order_sql: str


#: ``featured`` is a real, merchant-settable column. It is currently 0 for every
#: production listing, which makes it a no-op rather than a decoration -- when a
#: merchandiser sets it, the default order already honours it.
#:
#: Timestamps are compared as text. Every value written is a lexically sortable
#: ISO-8601 prefix, so this orders correctly even though production holds both
#: ``...:47Z`` and ``...:01`` forms; equal seconds fall through to ``id``.
_RECENT = (
    "COALESCE(NULLIF(l.published_at,''), NULLIF(l.created_at,''), '') DESC, l.id DESC"
)

#: Listings with no resolved price sort last in both directions rather than
#: clustering at the cheap end. "Request access" is not $0.00.
_PRICED_FIRST = "CASE WHEN COALESCE(l.price_minor,0) > 0 THEN 0 ELSE 1 END ASC"

SORTS: dict[str, Sort] = {
    "featured": Sort("featured", "Featured", f"l.featured DESC, {_RECENT}"),
    "newest": Sort("newest", "Newest", _RECENT),
    "price_low": Sort(
        "price_low",
        "Price: low to high",
        f"{_PRICED_FIRST}, l.price_minor ASC, l.id DESC",
    ),
    "price_high": Sort(
        "price_high",
        "Price: high to low",
        f"{_PRICED_FIRST}, l.price_minor DESC, l.id DESC",
    ),
    "title": Sort("title", "Title: A–Z", "LOWER(COALESCE(l.title,'')) ASC, l.id DESC"),
}

DEFAULT_SORT = "featured"


def resolve_sort(key: Any) -> Sort:
    """The requested sort, or the default. Never raises on user input."""
    return SORTS.get(str(key or "").strip().lower(), SORTS[DEFAULT_SORT])


def clean_search(raw: Any) -> str:
    """Normalise a user search term. Returns "" when there is nothing to search."""
    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:MAX_SEARCH_CHARS]


def _like_param(term: str) -> str:
    """A LIKE pattern for ``term``, with the wildcards the user typed defanged.

    The pattern is passed as a bind parameter, so it never reaches
    ``_escape_postgres_percent_literals`` and no SQL-text escaping applies. What
    does matter is that a buyer typing ``%`` must not get a wildcard, and
    ``ESCAPE '\\'`` is understood by both SQLite and Postgres.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped.casefold()}%"


def base_from() -> str:
    """The ``FROM`` chain every buyer-facing catalogue query shares."""
    return (
        "FROM marketplace_listings l "
        "LEFT JOIN users u ON u.user_id=l.seller_user_id "
        "LEFT JOIN marketplace_sellers ms ON ms.user_id=l.seller_user_id"
    )


def base_where() -> str:
    """Buyer visibility: the lifecycle predicate *and* the discovery predicate.

    Both, always. A surface that applies only the lifecycle half shows listings
    from deactivated and QA seller accounts, which is precisely how the grid and
    the search endpoint once disagreed about the same catalogue.
    """
    return f"{lifecycle.public_sql('l', 'ms')} AND {discovery_visible_sql('u')}"


class Facet(NamedTuple):
    slug: str
    label: str
    count: int
    children: tuple["Facet", ...] = ()


class Page(NamedTuple):
    rows: list[dict]
    total: int
    page: int
    page_size: int
    pages: int
    sort: Sort
    search: str
    category_slug: str
    category_label: str


def _label_for(key: str, spellings: Mapping[str, int]) -> str:
    """Pick the display label for a folded category key.

    An explicit entry wins. Otherwise the most-seen spelling wins, preferring a
    spelling that kept its apostrophe and breaking ties lexically so the choice
    is deterministic for a given set of rows. Only the label is chosen this way
    -- the slug comes from the key, so nothing here can move a URL.
    """
    if key in SEGMENT_LABELS:
        return SEGMENT_LABELS[key]
    if not spellings:
        return key.title()
    return max(
        spellings.items(),
        key=lambda item: (
            item[1],
            any(mark in item[0] for mark in _APOSTROPHES),
            item[0],
        ),
    )[0]


def taxonomy(cur, *, depth: int = 2) -> list[Facet]:
    """The category tree, derived from the categories live listings carry.

    One grouped query. A category exists here because a buyer-visible listing is
    in it, and its count is that listing count -- so an empty facet is not
    rendered because it is never produced.
    """
    cur.execute(
        f"SELECT l.category AS category, COUNT(*) AS n "
        f"{base_from()} WHERE {base_where()} GROUP BY l.category"
    )
    rows = [dict(row) for row in cur.fetchall()]

    tree: dict[str, dict] = {}
    for row in rows:
        count = int(row.get("n") or 0)
        segments = [seg for seg in category_path(row.get("category")) if segment_key(seg)]
        if not segments:
            continue
        level = tree
        for position, segment in enumerate(segments[:depth]):
            key = segment_key(segment)
            node = level.setdefault(
                key, {"key": key, "count": 0, "spellings": {}, "children": {}}
            )
            node["count"] += count
            node["spellings"][segment] = node["spellings"].get(segment, 0) + count
            level = node["children"]
            if position + 1 >= depth:
                break

    def build(level: Mapping[str, dict]) -> tuple[Facet, ...]:
        facets = [
            Facet(
                slug=node["key"].replace(" ", "-"),
                label=_label_for(node["key"], node["spellings"]),
                count=node["count"],
                children=build(node["children"]),
            )
            for node in level.values()
        ]
        facets.sort(key=lambda facet: (-facet.count, facet.label.casefold()))
        return tuple(facets)

    return list(build(tree))


def find_facet(facets: Sequence[Facet], slug: str) -> Optional[Facet]:
    """Depth-first lookup of a facet by slug, at any level."""
    for facet in facets:
        if facet.slug == slug:
            return facet
        found = find_facet(facet.children, slug)
        if found is not None:
            return found
    return None


def _category_clause(cur, slug: str) -> tuple[str, list, str]:
    """A WHERE fragment restricting to one category slug, at any depth.

    Resolved by matching the *folded* segments of each distinct category string
    rather than by a SQL prefix match, because the stored strings use two
    delimiters and inconsistent spellings -- a prefix match would silently drop
    half of a category. The distinct-category list is small (tens of rows
    against production's catalogue); see the module notes for the indexed column
    this becomes if that stops being true.
    """
    if not slug:
        return "", [], ""
    cur.execute(
        f"SELECT DISTINCT l.category AS category {base_from()} WHERE {base_where()}"
    )
    matched: list[str] = []
    label = ""
    for row in cur.fetchall():
        raw = dict(row).get("category")
        segments = [seg for seg in category_path(raw) if segment_key(seg)]
        slugs = [slugify(seg) for seg in segments]
        if slug in slugs:
            matched.append(str(raw or ""))
            if not label:
                label = segments[slugs.index(slug)]
    if not matched:
        return "1=0", [], ""
    placeholders = ",".join("?" for _ in matched)
    key = segment_key(label)
    return f"l.category IN ({placeholders})", matched, SEGMENT_LABELS.get(key, label)


def query(
    cur,
    *,
    search: Any = "",
    category_slug: Any = "",
    sort: Any = DEFAULT_SORT,
    page: Any = 1,
    page_size: int = PAGE_SIZE,
) -> Page:
    """One page of the buyer-visible catalogue.

    Filtering, sorting and paging all happen in SQL, so page 2 is the real
    second page of the real result set rather than a slice of a capped prefix.
    """
    term = clean_search(search)
    slug = slugify(category_slug) if category_slug else ""
    chosen = resolve_sort(sort)
    size = max(1, min(int(page_size or PAGE_SIZE), 96))

    clauses = [base_where()]
    params: list[Any] = []

    category_sql, category_params, category_label = _category_clause(cur, slug)
    if category_sql:
        clauses.append(category_sql)
        params.extend(category_params)

    if term:
        pattern = _like_param(term)
        clauses.append(
            "(LOWER(COALESCE(l.title,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(l.category,'')) LIKE ? ESCAPE '\\' "
            "OR LOWER(COALESCE(l.short_description,'')) LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])

    where = " AND ".join(clauses)

    cur.execute(f"SELECT COUNT(*) AS n {base_from()} WHERE {where}", tuple(params))
    total = int(dict(cur.fetchone() or {}).get("n") or 0)

    pages = max(1, (total + size - 1) // size)
    try:
        current = int(page or 1)
    except (TypeError, ValueError):
        current = 1
    current = max(1, min(current, min(pages, MAX_PAGE)))
    offset = (current - 1) * size

    cur.execute(
        f"SELECT l.*, {seller_identity.store_name_select('ms')} "
        f"{base_from()} WHERE {where} "
        f"ORDER BY {chosen.order_sql} LIMIT ? OFFSET ?",
        tuple(params) + (size, offset),
    )
    rows = [dict(row) for row in cur.fetchall()]

    return Page(
        rows=rows,
        total=total,
        page=current,
        page_size=size,
        pages=pages,
        sort=chosen,
        search=term,
        category_slug=slug,
        category_label=category_label,
    )


def related(cur, listing: Mapping[str, Any], *, limit: int = 8) -> list[dict]:
    """Other buyer-visible listings in the same category, nearest segment first.

    Real inventory only. Returns fewer than ``limit`` -- or nothing at all --
    rather than padding with unrelated products, because a "related" rail that
    is really "anything else we have" is a lie told with real rows.
    """
    listing_id = int(listing.get("id") or 0)
    segments = [seg for seg in category_path(listing.get("category")) if segment_key(seg)]
    if not segments:
        return []

    collected: list[dict] = []
    seen = {listing_id}
    for segment in reversed(segments):
        if len(collected) >= limit:
            break
        category_sql, category_params, _ = _category_clause(cur, slugify(segment))
        if not category_sql or category_sql == "1=0":
            continue
        cur.execute(
            f"SELECT l.*, {seller_identity.store_name_select('ms')} "
            f"{base_from()} WHERE {base_where()} AND {category_sql} AND l.id <> ? "
            f"ORDER BY l.featured DESC, {_RECENT} LIMIT ?",
            tuple(category_params) + (listing_id, limit * 2),
        )
        for row in cur.fetchall():
            item = dict(row)
            key = int(item.get("id") or 0)
            if key in seen:
                continue
            seen.add(key)
            collected.append(item)
            if len(collected) >= limit:
                break
    return collected[:limit]


def page_window(current: Any, pages: Any, span: int = 2) -> list[int]:
    """Page numbers to render around ``current``, clamped to what exists.

    ``current`` is clamped to the ceiling *before* the window is built, not
    after. Without that, a request for page 90 of a 40-page ceiling produced
    ``range(88, 41)`` -- empty -- and the pager rendered no page links at all,
    so the one visitor who most needed a way back had none. :func:`query`
    clamps too, but this is a public helper and a template may hand it a raw
    query-string value.
    """
    try:
        ceiling = max(1, min(int(pages or 1), MAX_PAGE))
    except (TypeError, ValueError):
        ceiling = 1
    try:
        here = max(1, min(int(current or 1), ceiling))
    except (TypeError, ValueError):
        here = 1
    start = max(1, here - span)
    end = min(ceiling, here + span)
    return list(range(start, end + 1))


def facet_total(facets: Iterable[Facet]) -> int:
    return sum(facet.count for facet in facets)
