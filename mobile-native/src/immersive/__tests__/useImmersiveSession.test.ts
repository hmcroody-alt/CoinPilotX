/**
 * The controller, pinned on the three races that are the reason it exists.
 *
 * The pure modules below it are already tested as value transformations. What
 * is left here is timing, and every one of these failures survives a demo: a
 * doubled request looks identical in the queue and costs the user data; a page
 * applied to a stale session makes the feed jump backwards and gets blamed on
 * the network; a latched-off retry storm is invisible until somebody reads a
 * server log.
 *
 * `fetchImmersivePage` is mocked with a deferred promise rather than an
 * immediate one on purpose. A mock that resolves instantly cannot express "two
 * swipes happened while the page was in flight", which is the only condition
 * any of these bugs occur under.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { useImmersiveSession } from "../useImmersiveSession";
import { fetchImmersivePage } from "../immersiveContinuation";
import { ImmersiveEntry, ImmersiveOrigin } from "../immersiveSession";
import { claimMediaPlayback, releaseMediaPlayback } from "../../core/mediaPlaybackCoordinator";

jest.mock("../immersiveContinuation", () => ({ fetchImmersivePage: jest.fn() }));
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined)
}));

const mockFetch = fetchImmersivePage as jest.MockedFunction<typeof fetchImmersivePage>;
const mockClaim = claimMediaPlayback as jest.MockedFunction<typeof claimMediaPlayback>;
const mockRelease = releaseMediaPlayback as jest.MockedFunction<typeof releaseMediaPlayback>;

const post = (id: number): ImmersiveEntry => ({ kind: "post", id });
const origin = (over: Partial<ImmersiveOrigin> = {}): ImmersiveOrigin => ({
  source: "HOME_FOR_YOU",
  entry: post(1),
  ...over
});

/** A page whose resolution this test controls. */
function deferred() {
  let resolve!: (value: Awaited<ReturnType<typeof fetchImmersivePage>>) => void;
  const promise = new Promise<Awaited<ReturnType<typeof fetchImmersivePage>>>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

const page = (entries: ImmersiveEntry[], over = {}) => ({
  entries,
  nextCursor: 99,
  exhausted: false,
  ...over
});

/** Ten seeded posts: far enough from the end that nothing fetches on mount. */
const tenPosts = Array.from({ length: 9 }, (_unused, index) => post(index + 2));

beforeEach(() => {
  jest.clearAllMocks();
  mockFetch.mockResolvedValue(page([]));
  mockClaim.mockResolvedValue(true);
});

describe("seeding", () => {
  it("opens on the tapped item with the origin's media already queued", () => {
    const { result } = renderHook(() => useImmersiveSession({ origin: origin({ entry: post(5) }), seed: tenPosts }));
    expect(result.current.activeEntry).toEqual(post(5));
    expect(result.current.session.queue[0]).toEqual(post(5));
  });

  it("mounts a bounded window rather than the whole queue", () => {
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    expect(result.current.slots.length).toBeLessThanOrEqual(6);
    expect(result.current.slots.filter((slot) => slot.role === "active")).toHaveLength(1);
  });

  it("makes no request at all while the seed is deep enough", async () => {
    renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    expect(mockFetch).not.toHaveBeenCalled();
  });
});

describe("race 1: the doubled request", () => {
  /**
   * Swiping twice near the end asks "should I continue?" twice, and both
   * answers are yes because the first page has not landed. Two identical
   * requests then return two identical pages; the second dedupes entirely away,
   * so the bug leaves no trace in the queue and shows up only as doubled
   * bandwidth on somebody's phone plan.
   */
  it("fires one request even when several swipes land while it is in flight", async () => {
    const gate = deferred();
    mockFetch.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2), post(3)] }));

    await act(async () => undefined);
    expect(mockFetch).toHaveBeenCalledTimes(1);

    act(() => result.current.next());
    act(() => result.current.next());
    await act(async () => undefined);
    expect(mockFetch).toHaveBeenCalledTimes(1);

    await act(async () => {
      gate.resolve(page([post(4)]));
      await gate.promise;
    });
  });

  it("reports that it is loading while the page is in flight, and stops after", async () => {
    const gate = deferred();
    mockFetch.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));

    await act(async () => undefined);
    expect(result.current.loading).toBe(true);

    await act(async () => {
      gate.resolve(page([post(4)], { exhausted: true }));
      await gate.promise;
    });
    expect(result.current.loading).toBe(false);
  });

  it("can fetch again once the first page has landed", async () => {
    mockFetch.mockResolvedValue(page([post(50)]));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalled());
    await act(async () => undefined);
    // Still near the end, so the latch must have cleared for a second attempt.
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThan(1));
    void result;
  });
});

