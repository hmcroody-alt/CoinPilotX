/**
 * The controller: where the three pure modules and the app meet.
 *
 * `immersiveSession` decides what is true, `immersiveContinuation` fetches,
 * `immersiveWindow` decides what is mounted — and none of them knows about
 * React, time, or each other. This hook is the only place that knows about all
 * of it, which is why it is the only place in the engine with a race to get
 * wrong. The tests for it are therefore almost entirely about concurrency.
 *
 * ## The three races, named
 *
 * 1. **Double fetch.** Swiping twice near the end asks "should I continue?"
 *    twice, and both answers are yes, because the first page has not landed. Two
 *    identical requests then return two identical pages; the second dedupes
 *    entirely away, so the bug is invisible in the queue and visible only as
 *    doubled bandwidth on a phone plan. An in-flight latch, not a debounce.
 *
 * 2. **Stale application.** A page that started when the cursor was at 8 must
 *    be appended to the session as it is when the page *lands*, not to the
 *    session that was captured in the closure that requested it. Applying the
 *    captured one silently discards every swipe made during the round trip —
 *    the user watches the feed jump backwards.
 *
 * 3. **Retry storm.** A failed continuation leaves `shouldContinueImmersive`
 *    still true, so the very next render asks again, fails again, and asks
 *    again. On a train that is a tight loop against production. The failure is
 *    latched and only cleared by a deliberate retry or by the cursor moving.
 *
 * ## What it does not do
 *
 * It does not pause anybody else's media. `claimMediaPlayback` does that, and
 * it may refuse the claim outright because a call is up — which is the correct
 * outcome and is handled rather than overridden. The engine is a participant in
 * the existing priority ladder, never a rival to it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { fetchImmersivePage } from "./immersiveContinuation";
import {
  ImmersiveEntry,
  ImmersiveOrigin,
  ImmersiveSession,
  appendImmersivePage,
  beginImmersiveSession,
  currentImmersiveEntry,
  entryKey,
  moveImmersiveCursor,
  shouldContinueImmersive
} from "./immersiveSession";
import {
  ImmersiveSlot,
  ImmersiveWindowOptions,
  activeImmersiveSlot,
  immersivePreloadKeys,
  immersiveWindow
} from "./immersiveWindow";
import type { ProfileTargetInput, NativeProfileTarget } from "../api/profileTarget";

export type UseImmersiveSessionInput = {
  origin: ImmersiveOrigin;
  /** Media the origin already holds, so the first swipes need no network. */
  seed?: readonly ImmersiveEntry[];
  /** Reels lane, when the origin was Reels. */
  lane?: string;
  /** Whose profile, when the origin was a profile. */
  profile?: ProfileTargetInput | NativeProfileTarget;
  window?: ImmersiveWindowOptions;
  /** How many items from the end to start fetching. */
  lookahead?: number;
};

export type UseImmersiveSessionResult = {
  session: ImmersiveSession;
  slots: ImmersiveSlot[];
  activeSlot: ImmersiveSlot | null;
  activeEntry: ImmersiveEntry | null;
  preloadKeys: string[];
  /** A continuation is in flight. */
  loading: boolean;
  /** The last continuation failed. Cleared by `retry` or by moving the cursor. */
  error: unknown;
  goTo: (index: number) => void;
  next: () => void;
  previous: () => void;
  retry: () => void;
  /** The row the origin should scroll back to (§25). Also releases playback. */
  close: () => ImmersiveEntry;
};

/**
 * The coordinator kind the engine claims under.
 *
 * `viewer`, deliberately, rather than a new kind of its own. The engine *is*
 * the full-screen viewer — adding an `immersive` rung would mean choosing a
 * number relative to `call`, `live` and `reel`, and the only safe choice is the
 * number `viewer` already has. A new rung is also how a media surface acquires
 * the ability to outrank a call by accident, which is the realtime-audio
 * violation the audit warned about wearing a different costume.
 */
const IMMERSIVE_PLAYBACK_KIND = "viewer" as const;

