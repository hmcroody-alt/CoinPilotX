/**
 * The product attachment that renders between a post's media and its actions.
 *
 * ## Why this is its own file
 *
 * The web has two post renderers and only ever runs one of them. `pulse_page_html`
 * serves an inline `<script data-pulse-shell-runtime>` whose `postHtml()` builds a
 * post as an HTML string; under the default `core` boot profile that whole script
 * is stripped and `pulse_home_core.js` builds the same post as DOM nodes instead.
 * Neither can call the other, because whichever one is present, the other is gone.
 *
 * Written twice, the attachment would be two cards that agree today. The first
 * divergence is a product that is tappable on one profile and inert on the other,
 * or priced on one and blank on the other, and nothing on either side would fail:
 * each renderer's own tests would still pass. So the card lives here, loaded by
 * both profiles, and each renderer asks for it in the shape it happens to need --
 * {@link html} for the string builder, {@link element} for the DOM builder. One
 * implementation, two adapters.
 *
 * ## What it will not invent
 *
 * Shipping cost, delivery estimate, rating and review count are **not rendered**,
 * because the overlay carries none of them. The equivalent native card takes the
 * same position, and `commerce/productSignal.ts` explains why: a card that renders
 * a fact it was not given is a card that lies about a stranger's product. When the
 * server starts sending them, they belong here -- not in a placeholder.
 *
 * ## What it reads
 *
 * `post.commerce`, produced by `services/pulsedrop/hydration.py:overlay()` and
 * declared in `mobile-native/src/api/pulseCommerceOverlay.ts`. The field names are
 * the app's, not the database's, and the two sides are held together by
 * `tests/pulsedrop/test_client_contract.py` -- which exists because they once
 * drifted and shipped a blank thumbnail to every client.
 *
 * Nothing here is cached or persisted. The overlay is live-hydrated on every read
 * precisely so it cannot be older than the post it arrived with.
 */
