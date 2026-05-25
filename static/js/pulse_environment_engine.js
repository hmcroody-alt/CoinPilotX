(function () {
  "use strict";

  function install() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (document.querySelector("[data-pulse-environment]")) return;
    const layer = document.createElement("div");
    layer.setAttribute("data-pulse-environment", "alive");
    layer.className = "pulse-environment-engine";
    layer.innerHTML = "<i></i><i></i><i></i>";
    document.body.prepend(layer);
    let x = 50, y = 16, raf = 0;
    const update = () => {
      raf = 0;
      document.documentElement.style.setProperty("--pulse-env-x", x + "%");
      document.documentElement.style.setProperty("--pulse-env-y", y + "%");
    };
    window.addEventListener("pointermove", (event) => {
      x = Math.round((event.clientX / Math.max(1, window.innerWidth)) * 100);
      y = Math.round((event.clientY / Math.max(1, window.innerHeight)) * 100);
      if (!raf) raf = requestAnimationFrame(update);
    }, { passive: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", install);
  else install();
})();
