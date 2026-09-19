/**
 * The continuation layer, pinned at the boundary it actually owns: mapping.
 *
 * The APIs are mocked, and that is the point rather than a shortcut. What can
 * go wrong here is not "the request failed" — it is that a livestream enters the
 * immersive queue, that a text post becomes a blank frame, that a reel arriving
 * through the feed gets stamped `post` and collides with the post of the same
 * number, or that a server cursor of 0 turns into an unbounded request loop.
 * Every one of those is a transformation, and every one is silent in production.
 */
import { fetchImmersivePage } from "../immersiveContinuation";
import { listFeed } from "../../api/feed";
import { listReels } from "../../api/reels";
import { listPublicProfilePosts } from "../../api/profile";

jest.mock("../../api/feed", () => ({
  ...jest.requireActual("../../api/feed"),
  listFeed: jest.fn()
}));
jest.mock("../../api/reels", () => ({ listReels: jest.fn() }));
jest.mock("../../api/profile", () => ({ listPublicProfilePosts: jest.fn() }));

const mockFeed = listFeed as jest.MockedFunction<typeof listFeed>;
const mockReels = listReels as jest.MockedFunction<typeof listReels>;
const mockProfile = listPublicProfilePosts as jest.MockedFunction<typeof listPublicProfilePosts>;

/** A post the feed would render: one drawable image. */
const withMedia = (over: Record<string, unknown> = {}) => ({
  id: 38,
  post_id: 38,
  body: "",
  media: [{ media_type: "image", media_url: "https://cdn.pulsesoc.com/a.jpg" }],
  author: { user_id: 7 },
  ...over
});

const feedResponse = (posts: unknown[], over: Record<string, unknown> = {}) =>
  ({ ok: true, posts, has_more: true, next_offset: 60, ...over }) as unknown as Awaited<ReturnType<typeof listFeed>>;

const reelsResponse = (reels: unknown[], over: Record<string, unknown> = {}) =>
  ({ ok: true, reels, has_more: true, next_offset: 24, ...over }) as unknown as Awaited<ReturnType<typeof listReels>>;

beforeEach(() => {
  jest.clearAllMocks();
});

describe("asking the origin's own endpoint", () => {
  /**
   * Ranking is a property of the query. Somebody who taps a post in Following
   * and swipes onward is still in Following, and answering with `for_you` from
   * item two onward is a silent re-ranking they never asked for.
   */
  it.each([
    ["for you", "HOME_FOR_YOU", "for_you"],
    ["following", "HOME_FOLLOWING", "following"],
    ["friends", "HOME_FRIENDS", "friends"]
  ] as const)("continues %s from the same feed key the tab uses", async (_label, source, feedKey) => {
    mockFeed.mockResolvedValue(feedResponse([withMedia()]));
    await fetchImmersivePage({ source, cursor: 40 });
    expect(mockFeed).toHaveBeenCalledWith(expect.objectContaining({ feed: feedKey, tab: feedKey, offset: 40 }));
  });

  it("asks a profile only for its media posts", async () => {
    mockProfile.mockResolvedValue({ ok: true, posts: [withMedia()], has_more: false, next_offset: 21 } as never);
    await fetchImmersivePage({ source: "PROFILE", cursor: 20, profile: { user_id: 9 } });
    expect(mockProfile).toHaveBeenCalledWith({ user_id: 9 }, expect.objectContaining({ offset: 20, mediaOnly: true }));
  });

  it("stays in the lane the reel was tapped in", async () => {
    mockReels.mockResolvedValue(reelsResponse([{ id: 5, reel_id: 5 }]));
    await fetchImmersivePage({ source: "REELS", cursor: 8, lane: "music" });
    expect(mockReels).toHaveBeenCalledWith(expect.objectContaining({ lane: "music", offset: 8 }));
  });

  it("carries the server's cursor and its word on whether more remains", async () => {
    mockFeed.mockResolvedValue(feedResponse([withMedia()], { next_offset: 61, has_more: false }));
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.nextCursor).toBe(61);
    expect(page.exhausted).toBe(true);
  });

  /**
   * A page can filter down to nothing -- all text posts, all livestreams --
   * while more media genuinely remains behind it. Inferring the end of the feed
   * from an empty result would truncate the session one page early.
   */
  it("does not call a page that filtered down to nothing the end of the feed", async () => {
    mockFeed.mockResolvedValue(feedResponse([withMedia({ media: [] })], { has_more: true }));
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries).toEqual([]);
    expect(page.exhausted).toBe(false);
  });
});