describe("race 2: the page applied to a stale session", () => {
  /**
   * A page that started when the cursor was at 0 must be appended to the
   * session as it is when the page *lands*. Appending to the closure's captured
   * copy silently discards every swipe made during the round trip, and the user
   * watches the feed jump backwards.
   */
  it("keeps swipes that happened while the page was in flight", async () => {
    const gate = deferred();
    mockFetch.mockReturnValue(gate.promise);
    const { result } = renderHook(() =>
      useImmersiveSession({ origin: origin(), seed: [post(2), post(3), post(4)] })
    );

    await act(async () => undefined);
    act(() => result.current.next());
    act(() => result.current.next());
    expect(result.current.session.cursor).toBe(2);

    await act(async () => {
      gate.resolve(page([post(9)]));
      await gate.promise;
    });

    // The cursor survived the round trip, and the page still landed.
    expect(result.current.session.cursor).toBe(2);
    expect(result.current.session.queue.map((entry) => entry.id)).toEqual([1, 2, 3, 4, 9]);
  });

  it("does not resurrect an item the session had already seen", async () => {
    const gate = deferred();
    mockFetch.mockReturnValue(gate.promise);
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await act(async () => undefined);
    await act(async () => {
      gate.resolve(page([post(1), post(2), post(7)]));
      await gate.promise;
    });
    expect(result.current.session.queue.map((entry) => entry.id)).toEqual([1, 2, 7]);
  });

  it("carries the server's cursor into the next request", async () => {
    mockFetch.mockResolvedValueOnce(page([post(20)], { nextCursor: 140 }));
    // The follow-up must keep the cursor where the server put it, or it would
    // be this mock rather than the hook deciding what `nextCursor` ends up as.
    mockFetch.mockResolvedValue(page([], { nextCursor: 140, exhausted: true }));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await waitFor(() => expect(result.current.session.nextCursor).toBe(140));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ cursor: 140 })));
  });
});

describe("race 3: the retry storm", () => {
  /**
   * A failed continuation leaves `shouldContinueImmersive` still true, so the
   * next render asks again, fails again, and asks again. On a train that is a
   * tight loop against production -- and it is invisible from the app, which
   * simply looks like it is still loading.
   */
  it("does not re-fire on its own after a failure", async () => {
    mockFetch.mockResolvedValue(page([], { error: new Error("offline") }));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));

    await waitFor(() => expect(result.current.error).toBeTruthy());
    const attempts = mockFetch.mock.calls.length;
    await act(async () => undefined);
    await act(async () => undefined);
    expect(mockFetch).toHaveBeenCalledTimes(attempts);
  });

  it("does not call a failure the end of the feed", async () => {
    mockFetch.mockResolvedValue(page([], { error: new Error("offline") }));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    expect(result.current.session.canContinue).toBe(true);
  });

  it("tries again when asked deliberately", async () => {
    mockFetch.mockResolvedValue(page([], { error: new Error("offline") }));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await waitFor(() => expect(result.current.error).toBeTruthy());
    const attempts = mockFetch.mock.calls.length;

    mockFetch.mockResolvedValue(page([post(8)], { exhausted: true }));
    act(() => result.current.retry());
    await waitFor(() => expect(mockFetch.mock.calls.length).toBeGreaterThan(attempts));
    expect(result.current.error).toBeNull();
  });

  /**
   * Moving is a fresh intent. A user who swipes after a failure is asking for
   * the next item, and should not have to find a retry button to get a feed
   * that works again once the signal is back.
   */
  it("clears the failure when the user swipes", async () => {
    mockFetch.mockResolvedValue(page([], { error: new Error("offline") }));
    const { result } = renderHook(() =>
      useImmersiveSession({ origin: origin(), seed: [post(2), post(3)] })
    );
    await waitFor(() => expect(result.current.error).toBeTruthy());
    mockFetch.mockResolvedValue(page([post(8)], { exhausted: true }));
    act(() => result.current.next());
    await waitFor(() => expect(result.current.error).toBeNull());
  });
});

