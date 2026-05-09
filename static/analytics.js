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

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setupReveal);
  } else {
    setupReveal();
  }
})();
