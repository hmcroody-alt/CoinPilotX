(function () {
  function sendAnalytics(eventName, element) {
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
})();
