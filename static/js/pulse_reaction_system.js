(() => {
  if (window.PulseReactionSystem) return;

  const toggleActions = new Set(["like", "save", "repost", "remix", "follow"]);

  function normalizeAction(button) {
    const raw = button?.dataset?.action
      || (button?.matches?.("[data-reel-react],[data-status-story-react],[data-post-like]") ? "like" : "")
      || (button?.matches?.("[data-reel-save],[data-save-post],[data-save-video]") ? "save" : "")
      || (button?.matches?.("[data-reel-repost],[data-post-repost],[data-video-repost]") ? "repost" : "")
      || (button?.matches?.("[data-reel-remix]") ? "remix" : "")
      || (button?.matches?.("[data-open-comments],[data-post-comment]") ? "comment" : "")
      || (button?.matches?.("[data-share-reel],[data-post-share],[data-share-video]") ? "share" : "")
      || (button?.matches?.("[data-reel-more],[data-post-menu]") ? "more" : "");
    return String(raw || "action").trim().toLowerCase();
  }

  function decorate(button, options = {}) {
    if (!button) return null;
    const action = String(options.action || normalizeAction(button)).trim().toLowerCase() || "action";
    button.classList.add("pulse-reaction-button");
    button.dataset.action = action;
    if (!button.hasAttribute("aria-label")) {
      const label = options.label || button.querySelector(".reel-action-label")?.textContent || button.textContent || action;
      button.setAttribute("aria-label", `${String(label).trim() || action} ${options.itemType || "content"}`.trim());
    }
    if (toggleActions.has(action) && !button.hasAttribute("aria-pressed")) {
      button.setAttribute("aria-pressed", button.classList.contains("active") || button.classList.contains("is-active") ? "true" : "false");
    }
    const icon = button.querySelector(".reel-action-icon,.post-action-icon,.action-icon");
    icon?.classList.add("pulse-reaction-icon");
    const label = button.querySelector(".reel-action-label");
    label?.classList.add("pulse-reaction-label");
    const meta = button.querySelector(".reel-action-meta");
    meta?.classList.add("pulse-reaction-meta");
    return button;
  }

  function hydrate(root = document) {
    root.querySelectorAll?.(".reel-actions,.reels-action-rail,.pulse-status-story-actions,.post-action-row").forEach((bar) => {
      bar.classList.add("pulse-reaction-bar");
      if (!bar.dataset.layout) {
        bar.dataset.layout = bar.classList.contains("post-action-row") ? "horizontal" : "vertical";
      }
    });
    root.querySelectorAll?.(".reel-action,.pulse-action-button,.pulse-status-action,.post-action-button").forEach((button) => decorate(button));
  }

  window.PulseReactionSystem = { decorate, hydrate };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => hydrate(document), { once: true });
  } else {
    hydrate(document);
  }
})();
