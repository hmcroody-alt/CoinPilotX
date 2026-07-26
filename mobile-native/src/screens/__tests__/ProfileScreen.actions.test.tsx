/**
 * Post social actions on ProfileScreen, and specifically the claim a scalar could
 * never satisfy: acting on one card must not mark a different card busy.
 *
 * ProfileScreen held `busyPostId: number | null`. One number cannot describe two
 * cards, so `busy={busyPostId === item.id}` disabled exactly one card at a time —
 * and because the handlers guarded themselves with `if (busyPostId === post.id)
 * return`, they read state React had not committed yet, so a double tap issued two
 * requests anyway. The guard's `isItemBusy` is keyed per action+id in a ref.
 *
 * `useSocialActionGuard` is unit-tested separately; this file asserts the screen
 * routes through it. PostCard is mocked as a prop recorder rather than rendered:
 * rendering it would test PostCard's buttons, whereas what needs pinning here is
 * the screen's concurrency, rollback and error-surfacing contract.
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
jest.mock("../../core/eventSync", () => ({
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined),
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  useBottomNavSurface: () => ({ handlers: { onScroll: jest.fn() }, contentPadding: {} })
}));
jest.mock("../../components/ProfileHeader", () => ({ ProfileHeader: () => null }));
jest.mock("../../api/profileTarget", () => ({
  ...jest.requireActual("../../api/profileTarget"),
  profileNavigationParams: jest.fn(() => null),
  profileTargetFromAuthor: jest.fn(() => null)
}));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));

// PostCard as a recorder: every render pushes its props, so a test can read what
// the user is seeing and invoke the handler the screen wired to a given control.
// Props are captured per post id as well, which is how the two-card independence
// assertion below reads both cards from the same render pass.
const mockCardProps: any[] = [];
jest.mock("../../components/PostCard", () => ({
  PostCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockMyProfile = jest.fn();
const mockCachedProfile = jest.fn();
const mockListFeed = jest.fn();
const mockSave = jest.fn();
const mockReact = jest.fn();
const mockDelete = jest.fn();
const mockRepost = jest.fn();
jest.mock("../../api/profile", () => ({
  ...jest.requireActual("../../api/profile"),
  getMyProfile: (...args: any[]) => mockMyProfile(...args),
  loadCachedProfile: (...args: any[]) => mockCachedProfile(...args)
}));
jest.mock("../../api/feed", () => ({
  ...jest.requireActual("../../api/feed"),
  listFeed: (...args: any[]) => mockListFeed(...args),
  savePost: (...args: any[]) => mockSave(...args),
  reactToPost: (...args: any[]) => mockReact(...args),
  deletePost: (...args: any[]) => mockDelete(...args),
  repostPost: (...args: any[]) => mockRepost(...args)
}));

import { ProfileScreen } from "../ProfileScreen";

function post(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    post_id: id,
    user_id: 7,
    body: `Post ${id}`,
    author: { id: 7, user_id: 7, display_name: "Me", username: "me" },
    reaction_counts: {},
    comment_count: 0,
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
  mockMyProfile.mockResolvedValue({
    user_id: 7,
    username: "me",
    public_player_id: "me",
    display_name: "Me"
  });
  mockListFeed.mockResolvedValue({ posts });
  const navigation = { navigate: jest.fn(), goBack: jest.fn() };
  const utils = render(<ProfileScreen navigation={navigation as never} />);
  await waitFor(() => expect(card(posts[0].id).post).toBeTruthy());
  await act(async () => undefined);
  return { ...utils, navigation };
}

beforeEach(() => {
  mockCardProps.length = 0;
  jest.clearAllMocks();
  mockCachedProfile.mockResolvedValue(null);
});

describe("ProfileScreen per-card busy state", () => {
  it("marks only the card being acted on, which a busyPostId scalar could do but", async () => {
    // ...only for one card. This is the paired assertion: card 1 busy AND card 2
    // not busy, from the same render. The scalar satisfied the first half; the
    // second half is what broke when two cards were in flight together.
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen([post(1), post(2)]);
    expect(card(1).busy).toBe(false);
    expect(card(2).busy).toBe(false);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onSave(post(1));
    });
    expect(card(1).busy).toBe(true);
    expect(card(2).busy).toBe(false);

    await tap(async () => {
      pending.resolve({ saved: true });
      await run;
    });
    expect(card(1).busy).toBe(false);
  });

  it("keeps two cards in flight at once, which a single number cannot represent", async () => {
    const first = deferred<any>();
    const second = deferred<any>();
    mockSave.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await renderScreen([post(1), post(2)]);

    let runOne!: Promise<unknown>;
    let runTwo!: Promise<unknown>;
    await tap(() => {
      runOne = card(1).onSave(post(1));
      runTwo = card(2).onSave(post(2));
    });
    expect(mockSave).toHaveBeenCalledTimes(2);
    expect(card(1).busy).toBe(true);
    expect(card(2).busy).toBe(true);

    // Settling one must not clear the other's marker.
    await tap(async () => {
      first.resolve({ saved: true });
      await runOne;
    });
    expect(card(1).busy).toBe(false);
    expect(card(2).busy).toBe(true);

    await tap(async () => {
      second.resolve({ saved: true });
      await runTwo;
    });
    expect(card(2).busy).toBe(false);
  });
});

describe("ProfileScreen save", () => {
  it("issues one request for a double tap, so a save cannot race an unsave", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    const onSave = card(1).onSave;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onSave(post(1));
      onSave(post(1));
    });
    expect(mockSave).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ saved: true });
      await first;
    });
    expect(card(1).post.saved).toBe(true);
  });

  it("shows the save immediately and then keeps the server's answer", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onSave(post(1));
    });
    expect(card(1).post.saved).toBe(true);
    await tap(async () => {
      pending.resolve({ saved: false });
      await run;
    });
    expect(card(1).post.saved).toBe(false);
  });

  it("rolls the save back and says why, instead of silently reverting", async () => {
    // The old `catch {}` reverted the bookmark with no message, which the user
    // cannot tell apart from a tap that never landed.
    mockSave.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { saved: false })]);
    await tap(() => card(1).onSave(post(1, { saved: false })));
    expect(Boolean(card(1).post.saved)).toBe(false);
    expect(queryByText(/offline/i)).toBeTruthy();
  });
});

describe("ProfileScreen reactions", () => {
  it("moves the count with the icon rather than waiting for the server", async () => {
    // The handler used to flip `viewer_reaction` only, so the number under the
    // button stayed stale until the next refresh.
    const pending = deferred<any>();
    mockReact.mockReturnValue(pending.promise);
    await renderScreen([post(1, { reaction_counts: { like: 4 } })]);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onReact(post(1, { reaction_counts: { like: 4 } }), "like");
    });
    expect(card(1).post.viewer_reaction).toBe("like");
    expect(card(1).post.reaction_counts).toEqual({ like: 5 });

    await tap(async () => {
      pending.resolve({ viewer_reaction: "like", reaction_counts: { like: 9 } });
      await run;
    });
    expect(card(1).post.reaction_counts).toEqual({ like: 9 });
  });

  it("lets the later of two taps win, so a slow first response cannot revert it", async () => {
    const slow = deferred<any>();
    mockReact.mockReturnValueOnce(slow.promise).mockResolvedValueOnce({ viewer_reaction: "love", reaction_counts: { love: 1 } });
    await renderScreen();

    const onReact = card(1).onReact;
    let first!: Promise<unknown>;
    await tap(async () => {
      first = onReact(post(1), "like");
      await onReact(post(1), "love");
    });
    expect(card(1).post.viewer_reaction).toBe("love");

    await tap(async () => {
      slow.resolve({ viewer_reaction: "like", reaction_counts: { like: 1 } });
      await first;
    });
    expect(card(1).post.viewer_reaction).toBe("love");
  });

  it("restores the previous reaction and count when the request fails", async () => {
    mockReact.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { viewer_reaction: "like", reaction_counts: { like: 3 } })]);
    await tap(() => card(1).onReact(post(1, { viewer_reaction: "like", reaction_counts: { like: 3 } }), "love"));
    expect(card(1).post.viewer_reaction).toBe("like");
    expect(card(1).post.reaction_counts).toEqual({ like: 3 });
    expect(queryByText(/offline/i)).toBeTruthy();
  });

  it("clears the reaction when the server reports it was withdrawn", async () => {
    mockReact.mockResolvedValue({ removed: true, reaction_counts: {} });
    await renderScreen([post(1, { viewer_reaction: "like", reaction_counts: { like: 1 } })]);
    await tap(() => card(1).onReact(post(1, { viewer_reaction: "like", reaction_counts: { like: 1 } }), "like"));
    expect(card(1).post.viewer_reaction).toBe("");
  });
});

describe("ProfileScreen repost", () => {
  it("hands PostCard a repost handler at all, which is the whole defect", async () => {
    // This screen rendered <PostCard> with no `onRepost` prop. PostCard's repost
    // control is a labelled, screen-reader-announced button that calls
    // `onRepost?.(post)`, so against undefined it ran and did nothing: every tap
    // was silently discarded and the count never moved. The other tests here would
    // all throw on `card(1).onRepost is not a function`, but they would throw for
    // the same reason a missing mock throws, so the wiring gets its own assertion.
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 1 });
    await renderScreen();
    expect(typeof card(1).onRepost).toBe("function");

    await tap(() => card(1).onRepost(post(1)));
    expect(mockRepost).toHaveBeenCalledTimes(1);
  });

  it("asks to create, not to undo, when the post is not reposted yet", async () => {
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 3 });
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(mockRepost).toHaveBeenCalledWith(1, { undo: false });
    expect(card(1).post.reposted).toBe(true);
    expect(card(1).post.repost_count).toBe(3);
  });

  it("undoes the repost on the second tap", async () => {
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

  it("reads is_reposted as well, since the serializer sends both spellings", async () => {
    // `_public_post` emits `reposted` and `is_reposted`. A handler that read only
    // one of them would treat an already-reposted post as fresh and send a create
    // where the viewer asked for an undo.
    mockRepost.mockResolvedValue({ ok: true, reposted: false, removed: true, repost_count: 2 });
    await renderScreen([post(1, { repost_count: 3, is_reposted: true })]);
    await tap(() => card(1).onRepost(post(1, { repost_count: 3, is_reposted: true })));
    expect(mockRepost).toHaveBeenCalledWith(1, { undo: true });
  });

  it("takes the server's count over its own optimistic guess", async () => {
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 18 });
    await renderScreen([post(1, { repost_count: 2, reposted: false })]);
    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(card(1).post.repost_count).toBe(18);
  });

  it("rolls the count back and says why when the repost fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1, { repost_count: 2, reposted: false })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 2, reposted: false })));
    expect(Boolean(card(1).post.reposted)).toBe(false);
    expect(card(1).post.repost_count).toBe(2);
    expect(queryByText(/offline/i)).toBeTruthy();
  });

  it("restores the reposted state when an undo fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen([post(1, { repost_count: 3, reposted: true })]);

    await tap(() => card(1).onRepost(post(1, { repost_count: 3, reposted: true })));
    expect(card(1).post.reposted).toBe(true);
    expect(card(1).post.repost_count).toBe(3);
  });

  it("issues one request for a double tap", async () => {
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

describe("ProfileScreen delete", () => {
  it("removes the post only after the server agrees", async () => {
    const pending = deferred<any>();
    mockDelete.mockReturnValue(pending.promise);
    const { queryByText } = await renderScreen([post(1), post(2)]);

    let run!: Promise<unknown>;
    await tap(() => {
      run = card(1).onDelete(post(1));
    });
    // Still present mid-flight: a post that vanishes and returns reads as the
    // feed resurrecting it.
    expect(card(1).post).toBeTruthy();

    await tap(async () => {
      pending.resolve({ ok: true });
      await run;
    });
    await waitFor(() => expect(queryByText("Post 1")).toBeNull());
  });

  it("keeps the post and reports the delete failure", async () => {
    mockDelete.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen([post(1)]);
    await tap(() => card(1).onDelete(post(1)));
    expect(card(1).post.id).toBe(1);
    expect(queryByText(/could not|offline|connection/i)).toBeTruthy();
  });

  it("issues one delete request for a double tap", async () => {
    const pending = deferred<any>();
    mockDelete.mockReturnValue(pending.promise);
    await renderScreen();
    const onDelete = card(1).onDelete;
    let first!: Promise<unknown>;
    await tap(() => {
      first = onDelete(post(1));
      onDelete(post(1));
    });
    expect(mockDelete).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ ok: true });
      await first;
    });
  });
});
