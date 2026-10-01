/**
 * Reels must stop playing the moment the user leaves it.
 *
 * The defect: Reels is a bottom-tab screen (`Tabs.Screen name="Reels"`) with no
 * `unmountOnBlur`, so walking to Home, Messages or a profile blurs the screen
 * but never unmounts it. `activeIndex` kept its value, `appActive` stayed true,
 * and the card's `active` prop — the only thing that drives `playAsync()` —
 * stayed true with it. The reel the user walked away from kept playing behind
 * whatever they opened next.
 *
 * `isFocused` was already computed in this screen; it was wired into the
 * immersive dock and the tilt pager but *not* into `active`. These tests pin it
 * into the playback rule:
 *
 *     shouldPlay = focused AND foreground AND no overlay AND is the active index
 *
 * The card is a prop recorder, as in `ReelsScreen.fullscreen.test.tsx`: what is
 * under test is what the screen *asks* for. `ReelPlayerCard.leavingReels.test.tsx`
 * proves the card honours the answer with a real `pauseAsync()`.
 */
import React from "react";
import { act, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 59, bottom: 34, left: 0, right: 0 })
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));
jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return { ResizeMode: { COVER: "cover", CONTAIN: "contain" }, Video: ReactActual.forwardRef(() => null) };
});
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return { ContentTranslation: ({ text }: any) => ReactActual.createElement(Text, null, text) };
});
jest.mock("../../core/eventSync", () => ({
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined),
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../core/reelsAudioSession", () => ({
  configureReelsAudioSession: jest.fn().mockResolvedValue(undefined)
}));

/**
 * Navigation focus, driven by the test.
 *
 * React Navigation's real `useIsFocused` subscribes to `focus`/`blur` and sets
 * state, so a blur re-renders the screen. Here the flip is the variable plus an
 * explicit `rerender`, which reproduces that re-render without standing up a
 * navigator. Crucially the blur event fires when the transition *begins*, not at
 * teardown — which is the whole reason this is the right signal.
 */
let mockFocused = true;
jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useIsFocused: () => mockFocused
}));
jest.mock("../../navigation/reelsReselect", () => ({
  registerReelsReselectHandler: jest.fn(() => () => undefined)
}));

const mockSetBottomNavHidden = jest.fn();
const mockShowBottomNav = jest.fn();
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: jest.requireActual("../../navigation/bottomNavMetrics").BOTTOM_NAV_CONTENT_CLEARANCE,
  useBottomNavScrollVisibility: () => ({ onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 }),
  useBottomNavVisibility: () => ({
    hidden: false,
    docked: true,
    miniPlayerVisible: false,
    setBottomNavHidden: mockSetBottomNavHidden,
    showBottomNav: mockShowBottomNav
  })
}));

jest.mock("../../spatial/motion/useTiltNavigation", () => {
  const { Animated } = jest.requireActual("react-native");
  return {
    useTiltNavigation: () => ({
      previewProgress: new Animated.Value(0),
      state: "neutral",
      notifyTouchStart: jest.fn(),
      notifyTouchEnd: jest.fn(),
      recalibrate: jest.fn()
    })
  };
});

jest.mock("../../api/profileTarget", () => ({
  profileNavigationParams: jest.fn(() => null),
  profileTargetFromAuthor: jest.fn(() => null)
}));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));

