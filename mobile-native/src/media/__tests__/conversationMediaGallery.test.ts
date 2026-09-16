/**
 * The conversation media gallery, asserted as properties rather than as clicks.
 *
 * Each of the mutation cases the brief lists under §37 has a test here that goes
 * red when that mutation is applied. They are named for the mutation, not for
 * the function, because the thing being protected is a behaviour and the
 * function it currently lives in is an implementation detail:
 *
 *   - "viewer always opens the first media item"
 *   - "the gallery is built only from mounted message cells"
 *   - "all media loads at full resolution at once"
 *   - "an expired signed URL permanently breaks the item"
 *   - "a multi-media tile opens the wrong index"
 *   - "new media arriving moves the item you are looking at"
 */

const mockPulseApi = jest.fn();

jest.mock("../../api/pulseApi", () => ({
  pulseApi: (path: string, options?: unknown) => mockPulseApi(path, options)
}));

import { fetchConversationMedia } from "../../api/conversationMedia";
import {
  ConversationMediaItem,
  conversationMediaKey,
  conversationMediaKind,
  indexOfConversationMedia,
  mergeConversationMedia,
  MEDIA_PREFETCH_RADIUS,
  normalizeConversationMediaItem,
  normalizeConversationMediaPage,
  prefetchNeighbours,
  removeConversationMedia,
  sortConversationMedia
} from "../conversationMediaCollection";

function item(attachmentId: number, messageId = attachmentId * 10, overrides: Partial<ConversationMediaItem> = {}): ConversationMediaItem {
  return {
    key: conversationMediaKey(messageId, attachmentId),
    attachmentId,
    mediaUploadId: attachmentId + 5000,
    messageId,
    kind: "image",
    url: `https://cdn.example/${attachmentId}.jpg`,
    downloadUrl: `https://cdn.example/${attachmentId}.jpg`,
    thumbnailUrl: "",
    mimeType: "image/jpeg",
    width: 1600,
    height: 900,
    durationSeconds: 0,
    senderId: 7,
    senderName: "Maria Cherie",
    createdAt: "2026-01-01T00:00:00Z",
    ...overrides
  };
}

describe("what counts as gallery media", () => {
  it("accepts photos and videos", () => {
    expect(conversationMediaKind({ media_type: "image" })).toBe("image");
    expect(conversationMediaKind({ media_type: "gif" })).toBe("image");
    expect(conversationMediaKind({ media_type: "video" })).toBe("video");
    expect(conversationMediaKind({ mime_type: "video/quicktime" })).toBe("video");
    expect(conversationMediaKind({ mime_type: "image/heic" })).toBe("image");
  });

  it("refuses voice notes and documents, which keep their own players", () => {
    expect(conversationMediaKind({ media_type: "voice", mime_type: "audio/m4a" })).toBeNull();
    expect(conversationMediaKind({ media_type: "audio" })).toBeNull();
    expect(conversationMediaKind({ media_type: "file", mime_type: "application/pdf" })).toBeNull();
  });

  it("drops a non-media row from a page rather than admitting it at a smaller size", () => {
    const page = normalizeConversationMediaPage({
      items: [
        { id: 1, message_id: 10, media_type: "image" },
        { id: 2, message_id: 11, media_type: "voice", mime_type: "audio/m4a" },
        { id: 3, message_id: 12, media_type: "video" }
      ],
      total: 2
    });
    expect(page.items.map((entry) => entry.attachmentId)).toEqual([1, 3]);
  });
});

describe("identity", () => {
  it("keys on message AND attachment, because messenger row ids collide across tables", () => {
    // `media_upload_id` 4242 and `attachment_id` 4242 are rows in different
    // tables. A bare integer key would alias these two photos onto one slot.
    const first = item(4242, 900, { mediaUploadId: 11 });
    const second = item(11, 901, { mediaUploadId: 4242 });
    expect(first.key).not.toBe(second.key);
    expect(mergeConversationMedia([first], [second])).toHaveLength(2);
  });

  it("reads the transport id from either shape the wire uses", () => {
    expect(normalizeConversationMediaItem({ attachment_id: 8, message_id: 3, media_type: "image" })?.attachmentId).toBe(8);
    expect(normalizeConversationMediaItem({ id: 8, message_id: 3, media_type: "image" })?.attachmentId).toBe(8);
  });

  it("keeps media_upload_id separate from attachment_id, since only one addresses the grant", () => {
    const normalized = normalizeConversationMediaItem({ id: 77, media_upload_id: 4242, message_id: 3, media_type: "image" });
    expect(normalized?.attachmentId).toBe(77);
    expect(normalized?.mediaUploadId).toBe(4242);
  });

  it("keeps the downloadable file separate from the playback source", () => {
    // For a Mux-backed video the server sends two different resources: `url`
    // is the HLS manifest the player streams, `download_url` is the
    // progressive original Save to Photos needs. Dropping the second here is
    // invisible — the gallery falls back to `url`, the download of a playlist
    // succeeds, and the photo library is what reports the failure.
    const normalized = normalizeConversationMediaItem({
      id: 601,
      media_upload_id: 87,
      message_id: 1728,
      media_type: "video",
      url: "https://stream.mux.com/vod601.m3u8?token=eyJ.abc.def",
      download_url: "/api/messages/media/87/download"
    });
    expect(normalized?.url).toBe("https://stream.mux.com/vod601.m3u8?token=eyJ.abc.def");
    expect(normalized?.downloadUrl).toBe("/api/messages/media/87/download");
  });

  it("leaves the downloadable file empty rather than copying the playback source", () => {
    // Absent has to stay distinguishable from "same as url". Copying `url` in
    // would assert that a manifest is a file, which is the exact claim the
    // field exists to stop anyone making.
    const normalized = normalizeConversationMediaItem({
      id: 601,
      message_id: 1728,
      media_type: "video",
      url: "https://stream.mux.com/vod601.m3u8"
    });
    expect(normalized?.downloadUrl).toBe("");
  });
});