export function useImmersiveSession(input: UseImmersiveSessionInput): UseImmersiveSessionResult {
  const { origin, seed, lane, profile, lookahead } = input;

  const [session, setSession] = useState<ImmersiveSession>(() => beginImmersiveSession(origin, seed || []));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  /**
   * The latch. A ref rather than state because it must be true *synchronously*,
   * before the render that would ask again: a state flag set in an effect is
   * one render too late, which is precisely the window the second fetch fires
   * in.
   */
  const inFlight = useRef(false);
  const mounted = useRef(true);
  /** The session as of now, for applying a page that started some renders ago. */
  const latest = useRef(session);
  latest.current = session;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const continueNow = useCallback(async () => {
    if (inFlight.current) return;
    const current = latest.current;
    if (!current.canContinue) return;
    inFlight.current = true;
    setLoading(true);
    const page = await fetchImmersivePage({
      source: current.origin.source,
      cursor: current.nextCursor,
      lane,
      profile,
      limit: undefined
    });
    inFlight.current = false;
    if (!mounted.current) return;
    setLoading(false);
    if (page.error) {
      setError(page.error);
      return;
    }
    setError(null);
    // Applied to the session as it is *now*. The closure's copy is however many
    // swipes out of date the round trip took.
    setSession((live) =>
      appendImmersivePage(live, page.entries, { nextCursor: page.nextCursor, exhausted: page.exhausted })
    );
  }, [lane, profile]);

  useEffect(() => {
    if (error) return; // Latched: a failure does not re-fire on the next render.
    if (!shouldContinueImmersive(session, lookahead)) return;
    continueNow().catch(() => undefined);
  }, [session, error, lookahead, continueNow]);

  const slots = useMemo(() => immersiveWindow(session, input.window), [session, input.window]);
  const activeSlot = useMemo(() => activeImmersiveSlot(session, input.window), [session, input.window]);
  const preloadKeys = useMemo(() => immersivePreloadKeys(session, input.window), [session, input.window]);
  const activeEntry = useMemo(() => currentImmersiveEntry(session), [session]);

  /**
   * One claim per item, keyed on the item.
   *
   * Claiming on every render would hand the coordinator a new owner object for
   * the same media and re-notify every subscriber on every frame of a scroll.
   * The claim is an event — "this item is now the one playing" — so it fires
   * when the identity of that item changes and at no other time.
   */
  const activeKey = activeSlot?.key || "";
  useEffect(() => {
    if (!activeKey) return;
    let cancelled = false;
    claimMediaPlayback({
      id: `immersive:${activeKey}`,
      kind: IMMERSIVE_PLAYBACK_KIND,
      pause: () => undefined
    }).catch(() => undefined);
    return () => {
      cancelled = true;
      void cancelled;
      releaseMediaPlayback(`immersive:${activeKey}`).catch(() => undefined);
    };
  }, [activeKey]);

  const goTo = useCallback((index: number) => {
    setError(null); // Moving is a fresh intent; it clears the latch.
    setSession((live) => moveImmersiveCursor(live, index));
  }, []);

  const next = useCallback(() => {
    setError(null);
    setSession((live) => moveImmersiveCursor(live, live.cursor + 1));
  }, []);

  const previous = useCallback(() => {
    setError(null);
    setSession((live) => moveImmersiveCursor(live, live.cursor - 1));
  }, []);

  const retry = useCallback(() => {
    setError(null);
  }, []);

  /**
   * §25. Returns the row the origin should scroll to, which is the *tapped*
   * entry rather than wherever the swiping ended — the origin's own list does
   * not contain item 40 of somebody else's media and cannot scroll to it.
   */
  const close = useCallback(() => {
    const target = latest.current.origin.entry;
    const key = entryKey(currentImmersiveEntry(latest.current));
    if (key) releaseMediaPlayback(`immersive:${key}`).catch(() => undefined);
    return target;
  }, []);

  return { session, slots, activeSlot, activeEntry, preloadKeys, loading, error, goTo, next, previous, retry, close };
}
