import { useCallback, useEffect, useRef, useState } from "react";
import { AccessibilityInfo } from "react-native";
import { useBottomNavVisibility } from "../navigation/BottomNavVisibility";
import { immersiveNavigatorEnabled } from "./flags";

/** Delay between a swipe settling and the dock hiding. */
export const IMMERSIVE_SETTLE_DELAY_MS = 1200;

/**
 * Immersive navigator behavior for spatial surfaces.
 *
 * The dock stays visible until the FIRST completed horizontal swipe settles;
 * ~1.2s later it hides. It reveals again on any of: an explicit reveal call
 * (safe-area tap / upward edge gesture / child surface closing), the screen
 * regaining focus, or a screen reader being active (in which case it never
 * hides at all). All behavior is inert unless `immersiveNavigatorEnabled()`.
 */
export function useImmersiveNavigator(surfaceFocused: boolean) {
  const { setBottomNavHidden, showBottomNav, hidden } = useBottomNavVisibility();
  const [screenReaderActive, setScreenReaderActive] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const swipesRef = useRef(0);

  useEffect(() => {
    AccessibilityInfo.isScreenReaderEnabled().then(setScreenReaderActive).catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("screenReaderChanged", setScreenReaderActive);
    return () => sub.remove();
  }, []);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const reveal = useCallback(() => {
    clearTimer();
    showBottomNav();
  }, [clearTimer, showBottomNav]);

  /** Call when a horizontal swipe settles on a page. */
  const notifySwipeSettled = useCallback(() => {
    if (!immersiveNavigatorEnabled() || screenReaderActive) return;
    swipesRef.current += 1;
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      if (swipesRef.current >= 1) setBottomNavHidden(true);
    }, IMMERSIVE_SETTLE_DELAY_MS);
  }, [clearTimer, screenReaderActive, setBottomNavHidden]);

  // Losing focus (child surface pushed) or regaining it always reveals the
  // dock, and unmount cleans up so no timer outlives the surface.
  useEffect(() => {
    if (surfaceFocused) {
      swipesRef.current = 0;
      reveal();
    }
    return reveal;
  }, [surfaceFocused, reveal]);

  // Screen reader turning on mid-session reveals immediately.
  useEffect(() => {
    if (screenReaderActive) reveal();
  }, [screenReaderActive, reveal]);

  return { notifySwipeSettled, reveal, hidden };
}
