/**
 * Reel social actions: concurrency, rollback, and — the point of this file — the
 * fact that a failure is now *said out loud*.
 *
 * Every handler on this screen used to end in a swallow. `handleReport` was
 * literally `await reportReel(reel.id).catch(() => undefined)`: tapping Report
 * produced no acknowledgement on success and no complaint on failure, so a user
 * reporting harmful content had no way to know whether anything happened.
 * `handleNotInterested` had a `finally` but no `catch`, so a failure rejected the
 * handler, the call site's `.catch(() => undefined)` absorbed it, and the Reel
 * simply stayed on screen. `handleReact`/`handleSave`/`handleRepost` reverted the
 * icon with `catch {}`, which is indistinguishable from a tap that never landed.
 *
 * The screen also wrote a `busyId` scalar that only `handleDeleteReel` ever read,
 * so a double tap on save or react issued two requests. `useSocialActionGuard`
 * has its own unit tests; "this screen actually routes through it" is a separate
 * claim and needs its own assertions, which is why they are here.
 *
 * ReelPlayerCard is mocked as a prop recorder rather than rendered. Rendering it
 * would test ReelPlayerCard's buttons; what needs pinning here is the screen's
 * concurrency, rollback and user-visible-error contract.
 */
import React from "react";
import { act, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
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
// Must resolve, not return undefined: the screen calls `.catch()` on it directly.
jest.mock("../../core/reelsAudioSession", () => ({ configureReelsAudioSession: jest.fn().mockResolvedValue(undefined) }));
jest.mock("../../navigation/reelsReselect", () => ({ registerReelsReselectHandler: jest.fn(() => () => undefined) }));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavScrollVisibility: () => ({ onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 })
}));
jest.mock("../../api/profileTarget", () => ({
  profileNavigationParams: jest.fn(() => null),
  profileTargetFromAuthor: jest.fn(() => null)
}));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));

