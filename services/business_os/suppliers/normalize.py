"""Provider JSON in, PulseSoc facts out. Nothing downstream reads CJ shapes.

Why this module exists
----------------------
``gateway.read`` returns whatever the provider said, tagged ``untrusted_content``.
That payload is a CJ-shaped dict: ``pid``, ``vid``, ``sellPrice`` as a string,
image lists under three different key spellings depending on the endpoint. If
that shape reaches the importer, then the importer knows CJ; if it reaches the
draft, then the draft knows CJ; and the second provider becomes a rewrite of
everything it touched rather than a new file next to this one.

So this is the only file in the dropshipping pipeline permitted to know a
provider's vocabulary. Everything above it consumes the normalized shapes:
:func:`product`, :func:`variants`, :func:`inventory`.

The three refusals
------------------
Normalization is where a lossy conversion is easiest to write and hardest to
see, so three conversions are deliberately *not* performed:

1. **An unparseable price is ``None``, never ``0``.** ``float(x or 0)`` turns a
   supplier outage into a free product with a 100% margin. Every money field
   here returns ``int`` cents or ``None``, and ``None`` propagates all the way
   to a margin the merchant sees as "unknown" rather than a number.
2. **An absent stock signal is ``UNKNOWN``, never ``OUT_OF_STOCK``.** Those are
   opposite claims. "We could not read inventory" must not render as a sold-out
   badge, because a sold-out badge is a state nobody escalates.
3. **A rejected media URL is dropped, never substituted.** A product that loses
   every image fails import validation. It does not get a placeholder that makes
   a broken import look like a successful one.

On untrusted content
--------------------
Supplier text and URLs are attacker-influenced in the ordinary case: a CJ
listing's description is written by a third-party seller. :func:`clean_text`
strips markup rather than escaping it, and :func:`safe_media_url` refuses
anything that is not an ``https`` URL pointing at a public host. Both are
applied here, at the boundary, so that no consumer has to remember to do it.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

#: Normalized stock vocabulary. Deliberately wider than the three states the
#: variant table stores, because the extra states are real merchant-facing
#: distinctions that collapse to ``UNKNOWN`` only at the storage boundary.
STOCK_IN_STOCK = "IN_STOCK"
STOCK_LOW_STOCK = "LOW_STOCK"
STOCK_OUT_OF_STOCK = "OUT_OF_STOCK"
STOCK_UNKNOWN = "UNKNOWN"
STOCK_DISCONTINUED = "DISCONTINUED"
STOCK_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
STOCK_VARIANT_UNAVAILABLE = "VARIANT_UNAVAILABLE"

STOCK_STATES = (
    STOCK_IN_STOCK, STOCK_LOW_STOCK, STOCK_OUT_OF_STOCK, STOCK_UNKNOWN,
    STOCK_DISCONTINUED, STOCK_PROVIDER_UNAVAILABLE, STOCK_VARIANT_UNAVAILABLE,
)

#: States that mean "we did not get an answer", as opposed to "the answer is no".
#: :func:`services.marketplace_variants.upsert_variant` only accepts three
#: states, so this set is what decides which of ours degrade to ``UNKNOWN``
#: rather than to ``OUT_OF_STOCK`` on the way into storage.
INDETERMINATE_STOCK = frozenset({
    STOCK_UNKNOWN, STOCK_PROVIDER_UNAVAILABLE, STOCK_VARIANT_UNAVAILABLE,
})

#: Below this count a provider-reported quantity reads as LOW_STOCK. This is a
#: display threshold only; it never changes whether the variant is sellable.
LOW_STOCK_THRESHOLD = 5

MAX_TITLE = 160
MAX_DESCRIPTION = 8000
MAX_MEDIA = 20
MAX_VARIANTS = 100
MAX_URL = 2048

_TAG = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
#: Money as the providers actually send it: "8.20", "8.2 - 12.50", "USD 8.20",
#: "$8.20". The first decimal number wins; a range's low end is the honest
#: "from" price for a card. Anything with no number at all yields None.
#:
#: The leading sign is captured even though every caller rejects negatives. It
#: has to be: without it "-5.00" matches "5.00" and a negative provider price
#: arrives as a positive cost, which is worse than either rejecting it or
#: passing it through — a sign error in the supplier feed becomes a real price.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class NormalizationError(ValueError):
    """A provider payload that cannot be understood at all.

    Raised only for structural failures — the payload is not a mapping, or the
    provider identity is unknown. A *missing field* is never an error here; it
    becomes ``None`` or ``UNKNOWN`` and the importer's validation decides
    whether that is fatal. Conflating the two would turn one absent weight into
    a failed import of an otherwise fine product.
    """


# ---------------------------------------------------------------------------
# Scalar coercion
# ---------------------------------------------------------------------------

def clean_text(value, limit: int) -> str | None:
    """Markup-stripped, length-capped plain text, or None.

    Strips tags rather than escaping them. Escaping preserves the payload for
    something downstream to unescape and render; stripping destroys it. A
    supplier description has no legitimate need for markup in a React Native
    ``<Text>``, so the destructive option is the correct one.

    Control characters are removed via the unicode category rather than a
    blocklist, which is what catches the bidi overrides and zero-width joiners
    that a blocklist of ``\\x00-\\x1f`` misses.
    """
    if value is None:
        return None
    text = str(value)
    text = _TAG.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Cc = control, Cf = format (bidi marks, ZWJ), Cs = surrogate.
    text = "".join(ch for ch in text if unicodedata.category(ch) not in {"Cc", "Cf", "Cs"} or ch == "\n")
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    if not text:
        return None
    return text[:limit]


def cents(value) -> int | None:
    """Minor units from a provider money field, or None when unknowable.

    Returns None — not 0 — for absent, blank, non-numeric and negative input.
    The distinction is the whole contract: ``marketplace_variants.margin_cents``
    returns None when cost is None, and shows the merchant "unknown" instead of
    a fabricated margin. A ``0`` here defeats that at the point where no test
    downstream can tell the difference.

    A range ("8.20-12.50") yields its low end, which is the "from" price a
    discovery card is supposed to show. Rounding is half-up on the cent because
    banker's rounding on a price list surprises merchants reconciling totals.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        match = _NUMBER.search(str(value))
        if match is None:
            return None
        try:
            amount = float(match.group(0))
        except ValueError:
            return None
    if amount != amount or amount in (float("inf"), float("-inf")):  # NaN/inf
        return None
    if amount < 0:
        return None
    if amount > 1_000_000_000:
        return None
    return int(amount * 100 + 0.5)


