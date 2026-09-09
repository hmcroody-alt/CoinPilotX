"""Browse a supplier's catalogue in normalized, provider-neutral terms.

This is the read half of the merchant's "find products" experience. It exists so
that the screens above it never see a provider payload: the gateway returns CJ
JSON, and everything that leaves this module is the same shape whether the
merchant connected CJ, Printful or Printify.

What this module is deliberately not
------------------------------------
It is not an import path. Nothing here is trusted later. A merchant browsing a
catalogue produces no durable economics — the numbers rendered on a search card
are a *preview*, and the import re-fetches every one of them from the provider
before writing anything (see ``importer._authoritative``). That separation is
why it is safe to serve this from cache, and why a stale or even wrong price
here cannot become a wrong price in the store.

Search results are also intentionally thin. CJ's search endpoint returns a
summary without variants, and this module does not paper over that by fetching
each product to fill the gap: fifty extra provider calls to populate a grid the
merchant will scroll past is how a browse surface becomes a rate-limit incident.
Variants arrive when the merchant opens one product.
"""

from __future__ import annotations

from services.business_os.suppliers import gateway, normalize, policy
from services.business_os.suppliers.errors import SupplierError

#: Search page size ceiling. A merchant scrolling is served more pages, not one
#: enormous page — an unbounded ``size`` is a provider-side amplification lever.
MAX_PAGE_SIZE = 50
MAX_PAGE = 200


def _entries(payload) -> list:
    """The product list inside whatever envelope the provider used."""
    data = payload
    for _ in range(4):
        if isinstance(data, dict):
            for key in ("list", "content", "data", "result", "products", "items"):
                inner = data.get(key)
                if isinstance(inner, (list, tuple)):
                    return list(inner)
                if isinstance(inner, dict):
                    data = inner
                    break
            else:
                return []
        elif isinstance(data, (list, tuple)):
            return list(data)
        else:
            return []
    return []


def _total(payload) -> int | None:
    """Provider result count, or None. Never a guess derived from page length.

    A count invented from ``len(page)`` reads as authoritative in a UI ("1–20 of
    20") while being wrong for every page but the last.
    """
    data = payload if isinstance(payload, dict) else {}
    for envelope in ("data", "result", "content"):
        inner = data.get(envelope)
        if isinstance(inner, dict):
            data = inner
            break
    for key in ("total", "totalCount", "totalRecords", "count"):
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _card(provider, entry) -> dict | None:
    """One search result, or None if it cannot be understood.

    A product whose identity will not normalize is dropped rather than rendered
    with a blank id. A card the merchant can tap but not import is worse than a
    card that was never shown.
    """
    try:
        normalized = normalize.product(provider, entry)
    except normalize.NormalizationError:
        return None
    if not normalized.get("external_product_id") or not normalized.get("title"):
        return None
    low, high = normalize.cost_range(normalized.get("variants"))
    if low is None:
        # Search summaries carry a headline price but no variant rows; fall back
        # to it rather than reporting unknown cost for the entire catalogue.
        low = high = normalized.get("from_cost_cents")
    return {
        "provider": normalized["provider"],
        "external_product_id": normalized["external_product_id"],
        "title": normalized["title"],
        "category": normalized.get("category"),
        "cover_image_url": normalized.get("cover_image_url"),
        "cost_low_cents": low,
        "cost_high_cents": high,
        "currency": normalized.get("currency"),
        "origin": normalized.get("origin"),
        "variant_count": len(normalized.get("variants") or ()) or None,
    }


#: Provider-neutral search filter names, and the CJ parameter each becomes.
#:
#: Only the response half of this module was ever normalized. Requests were
#: passed through untouched, so a caller that sent the documented neutral name
#: -- `keyword` -- reached CJ's own parameter allowlist, which knows only
#: `keyWord`, and was refused with a 400 naming a spelling no caller had been
#: given. An empty filter set was the one that worked, which is why the browse
#: screen could load and then fail on the merchant's first word.
#:
#: Both halves were internally consistent, which is how this survived: every
#: backend test called the adapter with CJ's spelling, and the mobile test
#: asserted the neutral one against a mocked backend. Neither exercised the
#: boundary between them.
#:
#: CJ's own spellings stay accepted so that callers already sending them keep
#: working. This map decides only what a merchant may ask for in words that
#: name no provider; `cj.search_products` still holds the allowlist that decides
#: what may actually reach CJ, and it is unchanged.
_NEUTRAL_FILTERS = {"keyword": "keyWord", "category_id": "categoryId", "country_code": "countryCode"}


