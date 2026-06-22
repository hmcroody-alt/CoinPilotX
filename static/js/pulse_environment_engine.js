(function () {
  "use strict";

  const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection || {};
  const prefersReducedData = !!connection.saveData || /(^|-)2g$/i.test(String(connection.effectiveType || ""));

  function install() {
    if (document.querySelector("[data-pulse-environment]")) return;
    const layer = document.createElement("div");
    layer.setAttribute("data-pulse-environment", "alive");
    layer.className = "pulse-environment-engine";
    layer.setAttribute("aria-hidden", "true");
    layer.innerHTML = `
      <div class="pulse-city-deep" data-city-layer="deep">
        <span class="pulse-city-planet"></span>
        <span class="pulse-city-moon"></span>
        <span class="pulse-city-starfield"></span>
      </div>
      <div class="pulse-city-district pulse-city-district-left" data-city-layer="mid">
        <span class="pulse-city-tower tower-a"></span>
        <span class="pulse-city-tower tower-b"></span>
        <span class="pulse-city-tower tower-c"></span>
        <span class="pulse-city-billboard billboard-live">LIVE</span>
        <span class="pulse-city-billboard billboard-trend">TRENDING</span>
      </div>
      <div class="pulse-city-district pulse-city-district-right" data-city-layer="mid">
        <span class="pulse-city-tower tower-d"></span>
        <span class="pulse-city-tower tower-e"></span>
        <span class="pulse-city-tower tower-f"></span>
        <span class="pulse-city-billboard billboard-ai">AI SCAN</span>
        <span class="pulse-city-billboard billboard-market">MARKET</span>
      </div>
      <div class="pulse-city-highway" data-city-layer="foreground">
        <span class="pulse-highway-line line-a"></span>
        <span class="pulse-highway-line line-b"></span>
        <span class="pulse-highway-line line-c"></span>
      </div>
      <div class="pulse-city-traffic" data-city-layer="foreground">
        <span class="pulse-city-vehicle vehicle-a"></span>
        <span class="pulse-city-vehicle vehicle-b"></span>
        <span class="pulse-city-vehicle vehicle-c"></span>
        <span class="pulse-city-vehicle vehicle-d"></span>
      </div>
      <div class="pulse-city-holograms" data-city-layer="foreground">
        <span class="pulse-city-holo holo-a"></span>
        <span class="pulse-city-holo holo-b"></span>
        <span class="pulse-city-holo holo-c"></span>
      </div>`;
    document.body.prepend(layer);
    if (prefersReducedMotion || prefersReducedData) layer.classList.add("is-reduced");
    if (!document.body.classList.contains("pulse-home-os")) return;
    let x = 50, y = 16, scroll = 0, raf = 0, activityTimer = 0;
    const update = () => {
      raf = 0;
      document.documentElement.style.setProperty("--pulse-env-x", x + "%");
      document.documentElement.style.setProperty("--pulse-env-y", y + "%");
      document.documentElement.style.setProperty("--pulse-city-x", String(x));
      document.documentElement.style.setProperty("--pulse-city-y", String(y));
      document.documentElement.style.setProperty("--pulse-city-scroll", String(scroll));
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    window.addEventListener("pointermove", event => {
      if (prefersReducedMotion || prefersReducedData) return;
      x = Math.round((event.clientX / Math.max(1, window.innerWidth)) * 100);
      y = Math.round((event.clientY / Math.max(1, window.innerHeight)) * 100);
      schedule();
    }, { passive: true });
    window.addEventListener("touchmove", event => {
      if (prefersReducedMotion || prefersReducedData || !event.touches?.length) return;
      x = Math.round((event.touches[0].clientX / Math.max(1, window.innerWidth)) * 100);
      y = Math.round((event.touches[0].clientY / Math.max(1, window.innerHeight)) * 100);
      schedule();
    }, { passive: true });
    window.addEventListener("scroll", () => {
      scroll = Math.min(120, Math.round(window.scrollY / 10));
      schedule();
    }, { passive: true });
    const refreshActivity = () => {
      const live = Number(document.querySelector('[data-network-count="live"]')?.textContent || 0);
      const ai = Number(document.querySelector('[data-network-count="ai"]')?.textContent || 0);
      const alerts = Number(document.querySelector("[data-alert-unread]")?.textContent || 0);
      layer.dataset.cityLive = String(Math.min(9, Math.max(0, live || 0)));
      layer.dataset.cityAi = String(Math.min(9, Math.max(0, ai || 0)));
      layer.dataset.cityAlerts = String(Math.min(9, Math.max(0, alerts || 0)));
      layer.classList.toggle("has-live-energy", live > 0);
      layer.classList.toggle("has-alert-energy", alerts > 0);
    };
    refreshActivity();
    activityTimer = window.setInterval(refreshActivity, 8000);
    document.addEventListener("visibilitychange", () => {
      layer.classList.toggle("is-paused", document.hidden);
      if (document.hidden && activityTimer) {
        clearInterval(activityTimer);
        activityTimer = 0;
      } else if (!document.hidden && !activityTimer) {
        refreshActivity();
        activityTimer = window.setInterval(refreshActivity, 8000);
      }
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