describe("order and merging", () => {
  it("orders by attachment id ascending, matching the thread top to bottom", () => {
    expect(sortConversationMedia([item(9), item(2), item(5)]).map((entry) => entry.attachmentId)).toEqual([2, 5, 9]);
  });

  it("re-sorts a page the server handed back in the wrong direction", () => {
    const page = normalizeConversationMediaPage({
      items: [
        { id: 30, message_id: 3, media_type: "image" },
        { id: 10, message_id: 1, media_type: "image" },
        { id: 20, message_id: 2, media_type: "image" }
      ]
    });
    expect(page.items.map((entry) => entry.attachmentId)).toEqual([10, 20, 30]);
  });

  it("dedupes on key, so the same photo arriving twice is one slot", () => {
    const merged = mergeConversationMedia([item(1), item(2)], [item(2), item(3)]);
    expect(merged.map((entry) => entry.attachmentId)).toEqual([1, 2, 3]);
  });

  it("lets a re-fetched item replace a stale one in place", () => {
    const stale = item(2, 20, { url: "https://cdn.example/expired.jpg" });
    const fresh = item(2, 20, { url: "https://cdn.example/fresh.jpg" });
    const merged = mergeConversationMedia([item(1), stale], [fresh]);
    expect(merged).toHaveLength(2);
    expect(merged[1].url).toBe("https://cdn.example/fresh.jpg");
  });

  /** MUTATION §37: "new media arriving moves the item you are looking at". */
  it("identifies the active item by key, so an older page landing does not move it", () => {
    const collection = [item(50), item(51), item(52)];
    const watching = collection[1].key;
    expect(indexOfConversationMedia(collection, watching)).toBe(1);
    const older = [item(10), item(20), item(30), item(40)];
    const grown = mergeConversationMedia(collection, older);
    // The numeric position moved by four. The photo did not.
    expect(indexOfConversationMedia(grown, watching)).toBe(5);
    expect(grown[5].key).toBe(watching);
  });
});

describe("opening on the tapped item", () => {
  /** MUTATION §37: "the viewer always opens the first media item". */
  it("resolves the seventeenth photo to index 16, not 0", () => {
    const collection = Array.from({ length: 43 }, (_, position) => item(position + 1));
    const seventeenth = collection[16];
    expect(indexOfConversationMedia(collection, seventeenth.key)).toBe(16);
    expect(indexOfConversationMedia(collection, seventeenth.key)).not.toBe(0);
  });

  /** MUTATION §37: "a multi-media tile opens the wrong index". */
  it("gives every tile of a multi-media message its own position", () => {
    // One message, four attachments. Tapping tile 3 must land on tile 3.
    const tiles = [item(101, 900), item(102, 900), item(103, 900), item(104, 900)];
    const collection = mergeConversationMedia([item(1), item(2)], tiles);
    expect(collection).toHaveLength(6);
    expect(indexOfConversationMedia(collection, tiles[2].key)).toBe(4);
    expect(new Set(collection.map((entry) => entry.key)).size).toBe(6);
  });

  it("returns -1 for an item that is not in the collection rather than defaulting to the first", () => {
    expect(indexOfConversationMedia([item(1), item(2)], "nope")).toBe(-1);
    expect(indexOfConversationMedia([item(1), item(2)], "")).toBe(-1);
  });
});

