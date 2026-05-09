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
    }

    if (window.posthog && window.posthog.capture) {
      window.posthog.capture(eventName, payload);
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

  function setupMobileNav() {
    var toggle = document.querySelector("[data-nav-toggle]");
    var header = document.querySelector("header");
    if (!toggle || !header) {
      return;
    }
    toggle.addEventListener("click", function () {
      var open = header.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
    });
    document.querySelectorAll("[data-nav-panel] a, .nav-actions a").forEach(function (link) {
      link.addEventListener("click", function () {
        header.classList.remove("nav-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Open navigation menu");
      });
    });
  }

  function renderPlatformStatus(data) {
    var panel = document.querySelector("[data-platform-status]");
    if (!panel) {
      return;
    }
    var rows = [
      ["Market data", data.market_data + " · " + data.market_source],
      ["Sports Edge", data.sports_edge + " · " + data.sports_source],
      ["Odds", data.odds_status || "unavailable"],
      ["AI Assistant", data.ai_assistant],
      ["Wallet Intel", data.wallet_intelligence],
      ["Scam Shield", data.scam_shield]
    ];
    panel.innerHTML = rows.map(function (row) {
      return '<div class="status-item"><strong>' + escapeHtml(row[0]) + '</strong><span>' + escapeHtml(row[1]) + '</span></div>';
    }).join("");
  }

  async function refreshPlatformStatus() {
    if (!document.querySelector("[data-platform-status]")) {
      return;
    }
    try {
      var response = await fetch("/api/platform-status", { cache: "no-store" });
      if (!response.ok) {
        throw new Error("status failed");
      }
      renderPlatformStatus(await response.json());
    } catch (err) {
      renderPlatformStatus({
        market_data: "degraded",
        market_source: "checking",
        sports_edge: "standby",
        sports_source: "checking",
        odds_status: "unavailable",
        ai_assistant: "checking",
        wallet_intelligence: "public BTC explorer",
        scam_shield: "rules active"
      });
    }
  }

  function setupIntelligenceConsole() {
    var output = document.querySelector("[data-tool-output]");
    if (!output) {
      return;
    }
    document.querySelectorAll("[data-tool-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        var tool = button.getAttribute("data-tool-tab");
        document.querySelectorAll("[data-tool-tab]").forEach(function (item) {
          item.classList.toggle("is-active", item === button);
        });
        document.querySelectorAll("[data-tool-form]").forEach(function (form) {
          form.classList.toggle("is-active", form.getAttribute("data-tool-form") === tool);
        });
      });
    });

    function setOutput(text) {
      output.textContent = text || "No response returned.";
    }

    async function postJson(url, payload) {
      var response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      var data = await response.json();
      if (!response.ok) {
        throw new Error(data.response || "Request failed");
      }
      return data;
    }

    var aiForm = document.querySelector('[data-tool-form="ai"]');
    var scamForm = document.querySelector('[data-tool-form="scam"]');
    var walletForm = document.querySelector('[data-tool-form="wallet"]');

    if (aiForm) {
      aiForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        setOutput("CoinPilotX AI is thinking...");
        try {
          var data = await postJson("/api/ai-assistant", { question: aiForm.elements.question.value });
          setOutput(data.response);
        } catch (err) {
          setOutput(err.message);
        }
      });
    }

    if (scamForm) {
      scamForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        setOutput("Scanning for scam patterns...");
        try {
          var data = await postJson("/api/scam-shield", { text: scamForm.elements.text.value });
          setOutput(data.response);
        } catch (err) {
          setOutput(err.message);
        }
      });
    }

    if (walletForm) {
      walletForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        setOutput("Checking public BTC wallet data...");
        try {
          var address = encodeURIComponent(walletForm.elements.address.value || "");
          var response = await fetch("/api/wallet-intel?address=" + address, { cache: "no-store" });
          var data = await response.json();
          if (!response.ok) {
            throw new Error(data.response || "Wallet check failed");
          }
          setOutput(data.response);
        } catch (err) {
          setOutput(err.message);
        }
      });
    }

    refreshPlatformStatus();
    window.setInterval(refreshPlatformStatus, 60000);
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
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (char) {
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

  function renderMarketSummary(summary) {
    var node = document.querySelector("[data-market-summary]");
    if (!node || !summary) {
      return;
    }
    var dominance = typeof summary.btc_dominance_proxy === "number" ? summary.btc_dominance_proxy.toFixed(1) + "%" : "n/a";
    var trending = (summary.trending_narratives || []).join(", ") || "Loading";
    var risk = (summary.risk_pockets || []).join(", ") || "Loading";
    node.innerHTML =
      '<div class="market-summary-card"><span>BTC dominance proxy</span><strong>' + escapeHtml(dominance) + '</strong></div>' +
      '<div class="market-summary-card"><span>Trending narratives</span><strong>' + escapeHtml(trending) + '</strong></div>' +
      '<div class="market-summary-card"><span>Risk pockets</span><strong>' + escapeHtml(risk) + '</strong></div>';
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
      renderMarketSummary(data.summary);
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

  var currentSportsFilter = "all";
  var selectedSportsGame = "";

  function sportsCard(game) {
    var scoreAway = game.state === "pre" ? "--" : game.away_score;
    var scoreHome = game.state === "pre" ? "--" : game.home_score;
    var active = selectedSportsGame === game.id ? " is-active" : "";
    var sportClass = " sport-" + escapeHtml(game.league || "live");
    var odds = game.odds_available ? "Odds: available" : "Odds: unavailable";
    var risk = game.risk_label || "Review";
    return (
      '<button class="sports-card' + sportClass + active + '" type="button" data-game-id="' + escapeHtml(game.id) + '">' +
        '<div class="sports-meta"><span>' + escapeHtml(game.league_label || game.league || "Live") + '</span><span>' + escapeHtml(game.status || "Scheduled") + '</span></div>' +
        '<div class="sports-teams">' +
          '<div class="sports-team-line"><span>' + escapeHtml(game.away_team || "Away") + '</span><span class="sports-score">' + escapeHtml(scoreAway) + '</span></div>' +
          '<div class="sports-team-line"><span>' + escapeHtml(game.home_team || "Home") + '</span><span class="sports-score">' + escapeHtml(scoreHome) + '</span></div>' +
        '</div>' +
        '<div class="sports-status">' + escapeHtml(odds) + ' · Tap for position intelligence.</div>' +
        '<span class="sports-risk">Risk: ' + escapeHtml(risk) + '</span>' +
      '</button>'
    );
  }

  function telegramSportsPrompt(game) {
    return (
      "Open CoinPilotX Bot and ask:\\n\\n" +
      "Analyze this Sports Edge game:\\n" +
      "League: " + (game.league_label || game.league || "Live") + "\\n" +
      "Game: " + (game.away_team || "Away") + " at " + (game.home_team || "Home") + "\\n" +
      "Start/status: " + (game.status || game.start_time || "Unknown") + "\\n" +
      "Give me position intelligence, risk context, market pressure, and why I should wait or avoid forcing a position.\\n" +
      "Informational only — not betting or financial advice."
    );
  }

  function getSportsDetailPanel() {
    var detail = document.querySelector("[data-sports-detail]");
    if (detail) {
      return detail;
    }

    detail = document.createElement("div");
    detail.className = "sports-detail";
    detail.setAttribute("data-sports-detail", "");
    detail.hidden = true;
    detail.innerHTML = '<div class="market-loading">Select a game to review position intelligence.</div>';

    var board = document.querySelector(".sports-board");
    var error = document.querySelector("[data-sports-error]");
    if (board && error) {
      board.insertBefore(detail, error);
    }
    return detail;
  }

  function findSportsCardById(gameId) {
    var cards = document.querySelectorAll("[data-game-id]");
    for (var index = 0; index < cards.length; index += 1) {
      if (cards[index].getAttribute("data-game-id") === gameId) {
        return cards[index];
      }
    }
    return null;
  }

  function positionSportsDetailPanel(detail, gameId) {
    var card = findSportsCardById(gameId);
    var list = document.querySelector("[data-sports-list]");
    if (card && card.parentNode) {
      card.parentNode.insertBefore(detail, card.nextSibling);
      return;
    }
    if (list) {
      list.appendChild(detail);
    }
  }

  function renderSportsDetail(data) {
    var detail = getSportsDetailPanel();
    if (!detail) {
      return;
    }
    var game = data.selected_game;
    var analysis = data.analysis;
    if (!game || !analysis) {
      detail.hidden = true;
      detail.removeAttribute("data-sport-detail");
      detail.innerHTML = '<div class="market-loading">Select a game to review position intelligence.</div>';
      return;
    }

    detail.hidden = false;
    detail.setAttribute("data-sport-detail", game.league || "live");
    positionSportsDetailPanel(detail, game.id);
    detail.innerHTML =
      '<div class="sports-detail-grid">' +
        '<div>' +
          '<span class="sports-action">' + escapeHtml(analysis.action || "REVIEW RISK") + '</span>' +
          '<h3>' + escapeHtml((game.away_team || "Away") + " at " + (game.home_team || "Home")) + '</h3>' +
          '<p>' + escapeHtml(game.league_label || "") + " · " + escapeHtml(game.status || "") + '</p>' +
          '<p><strong>Risk:</strong> ' + escapeHtml(analysis.risk_label || game.risk_label || "Review carefully.") + '</p>' +
          '<p><strong>Odds:</strong> ' + escapeHtml(game.odds_status || "unavailable") + '</p>' +
        '</div>' +
        '<div>' +
          '<p><strong>Matchup Summary:</strong> ' + escapeHtml(analysis.matchup_summary || "") + '</p>' +
          '<p><strong>Current Game State:</strong> ' + escapeHtml(analysis.current_game_state || "") + '</p>' +
          '<p><strong>Market / Odds Context:</strong> ' + escapeHtml(analysis.market_odds_context || analysis.market_context || "") + '</p>' +
          '<p><strong>Momentum Read:</strong> ' + escapeHtml(analysis.momentum_read || "") + '</p>' +
          '<p><strong>Why Avoid Forcing It:</strong> ' + escapeHtml(analysis.why_avoid || "") + '</p>' +
          '<p><strong>What Could Change:</strong> ' + escapeHtml(analysis.what_could_change || "") + '</p>' +
          '<p>' + escapeHtml(analysis.disclaimer || "Informational only — not betting or financial advice. Never risk money you cannot afford to lose.") + '</p>' +
          '<div class="sports-ai-prompt">' + escapeHtml(telegramSportsPrompt(game)) + '</div>' +
          '<div class="sports-telegram-action"><a class="button gold" href="https://t.me/DocShieldX_bot" data-analytics="try_sports_ai">Open Telegram Bot</a></div>' +
        '</div>' +
      '</div>';
  }

  async function refreshSportsEdge(gameId) {
    var list = document.querySelector("[data-sports-list]");
    var updated = document.querySelector("[data-sports-updated]");
    var error = document.querySelector("[data-sports-error]");
    var source = document.querySelector("[data-sports-source]");
    var odds = document.querySelector("[data-sports-odds]");
    if (!list || !updated) {
      return;
    }

    var params = "?league=" + encodeURIComponent(currentSportsFilter);
    if (gameId) {
      params += "&game_id=" + encodeURIComponent(gameId);
    }

    try {
      var response = await fetch("/api/sports-edge" + params, { cache: "no-store" });
      if (!response.ok) {
        throw new Error("Sports Edge request failed");
      }
      var data = await response.json();
      var games = (data.games || []).slice(0, 12);
      if (!games.length) {
        list.innerHTML = '<div class="market-loading">No live games are available from the public feed right now.</div>';
      } else {
        list.innerHTML = games.map(sportsCard).join("");
      }
      updated.textContent = (data.warning ? data.warning + " " : "") + "Last updated " + relativeTime(data.updated_at) + " · " + (data.source || "sports data");
      if (source) {
        source.textContent = "Source: " + (data.source || "sports data");
      }
      if (odds) {
        odds.textContent = "Odds: " + (data.odds_status || "unavailable");
      }
      renderSportsDetail(data);
      if (error) {
        error.hidden = true;
      }
    } catch (err) {
      if (error) {
        error.hidden = false;
      }
      updated.textContent = "Live sports data temporarily unavailable.";
    }
  }

  function setupSportsEdge() {
    if (!document.querySelector("[data-sports-list]")) {
      return;
    }
    document.querySelectorAll("[data-sports-filter]").forEach(function (button) {
      button.addEventListener("click", function () {
        currentSportsFilter = button.getAttribute("data-sports-filter") || "all";
        selectedSportsGame = "";
        document.querySelectorAll("[data-sports-filter]").forEach(function (item) {
          item.classList.toggle("is-active", item === button);
        });
        refreshSportsEdge();
      });
    });

    document.addEventListener("click", function (event) {
      var card = event.target.closest("[data-game-id]");
      if (!card) {
        return;
      }
      selectedSportsGame = card.getAttribute("data-game-id") || "";
      document.querySelectorAll("[data-game-id]").forEach(function (item) {
        item.classList.toggle("is-active", item === card);
      });
      refreshSportsEdge(selectedSportsGame);
    });

    refreshSportsEdge();
    window.setInterval(function () {
      refreshSportsEdge(selectedSportsGame);
    }, 60000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setupReveal();
      setupMobileNav();
      setupIntelligenceConsole();
      setupIntelligenceFeed();
      setupMarketBoard();
      setupSportsEdge();
    });
  } else {
    setupReveal();
    setupMobileNav();
    setupIntelligenceConsole();
    setupIntelligenceFeed();
    setupMarketBoard();
    setupSportsEdge();
  }
})();