// ReelPlayerCard as a recorder: each render pushes its props, so a test can read
// the reel the screen is currently showing and invoke the handler wired to a
// given control.
const mockCardProps: any[] = [];
jest.mock("../../components/ReelPlayerCard", () => ({
  ReelPlayerCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockList = jest.fn();
const mockCached = jest.fn();
const mockReact = jest.fn();
const mockSave = jest.fn();
const mockRepost = jest.fn();
const mockReport = jest.fn();
const mockNotInterested = jest.fn();
const mockFollow = jest.fn();
jest.mock("../../api/reels", () => ({
  ...jest.requireActual("../../api/reels"),
  listReels: (...args: any[]) => mockList(...args),
  loadCachedReelsSnapshot: (...args: any[]) => mockCached(...args),
  reactToReel: (...args: any[]) => mockReact(...args),
  saveReel: (...args: any[]) => mockSave(...args),
  repostReel: (...args: any[]) => mockRepost(...args),
  reportReel: (...args: any[]) => mockReport(...args),
  markReelNotInterested: (...args: any[]) => mockNotInterested(...args),
  followReelCreator: (...args: any[]) => mockFollow(...args),
  trackReelView: jest.fn().mockResolvedValue({ view_count: 1 }),
  getReelComments: jest.fn().mockResolvedValue({ comments: [], commentsCount: 0 }),
  loadReelCommentDraft: jest.fn().mockResolvedValue(null),
  saveReelCommentDraft: jest.fn().mockResolvedValue(undefined),
  clearReelCommentDraft: jest.fn().mockResolvedValue(undefined)
}));

import { PulseApiError } from "../../api/pulseApi";
import { ReelsScreen } from "../ReelsScreen";

const REEL_ID = 88;

function reel(overrides: Record<string, unknown> = {}) {
  return {
    id: REEL_ID,
    reel_id: REEL_ID,
    user_id: 9,
    title: "Reel under test",
    caption: "A reel fixture.",
    video_url: "https://cdn.example/r.mp4",
    poster_url: "https://cdn.example/r.jpg",
    author: { id: 9, user_id: 9, display_name: "Fixture Creator", username: "fixture_creator" },
    reactions_count: 0,
    comments_count: 0,
    media: [],
    ...overrides
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Invoke handlers inside act(). The guard flips busy state on entry and exit, so
 * calling a handler outside act() emits an "update not wrapped in act" warning on
 * every test — noise that trains people to ignore React's warnings.
 */
async function tap(body: () => unknown | Promise<unknown>): Promise<void> {
  await act(async () => {
    await body();
  });
}

/** The props of the most recent card render — i.e. what the user is seeing now. */
function card() {
  const latest = mockCardProps[mockCardProps.length - 1];
  if (!latest) throw new Error("ReelPlayerCard has not rendered");
  return latest;
}

async function renderScreen(overrides: Record<string, unknown> = {}) {
  // `has_more` / `next_offset`, matching api/reels.ts:listReels — not camelCase.
  mockList.mockResolvedValue({ ok: true, reels: [reel(overrides)], has_more: false, next_offset: 1 });
  const navigation = { navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) };
  const utils = render(<ReelsScreen route={{ params: {} } as never} navigation={navigation as never} />);
  await waitFor(() => expect(card().reel).toBeTruthy());
  // Flush after mount, not around it: `render` cannot be nested inside act()
  // (RNTL does its own host-component detection render and then unmounts it), but
  // VirtualizedList still schedules a cell-layout state update once data arrives.
  // That update is the list's rather than a handler's, and without this flush it
  // lands outside any act() boundary and warns — noise that trains people to
  // ignore React's warnings.
  await act(async () => undefined);
  return { ...utils, navigation };
}

beforeEach(() => {
  mockCardProps.length = 0;
  jest.clearAllMocks();
  // Must be a snapshot object, not null: the screen reads `snapshot.reels.length`
  // directly on both the happy and the recovery path.
  mockCached.mockResolvedValue({ reels: [], cachedAt: 0 });
});

describe("ReelsScreen save", () => {
  it("issues one request for a double tap, so a save cannot race an unsave", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    const onSave = card().onSave;
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    await tap(() => {
      first = onSave(reel());
      second = onSave(reel());
    });
    expect(mockSave).toHaveBeenCalledTimes(1);

    await tap(async () => {
      pending.resolve({ saved: true });
      await Promise.all([first, second]);
    });
    expect(card().reel.saved).toBe(true);
  });

  it("shows the save immediately and then keeps the server's answer", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    let run!: Promise<unknown>;
    await tap(() => {
      run = card().onSave(reel());
    });
    expect(card().reel.saved).toBe(true);
    await tap(async () => {
      pending.resolve({ saved: false });
      await run;
    });
    expect(card().reel.saved).toBe(false);
  });

  it("rolls the save back and says why, instead of silently reverting", async () => {
    // The old `catch {}` reverted the bookmark with no message, which the user
    // cannot tell apart from a tap that never registered.
    mockSave.mockRejectedValue(new Error("Network request failed"));
    const { queryByTestId } = await renderScreen({ saved: false });
    await tap(() => card().onSave(reel()));
    expect(Boolean(card().reel.saved)).toBe(false);
    expect(queryByTestId("reels-action-message")?.props.children).toMatch(/offline/i);
  });
});

describe("ReelsScreen reactions", () => {
  it("lets the later of two taps win, so a slow first response cannot revert it", async () => {
    const slow = deferred<any>();
    mockReact.mockReturnValueOnce(slow.promise).mockResolvedValueOnce({ reaction_type: "love", reaction_counts: { love: 1 } });
    await renderScreen();

    const onReact = card().onReact;
    let first!: Promise<unknown>;
    await tap(async () => {
      first = onReact(reel(), "fire");
      await onReact(reel(), "love");
    });
    expect(card().reel.viewer_reaction).toBe("love");

    await tap(async () => {
      slow.resolve({ reaction_type: "fire", reaction_counts: { fire: 1 } });
      await first;
    });
    expect(card().reel.viewer_reaction).toBe("love");
  });

  it("restores the previous reaction and count when the request fails", async () => {
    mockReact.mockRejectedValue(new Error("Network request failed"));
    const { queryByTestId } = await renderScreen({ viewer_reaction: "fire", reactions_count: 3 });
    await tap(() => card().onReact(reel({ viewer_reaction: "fire", reactions_count: 3 }), "love"));
    expect(card().reel.viewer_reaction).toBe("fire");
    expect(card().reel.reactions_count).toBe(3);
    expect(queryByTestId("reels-action-message")).toBeTruthy();
  });

  it("does not react to a live session or a reactions-disabled reel", async () => {
    await renderScreen();
    await tap(() => card().onReact(reel({ live_session_id: 12 }), "fire"));
    await tap(() => card().onReact(reel({ reactions_disabled: true }), "fire"));
    expect(mockReact).not.toHaveBeenCalled();
  });
});

describe("ReelsScreen repost", () => {
  it("issues one request for a double tap, so it cannot create two repost rows", async () => {
    const pending = deferred<any>();
    mockRepost.mockReturnValue(pending.promise);
    await renderScreen();

    const onRepost = card().onRepost;
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    await tap(() => {
      first = onRepost(reel());
      second = onRepost(reel());
    });
    expect(mockRepost).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ ok: true });
      await Promise.all([first, second]);
    });
    expect(card().reel.reposted).toBe(true);
  });

  it("undoes a repost on the second tap instead of refusing it", async () => {
    // This used to answer "You already reposted this Reel" and stop, which was the
    // honest response while the route had no delete path. Now that DELETE exists,
    // refusing would strand anyone who reposted by accident.
    mockRepost.mockResolvedValue({ ok: true, reposted: false });
    const { queryByTestId } = await renderScreen();

    await tap(() => card().onRepost(reel({ reposted: true })));
    expect(mockRepost).toHaveBeenCalledWith(REEL_ID, { undo: true });
    expect(Boolean(card().reel.reposted)).toBe(false);
    expect(queryByTestId("reels-action-message")?.props.children ?? "").not.toMatch(/already reposted/i);
  });

  it("asks to create, not to undo, when the reel is not reposted yet", async () => {
    mockRepost.mockResolvedValue({ ok: true, reposted: true });
    await renderScreen();
    await tap(() => card().onRepost(reel()));
    expect(mockRepost).toHaveBeenCalledWith(REEL_ID, { undo: false });
    expect(card().reel.reposted).toBe(true);
  });

  it("takes the server's flag over its own guess", async () => {
    // The server can disagree: another device may have already undone this repost.
    // Trusting the local guess would leave the button contradicting the row.
    mockRepost.mockResolvedValue({ ok: true, reposted: false });
    await renderScreen();
    await tap(() => card().onRepost(reel()));
    expect(Boolean(card().reel.reposted)).toBe(false);
  });

  it("rolls the repost back when the request fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen();
    await tap(() => card().onRepost(reel()));
    expect(Boolean(card().reel.reposted)).toBe(false);
  });

  it("restores the reposted state when an undo fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen();
    await tap(() => card().onRepost(reel({ reposted: true })));
    expect(card().reel.reposted).toBe(true);
  });
});

