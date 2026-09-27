/*
 * PulseSoc Marketplace — storefront progressive enhancement.
 *
 * Every behaviour in this file is an *enhancement*. The pages it runs on are
 * fully server-rendered and fully usable with JavaScript disabled:
 *
 *   - filters, sort, search and pagination are real links and a real GET form,
 *   - the gallery renders the first image in normal flow and the thumbnail rail
 *     is a list of anchors to `#mkt-slide-N`,
 *   - variant selection posts to the server, which resolves the variant and
 *     re-renders with the chosen combination in the query string.
 *
 * So this script only ever *improves* a working page: it removes full reloads
 * from gallery and variant interaction, and it turns the three image states
 * (pending / loaded / broken) into styled states.
 *
 * It deliberately does not:
 *   - compute or display any price (the server is the only price authority),
 *   - decide what is purchasable (the server resolves the variant),
 *   - fabricate any product fact.
 *
 * `window.PulseMarketplace.hydrate(root)` is idempotent and safe to call on any
 * subtree, so async-inserted markup can be hydrated the same way.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ *
   * Media states
   *
   * The stylesheet keeps an image transparent until it carries
   * `data-mkt-loaded`, which reveals the skeleton behind it. Two cases have
   * to be handled beyond a plain `load` listener:
   *
   *   1. The image may already be complete before this script runs (cache,
   *      or `loading="eager"` above the fold). `img.complete` catches that.
   *   2. `naturalWidth === 0` on a complete image means the decode failed.
   *      This is the only reliable way to detect a broken image that has
   *      already errored before we attached a handler — the `error` event is
   *      long gone by then.
   * ------------------------------------------------------------------ */

  function settleImage(img) {
    var box = img.closest(".mkt-media");
    if (img.complete && img.naturalWidth === 0) {
      if (box) box.classList.add("is-broken");
      return;
    }
    img.setAttribute("data-mkt-loaded", "1");
    if (box) box.classList.remove("is-broken");
  }

  function bindMedia(root) {
    var images = root.querySelectorAll(".mkt-media img:not([data-mkt-bound])");
    Array.prototype.forEach.call(images, function (img) {
      img.setAttribute("data-mkt-bound", "1");
      if (img.complete) {
        settleImage(img);
        return;
      }
      img.addEventListener("load", function () {
        settleImage(img);
      });
      img.addEventListener("error", function () {
        var box = img.closest(".mkt-media");
        if (box) box.classList.add("is-broken");
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Gallery
   *
   * The server emits every slide, with all but the active one `hidden`, plus
   * a rail of thumbnails. Without this script the rail's anchors jump to the
   * slide's id, which works because a slide is only `hidden` when there is
   * more than one — i.e. exactly when a rail exists.
   *
   * Keyboard model: the rail is a `tablist` of `tab` buttons over
   * `tabpanel` slides, which is the pattern assistive technology already
   * knows. Left/Right (and Home/End) move between thumbnails and change the
   * slide, matching what a sighted mouse user gets.
   * ------------------------------------------------------------------ */

  function bindGallery(gallery) {
    if (gallery.getAttribute("data-mkt-bound")) return;
    gallery.setAttribute("data-mkt-bound", "1");

    var tabs = Array.prototype.slice.call(gallery.querySelectorAll("[role='tab']"));
    var slides = Array.prototype.slice.call(gallery.querySelectorAll("[role='tabpanel']"));
    var counter = gallery.querySelector("[data-mkt-gallery-counter]");
    if (tabs.length < 2 || slides.length < 2) return;

    function show(index, moveFocus) {
      if (index < 0) index = slides.length - 1;
      if (index >= slides.length) index = 0;
      tabs.forEach(function (tab, i) {
        var on = i === index;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        /* Only the selected tab is in the tab sequence, so Tab leaves the
         * rail instead of walking every thumbnail. */
        tab.setAttribute("tabindex", on ? "0" : "-1");
        tab.classList.toggle("is-active", on);
      });
      slides.forEach(function (slide, i) {
        if (i === index) slide.removeAttribute("hidden");
        else slide.setAttribute("hidden", "");
      });
      if (counter) {
        counter.textContent = String(index + 1) + " / " + String(slides.length);
      }
      if (moveFocus && tabs[index]) tabs[index].focus();
      bindMedia(gallery);
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function (event) {
        event.preventDefault();
        show(index, false);
      });
      tab.addEventListener("keydown", function (event) {
        var key = event.key;
        if (key === "ArrowRight" || key === "ArrowDown") {
          event.preventDefault();
          show(index + 1, true);
        } else if (key === "ArrowLeft" || key === "ArrowUp") {
          event.preventDefault();
          show(index - 1, true);
        } else if (key === "Home") {
          event.preventDefault();
          show(0, true);
        } else if (key === "End") {
          event.preventDefault();
          show(slides.length - 1, true);
        }
      });
    });

    var prev = gallery.querySelector("[data-mkt-gallery-prev]");
    var next = gallery.querySelector("[data-mkt-gallery-next]");
    function current() {
      for (var i = 0; i < tabs.length; i += 1) {
        if (tabs[i].getAttribute("aria-selected") === "true") return i;
      }
      return 0;
    }
    if (prev) {
      prev.hidden = false;
      prev.addEventListener("click", function () {
        show(current() - 1, false);
      });
    }
    if (next) {
      next.hidden = false;
      next.addEventListener("click", function () {
        show(current() + 1, false);
      });
    }

    show(current(), false);
  }

  /* ------------------------------------------------------------------ *
   * Variants
   *
   * The purchasable variant is decided by the server. What the client owns is
   * the *presentation* of the choice: which combinations exist, which are out
   * of stock, and what the selected combination costs.
   *
   * `data-mkt-variants` carries a server-rendered table of real rows —
   * `{key, options:{name:value}, price, available}` — where `price` is the
   * already-formatted string the server produced from `price_cents`. The
   * client never multiplies, converts or re-formats a number; it only picks
   * which of the server's own strings to display. That is what keeps price
   * display and price authority the same value.
   * ------------------------------------------------------------------ */

  function parseVariants(panel) {
    var raw = panel.getAttribute("data-mkt-variants");
    if (!raw) return [];
    try {
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (err) {
      return [];
    }
  }

  function bindVariants(panel) {
    if (panel.getAttribute("data-mkt-bound")) return;
    panel.setAttribute("data-mkt-bound", "1");

    var variants = parseVariants(panel);
    if (!variants.length) return;

    var inputs = Array.prototype.slice.call(panel.querySelectorAll(".mkt-option input"));
    if (!inputs.length) return;

    var priceEl = panel.querySelector("[data-mkt-price]");
    var stockEl = panel.querySelector("[data-mkt-stock]");
    var noteEl = panel.querySelector("[data-mkt-variant-note]");

    /* The panel is a real GET form: without JS you pick options and press
     * "Update selection", and the server re-renders the page with that
     * variant's own price. With JS the same state is reached without a
     * round trip, so the button is redundant — hide it rather than leave a
     * control that appears to do nothing. It stays in the DOM (and stays the
     * form's submit button) so pressing Enter in the form still works. */
    var submitBtn = panel.querySelector("[data-mkt-variant-submit]");
    if (submitBtn) submitBtn.hidden = true;

    /* Nothing here touches the purchase CTA. The CTA is not variant-gated:
     * it opens a conversation with the seller, which is valid whichever
     * variant is highlighted, and its own authorization was decided
     * server-side. A client that could disable or relabel it would be
     * asserting a purchasability rule the server never made. */

    function selection() {
      var chosen = {};
      inputs.forEach(function (input) {
        if (input.checked) chosen[input.name] = input.value;
      });
      return chosen;
    }

    function matches(variant, chosen) {
      var options = variant.options || {};
      for (var name in chosen) {
        if (!Object.prototype.hasOwnProperty.call(chosen, name)) continue;
        if (options[name] !== chosen[name]) return false;
      }
      return true;
    }

    /* A combination is offered only if some real row carries it alongside
     * the rest of the current selection. Unreachable combinations are
     * disabled rather than hidden — see the stylesheet note: controls that
     * vanish as you choose are far more confusing than struck-through
     * ones. */
    function refreshAvailability(chosen) {
      inputs.forEach(function (input) {
        var probe = {};
        for (var name in chosen) {
          if (Object.prototype.hasOwnProperty.call(chosen, name) && name !== input.name) {
            probe[name] = chosen[name];
          }
        }
        probe[input.name] = input.value;
        var reachable = variants.some(function (variant) {
          return matches(variant, probe);
        });
        input.disabled = !reachable;
      });
    }

    function resolved(chosen) {
      var hit = null;
      variants.forEach(function (variant) {
        if (hit) return;
        var options = variant.options || {};
        var complete = true;
        for (var name in options) {
          if (!Object.prototype.hasOwnProperty.call(options, name)) continue;
          if (chosen[name] !== options[name]) complete = false;
        }
        if (complete && matches(variant, chosen)) hit = variant;
      });
      return hit;
    }

    function render() {
      var chosen = selection();
      refreshAvailability(chosen);
      var variant = resolved(chosen);

      if (priceEl && variant && variant.price) {
        priceEl.textContent = variant.price;
      }
      if (stockEl) {
        if (!variant) {
          /* No complete selection yet: say nothing rather than guess. */
          stockEl.textContent = "";
          stockEl.hidden = true;
          stockEl.classList.remove("is-out");
        } else {
          stockEl.hidden = false;
          stockEl.textContent = variant.stock_label || "";
          stockEl.classList.toggle("is-out", !variant.available);
          if (!variant.stock_label) stockEl.hidden = true;
        }
      }
      if (noteEl) {
        /* The note is the server's own sentence, rendered when the initial
         * selection was incomplete. Once a real variant is resolved the price
         * shown above it is that variant's exact price, so the guidance has
         * been answered and is removed. */
        noteEl.hidden = !!variant;
      }
    }

    inputs.forEach(function (input) {
      input.addEventListener("change", render);
    });
    render();
  }

  /* ------------------------------------------------------------------ *
   * Sort
   *
   * The sort control is a `<select>` inside a real GET form with a visible
   * submit button; changing it here just submits that form, so the resulting
   * URL is identical to the no-JS one and stays shareable.
   * ------------------------------------------------------------------ */

  function bindSort(root) {
    var selects = root.querySelectorAll("[data-mkt-sort]:not([data-mkt-bound])");
    Array.prototype.forEach.call(selects, function (select) {
      select.setAttribute("data-mkt-bound", "1");
      var form = select.form;
      if (!form) return;
      var submit = form.querySelector("[data-mkt-sort-submit]");
      if (submit) submit.hidden = true;
      select.addEventListener("change", function () {
        form.submit();
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Buyer actions — Save and Report
   *
   * Both reuse the existing endpoints unchanged
   * (`/api/pulse/marketplace/listings/save` and `.../report`). The only
   * addition is optimistic `aria-pressed` on Save and use of the shell's own
   * `toast()` when it is present.
   * ------------------------------------------------------------------ */

  function notify(message) {
    if (typeof window.toast === "function") {
      window.toast(message);
      return;
    }
    var live = document.getElementById("mkt-live");
    if (live) live.textContent = message;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return { ok: false, message: "Server returned an unreadable response." };
        })
        .then(function (data) {
          if (!response.ok || data.ok === false) {
            /* `pulseApi` elsewhere in the product reads `error_code`, not
             * `code`. Nothing here depends on either, but the message
             * precedence matches so error copy is consistent. */
            throw new Error(data.message || data.error || "Request failed.");
          }
          return data;
        });
    });
  }

  function bindActions(root) {
    var buttons = root.querySelectorAll("[data-mkt-save]:not([data-mkt-bound])");
    Array.prototype.forEach.call(buttons, function (button) {
      button.setAttribute("data-mkt-bound", "1");
      /* The server renders Save and Report hidden: they are fetch-only, with
       * no form behind them, so a scriptless visitor must not be shown a
       * control that cannot work. Binding is what earns them a place on the
       * page. */
      button.hidden = false;
      button.addEventListener("click", function (event) {
        event.preventDefault();
        var id = button.getAttribute("data-mkt-save");
        button.disabled = true;
        post("/api/pulse/marketplace/listings/save", { listing_id: id })
          .then(function () {
            button.setAttribute("aria-pressed", "true");
            notify("Saved to your list.");
          })
          .catch(function (err) {
            notify(err.message);
          })
          .then(function () {
            button.disabled = false;
          });
      });
    });

    var reports = root.querySelectorAll("[data-mkt-report]:not([data-mkt-bound])");
    Array.prototype.forEach.call(reports, function (button) {
      button.setAttribute("data-mkt-bound", "1");
      button.hidden = false;
      button.addEventListener("click", function (event) {
        event.preventDefault();
        var id = button.getAttribute("data-mkt-report");
        var reason = window.prompt("What is wrong with this listing?", "");
        /* A cancelled prompt is not an empty report. */
        if (reason === null) return;
        reason = String(reason).trim();
        if (!reason) {
          notify("A report needs a reason.");
          return;
        }
        button.disabled = true;
        post("/api/pulse/marketplace/listings/report", {
          listing_id: id,
          reason: reason,
        })
          .then(function () {
            notify("Reported. Our moderation team will review it.");
          })
          .catch(function (err) {
            notify(err.message);
          })
          .then(function () {
            button.disabled = false;
          });
      });
    });

    /* "Message seller" is normally an anchor to `/pulse/messages/new?q=<seller>`
     * so it works without JavaScript. Here we upgrade it: one POST to
     * `/api/pulse/messages/start` opens the conversation itself, skipping the
     * people-search step. If that POST fails we let the anchor's own href take
     * over rather than leaving the shopper with an error and no way through.
     *
     * When the seller has no username there is no page to link to, so the
     * server renders a hidden <button> instead; binding is what reveals it. */
    var contacts = root.querySelectorAll("[data-mkt-contact]:not([data-mkt-bound])");
    Array.prototype.forEach.call(contacts, function (button) {
      button.setAttribute("data-mkt-bound", "1");
      button.hidden = false;
      var fallbackHref = button.getAttribute("href") || "";
      button.addEventListener("click", function (event) {
        event.preventDefault();
        button.setAttribute("aria-disabled", "true");
        if ("disabled" in button) button.disabled = true;
        post("/api/pulse/messages/start", {
          user_id: button.getAttribute("data-mkt-contact"),
        })
          .then(function (data) {
            if (data && data.next_url) window.location.href = data.next_url;
            else if (fallbackHref) window.location.href = fallbackHref;
            else notify("Conversation ready.");
          })
          .catch(function (err) {
            button.setAttribute("aria-disabled", "false");
            if ("disabled" in button) button.disabled = false;
            if (fallbackHref) {
              window.location.href = fallbackHref;
              return;
            }
            notify(err.message);
          });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Add to cart
   *
   * `POST /api/pulse/marketplace/cart` is the same endpoint the native app
   * has always added through. Nothing new is added server-side and no total
   * is computed here: the *server's* `badge_count` is what the header shows,
   * so the number on this page is the number the cart page will print.
   *
   * Deliberately not a local increment. A `+1` on click is right until it
   * is not -- the route refuses `OWN_LISTING`, `OUT_OF_STOCK`,
   * `SELLER_UNAVAILABLE`, `ITEM_UNAVAILABLE` and `CART_FULL`, and it caps
   * quantity against real inventory, so a client that counted its own
   * clicks would drift from the cart on the first refusal and stay wrong
   * until a reload. The response already carries the true count; reading it
   * costs nothing and cannot disagree.
   * ------------------------------------------------------------------ */

  /* One source, called only with a number the server sent. `undefined` is a
   * response that did not carry a count, and leaves the badge untouched
   * rather than blanking it to zero. */
  function setCartCount(value) {
    if (value === null || value === undefined) return;
    var count = parseInt(value, 10);
    if (isNaN(count) || count < 0) return;
    var pills = document.querySelectorAll("[data-mkt-cart-count]");
    Array.prototype.forEach.call(pills, function (pill) {
      pill.textContent = String(count);
      pill.hidden = count === 0;
    });
    /* The accessible name is rewritten with it. Leaving the old one behind
     * is the failure mode where a screen reader announces "Your cart, empty"
     * over a cart holding three things. */
    var links = document.querySelectorAll("[data-mkt-cart-link]");
    Array.prototype.forEach.call(links, function (link) {
      link.setAttribute(
        "aria-label",
        count === 0
          ? "Your cart, empty"
          : count === 1
          ? "Your cart, 1 item"
          : "Your cart, " + count + " items"
      );
    });
  }

  function bindAddToCart(root) {
    var buttons = root.querySelectorAll("[data-mkt-add]:not([data-mkt-bound])");
    Array.prototype.forEach.call(buttons, function (button) {
      button.setAttribute("data-mkt-bound", "1");
      /* Hidden by the server until now, same as Save and Report above: this
       * is fetch-only, and the whole card is already a link to the product
       * page where the purchase is reachable without JavaScript. */
      button.hidden = false;
      var label = button.textContent;
      button.addEventListener("click", function (event) {
        /* The card is one big link and this button sits inside it. The CSS
         * raise makes the button the click *target*; this stops the event
         * reaching the card, which would otherwise navigate away from the
         * page mid-request. Both halves are needed. */
        event.preventDefault();
        event.stopPropagation();
        button.disabled = true;
        button.textContent = "Adding…";
        post("/api/pulse/marketplace/cart", {
          listing_id: button.getAttribute("data-mkt-add"),
          qty: 1,
        })
          .then(function (data) {
            setCartCount(data && data.badge_count);
            button.setAttribute("data-mkt-added", "1");
            button.textContent = "In cart";
            notify("Added to your cart.");
          })
          .catch(function (err) {
            /* The button comes back rather than staying spent: every one of
             * the route's refusals is a thing the buyer might fix (sign in
             * elsewhere, free up a full cart), and a dead control gives them
             * nothing to retry. */
            button.textContent = label;
            button.disabled = false;
            notify(err.message);
          });
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Long-description disclosure
   * ------------------------------------------------------------------ */

  function bindClamp(root) {
    var toggles = root.querySelectorAll("[data-mkt-clamp-toggle]:not([data-mkt-bound])");
    Array.prototype.forEach.call(toggles, function (button) {
      button.setAttribute("data-mkt-bound", "1");
      var target = document.getElementById(button.getAttribute("data-mkt-clamp-toggle"));
      if (!target) return;
      /* Only worth a control if the text is actually clipped. */
      if (target.scrollHeight <= target.clientHeight + 2) {
        button.hidden = true;
        target.classList.remove("mkt-clamp");
        return;
      }
      button.hidden = false;
      button.addEventListener("click", function () {
        var open = target.classList.toggle("mkt-clamp") === false;
        button.setAttribute("aria-expanded", open ? "true" : "false");
        button.textContent = open ? "Show less" : "Show more";
      });
    });
  }

  function hydrate(root) {
    var scope = root || document;
    bindMedia(scope);
    bindSort(scope);
    bindActions(scope);
    bindAddToCart(scope);
    bindClamp(scope);
    Array.prototype.forEach.call(scope.querySelectorAll("[data-mkt-gallery]"), bindGallery);
    Array.prototype.forEach.call(scope.querySelectorAll("[data-mkt-variants]"), bindVariants);
  }

  window.PulseMarketplace = { hydrate: hydrate };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      hydrate(document);
    });
  } else {
    hydrate(document);
  }
})();
