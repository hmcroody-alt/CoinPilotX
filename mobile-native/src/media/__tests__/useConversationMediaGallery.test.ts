/**
 * The gallery hook: what happens between a tap and a full-screen photo.
 *
 * These tests exist because the interesting failures are all in the *sequence*,
 * not in any single function. Opening on the tapped item is easy to get right
 * for the first item and wrong for the seventeenth; resolving access URLs is
 * easy to get right for one photo and catastrophic for four hundred; a failed
 * grant is easy to turn into a spinner nobody can escape.
 */

const mockFetchConversationMedia = jest.fn();
const mockResolveAccess = jest.fn();
const mockIsProtected = jest.fn();

jest.mock("../../api/conversationMedia", () => ({
  fetchConversationMedia: (...args: unknown[]) => mockFetchConversationMedia(...args)
}));

jest.mock("../messengerMediaAccess", () => ({
  isProtectedMessengerMediaUrl: (url: string) => mockIsProtected(url),
  resolveMessengerMediaAccess: (id: number) => mockResolveAccess(id)
}));

import { act, renderHook, waitFor } from "@testing-library/react-native";

import { ConversationMediaItem, conversationMediaKey } from "../conversationMediaCollection";
import { gallerySeedFromMessage, useConversationMediaGallery } from "../useConversationMediaGallery";

function seed(attachmentId: number, messageId = attachmentId * 10, overrides: Record<string, unknown> = {}): ConversationMediaItem {
  return gallerySeedFromMessage({
    messageId,
    attachmentId,
    mediaUploadId: attachmentId + 5000,
    kind: "image",
    url: `https://cdn.example/${attachmentId}.jpg`,
    senderName: "Maria Cherie",
    ...overrides
  });
}

function page(ids: number[], extra: Record<string, unknown> = {}) {
  return {
    items: ids.map((id) => ({
      key: conversationMediaKey(id * 10, id),
      attachmentId: id,
      mediaUploadId: id + 5000,
      messageId: id * 10,
      kind: "image" as const,
      url: `https://cdn.example/${id}.jpg`,
      thumbnailUrl: "",
      mimeType: "image/jpeg",
      width: 0,
      height: 0,
      durationSeconds: 0,
      senderId: 7,
      senderName: "Maria Cherie",
      createdAt: ""
    })),
    total: 0,
    hasOlder: false,
    hasNewer: false,
    oldestId: ids.length ? Math.min(...ids) : 0,
    newestId: ids.length ? Math.max(...ids) : 0,
    ...extra
  };
}

beforeEach(() => {
  mockFetchConversationMedia.mockReset();
  mockResolveAccess.mockReset();
  mockIsProtected.mockReset();
  mockFetchConversationMedia.mockResolvedValue(page([]));
  mockIsProtected.mockReturnValue(false);
  mockResolveAccess.mockResolvedValue({ url: "", thumbnailUrl: "" });
});

describe("opening", () => {
  it("shows the tapped photo immediately, before any page has landed", () => {
    // Never resolves. The user still gets their picture.
    mockFetchConversationMedia.mockReturnValue(new Promise(() => undefined));
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    expect(result.current.visible).toBe(true);
    expect(result.current.index).toBe(0);
    expect(result.current.items[result.current.index].attachmentId).toBe(17);
  });

  /** MUTATION §37: "the viewer always opens the first media item". */
  it("stays on the tapped photo once the rest of the conversation arrives around it", async () => {
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number; afterId?: number }) =>
      Promise.resolve(query.beforeId ? page([14, 15, 16], { total: 43 }) : page([18, 19], { total: 43 }))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.items).toHaveLength(6));
    expect(result.current.index).toBe(3);
    expect(result.current.items[result.current.index].attachmentId).toBe(17);
    expect(result.current.total).toBe(43);
  });

  it("pages in BOTH directions from the tapped item, not from the top of the thread", async () => {
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(mockFetchConversationMedia).toHaveBeenCalledTimes(2));
    const queries = mockFetchConversationMedia.mock.calls.map((call) => call[1]);
    expect(queries.some((query) => query.beforeId === 17)).toBe(true);
    expect(queries.some((query) => query.afterId === 17)).toBe(true);
  });

  it("still opens when the network is gone, with a collection of exactly what was tapped", () => {
    const { result } = renderHook(() => useConversationMediaGallery(42, { online: false }));
    act(() => result.current.open(seed(17)));
    expect(mockFetchConversationMedia).not.toHaveBeenCalled();
    expect(result.current.visible).toBe(true);
    expect(result.current.items).toHaveLength(1);
    expect(result.current.loading).toBe(false);
  });

  it("keeps the photo on screen when one direction fails and the other does not", async () => {
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number }) =>
      query.beforeId ? Promise.reject(new Error("offline")) : Promise.resolve(page([18, 19]))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.items).toHaveLength(3));
    expect(result.current.items[result.current.index].attachmentId).toBe(17);
  });
});

