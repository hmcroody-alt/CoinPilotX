/**
 * The two HomeScreen handlers whose failures used to disappear, plus the per-card
 * busy claim its `busyPostId` scalar could not make.
 *
 * `handleInlineComment` and `handleFollow` both ended in `finally` with no
 * `catch`. A rejected request therefore rejected out of the handler; the call site
 * absorbed it, and the screen was left showing the optimistic result — a comment
 * count raised for a comment that was never stored, and a Follow button claiming a
 * follow that never happened. Nothing told the user. Both now roll back and report.
 *
 * The busy state was `busyPostId: number | null`, so `busy={busyPostId === item.id}`
 * could mark only one card; liking post 1 greyed post 2's controls as well once the
 * scalar moved. The guard keys by action+id, so a card is marked exactly when it is
 * the card being acted on.
 *
 * Save/react/delete concurrency is covered on ProfileScreen and ReelsScreen against
 * the same guard, so this file does not repeat it — it pins only what is unique to
 * Home. Repost is the exception: Home is the only screen that renders many cards at
 * once, so "the tapped card toggles and its neighbours do not" is a claim only this
 * file can make, and the toggle itself had no Home coverage at all while it was a
 * one-way button. PostCard is mocked as a prop recorder rather than rendered:
 * rendering it would test PostCard's buttons instead of the screen's contract.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
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
jest.mock("expo-battery", () => ({ useLowPowerMode: () => false }));
jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) }),
  useRoute: () => ({ params: undefined })
}));
jest.mock("../../core/eventSync", () => ({
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined),
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../core/pulseRadio", () => ({
  getPulseRadioState: () => ({ playing: false, track: null }),
  subscribePulseRadio: jest.fn(() => () => undefined),
  togglePulseRadio: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({ onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 })
}));
jest.mock("../../navigation/homeReselect", () => ({ registerHomeReselectHandler: jest.fn(() => () => undefined) }));
jest.mock("../../api/profileTarget", () => ({ profileNavigationParams: jest.fn(() => null) }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7, username: "me" } } }) }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));
jest.mock("../../components/HomePulseComposer", () => ({ HomePulseComposer: () => null }));
jest.mock("../../components/MasterNavigationDrawer", () => ({ MasterNavigationDrawer: () => null }));
jest.mock("../../components/WelcomeUfoOverlay", () => ({ WelcomeUfoOverlay: () => null }));
jest.mock("../../components/StaticUFOField", () => ({ StaticUFOField: () => null }));
// Resolves to an array, not `{ ads: [] }`: the screen runs `ads.filter` on it
// directly inside a useMemo, so a wrapper object throws during render.
jest.mock("../../api/ads", () => ({ fetchSponsoredAds: jest.fn().mockResolvedValue([]) }));

// PostCard as a recorder: every render pushes its props, so a test can read what
// the user is seeing and invoke the handler the screen wired to a given control.
const mockCardProps: any[] = [];
jest.mock("../../components/PostCard", () => ({
  PostCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockListFeed = jest.fn();
const mockCachedFeed = jest.fn();
const mockAddComment = jest.fn();
const mockFollowAuthor = jest.fn();
const mockRepost = jest.fn();
jest.mock("../../api/feed", () => ({
  ...jest.requireActual("../../api/feed"),
  listFeed: (...args: any[]) => mockListFeed(...args),
  loadCachedFeed: (...args: any[]) => mockCachedFeed(...args),
  addPostComment: (...args: any[]) => mockAddComment(...args),
  toggleFollowAuthor: (...args: any[]) => mockFollowAuthor(...args),
  repostPost: (...args: any[]) => mockRepost(...args)
}));
// Save is intercepted one layer lower than the other actions, at the transport.
// There is no `savePost` in the API module to stub any more: the screen calls
// the shared save contract, and stubbing the contract would let a broken
// request body pass. Mocking `pulseApi` means these tests still see the exact
// URL and payload the server would receive.
jest.mock("../../api/pulseApi", () => ({
  ...jest.requireActual("../../api/pulseApi"),
  pulseApi: (...args: any[]) => mockSaveApi(...args)
}));
const mockSaveApi = jest.fn();
jest.mock("../../api/status", () => ({
  ...jest.requireActual("../../api/status"),
  listStatuses: jest.fn().mockResolvedValue({ statuses: [] }),
  loadCachedStatuses: jest.fn().mockResolvedValue(null)
}));

import { peekSaveState, resetSavedStoreForTests } from "../../social/savedStore";
import { resetSaveActionsForTests } from "../../social/useSaveAction";
import { HomeScreen } from "../HomeScreen";

function post(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    post_id: id,
    user_id: 100 + id,
    body: `Post ${id}`,
    author: {
      id: 100 + id,
      user_id: 100 + id,
      display_name: `Author ${id}`,
      username: `author${id}`,
      public_player_id: `author-${id}`
    },
    reaction_counts: {},
    comment_count: 0,
    preview_comments: [],
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
 * calling a handler outside act() warns on every test.
 */
