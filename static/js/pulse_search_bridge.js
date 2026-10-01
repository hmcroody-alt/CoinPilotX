(function () {
  if (window.__pulseSearchBridgeBound) return;
  window.__pulseSearchBridgeBound = true;

  const groups = [["posts", "Posts"], ["creators", "Creators"], ["videos", "Videos"], ["reels", "Reels"], ["statuses", "Statuses"], ["marketplace", "Marketplace"], ["music", "Music"], ["groups", "Groups"], ["rooms", "Chat Rooms"], ["comments", "Comments"]];
  const state = { timer: 0, controller: null, query: "" };
  const escapeHtml = value => String(value || "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const overlay = () => document.getElementById("pulseSearchOverlay");
  const overlayInput = () => document.getElementById("pulseSearchOverlayInput");
  const resultsBox = () => document.querySelector("[data-pulse-search-results]");

  function message(html) {
    const box = resultsBox();
    if (box) box.innerHTML = html;
  }

  function highlight(text, query) {
    const safe = escapeHtml(text);
    const term = String(query || "").trim();
    if (!term) return safe;
    const needle = Array.from(term).map(char => ".*+?^${}()|[]\\".includes(char) ? `\\${char}` : char).join("");
    return safe.replace(new RegExp(`(${needle})`, "ig"), "<em>$1</em>");
  }

  function resultHtml(item, query) {
    const letter = String(item.type || item.title || "P").slice(0, 1).toUpperCase();
    return `<a class="pulse-search-result" href="${escapeHtml(item.url || "/pulse")}"><span class="pulse-search-mark">${escapeHtml(letter)}</span><span><strong>${highlight(item.title || "PulseSoc result", query)}</strong><small>${highlight(item.description || item.meta || "", query)}</small></span><span class="pulse-search-type">${escapeHtml(item.type || "PulseSoc")}</span></a>`;
  }

  function render(data) {
    const query = data.query || state.query || "";
    const results = data.results || {};
    const sections = groups.map(([key, label]) => {
      const items = results[key] || [];
      return items.length ? `<section class="pulse-search-group"><h3>${label}</h3>${items.map(item => resultHtml(item, query)).join("")}</section>` : "";
    }).join("");
    message(sections || '<div class="pulse-search-empty"><strong>No PulseSoc results found.</strong><p class="muted">Try another creator, topic, video, sound, listing, room, reel, or signal.</p></div>');
  }

  async function run(query) {
    const clean = String(query || "").trim();
    state.query = clean;
    if (!clean) {
      message('<p class="muted">Search public PulseSoc posts, creators, videos, reels, statuses, marketplace listings, music, groups, rooms, and comments.</p>');
      return;
    }
    state.controller?.abort();
    state.controller = typeof AbortController === "undefined" ? null : new AbortController();
    message('<div class="pulse-search-loading">Searching PulseSoc...</div>');
    try {
      const response = await fetch(`/api/pulse/search?q=${encodeURIComponent(clean)}&limit=8`, { credentials: "same-origin", cache: "no-store", signal: state.controller?.signal });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) throw new Error(data.message || data.error || "Search failed.");
      if (clean !== state.query) return;
      render(data);
    } catch (error) {
      if (clean !== state.query || error?.name === "AbortError") return;
      message(`<div class="pulse-search-error"><strong>Search could not load.</strong><p class="muted">${escapeHtml(error.message || "Try again in a moment.")}</p></div>`);
    }
  }

  function open(query) {
    const modal = overlay();
    if (!modal) {
      window.location.assign(`/pulse/search?q=${encodeURIComponent(query || "")}`);
      return;
    }
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("pulse-search-open");
    const clean = String(query || "").trim();
    const input = overlayInput();
    if (input) {
      input.value = clean;
      setTimeout(() => input.focus(), 30);
    }
    run(clean);
  }

  function close() {
    overlay()?.classList.remove("open");
    overlay()?.setAttribute("aria-hidden", "true");
    document.body.classList.remove("pulse-search-open");
  }

  function debounce(query) {
    clearTimeout(state.timer);
    state.timer = setTimeout(() => run(query), 320);
  }

  document.addEventListener("input", event => {
    const desktopInput = event.target.closest?.(".pulse-desktop-search[data-pulse-search-input]");
    if (desktopInput) {
      const query = desktopInput.value || "";
      if (query.trim().length >= 2) {
        open(query);
        debounce(query);
      }
      return;
    }
    if (event.target.closest?.("#pulseSearchOverlayInput")) debounce(event.target.value || "");
  }, true);

  document.addEventListener("submit", event => {
    const form = event.target.closest?.("[data-pulse-search-form], [data-pulse-search-overlay-form]");
    if (!form) return;
    event.preventDefault();
    const input = form.querySelector("[data-pulse-search-input], #pulseSearchOverlayInput");
    open(input?.value || "");
  }, true);

  document.addEventListener("click", event => {
    if (event.target.closest?.("#pulseMobileSearch")) {
      event.preventDefault();
      open("");
      return;
    }
    if (event.target.closest?.("[data-close-pulse-search]")) {
      event.preventDefault();
      close();
      return;
    }
    if (event.target === overlay()) close();
  }, true);

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && overlay()?.classList.contains("open")) close();
  });

  window.PulseSocGlobalSearch = open;
})();
