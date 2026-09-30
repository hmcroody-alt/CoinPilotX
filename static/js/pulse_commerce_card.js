/**
 * The browser twin of `services/pulse_commerce_card.py`.
 *
 * Same markup, same class names, same three gates. Read that module's docstring
 * for why the commerce attachment needs a renderer at all and why there are two
 * of them; `tests/pulse_commerce/test_commerce_card_parity.py` reads both files
 * and fails when they drift.
 *
 * Used by the two web surfaces that build their cards in the browser:
 *   - `static/js/pulse_home_core.js` -> `renderPost()`, the home feed.
 *   - the reels shell's inline `reelHtml()` in `bot.py`.
 *
 * `/pulse/post/<id>` does not use this file. It is the SEO and link-unfurl
 * target, so its card is rendered into the response body by the Python twin.
 *
 * Standalone and dependency-free on purpose: the reels shell is an inline
 * `<script>` inside an f-string with its own `esc`, and the home feed is a
 * module-scoped IIFE. Neither can import, so this attaches one global.
 */
(function () {
  "use strict";

  var AVAILABLE = "";
  var OUT_OF_STOCK = "OUT_OF_STOCK";
  var UNAVAILABLE = "UNAVAILABLE";
  var NOT_PRICED = "NOT_PRICED";
  var REMOVED = "REMOVED";

  var KNOWN = [AVAILABLE, OUT_OF_STOCK, UNAVAILABLE, NOT_PRICED, REMOVED];
  // Mirrors `_PRICE_OK` in the Python twin and `commercePriceVisible` in the
  // app. The line is drawn at who took the product down: sold out and unpriced
  // are the seller still offering the listing, so the price stays and the state
  // is a chip beside it.
  var PRICE_OK = [AVAILABLE, OUT_OF_STOCK, NOT_PRICED];
  var GONE = [UNAVAILABLE, REMOVED];
  var STOCKLESS = ["digital", "course", "service", "event", "booking"];

  /**
   * Python's `html.escape(value, quote=True)`, character for character.
   *
   * The apostrophe is `&#x27;` and not the more familiar `&#39;` because that
   * is what CPython emits, and the two renderers are compared byte for byte by
   * `test_the_two_renderers_emit_identical_markup`. Both entities are valid and
   * both render as `'`; only one of them keeps that test green. It matters
   * beyond the test too — the markup uses single-quoted attributes, so this is
   * the escape that stops a listing title from closing an `aria-label` and
   * opening an event handler.
   */
  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;" }[c];
      }
    );
  }

  function text(value) {
    return String(value === null || value === undefined ? "" : value).trim();
  }

  /** Present and shaped like an overlay. Mirrors `isPulseCommerceOverlay`. */
  function isOverlay(value) {
    return !!value && typeof value === "object" && value.pulsedrop === true && !!value.product;
  }

  /**
   * `null` when the label holds no amount checkout could charge.
   *
   * The payload ships a formatted label and no minor-unit integer, because
   * there is one price formatter in this product and it is server-side. So the
   * only question answerable here is whether the label contains a number, which
   * is exactly the question NOT_PRICED asks.
   */
  function priceMinor(product) {
    var label = text(product && product.price_label);
    if (!label) return null;
    var digits = label.replace(/\D/g, "");
    if (!digits) return null;
    var value = parseInt(digits, 10);
    if (!value) return null;
    return value;
  }

  /**
   * The availability code: the server's when recognised, derived otherwise.
   *
   * Defaulting an unrecognised code to "available" is how a sold-out product
   * gets an enabled button, so an unknown shape is re-derived from the four
   * fields the payload ships for exactly this purpose.
   */
  function availabilityBlock(commerce) {
    var declared = (commerce && commerce.availability) || {};
    if (typeof declared.code === "string" && KNOWN.indexOf(declared.code) !== -1) {
      return declared.code;
    }
    var product = (commerce && commerce.product) || {};
    if (product.buyer_visible === false) return UNAVAILABLE;
    if (String(product.inventory_state || "").toLowerCase() === "out_of_stock") return OUT_OF_STOCK;
    var productType = String(product.product_type || "").toLowerCase();
    if (STOCKLESS.indexOf(productType) === -1 && Number(product.quantity || 0) <= 0) {
      return OUT_OF_STOCK;
    }
    if (priceMinor(product) === null) return NOT_PRICED;
    return AVAILABLE;
  }

  /**
   * May the card be followed?
   *
   * Gated on the route and the state as well as the flag. The product page 404s
   * for any listing that is not public, so a link to a withdrawn product is a
   * link to an error page. The server already declines to emit a route for
   * those states; this refuses to trust that it always will.
   */
  function ctaEnabled(commerce) {
    var cta = (commerce && commerce.cta) || {};
    if (!cta.enabled) return false;
    if (!text(cta.route)) return false;
    return availabilityBlock(commerce) === AVAILABLE;
  }

  function priceVisible(commerce) {
    return PRICE_OK.indexOf(availabilityBlock(commerce)) !== -1;
  }

  /**
   * A same-origin path, or "".
   *
   * The one thing a renderer must never do is turn a payload value into an
   * `href` with a scheme on it. A `javascript:` route reaching here would be a
   * stored XSS with the platform itself as the injection point.
   */
  function safeRoute(route) {
    var value = text(route);
    if (value.charAt(0) !== "/" || value.slice(0, 2) === "//") return "";
    return value;
  }

  function chip(value, extra) {
    if (!value) return "";
    return "<span class='pulse-commerce-chip" + (extra ? " " + extra : "") + "'>" + esc(value) + "</span>";
  }

  /** The attachment card as an HTML string, or "" when there is nothing to show. */
  function html(commerce, options) {
    if (!isOverlay(commerce)) return "";
    var opts = options || {};
    var product = commerce.product || {};
    var seller = commerce.seller || {};
    var label = commerce.label || {};
    var cta = commerce.cta || {};
    var availability = commerce.availability || {};

    var block = availabilityBlock(commerce);
    var routable = ctaEnabled(commerce);
    var showPrice = priceVisible(commerce) && !!text(product.price_label);
    var showState = block !== AVAILABLE;

    // Anything unrecognised reads as a Signal, which is the treatment that
    // survives being placed anywhere. The reel variant assumes dark video
    // behind it and is unreadable on a light surface.
    var variant = String(opts.surface || commerce.surface || "signal").toLowerCase();
    if (variant !== "reel") variant = "signal";

    var title = text(product.title);
    // `cover_image_url` is the key the server emits. `image_url` is the name the
    // app's type declared, which is why that card has never shown a thumbnail;
    // both are read here so neither spelling produces a blank square.
    var image = text(product.cover_image_url) || text(product.image_url);
    var price = text(product.price_label);
    var store = text(seller.store_name) || text(seller.username);
    var labelText = text(label.fallback);
    var stateText = text(availability.fallback);
    var ctaText = text(cta.fallback);
    // The server's sentence, but only where it is allowed to mention a price.
    // `hydration.py` assembles it under the same disclosure rule the pixels
    // follow, and taking it verbatim regardless would be the one place that
    // trusts that rule instead of mirroring it -- the place where a regression
    // is invisible, because a correct-looking card can still have an
    // `aria-label` announcing a price the card withholds.
    var accessibility = text(commerce.accessibility_text);
    if (!accessibility || !showPrice) {
      accessibility = [labelText, title, showState ? stateText : ""].filter(Boolean).join(". ");
    }

    var thumb = image
      ? "<span class='pulse-commerce-thumb'><img src='" + esc(image) + "' alt='' loading='lazy' decoding='async'></span>"
      : "<span class='pulse-commerce-thumb is-empty' aria-hidden='true'></span>";

    var chips = chip(labelText, "pulse-commerce-chip-label");
    if (showState) {
      chips += chip(stateText, "pulse-commerce-chip-state" + (GONE.indexOf(block) !== -1 ? " is-gone" : ""));
    }
    var chipsHtml = chips ? "<span class='pulse-commerce-chips'>" + chips + "</span>" : "";

    var titleHtml = title ? "<span class='pulse-commerce-title'>" + esc(title) + "</span>" : "";

    var meta = "";
    // Never re-formatted here: the server formatted it in the listing's own
    // currency, and a client-side `toFixed` renders 4900 yen as $49.00.
    if (showPrice) meta += "<span class='pulse-commerce-price'>" + esc(price) + "</span>";
    if (store) meta += "<span class='pulse-commerce-store'>" + esc(store) + "</span>";
    var metaHtml = meta ? "<span class='pulse-commerce-meta'>" + meta + "</span>" : "";

    var ctaHtml = routable && ctaText ? "<span class='pulse-commerce-cta'>" + esc(ctaText) + "</span>" : "";

    var body = thumb + "<span class='pulse-commerce-copy'>" + chipsHtml + titleHtml + metaHtml + ctaHtml + "</span>";

    // One announcement rather than five, for the reason the Python twin gives
    // at length: walked span by span this reads as five unrelated fragments.
    var main = routable
      ? "<a class='pulse-commerce-main' href='" + esc(safeRoute(cta.route)) + "' aria-label='" + esc(accessibility) + "'>" + body + "</a>"
      : "<div class='pulse-commerce-main is-static' role='group' aria-label='" + esc(accessibility) + "'>" + body + "</div>";

    var storeRoute = safeRoute(seller.route);
    var storeHtml = storeRoute
      ? "<a class='pulse-commerce-seller' href='" + esc(storeRoute) + "'>Visit store</a>"
      : "";

    var attribution = (commerce.attribution || {}).token || "";
    var listingId = product.listing_id || 0;

    return (
      "<section class='pulse-commerce-card pulse-commerce-" + esc(variant) + "' " +
      "data-pulse-commerce='1' data-surface='" + esc(variant) + "' " +
      "data-attribution='" + esc(attribution) + "' data-listing-id='" + esc(listingId) + "' " +
      "data-availability='" + esc(block) + "'>" + main + storeHtml + "</section>"
    );
  }

  /** `html()` for `post.commerce`. "" for a post without one. */
  function postHtml(post, options) {
    if (!post || typeof post !== "object") return "";
    return html(post.commerce, options);
  }

  /**
   * Append the card to `container` and return the element, or `null`.
   *
   * For the home feed, which builds its card as DOM rather than as a string.
   * Wrapped so a malformed overlay cannot take the post down with it: a post
   * whose commerce hydration went wrong must still render as a post.
   */
  function render(container, commerce, options) {
    if (!container) return null;
    var markup;
    try {
      markup = html(commerce, options);
    } catch (error) {
      if (window.console && console.warn) console.warn("PulseCommerceCard render failed", error);
      return null;
    }
    if (!markup) return null;
    var host = document.createElement("div");
    host.innerHTML = markup;
    var node = host.firstElementChild;
    if (!node) return null;
    container.appendChild(node);
    return node;
  }

  window.PulseCommerceCard = {
    html: html,
    postHtml: postHtml,
    render: render,
    isOverlay: isOverlay,
    availabilityBlock: availabilityBlock,
    ctaEnabled: ctaEnabled,
    priceVisible: priceVisible,
  };
})();
