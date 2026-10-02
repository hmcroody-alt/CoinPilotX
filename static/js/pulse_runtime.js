/* The two primitives every PulseSoc page script assumes exist: `pulseApi` and
 * `toast`.
 *
 * They lived inline in `pulse_social_shell` for as long as the shell was the
 * only document that ever wrapped a page script. It is not any more --
 * `marketplace_storefront.public_document` wraps the same cart markup for a
 * visitor -- and a script that calls a bare `pulseApi()` throws a
 * ReferenceError in the frame that never defined it. The page renders, the
 * first `load()` dies, and the visitor gets a cart that is permanently empty
 * with nothing in the UI to say so.
 *
 * So: one file, loaded by both wrappers, rather than a second copy in the
 * second wrapper. These two have a wire contract with the server -- `pulseApi`
 * reads `error_code` and not `code`, and it treats `{ok:false}` on a 200 as a
 * failure -- and a fork is how one half of the app silently stops honouring
 * half of it.
 *
 * Classic script, no `defer`, assigned onto `window` on purpose. Page scripts
 * reference these by bare name; a `const` at the top level of a classic script
 * would satisfy that too, but only for scripts that run later in the same
 * document. Going through `window` means a module, an inline handler, or
 * anything else that does not share the script scope resolves them the same
 * way.
 */
(function () {
  "use strict";

  window.pulseApi = async function pulseApi(url, opts = {}) {
    const isForm = opts.body instanceof FormData;
    const r = await fetch(url, {
      credentials: "same-origin",
      cache: "no-store",
      headers: isForm ? {} : { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    });
    const d = await r.json().catch(() => ({
      ok: false,
      message: "Server returned an unreadable response.",
    }));
    if (!r.ok || d.ok === false) {
      const err = new Error(d.message || d.error || "Request failed.");
      Object.assign(err, d);
      throw err;
    }
    return d;
  };

  /* The shell ships a `<div class="toast" id="toast">` and the CSS that shows
   * it. The public document ships neither, and it should not have to -- a
   * wrapper's job is the frame, not the error channel of whatever body it is
   * given. So the node is made here when it is missing, and styled here only
   * in that case: where the shell did provide one, its stylesheet still owns
   * the look and nothing below touches it. */
  function toastNode() {
    let node = document.getElementById("toast");
    if (node) return node;
    node = document.createElement("div");
    node.id = "toast";
    node.className = "toast";
    node.setAttribute("role", "status");
    node.setAttribute("aria-live", "polite");
    // `pointer-events:none` because this is an announcement, not a control:
    // every writer of this node sets `textContent`, so it never holds anything
    // clickable, and it is parked over the bottom of the viewport where real
    // controls live. Without it the toast raised by adding one product eats the
    // click on the next product's Add to cart for its whole 3.2s.
    node.style.cssText =
      "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:40;" +
      "display:none;min-width:min(92vw,420px);border:1px solid rgba(110,223,246,.22);" +
      "border-radius:12px;background:#071321;color:#f2fbff;padding:12px;" +
      "font:inherit;pointer-events:none;box-shadow:0 18px 60px rgba(0,0,0,.4)";
    node.dataset.pulseRuntimeToast = "1";
    document.body.appendChild(node);
    return node;
  }

  window.toast = function toast(message) {
    const node = toastNode();
    if (!node) return;
    node.textContent = message;
    node.classList.add("show");
    // `.toast.show{display:block}` is the shell's rule and does not reach a
    // node this file created, so that one gets told directly.
    if (node.dataset.pulseRuntimeToast) node.style.display = "block";
    clearTimeout(node._pulseToastTimer);
    node._pulseToastTimer = setTimeout(function () {
      node.classList.remove("show");
      if (node.dataset.pulseRuntimeToast) node.style.display = "none";
    }, 3200);
  };
})();
