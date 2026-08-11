/**
 * Behaviour of the dock's hide-on-scroll gesture.
 *
 * The properties under test are the ones the mission brief names directly —
 * "never flicker, never jump" — and they are all properties of *deltas between
 * consecutive scroll events*, which is exactly the kind of thing that reads as
 * correct in a diff and misbehaves on a device. Each test below corresponds to
 * a way the delta can lie: a restored scroll position, a rotation, or a scroll
 * that has settled and is oscillating by a pixel or two.
 */

import React, { ReactNode } from "react";
import { act, renderHook } from "@testing-library/react-native";
import { NavigationContext } from "@react-navigation/native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 34, left: 0, right: 0 })
}));

import {
  BottomNavVisibilityProvider,
  useBottomNavScrollVisibility,
  useBottomNavSurface,
  useBottomNavVisibility
} from "../BottomNavVisibility";
import { BOTTOM_NAV_ACTIVE_PLAYER_CLEARANCE, BOTTOM_NAV_CONTENT_CLEARANCE, BOTTOM_NAV_UNDOCKED_PADDING } from "../bottomNavMetrics";

/**
 * A navigation object with just the surface the focus hook uses.
 *
 * Provided through the real `NavigationContext` rather than by module-mocking
 * `@react-navigation/native`, so the test exercises the same subscription path
 * the app does — including the blur that must stop a background screen from
 * driving the dock.
 */
function fakeNavigation() {
  const listeners: Record<string, Array<() => void>> = { focus: [], blur: [] };
  let focused = true;
  return {
    nav: {
      isFocused: () => focused,
      addListener: (event: string, callback: () => void) => {
        (listeners[event] ||= []).push(callback);
        return () => {
          listeners[event] = listeners[event].filter((entry) => entry !== callback);
        };
      }
    },
    blur() {
      focused = false;
      listeners.blur.forEach((callback) => callback());
    }
  };
}

let navigator = fakeNavigation();

const wrapper = ({ children }: { children: ReactNode }) => (
  <NavigationContext.Provider value={navigator.nav as never}>
    <BottomNavVisibilityProvider>{children}</BottomNavVisibilityProvider>
  </NavigationContext.Provider>
);

/** A scroll event shaped like the ones a long, scrollable list emits. */
function scrollEvent(y: number, { viewport = 800, content = 4000 } = {}) {
  return {
    nativeEvent: {
      contentOffset: { y },
      layoutMeasurement: { height: viewport },
      contentSize: { height: content }
    }
  } as never;
}

function mountGesture(options?: Parameters<typeof useBottomNavScrollVisibility>[0]) {
  return renderHook(
    () => ({
      gesture: useBottomNavScrollVisibility(options),
      dock: useBottomNavVisibility()
    }),
    { wrapper }
  );
}