describe("prefetch window", () => {
  /** MUTATION §37: "the gallery loads every item at full resolution at once". */
  it("keeps the neighbours warm and nothing else", () => {
    const collection = Array.from({ length: 400 }, (_, position) => item(position + 1));
    const window = prefetchNeighbours(collection, 200);
    expect(window).toHaveLength(2);
    expect(window.map((entry) => entry.attachmentId)).toEqual([202, 200]);
    expect(MEDIA_PREFETCH_RADIUS).toBe(1);
  });

  it("puts the forward neighbour first, because forward is where a swipe goes", () => {
    const collection = [item(1), item(2), item(3), item(4), item(5)];
    expect(prefetchNeighbours(collection, 2, 2).map((entry) => entry.attachmentId)).toEqual([4, 2, 5, 1]);
  });

  it("does not run off either end of the collection", () => {
    const collection = [item(1), item(2), item(3)];
    expect(prefetchNeighbours(collection, 0).map((entry) => entry.attachmentId)).toEqual([2]);
    expect(prefetchNeighbours(collection, 2).map((entry) => entry.attachmentId)).toEqual([2]);
    expect(prefetchNeighbours(collection, -1)).toEqual([]);
    expect(prefetchNeighbours(collection, 9)).toEqual([]);
  });
});

describe("removal while the viewer is open", () => {
  it("lands on the next item, which is what a deletion looks like to a person", () => {
    const collection = [item(1), item(2), item(3)];
    const result = removeConversationMedia(collection, collection[1].key, 1);
    expect(result.items.map((entry) => entry.attachmentId)).toEqual([1, 3]);
    expect(result.index).toBe(1);
    expect(result.items[result.index].attachmentId).toBe(3);
  });

  it("clamps back one when the deleted item was last", () => {
    const collection = [item(1), item(2), item(3)];
    const result = removeConversationMedia(collection, collection[2].key, 2);
    expect(result.index).toBe(1);
    expect(result.items[result.index].attachmentId).toBe(2);
  });

  it("shifts the position down when the deletion was above you", () => {
    const collection = [item(1), item(2), item(3)];
    const result = removeConversationMedia(collection, collection[0].key, 2);
    expect(result.index).toBe(1);
    expect(result.items[result.index].attachmentId).toBe(3);
  });

  it("reports -1 for an emptied collection, which the caller reads as close", () => {
    expect(removeConversationMedia([item(1)], conversationMediaKey(10, 1), 0).index).toBe(-1);
  });

  it("is a no-op for an item that already went", () => {
    const collection = [item(1), item(2)];
    const result = removeConversationMedia(collection, "gone", 1);
    expect(result.items).toBe(collection);
    expect(result.index).toBe(1);
  });
});

describe("the media history request", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
    mockPulseApi.mockResolvedValue({ items: [], total: 0 });
  });

  /** MUTATION §37: "the gallery is built only from mounted message cells". */
  it("asks the server for the collection instead of reading rendered state", async () => {
    await fetchConversationMedia(42);
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi.mock.calls[0][0]).toBe("/api/pulse/communications/v2/conversations/42/media");
  });

  it("sends cursors as attachment ids, never as offsets", async () => {
    await fetchConversationMedia(42, { beforeId: 900, limit: 60 });
    expect(mockPulseApi.mock.calls[0][0]).toContain("before_id=900");
    expect(mockPulseApi.mock.calls[0][0]).not.toContain("offset");
    await fetchConversationMedia(42, { afterId: 901, limit: 60 });
    expect(mockPulseApi.mock.calls[1][0]).toContain("after_id=901");
  });

  it("prefers the forward cursor when a caller supplies both, rather than sending a contradiction", async () => {
    await fetchConversationMedia(42, { afterId: 5, beforeId: 9 });
    expect(mockPulseApi.mock.calls[0][0]).toContain("after_id=5");
    expect(mockPulseApi.mock.calls[0][0]).not.toContain("before_id");
  });

  it("omits a media_type filter so photos and videos come back interleaved", async () => {
    await fetchConversationMedia(42, { limit: 60 });
    expect(mockPulseApi.mock.calls[0][0]).not.toContain("media_type");
  });

  it("carries the whole-collection total through, so a counter can say 12 of 43", async () => {
    mockPulseApi.mockResolvedValue({
      items: [{ id: 5, message_id: 2, media_type: "image" }],
      total: 43,
      has_older: true,
      has_newer: false,
      oldest_id: 5,
      newest_id: 5
    });
    const page = await fetchConversationMedia(42);
    expect(page.total).toBe(43);
    expect(page.items).toHaveLength(1);
    expect(page.hasOlder).toBe(true);
    expect(page.hasNewer).toBe(false);
  });

  it("survives a payload with no items at all", async () => {
    mockPulseApi.mockResolvedValue({});
    const page = await fetchConversationMedia(42);
    expect(page.items).toEqual([]);
    expect(page.total).toBe(0);
  });
});
