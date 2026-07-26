/**
 * Post-level social actions on PostDetailScreen: react, save, repost, share.
 *
 * PostDetailScreen.comments.test.tsx covers the comment thread and mocks PostCard
 * away, so the post's own action row has no behavioural coverage there. The
 * actions live inside PostCard, so this suite mocks PostCard as a prop recorder
 * and drives the handlers the screen passes down. That is deliberate: rendering
 * the real PostCard would test PostCard's buttons, whereas what needs pinning
 * here is the screen's concurrency, rollback and share contract.
 *
 * The concurrency assertions matter because this screen previously wrote a `busy`
 * scalar that nothing read, so a double tap issued two requests. useSocialActionGuard
 * has unit coverage of its own, but "the screen actually routes through it" is a
 * separate claim and needs its own test.
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
jest.mock("expo-haptics", () => ({ impactAsync: jest.fn(), ImpactFeedbackStyle: { Light: "light" } }));
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return { ContentTranslation: ({ text }: any) => ReactActual.createElement(Text, null, text) };
});
jest.mock("../../core/eventSync", () => ({
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined),
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

// PostCard as a recorder. Every render pushes its props, so a test can read the
// post the screen is currently showing (optimistic value, then settled value)
// and invoke the handler the screen wired to a given control.
const mockCardProps: any[] = [];
jest.mock("../../components/PostCard", () => ({
  PostCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockShare = jest.fn().mockResolvedValue({ ok: true });
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: (...args: any[]) => mockShare(...args) }));

const mockDetail = jest.fn();
const mockList = jest.fn();
const mockCached = jest.fn().mockResolvedValue(null);
const mockReact = jest.fn();
const mockSave = jest.fn();
const mockRepost = jest.fn();
jest.mock("../../api/feed", () => ({
  ...jest.requireActual("../../api/feed"),
  getPostDetail: (...args: any[]) => mockDetail(...args),
  listPostComments: (...args: any[]) => mockList(...args),
  loadCachedPostDetail: (...args: any[]) => mockCached(...args),
  reactToPost: (...args: any[]) => mockReact(...args),
  savePost: (...args: any[]) => mockSave(...args),
  repostPost: (...args: any[]) => mockRepost(...args)
}));

import { POST_COMMENT_PAGE_SIZE } from "../../api/feed";
import { PostDetailScreen } from "../PostDetailScreen";

const POST_ID = 5;

function post(overrides: Record<string, unknown> = {}) {
  return {
    id: POST_ID,
    post_id: POST_ID,
    user_id: 9,
    body: "A post under test.",
    author: { id: 9, user_id: 9, display_name: "Fixture Author", username: "fixture_author" },
    comment_count: 0,
    reaction_counts: {},
    repost_count: 0,
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
 * calling a handler outside act() produces an "update not wrapped in act" warning
 * on every test — noise that teaches people to ignore React's warnings.
 */
async function tap(body: () => unknown | Promise<unknown>): Promise<void> {
  await act(async () => {
    await body();
  });
}

/** The props of the most recent PostCard render — i.e. what the user is seeing now. */
function card() {
  const latest = mockCardProps[mockCardProps.length - 1];
  if (!latest) throw new Error("PostCard has not rendered");
  return latest;
}

async function renderScreen(overrides: Record<string, unknown> = {}) {
  mockDetail.mockResolvedValue({ post: post(overrides), comments: [] });
  mockList.mockResolvedValue({
    comments: [],
    flat: [],
    total: 0,
    hasMore: false,
    limit: POST_COMMENT_PAGE_SIZE,
    offset: 0
  });
  const navigation = { navigate: jest.fn(), goBack: jest.fn() };
  const utils = render(
    <PostDetailScreen route={{ params: { postId: POST_ID } } as never} navigation={navigation as never} />
  );
  await waitFor(() => expect(card().post).toBeTruthy());
  return { ...utils, navigation };
}

