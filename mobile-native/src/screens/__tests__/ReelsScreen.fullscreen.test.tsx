/**
 * Full-screen Reels: viewport, pager configuration, and directional navigator
 * visibility as the screen actually wires them.
 *
 * `navigatorVisibility.test.ts` proves the rule. This file proves the screen
 * feeds the rule the right arguments, which is where the previous
 * implementation went wrong: it called a `notifySwipeSettled()` with no
 * arguments from a momentum handler that fires identically for a finger swipe
 * and for a tilt commit, so tilting hid the navigator. That defect was invisible
 * to a unit test of the rule and to a manual pass by anyone who did not think to
 * tilt backwards. The assertions below drive the real scroll callbacks with real
 * offsets and read the real visibility store.
 *
 * ReelPlayerCard is a prop recorder rather than a render: what needs pinning
 * here is what the screen *asks* for (full-bleed, safe-area insets, the tap
 * recovery handler), not how the card draws it.
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
jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useIsFocused: () => true
}));
jest.mock("../../navigation/reelsReselect", () => ({
  registerReelsReselectHandler: jest.fn(() => () => undefined)
}));

// The visibility store, mocked with *stable* spies so a test can read what the
// screen did to navigation across renders. `BOTTOM_NAV_CONTENT_CLEARANCE` must
// be re-exported: the screen computes overlay positions from it, and a missing
// export would silently produce NaN offsets rather than a failure.
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

// The tilt controller, mocked so the touch-takeover handshake is observable.
// `previewProgress` has to be a real Animated.Value — the screen interpolates it
// into the parallax wrapper's transform on every render.
const mockNotifyTouchStart = jest.fn();
const mockNotifyTouchEnd = jest.fn();
jest.mock("../../spatial/motion/useTiltNavigation", () => {
  const { Animated } = jest.requireActual("react-native");
  return {
    useTiltNavigation: () => ({
      previewProgress: new Animated.Value(0),
      state: "neutral",
      notifyTouchStart: mockNotifyTouchStart,
      notifyTouchEnd: mockNotifyTouchEnd,
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

import { FlatList } from "react-native";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../../navigation/bottomNavMetrics";
import { __clearSpatialFlagOverrides, __setSpatialFlagOverride } from "../../spatial/flags";
import { ReelsScreen } from "../ReelsScreen";

/** Matches the grace window in ReelsScreen; a released drag resolves after it. */
const DRAG_ABANDON_GRACE_MS = 120;

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
/** jsdom's window width under jest-expo, which the screen seeds its state from. */
const PAGE = require("react-native").Dimensions.get("window").width;

function enableSpatial() {
  __setSpatialFlagOverride("spatialConsoleEnabled", true);
  __setSpatialFlagOverride("spatialReelsEnabled", true);
  __setSpatialFlagOverride("immersiveNavigatorEnabled", true);
}

async function renderScreen() {
  mockList.mockResolvedValue({ ok: true, reels: REELS, has_more: false, next_offset: 3 });
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) };
  const utils = render(<ReelsScreen route={{ params: {} } as never} navigation={navigation as never} />);
  await waitFor(() => expect(mockCardProps.length).toBeGreaterThan(0));
  // Flush outside the render call: RNTL cannot have `render` nested in act(),
  // but VirtualizedList still schedules a cell-layout update once data lands.
  await act(async () => undefined);
  return { ...utils, list: utils.UNSAFE_getByType(FlatList) as any };
}

/** The most recent props for a given reel — i.e. what the user is seeing now. */
function cardFor(id: number) {
  return [...mockCardProps].reverse().find((props) => props.reel?.id === id);
}

/** A horizontal scroll event carrying the one measurement the rule reads. */
const scrollEvent = (x: number) => ({ nativeEvent: { contentOffset: { x, y: 0 } } });

/** Where the pager is resting, so each helper can swipe *from* somewhere real. */
let pagerOffsetX = 0;

