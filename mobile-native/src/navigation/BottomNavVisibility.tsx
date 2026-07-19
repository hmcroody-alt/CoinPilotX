import { useIsFocused } from "@react-navigation/native";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Keyboard, NativeScrollEvent, NativeSyntheticEvent } from "react-native";

type BottomNavVisibilityContextValue = {
  hidden: boolean;
  pinned: boolean;
  keyboardVisible: boolean;
  setBottomNavHidden: (hidden: boolean) => void;
  setBottomNavPinned: (reason: string, pinned: boolean) => void;
  showBottomNav: () => void;
};

type ScrollVisibilityOptions = {
  enabled?: boolean;
  hideThreshold?: number;
  topRevealY?: number;
  minimumScrollableDistance?: number;
};

const BottomNavVisibilityContext = createContext<BottomNavVisibilityContextValue | null>(null);

export function BottomNavVisibilityProvider({ children }: { children: ReactNode }) {
  const [requestedHidden, setRequestedHidden] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
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
      hidden,
      pinned,
      keyboardVisible,
      setBottomNavHidden,
      setBottomNavPinned,
      showBottomNav
    }),
    [hidden, pinned, keyboardVisible, setBottomNavHidden, setBottomNavPinned, showBottomNav]
  );

  return <BottomNavVisibilityContext.Provider value={value}>{children}</BottomNavVisibilityContext.Provider>;
}

export function useBottomNavVisibility() {
  const context = useContext(BottomNavVisibilityContext);
  if (!context) {
    return {
      hidden: false,
      pinned: false,
      keyboardVisible: false,
      setBottomNavHidden: () => undefined,
      setBottomNavPinned: () => undefined,
      showBottomNav: () => undefined
    } satisfies BottomNavVisibilityContextValue;
  }
  return context;
}

export function useBottomNavScrollVisibility({
  enabled = true,
  hideThreshold = 8,
  topRevealY = 88,
  minimumScrollableDistance = 120
}: ScrollVisibilityOptions = {}) {
  const isFocused = useIsFocused();
  const { setBottomNavHidden, showBottomNav } = useBottomNavVisibility();
  const lastY = useRef(0);

  useEffect(() => {
    if (isFocused) showBottomNav();
    return () => showBottomNav();
  }, [isFocused, showBottomNav]);

  const onScroll = useCallback(
    (event: NativeSyntheticEvent<NativeScrollEvent>) => {
      if (!enabled || !isFocused) return;
      const native = event.nativeEvent;
      const y = Math.max(0, native.contentOffset?.y || 0);
      const viewportHeight = Math.max(0, native.layoutMeasurement?.height || 0);
      const contentHeight = Math.max(0, native.contentSize?.height || 0);
      const canHide = contentHeight - viewportHeight > topRevealY + minimumScrollableDistance;
      const delta = y - lastY.current;

      if (!canHide || y <= topRevealY) {
        setBottomNavHidden(false);
      } else if (Math.abs(delta) >= hideThreshold) {
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