const mockCardProps: any[] = [];
jest.mock("../../components/ReelPlayerCard", () => ({
  ReelPlayerCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockList = jest.fn();
const mockCached = jest.fn();
jest.mock("../../api/reels", () => ({
  ...jest.requireActual("../../api/reels"),
  listReels: (...args: any[]) => mockList(...args),
  loadCachedReelsSnapshot: (...args: any[]) => mockCached(...args),
  trackReelView: jest.fn().mockResolvedValue({ view_count: 1 }),
  getReelComments: jest.fn().mockResolvedValue({ comments: [], commentsCount: 0 }),
  loadReelCommentDraft: jest.fn().mockResolvedValue(null),
  saveReelCommentDraft: jest.fn().mockResolvedValue(undefined),
  clearReelCommentDraft: jest.fn().mockResolvedValue(undefined)
}));

import { AppState, FlatList } from "react-native";
import { __clearSpatialFlagOverrides } from "../../spatial/flags";
import { ReelsScreen } from "../ReelsScreen";

function reel(id: number) {
  return {
    id,
    reel_id: id,
    user_id: 9,
    title: `Reel ${id}`,
    caption: "A reel fixture.",
    video_url: "https://cdn.example/r.mp4",
    poster_url: "https://cdn.example/r.jpg",
    author: { id: 9, user_id: 9, display_name: "Fixture Creator", username: "fixture_creator" },
    reactions_count: 0,
    comments_count: 0,
    media: []
  };
}

const REELS = [reel(1), reel(2), reel(3)];

/** AppState change handlers the screen registered, so a test can background it. */
let appStateHandlers: ((state: string) => void)[] = [];

const screenProps = {
  route: { params: {}, name: "Reels" } as never,
  navigation: { navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) } as never
};

async function renderScreen() {
  mockList.mockResolvedValue({ ok: true, reels: REELS, has_more: false, next_offset: 3 });
  const utils = render(<ReelsScreen {...screenProps} />);
  await waitFor(() => expect(mockCardProps.length).toBeGreaterThan(0));
  await act(async () => undefined);
  return { ...utils, list: utils.UNSAFE_getByType(FlatList) as any };
}

/** Re-render the screen the way a focus or blur event would. */
async function setFocused(view: any, focused: boolean) {
  mockFocused = focused;
  await act(async () => {
    view.rerender(<ReelsScreen {...screenProps} />);
  });
}

/** Deliver an OS foreground/background transition to the screen's listener. */
async function setAppState(state: "active" | "background" | "inactive") {
  await act(async () => {
    appStateHandlers.forEach((handler) => handler(state));
  });
}

/** Move viewability onto a different reel, exactly as the list reports it. */
async function makeViewable(list: any, index: number) {
  await act(async () => {
    list.props.onViewableItemsChanged({
      viewableItems: [{ isViewable: true, index, item: REELS[index], key: String(REELS[index].id) }]
    });
  });
}

/** The most recent props for a given reel — i.e. what the screen is asking now. */
function cardFor(id: number) {
  return [...mockCardProps].reverse().find((props) => props.reel?.id === id);
}

/** Every reel the screen currently wants playing. The rule allows at most one. */
function activeReelIds() {
  return REELS.map((item) => item.id).filter((id) => cardFor(id)?.active === true);
}

beforeEach(() => {
  mockCardProps.length = 0;
  appStateHandlers = [];
  mockFocused = true;
  jest.clearAllMocks();
  __clearSpatialFlagOverrides();
  mockCached.mockResolvedValue({ reels: [], cachedAt: 0 });
  Object.defineProperty(AppState, "currentState", { configurable: true, value: "active" });
  jest.spyOn(AppState, "addEventListener").mockImplementation((type: any, handler: any) => {
    if (type === "change") appStateHandlers.push(handler);
    return { remove: jest.fn() } as any;
  });
});

afterEach(() => {
  jest.restoreAllMocks();
  __clearSpatialFlagOverrides();
});

describe("leaving Reels stops playback", () => {
  it("plays the centred reel while Reels is focused and in the foreground", async () => {
    await renderScreen();
    expect(activeReelIds()).toEqual([1]);
  });

  /**
   * Home, Messages and Profile are one test, not three, and that is the point:
   * the screen never learns *where* the user went. All three destinations emit
   * the same navigator blur, so a fix keyed on the blur covers every exit —
   * including the ones nobody thought to enumerate.
   */
  it.each(["Home", "Messages", "Profile"])("pauses when the user navigates to %s", async () => {
    const view = await renderScreen();
    expect(activeReelIds()).toEqual([1]);

    await setFocused(view, false);

    expect(activeReelIds()).toEqual([]);
    expect(cardFor(1).active).toBe(false);
  });

  it("pauses on blur alone, without waiting for the screen to unmount", async () => {
    // Reels is a tab screen with no `unmountOnBlur`, so the unmount never comes.
    // Anything that waited for teardown — an effect cleanup, a component
    // willUnmount — would never fire, which is precisely how this shipped.
    const view = await renderScreen();

    await setFocused(view, false);

    expect(cardFor(1).active).toBe(false);
    expect(view.UNSAFE_queryByType(FlatList)).not.toBeNull(); // still mounted
  });

  it("resumes from the reel that is actually visible when the user comes back", async () => {
    const view = await renderScreen();
    await makeViewable(view.list, 1);
    expect(activeReelIds()).toEqual([2]);

    await setFocused(view, false);
    expect(activeReelIds()).toEqual([]);

    await setFocused(view, true);

    // Reel 2, not reel 1: playback is recomputed from the current visible state
    // rather than restarting the feed from the top.
    expect(activeReelIds()).toEqual([2]);
  });

  it("keeps at most one reel active across a swipe from A to B", async () => {
    const view = await renderScreen();
    expect(activeReelIds()).toEqual([1]);

    await makeViewable(view.list, 1);

    expect(activeReelIds()).toEqual([2]);
    expect(cardFor(1).active).toBe(false);
    expect(cardFor(3)?.active ?? false).toBe(false);
  });

  it("pauses when the app goes to the background", async () => {
    await renderScreen();
    expect(activeReelIds()).toEqual([1]);

    await setAppState("background");

    expect(activeReelIds()).toEqual([]);
  });

  it("does not restart when the app is foregrounded on another route", async () => {
    const view = await renderScreen();

    // User leaves Reels for Home, backgrounds the app there, then comes back to
    // the app — still on Home. The foreground re-arms `appActive`; nothing else
    // may re-arm with it.
    await setFocused(view, false);
    await setAppState("background");
    await setAppState("active");
    await act(async () => {
      view.rerender(<ReelsScreen {...screenProps} />);
    });

    expect(activeReelIds()).toEqual([]);
  });

  /**
   * MUTATION REGRESSION.
   *
   * Delete `isFocused &&` from `playbackAllowed` in ReelsScreen and this test
   * fails: the blurred screen goes on asking for reel 1 to play. It is stated as
   * its own case, separate from the destination tests above, so the failure
   * names the cause rather than a symptom.
   */
  it("REGRESSION: a blurred Reels screen asks for nothing to play", async () => {
    const view = await renderScreen();
    const playingBefore = activeReelIds();
    expect(playingBefore.length).toBe(1);

    await setFocused(view, false);

    expect(activeReelIds()).toEqual([]);
  });
});
