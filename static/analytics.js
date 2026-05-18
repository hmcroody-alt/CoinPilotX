(function () {
  var SESSION_KEY = "coinpilotxai_session_id";
  var RETURN_VISIT_KEY = "coinpilotxai_last_visit_day";
  var scrollDepthSent = {};
  var pageStart = Date.now();

  function adsConfig() {
    return window.CPX_ADS_CONFIG || {};
  }

  function getSessionId() {
    try {
      var existing = window.localStorage.getItem(SESSION_KEY);
      if (existing) {
        return existing;
      }
      var created = "cpx_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 12);
      window.localStorage.setItem(SESSION_KEY, created);
      return created;
    } catch (err) {
      return "cpx_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 12);
    }
  }

  function analyticsEventName(rawName, element) {
    var name = rawName || "cta_click";
    var href = element && element.href ? element.href : "";
    if (href.indexOf("t.me/DocShieldX_bot") !== -1) {
      return "telegram_click";
    }
    var map = {
      launch_free: "signup_started",
      continue_telegram: "telegram_click",
      upgrade_pro: "pro_upgrade_click",
      pro_checkout_started: "pro_checkout_started",
      pro_payment_success: "pro_payment_success",
      telegram_upgrade_clicked: "telegram_upgrade_clicked",
      telegram_connect: "telegram_connect",
      sms_activation: "sms_activation",
      website_upgrade_started: "website_upgrade_started",
      stripe_checkout_started: "stripe_checkout_started",
      dashboard_upgrade_clicked: "dashboard_upgrade_clicked",
      account_click: "account_click",
      pay_btc: "pro_upgrade_click",
      pay_card: "pro_upgrade_click",
      view_pricing: "pricing_click",
      compare_pro: "pricing_click",
      day_signal: "day_signal_click",
      try_ai_assistant: "ai_assistant_click",
      view_sports_edge: "sports_edge_click",
      view_live_market: "market_click",
      view_safety: "support_click",
      support_click: "support_click",
      signup_form_submit: "signup_form_submit",
      signup_started: "signup_started",
      signup_completed: "signup_completed",
      enter_alpha_arena: "arena_entered",
      alpha_arena_enter_clicked: "alpha_arena_enter_clicked",
      alpha_arena_preview_clicked: "alpha_arena_preview_clicked",
      enter_roast_battle: "roast_battle_entered",
      roast_battle_join: "roast_battle_join",
      scam_scan_started: "scam_scan_started",
      scam_scan_completed: "scam_shield_scan",
      alert_activation: "alert_activation",
      alert_created: "alert_created",
      replay_view: "replay_view",
      replay_share: "replay_share",
      creator_profile_visit: "creator_profile_visit",
      share_telegram: "share_click",
      share_x: "share_click",
      share_reddit: "share_click",
      share_whatsapp: "share_click",
      internal_link: "internal_link_click",
      signup_click: "signup_click",
      referral_link: "referral_link_click",
      view_prediction: "seo_prediction_view",
      view_live_market_page: "seo_live_market_view",
      article_read: "seo_article_read",
      view_demo: "demo_view",
      install_app: "pwa_install_click"
    };
    return map[name] || name || "cta_click";
  }

  function utmParams() {
    var params = new URLSearchParams(window.location.search || "");
    return {
      utm_source: params.get("utm_source") || "",
      utm_medium: params.get("utm_medium") || "",
      utm_campaign: params.get("utm_campaign") || "",
      utm_content: params.get("utm_content") || "",
      utm_term: params.get("utm_term") || "",
      gclid: params.get("gclid") || "",
      gbraid: params.get("gbraid") || "",
      wbraid: params.get("wbraid") || ""
    };
  }

  function googleAdsConversionKey(eventName) {
    var map = {
      signup_completed: "account_creation",
      account_created: "account_creation",
      pro_upgrade: "pro_upgrade",
      pro_checkout_started: "pro_upgrade",
      pro_payment_success: "pro_upgrade",
      pro_subscription_active: "pro_upgrade",
      arena_entered: "arena_session_start",
      arena_session_start: "arena_session_start",
      roast_battle_join: "roast_battle_join",
      scam_shield_scan: "scam_shield_scan",
      scam_shield_used: "scam_shield_scan",
      replay_share: "replay_share",
      return_visit: "return_visit",
      alert_activation: "alert_activation",
      alert_created: "alert_activation"
    };
    return map[eventName] || "";
  }

  function sendGoogleAdsConversion(eventName, payload) {
    var config = adsConfig();
    var adsId = config.googleAdsId || "";
    var labels = config.conversions || {};
    var key = googleAdsConversionKey(eventName);
    var label = key ? labels[key] : "";
    if (!window.gtag || !adsId || !label) {
      return;
    }
    window.gtag("event", "conversion", Object.assign({}, payload || {}, {
      send_to: adsId + "/" + label
    }));
  }

  function trackFirstParty(eventName, metadata, options) {
    var utm = utmParams();
    var payload = {
      session_id: getSessionId(),
      event_name: eventName,
      page_url: window.location.href,
      referrer: document.referrer || "",
      utm_source: utm.utm_source,
      utm_medium: utm.utm_medium,
      utm_campaign: utm.utm_campaign,
      utm_content: utm.utm_content,
      utm_term: utm.utm_term,
      gclid: utm.gclid,
      gbraid: utm.gbraid,
      wbraid: utm.wbraid,
      metadata: metadata || {}
    };
    var body = JSON.stringify(payload);
    if (options && options.beacon && navigator.sendBeacon) {
      try {
        var blob = new Blob([body], { type: "application/json" });
        navigator.sendBeacon("/api/track", blob);
        return;
      } catch (err) {
        // Fall through to fetch.
      }
    }
    fetch("/api/track", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: Boolean(options && options.keepalive)
    }).catch(function () {
      // Analytics must never break the site.
    });
  }

  function sendAnalytics(eventName, element) {
    // Ethical funnel analytics: CTA intent only. Do not track wallet data, seed phrases, private keys, or user secrets.
    var normalizedEvent = analyticsEventName(eventName, element);
    var payload = {
      event_category: "coinpilotx_website",
      event_label: element && (element.textContent || "").trim(),
      link_url: element && element.href ? element.href : undefined
    };

    if (window.gtag) {
      window.gtag("event", normalizedEvent, payload);
    }
    sendGoogleAdsConversion(normalizedEvent, payload);

    if (window.posthog && window.posthog.capture) {
      window.posthog.capture(normalizedEvent, payload);
    }

    if (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1") {
      console.log("[CoinPilotX analytics]", normalizedEvent, payload);
    }

    trackFirstParty(normalizedEvent, payload);
  }

  window.coinPilotXTrack = function (eventName, metadata) {
    if (window.gtag) {
      window.gtag("event", eventName, metadata || {});
    }
    sendGoogleAdsConversion(eventName, metadata || {});
    if (window.posthog && window.posthog.capture) {
      window.posthog.capture(eventName, metadata || {});
    }
    trackFirstParty(eventName, metadata || {});
  };

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

  function setupScrollAndTimeTracking() {
    var params = new URLSearchParams(window.location.search || "");
    trackFirstParty("page_view", {
      title: document.title,
      path: window.location.pathname,
      ref: params.get("ref") || "",
      screen_width: window.innerWidth,
      screen_height: window.innerHeight
    });
    if (params.get("signup_completed") === "1") {
      window.coinPilotXTrack("signup_completed", { path: window.location.pathname });
    }
    if (params.get("pro_payment_success") === "1" || params.get("payment_success") === "1") {
      window.coinPilotXTrack("pro_payment_success", { path: window.location.pathname });
    }
    try {
      var today = new Date().toISOString().slice(0, 10);
      var previousDay = window.localStorage.getItem(RETURN_VISIT_KEY);
      if (previousDay && previousDay !== today) {
        window.coinPilotXTrack("return_visit", {
          previous_visit_day: previousDay,
          current_visit_day: today,
          path: window.location.pathname
        });
      }
      window.localStorage.setItem(RETURN_VISIT_KEY, today);
    } catch (err) {
      // Return visit tracking is optional and must not block the page.
    }

    window.addEventListener("scroll", function () {
      var doc = document.documentElement;
      var scrollable = Math.max(doc.scrollHeight - window.innerHeight, 1);
      var depth = Math.round((window.scrollY / scrollable) * 100);
      [25, 50, 75, 100].forEach(function (mark) {
        if (depth >= mark && !scrollDepthSent[mark]) {
          scrollDepthSent[mark] = true;
          trackFirstParty("scroll_depth", { depth: mark });
        }
      });
    }, { passive: true });

    function sendTimeOnPage() {
      var seconds = Math.max(1, Math.round((Date.now() - pageStart) / 1000));
      trackFirstParty("time_on_page", { seconds: seconds }, { beacon: true, keepalive: true });
    }

    window.addEventListener("pagehide", sendTimeOnPage);
    window.addEventListener("beforeunload", sendTimeOnPage);
  }

  function setupVisitorHeartbeat() {
    if (!window.fetch) {
      return;
    }
    var lastSent = 0;
    function sendHeartbeat(force) {
      var now = Date.now();
      if (!force && document.visibilityState !== "visible") {
        return;
      }
      if (!force && now - lastSent < 20000) {
        return;
      }
      lastSent = now;
      fetch("/api/visitor/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        keepalive: true,
        body: JSON.stringify({ path: window.location.pathname, visibility: document.visibilityState })
      }).catch(function () {});
    }
    sendHeartbeat(true);
    window.setInterval(function () { sendHeartbeat(false); }, 25000);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        sendHeartbeat(true);
      }
    });
  }

  function setupLeadForms() {
    document.querySelectorAll("[data-lead-form]").forEach(function (form) {
      form.addEventListener("submit", async function (event) {
        event.preventDefault();
        var message = form.querySelector("[data-lead-message]");
        var data = new FormData(form);
        var email = (data.get("email") || "").toString().trim();
        var phone = (data.get("phone") || "").toString().trim();
        var emailOptIn = Boolean(data.get("email_opt_in"));
        var smsOptIn = Boolean(data.get("sms_opt_in"));

        function setMessage(text, type) {
          if (!message) {
            return;
          }
          message.textContent = text;
          message.classList.remove("success", "error");
          if (type) {
            message.classList.add(type);
          }
        }

        if (!email) {
          setMessage("Please enter your email address.", "error");
          return;
        }
        if (smsOptIn && !phone) {
          setMessage("SMS opt-in requires a phone number.", "error");
          return;
        }
        if (!emailOptIn && !smsOptIn) {
          setMessage("Please choose email updates, SMS updates, or both.", "error");
          return;
        }

        var utm = utmParams();
        var payload = {
          session_id: getSessionId(),
          full_name: (data.get("full_name") || "").toString(),
          email: email,
          phone: phone,
          country: (data.get("country") || "").toString(),
          source: form.getAttribute("data-source") || "website",
          email_opt_in: emailOptIn,
          sms_opt_in: smsOptIn,
          page_url: window.location.href,
          referrer: document.referrer || "",
          utm_source: utm.utm_source,
          utm_medium: utm.utm_medium,
          utm_campaign: utm.utm_campaign
        };

        setMessage("Saving your preferences...", "");
        try {
          var response = await fetch("/api/leads", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
          });
          var result = await response.json();
          if (!response.ok || !result.ok) {
            throw new Error(result.message || "Please check the form and try again.");
          }
          setMessage(result.message || "Thanks — you’re on the CoinPilotXAI Inc. update list.", "success");
          form.reset();
          window.coinPilotXTrack("signup_form_submit", {
            source: payload.source,
            email_opt_in: emailOptIn,
            sms_opt_in: smsOptIn
          });
        } catch (err) {
          setMessage(err.message || "Could not save your preferences right now.", "error");
        }
      });
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

  function setupDaySignal() {
    var card = document.querySelector("[data-day-signal-card]");
    if (!card) {
      return;
    }

    var progress = card.querySelector("[data-day-progress]");
    var questionEl = card.querySelector("[data-day-question]");
    var helper = card.querySelector("[data-day-helper]");
    var options = card.querySelector("[data-day-options]");
    var resultEl = card.querySelector("[data-day-result]");
    var startButton = card.querySelector("[data-day-start]");
    var answers = {};
    var stepIndex = 0;

    var questions = [
      {
        key: "feeling",
        question: "How do you feel today?",
        helper: "Choose the emotional state that feels most honest right now.",
        options: [
          ["confident", "Confident"],
          ["calm", "Calm"],
          ["nervous", "Nervous"],
          ["tired", "Tired"]
        ]
      },
      {
        key: "prepared",
        question: "How prepared are you for what you want to do today?",
        helper: "Readiness matters more than excitement.",
        options: [
          ["very_prepared", "Very prepared"],
          ["somewhat_prepared", "Somewhat prepared"],
          ["not_prepared", "Not prepared"],
          ["guessing", "I'm guessing"]
        ]
      },
      {
        key: "opportunity",
        question: "What kind of opportunity are you thinking about?",
        helper: "The score adjusts for the type of decision in front of you.",
        options: [
          ["crypto", "Crypto/Trading"],
          ["sports", "Sports Edge"],
          ["business", "Business/Money"],
          ["personal", "Personal decision"]
        ]
      },
      {
        key: "walkaway",
        question: "Are you willing to walk away if the signal looks risky?",
        helper: "Discipline is part of the signal.",
        options: [
          ["yes", "Yes"],
          ["maybe", "Maybe"],
          ["no", "No"]
        ]
      }
    ];

    function postJson(url, payload) {
      return fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) {
            throw new Error(data.response || "Day Signal check failed");
          }
          return data;
        });
      });
    }

    function renderQuestion() {
      var item = questions[stepIndex];
      progress.textContent = "Question " + (stepIndex + 1) + " of " + questions.length;
      questionEl.textContent = item.question;
      helper.textContent = item.helper;
      resultEl.hidden = true;
      resultEl.textContent = "";
      options.innerHTML = "";
      item.options.forEach(function (option) {
        var button = document.createElement("button");
        button.className = "button";
        button.type = "button";
        button.textContent = option[1];
        button.addEventListener("click", function () {
          answers[item.key] = option[0];
          stepIndex += 1;
          if (stepIndex < questions.length) {
            renderQuestion();
            return;
          }
          renderResult();
        });
        options.appendChild(button);
      });
    }

    async function renderResult() {
      progress.textContent = "Generating Day Signal";
      questionEl.textContent = "CoinPilotX is checking readiness, discipline, and risk alignment...";
      helper.textContent = "This is a responsible confidence check, not a prediction of fate or guaranteed outcome.";
      options.innerHTML = "";
      try {
        var data = await postJson("/api/day-signal", { answers: answers });
        progress.textContent = "Day Signal Ready";
        questionEl.textContent = "CoinPilotX Day Signal";
        helper.textContent = "Use this as a pause point before acting.";
        resultEl.hidden = false;
        resultEl.textContent = data.response;
        var restart = document.createElement("button");
        restart.className = "button primary pulse-cta";
        restart.type = "button";
        restart.textContent = "Check Again";
        restart.addEventListener("click", start);
        options.appendChild(restart);
      } catch (err) {
        progress.textContent = "Try Again";
        questionEl.textContent = "Day Signal could not finish.";
        helper.textContent = err.message;
        var retry = document.createElement("button");
        retry.className = "button primary pulse-cta";
        retry.type = "button";
        retry.textContent = "Restart Day Signal";
        retry.addEventListener("click", start);
        options.appendChild(retry);
      }
    }

    function start() {
      answers = {};
      stepIndex = 0;
      renderQuestion();
    }

    if (startButton) {
      startButton.addEventListener("click", start);
    }
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
          '<div class="sports-telegram-action"><a class="button gold" href="/app" data-analytics="try_sports_ai">Open Command Center</a></div>' +
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
      setupDaySignal();
      setupIntelligenceFeed();
      setupMarketBoard();
      setupSportsEdge();
      setupLeadForms();
      setupScrollAndTimeTracking();
      setupVisitorHeartbeat();
    });
  } else {
    setupReveal();
    setupMobileNav();
    setupIntelligenceConsole();
    setupDaySignal();
    setupIntelligenceFeed();
    setupMarketBoard();
    setupSportsEdge();
    setupLeadForms();
    setupScrollAndTimeTracking();
    setupVisitorHeartbeat();
  }
})();