describe("bottom nav scroll gesture", () => {
  beforeEach(() => {
    navigator = fakeNavigation();
  });

  it("hides on a sustained scroll down and comes back on scroll up", () => {
    const { result } = mountGesture();

    act(() => result.current.gesture.onScroll(scrollEvent(0)));
    act(() => result.current.gesture.onScroll(scrollEvent(400)));
    expect(result.current.dock.hidden).toBe(true);

    act(() => result.current.gesture.onScroll(scrollEvent(300)));
    expect(result.current.dock.hidden).toBe(false);
  });

  it("does not hide on the first event after focus, however far down it lands", () => {
    // A list that restores its offset emits a scroll event at y=800 before the
    // user has touched anything. Diffed against a fresh `lastY` of 0 that is an
    // 800pt flick downward, and the dock used to vanish on arrival.
    const { result } = mountGesture();

    act(() => result.current.gesture.onScroll(scrollEvent(800)));
    expect(result.current.dock.hidden).toBe(false);

    // ...and the restored position becomes the new baseline, so the next real
    // downward scroll is measured from 800 rather than from 0.
    act(() => result.current.gesture.onScroll(scrollEvent(900)));
    expect(result.current.dock.hidden).toBe(true);
  });

  it("re-primes when the screen rotates instead of reading the relayout as a gesture", () => {
    const { result } = mountGesture();

    act(() => result.current.gesture.onScroll(scrollEvent(400)));
    act(() => result.current.gesture.onScroll(scrollEvent(900)));
    expect(result.current.dock.hidden).toBe(true);

    // Rotation: the viewport height changes and every offset is recomputed. The
    // delta that arrives describes a relayout, not a flick — so the dock must
    // come back rather than commit to whichever direction the numbers moved.
    act(() => result.current.gesture.onScroll(scrollEvent(1800, { viewport: 380, content: 6000 })));
    expect(result.current.dock.hidden).toBe(false);
  });

  it("ignores sub-threshold jitter rather than toggling on every pixel", () => {
    const { result } = mountGesture({ hideThreshold: 8 });

    act(() => result.current.gesture.onScroll(scrollEvent(0)));
    act(() => result.current.gesture.onScroll(scrollEvent(400)));
    expect(result.current.dock.hidden).toBe(true);

    // A settling scroll oscillates by a pixel or two. Each of these is a
    // direction reversal; reacting to any of them is what flicker looks like.
    act(() => result.current.gesture.onScroll(scrollEvent(397)));
    act(() => result.current.gesture.onScroll(scrollEvent(401)));
    act(() => result.current.gesture.onScroll(scrollEvent(398)));
    expect(result.current.dock.hidden).toBe(true);
  });

  it("keeps the dock up on a surface too short to scroll it back", () => {
    const { result } = mountGesture();

    act(() => result.current.gesture.onScroll(scrollEvent(0, { viewport: 800, content: 860 })));
    act(() => result.current.gesture.onScroll(scrollEvent(60, { viewport: 800, content: 860 })));
    expect(result.current.dock.hidden).toBe(false);
  });

  it("keeps the dock up near the top of the content", () => {
    const { result } = mountGesture({ topRevealY: 88 });

    act(() => result.current.gesture.onScroll(scrollEvent(0)));
    act(() => result.current.gesture.onScroll(scrollEvent(60)));
    expect(result.current.dock.hidden).toBe(false);
  });

  it("ignores scroll from a screen that has been navigated away from", () => {
    // A backgrounded screen can still emit scroll events — a settling
    // deceleration, or a list restoring its offset. None of them should move a
    // dock the user is looking at on a different screen.
    const { result } = mountGesture();

    act(() => result.current.gesture.onScroll(scrollEvent(0)));
    act(() => navigator.blur());
    act(() => result.current.gesture.onScroll(scrollEvent(900)));
    expect(result.current.dock.hidden).toBe(false);
  });
});

describe("useBottomNavSurface clearance", () => {
  it("reserves the dock clearance on top of the device inset when docked", () => {
    const { result } = renderHook(() => useBottomNavSurface(), { wrapper });
    // 34pt inset from the mock above — the dock draws that itself, so the
    // clearance is added to it rather than assumed to contain it.
    expect(result.current.paddingBottom).toBe(34 + BOTTOM_NAV_CONTENT_CLEARANCE);
    expect(result.current.contentPadding).toEqual({ paddingBottom: 34 + BOTTOM_NAV_CONTENT_CLEARANCE });
  });

  it("adds player clearance only while the distinct mini-player is visible", () => {
    const { result } = renderHook(
      () => ({ surface: useBottomNavSurface(), dock: useBottomNavVisibility() }),
      { wrapper }
    );

    expect(result.current.surface.paddingBottom).toBe(34 + BOTTOM_NAV_CONTENT_CLEARANCE);
    act(() => result.current.dock.setMiniPlayerVisible(true));
    expect(result.current.surface.paddingBottom).toBe(
      34 + BOTTOM_NAV_CONTENT_CLEARANCE + BOTTOM_NAV_ACTIVE_PLAYER_CLEARANCE
    );
    act(() => result.current.dock.setMiniPlayerVisible(false));
    expect(result.current.surface.paddingBottom).toBe(34 + BOTTOM_NAV_CONTENT_CLEARANCE);
  });

  it("drops to plain inset padding when there is no dock below the surface", () => {
    // No provider: a pushed stack screen covers the dock entirely, so reserving
    // room for it would leave a band of dead space above the home indicator.
    const { result } = renderHook(() => useBottomNavSurface());
    expect(result.current.paddingBottom).toBe(34 + BOTTOM_NAV_UNDOCKED_PADDING);
  });

  it("drops to plain inset padding when the caller opts out", () => {
    const { result } = renderHook(() => useBottomNavSurface({ enabled: false }), { wrapper });
    expect(result.current.paddingBottom).toBe(34 + BOTTOM_NAV_UNDOCKED_PADDING);
  });
});
