type ReelsReselectHandler = () => void | Promise<void>;

// Single-owner registry mirroring homeReselect. ReelsScreen registers a handler
// on mount; the bottom-nav Reels button invokes it when the user double-taps the
// tab while Reels is already the active route (scroll-to-top + refresh of the
// currently-selected category). Kept module-level (not context) so the tab bar
// can trigger it without threading a ref through the navigator tree.
let handler: ReelsReselectHandler | null = null;

export function registerReelsReselectHandler(nextHandler: ReelsReselectHandler) {
  handler = nextHandler;
  return () => {
    if (handler === nextHandler) handler = null;
  };
}

export function triggerReelsReselect() {
  if (!handler) return false;
  Promise.resolve(handler()).catch(() => undefined);
  return true;
}