/**
 * Drive a complete finger swipe: drag begins, the finger lifts partway, and
 * momentum carries the pager to `toIndex`. This is the exact callback order RN
 * emits, and the order is the point — visibility resolves on the lift, and the
 * source of a gesture is decided by whether a drag was seen at all.
 */
async function swipeTo(list: any, toIndex: number) {
  const from = pagerOffsetX;
  const to = toIndex * PAGE;
  await act(async () => {
    list.props.onScrollBeginDrag(scrollEvent(from));
    // The finger releases most of the way across; the fling finishes the page.
    list.props.onScrollEndDrag(scrollEvent(from + (to - from) * 0.6));
    list.props.onMomentumScrollBegin();
    list.props.onMomentumScrollEnd(scrollEvent(to));
  });
  pagerOffsetX = to;
}

/**
 * A swipe right the pager cannot act on: at the leading edge it rubber-bands
 * and settles back on the reel it started from. On a device this is the very
 * first thing a user tries, and it is what the index-derived rule got wrong.
 */
async function swipeRightAgainstTheEdge(list: any, distance = 120) {
  const from = pagerOffsetX;
  await act(async () => {
    list.props.onScrollBeginDrag(scrollEvent(from));
    list.props.onScrollEndDrag(scrollEvent(from - distance));
    list.props.onMomentumScrollBegin();
    list.props.onMomentumScrollEnd(scrollEvent(from));
  });
}

/** A drag that travels and returns under the finger: the user changed their mind. */
async function cancelledDrag(list: any) {
  const from = pagerOffsetX;
  await act(async () => {
    list.props.onScrollBeginDrag(scrollEvent(from));
    list.props.onScrollEndDrag(scrollEvent(from));
  });
}

/** A tilt commit or any other programmatic scroll: momentum with no drag. */
async function settleWithoutTouch(list: any, toIndex: number) {
  await act(async () => {
    list.props.onMomentumScrollEnd(scrollEvent(toIndex * PAGE));
  });
  pagerOffsetX = toIndex * PAGE;
}

beforeEach(() => {
  mockCardProps.length = 0;
  pagerOffsetX = 0;
  jest.clearAllMocks();
  __clearSpatialFlagOverrides();
  mockCached.mockResolvedValue({ reels: [], cachedAt: 0 });
  Object.defineProperty(require("react-native").AppState, "currentState", {
    configurable: true,
    value: "active"
  });
});

afterEach(() => {
  __clearSpatialFlagOverrides();
});

describe("full-screen viewport", () => {
  it("asks the card for full-bleed layout with overlays inside the safe areas", async () => {
    enableSpatial();
    await renderScreen();
    const card = cardFor(1);

    expect(card.fullBleed).toBe(true);
    // Media runs edge to edge; only the *controls* are inset. Top clears the
    // notch plus the lane rail, bottom clears the dock's resting height.
    expect(card.contentTop).toBe(59 + 56);
    expect(card.contentBottom).toBe(34 + BOTTOM_NAV_CONTENT_CLEARANCE);
    expect(card.safeBottom).toBe(34);
  });

  it("parks overlays independently of navigator visibility so a hide cannot relayout the reel", async () => {
    enableSpatial();
    const { list } = await renderScreen();
    const before = cardFor(1).contentBottom;

    await swipeTo(list, 1); // reveal
    await swipeTo(list, 0); // hide

    // Same number, after both transitions: the dock moves alone. Deriving this
    // from the hidden flag would relayout every reel mid-gesture, which is both
    // the content jump the mission forbids and a guaranteed dropped frame.
    expect(cardFor(1).contentBottom).toBe(before);
  });
});

