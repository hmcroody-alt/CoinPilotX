/**
 * "Tap this reel, get this reel" — asserted as a property of the whole
 * lifecycle, not of the final state.
 *
 * The requirement is not that the right reel is playing once everything has
 * settled. It is that no *other* reel is ever on screen on the way there. Those
 * are different assertions and only the second one catches the defect, because
 * the player has two places it renders a reel before the requested one can
 * possibly be confirmed:
 *
 *   1. the cached snapshot, drawn before the network call is even issued, which
 *      holds whatever the lane held last time;
 *   2. the fetched page, when `focusInitialReel` cannot find the requested id on
 *      it — a reel ranked into another lane, or aged past the first page — in
 *      which case the old code silently opened whatever came back first.
 *
 * So the assertions below read `active` across *every* render the card mock
 * recorded, and demand that the set of reels which were ever active is exactly
 * the one that was tapped. A test that only checked the end state would pass
 * against both bugs.
 *
 * There is no endpoint that returns one reel — `/api/pulse/reels/<id>` is PATCH
 * and DELETE only — so the fix is a handoff, not a fetch, and the last test here
 * pins the fallback: with nothing staged, the screen behaves exactly as it did
 * before this feature existed.
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
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: jest.requireActual("../../navigation/bottomNavMetrics").BOTTOM_NAV_CONTENT_CLEARANCE,
  useBottomNavScrollVisibility: () => ({ onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 }),
  useBottomNavVisibility: () => ({
    hidden: false,
    docked: true,
    miniPlayerVisible: false,
    setBottomNavHidden: jest.fn(),
    showBottomNav: jest.fn()
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
import type { PulseReel } from "../../api/reels";
import { clearReelTransfer, stageReelTransfer } from "../../discovery/reelTransfer";
import { __clearSpatialFlagOverrides, __setSpatialFlagOverride } from "../../spatial/flags";
import { ReelsScreen } from "../ReelsScreen";

function reel(id: number, extra: Partial<PulseReel> = {}): PulseReel {
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
    media: [],
    ...extra
  } as PulseReel;
}

/** The reel the user tapped. Deliberately absent from both the cache and the feed. */
const TAPPED = reel(42);
/** What the lane happened to hold last time — the flash, if it ever renders. */
const CACHED_PAGE = [reel(1), reel(2)];
/** What the feed returns. Also not the tapped reel, in most of these tests. */
const FETCHED_PAGE = [reel(1), reel(2), reel(3)];

/**
 * Every reel that has been `active` at any point, oldest first, de-duplicated.
 *
 * This is the whole test: the answer must be `[42]`. Not "ends with 42" — an
 * unrelated reel that played for 300ms and was replaced is exactly the bug.
 */
function everActiveReelIds(): number[] {
  const seen: number[] = [];
  for (const props of mockCardProps) {
    if (!props.active) continue;
    const id = Number(props.reel?.id);
    if (!seen.includes(id)) seen.push(id);
  }
  return seen;
}

/** The most recent props for a reel — what the user is looking at now. */
function cardFor(id: number) {
  return [...mockCardProps].reverse().find((props) => Number(props.reel?.id) === id);
}

const navigation = { navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) };

function renderPlayer(params: Record<string, unknown>) {
  return render(<ReelsScreen route={{ params } as never} navigation={navigation as never} />);
}

beforeEach(() => {
  jest.clearAllMocks();
  // `active` is the signal every assertion in this file reads, and the screen
  // ands it with `AppState.currentState === "active"`. Under jest-expo that
  // property is `undefined`, so without this line no card is ever active and
  // every assertion below would pass or fail for a reason unrelated to reels.
  (AppState as unknown as { currentState: string }).currentState = "active";
  mockCardProps.length = 0;
  clearReelTransfer();
  __clearSpatialFlagOverrides();
  __setSpatialFlagOverride("spatialConsoleEnabled", true);
  __setSpatialFlagOverride("spatialReelsEnabled", true);
  __setSpatialFlagOverride("immersiveNavigatorEnabled", true);
  mockCached.mockResolvedValue({ reels: CACHED_PAGE, cachedAt: 1 });
  mockList.mockResolvedValue({ ok: true, reels: FETCHED_PAGE, has_more: false, next_offset: 3 });
});