describe("the collection moving underneath the viewer", () => {
  /** MUTATION §37: "new media arriving moves the item you are looking at". */
  it("does not move the active photo when older media pages in", async () => {
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number }) =>
      query.beforeId ? Promise.resolve(page([46, 47, 48, 49])) : Promise.resolve(page([]))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(50)));
    // Seeded: the tapped photo is the only thing in the collection, at 0.
    expect(result.current.index).toBe(0);
    const watching = result.current.items[0].attachmentId;

    // Four older photos land above it, so every numeric index shifts by four.
    // That is precisely the situation that used to move the viewer off the
    // photo the user was looking at, and the reason the active item is tracked
    // by key rather than by position.
    await waitFor(() => expect(result.current.items).toHaveLength(5));
    expect(result.current.index).toBe(4);
    expect(result.current.items[result.current.index].attachmentId).toBe(watching);
  });

  it("translates a swipe position straight back into the photo at that position", async () => {
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number }) =>
      query.beforeId ? Promise.resolve(page([1, 2])) : Promise.resolve(page([4, 5]))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(3)));
    await waitFor(() => expect(result.current.items).toHaveLength(5));
    act(() => result.current.setIndex(4));
    expect(result.current.index).toBe(4);
    expect(result.current.items[4].attachmentId).toBe(5);
  });

  it("refuses a position off either end rather than blanking the viewer", async () => {
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(3)));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    act(() => result.current.setIndex(99));
    expect(result.current.index).toBe(0);
    act(() => result.current.setIndex(-5));
    expect(result.current.index).toBe(0);
  });
});

describe("deletion while open", () => {
  it("lands on the next photo when the one you are looking at is deleted", async () => {
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number }) =>
      query.beforeId ? Promise.resolve(page([1])) : Promise.resolve(page([3]))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(2)));
    await waitFor(() => expect(result.current.items).toHaveLength(3));
    act(() => result.current.dropMessage(20));
    expect(result.current.visible).toBe(true);
    expect(result.current.items).toHaveLength(2);
    expect(result.current.items[result.current.index].attachmentId).toBe(3);
  });

  it("closes rather than showing a black frame when the last photo goes", async () => {
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(2)));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    act(() => result.current.dropMessage(20));
    expect(result.current.visible).toBe(false);
  });

  it("removes every tile of a multi-media message in one go", async () => {
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => {
      result.current.open(seed(1, 900));
    });
    act(() => {
      result.current.open(seed(2, 900));
      result.current.open(seed(3, 901));
    });
    await waitFor(() => expect(result.current.items).toHaveLength(3));
    act(() => result.current.dropMessage(900));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.items[0].attachmentId).toBe(3);
  });

  it("ignores a deletion for a message that has no media in the collection", async () => {
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(2)));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    act(() => result.current.dropMessage(999));
    expect(result.current.visible).toBe(true);
    expect(result.current.items).toHaveLength(1);
  });
});

