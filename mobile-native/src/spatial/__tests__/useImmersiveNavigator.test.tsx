/**
 * The plumbing around the directional rule: flag gating, the accessibility
 * override, and the reveals that are not a function of any gesture.
 *
 * `navigatorVisibility.test.ts` owns the decision table. What is asserted here
 * is everything the hook adds on top of it, and specifically the two asymmetries
 * that are easy to get backwards:
 *
 *   - the flag gates *hiding*, not revealing. A recovery affordance that is
 *     itself feature-flagged is a way to strand somebody behind an invisible
 *     dock, so `reveal()` works with the flag off.
 *   - a screen reader drops hides but lets reveals through, for the same reason:
 *     navigation must never leave the accessibility tree, but it may re-enter it.
 */
import React from "react";
import { Text } from "react-native";
import { act, render } from "@testing-library/react-native";

const mockSetBottomNavHidden = jest.fn();
const mockShowBottomNav = jest.fn();
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavVisibility: () => ({
    hidden: false,
    docked: true,
    miniPlayerVisible: false,
    setBottomNavHidden: mockSetBottomNavHidden,
    showBottomNav: mockShowBottomNav
  })
}));

import { AccessibilityInfo } from "react-native";
import { __clearSpatialFlagOverrides, __setSpatialFlagOverride } from "../flags";
import type { HorizontalSwipe } from "../navigatorVisibility";
import { useImmersiveNavigator } from "../useImmersiveNavigator";

type Api = ReturnType<typeof useImmersiveNavigator>;

/**
 * Render the hook and expose its API. RNTL's own `renderHook` is available, but
 * a host component keeps the `surfaceFocused` prop changes explicit, which is
 * half of what this file tests.
 *
 * `api` is handed back as a function, not a property. Spreading `utils` and
 * adding a `get api()` looks equivalent but is not: Babel's object-spread helper
 * *evaluates* the accessor once and stores the result as a data property, so
 * every test would read the API from the first render — the one where the screen
 * reader is still off. That produced a red test against correct product code.
 */
function mountHook(surfaceFocused = true) {
  let api!: Api;
  function Harness({ focused }: { focused: boolean }) {
    api = useImmersiveNavigator(focused);
    return <Text>navigator</Text>;
  }
  const utils = render(<Harness focused={surfaceFocused} />);
  return {
    unmount: utils.unmount,
    getApi: () => api,
    setFocused: (focused: boolean) => utils.rerender(<Harness focused={focused} />)
  };
}

async function swipe(api: Api, gesture: HorizontalSwipe) {
  await act(async () => api.notifySwipe(gesture));
}

/** A finger-drag right by `distance` points: the content offset falls. Reveals. */
const right = (distance: number): HorizontalSwipe => ({ source: "touch", startOffsetX: 750, endOffsetX: 750 - distance });
/** A finger-drag left by `distance` points: the content offset rises. Hides. */
const left = (distance: number): HorizontalSwipe => ({ source: "touch", startOffsetX: 750, endOffsetX: 750 + distance });

/** Set before mounting: the hook queries this once on mount. */
let screenReaderEnabled = false;
/** The hook's own "screenReaderChanged" handler, for turning VoiceOver on live. */
let screenReaderListener: ((on: boolean) => void) | null = null;

beforeEach(() => {
  jest.clearAllMocks();
  __clearSpatialFlagOverrides();
  screenReaderEnabled = false;
  screenReaderListener = null;
  jest
    .spyOn(AccessibilityInfo, "isScreenReaderEnabled")
    .mockImplementation(() => Promise.resolve(screenReaderEnabled));
  jest.spyOn(AccessibilityInfo, "addEventListener").mockImplementation(((
    _event: string,
    handler: (on: boolean) => void
  ) => {
    screenReaderListener = handler;
    return { remove: () => { screenReaderListener = null; } };
  }) as never);
  __setSpatialFlagOverride("spatialConsoleEnabled", true);
  __setSpatialFlagOverride("immersiveNavigatorEnabled", true);
});

afterEach(() => __clearSpatialFlagOverrides());