async function tap(body: () => unknown | Promise<unknown>): Promise<void> {
  await act(async () => {
    await body();
  });
}

/** Props of the most recent render for a given post id — what the user sees now. */
function card(id: number) {
  for (let index = mockCardProps.length - 1; index >= 0; index -= 1) {
    if (Number(mockCardProps[index].post?.id) === id) return mockCardProps[index];
  }
  throw new Error(`PostCard for post ${id} has not rendered`);
}

async function renderScreen(posts = [post(1)]) {
  mockListFeed.mockResolvedValue({ posts });
  const utils = render(<HomeScreen />);
  await waitFor(() => expect(card(posts[0].id).post).toBeTruthy());
  await act(async () => undefined);
  return utils;
}

beforeEach(() => {
  mockCardProps.length = 0;
  jest.clearAllMocks();
  // The save store outlives a render, which is the point of it. Cleared between
  // tests so one test's save does not decide the next test's starting state.
  resetSavedStoreForTests();
  resetSaveActionsForTests();
  mockCachedFeed.mockResolvedValue(null);
});

describe("HomeScreen inline comment", () => {
  it("raises the count immediately and keeps the returned comment", async () => {
    const pending = deferred<any>();
    mockAddComment.mockReturnValue(pending.promise);
    await renderScreen([post(1, { comment_count: 2 })]);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onSubmitComment(post(1, { comment_count: 2 }), "Nice one");
    });
    expect(card(1).post.comment_count).toBe(3);

    await tap(async () => {
      pending.resolve({ comment: { id: 55, body: "Nice one" } });
      await run;
    });
    expect(card(1).post.preview_comments[0].id).toBe(55);
    expect(card(1).post.comment_count).toBe(3);
  });

  it("puts the count back and says why when the comment fails", async () => {
    // The defect: `finally` with no `catch`. The count stayed raised for a comment
    // the server never stored, and nothing was said.
    mockAddComment.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { comment_count: 4 })]);

    await tap(() => card(1).onSubmitComment(post(1, { comment_count: 4 }), "Nice one"));
    expect(card(1).post.comment_count).toBe(4);
    expect(card(1).post.preview_comments).toEqual([]);
    expect(queryByText(/offline/i)).toBeTruthy();
  });

  it("issues one request for a double submit", async () => {
    const pending = deferred<any>();
    mockAddComment.mockReturnValue(pending.promise);
    await renderScreen();

    const onSubmitComment = card(1).onSubmitComment;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onSubmitComment(post(1), "Nice one");
      onSubmitComment(post(1), "Nice one");
    });
    expect(mockAddComment).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ comment: { id: 1, body: "Nice one" } });
      await first;
    });
  });
});

describe("HomeScreen follow author", () => {
  it("flips the button immediately and keeps the server's answer", async () => {
    const pending = deferred<any>();
    mockFollowAuthor.mockReturnValue(pending.promise);
    await renderScreen([post(1, { viewer_follows_author: false })]);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onFollow(post(1, { viewer_follows_author: false }));
    });
    expect(card(1).post.viewer_follows_author).toBe(true);

    await tap(async () => {
      pending.resolve({ following: true });
      await run;
    });
    expect(card(1).post.viewer_follows_author).toBe(true);
  });

  it("unfollows every card by the same author, not only the one tapped", async () => {
    // Two cards from one author: following from either must move both, or the feed
    // shows the same person as both followed and not followed.
    const author = {
      id: 42,
      user_id: 42,
      display_name: "Shared Author",
      username: "shared",
      public_player_id: "shared-author"
    };
    mockFollowAuthor.mockResolvedValue({ following: true });
    await renderScreen([
      post(1, { author, viewer_follows_author: false }),
      post(2, { author, viewer_follows_author: false })
    ]);

    await tap(() => card(1).onFollow(post(1, { author, viewer_follows_author: false })));
    expect(card(1).post.viewer_follows_author).toBe(true);
    expect(card(2).post.viewer_follows_author).toBe(true);
  });

  it("puts the button back and says why when the follow fails", async () => {
    // The defect: `finally` with no `catch`. The button was left claiming a follow
    // that never happened.
    mockFollowAuthor.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { viewer_follows_author: false })]);

    await tap(() => card(1).onFollow(post(1, { viewer_follows_author: false })));
    expect(Boolean(card(1).post.viewer_follows_author)).toBe(false);
    expect(queryByText(/offline/i)).toBeTruthy();
  });

  it("issues one request for a double tap", async () => {
    const pending = deferred<any>();
    mockFollowAuthor.mockReturnValue(pending.promise);
    await renderScreen();

    const onFollow = card(1).onFollow;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onFollow(post(1));
      onFollow(post(1));
    });
    expect(mockFollowAuthor).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ following: true });
      await first;
    });
  });
});