describe("participating in the playback ladder", () => {
  /**
   * `viewer` rather than a kind of its own. A new rung would mean choosing a
   * number relative to `call`, and that is how a media surface acquires the
   * ability to outrank a call by accident.
   */
  it("claims under the existing viewer priority, never a new rung", async () => {
    renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    expect(mockClaim).toHaveBeenCalledWith(expect.objectContaining({ kind: "viewer", id: "immersive:post:1" }));
  });

  /**
   * The claim is an event -- "this item is now the one playing" -- not a state
   * to re-assert. Claiming every render would re-notify every coordinator
   * subscriber on every frame of a scroll.
   */
  it("claims once per item, not once per render", async () => {
    const { result, rerender } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    const claims = mockClaim.mock.calls.length;
    rerender({});
    rerender({});
    await act(async () => undefined);
    expect(mockClaim).toHaveBeenCalledTimes(claims);
  });

  it("claims the new item when the cursor moves", async () => {
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    act(() => result.current.next());
    await act(async () => undefined);
    expect(mockClaim).toHaveBeenCalledWith(expect.objectContaining({ id: "immersive:post:2" }));
  });

  it("does not hold the session when the engine unmounts", async () => {
    const { unmount } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    mockRelease.mockClear();
    unmount();
    expect(mockRelease).toHaveBeenCalledWith("immersive:post:1");
  });

  /**
   * A refused claim is the correct outcome, not an error: it means a call is
   * up, and the ladder is doing its job. The engine must stay usable -- the
   * user is still looking at the item, still swiping -- it simply is not the
   * one making sound.
   */
  it("keeps working when the ladder refuses the claim because a call is up", async () => {
    mockClaim.mockResolvedValue(false);
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    act(() => result.current.next());
    expect(result.current.activeEntry).toEqual(post(2));
    expect(result.current.error).toBeNull();
  });
});

describe("moving and coming back", () => {
  it("clamps rather than landing on a blank frame", async () => {
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    act(() => result.current.goTo(999));
    expect(result.current.activeEntry).toEqual(post(2));
    act(() => result.current.previous());
    act(() => result.current.previous());
    expect(result.current.activeEntry).toEqual(post(1));
  });

  /**
   * §25. The tapped entry, not the one the cursor ended on -- the origin's own
   * list does not contain item 40 of somebody else's media and cannot scroll
   * to it, so returning that would land the user at the top of a feed they were
   * halfway down.
   */
  it("returns the origin to what was tapped, not to where the swiping ended", async () => {
    const { result } = renderHook(() =>
      useImmersiveSession({ origin: origin({ entry: post(1) }), seed: [post(2), post(3)] })
    );
    act(() => result.current.next());
    act(() => result.current.next());
    expect(result.current.activeEntry).toEqual(post(3));
    expect(result.current.close()).toEqual(post(1));
  });

  it("gives up the audio session on close", async () => {
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: tenPosts }));
    await act(async () => undefined);
    mockRelease.mockClear();
    act(() => {
      result.current.close();
    });
    expect(mockRelease).toHaveBeenCalledWith("immersive:post:1");
  });
});

describe("a source that cannot continue", () => {
  /**
   * A finite gallery that ends is the honest behaviour for a finite collection,
   * not a degraded session. It must also never ask -- SEARCH and GROUP have no
   * cursor to ask with.
   */
  it("never reaches for a next page from a shared post", async () => {
    const { result } = renderHook(() =>
      useImmersiveSession({ origin: origin({ source: "SHARED_POST" }), seed: [] })
    );
    await act(async () => undefined);
    await act(async () => undefined);
    expect(mockFetch).not.toHaveBeenCalled();
    expect(result.current.session.canContinue).toBe(false);
    expect(result.current.activeEntry).toEqual(post(1));
  });

  it("stops asking once the server says there is no more", async () => {
    mockFetch.mockResolvedValue(page([post(9)], { exhausted: true }));
    const { result } = renderHook(() => useImmersiveSession({ origin: origin(), seed: [post(2)] }));
    await waitFor(() => expect(result.current.session.canContinue).toBe(false));
    const attempts = mockFetch.mock.calls.length;
    await act(async () => undefined);
    await act(async () => undefined);
    expect(mockFetch).toHaveBeenCalledTimes(attempts);
  });
});

describe("asking for the right continuation", () => {
  it("passes the lane a reel was tapped in", async () => {
    renderHook(() =>
      useImmersiveSession({ origin: origin({ source: "REELS" }), seed: [], lane: "music" })
    );
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ lane: "music" })));
  });

  it("passes whose profile is being continued", async () => {
    const profile = { user_id: 9 };
    renderHook(() => useImmersiveSession({ origin: origin({ source: "PROFILE" }), seed: [], profile }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ profile })));
  });

  it("resumes from the origin's own cursor rather than from zero", async () => {
    renderHook(() => useImmersiveSession({ origin: origin({ cursor: 40 }), seed: [] }));
    await waitFor(() => expect(mockFetch).toHaveBeenCalledWith(expect.objectContaining({ cursor: 40 })));
  });
});