describe("access URLs", () => {
  /** MUTATION §37: "the gallery loads everything at full resolution at once". */
  it("resolves the active item and its two neighbours, and nothing else in a 400-photo thread", async () => {
    mockIsProtected.mockReturnValue(true);
    mockResolveAccess.mockImplementation((id: number) => Promise.resolve({ url: `https://signed/${id}`, thumbnailUrl: "" }));
    const older = Array.from({ length: 200 }, (_, offset) => offset + 1);
    const newer = Array.from({ length: 199 }, (_, offset) => offset + 202);
    mockFetchConversationMedia.mockImplementation((_id: number, query: { beforeId?: number }) =>
      Promise.resolve(query.beforeId ? page(older) : page(newer))
    );
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(201)));
    await waitFor(() => expect(result.current.items).toHaveLength(400));
    await waitFor(() => expect(Object.keys(result.current.resolved).length).toBe(3));
    expect(mockResolveAccess.mock.calls.length).toBeLessThanOrEqual(3);
  });

  /** MUTATION §37: "an expired signed URL permanently breaks the item". */
  it("mints a fresh grant per item rather than trusting the URL the message row carried", async () => {
    mockIsProtected.mockReturnValue(true);
    mockResolveAccess.mockResolvedValue({ url: "https://signed/fresh", thumbnailUrl: "https://signed/thumb" });
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.resolved[result.current.items[0].key]?.url).toBe("https://signed/fresh"));
    expect(mockResolveAccess).toHaveBeenCalledWith(5017);
  });

  it("states a failed grant instead of spinning on it forever", async () => {
    mockIsProtected.mockReturnValue(true);
    mockResolveAccess.mockRejectedValue(new Error("410 gone"));
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.resolved[result.current.items[0].key]).toBeDefined());
    expect(result.current.resolved[result.current.items[0].key]).toEqual({ url: "", thumbnailUrl: "", unavailable: true });
  });

  it("does not ask for a grant for a URL that is already loadable", async () => {
    mockIsProtected.mockReturnValue(false);
    const { result } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.resolved[result.current.items[0].key]).toBeDefined());
    expect(mockResolveAccess).not.toHaveBeenCalled();
    expect(result.current.resolved[result.current.items[0].key].url).toBe("https://cdn.example/17.jpg");
  });

  it("asks once per item, not once per render", async () => {
    mockIsProtected.mockReturnValue(true);
    mockResolveAccess.mockResolvedValue({ url: "https://signed/one", thumbnailUrl: "" });
    const { result, rerender } = renderHook(() => useConversationMediaGallery(42));
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(mockResolveAccess).toHaveBeenCalledTimes(1));
    rerender(undefined);
    rerender(undefined);
    expect(mockResolveAccess).toHaveBeenCalledTimes(1);
  });
});

describe("switching conversations", () => {
  it("drops the previous conversation's photos rather than showing them under a new title", async () => {
    const { result, rerender } = renderHook(({ id }: { id: number }) => useConversationMediaGallery(id), {
      initialProps: { id: 42 }
    });
    act(() => result.current.open(seed(17)));
    await waitFor(() => expect(result.current.items).toHaveLength(1));
    rerender({ id: 43 });
    expect(result.current.items).toEqual([]);
    expect(result.current.visible).toBe(false);
    expect(result.current.index).toBe(-1);
  });
});

describe("the seed a bubble hands over", () => {
  it("carries the identity the gallery keys on, with both media ids kept apart", () => {
    const built = gallerySeedFromMessage({
      messageId: 900,
      attachmentId: 77,
      mediaUploadId: 4242,
      kind: "video",
      url: "https://cdn.example/clip.m3u8",
      durationSeconds: 12.5,
      senderName: "Maria Cherie"
    });
    expect(built.key).toBe("900:77");
    expect(built.attachmentId).toBe(77);
    expect(built.mediaUploadId).toBe(4242);
    expect(built.kind).toBe("video");
    expect(built.durationSeconds).toBe(12.5);
    expect(built.senderName).toBe("Maria Cherie");
  });

  it("fills every field, so a seeded item and a fetched item are the same shape", () => {
    const built = gallerySeedFromMessage({ messageId: 1, attachmentId: 2, kind: "image", url: "u" });
    expect(built.mediaUploadId).toBe(0);
    expect(built.thumbnailUrl).toBe("");
    expect(built.width).toBe(0);
    expect(built.senderName).toBe("");
    expect(built.createdAt).toBe("");
  });
});
