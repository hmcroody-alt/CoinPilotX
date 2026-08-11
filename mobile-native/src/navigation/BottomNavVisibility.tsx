import { NavigationContext } from "@react-navigation/native";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Keyboard, NativeScrollEvent, NativeSyntheticEvent } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { BOTTOM_NAV_ACTIVE_PLAYER_CLEARANCE, BOTTOM_NAV_CONTENT_CLEARANCE, BOTTOM_NAV_UNDOCKED_PADDING } from "./bottomNavMetrics";

type BottomNavVisibilityContextValue = {
  /**
   * Whether a dock is actually on screen beneath this subtree.
   *
   * True only under the real provider, which is mounted inside the tab
   * navigator. Several screens (Search, Groups, Marketplace, Status) are
   * registered both as a tab and as a pushed stack destination; a pushed
   * instance covers the dock entirely, so reserving clearance for it there
   * would leave a band of dead space above the home indicator. Reading this
   * flag lets one component get it right in both places without a prop.
   */
  docked: boolean;
  hidden: boolean;
  pinned: boolean;
  keyboardVisible: boolean;
  miniPlayerVisible: boolean;
  setBottomNavHidden: (hidden: boolean) => void;
  setBottomNavPinned: (reason: string, pinned: boolean) => void;
  setMiniPlayerVisible: (visible: boolean) => void;
  showBottomNav: () => void;
};

type ScrollVisibilityOptions = {
  enabled?: boolean;
  hideThreshold?: number;
  topRevealY?: number;
  minimumScrollableDistance?: number;
};

/**
 * Vertical space a scrollable surface reserves so its last content clears the
 * floating bottom navigation dock. The dock draws its own safe-area padding, so
 * callers add this to `Math.max(insets.bottom, 12)` instead of hardcoding a
 * device-specific total (which was the root cause of the Home composer/dock
 * overlap: a fixed 172pt that ignored per-device safe-area insets).
 *
 * Re-exported from `bottomNavMetrics` rather than defined here: the value is now
 * derived from the dock's own layout constants so the two cannot drift apart.
 * Importing it from either module is equivalent.
 */
export { BOTTOM_NAV_CONTENT_CLEARANCE } from "./bottomNavMetrics";

const BottomNavVisibilityContext = createContext<BottomNavVisibilityContextValue | null>(null);

