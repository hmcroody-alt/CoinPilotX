/* PulseSoc Operations Center V2 — shell runtime.
   Command palette (Cmd/Ctrl-K), global section search, active-link
   highlighting, collapsible group persistence, mobile nav, live status.
   Destination index is injected by admin_page_html() as JSON. */
(function () {
  "use strict";

  var INDEX = [];
  try {
    var el = document.getElementById("ops-nav-index");
    if (el) INDEX = JSON.parse(el.textContent || "[]");
  } catch (e) { INDEX = []; }

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  /* ---- Active link highlighting -------------------------------------- */
  function markActive() {
    var path = window.location.pathname.replace(/\/+$/, "") || "/admin/dashboard";
    var best = null, bestLen = -1;
    $$(".ops-link").forEach(function (a) {
      a.classList.remove("is-active");
      var href = (a.getAttribute("href") || "").replace(/\/+$/, "");
      if (!href) return;
      if (path === href || path.indexOf(href + "/") === 0) {
        if (href.length > bestLen) { best = a; bestLen = href.length; }
      }
    });
    if (best) {
      best.classList.add("is-active");
      var grp = best.closest("details.ops-group");
      if (grp) grp.open = true;
    }
  }

  /* ---- Collapsible group persistence --------------------------------- */
  function persistGroups() {
    $$("details.ops-group").forEach(function (d) {
      var key = "ops-grp:" + (d.dataset.key || "");
      var saved = localStorage.getItem(key);
      if (saved === "0") d.open = false;
      if (saved === "1") d.open = true;
      d.addEventListener("toggle", function () {
        try { localStorage.setItem(key, d.open ? "1" : "0"); } catch (e) {}
      });
    });
  }

  /* ---- Mobile nav ---------------------------------------------------- */
  function wireMobileNav() {
    var btn = $(".ops-menu-btn"), scrim = $(".ops-scrim-mobile");
    function close() { document.body.classList.remove("ops-nav-open"); }
    if (btn) btn.addEventListener("click", function () { document.body.classList.toggle("ops-nav-open"); });
    if (scrim) scrim.addEventListener("click", close);
    $$(".ops-link").forEach(function (a) { a.addEventListener("click", close); });
  }

  /* ---- Command palette ----------------------------------------------- */
  var palette, input, list, items = [], activeIdx = 0;
  var remoteResults = [], remoteQuery = "", fetchTimer = null, fetchToken = 0;

  function scheduleRemote(q) {
    clearTimeout(fetchTimer);
    q = (q || "").trim();
    if (q.length < 2) { remoteResults = []; remoteQuery = ""; return; }
    fetchTimer = setTimeout(function () {
      var token = ++fetchToken, qq = q;
      fetch("/admin/ops/search.json?q=" + encodeURIComponent(qq), {
        credentials: "same-origin", headers: { "Accept": "application/json" }
      })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (token !== fetchToken) return;              // stale response
          remoteResults = (d && d.ok && d.results) ? d.results : [];
          remoteQuery = qq;
          if (palette.classList.contains("open")) render(input.value);
        })
        .catch(function () { /* degrade to section-only search */ });
    }, 180);
  }

  function score(item, q) {
    var hay = (item.label + " " + item.group + " " + item.href).toLowerCase();
    q = q.toLowerCase().trim();
    if (!q) return 1;
    if (item.label.toLowerCase().indexOf(q) === 0) return 100;
    if (hay.indexOf(q) !== -1) return 60;
    // subsequence fuzzy
    var qi = 0;
    for (var i = 0; i < hay.length && qi < q.length; i++) if (hay[i] === q[qi]) qi++;
    return qi === q.length ? 20 : 0;
  }

  function render(q) {
    var local = INDEX
      .map(function (it) { return { it: it, s: score(it, q) }; })
      .filter(function (m) { return m.s > 0; })
      .sort(function (a, b) { return b.s - a.s; })
      .slice(0, 30)
      .map(function (m) { return m.it; });

    // Live entity results (users, …) lead when they match the current query.
    var remote = (q && q.trim() === remoteQuery) ? remoteResults : [];
    var matches = remote.concat(local);

    list.innerHTML = "";
    items = [];
    if (!matches.length) {
      list.innerHTML = '<div class="ops-palette__empty">No matches for "' + escapeHtml(q) + '"</div>';
      return;
    }
    var lastGroup = null;
    matches.forEach(function (it) {
      if (it.group !== lastGroup) {
        var gl = document.createElement("div");
        gl.className = "ops-palette__grouplabel";
        gl.textContent = it.group;
        list.appendChild(gl);
        lastGroup = it.group;
      }
      var sub = it.sublabel ? '<span class="sub">' + escapeHtml(it.sublabel) + '</span>' : '';
      var row = document.createElement("div");
      row.className = "ops-palette__item";
      row.setAttribute("role", "option");
      row.innerHTML =
        '<span class="ico">' + (it.icon || "&#8226;") + '</span>' +
        '<span class="lbl">' + escapeHtml(it.label) + sub + '</span>' +
        '<span class="grp">' + escapeHtml(it.group) + '</span>';
      row.addEventListener("click", function () { go(it.href); });
      row.addEventListener("mousemove", function () { setActive(items.indexOf(row)); });
      list.appendChild(row);
      items.push(row);
    });
    setActive(0);
  }

  function setActive(i) {
    if (!items.length) return;
    activeIdx = (i + items.length) % items.length;
    items.forEach(function (r, idx) { r.classList.toggle("active", idx === activeIdx); });
    var el = items[activeIdx];
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
  }

  function go(href) { if (href) window.location.href = href; }

  function openPalette() {
    if (!palette) return;
    palette.classList.add("open");
    input.value = "";
    render("");
    input.focus();
    document.body.style.overflow = "hidden";
  }
  function closePalette() {
    if (!palette) return;
    palette.classList.remove("open");
    document.body.style.overflow = "";
  }

  function wirePalette() {
    palette = $(".ops-palette");
    if (!palette) return;
    input = $(".ops-palette__input", palette);
    list = $(".ops-palette__list", palette);
    $(".ops-palette__scrim", palette).addEventListener("click", closePalette);
    input.addEventListener("input", function () { render(input.value); scheduleRemote(input.value); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIdx + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIdx - 1); }
      else if (e.key === "Enter") {
        e.preventDefault();
        var row = items[activeIdx];
        if (row) row.dispatchEvent(new MouseEvent("click"));
      } else if (e.key === "Escape") { closePalette(); }
    });

    var search = $(".ops-search");
    if (search) search.addEventListener("click", openPalette);

    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        palette.classList.contains("open") ? closePalette() : openPalette();
      } else if (e.key === "Escape" && palette.classList.contains("open")) {
        closePalette();
      }
    });
  }

  /* ---- Live status strip --------------------------------------------- */
  function paintStat(node, state) {
    node.classList.remove("ok", "warn", "down");
    if (state === "ok" || state === "warn" || state === "down") node.classList.add(state);
  }
  function pollStatus() {
    var strip = $(".ops-status-strip");
    if (!strip) return;
    fetch("/admin/ops/status.json", { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.services) return;
        $$(".ops-stat", strip).forEach(function (node) {
          var svc = node.dataset.svc;
          if (data.services[svc]) paintStat(node, data.services[svc]);
        });
      })
      .catch(function () { /* degrade silently; dots stay neutral */ });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function init() {
    markActive();
    persistGroups();
    wireMobileNav();
    wirePalette();
    pollStatus();
    setInterval(pollStatus, 20000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
