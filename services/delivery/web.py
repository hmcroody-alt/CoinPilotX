"""The delivery line a server-rendered page can print before its first byte.

Why a page cannot call the endpoint
-----------------------------------
``POST /api/pulse/delivery/estimate`` is the canonical answer and the app's only
source. The web could have had the same arrangement — render a placeholder, fetch
on mount — and for the signed-in shopper it would be indistinguishable. It is
wrong for the two readers who matter most on a marketplace product page:

* **Googlebot.** A delivery window that only exists after a ``fetch`` is a
  delivery window that is not in the document, and the arrival date is precisely
  the fact a shopping crawler is looking for. The whole point of §91-93 is that
  the two surfaces agree; a web surface whose estimate is invisible to the crawler
  does not agree with anything.
* **The first paint.** The line sits directly under the price. Shipping it empty
  and filling it in ~400ms later moves the buy button on the one page where the
  buyer is deciding.

Why the page must not call CJ either
------------------------------------
The obvious implementation — run the canonical path synchronously in the render —
makes PulseSoc's TTFB on its most important page a function of CJ's slowest
response, twice: once for ``getInventoryByPid`` to find the warehouse and once
for the freight quote. A supplier timeout, which this domain is built to survive,
would become a page that takes ten seconds to start.

So this module renders **from the cache only**. :func:`context` runs the real
canonical path with ``cache_only=True``, which answers from the delivery cache —
fresh *or* stale — and refuses with ``quote.REASON_NOT_CACHED`` rather than
spending a supplier call at either tier. The result:

* A warm corridor — which is the overwhelming majority of traffic on any product
  with visitors, because the cache key is (corridor, variant, quantity) and not
  (visitor, …) — renders a real date into the HTML at zero supplier cost and no
  added latency.
* A cold one renders "Checking delivery options…" with the hooks for the same
  endpoint the app calls, and the fetch that fills it in also warms the cache for
  everyone behind them.

That is a cache whose miss penalty is paid by one visitor's progressive
enhancement instead of by every visitor's TTFB, and it is the reason
``cache_only`` exists in :mod:`services.delivery.quote` and
:mod:`services.delivery.origin` at all.

The one thing a shared cache may not carry
------------------------------------------
``/pulse/marketplace/<id>`` answers anonymous readers with
``Cache-Control: public, max-age=300``. One rendering of that page is served to
every visitor behind the same cache for five minutes, so **a date resolved from
one visitor's corridor must never be in it**: a German reader would be served a
US window, indistinguishable from a correct one and wrong. The failure is not a
privacy leak — a country is not PII and none of it is echoed — it is a page
confidently stating an arrival date for the wrong country, which is the exact
class of defect this package exists to remove.

``shared_cache=True`` is therefore a mode and not a hint: it refuses to resolve a
per-visitor destination, and raises if a caller hands it one. What such a page may
still carry is :func:`destination.policy_destination` — the corridor implied by
``MARKETPLACE_SHIPPING_COUNTRIES`` naming exactly one country, which is the
default and today's production value. That is a fact about PulseSoc's checkout
rather than about the reader, identical for everyone the page is served to, and it
is what lets the most-crawled page on the site carry a real arrival window in its
HTML. A deployment that accepts two countries gets no server-rendered date and the
client fetch supplies it; nothing invents one.

The alternative was ``Vary`` on the edge's country header, and it was rejected:
it multiplies the cache key of the most-crawled page on the site by the number of
countries CJ ships to, to put a date in HTML five minutes before a visitor's own
fetch would have put the same date in the same element.

The signed-in member page has no such constraint — ``add_pwa_headers`` stamps
``no-store`` on every ``/pulse/`` response with a session — so it resolves the
buyer's corridor and can print a real window server-side.

What this module does not decide
--------------------------------
Any sentence. Every string comes from :mod:`services.delivery.copy`, which is the
half of the vocabulary contract the app's ``deliveryCopy.ts`` mirrors. The one
mapping made here is ``REASON_NOT_CACHED`` → :func:`copy.loading_copy`, and it is
here rather than in ``copy.py`` deliberately: the app can never see that reason
(it never sets ``cache_only``), so putting it in the shared table would add an
entry the contract test would have to special-case, and a special case in a
byte-for-byte contract test is how the contract stops being one.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

from services.business_os.suppliers import connections

from . import copy as delivery_copy
from . import destination as destinations
from . import entry, listing, policy, quote, store as store_module
from . import variant_facts

LOGGER = logging.getLogger(__name__)

#: Where the progressive-enhancement fetch goes. One string, so a template cannot
#: point at a path that does not exist and a rename cannot leave one caller behind.
ESTIMATE_ENDPOINT = "/api/pulse/delivery/estimate"

#: The attribute the client script looks for. Named for the domain rather than for
#: the widget, because the same hook has to work in the member storefront's
#: generated HTML and in the anonymous Jinja template.
ROOT_ATTRIBUTE = "data-delivery-estimate"


def context(variant_ref: Any, *, quantity: int = 1,
            buyer_user_id: Optional[int] = None,
            headers: Any = None,
            shared_cache: bool = False,
            connect: Optional[Callable[[], Any]] = None,
            store: Optional[Any] = None) -> Dict[str, Any]:
    """Everything a template needs to print the delivery line, and nothing else.

    Returns::

        {"tone", "text", "shipping", "retryable", "stale", "pending",
         "variant_ref", "quantity", "country", "known", "blocks_checkout", "endpoint"}

    ``pending`` is the key that separates this module's answer from the API's. It
    is true only for a cache miss — the server has no estimate *yet* and one is
    obtainable — and it is what tells the page to fetch. Every other refusal is a
    real answer and must not be re-asked over HTTP: a seller-shipped item stays
    seller-shipped, and a page that retried it would spend a request per visitor
    to be told the same thing.

    Total. Every failure below — an invalid reference, an unreadable listing, a
    supplier connection that cannot be built, a defect in this package — comes
    back as the pending line, because this function is called from inside a
    product page render and a delivery estimate must never be the reason a product
    page 500s. The ``pending`` shape is the right one for an unexpected failure
    too: the client fetch that follows goes to the endpoint, which has the
    reporting and the real error taxonomy, so a genuine outage surfaces there
    rather than being swallowed here.

    ``shared_cache=True`` for any response a proxy may hand to a second visitor.
    See the module docstring: it suppresses destination resolution entirely and
    raises if a destination input is passed anyway, which the wrapper here turns
    into the pending line and a logged exception. Loud in the log, safe on the
    page, and never a per-corridor date in a shared document.
    """
    try:
        return _context(variant_ref, quantity=quantity, buyer_user_id=buyer_user_id,
                        headers=headers, shared_cache=shared_cache,
                        connect=connect, store=store)
    except Exception:  # noqa: BLE001 — see the docstring: never break the page
        LOGGER.exception("DELIVERY_WEB_CONTEXT_FAILED variant_ref=%s", variant_ref)
        return _pending(variant_ref, quantity)


def _context(variant_ref: Any, *, quantity: int, buyer_user_id: Optional[int],
             headers: Any, shared_cache: bool,
             connect: Optional[Callable[[], Any]],
             store: Optional[Any]) -> Dict[str, Any]:
    if shared_cache and (headers is not None or buyer_user_id is not None):
        # Raised rather than ignored. Silently dropping the inputs would give a
        # caller who believes they are rendering a personalised line a page that
        # is merely wrong about delivery, and the mistake is invisible in review:
        # both versions render a plausible sentence.
        raise ValueError(
            "a shared-cached page must not resolve a visitor's destination; "
            "pass neither headers nor buyer_user_id with shared_cache=True")

    if not isinstance(variant_ref, str) or not variant_ref.strip():
        return _pending(variant_ref, quantity)
    variant_ref = variant_ref.strip()

    kwargs = {"connect": connect} if connect is not None else {}
    try:
        declared = listing.declaration(variant_ref, **kwargs)
    except variant_facts.VariantRefInvalid:
        # A reference this domain cannot parse. Pending rather than a refusal
        # sentence: the caller has a bug, and a product page is not where a buyer
        # should be told about it. The endpoint answers the same request with a
        # 400 and the reason, which is where it belongs.
        return _pending(variant_ref, quantity)

    if not declared["visible"]:
        # The page is rendering a listing this domain says nobody may ask about.
        # That is either a caller passing the wrong reference or a listing
        # unpublished between the page's own read and this one, and in both cases
        # the delivery line has nothing to say.
        return _pending(variant_ref, quantity)

    # The single-destination corridor, or the canonical `TIER_NONE` record —
    # never a hand-built "unknown destination" dict, so there is no second
    # spelling of the shape to drift. Costs no query either way.
    where = destinations.policy_destination() if shared_cache else destinations.resolve(
        # No `stated=`. A server-rendered page has no buyer assertion to carry:
        # the country picker lives in the client script, and what it does is call
        # the endpoint. Passing a query parameter through to here instead would
        # make the corridor part of the URL, which is a cache key the edge would
        # fragment on and an address fragment in browser history for a visitor who
        # is only browsing.
        buyer_user_id=buyer_user_id,
        headers=headers,
    )

    result = entry.delivery_for_variant(
        variant_ref=variant_ref,
        fulfillment=declared["fulfillment"],
        destination=where["destination"],
        quantity=quantity,
        adapter_source=_adapter_source(declared["supplier"]),
        now_at=policy.now_at,
        handling=policy.handling(),
        buffer_days=policy.buffer_days(),
        dispatch_cutoff_hour=policy.dispatch_cutoff_hour(),
        holidays=policy.closures(),
        ceiling_days=policy.ceiling_days(),
        policy=policy.route_policy(),
        unspecified_basis=policy.unspecified_basis(),
        store=store if store is not None else store_module.CacheEngineStore(),
        # The whole reason this module is not just a call to the endpoint's body.
        cache_only=True,
    )

    buyer = result["buyer"]
    if buyer.get("reason") == quote.REASON_NOT_CACHED:
        # Not an error and not a delivery fact — nobody asked the supplier. The
        # client fetch is the thing that asks, so the line says what it is doing.
        return _rendered(delivery_copy.loading_copy(), variant_ref, quantity,
                         where, buyer, pending=True)

    return _rendered(delivery_copy.delivery_copy(delivery=buyer, destination=where),
                     variant_ref, quantity, where, buyer, pending=False)


def accepted_countries() -> Tuple[Dict[str, str], ...]:
    """The countries a "deliver to" control may offer, as ``{"code", "name"}``.

    ``marketplace_fulfillment.shipping_countries`` and not a list of this module's
    own, for the same reason :func:`destination.policy_destination` uses it: the
    picker must not offer a corridor checkout would refuse an address in. A buyer
    who selected one would be shown a real, correct arrival window and then
    blocked at payment, which is a worse outcome than never offering it.

    A code the name table does not know is offered under its own code rather than
    dropped — the platform accepts it, so it has to be selectable. That is the
    checkout picker's rule (``checkoutCountries.countryName``) and the opposite of
    ``marketplace_fulfillment.country_name``'s, which returns ``""`` because it is
    naming a country *to a supplier*. Both are right for their side.

    Sorted by name so the order does not follow whatever an operator typed into
    the environment variable.
    """
    from services import marketplace_fulfillment

    try:
        codes = marketplace_fulfillment.shipping_countries()
    except Exception:  # noqa: BLE001 — no configuration, no picker
        LOGGER.exception("DELIVERY_WEB_COUNTRIES_UNREADABLE")
        return ()
    offered = [{"code": code,
                "name": marketplace_fulfillment.country_name(code) or code}
               for code in codes]
    return tuple(sorted(offered, key=lambda entry: entry["name"]))


def _rendered(line: Dict[str, Any], variant_ref: str, quantity: int,
              where: Dict[str, Any], buyer: Dict[str, Any],
              *, pending: bool) -> Dict[str, Any]:
    """One copy dict plus the page's own hooks, flattened for a template.

    The buyer half is deliberately *not* passed through. A template with the
    estimate in hand can print a field this module never reviewed — a raw
    ``confidence`` token, a bare ISO date — and the whole point of the copy layer
    is that the sentences are decided in one place. What a template gets is the
    sentence and the facts it needs to wire a fetch.
    """
    offered = accepted_countries()
    return {
        "tone": line["tone"],
        "text": line["text"],
        "shipping": line["shipping"],
        "retryable": line["retryable"],
        "stale": line["stale"],
        "pending": pending,
        "variant_ref": variant_ref,
        "quantity": quantity,
        # Echoed for the picker's benefit: "to Germany — change" needs the country,
        # and `known` is what stops the page claiming a resolved destination for a
        # corridor guessed from an edge header. Never the postal code: a page-tier
        # resolution cannot carry one (`destination.POSTAL_TIERS`), so there is
        # none to leak.
        "country": where["destination"].get("country"),
        "known": bool(where.get("known")),
        "blocks_checkout": delivery_copy.blocks_checkout(buyer),
        "endpoint": ESTIMATE_ENDPOINT,
        # Empty on a single-destination deployment: there is nothing to choose,
        # and a select with one option is a control that cannot do anything.
        "countries": offered if len(offered) > 1 else (),
    }


def _pending(variant_ref: Any, quantity: Any) -> Dict[str, Any]:
    """The line for "this page cannot say yet", with no estimate behind it.

    Shares :func:`copy.loading_copy` with the cache-miss case rather than having a
    line of its own, because from the buyer's side the two are the same situation:
    the answer is not on this page and something is about to go and get it. The
    difference between them is a server-side one and belongs in the log, which is
    where :func:`context` puts it.
    """
    line = delivery_copy.loading_copy()
    return {
        "tone": line["tone"], "text": line["text"], "shipping": line["shipping"],
        "retryable": line["retryable"], "stale": line["stale"], "pending": True,
        "variant_ref": variant_ref if isinstance(variant_ref, str) else "",
        "quantity": quantity if isinstance(quantity, int) and quantity > 0 else 1,
        "country": None, "known": False, "blocks_checkout": False,
        "endpoint": ESTIMATE_ENDPOINT, "countries": (),
    }


def _adapter_source(supplier: Optional[Dict[str, Any]]) -> Callable[[], Any]:
    """A callable that yields the supplier adapter, or raises.

    Raising is the contract ``entry._Deferred`` is built around, and it is what
    turns an incomplete connection mapping into ``connection_unavailable`` — an
    answer that sends an operator to the credential mapping — instead of into a
    missing weight, which would send them to the catalogue.

    In practice this is never called on the web path: ``cache_only=True`` means
    ``_Deferred`` is never resolved, because neither the origin lookup nor the
    freight quote reaches a supplier. It is passed anyway because
    ``delivery_for_variant`` requires an ``adapter_source`` and a ``lambda`` that
    raised something generic would turn the day somebody sets ``cache_only=False``
    here into a debugging session instead of a known reason.

    ``worker_adapter`` and not ``adapter_for``: there is no actor to authorize —
    the platform is reading a shipping rate on a visitor's behalf.
    """

    def source() -> Any:
        if not supplier:
            raise RuntimeError("this listing has no complete supplier connection")
        return connections.worker_adapter(
            supplier["connection_id"], supplier["business_id"], supplier["store_id"])

    return source


def html(line: Dict[str, Any]) -> str:
    """The delivery block as HTML, for the member storefront's string-built page.

    A function rather than a template because ``marketplace_storefront`` builds its
    product page as one f-string and has no Jinja context to extend. The anonymous
    page does have one and uses :func:`context` directly through a partial, so this
    is not the only renderer — which is why both of them take the same dict from
    the same function, and why every sentence in both comes from ``copy``.

    Escaping: the only interpolated values are this module's own copy strings, the
    endpoint constant, and ``variant_ref``/``quantity``/``country``, which are a
    parsed reference, an int and a two-letter code. Nothing here originates with a
    seller. ``_attr`` is applied regardless, because "no attacker-controlled value
    reaches this string" is a property of today's callers and not of the function.
    """
    classes = ["pulse-delivery", f"pulse-delivery--{line['tone'].lower()}"]
    if line["pending"]:
        classes.append("pulse-delivery--pending")
    if line["stale"]:
        classes.append("pulse-delivery--stale")

    attributes = [
        'class="%s"' % " ".join(classes),
        '%s="1"' % ROOT_ATTRIBUTE,
        'data-variant-ref="%s"' % _attr(line["variant_ref"]),
        'data-quantity="%d"' % int(line["quantity"]),
        'data-endpoint="%s"' % _attr(line["endpoint"]),
        'data-country="%s"' % _attr(line["country"] or ""),
        'data-pending="%s"' % ("1" if line["pending"] else "0"),
        # The sentence to show again if the buyer changes the country, carried as
        # data rather than written into the script. The script has no delivery
        # vocabulary of its own on purpose: there are exactly two copies of this
        # vocabulary, held together by a contract test, and a third one in a
        # JavaScript file would be outside it.
        'data-loading-text="%s"' % _attr(delivery_copy.LOADING_TEXT),
    ]
    if line["pending"]:
        # `aria-live` only while a fetch is expected. On a settled line it would
        # announce a sentence that is not going to change, on every page load.
        attributes.append('aria-live="polite"')

    shipping = ""
    if line["shipping"]:
        shipping = ('<span class="pulse-delivery__shipping">%s</span>'
                    % _attr(line["shipping"]))

    picker = ""
    if line.get("countries"):
        # A real `<label>` and a real `<select>`: the control changes the estimate
        # for the page, so it is a form control and not a div with a click
        # handler. Rendered server-side and not injected by the script, so it
        # works with the estimate still pending and is in the document for a
        # keyboard or screen-reader user before any JavaScript runs.
        options = "".join(
            '<option value="%s"%s>%s</option>'
            % (_attr(entry["code"]),
               ' selected' if entry["code"] == (line["country"] or "") else "",
               _attr(entry["name"]))
            for entry in line["countries"])
        picker = (
            '<label class="pulse-delivery__where">'
            '<span class="pulse-delivery__where-label">Deliver to</span>'
            '<select data-delivery-country>%s</select></label>' % options)
    # Percent formatting throughout rather than f-strings, because the attribute
    # values are quoted and this module has to parse on Python 3.11 — nested
    # same-type quotes and backslashes inside an f-string expression are both 3.12+.
    # CI and production run 3.11; the local interpreter is newer and would have
    # accepted the prettier version right up to the deploy.
    return ('<div %s><span class="pulse-delivery__text">%s</span>%s%s</div>'
            % (" ".join(attributes), _attr(line["text"]), shipping, picker))


#: The eight declarations the markup above needs, as the canonical copy.
#:
#: Two surfaces render `html()` and they have unrelated chrome: the anonymous
#: page extends `_public_shell.html`, which is deliberately one request and so
#: carries these rules inlined in its own `<style>`; the signed-in page is an
#: f-string inside `bot.py` with no template to extend. Neither can import the
#: other's stylesheet, and a second hand-written copy of the rules would drift
#: from the first the moment a class name here changes -- silently, because a
#: page with a missing rule still renders a correct sentence, just unstyled.
#:
#: So the string lives here next to the markup that names the classes, the
#: signed-in page emits it through `style_tag()`, and
#: `tests/delivery/test_delivery_web.py` asserts every selector in it is present
#: in `_public_shell.html`. The shell keeps its literal copy -- a template cannot
#: interpolate this without being handed a delivery context, and eight feature
#: pages share that shell -- but it can no longer diverge unnoticed.
CSS = """
.pulse-delivery{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin:10px 0 0;font-size:15px;color:#c9dbe4}
.pulse-delivery__text{font-weight:700;color:#f2fbff}
.pulse-delivery__shipping{font-weight:800;color:#36e58f}
.pulse-delivery--pending .pulse-delivery__text{color:#a9bfca;font-weight:600}
.pulse-delivery--terminal .pulse-delivery__text{color:#a9bfca;font-weight:600}
.pulse-delivery__where{display:inline-flex;align-items:center;gap:8px;font-size:14px;color:#7f97a4}
.pulse-delivery__where select{min-height:36px;border-radius:9px;border:1px solid rgba(110,223,246,.26);background:#0b1626;color:#f2fbff;padding:4px 8px}
""".strip()


def style_tag() -> str:
    """:data:`CSS` as a ``<style>`` element, for a surface with no stylesheet.

    Inline rather than a `/static/css/pulse_delivery.css` link because the
    signed-in product page would then make a render-blocking request for seven
    declarations, and because a file under `/static` is served with a one-year
    immutable cache: shipping a class-name change would mean remembering to bump
    a `?v=` token in a second place. The rules travel with the markup instead.
    """
    return "<style>%s</style>" % CSS


def selectors() -> Tuple[str, ...]:
    """Every selector in :data:`CSS`, for the template-parity test."""
    found = []
    for block in CSS.split("}"):
        head = block.split("{")[0].strip()
        if head:
            found.append(head)
    return tuple(found)


def _attr(value: Any) -> str:
    """Escape for an HTML attribute or text node.

    Hand-rolled rather than ``markupsafe.escape`` so this module imports nothing
    from the web framework — it is called from a Flask render and from a test that
    has no app context, and the estimate domain has no other Flask dependency.
    """
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))
