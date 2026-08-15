/**
 * Pure math for the spatial pager, kept free of React/RN imports so the
 * index/threshold behavior is unit-testable.
 */

/** Settled page index for a horizontal offset. Clamped to valid range. */
export function settledIndexForOffset(offsetX: number, pageWidth: number, pageCount: number): number {
  if (pageWidth <= 0 || pageCount <= 0) return 0;
  const raw = Math.round(offsetX / pageWidth);
  return Math.min(Math.max(raw, 0), pageCount - 1);
}

/**
 * Depth treatment for a page while the pager is mid-drag.
 *
 * `position` is the page's index minus the current fractional scroll index:
 * 0 when centered, ±1 when fully off-screen. At rest (|position| = 0 or 1)
 * both values are identity/invisible, which is what guarantees "no adjacent
 * post visible at rest" — the neighbor only picks up scale/opacity while it
 * is partially on screen.
 */
export function depthForPosition(position: number, reduceMotion: boolean): { scale: number; opacity: number } {
  if (reduceMotion) return { scale: 1, opacity: 1 };
  const distance = Math.min(Math.abs(position), 1);
  return {
    // Restrained: 4% shrink at the extreme, back to 1 when settled.
    scale: 1 - 0.04 * Math.sin(distance * Math.PI),
    opacity: 1 - 0.18 * Math.sin(distance * Math.PI)
  };
}

/**
 * Whether a page should be mounted under current ±1 virtualization.
 */
export function shouldRenderPage(index: number, settledIndex: number): boolean {
  return Math.abs(index - settledIndex) <= 1;
}

/**
 * Immersive-navigator decision: hide the dock only after the FIRST completed
 * swipe has settled, and only once the settle delay has elapsed.
 */
export function shouldHideDockAfterSettle(completedSwipes: number, msSinceSettle: number, settleDelayMs: number): boolean {
  return completedSwipes >= 1 && msSinceSettle >= settleDelayMs;
}
