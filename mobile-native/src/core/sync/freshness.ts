/**
 * What a surface should actually show, given what it has and what the network
 * is doing.
 *
 * Every offline-capable screen has to answer the same question and they had all
 * been answering it separately, in JSX, with a chain of ternaries built up one
 * bug report at a time. The chain is where the two characteristic failures come
 * from: the error branch and the empty branch both render (so the reader is told
 * "something went wrong" and "there is nothing here" at once, and cannot tell
 * which is true), and a failed refresh wipes a screenful of perfectly good
 * cached content back to a spinner.
 *
 * This module is a pure function, deliberately. It takes no dependency on
 * connectivity or the cache; the caller passes what it has. That makes every
 * combination — including the ones that are awkward to reach on a device, like
 * "offline with content cached eight days ago while a refresh is in flight" —
 * a one-line test rather than a field report.
 *
 * WHY "OFFLINE" IS NOT A MEMBER OF THIS UNION
 *
 * Because offline is not an error and not a state of the surface. A surface
 * holding cached posts while the device is offline is in exactly the same state
 * as one holding cached posts while a refresh runs: it is showing content. What
 * differs is the *notice* above it, which is why connectivity feeds `notice`
 * and never `state`. Encoding offline as a surface state is how screens end up
 * blanking themselves when the tunnel drops.
 */

import type { ConnectivityState } from "../connectivity";

/**
 * The four things a surface can be, and there are only four.
 *
 * - `LOADING` — nothing to show yet and something is on its way. The only state
 *   that may render a spinner in place of content.
 * - `CONTENT` — there is something to show. Live or cached; the distinction
 *   belongs in the notice, not here.
 * - `EMPTY` — a successful load established there is genuinely nothing.
 * - `ERROR` — a load failed and there is nothing cached to fall back to.
 *
 * `EMPTY` and `ERROR` are mutually exclusive by construction rather than by
 * convention, which is the point: "there are no posts" is a claim about the
 * server's answer, and a request that failed did not produce one.
 */
export type SurfaceState = "LOADING" | "CONTENT" | "EMPTY" | "ERROR";

/**
 * The banner above the content, if any.
 *
 * - `NONE` — content is live and current; say nothing.
 * - `REFRESHING` — showing content while revalidating behind it.
 * - `OFFLINE` — showing content the device cannot currently check.
 * - `STALE` — online, but what is shown is old enough to be worth flagging.
 */
export type FreshnessNotice = "NONE" | "REFRESHING" | "OFFLINE" | "STALE";

export type SurfacePresentation = {
  state: SurfaceState;
  notice: FreshnessNotice;
  /**
   * Age of the displayed content, or null when unknown — which is a real and
   * distinct answer, not a zero. An entry written before cache entries carried
   * timestamps has no age, and a surface must say "cached" rather than invent
   * "updated just now". See the note on `storedAt` in `src/core/cache.ts`.
   */
  ageMs: number | null;
  /** True only when a spinner may replace content rather than sit beside it. */
  showSpinner: boolean;
  /** True when the reader is looking at something the network has not confirmed. */
  fromCache: boolean;
};

export type SurfaceInput = {
  /** How many items the surface currently holds, cached or live. */
  itemCount: number;
  /** Age of those items in ms; null when unknown, undefined when they are live. */
  ageMs?: number | null;
  /** True once any load — successful or not — has completed for this surface. */
  hasLoaded: boolean;
  /** True while a request is in flight. */
  isLoading: boolean;
  /** The last error, if the most recent attempt failed. */
  error?: unknown;
  connectivity: ConnectivityState;
  /** Above this, online content is called out as stale. */
  staleAfterMs?: number;
};

/**
 * Ten minutes. Long enough that a user scrolling a feed is not nagged about
 * content they watched arrive, short enough that a screen resumed from the app
 * switcher the next morning admits it is showing yesterday.
 */
export const DEFAULT_STALE_AFTER_MS = 10 * 60_000;

export function surfacePresentation(input: SurfaceInput): SurfacePresentation {
  const hasContent = input.itemCount > 0;
  const ageMs = input.ageMs === undefined ? null : input.ageMs;
  const offline = input.connectivity === "offline";

  // Content wins over every other consideration, including an error. A refresh
  // that failed has not invalidated what is already on screen — it has only
  // failed to replace it — and throwing it away costs the reader something real
  // in exchange for telling them something they can see in the notice anyway.
  if (hasContent) {
    const notice: FreshnessNotice = offline
      ? "OFFLINE"
      : input.isLoading
        ? "REFRESHING"
        : isStale(ageMs, input.staleAfterMs)
          ? "STALE"
          : "NONE";
    return {
      state: "CONTENT",
      notice,
      ageMs,
      showSpinner: false,
      // Live content is handed in with `ageMs: undefined`; anything with a
      // recorded age came off disk, including an age we could not recover.
      fromCache: input.ageMs !== undefined || offline
    };
  }

  // Nothing to show. Only now does the difference between "failed" and "empty"
  // matter, and only one of them can be true.
  if (input.error) {
    return {
      state: "ERROR",
      notice: offline ? "OFFLINE" : "NONE",
      ageMs: null,
      showSpinner: false,
      fromCache: false
    };
  }

  if (input.isLoading || !input.hasLoaded) {
    return { state: "LOADING", notice: "NONE", ageMs: null, showSpinner: true, fromCache: false };
  }

  // A completed load with no error and no items. This is the only path that may
  // claim there is nothing here, because it is the only one that asked and was
  // told so.
  return { state: "EMPTY", notice: offline ? "OFFLINE" : "NONE", ageMs: null, showSpinner: false, fromCache: false };
}

function isStale(ageMs: number | null, staleAfterMs = DEFAULT_STALE_AFTER_MS): boolean {
  // Unknown age is not stale. It is unknown, and a surface that treats every
  // legacy cache entry as stale would flag a banner on content that may have
  // been written a second ago.
  return ageMs !== null && ageMs > staleAfterMs;
}

/**
 * The age broken into a unit and a count, for the caller to translate.
 *
 * Not a formatted string: "12m ago" is English, and word order, pluralisation
 * and the position of the number all move between locales. Returning the parts
 * keeps this module pure and keeps the sentence in the locale files where the
 * i18n gate can see it.
 *
 * Null means the age is unknown, so the caller renders nothing rather than a
 * placeholder that reads like a measurement.
 */
export type AgeDescriptor = { unit: "now" | "minutes" | "hours" | "days"; value: number };

export function describeAge(ageMs: number | null): AgeDescriptor | null {
  if (ageMs === null || !Number.isFinite(ageMs) || ageMs < 0) return null;
  const minutes = Math.floor(ageMs / 60_000);
  if (minutes < 1) return { unit: "now", value: 0 };
  if (minutes < 60) return { unit: "minutes", value: minutes };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { unit: "hours", value: hours };
  return { unit: "days", value: Math.floor(hours / 24) };
}