def currency(value) -> str | None:
    """A 3-letter uppercase ISO code, or None. Never a default."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if len(code) == 3 and code.isalpha() else None


def external_id(value, limit: int = 190) -> str | None:
    """A provider identifier, or None when it is not usable as one.

    Rejects anything with whitespace or control characters. A provider id ends
    up in a UNIQUE index and in a URL path; an id containing a space is either
    a display label that was mistaken for an id, or an injection attempt, and
    neither should become a mapping key.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text or len(text) > limit:
        return None
    if any(ch.isspace() or unicodedata.category(ch) in {"Cc", "Cf"} for ch in text):
        return None
    return text


def grams(value) -> int | None:
    """Weight in whole grams, or None. Providers send "12.5" and 12.5 alike."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        amount = float(value)
    else:
        match = _NUMBER.search(str(value))
        if match is None:
            return None
        amount = float(match.group(0))
    if amount != amount or amount < 0 or amount > 10_000_000:
        return None
    return int(amount + 0.5)


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

def safe_media_url(value) -> str | None:
    """An ``https`` URL on a public host, or None.

    This is an SSRF boundary, not a formatting nicety. Imported media URLs are
    fetched or proxied by our own infrastructure later, so a supplier that
    returns ``http://169.254.169.254/latest/meta-data/`` is asking our server to
    read its own cloud credentials and hand them back.

    Refusals, and why each one:

    * **Non-https** — plaintext media on a TLS storefront is a mixed-content
      break, and ``file://``/``gopher://`` are the classic SSRF pivots.
    * **Credentials in the authority** (``https://user:pass@host/``) — the part
      before ``@`` is ignored by servers and read by humans, which is what makes
      it a phishing primitive.
    * **IP literals in any private range** — loopback, link-local, RFC1918,
      unique-local, and the IPv4-mapped IPv6 forms of all of those. Checking the
      textual prefix instead would miss ``[::ffff:127.0.0.1]`` and ``2130706433``.
    * **A bare hostname with no dot** — ``https://metadata/`` resolves inside
      many container networks and nowhere on the public internet.

    A hostname that resolves to a private address at fetch time is *not* caught
    here and cannot be: this function does no DNS, deliberately, because a
    check-then-fetch would be a TOCTOU window. The fetching layer is where a
    resolved-address check belongs.
    """
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_URL:
        return None
    if any(unicodedata.category(ch) in {"Cc", "Cf"} or ch.isspace() for ch in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https":
        return None
    if parts.username or parts.password or "@" in parts.netloc:
        return None
    host = (parts.hostname or "").strip().lower()
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        mapped = getattr(address, "ipv4_mapped", None) or address
        if (mapped.is_private or mapped.is_loopback or mapped.is_link_local
                or mapped.is_reserved or mapped.is_multicast or mapped.is_unspecified):
            return None
    elif "." not in host or host.endswith(".local") or host.endswith(".internal"):
        return None
    return url


def media_list(values) -> list[str]:
    """Deduplicated, order-preserving list of accepted media URLs.

    Order matters and is preserved because the first surviving entry becomes the
    cover image. Dropping a rejected URL silently shifts the cover, which is
    correct — the alternative is a cover that renders as a black card.
    """
    seen, out = set(), []
    for candidate in values or ():
        url = safe_media_url(candidate)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
            if len(out) >= MAX_MEDIA:
                break
    return out


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------

def stock_state(*, quantity=None, declared=None, provider_failed: bool = False) -> tuple[str, int | None]:
    """Normalized (state, quantity) from whatever the provider offered.

    ``provider_failed`` is a separate argument rather than an absent quantity
    because the two are different facts with the same shape. A provider that
    answered "0" is out of stock; a provider that did not answer is unknown, and
    passing ``quantity=None`` for both would erase the distinction at the only
    point where the caller still knows it.

    ``declared`` carries a provider's own textual state where one exists. It is
    honoured only when it maps to a state we recognise — an unrecognised string
    yields ``UNKNOWN`` rather than being passed through, so that a provider
    inventing a new word cannot make a variant read as purchasable to code that
    predates the word.
    """
    if provider_failed:
        return STOCK_PROVIDER_UNAVAILABLE, None
    if declared is not None:
        token = str(declared).strip().upper().replace("-", "_").replace(" ", "_")
        if token in STOCK_STATES:
            if token in INDETERMINATE_STOCK:
                return token, None
            return token, _quantity_or_none(quantity)
    count = _quantity_or_none(quantity)
    if count is None:
        return STOCK_UNKNOWN, None
    if count <= 0:
        return STOCK_OUT_OF_STOCK, 0
    if count < LOW_STOCK_THRESHOLD:
        return STOCK_LOW_STOCK, count
    return STOCK_IN_STOCK, count


def _quantity_or_none(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        count = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return count if 0 <= count <= 100_000_000 else None


def storage_stock_state(state: str) -> str:
    """Collapse the merchant-facing vocabulary onto the three stored states.

    ``marketplace_listing_variants.stock_state`` accepts IN_STOCK, OUT_OF_STOCK
    and UNKNOWN. Everything indeterminate — and ``DISCONTINUED``, which is a
    provider-catalogue fact rather than a count — has to land on ``UNKNOWN``
    rather than ``OUT_OF_STOCK``, because the storage layer's readers treat
    OUT_OF_STOCK as a confirmed negative and gate purchasability on it.

    ``LOW_STOCK`` is the one that collapses upward: it is a display refinement
    of IN_STOCK and is still confirmed-available.
    """
    if state == STOCK_LOW_STOCK:
        return STOCK_IN_STOCK
    if state in (STOCK_IN_STOCK, STOCK_OUT_OF_STOCK):
        return state
    return STOCK_UNKNOWN


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def _mapping(payload, what: str) -> dict:
    if isinstance(payload, dict):
        # Providers wrap the useful object in an envelope inconsistently.
        for key in ("data", "result", "content"):
            inner = payload.get(key)
            if isinstance(inner, dict) and not _looks_like_product(payload):
                return inner
        return payload
    raise NormalizationError(f"{what} payload is not an object")


def _looks_like_product(payload: dict) -> bool:
    return any(k in payload for k in ("pid", "productName", "productNameEn", "productSku"))


def _first(payload: dict, *names, default=None):
    """First present, non-blank value among several provider key spellings.

    CJ alone spells the same product name ``productNameEn``, ``productName`` and
    ``nameEn`` across three endpoints. A lookup chain here is cheaper and far
    more legible than a per-endpoint adapter class.
    """
    for name in names:
        if name in payload:
            value = payload[name]
            if value is not None and value != "" and value != []:
                return value
    return default


def _cj_product(payload: dict) -> dict:
    data = _mapping(payload, "product")
    gallery = _first(data, "productImageSet", "productImages", "images", default=[])
    if isinstance(gallery, str):
        gallery = [gallery]
    cover = _first(data, "productImage", "productMainImage", "image")
    media = media_list([cover, *(gallery if isinstance(gallery, (list, tuple)) else [])])
    return {
        "provider": "cj",
        "external_product_id": external_id(_first(data, "pid", "productId", "id")),
        "title": clean_text(_first(data, "productNameEn", "productName", "nameEn", "name"), MAX_TITLE),
        "description": clean_text(_first(data, "description", "productDescription", "descriptionEn"), MAX_DESCRIPTION),
        "category": clean_text(_first(data, "categoryName", "categoryNameEn", "category"), 120),
        "brand": clean_text(_first(data, "brandName", "brand"), 120),
        "external_sku": external_id(_first(data, "productSku", "sku"), 120),
        "media": media,
        "cover_image_url": media[0] if media else None,
        "from_cost_cents": cents(_first(data, "sellPrice", "productPrice", "price")),
        "currency": currency(_first(data, "currency", "sellPriceCurrency")) or "USD",
        "origin": clean_text(_first(data, "productProCountry", "sourceFrom", "countryCode"), 60),
        "weight_grams": grams(_first(data, "productWeight", "weight")),
        "variants": _cj_variants_from(data),
    }


def _cj_variants_from(data: dict) -> list[dict]:
    raw = _first(data, "variants", "variantList", "productVariants", default=[])
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        variant = _cj_variant(entry, index)
        if variant["external_variant_id"] is not None:
            out.append(variant)
        if len(out) >= MAX_VARIANTS:
            break
    return out


def _cj_variant(entry: dict, position: int = 0) -> dict:
    options = _cj_options(entry)
    state, quantity = stock_state(
        quantity=_first(entry, "variantQuantity", "quantity", "inventory", "stock"),
        declared=_first(entry, "variantStandard", "stockStatus"),
    )
    image = safe_media_url(_first(entry, "variantImage", "image"))
    return {
        "external_variant_id": external_id(_first(entry, "vid", "variantId", "id")),
        "external_sku": external_id(_first(entry, "variantSku", "sku"), 120),
        "options": options,
        "cost_cents": cents(_first(entry, "variantSellPrice", "sellPrice", "price")),
        "currency": currency(_first(entry, "currency")) or "USD",
        "stock_state": state,
        "stock_quantity": quantity,
        "weight_grams": grams(_first(entry, "variantWeight", "weight")),
        "length_mm": grams(_first(entry, "variantLength", "length")),
        "width_mm": grams(_first(entry, "variantWidth", "width")),
        "height_mm": grams(_first(entry, "variantHeight", "height")),
        "warehouse": clean_text(_first(entry, "variantWarehouse", "warehouse", "countryCode"), 60),
        "image_url": image,
        "position": position,
    }


def _cj_options(entry: dict) -> list[dict]:
    """Option pairs, from either a structured list or CJ's ``"Black-XL"`` key.

    The fallback split is why ``variantKey`` is read at all: CJ frequently omits
    a structured option list and encodes the combination in a hyphenated string.
    Without the fallback those variants normalize to zero options, and
    ``marketplace_variants.variant_key`` then hashes every one of them to the
    same key — so a 12-variant product imports as one variant, silently.
    """
    raw = _first(entry, "variantOptions", "options", default=None)
    out = []
    if isinstance(raw, (list, tuple)):
        for option in raw:
            if not isinstance(option, dict):
                continue
            name = clean_text(_first(option, "name", "key", "optionName"), 60)
            value = clean_text(_first(option, "value", "val", "optionValue"), 120)
            if name and value:
                out.append({"name": name, "value": value})
    if out:
        return out
    key = _first(entry, "variantKey", "variantNameEn", "variantName")
    if isinstance(key, str) and key.strip():
        parts = [clean_text(part, 120) for part in key.split("-")]
        return [{"name": f"option{index + 1}", "value": part}
                for index, part in enumerate(parts) if part]
    return out


def _cj_inventory(payload) -> dict:
    """``{external_variant_id: (state, quantity)}`` from an inventory read."""
    data = payload
    if isinstance(payload, dict):
        for key in ("data", "result", "content"):
            if isinstance(payload.get(key), (list, dict)):
                data = payload[key]
                break
    rows = data if isinstance(data, (list, tuple)) else [data]
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = external_id(_first(row, "vid", "variantId", "id"))
        if vid is None:
            continue
        total = _first(row, "totalInventoryNum", "quantity", "inventoryNum", "storageNum")
        if total is None and isinstance(row.get("inventoryList"), (list, tuple)):
            counts = [_quantity_or_none(_first(item, "storageNum", "quantity"))
                      for item in row["inventoryList"] if isinstance(item, dict)]
            counts = [c for c in counts if c is not None]
            total = sum(counts) if counts else None
        out[vid] = stock_state(quantity=total, declared=_first(row, "stockStatus"))
    return out


_PRODUCT_ADAPTERS = {"cj": _cj_product}
_INVENTORY_ADAPTERS = {"cj": _cj_inventory}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def supported(provider) -> bool:
    return str(provider or "").strip().lower() in _PRODUCT_ADAPTERS


def product(provider, payload) -> dict:
    """Normalized product with its variants, from a provider product payload."""
    key = str(provider or "").strip().lower()
    adapter = _PRODUCT_ADAPTERS.get(key)
    if adapter is None:
        raise NormalizationError("unsupported provider")
    return adapter(payload)


def variants(provider, payload) -> list[dict]:
    """Normalized variants from a dedicated variants payload.

    Accepts both a bare list and the product-shaped envelope, because
    ``gateway.read("variants")`` and ``gateway.read("product")`` disagree about
    which they return depending on the CJ endpoint version.
    """
    key = str(provider or "").strip().lower()
    if key not in _PRODUCT_ADAPTERS:
        raise NormalizationError("unsupported provider")
    data = payload
    if isinstance(payload, dict):
        for envelope in ("data", "result", "content"):
            if isinstance(payload.get(envelope), (list, tuple)):
                data = payload[envelope]
                break
        else:
            return _cj_variants_from(_mapping(payload, "variants"))
    if not isinstance(data, (list, tuple)):
        return []
    out = []
    for index, entry in enumerate(data):
        if isinstance(entry, dict):
            variant = _cj_variant(entry, index)
            if variant["external_variant_id"] is not None:
                out.append(variant)
        if len(out) >= MAX_VARIANTS:
            break
    return out


def inventory(provider, payload) -> dict:
    key = str(provider or "").strip().lower()
    adapter = _INVENTORY_ADAPTERS.get(key)
    if adapter is None:
        raise NormalizationError("unsupported provider")
    return adapter(payload)


def apply_inventory(normalized_variants, readings) -> list[dict]:
    """Overlay a fresh inventory read onto normalized variants.

    A variant the inventory read did not mention keeps whatever the catalogue
    said. It does not become out of stock: the inventory endpoint returning a
    short list is a normal provider behaviour (it omits warehouses with no rows),
    and treating omission as zero would empty a storefront on every partial read.
    """
    if not readings:
        return list(normalized_variants or ())
    out = []
    for variant in normalized_variants or ():
        reading = readings.get(variant.get("external_variant_id"))
        if reading is None:
            out.append(variant)
            continue
        state, quantity = reading
        out.append({**variant, "stock_state": state, "stock_quantity": quantity})
    return out


def cost_range(normalized_variants) -> tuple[int | None, int | None]:
    """(low, high) supplier cost across variants, or (None, None) if unknown.

    Variants with unknown cost are excluded from the range rather than treated
    as zero. If *every* variant has unknown cost the answer is (None, None),
    which the UI renders as "cost unavailable" — not as "$0.00–$0.00".
    """
    costs = [v.get("cost_cents") for v in normalized_variants or ()
             if v.get("cost_cents") is not None]
    if not costs:
        return None, None
    return min(costs), max(costs)