describe("pager configuration", () => {
  it("pages horizontally, one viewport per gesture", async () => {
    enableSpatial();
    const { list } = await renderScreen();

    expect(list.props.horizontal).toBe(true);
    expect(list.props.pagingEnabled).toBe(true);
    expect(list.props.snapToInterval).toBe(PAGE);
    // The one that prevents a fast flick from skipping several reels.
    expect(list.props.disableIntervalMomentum).toBe(true);
  });

  it("computes page offsets arithmetically instead of measuring", async () => {
    enableSpatial();
    const { list } = await renderScreen();

    expect(list.props.getItemLayout(REELS, 2)).toEqual({ length: PAGE, offset: PAGE * 2, index: 2 });
    expect(list.props.getItemLayout(REELS, 0)).toEqual({ length: PAGE, offset: 0, index: 0 });
  });

  it("keeps exactly one reel playing while a swipe crosses between two", async () => {
    // Mid-swipe neither reel clears the 72% viewability threshold, so the list
    // reports nothing viewable. Acting on that pauses the reel being left and
    // leaves the one being entered black until the snap lands.
    enableSpatial();
    const { list } = await renderScreen();
    expect(cardFor(1).active).toBe(true);

    await act(async () => list.props.onViewableItemsChanged({ viewableItems: [] }));

    // Reel 3 is outside the preload window and has never rendered, so the two
    // mounted cards are the whole population: one owner, one not.
    expect(cardFor(1).active).toBe(true);
    expect(cardFor(2).active).toBe(false);
  });

  it("hands playback to the reel the pager settled on", async () => {
    enableSpatial();
    const { list } = await renderScreen();

    await act(async () =>
      list.props.onViewableItemsChanged({
        viewableItems: [{ isViewable: true, index: 1, item: REELS[1] }]
      })
    );

    expect(cardFor(2).active).toBe(true);
    expect(cardFor(1).active).toBe(false);
  });

  it("still releases playback on the legacy path when the list has nothing viewable", async () => {
    // Vertically an empty viewability report means the list really is empty, and
    // the legacy screen's behavior there is not ours to change.
    const { list } = await renderScreen();

    await act(async () => list.props.onViewableItemsChanged({ viewableItems: [] }));

    expect(cardFor(1).active).toBe(false);
  });
});