(function () {
  "use strict";

  var ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(
      /[&<>"']/g,
      function (char) {
        return ESCAPES[char];
      }
    );
  }

  /** Same normalization the inline runtime's `mediaUrl` applies to a bare key. */
  function mediaUrl(url) {
    var value = String(url || "").trim();
    if (!value) return "";
    if (
      value.indexOf("http://") === 0 ||
      value.indexOf("https://") === 0 ||
      value.indexOf("/") === 0 ||
      value.indexOf("data:") === 0 ||
      value.indexOf("blob:") === 0
    ) {
      return value;
    }
    return "/" + value.replace(/^\/+/, "");
  }

  /**
   * The attribution the destination page needs to credit this tap.
   *
   * `pd` is the server's own join token, carried rather than reconstructed: it
   * already encodes publication, surface and listing in the spelling the ledger
   * indexes on, so rebuilding it here would be a second implementation of a key
   * whose whole job is to match.
   */
  function attribution(post, commerce, listingId) {
    var query = new URLSearchParams({
      src: "social_post",
      surface: String(commerce.surface || "signal"),
      post: String((post && post.id) || ""),
      listing: String(listingId)
    });
    var token = String((commerce.attribution || {}).token || "");
    if (token) query.set("pd", token);
    return query.toString();
  }

  function withAttribution(route, post, commerce, listingId) {
    var base = String(route || "").trim();
    if (!base) return "";
    return base + (base.indexOf("?") >= 0 ? "&" : "?") + attribution(post, commerce, listingId);
  }

  /**
   * The overlay, if this post has a renderable one.
   *
   * `pulsedrop === true` is the discriminator the payload declares, and a listing
   * id of 0 is the server's sentinel for an overlay whose listing row is gone. A
   * card for a product that no longer exists is worse than no card, so both are
   * refusals rather than degradations.
   */
  function overlayOf(post) {
    var commerce = post && post.commerce;
    if (!commerce || commerce.pulsedrop !== true || !commerce.product) return null;
    var listingId = Number(commerce.product.listing_id || 0);
    if (!(listingId > 0)) return null;
    return { commerce: commerce, listingId: listingId };
  }

  /**
   * The `data-i18n` marker for a block that carries a translation key.
   *
   * The overlay ships `i18n_key` alongside every `fallback` it writes, and the
   * native card translates through it. The web read only the fallback, so a
   * reader in French got a French post with an English badge, an English button
   * and an English stock line -- one payload, two languages.
   *
   * `pulse_i18n.js` already resolves `[data-i18n]` against the current language
   * and falls back to the node's own text, so marking the node is the whole fix:
   * the English string stays in the HTML, which is also what a crawler should
   * see on the server-rendered permalink.
   */
  function i18nAttr(block) {
    var key = String((block || {}).i18n_key || "").trim();
    return key ? ' data-i18n="' + esc(key) + '"' : "";
  }

  function html(post) {
    var found = overlayOf(post);
    if (!found) return "";
    var commerce = found.commerce;
    var listingId = found.listingId;
    var product = commerce.product || {};
    var seller = commerce.seller || {};
    var cta = commerce.cta || {};
    var availability = commerce.availability || {};

    // The CTA route is the only product route. It is empty for every state the
    // marketplace would 404 on, which is how a withdrawn listing loses its tap
    // while keeping its card. See `hydration.py`'s `_ROUTABLE`.
    var productHref = cta.enabled ? withAttribution(cta.route, post, commerce, listingId) : "";
    var storeHref = seller.route ? withAttribution(seller.route, post, commerce, listingId) : "";

    var title = String(product.title || "").trim();
    var price = String(product.price_label || "").trim();
    var store = String(seller.store_name || seller.username || "").trim();
    var badge = String((commerce.label || {}).fallback || "Discover").trim();
    var state = String(availability.fallback || "").trim();
    var image = String(product.image_url || "").trim();

    var track =
      'data-commerce-listing="' + listingId + '"' +
      ' data-commerce-post="' + esc(String((post && post.id) || "")) + '"' +
      ' data-commerce-token="' + esc(String((commerce.attribution || {}).token || "")) + '"' +
      ' data-commerce-surface="' + esc(String(commerce.surface || "signal")) + '"' +
      ' data-commerce-seller="' + Number(seller.seller_user_id || 0) + '"';

    // An empty span rather than a broken <img>: a listing with no cover is a real
    // state, and a browser's broken-image glyph reads as the page having failed.
    var picture = image
      ? '<img class="pulse-commerce-thumb" src="' + esc(mediaUrl(image)) +
        '" alt="" loading="lazy" decoding="async" width="72" height="72">'
      : '<span class="pulse-commerce-thumb"></span>';

    // The thumbnail is a duplicate of the title link, so it is taken out of the
    // tab order and hidden from screen readers. A keyboard user reaching the same
    // product twice per post is the cost of making the picture clickable.
    var thumb = productHref
      ? '<a ' + track + ' data-commerce-click="product" href="' + esc(productHref) +
        '" tabindex="-1" aria-hidden="true">' + picture + "</a>"
      : picture;

    var titleHtml = title
      ? productHref
        ? '<a class="pulse-commerce-title" ' + track + ' data-commerce-click="product" href="' +
          esc(productHref) + '">' + esc(title) + "</a>"
        : '<span class="pulse-commerce-title">' + esc(title) + "</span>"
      : "";

    var priceHtml = price ? '<span class="pulse-commerce-price">' + esc(price) + "</span>" : "";
    var sellerHtml = store
      ? storeHref
        ? '<a class="pulse-commerce-seller" ' + track + ' data-commerce-click="store" href="' +
          esc(storeHref) + '">' + esc(store) + "</a>"
        : '<span class="pulse-commerce-seller">' + esc(store) + "</span>"
      : "";
    var meta =
      priceHtml || sellerHtml
        ? '<p class="pulse-commerce-meta">' + priceHtml +
          (priceHtml && sellerHtml ? '<span class="time-dot">•</span>' : "") + sellerHtml + "</p>"
        : "";

    // Either a button or a state chip, never both and never neither: the row that
    // would otherwise be empty is what tells a reader the product is unavailable.
    var action = productHref
      ? '<a class="pulse-commerce-cta" ' + track + ' data-commerce-click="product"' + i18nAttr(cta) +
        ' href="' + esc(productHref) + '">' + esc(cta.fallback || "View product") + "</a>"
      : state
        ? '<span class="pulse-commerce-state"' + i18nAttr(availability) + ">" + esc(state) + "</span>"
        : "";

    var storeRow = storeHref
      ? '<a class="pulse-commerce-store" ' + track + ' data-commerce-click="store" href="' +
        esc(storeHref) + '">Visit store ›</a>'
      : "";

    return (
      '<section class="pulse-commerce" ' + track + ' aria-label="' +
      esc(commerce.accessibility_text || title) + '">' + thumb +
      '<div class="pulse-commerce-body"><div class="pulse-commerce-top">' +
      '<span class="pulse-commerce-badge"' + i18nAttr(commerce.label) + ">" + esc(badge) +
      "</span>" + action + "</div>" +
      titleHtml + meta + storeRow + "</div></section>"
    );
  }

  /** The same card as a detached node, for the renderer that builds DOM. */
  function element(post) {
    var markup = html(post);
    if (!markup) return null;
    var template = document.createElement("template");
    template.innerHTML = markup;
    return template.content.firstElementChild;
  }

  function detailOf(node) {
    return {
      listing_id: Number(node.dataset.commerceListing || 0),
      post_id: node.dataset.commercePost || "",
      attribution_token: node.dataset.commerceToken || "",
      surface: node.dataset.commerceSurface || "",
      seller_user_id: Number(node.dataset.commerceSeller || 0)
    };
  }

  /**
   * Announce, do not post.
   *
   * There is no web commerce event endpoint yet -- the native side records these
   * through its own client. Rather than invent a route, the card emits a DOM event
   * and calls an optional global collector if one is installed, so whoever builds
   * the sink later has both the name and the payload already flowing.
   */
  function emit(name, node) {
    if (!node) return;
    var detail = detailOf(node);
    document.dispatchEvent(new CustomEvent(name, { detail: detail }));
    if (typeof window.coinPilotXTrack === "function") window.coinPilotXTrack(name, detail);
  }

  var seen = new WeakSet();
  var observer =
    "IntersectionObserver" in window
      ? new IntersectionObserver(
          function (entries) {
            entries.forEach(function (entry) {
              if (!entry.isIntersecting || seen.has(entry.target)) return;
              seen.add(entry.target);
              observer.unobserve(entry.target);
              emit("commerce_attachment_impression", entry.target);
            });
          },
          { threshold: 0.5 }
        )
      : null;

  /**
   * Translate the marked nodes in `root`.
   *
   * `pulse_i18n.js` sweeps `[data-i18n]` once, at DOMContentLoaded. Feed cards
   * arrive long after that -- on scroll, on a new post, on a profile switch --
   * so a card that is only ever swept by that pass is a card that is translated
   * exactly when it happened to be in the first page of results.
   */
  function localize(root) {
    var translate = window.PulseI18n && window.PulseI18n.t;
    if (typeof translate !== "function" || !root || !root.querySelectorAll) return;
    var marked = root.querySelectorAll("[data-i18n]");
    for (var i = 0; i < marked.length; i += 1) {
      var key = marked[i].getAttribute("data-i18n");
      // The node's own text is the server's fallback, and `t` returns it
      // unchanged when the key is absent -- so this is safe to re-run.
      if (key) marked[i].textContent = translate(key, marked[i].textContent || "");
    }
    if (root.getAttribute && root.getAttribute("data-i18n")) {
      root.textContent = translate(root.getAttribute("data-i18n"), root.textContent || "");
    }
  }

  /** Start counting impressions for any cards inside `root`. Idempotent. */
  function hydrate(root) {
    localize(root);
    if (!observer || !root) return;
    var scope = root.querySelectorAll ? root : document;
    var cards = scope.querySelectorAll(".pulse-commerce");
    for (var i = 0; i < cards.length; i += 1) {
      if (!seen.has(cards[i])) observer.observe(cards[i]);
    }
    // `querySelectorAll` does not match the root itself, and the DOM renderer
    // hands us the card's own node when it appends one post at a time.
    if (root.classList && root.classList.contains("pulse-commerce") && !seen.has(root)) {
      observer.observe(root);
    }
  }

  // Delegated once at the document, so a card added to any feed after load is
  // already wired. Does not preventDefault: these are real links, and a middle
  // click or a modifier click must keep working.
  document.addEventListener("click", function (event) {
    var target = event.target;
    var hit = target && target.closest ? target.closest("[data-commerce-click]") : null;
    if (!hit) return;
    emit(
      hit.dataset.commerceClick === "store"
        ? "commerce_attachment_store_click"
        : "commerce_attachment_product_click",
      hit
    );
  });

  window.PulseCommerceCard = { html: html, element: element, hydrate: hydrate };
})();