describe("ReelsScreen report", () => {
  it("acknowledges a filed report, because a safety action with no feedback is not an action", async () => {
    // Was `reportReel(reel.id).catch(() => undefined)`. A user who taps Report and
    // sees nothing either taps repeatedly or concludes the platform ignored them.
    mockReport.mockResolvedValue({ ok: true });
    const { queryByTestId } = await renderScreen();
    await tap(() => card().onReport(reel()));
    expect(mockReport).toHaveBeenCalledWith(REEL_ID);
    expect(queryByTestId("reels-action-message")?.props.children).toMatch(/report sent/i);
  });

  it("tells the user when a report could not be filed", async () => {
    mockReport.mockRejectedValue(new PulseApiError("nope", 500));
    const { queryByTestId } = await renderScreen();
    await tap(() => card().onReport(reel()));
    const message = queryByTestId("reels-action-message")?.props.children;
    expect(message).toBeTruthy();
    expect(message).not.toMatch(/report sent/i);
  });

  it("issues one report for a double tap", async () => {
    const pending = deferred<any>();
    mockReport.mockReturnValue(pending.promise);
    await renderScreen();
    const onReport = card().onReport;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onReport(reel());
      onReport(reel());
    });
    expect(mockReport).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ ok: true });
      await first;
    });
  });
});

describe("ReelsScreen not interested", () => {
  it("removes the reel once the server has recorded the signal", async () => {
    mockNotInterested.mockResolvedValue({ ok: true });
    const { queryByTestId } = await renderScreen();
    await tap(() => card().onNotInterested(reel()));
    await waitFor(() => expect(queryByTestId("reels-action-message")).toBeNull());
    expect(mockNotInterested).toHaveBeenCalledWith(REEL_ID);
  });

  it("keeps the reel and reports the failure instead of rejecting into a caller's empty catch", async () => {
    // This handler had a `finally` and no `catch`. Every call site wrapped it in
    // `.catch(() => undefined)`, so the Reel stayed put and nothing was said.
    mockNotInterested.mockRejectedValue(new Error("Network request failed"));
    const { queryByTestId } = await renderScreen();
    await tap(() => card().onNotInterested(reel()));
    expect(card().reel.id).toBe(REEL_ID);
    expect(queryByTestId("reels-action-message")?.props.children).toMatch(/offline/i);
  });
});

describe("ReelsScreen follow creator", () => {
  it("keeps the server's answer rather than assuming the optimistic one", async () => {
    mockFollow.mockResolvedValue({ following: false });
    await renderScreen({ viewer_follows_author: false });
    await tap(() => card().onFollowCreator(reel({ viewer_follows_author: false })));
    expect(card().reel.viewer_follows_author).toBe(false);
  });

  it("reverts and explains when the follow fails", async () => {
    // `followReelCreator(...).catch(() => null)` reduced every failure to
    // "revert the button" — which is also what a successful unfollow looks like.
    mockFollow.mockRejectedValue(new Error("Network request failed"));
    const { queryByTestId } = await renderScreen({ viewer_follows_author: false });
    await tap(() => card().onFollowCreator(reel({ viewer_follows_author: false })));
    expect(Boolean(card().reel.viewer_follows_author)).toBe(false);
    expect(queryByTestId("reels-action-message")?.props.children).toMatch(/offline/i);
  });
});

describe("ReelsScreen busy state", () => {
  it("marks the card busy for the duration of a request and clears it afterwards", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();
    expect(card().busy).toBe(false);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card().onSave(reel());
    });
    expect(card().busy).toBe(true);

    await tap(async () => {
      pending.resolve({ saved: true });
      await run;
    });
    expect(card().busy).toBe(false);
  });
});
