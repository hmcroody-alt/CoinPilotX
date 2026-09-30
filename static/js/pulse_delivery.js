/*
 * Fills in a delivery estimate the server could not put in the HTML.
 *
 * The server renders every delivery line from the cache only (see
 * services/delivery/web.py). A warm corridor is already a real window in the
 * document and this script does nothing at all for it. A cold one, or a page a
 * proxy may share between visitors, arrives with data-pending="1" and one
 * sentence — "Checking delivery options…" — and this asks the same endpoint the
 * iOS app asks and writes the answer into the same element.
 *
 * Why this is 60 lines of plain DOM and not a component:
 *   - It runs on two unrelated pages: a Jinja template with no JS framework and
 *     a page built as an f-string in bot.py. Neither has a render tree to join.
 *   - It is on the critical path of the marketplace product page. The whole
 *     point of rendering from cache was to protect first paint, and shipping a
 *     framework to fill in one sentence would spend more than the fetch saves.
 *
 * Copy: every sentence comes from the server. This file has no delivery
 * vocabulary of its own — not even a fallback string — because there are already
 * exactly two copies of it (services/delivery/copy.py and the app's
 * deliveryCopy.ts) held together by a contract test, and a third one here would
 * be outside it. On a failed fetch the pending line is left exactly as it is:
 * that sentence is already true, and "could not load" would replace something
 * honest with something alarming.
 */
(function () {
  "use strict";

  var ROOT = "[data-delivery-estimate]";
  var inflight = new WeakMap();

  function render(root, line) {
    var text = root.querySelector(".pulse-delivery__text");
    if (text && typeof line.text === "string" && line.text) text.textContent = line.text;

    var shipping = root.querySelector(".pulse-delivery__shipping");
    if (shipping) {
      // Removed rather than emptied when the answer carries none. An empty span
      // holding a margin is a gap under the price that looks like a bug.
      if (line.shipping) shipping.textContent = line.shipping;
      else shipping.remove();
    } else if (line.shipping) {
      shipping = document.createElement("span");
      shipping.className = "pulse-delivery__shipping";
      shipping.textContent = line.shipping;
      root.appendChild(shipping);
    }

    root.dataset.pending = "0";
    root.classList.remove("pulse-delivery--pending");
    // The tone class is what any styling hangs off, and the server's own class
    // is now stale — a settled refusal must not keep rendering as a live
    // estimate. Cleared by prefix so this does not need the server's list.
    Array.prototype.slice.call(root.classList).forEach(function (name) {
      if (name.indexOf("pulse-delivery--") === 0) root.classList.remove(name);
    });
    if (line.tone) root.classList.add("pulse-delivery--" + String(line.tone).toLowerCase());
    if (line.stale) root.classList.add("pulse-delivery--stale");
    // Nothing further is expected, so stop announcing. Left in place while
    // pending so the fill-in is read out once, which is the one moment it is
    // worth interrupting for.
    root.removeAttribute("aria-live");
  }

  function ask(root, country) {
    var ref = root.dataset.variantRef;
    var endpoint = root.dataset.endpoint;
    if (!ref || !endpoint || inflight.get(root)) return;

    var body = { variant_ref: ref, quantity: Number(root.dataset.quantity || 1) || 1 };
    // Only when the buyer picked one. Sending the server's own resolved country
    // back to it would promote a policy- or edge-derived guess to `STATED`,
    // which is the tier that means "the buyer said so" — and the estimate would
    // then claim "to United States" on the strength of our own assumption.
    if (country) body.country = country;

    inflight.set(root, true);
    fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // The endpoint reads the session cookie when there is one, for the
      // last-order destination tier. Anonymous is the normal case and works.
      credentials: "same-origin",
      body: JSON.stringify(body)
    })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (payload) {
        if (payload && payload.ok && payload.line) render(root, payload.line);
      })
      .catch(function () { /* the pending sentence stays; see the header */ })
      .then(function () { inflight.set(root, false); });
  }

  function start() {
    var roots = document.querySelectorAll(ROOT);
    Array.prototype.forEach.call(roots, function (root) {
      if (root.dataset.pending === "1") ask(root, root.dataset.country || "");

      var select = root.querySelector("[data-delivery-country]");
      if (!select) return;
      select.addEventListener("change", function () {
        var text = root.querySelector(".pulse-delivery__text");
        // The previous window is for a different country and must come down
        // before the new one is asked for, or the page shows a date that is no
        // longer about the selected destination for as long as the fetch takes.
        if (text && root.dataset.loadingText) text.textContent = root.dataset.loadingText;
        root.dataset.pending = "1";
        root.classList.add("pulse-delivery--pending");
        ask(root, select.value);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