def _provider_filters(filters) -> dict:
    translated: dict = {}
    for name, value in (filters or {}).items():
        key = _NEUTRAL_FILTERS.get(name, name)
        # One filter asked for twice under two spellings has no right answer,
        # and picking a winner would search for something nobody requested.
        if key in translated:
            raise SupplierError("invalid_input", http_status=400)
        translated[key] = value
    return translated


def search(business_id, store_id, actor_user_id, connection_id, *,
           filters=None, page=1, size=20, provider="cj", context=None):
    """A page of normalized catalogue results for one connection."""
    policy.require_enabled()
    if not normalize.supported(provider):
        raise SupplierError("unsupported_provider", http_status=400)
    if not isinstance(filters, dict) and filters is not None:
        raise SupplierError("invalid_input", http_status=400)
    filters = _provider_filters(filters)
    try:
        page, size = int(page), int(size)
    except (TypeError, ValueError):
        raise SupplierError("invalid_pagination", http_status=400) from None
    if not 1 <= page <= MAX_PAGE or not 1 <= size <= MAX_PAGE_SIZE:
        raise SupplierError("invalid_pagination", http_status=400)

    result = gateway.read("search", business_id=business_id, store_id=store_id,
                          actor_user_id=actor_user_id, connection_id=connection_id,
                          params={"filters": filters or {}, "page": page, "size": size},
                          context=context)
    payload = result.get("data")
    cards = [card for card in (_card(provider, e) for e in _entries(payload)) if card]
    return {
        "products": cards,
        "page": page,
        "size": size,
        "total": _total(payload),
        # A short page is the only honest end-of-results signal available when
        # the provider gave no total.
        "has_more": len(cards) >= size,
        "cached": bool(result.get("cached")),
    }


def detail(business_id, store_id, actor_user_id, connection_id, external_product_id, *,
           provider="cj", context=None):
    """One supplier product with its variants and a live inventory overlay.

    The inventory read is best-effort on purpose. When it fails the catalogue's
    own stock signal stands and every variant it would have covered stays at
    whatever the catalogue said — including ``UNKNOWN``. It does not fall back to
    out-of-stock, which would present a provider outage as a sold-out product.
    """
    policy.require_enabled()
    if not normalize.supported(provider):
        raise SupplierError("unsupported_provider", http_status=400)
    pid = normalize.external_id(external_product_id)
    if not pid:
        raise SupplierError("invalid_input", http_status=400)

    result = gateway.read("product", business_id=business_id, store_id=store_id,
                          actor_user_id=actor_user_id, connection_id=connection_id,
                          params={"pid": pid}, context=context)
    normalized = normalize.product(provider, result.get("data"))
    if not normalized.get("external_product_id"):
        raise SupplierError("product_unavailable", http_status=404)

    variants = normalized.get("variants") or []
    if not variants:
        try:
            fetched = gateway.read("variants", business_id=business_id, store_id=store_id,
                                   actor_user_id=actor_user_id, connection_id=connection_id,
                                   params={"pid": pid}, context=context)
            variants = normalize.variants(provider, fetched.get("data"))
        except Exception:
            variants = []

    inventory_ok = True
    try:
        readings = gateway.read("inventory", business_id=business_id, store_id=store_id,
                                actor_user_id=actor_user_id, connection_id=connection_id,
                                params={"pid": pid}, context=context)
        variants = normalize.apply_inventory(variants, normalize.inventory(provider, readings.get("data")))
    except Exception:
        inventory_ok = False

    low, high = normalize.cost_range(variants)
    return {
        "product": {**normalized, "variants": variants},
        "cost_low_cents": low if low is not None else normalized.get("from_cost_cents"),
        "cost_high_cents": high if high is not None else normalized.get("from_cost_cents"),
        "inventory_fresh": inventory_ok,
        "snapshot_id": result.get("snapshot_id"),
        "cached": bool(result.get("cached")),
    }
