import { useCallback, useEffect, useState } from "react";
import { AccessibilityInfo } from "react-native";
import { useBottomNavVisibility } from "../navigation/BottomNavVisibility";
import { immersiveNavigatorEnabled } from "./flags";
import { navigatorIntentForSettle, type PageSettle } from "./navigatorVisibility";

/**
 * Directional navigator visibility for the Reels player.
 *
 * The dock's visibility is a function of the direction of a *committed touch*
 * page transition, and of nothing else: swiping back (toward the previous reel)
 * hides it, swiping forward reveals it, and everything else leaves it alone.
 * The decision table lives in `navigatorVisibility.ts`; this hook is the
 * plumbing around it — flag gating, the accessibility override, and the reveals
 * that are not a function of any gesture.
 *
 * This replaced a count-and-delay model (hide ~1.2s after the first settled
 * swipe, in any direction). Two things were wrong with it. It could not tell a
 * finger from a tilt commit, so the optional motion controller silently drove
 * navigation chrome — which §5 forbids outright. And a hide that lands a second
 * after the gesture that caused it is unattributable: users read it as the app
 * doing something on its own rather than as a response to what they just did.
 * Acting on the settle itself makes the cause legible and makes the reverse
 * gesture an obvious undo.
 *
 * Two asymmetries are deliberate, and both exist so nobody gets stranded behind
 * an invisible dock:
 *
 *   - the flag gates *hiding*, not revealing. Rollback level 1 turns the
 *     immersive behavior off; it must not also disable the recovery path that
 *     gets a user out of a dock some other build hid.
 *   - a screen reader drops hides but lets reveals through. Navigation must
 *     never leave the accessibility tree, but it may re-enter it.
 *
 * Reels is the only caller. Home Feed mounted this hook while spatial paging was
 * being tested there; that was withdrawn, and the restored Feed uses the legacy
 * scroll-driven dock behavior with nothing from this module. Callers pass
 * `false` for `surfaceFocused` while a child sheet is open, which reveals the
 * dock — that is how comments, sharing and the reel menu keep navigation on
 * screen without waiting for a gesture.
 */
export function useImmersiveNavigator(surfaceFocused: boolean) {
  const { setBottomNavHidden, showBottomNav, hidden } = useBottomNavVisibility();
  const [screenReaderActive, setScreenReaderActive] = useState(false);

  useEffect(() => {
    AccessibilityInfo.isScreenReaderEnabled().then(setScreenReaderActive).catch(() => undefined);
    const sub = AccessibilityInfo.addEventListener("screenReaderChanged", setScreenReaderActive);
    return () => sub.remove();
  }, []);

  /**
   * The recovery affordance: a tap on unclaimed media, a child surface closing,
   * or re-entering the surface. Intentionally not flag-gated — see above.
   */
  const reveal = useCallback(() => {
    showBottomNav();
  }, [showBottomNav]);

  /** Call when the pager has settled on a page. */
  const notifyPageSettled = useCallback(
    (settle: PageSettle) => {
      if (!immersiveNavigatorEnabled()) return;
      const intent = navigatorIntentForSettle(settle);
      if (intent === "unchanged") return;
      if (intent === "reveal") {
        showBottomNav();
        return;
      }
      if (screenReaderActive) return;
      setBottomNavHidden(true);
    },
    [screenReaderActive, setBottomNavHidden, showBottomNav]
  );

  // Entering the surface reveals; so does leaving it, including unmount, so no
  // screen can strand the dock off-screen for whatever comes next.
  useEffect(() => {
    if (surfaceFocused) reveal();
    return reveal;
  }, [surfaceFocused, reveal]);

  // A screen reader turning on mid-session reveals immediately: the user must
  // not have to find an invisible control to get navigation back.
  useEffect(() => {
    if (screenReaderActive) reveal();
  }, [screenReaderActive, reveal]);

  return { notifyPageSettled, reveal, hidden };
}
