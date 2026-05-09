(function () {
  function sendAnalytics(eventName, element) {
    // Ethical funnel analytics: CTA intent only. Do not track wallet data, seed phrases, private keys, or user secrets.
    var payload = {
      event_category: "coinpilotx_website",
      event_label: element && (element.textContent || "").trim(),
      link_url: element && element.href ? element.href : undefined
    };

    if (window.gtag) {
      window.gtag("event", eventName, payload);
      return;
    }

    if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
      console.log("[CoinPilotX analytics]", eventName, payload);
    }
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-analytics]");
    if (!target) {
      return;
    }

    sendAnalytics(target.getAttribute("data-analytics"), target);
  });

  function setupReveal() {
    var revealTargets = document.querySelectorAll(
      "section .section-head, section .card, .terminal-card, .pricing-card, .metric-card, .security-item, .cta-strip"
    );

    revealTargets.forEach(function (element, index) {
      element.classList.add("reveal");
      element.style.transitionDelay = Math.min(index % 6, 5) * 55 + "ms";
    });

    if (!("IntersectionObserver" in window) || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      revealTargets.forEach(function (element) {
        element.classList.add("is-visible");
        element.style.transitionDelay = "";
      });
      return;
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.14 });

    revealTargets.forEach(function (element) {
      observer.observe(element);
    });
  }

  function formatCurrency(value) {
    if (typeof value !== "number") {
      return "--";
    }
    return "$" + value.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  function formatCompactCurrency(value) {
    if (typeof value !== "number") {
      return "n/a";
    }
    if (Math.abs(value) >= 1e12) {
      return "$" + (value / 1e12).toFixed(2) + "T";
    }
    if (Math.abs(value) >= 1e9) {
      return "$" + (value / 1e9).toFixed(2) + "B";
    }
    if (Math.abs(value) >= 1e6) {
      return "$" + (value / 1e6).toFixed(2) + "M";
    }
    if (Math.abs(value) >= 1) {
      return "$" + value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }
    return "$" + value.toFixed(6);
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, function (char) {
      return {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      }[char];
    });
  }

  function formatSignedPercent(value) {
    if (typeof value !== "number") {
      return "--";
    }
    var sign = value > 0 ? "+" : "";
    return sign + value.toFixed(2) + "%";
  }

  function setWidgetTone(widget, value, reverse) {
    if (!widget || typeof value !== "number") {
      return;
    }
    widget.classList.remove("positive", "caution", "danger");
    var tone = "caution";
    if (reverse) {
      tone = value >= 68 ? "danger" : value <= 38 ? "positive" : "caution";
    } else {
      tone = value >= 62 ? "positive" : value <= 40 ? "danger" : "caution";
    }
    widget.classList.add(tone, "is-flashing");
    window.setTimeout(function () {
      widget.classList.remove("is-flashing");
    }, 420);
  }

  function animateNumber(element, nextValue) {
    if (!element || typeof nextValue !== "number") {
      if (element) {
        element.textContent = "--";
      }
      return;
    }

    var current = Number(element.dataset.current || element.textContent || 0);
    var start = Number.isFinite(current) ? current : nextValue;
    var duration = 520;
    var started = performance.now();

    function tick(now) {
      var progress = Math.min(1, (now - started) / duration);
      var eased = 1 - Math.pow(1 - progress, 3);
      var value = Math.round(start + (nextValue - start) * eased);
      element.textContent = String(value);
      if (progress < 1) {
        requestAnimationFrame(tick);
      } else {
        element.dataset.current = String(nextValue);
      }
    }

    requestAnimationFrame(tick);
  }

  function setFeedText(key, value) {
    document.querySelectorAll('[data-feed="' + key + '"]').forEach(function (element) {
      element.textContent = value;
    });
  }

  function relativeTime(isoDate) {
    var then = Date.parse(isoDate);
    if (!then) {
      return "just now";
    }
    var seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
    if (seconds < 5) {
      return "just now";
    }
    if (seconds < 60) {
      return seconds + "s ago";
    }
    return Math.round(seconds / 60) + "m ago";
  }

  async function refreshIntelligenceFeed() {
    var terminal = document.querySelector(".terminal-card[data-state]");
    var panel = document.querySelector(".assistant-panel");
    if (!terminal || !panel) {
      return;
    }

    panel.classList.add("is-updating");
    try {
      var response = await fetch("/api/intelligence-feed", { cache: "no-store" });
      if (!response.ok) {
        throw new Error("Feed request failed");
      }
      var data = await response.json();

      terminal.dataset.state = data.market_state || "updating";
      animateNumber(document.querySelector('[data-feed="signal"]'), data.signal);
      animateNumber(document.querySelector('[data-feed="risk"]'), data.risk);
      setWidgetTone(document.querySelector('[data-widget="signal"]'), data.signal, false);
      setWidgetTone(document.querySelector('[data-widget="risk"]'), data.risk, true);

      var actionWidget = document.querySelector('[data-widget="action"]');
      if (actionWidget) {
        actionWidget.classList.remove("positive", "caution", "danger");
        actionWidget.classList.add(data.risk >= 65 ? "danger" : data.signal >= 62 ? "positive" : "caution");
      }

      setFeedText("action", data.action || "UPDATING");
      setFeedText("market_state", (data.market_state || "updating").toUpperCase());
      setFeedText("btc_price", formatCurrency(data.btc_price));
      setFeedText("change_24h", formatSignedPercent(data.change_24h));
      setFeedText("trend", data.trend || "--");
      setFeedText("volatility", data.volatility || "--");
      setFeedText("whale_pressure", data.whale_pressure || "--");
      setFeedText("volume_pressure", data.volume_pressure || "--");
      setFeedText("fear_greed", data.fear_greed || "--");
      setFeedText("confidence", typeof data.confidence === "number" ? data.confidence + "%" : "--");
      setFeedText("updated_at", relativeTime(data.updated_at));
      setFeedText("message", data.message || "Educational only — not financial advice.");
    } catch (error) {
      terminal.dataset.state = "updating";
      setFeedText("market_state", "UPDATING");
      setFeedText("message", "Live intelligence is reconnecting. Educational only — not financial advice.");
    } finally {
      window.setTimeout(function () {
        panel.classList.remove("is-updating");
      }, 360);
    }
  }

  function setupIntelligenceFeed() {
    if (!document.querySelector(".terminal-card[data-state]")) {
      return;
    }
    refreshIntelligenceFeed();
    window.setInterval(refreshIntelligenceFeed, 30000);
  }

  var currentMarketFilter = "top_volume";
  var previousMarketPrices = {};

  function marketRow(item) {
    var change = typeof item.change_24h === "number" ? item.change_24h : null;
    var changeClass = change === null ? "" : change >= 0 ? "positive" : "negative";
    var changeText = change === null ? "n/a" : formatSignedPercent(change);
    var image = item.image ? '<img src="' + escapeHtml(item.image) + '" alt="" loading="lazy">' : "";
    var fallback = image ? "" : escapeHtml((item.symbol || "?").slice(0, 2));
    var priorPrice = previousMarketPrices[item.id || item.symbol];
    var updated = typeof priorPrice === "number" && typeof item.price === "number" && priorPrice !== item.price;
    previousMarketPrices[item.id || item.symbol] = item.price;

    return (
      '<div class="market-row' + (updated ? " is-updated" : "") + '">' +
        '<div class="market-asset">' +
          '<div class="coin-icon">' + image + fallback + '</div>' +
          '<div><div class="asset-name">' + escapeHtml(item.name || "Unknown") + '</div><div class="asset-symbol">' + escapeHtml(item.symbol || "") + '</div></div>' +
        '</div>' +
        '<div class="market-value" aria-label="Price">' + formatCompactCurrency(item.price) + '</div>' +
        '<div class="market-change ' + changeClass + '" aria-label="24 hour change">' + changeText + '</div>' +
        '<div class="market-value" aria-label="24 hour volume">' + formatCompactCurrency(item.volume_24h) + '</div>' +
        '<div class="market-value" aria-label="Market cap">' + formatCompactCurrency(item.market_cap) + '</div>' +
      '</div>'
    );
  }

  async function refreshMarketBoard() {
    var list = document.querySelector("[data-market-list]");
    var updated = document.querySelector("[data-market-updated]");
    var error = document.querySelector("[data-market-error]");
    if (!list || !updated) {
      return;
    }

    try {
      var response = await fetch("/api/markets?category=" + encodeURIComponent(currentMarketFilter), { cache: "no-store" });
      if (!response.ok) {
        throw new Error("Market board request failed");
      }
      var data = await response.json();
      var markets = (data.markets || []).slice(0, 12);
      if (!markets.length) {
        throw new Error("No market rows");
      }

      list.innerHTML =
        '<div class="market-row market-head"><div>Asset</div><div>Price</div><div>24h</div><div>Volume</div><div>Market Cap</div></div>' +
        markets.map(marketRow).join("");
      updated.textContent = (data.warning ? data.warning + " " : "") + "Last updated " + relativeTime(data.updated_at) + " · " + (data.source || "market data");
      if (error) {
        error.hidden = true;
      }
    } catch (err) {
      if (error) {
        error.hidden = false;
      }
      updated.textContent = "Live market data temporarily unavailable.";
    }
  }

  function setupMarketBoard() {
    if (!document.querySelector("[data-market-list]")) {
      return;
    }
    document.querySelectorAll("[data-market-filter]").forEach(function (button) {
      button.addEventListener("click", function () {
        currentMarketFilter = button.getAttribute("data-market-filter") || "top_volume";
        document.querySelectorAll("[data-market-filter]").forEach(function (item) {
          item.classList.toggle("is-active", item === button);
        });
        refreshMarketBoard();
      });
    });
    refreshMarketBoard();
    window.setInterval(refreshMarketBoard, 45000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setupReveal();
      setupIntelligenceFeed();
      setupMarketBoard();
    });
  } else {
    setupReveal();
    setupIntelligenceFeed();
    setupMarketBoard();
  }
})();