afterEach(() => {
  clearReelTransfer();
  __clearSpatialFlagOverrides();
});

describe("a reel handed over from Home", () => {
  it("is on screen in the first frame, before any request resolves", async () => {
    // The feed never answers. If the requested reel needed the network to
    // arrive, nothing would be playing at all.
    mockList.mockReturnValue(new Promise(() => undefined));
    const nonce = stageReelTransfer(TAPPED);

    renderPlayer({ reelId: 42, reelTransferNonce: nonce });

    await waitFor(() => expect(mockCardProps.length).toBeGreaterThan(0));
    expect(everActiveReelIds()).toEqual([42]);
  });

  it("is the only reel ever made active, cache and feed notwithstanding", async () => {
    const nonce = stageReelTransfer(TAPPED);

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    expect(everActiveReelIds()).toEqual([42]);
    // And the rest of the feed did arrive — this is not passing because the
    // screen gave up and rendered a single reel with nothing to swipe to.
    const list = utils.UNSAFE_getByType(FlatList) as any;
    expect(list.props.data.map((item: PulseReel) => item.id)).toEqual([42, 1, 2, 3]);
  });

  it("does not render the cached snapshot it would otherwise have shown", async () => {
    // The control for the test above: with no staged reel the snapshot is what
    // renders first, so its absence here is a decision and not a coincidence.
    mockList.mockReturnValue(new Promise(() => undefined));

    renderPlayer({});

    await waitFor(() => expect(mockCardProps.length).toBeGreaterThan(0));
    expect(everActiveReelIds()).toEqual([1]);
  });

  it("keeps the tapped reel first even though the feed page does not contain it", async () => {
    const nonce = stageReelTransfer(TAPPED);

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    const list = utils.UNSAFE_getByType(FlatList) as any;
    expect(list.props.data[0].id).toBe(42);
  });

  it("prefers the server's copy when the page does contain it", async () => {
    // The carried copy is as old as the tap. If the feed has a fresher one, the
    // counts the user sees should be the fresh ones — at position zero either way.
    const stale = reel(42, { reactions_count: 3 });
    const fresh = reel(42, { reactions_count: 99 });
    mockList.mockResolvedValue({ ok: true, reels: [reel(1), fresh, reel(2)], has_more: false, next_offset: 3 });
    const nonce = stageReelTransfer(stale);

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    const list = utils.UNSAFE_getByType(FlatList) as any;
    expect(list.props.data.map((item: PulseReel) => item.id)).toEqual([42, 1, 2]);
    expect(cardFor(42).reel.reactions_count).toBe(99);
    expect(everActiveReelIds()).toEqual([42]);
  });

  it("stays playable when the feed request fails outright", async () => {
    // Offline, or a 500. The reel is already on screen and already playable, so
    // an error state here would be the screen throwing away something it has.
    mockList.mockRejectedValue(new Error("network down"));
    mockCached.mockResolvedValue({ reels: [], cachedAt: 0 });
    const nonce = stageReelTransfer(TAPPED);

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    const list = utils.UNSAFE_getByType(FlatList) as any;
    expect(list.props.data.map((item: PulseReel) => item.id)).toEqual([42]);
    expect(everActiveReelIds()).toEqual([42]);
  });

  it("still puts it first when the failure falls back to cache", async () => {
    mockList.mockRejectedValue(new Error("network down"));
    const nonce = stageReelTransfer(TAPPED);

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    const list = utils.UNSAFE_getByType(FlatList) as any;
    expect(list.props.data.map((item: PulseReel) => item.id)).toEqual([42, 1, 2]);
    expect(everActiveReelIds()).toEqual([42]);
  });
});

