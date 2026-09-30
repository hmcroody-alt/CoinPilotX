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

  // The store link's copy is the renderer's own -- the overlay ships a key
  // beside every string it supplies, and it supplies no seller CTA -- so the
  // key is named here, and it is the key `CommerceOverlay.tsx` already renders
  // through rather than a second one meaning the same thing.
  var SELLER_I18N_KEY = "commerce:pulsedrop.seller.visitStore";
  var SELLER_FALLBACK = "Visit store";

  // The accessible sentence, in visual order. Each part is read off the card
  // itself rather than the payload, which is what keeps the sentence honest:
  // the price span exists only where `showPrice` put it, so a card that
  // withholds a price has no node here to announce.
  var COMPOSED_PARTS = [
    ".pulse-commerce-chip-label",
    ".pulse-commerce-title",
    ".pulse-commerce-price",
    ".pulse-commerce-chip-state",
  ];

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

  /**
   * The `data-i18n` marker for an overlay block, or "".
   *
   * The overlay ships a translation key beside every string it supplies and the
   * app renders through the key; this renderer read the fallback alone, so a
   * French member scrolling a French post met an English badge and an English
   * button on the one element meant to sell them something. The English
   * fallback stays in the markup and `pulse_i18n.js` swaps the text, because
   * the server does not know the reader's language on a cached response.
   */
  function i18n(block) {
    var key = text(block && block.i18n_key);
    return key ? " data-i18n='" + esc(key) + "'" : "";
  }

  function chip(value, extra, marker) {
    if (!value) return "";
    return "<span class='pulse-commerce-chip" + (extra ? " " + extra : "") + "'" + (marker || "") + ">" + esc(value) + "</span>";
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
    var composed = !accessibility || !showPrice;
    if (composed) {
      accessibility = [labelText, title, showState ? stateText : ""].filter(Boolean).join(". ");
    }

    var thumb = image
      ? "<span class='pulse-commerce-thumb'><img src='" + esc(image) + "' alt='' loading='lazy' decoding='async'></span>"
      : "<span class='pulse-commerce-thumb is-empty' aria-hidden='true'></span>";

    var chips = chip(labelText, "pulse-commerce-chip-label", i18n(label));
    if (showState) {
      chips += chip(stateText, "pulse-commerce-chip-state" + (GONE.indexOf(block) !== -1 ? " is-gone" : ""), i18n(availability));
    }
    var chipsHtml = chips ? "<span class='pulse-commerce-chips'>" + chips + "</span>" : "";

    var titleHtml = title ? "<span class='pulse-commerce-title'>" + esc(title) + "</span>" : "";

    var meta = "";
    // Never re-formatted here: the server formatted it in the listing's own
    // currency, and a client-side `toFixed` renders 4900 yen as $49.00.
    if (showPrice) meta += "<span class='pulse-commerce-price'>" + esc(price) + "</span>";
    if (store) meta += "<span class='pulse-commerce-store'>" + esc(store) + "</span>";
    var metaHtml = meta ? "<span class='pulse-commerce-meta'>" + meta + "</span>" : "";

    var ctaHtml = routable && ctaText ? "<span class='pulse-commerce-cta'" + i18n(cta) + ">" + esc(ctaText) + "</span>" : "";

    var body = thumb + "<span class='pulse-commerce-copy'>" + chipsHtml + titleHtml + metaHtml + ctaHtml + "</span>";

    // One announcement rather than five, for the reason the Python twin gives
    // at length: walked span by span this reads as five unrelated fragments.
    var main = routable
      ? "<a class='pulse-commerce-main' href='" + esc(safeRoute(cta.route)) + "' aria-label='" + esc(accessibility) + "'>" + body + "</a>"
      : "<div class='pulse-commerce-main is-static' role='group' aria-label='" + esc(accessibility) + "'>" + body + "</div>";

    var storeRoute = safeRoute(seller.route);
    var storeHtml = storeRoute
      ? "<a class='pulse-commerce-seller' href='" + esc(storeRoute) + "' data-i18n='" + esc(SELLER_I18N_KEY) + "'>" + esc(SELLER_FALLBACK) + "</a>"
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
   * Translate a card that is already in the document. Idempotent.
   *
   * `pulse_i18n.js` sweeps `[data-i18n]` once, at DOMContentLoaded. Feed and
   * reel cards arrive long after that — on scroll, on a new post, on a profile
   * switch — so a card that is only ever caught by that pass is translated
   * exactly when it happened to be in the first page of results. Every surface
   * that inserts a card has to say so, which is what this is for.
   *
   * The accessible sentence is rebuilt afterwards, for every card. A translated
   * chip over an English `aria-label` is a card that shows one thing and
   * announces another, and that is the half of the bug a sighted reviewer
   * cannot see.
   *
   * Every card, and not only the ones the renderer composed: the server's own
   * prose sentence is built from `editorial.accessibility_text`, which says in
   * its own docstring that it "cannot know the reader's language" and is
   * English by construction. So the richer sentence is the right one for a
   * reader who has no JavaScript -- their chips are English too -- and the
   * wrong one the moment the sweep translates the card around it.
   *
   * Silent when `pulse_i18n.js` has not loaded: the markup already carries the
   * server's English fallback, so an untranslated card is the previous
   * behaviour rather than an empty one.
   */
  function recompose(root) {
    var cards = root && root.querySelectorAll ? root.querySelectorAll(".pulse-commerce-main") : [];
    for (var index = 0; index < cards.length; index += 1) {
      var card = cards[index];
      var parts = [];
      for (var part = 0; part < COMPOSED_PARTS.length; part += 1) {
        var node = card.querySelector(COMPOSED_PARTS[part]);
        var value = node ? text(node.textContent) : "";
        if (value) parts.push(value);
      }
      if (parts.length) card.setAttribute("aria-label", parts.join(". "));
    }
    return root;
  }

  function localize(root) {
    var i18nApi = window.PulseI18n;
    if (!root || !i18nApi || typeof i18nApi.translateMarkedNodes !== "function") return root;
    i18nApi.translateMarkedNodes(root);
    return recompose(root);
  }

  // `pulse_i18n.js` re-sweeps the whole document whenever the language settles
  // -- on load, and again when `/api/account/language` answers with something
  // other than the cached guess. That sweep translates the chips of cards it
  // did not render, including the server-rendered permalink, and it knows
  // nothing about the sentence composed from them. Without this the permalink
  // shows French chips and announces an English sentence.
  if (typeof document !== "undefined" && document.addEventListener) {
    document.addEventListener("PulseLanguageChanged", function () {
      recompose(document);
    });
    // And once for the sweep that already happened. On the permalink this file
    // is deferred *after* `pulse_i18n.js`, so i18n has translated the served
    // card's chips and announced it before the listener above exists. Without
    // this the permalink's accessible sentence stays English until something
    // else changes the language, which for a logged-out reader is never.
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () { recompose(document); }, { once: true });
    } else {
      recompose(document);
    }
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
    // After the append, so a reader on a translated page never sees the English
    // fallback paint first.
    localize(node);
    return node;
  }

  window.PulseCommerceCard = {
    html: html,
    postHtml: postHtml,
    render: render,
    localize: localize,
    isOverlay: isOverlay,
    availabilityBlock: availabilityBlock,
    ctaEnabled: ctaEnabled,
    priceVisible: priceVisible,
  };
})();