describe("HomeScreen failure banner", () => {
  it("shows the failure over a feed that has posts, and clears it when dismissed", async () => {
    // The banner is the reason the two rollback tests above can see anything at
    // all: `error` was previously rendered only by ListEmptyComponent, so with
    // even one post in the feed a reported failure was displayed nowhere. It is
    // dismissible so a stale failure does not sit over the feed indefinitely.
    mockFollowAuthor.mockRejectedValue(new Error("Network request failed"));
    const { queryByText, getByLabelText } = await renderScreen([post(1), post(2)]);

    await tap(() => card(1).onFollow(post(1)));
    expect(queryByText(/offline/i)).toBeTruthy();

    // Found by its accessibility label, which is what a screen reader user has
    // to work with: the message plus how to get rid of it.
    const banner = getByLabelText(/Tap to dismiss/i);
    await tap(() => fireEvent.press(banner));
    expect(queryByText(/offline/i)).toBeNull();
  });
});

describe("HomeScreen repost", () => {
  it("asks to create, not to undo, when the post is not reposted yet", async () => {
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 3 });
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(mockRepost).toHaveBeenCalledWith(1, { undo: false });
    expect(card(1).post.reposted).toBe(true);
    expect(card(1).post.repost_count).toBe(3);
  });

  it("undoes the repost on the second tap instead of stopping at one-way", async () => {
    // Home rendered a one-way button while the route had no DELETE branch, so a
    // viewer who tapped by accident had no way back. Both directions now.
    mockRepost
      .mockResolvedValueOnce({ ok: true, reposted: true, repost_count: 3 })
      .mockResolvedValueOnce({ ok: true, reposted: false, removed: true, repost_count: 2 });
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(card(1).post.reposted).toBe(true);

    await tap(() => card(1).onRepost(card(1).post));
    expect(mockRepost).toHaveBeenLastCalledWith(1, { undo: true });
    expect(card(1).post.reposted).toBe(false);
    expect(card(1).post.repost_count).toBe(2);
  });

  it("takes the server's count over its own optimistic guess", async () => {
    // The optimistic bump is 2 -> 3, but the count includes reposts by people this
    // feed cannot see, so only the server's number is right.
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 41 });
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);
    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(card(1).post.repost_count).toBe(41);
  });

  it("moves only the tapped card, not its neighbour", async () => {
    // The claim only a multi-card screen can make: `updatePost` is keyed by id, so
    // reposting post 1 must leave post 2 exactly as it was.
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 3 });
    await renderScreen([
      post(1, { repost_count: 2, reposted: false }),
      post(2, { repost_count: 2, reposted: false })
    ]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(card(1).post.reposted).toBe(true);
    expect(Boolean(card(2).post.reposted)).toBe(false);
    expect(card(2).post.repost_count).toBe(2);
  });

  it("puts the count back and says why when the repost fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(Boolean(card(1).post.reposted)).toBe(false);
    expect(card(1).post.repost_count).toBe(2);
    expect(queryByText(/offline/i)).toBeTruthy();
  });

  it("restores the reposted state when an undo fails", async () => {
    // Rollback has to work in both directions, or a failed undo hides a repost the
    // viewer still owns.
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen([post(1, { repost_count: 3, reposted: true })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 3, reposted: true })));
    expect(mockRepost).toHaveBeenCalledWith(1, { undo: true });
    expect(card(1).post.reposted).toBe(true);
    expect(card(1).post.repost_count).toBe(3);
  });

  it("issues one request for a double tap, so the two answers cannot race", async () => {
    const pending = deferred<any>();
    mockRepost.mockReturnValue(pending.promise);
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    const onRepost = card(1).onRepost;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onRepost(post(1, { repost_count: 2, reposted: false }));
      onRepost(post(1, { repost_count: 2, reposted: false }));
    });
    expect(mockRepost).toHaveBeenCalledTimes(1);

    await tap(async () => {
      pending.resolve({ ok: true, reposted: true, repost_count: 3 });
      await first;
    });
    expect(card(1).post.repost_count).toBe(3);
  });
});