describe("useImmersiveNavigator", () => {
  it("reveals on entering the surface", async () => {
    const harness = mountHook(true);
    await act(async () => undefined);
    expect(mockShowBottomNav).toHaveBeenCalled();
    expect(harness.getApi().hidden).toBe(false);
  });

  it("restores navigation on unmount so no screen can strand the dock off-screen", async () => {
    const harness = mountHook(true);
    await act(async () => undefined);
    mockShowBottomNav.mockClear();

    harness.unmount();

    expect(mockShowBottomNav).toHaveBeenCalled();
  });

  it("reveals when a child surface opens, without waiting for a gesture", async () => {
    // Comments, sharing and the reel menu pass `false` here: a sheet over the
    // player is treated exactly like leaving the surface.
    const harness = mountHook(true);
    await act(async () => undefined);
    await swipe(harness.getApi(), left(200));
    mockShowBottomNav.mockClear();

    await act(async () => harness.setFocused(false));

    expect(mockShowBottomNav).toHaveBeenCalled();
  });

  it("hides on a swipe the rule calls a hide", async () => {
    const harness = mountHook(true);
    await act(async () => undefined);

    await swipe(harness.getApi(), left(200));

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
  });

  it("reveals on every repeat of a right swipe that cannot change the page", async () => {
    // The device symptom this fix exists for, now pointed at the recovery
    // direction: the user is on the first reel with the dock hidden, so every
    // right swipe rubber-bands back to offset 0. Under the previous
    // index-derived rule that was "no transition" and the dock stayed hidden no
    // matter how many times they tried — a user with no way back to navigation.
    // Each gesture is now its own decision.
    const harness = mountHook(true);
    await act(async () => undefined);
    await swipe(harness.getApi(), left(200));
    mockShowBottomNav.mockClear();
    const atLeadingEdge = { source: "touch", startOffsetX: 0, endOffsetX: 0 - 120 } as const;

    await swipe(harness.getApi(), atLeadingEdge);
    await swipe(harness.getApi(), atLeadingEdge);
    await swipe(harness.getApi(), atLeadingEdge);

    expect(mockShowBottomNav).toHaveBeenCalledTimes(3);
  });

  it("hides on every repeat of a left swipe at the trailing edge", async () => {
    // The mirror: the user is on the last loaded reel and keeps swiping forward.
    // The page cannot change, the gesture still has a direction.
    const harness = mountHook(true);
    await act(async () => undefined);
    const atTrailingEdge = { source: "touch", startOffsetX: 3750, endOffsetX: 3750 + 120 } as const;

    await swipe(harness.getApi(), atTrailingEdge);
    await swipe(harness.getApi(), atTrailingEdge);
    await swipe(harness.getApi(), atTrailingEdge);

    expect(mockSetBottomNavHidden).toHaveBeenCalledTimes(3);
    expect(mockSetBottomNavHidden).toHaveBeenNthCalledWith(3, true);
  });

  it("restores on a right swipe after a left swipe hid it", async () => {
    const harness = mountHook(true);
    await act(async () => undefined);
    await swipe(harness.getApi(), left(200));
    mockShowBottomNav.mockClear();

    await swipe(harness.getApi(), right(200));

    expect(mockShowBottomNav).toHaveBeenCalled();
  });

  it("does nothing at all for a gesture the rule leaves unchanged", async () => {
    const harness = mountHook(true);
    await act(async () => undefined);
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await swipe(harness.getApi(), { source: "motion", startOffsetX: 0, endOffsetX: 375 });
    await swipe(harness.getApi(), left(4));

    expect(mockShowBottomNav).not.toHaveBeenCalled();
    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
  });

  describe("with the immersive flag off", () => {
    beforeEach(() => __setSpatialFlagOverride("immersiveNavigatorEnabled", false));

    it("never hides", async () => {
      const harness = mountHook(true);
      await act(async () => undefined);

      await swipe(harness.getApi(), left(200));

      expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
    });

    it("still reveals on demand", async () => {
      // Rollback level 1 must not disable the recovery path — it is the one
      // thing that gets a user out of a dock that some *other* build hid.
      const harness = mountHook(true);
      await act(async () => undefined);
      mockShowBottomNav.mockClear();

      await act(async () => harness.getApi().reveal());

      expect(mockShowBottomNav).toHaveBeenCalled();
    });
  });

  describe("with a screen reader", () => {
    it("drops hides so navigation never leaves the accessibility tree", async () => {
      screenReaderEnabled = true;
      const harness = mountHook(true);
      await act(async () => undefined);

      await swipe(harness.getApi(), left(200));

      expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
    });

    it("still reveals on a swipe right", async () => {
      screenReaderEnabled = true;
      const harness = mountHook(true);
      await act(async () => undefined);
      mockShowBottomNav.mockClear();

      await swipe(harness.getApi(), right(200));

      expect(mockShowBottomNav).toHaveBeenCalled();
    });

    it("reveals immediately when it is turned on mid-session", async () => {
      const harness = mountHook(true);
      await act(async () => undefined);
      await swipe(harness.getApi(), left(200));
      mockShowBottomNav.mockClear();

      // VoiceOver comes on while the dock is hidden: the user must not have to
      // find an invisible control to get navigation back.
      await act(async () => screenReaderListener?.(true));

      expect(mockShowBottomNav).toHaveBeenCalled();
    });
  });
});