describe("directional navigator visibility", () => {
  it("reveals navigation on a committed swipe to the next reel", async () => {
    enableSpatial();
    const { list } = await renderScreen();
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await swipeTo(list, 1);

    expect(mockShowBottomNav).toHaveBeenCalled();
    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
  });

  it("hides navigation on a committed swipe back to the previous reel", async () => {
    enableSpatial();
    const { list } = await renderScreen();
    await swipeTo(list, 2);
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await swipeTo(list, 1);

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("hides on a right swipe at the first reel, which cannot change the page", async () => {
    // The device failure this rule was rewritten for. The pager is at offset 0,
    // so a right swipe rubber-bands back to the reel it started from. Reading
    // direction from the settled *index* made that "no transition" and left the
    // dock up — for the single most likely way anyone tries the gesture.
    enableSpatial();
    const { list } = await renderScreen();
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await swipeRightAgainstTheEdge(list);

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("hides on every repeat of that swipe", async () => {
    // "Repeated right swipes reliably hide the navigator": each gesture is its
    // own decision, so nothing accumulates and nothing gets stuck.
    enableSpatial();
    const { list } = await renderScreen();
    mockSetBottomNavHidden.mockClear();

    await swipeRightAgainstTheEdge(list);
    await swipeRightAgainstTheEdge(list);
    await swipeRightAgainstTheEdge(list);

    expect(mockSetBottomNavHidden.mock.calls).toEqual([[true], [true], [true]]);
  });

  it("hides the moment the finger lifts, without waiting for the fling to land", async () => {
    // A hide that arrives a fling later is unattributable: users read it as the
    // app acting on its own rather than as a response to what they just did.
    enableSpatial();
    const { list } = await renderScreen();
    mockSetBottomNavHidden.mockClear();

    await act(async () => {
      list.props.onScrollBeginDrag(scrollEvent(2 * PAGE));
      list.props.onScrollEndDrag(scrollEvent(2 * PAGE - 140));
    });

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
  });

  it("leaves navigation alone for a drag that travels and comes back", async () => {
    // A cancelled gesture, and the same shape as a tap the pager saw as a
    // one-pixel drag. Net travel below the commit threshold decides nothing.
    enableSpatial();
    const { list } = await renderScreen();
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await cancelledDrag(list);

    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("leaves navigation alone when a tilt commit moves the pager", async () => {
    // The regression this file exists for. A tilt commit animates
    // scrollToOffset, which emits a momentum-end byte-identical to a swipe's.
    // The absence of a preceding drag is the only thing that distinguishes it.
    enableSpatial();
    const { list } = await renderScreen();
    await swipeTo(list, 1);
    mockShowBottomNav.mockClear();
    mockSetBottomNavHidden.mockClear();

    await settleWithoutTouch(list, 0); // tilt backwards — would have hidden before
    await settleWithoutTouch(list, 2); // and forwards

    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("ignores viewability flipping mid-drag", async () => {
    // Viewability answers "what is on screen enough to play", and it flips as
    // soon as the incoming reel crosses ~72% — *before* the finger lifts. It
    // used to be the origin of the direction, which meant the answer depended on
    // a callback the user cannot see and does not control. The gesture's own
    // offsets are now the only input, so this cannot skew the decision.
    enableSpatial();
    const { list } = await renderScreen();
    await swipeTo(list, 2);
    mockSetBottomNavHidden.mockClear();
    mockShowBottomNav.mockClear();

    await act(async () => {
      list.props.onScrollBeginDrag(scrollEvent(2 * PAGE));
      // The incoming reel wins viewability while the finger is still down.
      list.props.onViewableItemsChanged({ viewableItems: [{ isViewable: true, index: 1, item: REELS[1] }] });
      list.props.onScrollEndDrag(scrollEvent(2 * PAGE - 140));
      list.props.onMomentumScrollBegin();
      list.props.onMomentumScrollEnd(scrollEvent(PAGE));
    });

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("still decides correctly when nothing is viewable at all", async () => {
    // Viewability reports nothing on an emptied refresh or a teardown. The rule
    // never consults it, so a right swipe still hides.
    enableSpatial();
    const { list } = await renderScreen();
    await swipeTo(list, 2);
    await act(async () => list.props.onViewableItemsChanged({ viewableItems: [] }));
    mockSetBottomNavHidden.mockClear();
    mockShowBottomNav.mockClear();

    await swipeTo(list, 1);

    expect(mockSetBottomNavHidden).toHaveBeenCalledWith(true);
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });

  it("reveals navigation when the user taps unclaimed media", async () => {
    enableSpatial();
    const { list } = await renderScreen();
    await swipeTo(list, 1);
    await swipeTo(list, 0); // hidden
    mockShowBottomNav.mockClear();

    await act(async () => cardFor(1).onSurfaceTap());

    // The recovery path. It is wired to the card's unclaimed-surface tap, so a
    // tap that a button or the caption consumed never reaches it.
    expect(mockShowBottomNav).toHaveBeenCalled();
  });

  it("keeps navigation permanently visible when the immersive flag is off", async () => {
    // Rollback level 1: full-screen paging survives, visibility does not move.
    __setSpatialFlagOverride("spatialConsoleEnabled", true);
    __setSpatialFlagOverride("spatialReelsEnabled", true);
    __setSpatialFlagOverride("immersiveNavigatorEnabled", false);
    const { list } = await renderScreen();
    expect(list.props.horizontal).toBe(true);
    mockSetBottomNavHidden.mockClear();

    await swipeTo(list, 1);
    await swipeTo(list, 0);

    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
  });
});

describe("touch takeover", () => {
  beforeEach(() => jest.useFakeTimers());
  afterEach(() => jest.useRealTimers());

  it("hands motion back after a drag that never produced momentum", async () => {
    // The permanent-suspension bug: `notifyTouchEnd` used to fire only from
    // `onMomentumScrollEnd`, and a slow drag released at rest emits no momentum
    // at all. The motion machine stayed touch-suspended for the rest of the
    // session — reproducible with one short drag that snapped back.
    enableSpatial();
    const { list } = await renderScreen();
    mockNotifyTouchEnd.mockClear();

    await act(async () => {
      list.props.onScrollBeginDrag(scrollEvent(0));
      list.props.onScrollEndDrag(scrollEvent(0));
    });
    expect(mockNotifyTouchStart).toHaveBeenCalled();
    expect(mockNotifyTouchEnd).not.toHaveBeenCalled();

    await act(async () => {
      jest.advanceTimersByTime(DRAG_ABANDON_GRACE_MS);
    });

    expect(mockNotifyTouchEnd).toHaveBeenCalled();
  });

  it("does not let the abandoned-drag timer fire while momentum is still running", async () => {
    enableSpatial();
    const { list } = await renderScreen();
    mockNotifyTouchEnd.mockClear();

    await act(async () => {
      list.props.onScrollBeginDrag(scrollEvent(0));
      list.props.onScrollEndDrag(scrollEvent(PAGE * 0.6));
      list.props.onMomentumScrollBegin();
      jest.advanceTimersByTime(DRAG_ABANDON_GRACE_MS * 4);
    });

    // Momentum cancelled the timer, so motion stays suspended for the whole
    // fling rather than being handed back mid-flight.
    expect(mockNotifyTouchEnd).not.toHaveBeenCalled();
    // Visibility, by contrast, resolved on the lift and did not wait for it.
    expect(mockShowBottomNav).toHaveBeenCalled();

    await act(async () => {
      list.props.onMomentumScrollEnd(scrollEvent(PAGE));
    });

    expect(mockNotifyTouchEnd).toHaveBeenCalled();
  });

  it("expires an abandoned drag claim so the next tilt commit is not read as a swipe", async () => {
    enableSpatial();
    const { list } = await renderScreen();

    await act(async () => {
      list.props.onScrollBeginDrag(scrollEvent(0));
      list.props.onScrollEndDrag(scrollEvent(0));
      jest.advanceTimersByTime(DRAG_ABANDON_GRACE_MS);
    });
    mockSetBottomNavHidden.mockClear();
    mockShowBottomNav.mockClear();

    // A tilt commit arriving after the abandoned drag must still count as motion.
    await act(async () => {
      list.props.onMomentumScrollEnd(scrollEvent(0));
    });

    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
    expect(mockShowBottomNav).not.toHaveBeenCalled();
  });
});

describe("legacy Reels regression", () => {
  it("keeps vertical paging and the plain card when spatial Reels is off", async () => {
    __setSpatialFlagOverride("spatialConsoleEnabled", false);
    const { list } = await renderScreen();

    expect(list.props.horizontal).toBe(false);
    expect(list.props.getItemLayout).toBeUndefined();
    expect(list.props.disableIntervalMomentum).toBe(false);
    expect(list.props.refreshControl).toBeTruthy();

    const card = cardFor(1);
    expect(card.fullBleed).toBe(false);
    // No tap-to-recover handler, because nothing can hide navigation here.
    expect(card.onSurfaceTap).toBeUndefined();
  });

  it("never touches navigator visibility on the legacy path", async () => {
    __setSpatialFlagOverride("spatialConsoleEnabled", false);
    const { list } = await renderScreen();
    mockSetBottomNavHidden.mockClear();

    await act(async () => {
      list.props.onScrollBeginDrag({ nativeEvent: { contentOffset: { x: 0, y: 0 } } });
      list.props.onScrollEndDrag({ nativeEvent: { contentOffset: { x: 0, y: PAGE } } });
      list.props.onMomentumScrollEnd({ nativeEvent: { contentOffset: { x: 0, y: PAGE } } });
    });

    expect(mockSetBottomNavHidden).not.toHaveBeenCalled();
  });
});