export function BottomNavVisibilityProvider({ children }: { children: ReactNode }) {
  const [requestedHidden, setRequestedHidden] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const [miniPlayerVisible, setMiniPlayerVisible] = useState(false);
  const [pinVersion, setPinVersion] = useState(0);
  const pinnedReasons = useRef(new Set<string>());

  useEffect(() => {
    const showSub = Keyboard.addListener("keyboardDidShow", () => setKeyboardVisible(true));
    const hideSub = Keyboard.addListener("keyboardDidHide", () => setKeyboardVisible(false));
    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  const setBottomNavHidden = useCallback((hidden: boolean) => {
    setRequestedHidden(Boolean(hidden));
  }, []);

  const setBottomNavPinned = useCallback((reason: string, pinned: boolean) => {
    if (!reason) return;
    const reasons = pinnedReasons.current;
    const hadReason = reasons.has(reason);
    if (pinned) reasons.add(reason);
    else reasons.delete(reason);
    if (hadReason !== pinned) setPinVersion((version) => version + 1);
  }, []);

  const showBottomNav = useCallback(() => {
    setRequestedHidden(false);
  }, []);

  const pinned = pinVersion >= 0 && pinnedReasons.current.size > 0;
  const hidden = keyboardVisible || (!pinned && requestedHidden);

  const value = useMemo<BottomNavVisibilityContextValue>(
    () => ({
      docked: true,
      hidden,
      pinned,
      keyboardVisible,
      miniPlayerVisible,
      setBottomNavHidden,
      setBottomNavPinned,
      setMiniPlayerVisible,
      showBottomNav
    }),
    [hidden, pinned, keyboardVisible, miniPlayerVisible, setBottomNavHidden, setBottomNavPinned, showBottomNav]
  );

  return <BottomNavVisibilityContext.Provider value={value}>{children}</BottomNavVisibilityContext.Provider>;
}

export function useBottomNavVisibility() {
  const context = useContext(BottomNavVisibilityContext);
  if (!context) {
    return {
      docked: false,
      hidden: false,
      pinned: false,
      keyboardVisible: false,
      miniPlayerVisible: false,
      setBottomNavHidden: () => undefined,
      setBottomNavPinned: () => undefined,
      setMiniPlayerVisible: () => undefined,
      showBottomNav: () => undefined
    } satisfies BottomNavVisibilityContextValue;
  }
  return context;
}

/**
 * Stand-in for `NavigationContext` when the real one is unavailable.
 *
 * Only ever read, never provided. It exists so the `useContext` call below is
 * unconditional even if `@react-navigation/native` is module-mocked without
 * `NavigationContext` — a shape several existing screen tests use.
 */
const AbsentNavigationContext = createContext<undefined>(undefined);

/**
 * `useIsFocused`, but tolerant of there being no navigator overhead.
 *
 * The library's own hook throws "Couldn't find a navigation object" outside a
 * `NavigationContainer`. That was survivable while four screens used the dock
 * hooks; now that eleven do, it means any of them rendered in isolation — a
 * screen test, a preview, a component pulled into a modal — crashes on a
 * concern that has nothing to do with what is being rendered. The sibling
 * `useBottomNavVisibility` already degrades to inert defaults when its provider
 * is missing; this brings focus detection in line with it.
 *
 * The subscription logic is the library's, minus the throw: a screen with no
 * navigator is never blurred, so it reads as focused.
 */
function useIsFocusedIfNavigated() {
  const navigation = useContext((NavigationContext || AbsentNavigationContext) as typeof AbsentNavigationContext) as
    | { isFocused: () => boolean; addListener: (event: string, callback: () => void) => () => void }
    | undefined;
  const [focused, setFocused] = useState(() => (navigation ? navigation.isFocused() : true));

  useEffect(() => {
    if (!navigation) {
      setFocused(true);
      return;
    }
    setFocused(navigation.isFocused());
    const unsubscribeFocus = navigation.addListener("focus", () => setFocused(true));
    const unsubscribeBlur = navigation.addListener("blur", () => setFocused(false));
    return () => {
      unsubscribeFocus();
      unsubscribeBlur();
    };
  }, [navigation]);

  return focused;
}

export function useBottomNavScrollVisibility({
  enabled = true,
  hideThreshold = 8,
  topRevealY = 88,
  minimumScrollableDistance = 120
}: ScrollVisibilityOptions = {}) {
  const isFocused = useIsFocusedIfNavigated();
  const { setBottomNavHidden, showBottomNav } = useBottomNavVisibility();
  const lastY = useRef(0);
  /**
   * Whether `lastY` reflects a position this surface has actually observed.
   *
   * Deltas are only meaningful between two consecutive scroll events from the
   * same layout. Two things invalidate that, and both used to produce a dock
   * that hid itself without the user scrolling:
   *
   *   - Returning to a screen that restores its offset. The list remounts with
   *     `lastY` at 0 and immediately emits a scroll event at, say, y=800. The
   *     delta reads as an 800pt downward flick and the dock slides away before
   *     the user has touched anything.
   *   - Rotation. The viewport height changes, every offset is recomputed, and
   *     the resulting event carries a delta that describes a relayout rather
   *     than a gesture.
   *
   * In both cases the correct reading is "no gesture happened": adopt the new
   * offset silently and start measuring from there.
   */
  const primed = useRef(false);
  const lastViewportHeight = useRef(0);

  useEffect(() => {
    if (isFocused) {
      primed.current = false;
      showBottomNav();
    }
    return () => showBottomNav();
  }, [isFocused, showBottomNav]);

  const onScroll = useCallback(
    (event: NativeSyntheticEvent<NativeScrollEvent>) => {
      if (!enabled || !isFocused) return;
      const native = event.nativeEvent;
      const y = Math.max(0, native.contentOffset?.y || 0);
      const viewportHeight = Math.max(0, native.layoutMeasurement?.height || 0);
      const contentHeight = Math.max(0, native.contentSize?.height || 0);

      const rotated = lastViewportHeight.current > 0 && viewportHeight > 0 && viewportHeight !== lastViewportHeight.current;
      lastViewportHeight.current = viewportHeight;

      if (!primed.current || rotated) {
        primed.current = true;
        lastY.current = y;
        // A surface the user has not scrolled yet, or has just rotated, always
        // shows the dock. Anything else would be a state change they did not ask
        // for — and on rotation, one they cannot undo without scrolling.
        setBottomNavHidden(false);
        return;
      }

      // Short surfaces never hide the dock: there is not enough travel for the
      // reveal-on-scroll-up gesture to bring it back, so hiding it would strand
      // the user. `topRevealY` additionally pins it while near the top.
      const canHide = contentHeight - viewportHeight > topRevealY + minimumScrollableDistance;
      const delta = y - lastY.current;

      if (!canHide || y <= topRevealY) {
        setBottomNavHidden(false);
      } else if (Math.abs(delta) >= hideThreshold) {
        // Deltas below the threshold are left alone rather than resolved either
        // way — reacting to every pixel is what makes a dock flicker during the
        // small direction reversals of a settling scroll.
        setBottomNavHidden(delta > 0);
      }

      lastY.current = y;
    },
    [enabled, hideThreshold, isFocused, minimumScrollableDistance, setBottomNavHidden, topRevealY]
  );

  const onScrollBeginDrag = useCallback(() => {
    if (!enabled || !isFocused) return;
    lastY.current = Math.max(0, lastY.current);
  }, [enabled, isFocused]);

  return {
    onScroll,
    onScrollBeginDrag,
    scrollEventThrottle: 16
  };
}

/**
 * Everything a scrollable surface needs to coexist with the dock, in one call.
 *
 * The two halves of that — reacting to scroll, and reserving room so the dock
 * does not cover the content — were previously independent: a screen could wire
 * one and forget the other, and six of them did. Six tab screens drove the dock
 * correctly but padded their lists by whatever number happened to be in their
 * stylesheet, so the last row sat underneath the pill. Returning both from a
 * single hook makes the pair hard to split up by accident.
 *
 * Usage is two props on the list:
 *
 *     const dock = useBottomNavSurface();
 *     <FlatList {...dock.handlers} contentContainerStyle={[styles.content, dock.contentPadding]} />
 */
export function useBottomNavSurface(options: ScrollVisibilityOptions = {}) {
  const insets = useSafeAreaInsets();
  const { docked: dockPresent } = useBottomNavVisibility();
  const playerClearance = usePulseRadioPlayerClearance();
  // `enabled: false` is a caller opting out; `docked: false` is there being no
  // dock to opt out of. Either way the surface pads itself as undocked.
  const docked = dockPresent && options.enabled !== false;
  const handlers = useBottomNavScrollVisibility(options);

  const paddingBottom = Math.max(insets.bottom, 12) + (docked ? BOTTOM_NAV_CONTENT_CLEARANCE + playerClearance : BOTTOM_NAV_UNDOCKED_PADDING);
  const contentPadding = useMemo(() => ({ paddingBottom }), [paddingBottom]);

  return { handlers, contentPadding, paddingBottom };
}

/** Dynamic padding for surfaces that manage their own scroll handlers. */
export function useBottomNavContentPadding() {
  const insets = useSafeAreaInsets();
  const playerClearance = usePulseRadioPlayerClearance();
  return Math.max(insets.bottom, 12) + BOTTOM_NAV_CONTENT_CLEARANCE + playerClearance;
}

function usePulseRadioPlayerClearance() {
  const { miniPlayerVisible } = useBottomNavVisibility();
  return miniPlayerVisible ? BOTTOM_NAV_ACTIVE_PLAYER_CLEARANCE : 0;
}
