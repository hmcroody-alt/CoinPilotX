/**
 * Pure, unit-testable lifecycle for the in-app foreground notification banner.
 *
 * Root cause of "the banner stays stuck at the top until I dismiss it": the
 * foreground push presentation had no owned auto-dismiss path, and nothing
 * guaranteed a *new* notification replaced (rather than stacked on top of) the
 * previous one. Centralizing the state transitions here — as pure functions —
 * lets the React component stay a thin shell around `setTimeout`, and makes the
 * two mandated invariants testable without a renderer:
 *
 *   1. A temporary banner auto-dismisses after an appropriate duration
 *      (extended, never removed, for screen-reader users).
 *   2. Presenting a new banner supersedes the old one and invalidates the old
 *      banner's pending timer, so a stale timer can never dismiss the wrong
 *      banner and no banner is ever left stuck.
 *
 * Only banners explicitly flagged `persistent` (intentional system states) opt
 * out of auto-dismiss.
 */

export const DEFAULT_BANNER_MS = 4500;
export const MIN_BANNER_MS = 2500;
/** Screen-reader users get materially longer to perceive the banner. */
export const ACCESSIBLE_BANNER_MS = 9000;

export type BannerNotification = {
  id: string;
  title: string;
  body?: string;
  /** Deep-link target routed through notificationRouting on tap. */
  target?: string;
  /** Intentional persistent system state — opts out of auto-dismiss. */
  persistent?: boolean;
  /** Explicit duration override for temporary banners. */
  durationMs?: number;
};

export type BannerState = {
  banner: BannerNotification | null;
  /**
   * Monotonic token identifying the currently-presented banner. Every present
   * bumps it; a timer captured for an older token is a no-op when it fires.
   */
  token: number;
};

export type DismissReason = "timeout" | "swipe" | "tap" | "replaced" | "unmount";

export function initialBannerState(): BannerState {
  return { banner: null, token: 0 };
}

/** Present a banner, superseding any current one and invalidating its timer. */
export function presentBanner(state: BannerState, banner: BannerNotification): BannerState {
  return { banner, token: state.token + 1 };
}

/**
 * Milliseconds before a banner should auto-dismiss, or `null` for a persistent
 * banner that must not auto-dismiss.
 */
export function resolveAutoDismissMs(
  banner: BannerNotification,
  options: { screenReaderEnabled?: boolean } = {}
): number | null {
  if (banner.persistent) return null;
  const base = Math.max(MIN_BANNER_MS, Math.round(banner.durationMs ?? DEFAULT_BANNER_MS));
  if (options.screenReaderEnabled) return Math.max(base, ACCESSIBLE_BANNER_MS);
  return base;
}

/**
 * Dismiss the active banner. When `token` is provided (a timer firing), the
 * dismissal only applies if it still matches the presented banner — a stale
 * timer for a superseded banner is ignored so it can't clear a newer one.
 */
export function dismissBanner(state: BannerState, token?: number): BannerState {
  if (token !== undefined && token !== state.token) return state;
  if (!state.banner) return state;
  return { banner: null, token: state.token };
}