beforeEach(() => {
  mockCardProps.length = 0;
  jest.clearAllMocks();
  mockCached.mockResolvedValue(null);
  mockShare.mockResolvedValue({ ok: true });
});

describe("PostDetailScreen save", () => {
  it("issues one request for a double tap, so a save cannot race an unsave", async () => {
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    const onSave = card().onSave;
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    await tap(() => {
      first = onSave(post());
      second = onSave(post());
    });
    expect(mockSave).toHaveBeenCalledTimes(1);

    await tap(async () => {
      pending.resolve({ saved: true });
      await Promise.all([first, second]);
    });
    expect(card().post.saved).toBe(true);
  });

  it("shows the save immediately and then keeps the server's answer", async () => {
    // Optimistic first: the flag flips before the request settles. The server is
    // authoritative afterwards, which is what stops a rejected save from looking
    // saved until the next refresh.
    const pending = deferred<any>();
    mockSave.mockReturnValue(pending.promise);
    await renderScreen();

    let run!: Promise<unknown>;
    await tap(() => {
      run = card().onSave(post());
    });
    expect(card().post.saved).toBe(true);
    await tap(async () => {
      pending.resolve({ saved: false });
      await run;
    });
    expect(card().post.saved).toBe(false);
  });

  it("rolls the save back and reports the failure", async () => {
    mockSave.mockRejectedValue(new Error("Network request failed"));
    const { queryByText } = await renderScreen({ saved: false });
    await tap(() => card().onSave(post()));
    expect(card().post.saved).toBe(false);
    expect(queryByText(/offline/i)).toBeTruthy();
  });
});

describe("PostDetailScreen repost", () => {
  it("issues one request for a double tap, so it cannot create two repost rows", async () => {
    // The route is idempotent now, but the guard still has to hold: two requests
    // in flight means the second one's response decides the button, and whichever
    // lands last wins. One request per tap keeps the flag deterministic.
    const pending = deferred<any>();
    mockRepost.mockReturnValue(pending.promise);
    await renderScreen();

    const onRepost = card().onRepost;
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;
    await tap(() => {
      first = onRepost(post());
      second = onRepost(post());
    });
    expect(mockRepost).toHaveBeenCalledTimes(1);
    await tap(async () => {
      pending.resolve({ ok: true, reposted: true, repost_count: 1 });
      await Promise.all([first, second]);
    });
  });

  it("asks to create, not to undo, when the post is not reposted yet", async () => {
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 5 });
    await renderScreen({ repost_count: 4, reposted: false });
    await tap(() => card().onRepost(post()));
    expect(mockRepost).toHaveBeenCalledWith(POST_ID, { undo: false });
    expect(card().post.reposted).toBe(true);
    expect(card().post.repost_count).toBe(5);
  });

  it("undoes the repost on the second tap instead of stopping at one-way", async () => {
    // This is the defect the old assertion pinned in place: the screen bumped the
    // count once and refused to go back, because the route had no DELETE branch.
    // It does now, so a second tap must send `undo` and drop the count.
    mockRepost
      .mockResolvedValueOnce({ ok: true, reposted: true, repost_count: 5 })
      .mockResolvedValueOnce({ ok: true, reposted: false, removed: true, repost_count: 4 });
    await renderScreen({ repost_count: 4, reposted: false });

    await tap(() => card().onRepost(post()));
    expect(card().post.reposted).toBe(true);
    expect(card().post.repost_count).toBe(5);

    await tap(() => card().onRepost(card().post));
    expect(mockRepost).toHaveBeenLastCalledWith(POST_ID, { undo: true });
    expect(card().post.reposted).toBe(false);
    expect(card().post.repost_count).toBe(4);
  });

  it("takes the server's count over its own optimistic guess", async () => {
    // The optimistic bump is 4 -> 5, but other people repost too, so only the
    // server knows the real total. A screen that kept its own arithmetic would
    // drift further from the truth on every tap.
    mockRepost.mockResolvedValue({ ok: true, reposted: true, repost_count: 12 });
    await renderScreen({ repost_count: 4, reposted: false });
    await tap(() => card().onRepost(post()));
    expect(card().post.repost_count).toBe(12);
  });

  it("rolls the count back when the repost fails", async () => {
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen({ repost_count: 4 });
    await tap(() => card().onRepost(post()));
    expect(card().post.repost_count).toBe(4);
    expect(Boolean(card().post.reposted)).toBe(false);
  });

  it("restores the reposted state when an undo fails", async () => {
    // Rollback has to work in both directions. A failed undo that left the button
    // showing not-reposted would hide a repost the viewer still owns.
    mockRepost.mockRejectedValue(new Error("Network request failed"));
    await renderScreen({ repost_count: 4, reposted: true });
    await tap(() => card().onRepost(post({ repost_count: 4, reposted: true })));
    expect(mockRepost).toHaveBeenCalledWith(POST_ID, { undo: true });
    expect(card().post.reposted).toBe(true);
    expect(card().post.repost_count).toBe(4);
  });
});

