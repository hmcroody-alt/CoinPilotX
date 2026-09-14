/**
 * The React binding. One hook per surface, and the only thing a screen needs.
 *
 * Screens hand over what they already know -- their list, which item is active,
 * whether they are focused -- and get back a scroll handler and a readiness
 * lookup. They do not learn what a priority or a cache key is, which is what
 * keeps the policy in one place instead of five.
 */

import { NavigationContext } from "@react-navigation/native";
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState } from "react-native";
import type { NativeScrollEvent, NativeSyntheticEvent } from "react-native";

import { mediaCacheKey, mediaIdentityOf } from "./mediaIdentity";
import type { MediaDescriptor, MediaRendition } from "./mediaIdentity";
import type { MediaSurface, ScrollDirection, ScrollVelocityBand } from "./mediaPrefetchPlanner";
import { sharedMediaPrefetcher } from "./mediaPrefetcher";
import type { MediaPrefetcher } from "./mediaPrefetcher";
import { createScrollTracker, trackScroll } from "./mediaViewportSignals";

export type UseMediaPrefetchInput = {
  surface: MediaSurface;
  /** Ordered as rendered. `null` entries are fine -- text posts have no media. */
  items: readonly (MediaDescriptor | null | undefined)[];
  activeIndex: number;
  /** The screen is focused and the app is foregrounded. */
  active: boolean;
  dataSaver?: boolean;
  /**
   * For pagers. A full-screen pager has no meaningful scroll velocity -- it
   * snaps -- so Reels and Statuses tell us which way the user swiped instead of
   * having us infer it from an offset that is always either 0 or one page.
   */
  direction?: ScrollDirection;
  prefetcher?: MediaPrefetcher;
};

export type UseMediaPrefetchResult = {
  onScroll: (event: NativeSyntheticEvent<NativeScrollEvent>) => void;
  /** Call from onMomentumScrollEnd so the window re-opens once the fling stops. */
  onScrollSettled: () => void;
  velocity: ScrollVelocityBand;
  direction: ScrollDirection;
  isWarm: (media: MediaDescriptor | null | undefined, rendition: MediaRendition) => boolean;
};

/**
 * Which way a pager is moving, from its index alone.
 *
 * Holds the last real direction when the index does not change, rather than
 * decaying to "idle". A user who pauses on a reel is still, as far as the next
 * swipe is concerned, going the way they were going -- resetting to idle would
 * re-plan the window symmetrically every time they stopped to watch something.
 */
export function usePagerDirection(activeIndex: number): ScrollDirection {
  const previous = useRef(activeIndex);
  const [direction, setDirection] = useState<ScrollDirection>("forward");

  useEffect(() => {
    const last = previous.current;
    previous.current = activeIndex;
    if (activeIndex === last) return;
    setDirection(activeIndex > last ? "forward" : "backward");
  }, [activeIndex]);

  return direction;
}

/**
 * Whether the app is foregrounded, for §10.
 *
 * Read inside a component rather than at module scope on purpose: a module-level
 * `AppState.currentState` is captured during launch, when it is still
 * "inactive", so a singleton that seeded itself from it would consider the app
 * backgrounded for the rest of the process.
 */
export function useAppForegrounded(): boolean {
  const [foregrounded, setForegrounded] = useState(() => AppState.currentState === "active");

  useEffect(() => {
    setForegrounded(AppState.currentState === "active");
    const subscription = AppState.addEventListener("change", (next) => setForegrounded(next === "active"));
    return () => subscription.remove();
  }, []);

  return foregrounded;
}

/**
 * Whether this screen owns the current route, for §9.
 *
 * `useIsFocused` would be the obvious call and is the wrong one here. It throws
 * when there is no navigation container above it, and a large share of this
 * app's screens take `navigation` as a plain prop and are rendered bare in
 * tests -- so adopting it inside a shared media hook would turn an unrelated
 * suite red the moment a screen started prefetching. Reading the context
 * directly lets the hook degrade to "focused" when there is no navigator to ask,
 * which is the correct answer for a screen that is the only thing mounted.
 */
export function useRouteFocused(): boolean {
  const navigation = useContext(NavigationContext);
  const [focused, setFocused] = useState(() => (navigation ? navigation.isFocused() : true));

  useEffect(() => {
    if (!navigation) {
      setFocused(true);
      return;
    }
    setFocused(navigation.isFocused());
    const stopFocus = navigation.addListener("focus", () => setFocused(true));
    const stopBlur = navigation.addListener("blur", () => setFocused(false));
    return () => {
      stopFocus();
      stopBlur();
    };
  }, [navigation]);

  return focused;
}

export function useMediaPrefetch(input: UseMediaPrefetchInput): UseMediaPrefetchResult {
  const { surface, items, activeIndex, active, dataSaver = false } = input;
  const prefetcher = useMemo(() => input.prefetcher ?? sharedMediaPrefetcher(), [input.prefetcher]);

  const tracker = useRef(createScrollTracker());
  const [motion, setMotion] = useState<{ velocity: ScrollVelocityBand; direction: ScrollDirection }>({
    velocity: "idle",
    direction: "idle"
  });

  const onScroll = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const offset = event?.nativeEvent?.contentOffset?.y ?? 0;
    const next = trackScroll(tracker.current, offset, Date.now());
    const changed = next.band !== tracker.current.band || next.direction !== tracker.current.direction;
    tracker.current = next;
    // Only a band change re-renders. Setting state on every scroll frame would
    // make the prefetcher the most expensive thing in the scroll, which is an
    // impressive way to lose a performance mission.
    if (changed) setMotion({ velocity: next.band, direction: next.direction });
  }, []);

  const onScrollSettled = useCallback(() => {
    tracker.current = { ...tracker.current, band: "idle" };
    setMotion((current) => (current.velocity === "idle" ? current : { ...current, velocity: "idle" }));
  }, []);

  useEffect(() => {
    if (!active) {
      prefetcher.release(surface);
      return;
    }
    prefetcher.apply(surface, {
      items,
      activeIndex,
      direction: input.direction ?? motion.direction,
      velocity: motion.velocity,
      dataSaver,
      active: true
    });
  }, [prefetcher, surface, items, activeIndex, active, dataSaver, motion.velocity, motion.direction, input.direction]);

  // Unmount is separate from blur on purpose: a blurred screen that is still
  // mounted keeps its cache pins, so returning to it repaints from cache rather
  // than re-fetching what was already there.
  useEffect(() => () => prefetcher.release(surface), [prefetcher, surface]);

  const isWarm = useCallback(
    (media: MediaDescriptor | null | undefined, rendition: MediaRendition) => {
      const identity = mediaIdentityOf(media);
      if (!identity) return false;
      return prefetcher.isWarm(mediaCacheKey(identity, rendition));
    },
    [prefetcher]
  );

  return { onScroll, onScrollSettled, velocity: motion.velocity, direction: motion.direction, isWarm };
}