describe("a second tap, on a tab that never unmounted", () => {
  it("switches to the newly tapped reel with nothing from cache or feed in between", async () => {
    // The Reels tab stays mounted for the life of the app, so the mount-time
    // handoff runs once and everything after it arrives as a param change.
    const firstNonce = stageReelTransfer(TAPPED);
    const utils = renderPlayer({ reelId: 42, reelTransferNonce: firstNonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);
    mockCardProps.length = 0;

    const secondNonce = stageReelTransfer(reel(77));
    await act(async () => {
      utils.rerender(
        <ReelsScreen
          route={{ params: { reelId: 77, reelTransferNonce: secondNonce } } as never}
          navigation={navigation as never}
        />
      );
    });

    const active = everActiveReelIds();
    // 42 leads, because the render triggered by the param change still holds the
    // outgoing list — but that render is never painted: the handoff runs in a
    // layout effect, which commits before the browser/UI thread draws. What must
    // not appear is anything from the cached snapshot or the fetched page, and
    // that is what this asserts. Reel 42 is the frame the user was already on;
    // reels 1, 2 and 3 would be reels they never asked for.
    expect(active).toEqual([42, 77]);
    expect(active.filter((id) => [1, 2, 3].includes(id))).toEqual([]);
  });

  it("re-opens the same reel when it is tapped twice", async () => {
    // Same id, new nonce. Keying on the id alone would make the second tap a
    // no-op, which on a device reads as a dead card.
    const firstNonce = stageReelTransfer(TAPPED);
    const utils = renderPlayer({ reelId: 42, reelTransferNonce: firstNonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);
    const listBefore = utils.UNSAFE_getByType(FlatList) as any;
    expect(listBefore.props.data.length).toBeGreaterThan(1);

    const secondNonce = stageReelTransfer(TAPPED);
    await act(async () => {
      utils.rerender(
        <ReelsScreen
          route={{ params: { reelId: 42, reelTransferNonce: secondNonce } } as never}
          navigation={navigation as never}
        />
      );
    });

    // Reset to the tapped reel alone, then refilled by the reload.
    expect(everActiveReelIds()).toEqual([42]);
    expect(mockList.mock.calls.length).toBeGreaterThan(1);
  });
});

describe("a carried reel whose id and reel_id disagree", () => {
  /**
   * The carried copy is raw and the fetched one is normalized.
   *
   * `normalizeReel` collapses `reel_id` and `id` onto a single value, so a reel
   * that has been through it can never disagree with itself and none of the
   * other tests in this file can catch a precedence mistake. The seed is the one
   * reel on screen that has *not* necessarily been normalized — it is carried
   * from whatever the tap site was holding. So this is the shape that decides
   * whether the modules agree, and every id resolver in the chain has to read
   * `reel_id` first to survive it.
   */
  const RAW_SEED = reel(42, { id: 900 });
  const NORMALIZED_COPY = reel(42);

  it("renders once, not twice, when the feed returns the normalized copy", async () => {
    mockList.mockResolvedValue({
      ok: true,
      reels: [reel(1), NORMALIZED_COPY, reel(2)],
      has_more: false,
      next_offset: 3
    });
    const nonce = stageReelTransfer(RAW_SEED);
    // The id the real navigation carries: `buildReels` resolves `reel_id` first,
    // so this is 42 and not 900 even though the carried object leads with 900.
    expect(nonce).toEqual(expect.stringContaining("42"));

    const utils = renderPlayer({ reelId: 42, reelTransferNonce: nonce });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    const list = utils.UNSAFE_getByType(FlatList) as any;
    // Resolve `id` the other way round and the seed dedupes against 900, finds
    // nothing, and leaves the fetched copy in place behind itself — the same
    // reel at index 0 and again further down, as [42, 42, 1, 2].
    expect(list.props.data.map((item: PulseReel) => item.reel_id)).toEqual([42, 1, 2]);
    // And the survivor is the server's copy, not the carried one.
    expect(list.props.data[0].id).toBe(42);
  });
});

describe("without a handoff", () => {
  it("behaves exactly as it did before: cache first, then the feed", async () => {
    // The rollback path. A deep link, a notification, or the tab opened by hand
    // all arrive with no staged payload, and none of them should change.
    renderPlayer({});
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    expect(everActiveReelIds()).toEqual([1]);
    expect(mockCached).toHaveBeenCalled();
  });

  it("honours a bare reelId the old way when the reel is on the page", async () => {
    // A deep link to a reel that is in the feed still works through
    // `focusInitialReel` — the handoff is an addition, not a replacement.
    renderPlayer({ reelId: 3 });
    await waitFor(() => expect(mockList).toHaveBeenCalled());
    await act(async () => undefined);

    expect(cardFor(3)).toBeTruthy();
    expect(everActiveReelIds()[everActiveReelIds().length - 1]).toBe(3);
  });
});
