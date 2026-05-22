(function () {
  function parseTimestamp(value) {
    if (!value) return null;
    const raw = String(value).trim();
    if (!raw) return null;
    const normalized = raw.includes("T") && !/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw + "Z" : raw;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function clock(date) {
    return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function smartTime(value, nowDate) {
    const date = parseTimestamp(value);
    if (!date) return "Recently";
    const now = nowDate || new Date();
    const diffMs = Math.max(0, now.getTime() - date.getTime());
    const seconds = Math.floor(diffMs / 1000);
    if (seconds < 10) return "Just now";
    if (seconds < 60) return seconds + " sec ago";
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return minutes + (minutes === 1 ? " min ago" : " min ago");
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return hours + (hours === 1 ? " hour ago" : " hours ago");

    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const dayDiff = Math.round((startToday - startDate) / 86400000);
    if (dayDiff === 1) return "Yesterday at " + clock(date);
    if (dayDiff > 1 && dayDiff < 7) {
      return date.toLocaleDateString([], { weekday: "long" }) + " at " + clock(date);
    }
    if (date.getFullYear() === now.getFullYear()) {
      return date.toLocaleDateString([], { month: "short", day: "numeric" }) + " at " + clock(date);
    }
    return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
  }

  function element(timestamp, suffix) {
    const escapeHtml = function (value) {
      return String(value || "").replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    };
    const safe = escapeHtml(timestamp);
    const tail = suffix ? " <span class=\"time-dot\">•</span> " + escapeHtml(suffix) : "";
    return "<time class=\"smart-time\" datetime=\"" + safe + "\" data-timestamp=\"" + safe + "\">" + smartTime(timestamp) + "</time>" + tail;
  }

  function hydrate(root) {
    const scope = root || document;
    scope.querySelectorAll(".smart-time").forEach(function (node) {
      const ts = node.getAttribute("data-timestamp") || node.getAttribute("datetime") || node.textContent;
      node.textContent = smartTime(ts);
      if (ts) node.setAttribute("title", ts);
    });
  }

  window.CoinPilotTime = { smartTime: smartTime, element: element, hydrate: hydrate };
  document.addEventListener("DOMContentLoaded", function () {
    hydrate(document);
    setInterval(function () { hydrate(document); }, 45000);
  });
})();