describe("what becomes an entry", () => {
  it("stamps kind from the endpoint the page came through, not from the payload", async () => {
    // A reel-shaped row arriving through the feed is still a post here: the door
    // it came through is what decides, so it cannot collide with reel 38.
    mockFeed.mockResolvedValue(feedResponse([withMedia({ id: 38, content_type: "video", post_type: "reel" })]));
    mockReels.mockResolvedValue(reelsResponse([{ id: 38, reel_id: 38, author: { user_id: 7 } }]));
    const fromFeed = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    const fromReels = await fetchImmersivePage({ source: "REELS", cursor: 8 });
    expect(fromFeed.entries).toEqual([{ kind: "post", id: 38, authorId: 7 }]);
    expect(fromReels.entries).toEqual([{ kind: "reel", id: 38, authorId: 7 }]);
  });

  /**
   * A text post is a real post and belongs in the feed. In a full-screen media
   * queue it is a frame the user swipes into and finds blank.
   */
  it("drops a post with nothing to look at", async () => {
    mockFeed.mockResolvedValue(
      feedResponse([withMedia({ id: 1, post_id: 1, media: [] }), withMedia({ id: 2, post_id: 2 })])
    );
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries).toEqual([{ kind: "post", id: 2, authorId: 7 }]);
  });

  /**
   * The livestream hard lock, enforced at the door. A live session must never
   * enter the immersive queue -- and it is checked by `content_type` rather than
   * left to the negative id `normalizeReel` gives it, because relying on the
   * sign of a number another module chose breaks silently and breaks open.
   */
  it.each([
    ["typed live", { id: 5, reel_id: 5, content_type: "live" }],
    ["typed live as a post_type", { id: 6, reel_id: 6, post_type: "live" }],
    ["carrying a live session id", { id: 7, reel_id: 7, live_session_id: 900 }],
    ["carrying a nested live session", { id: 8, reel_id: 8, live: { live_session_id: 900 } }]
  ])("refuses a reel that is %s", async (_label, row) => {
    mockReels.mockResolvedValue(reelsResponse([row, { id: 9, reel_id: 9 }]));
    const page = await fetchImmersivePage({ source: "REELS", cursor: 8 });
    expect(page.entries).toEqual([{ kind: "reel", id: 9 }]);
  });

  it("drops an item with no usable id rather than queueing a frame nobody can open", async () => {
    mockFeed.mockResolvedValue(feedResponse([withMedia({ id: 0, post_id: 0 }), withMedia({ id: 4, post_id: 4 })]));
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries).toEqual([{ kind: "post", id: 4, authorId: 7 }]);
  });

  it("prefers the canonical post id over the row id when they disagree", async () => {
    mockFeed.mockResolvedValue(feedResponse([withMedia({ id: 999, post_id: 38 })]));
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries[0].id).toBe(38);
  });

  it("omits the author rather than inventing one", async () => {
    mockFeed.mockResolvedValue(feedResponse([withMedia({ author: {}, user_id: 0 })]));
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries).toEqual([{ kind: "post", id: 38 }]);
  });
});

describe("sources with no next page", () => {
  /**
   * `sourceCanContinue` already refuses these, so reaching here means a caller
   * asked anyway. The answer is the truth rather than an invented query against
   * a surface that has no cursor at all.
   */
  it.each([["a shared post", "SHARED_POST"], ["search", "SEARCH"], ["a group", "GROUP"]] as const)(
    "fetches nothing for %s",
    async (_label, source) => {
      const page = await fetchImmersivePage({ source, cursor: 10 });
      expect(page).toEqual({ entries: [], nextCursor: 10, exhausted: true });
      expect(mockFeed).not.toHaveBeenCalled();
      expect(mockReels).not.toHaveBeenCalled();
      expect(mockProfile).not.toHaveBeenCalled();
    }
  );

  it("does not guess whose profile to continue when it was not told", async () => {
    const page = await fetchImmersivePage({ source: "PROFILE", cursor: 20 });
    expect(page.exhausted).toBe(true);
    expect(mockProfile).not.toHaveBeenCalled();
  });
});

describe("the offset-zero refusal", () => {
  /**
   * Offset 0 can only return the page the session was seeded from, so the round
   * trip buys nothing -- every item dedupes away. The reason it is refused
   * rather than merely wasteful: a server answering `next_offset: 0` would leave
   * the session permanently one swipe from the end and permanently requesting
   * the same page, which is an unbounded request loop against production wearing
   * the costume of a working feed.
   */
  it.each([
    ["zero", 0],
    ["negative", -5],
    ["not a number", NaN]
  ])("makes no request at all for a cursor of %s", async (_label, cursor) => {
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor });
    expect(page).toEqual({ entries: [], nextCursor: 0, exhausted: true });
    expect(mockFeed).not.toHaveBeenCalled();
  });
});

describe("when the network fails", () => {
  /**
   * A dropped connection means "not right now", not "there is no more media".
   * Ending the session on a timeout permanently truncates it because of one bad
   * moment on a train.
   */
  it("leaves the session able to try again rather than calling it the end", async () => {
    const boom = new Error("offline");
    mockFeed.mockRejectedValue(boom);
    const page = await fetchImmersivePage({ source: "HOME_FOR_YOU", cursor: 40 });
    expect(page.entries).toEqual([]);
    expect(page.exhausted).toBe(false);
    expect(page.nextCursor).toBe(40);
    expect(page.error).toBe(boom);
  });

  it("reports the failure rather than swallowing it into an empty page", async () => {
    mockReels.mockRejectedValue(new Error("500"));
    expect((await fetchImmersivePage({ source: "REELS", cursor: 8 })).error).toBeTruthy();
  });
});
