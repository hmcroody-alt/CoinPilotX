/*
 * The web Marketplace cart.
 *
 * Consumes services/marketplace_cart_routes.py unchanged. No endpoint here is
 * new and none is web-specific: the native app calls the same six. The cart
 * itself is a server-side row set (marketplace_cart_items keyed on the buyer),
 * so this file is a second view of the app's cart rather than a second cart.
 *
 * Three rules this file exists to keep, all of them learned the hard way
 * elsewhere in this codebase:
 *
 * 1. Error and empty never co-render. `show()` is the only thing that toggles
 *    the four panels, and it takes exactly one of them. A failed fetch is not
 *    an empty cart, and a cart page that shows "nothing here yet" after a 500
 *    tells the buyer their items are gone.
 *
 * 2. The state words are the server's. `_line_state()` returns available,
 *    price_changed, low_stock, sold, restricted or removed, and each is printed
 *    as itself. Flattening them to in-stock/out-of-stock would drop
 *    price_changed, which is the one a buyer has to see before paying.
 *
 * 3. Every mutation re-reads. PATCH and DELETE return only the line they
 *    touched, and a quantity change can move a line from available to
 *    low_stock, so the list is refetched rather than patched in place. Local
 *    edits would drift from the states above the moment stock moved.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-cart-root]");
  if (!root) return;

  var els = {
    needsjs: root.querySelector("[data-cart-needsjs]"),
    fail: root.querySelector("[data-cart-fail]"),
    lines: root.querySelector("[data-cart-lines]"),
    empty: root.querySelector("[data-cart-empty]"),
    summary: root.querySelector("[data-cart-summary]")
  };

  // The one place panel visibility is decided, so two of them cannot be shown
  // at once by a later edit that only remembered to turn one on.
  function show(which) {
    els.needsjs.hidden = true;
    els.fail.hidden = which !== "fail";
    els.lines.hidden = which !== "lines";
    els.empty.hidden = which !== "empty";
    els.summary.hidden = which !== "lines";
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Minor units to a readable amount. Intl decides the fraction digits from the
  // currency rather than assuming two, because the API returns whatever the
  // listing is priced in and a hardcoded /100 is wrong for JPY.
  function money(minor, currency) {
    var code = (currency || "USD").toUpperCase();
    try {
      var fmt = new Intl.NumberFormat(undefined, { style: "currency", currency: code });
      var digits = fmt.resolvedOptions().maximumFractionDigits;
      return fmt.format((minor || 0) / Math.pow(10, digits));
    } catch (err) {
      return ((minor || 0) / 100).toFixed(2) + " " + code;
    }
  }

  // Server state -> what the buyer reads, and whether the line can be paid for.
  // `buys` is not derived from a "not one of the bad ones" test: a state this
  // file has never heard of must not be assumed purchasable.
  var STATES = {
    available:     { word: "Ready",         tone: "ok",   buys: true },
    price_changed: { word: "Price changed", tone: "attn", buys: false },
    low_stock:     { word: "Low stock",     tone: "attn", buys: true },
    sold:          { word: "Sold out",      tone: "gone", buys: false },
    restricted:    { word: "Unavailable",   tone: "gone", buys: false },
    removed:       { word: "Removed",       tone: "gone", buys: false }
  };

  function stateOf(name) {
    return STATES[name] || { word: String(name || "Unavailable"), tone: "gone", buys: false };
  }

  function lineHtml(line) {
    var st = stateOf(line.state);
    var cover = line.cover_image_url
      ? "<img src='" + esc(line.cover_image_url) + "' alt='' loading='lazy'>"
      : "<img alt=''>";
    var moved = line.state === "price_changed";
    var price = moved ? line.price_now_minor : line.price_snapshot_minor;
    var was = moved
      ? "<p class='store'>Was " + esc(money(line.price_snapshot_minor, line.currency)) + "</p>"
      : "";
    // The quantity field and Remove are offered on every line, including the
    // ones that cannot be bought. A buyer looking at a sold-out line wants to
    // remove it, and a page that disables its controls when a line goes bad
    // leaves them with a cart they cannot clear.
    var confirm = moved
      ? "<button class='confirm' data-confirm-price='" + esc(line.line_id) + "'>Accept new price</button>"
      : "";
    return "" +
      "<div class='line " + esc(st.tone) + "' data-line='" + esc(line.line_id) + "'>" +
        cover +
        "<div>" +
          "<h3><a href='/pulse/marketplace/" + esc(line.listing_id) + "'>" + esc(line.title) + "</a></h3>" +
          "<p class='store'>" + esc(line.seller_store_name) + "</p>" +
          was +
          "<span class='state'>" + esc(st.word) + "</span>" +
        "</div>" +
        "<div class='money'>" +
          "<b>" + esc(money(price * (line.qty || 1), line.currency)) + "</b>" +
          "<div class='controls'>" +
            confirm +
            "<input type='number' min='1' max='99' value='" + esc(line.qty) + "' " +
              "aria-label='Quantity' data-qty='" + esc(line.line_id) + "'>" +
            "<button data-remove='" + esc(line.line_id) + "'>Remove</button>" +
          "</div>" +
        "</div>" +
      "</div>";
  }

  // The checkout block. This is where the web is honest about what it cannot do
  // yet: the buyer can build a cart here, and paying happens in the app. The
  // wording is not "web checkout coming soon" -- it names the working path,
  // because the cart genuinely is the same cart and opening the app genuinely
  // finishes the job.
  function summaryHtml(lines, options) {
    var payable = lines.filter(function (l) { return stateOf(l.state).buys; });
    var currency = (payable[0] || lines[0] || {}).currency || "USD";
    var subtotal = payable.reduce(function (sum, l) {
      var each = l.state === "price_changed" ? l.price_now_minor : l.price_snapshot_minor;
      return sum + each * (l.qty || 1);
    }, 0);
    var mixed = payable.some(function (l) { return (l.currency || "USD") !== currency; });
    var blocked = lines.length - payable.length;

    var rows =
      "<div class='row'><span>Items ready to pay for</span><span>" + payable.length + "</span></div>" +
      (blocked
        ? "<div class='row'><span>Items needing attention</span><span>" + blocked + "</span></div>"
        : "");

    // A subtotal across two currencies is not a number. Rather than print a
    // wrong one, say why there isn't one.
    var total = mixed
      ? "<div class='row grand'><span>Total</span><span>Shown per item &mdash; your cart mixes currencies</span></div>"
      : "<div class='row grand'><span>Subtotal</span><b>" + esc(money(subtotal, currency)) + "</b></div>";

    // The server's own verdict on whether the card rail is open, rendered as it
    // was given. When it is closed the API supplies the sentence to print, so
    // this page cannot disagree with the app about why.
    var closed = options && options.card_payments_available === false;
    var note = closed
      ? esc(options.payment_unavailable_message || "Card payments are unavailable right now.")
      : "Paying happens in the PulseSoc app. Your cart is already there &mdash; " +
        "open it and your items are waiting.";

    // Injected by the template from services/app_links.py rather than written
    // here, so the app registry stays the only thing that decides what a
    // PulseSoc destination's link looks like.
    var appHref = root.getAttribute("data-cart-app-href") || "";
    var cta = appHref
      ? "<a class='button primary' href='" + esc(appHref) + "'>Open cart in the app</a>"
      : "";

    return "<div class='totals'>" + rows + total +
      "<div class='pay'>" + cta +
        "<p class='note'>" + note + "</p>" +
      "</div></div>";
  }

  function render(data, options) {
    var lines = (data && data.lines) || [];
    if (!lines.length) { show("empty"); return; }
    els.lines.innerHTML = lines.map(lineHtml).join("");
    els.summary.innerHTML = summaryHtml(lines, options);
    show("lines");
  }

  function fail(message) {
    els.fail.innerHTML = "<div class='fail'><p>" + esc(message) +
      "</p><p><button data-cart-retry>Try again</button></p></div>";
    show("fail");
  }

  function load() {
    // checkout-options is fetched alongside the lines rather than lazily,
    // because its answer changes the wording of the pay block; fetching it
    // after render would flash the wrong sentence. Its failure is survivable --
    // the cart is still worth showing without it -- so it resolves to null
    // rather than rejecting the pair.
    var opts = pulseApi("/api/pulse/marketplace/cart/checkout-options")
      .catch(function () { return null; });
    return Promise.all([pulseApi("/api/pulse/marketplace/cart"), opts])
      .then(function (pair) { render(pair[0], pair[1]); })
      .catch(function (err) {
        fail((err && err.message) || "Your cart could not be loaded.");
      });
  }

  root.addEventListener("click", function (event) {
    var retry = event.target.closest("[data-cart-retry]");
    if (retry) { els.needsjs.hidden = false; load(); return; }

    var remove = event.target.closest("[data-remove]");
    if (remove) {
      remove.disabled = true;
      pulseApi("/api/pulse/marketplace/cart/" + remove.dataset.remove, { method: "DELETE" })
        .then(load)
        .catch(function (err) { remove.disabled = false; toast(err.message); });
      return;
    }

    var accept = event.target.closest("[data-confirm-price]");
    if (accept) {
      accept.disabled = true;
      pulseApi("/api/pulse/marketplace/cart/" + accept.dataset.confirmPrice + "/confirm-price",
               { method: "POST", body: "{}" })
        .then(load)
        .catch(function (err) { accept.disabled = false; toast(err.message); });
    }
  });

  // `change` rather than `input`: typing "12" fires input twice and would send a
  // PATCH for the intermediate 1.
  root.addEventListener("change", function (event) {
    var field = event.target.closest("[data-qty]");
    if (!field) return;
    var qty = Math.max(1, Math.min(99, parseInt(field.value, 10) || 1));
    field.value = qty;
    pulseApi("/api/pulse/marketplace/cart/" + field.dataset.qty,
             { method: "PATCH", body: JSON.stringify({ qty: qty }) })
      .then(load)
      .catch(function (err) { toast(err.message); load(); });
  });

  load();
})();