describe("PostDetailScreen reactions", () => {
  it("lets the later of two taps win, so a slow first response cannot revert it", async () => {
    // Changing your mind mid-flight is legitimate, so the second tap is allowed
    // through; the guard's sequence check is what discards the stale answer.
    const slow = deferred<any>();
    mockReact.mockReturnValueOnce(slow.promise).mockResolvedValueOnce({ reaction_type: "love", reaction_counts: { love: 1 } });
    await renderScreen();

    const onReact = card().onReact;
    let first!: Promise<unknown>;
    await tap(async () => {
      first = onReact(post(), "like");
      await onReact(post(), "love");
    });
    expect(card().post.viewer_reaction).toBe("love");

    await tap(async () => {
      slow.resolve({ reaction_type: "like", reaction_counts: { like: 1 } });
      await first;
    });
    expect(card().post.viewer_reaction).toBe("love");
  });

  it("clears the reaction when the server reports it was withdrawn", async () => {
    mockReact.mockResolvedValue({ removed: true, reaction_counts: {} });
    await renderScreen({ viewer_reaction: "like", reaction_counts: { like: 1 } });
    await tap(() => card().onReact(post({ viewer_reaction: "like" }), "like"));
    expect(card().post.viewer_reaction).toBe("");
  });

  it("restores the previous reaction when the request fails", async () => {
    mockReact.mockRejectedValue(new Error("Network request failed"));
    await renderScreen({ viewer_reaction: "like", reaction_counts: { like: 3 } });
    await tap(() => card().onReact(post({ viewer_reaction: "like" }), "love"));
    expect(card().post.viewer_reaction).toBe("like");
    expect(card().post.reaction_counts).toEqual({ like: 3 });
  });
});

describe("PostDetailScreen share", () => {
  it("shares through the native sheet with a deep link, not a bare copied URL", async () => {
    await renderScreen();
    await tap(() => card().onShare(post({ title: "Fixture title", thumbnail_url: "https://cdn.example/p.jpg" })));
    expect(mockShare).toHaveBeenCalledTimes(1);
    const payload = mockShare.mock.calls[0][0];
    expect(payload.kind).toBe("post");
    expect(payload.url).toMatch(new RegExp(`${POST_ID}$`));
    expect(payload.title).toBe("Fixture title");
    expect(payload.description).toBe("A post under test.");
    expect(payload.author).toBe("Fixture Author");
    expect(payload.previewImageUrl).toBe("https://cdn.example/p.jpg");
  });
});

describe("PostDetailScreen owner-only controls", () => {
  it("offers delete to the author and withholds it from everyone else", async () => {
    await renderScreen({ user_id: 7, author: { id: 7, user_id: 7, display_name: "Me", username: "me" } });
    expect(typeof card().onDelete).toBe("function");

    mockCardProps.length = 0;
    await renderScreen({ user_id: 9 });
    expect(card().onDelete).toBeUndefined();
  });
});