describe("HomeScreen per-card busy state", () => {
  // Driven through repost rather than save. Save deliberately no longer flows
  // through this screen's guard — it is owned by the shared save store, so that
  // a tap here also settles the copy of the post the profile behind this screen
  // is rendering. Repost is still a per-screen optimistic action, so it is the
  // action that still exercises `guard.isItemBusy`.
  it("marks the acted-on card and leaves its sibling usable", async () => {
    // `busyPostId === item.id` could mark only one card, so this pair of
    // assertions from a single render is the claim the scalar could not make.
    const pending = deferred<any>();
    mockRepost.mockReturnValue(pending.promise);
    await renderScreen([post(1), post(2)]);
    expect(card(1).busy).toBe(false);
    expect(card(2).busy).toBe(false);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onRepost(post(1));
    });
    expect(card(1).busy).toBe(true);
    expect(card(2).busy).toBe(false);

    await tap(async () => {
      pending.resolve({ ok: true, reposted: true, repost_count: 1 });
      await run;
    });
    expect(card(1).busy).toBe(false);
  });

  it("holds two cards busy at once", async () => {
    const first = deferred<any>();
    const second = deferred<any>();
    mockRepost.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await renderScreen([post(1), post(2)]);

    let runOne!: Promise<unknown>;
    let runTwo!: Promise<unknown>;
    await tap(() => {
      runOne = card(1).onRepost(post(1));
      runTwo = card(2).onRepost(post(2));
    });
    expect(mockRepost).toHaveBeenCalledTimes(2);
    expect(card(1).busy).toBe(true);
    expect(card(2).busy).toBe(true);

    // Settling one must not clear the other's marker.
    await tap(async () => {
      first.resolve({ ok: true, reposted: true, repost_count: 1 });
      await runOne;
    });
    expect(card(1).busy).toBe(false);
    expect(card(2).busy).toBe(true);

    await tap(async () => {
      second.resolve({ ok: true, reposted: true, repost_count: 1 });
      await runTwo;
    });
    expect(card(2).busy).toBe(false);
  });
});

describe("HomeScreen save", () => {
  it("asserts the wanted state on the save route rather than asking for a toggle", async () => {
    mockSaveApi.mockResolvedValue({ ok: true, saved: true, changed: true });
    await renderScreen();

    await tap(() => card(1).onSave(post(1)));

    expect(mockSaveApi).toHaveBeenCalledWith("/api/pulse/posts/1/save", expect.objectContaining({ method: "POST" }));
    expect(JSON.parse(mockSaveApi.mock.calls[0][1].body)).toEqual({ post_id: 1, saved: true });
  });

  it("saves the original when the card is a repost, so both copies agree", async () => {
    mockSaveApi.mockResolvedValue({ ok: true, saved: true });
    await renderScreen([post(1, { repost: { original_post_id: 41 } })]);

    await tap(() => card(1).onSave(card(1).post));

    expect(JSON.parse(mockSaveApi.mock.calls[0][1].body)).toMatchObject({ post_id: 41, saved: true });
  });

  it("issues one request for a double tap, so a save cannot race an unsave", async () => {
    const pending = deferred<any>();
    mockSaveApi.mockReturnValue(pending.promise);
    await renderScreen();

    const onSave = card(1).onSave;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onSave(post(1));
      onSave(post(1));
    });
    expect(mockSaveApi).toHaveBeenCalledTimes(1);

    await tap(async () => {
      pending.resolve({ ok: true, saved: true });
      await first;
    });
    expect(peekSaveState("post", 1)).toEqual({ saved: true, pending: false });
  });

  it("rolls back and reports the failure instead of silently reverting", async () => {
    mockSaveApi.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { saved: false })]);

    await tap(() => card(1).onSave(card(1).post));

    expect(peekSaveState("post", 1)?.saved).toBe(false);
    expect(queryByText(/offline|could not|connection/i)).toBeTruthy();
  });
});
