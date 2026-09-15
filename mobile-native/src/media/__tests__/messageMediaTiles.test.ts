/**
 * §21: a message carrying several photos, split back into its tiles.
 *
 * The one thing worth holding onto while reading these: a tile is not a new
 * kind of object. It is a `ConversationMediaItem`, produced by the same
 * normalizer and keyed the same way as the server-paged collection, which is
 * why "opening tile 3 opens index 3" is a property rather than a coincidence.
 * Most of the tests below are really checks that that identity survives.
 */

import { conversationMediaKey, indexOfConversationMedia } from "../conversationMediaCollection";
import { isMultiMediaMessage, mediaTileColumns, messageMediaTiles } from "../messageMediaTiles";

function attachment(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 900,
    message_id: 77,
    media_upload_id: 4400,
    media_type: "image",
    mime_type: "image/jpeg",
    url: "https://cdn.example/a.jpg",
    thumbnail_url: "https://cdn.example/a-thumb.jpg",
    width: 1200,
    height: 900,
    sender_user_id: 12,
    sender_display_name: "Maria Cherie",
    created_at: "2026-09-01T10:00:00Z",
    ...overrides
  };
}

describe("splitting a message into tiles", () => {
  it("returns nothing for a message with no attachments", () => {
    expect(messageMediaTiles(77, undefined)).toEqual([]);
    expect(messageMediaTiles(77, [])).toEqual([]);
  });

  it("keeps a single attachment as a single tile", () => {
    const tiles = messageMediaTiles(77, [attachment()]);
    expect(tiles).toHaveLength(1);
    expect(tiles[0].key).toBe("77:900");
  });

  /**
   * The canonical order is ascending attachment id — the same key the server
   * orders its pages by. A message whose attachments arrive shuffled must still
   * render in the order the gallery will hold them, or tile 3 and index 3 name
   * different photos.
   */
  it("orders tiles the way the collection orders them, not the way they arrived", () => {
    const tiles = messageMediaTiles(77, [
      attachment({ id: 903 }),
      attachment({ id: 901 }),
      attachment({ id: 902 })
    ]);
    expect(tiles.map((tile) => tile.attachmentId)).toEqual([901, 902, 903]);
  });

  /**
   * §28. Voice notes stay a waveform player and documents stay a document card.
   * Both paths run the same classifier, so this is really asserting that the
   * tile builder did not grow a second one.
   */
  it("drops voice notes and documents rather than tiling them", () => {
    const tiles = messageMediaTiles(77, [
      attachment({ id: 901 }),
      attachment({ id: 902, media_type: "voice", mime_type: "audio/m4a" }),
      attachment({ id: 903, media_type: "document", mime_type: "application/pdf" }),
      attachment({ id: 904, media_type: "video", mime_type: "video/mp4", duration_seconds: 12 })
    ]);
    expect(tiles.map((tile) => tile.attachmentId)).toEqual([901, 904]);
    expect(tiles.map((tile) => tile.kind)).toEqual(["image", "video"]);
  });

  it("skips a row with no usable attachment id instead of keying it as zero", () => {
    const tiles = messageMediaTiles(77, [attachment({ id: 0, attachment_id: 0 }), attachment({ id: 905 })]);
    expect(tiles.map((tile) => tile.attachmentId)).toEqual([905]);
  });

  /**
   * A row cached before the payload carried `message_id` normalizes to message
   * 0, which keys as `0:<attachmentId>` and matches nothing in the collection.
   * That drift is invisible in the tile itself — it shows up much later as "the
   * tile opens the right photo but the counter says 1 of 43".
   */
  it("backfills a missing message id so the key matches the collection's", () => {
    const tiles = messageMediaTiles(77, [attachment({ message_id: undefined, id: 906 })]);
    expect(tiles[0].messageId).toBe(77);
    expect(tiles[0].key).toBe(conversationMediaKey(77, 906));
  });

  it("leaves a tile that already agrees with its message untouched", () => {
    const tiles = messageMediaTiles(77, [attachment({ id: 907 })]);
    expect(tiles[0].messageId).toBe(77);
    expect(tiles[0].key).toBe("77:907");
  });

  it("carries the identity the viewer needs, not just a url", () => {
    const [tile] = messageMediaTiles(77, [attachment({ id: 908 })]);
    expect(tile.mediaUploadId).toBe(4400);
    expect(tile.senderName).toBe("Maria Cherie");
    expect(tile.senderId).toBe(12);
    expect(tile.width).toBe(1200);
    expect(tile.height).toBe(900);
  });
});

describe("when a grid is warranted", () => {
  /**
   * The single-media path has to stay exactly what it was. A one-photo message
   * is the overwhelming majority of messenger media and already renders at full
   * bubble width; routing it through a grid of one would shrink every photo in
   * every thread to fix a case mobile cannot even produce.
   */
  it("does not call a single photo a grid", () => {
    expect(isMultiMediaMessage(messageMediaTiles(77, [attachment()]))).toBe(false);
    expect(isMultiMediaMessage([])).toBe(false);
  });

  it("calls two or more a grid", () => {
    const tiles = messageMediaTiles(77, [attachment({ id: 901 }), attachment({ id: 902 })]);
    expect(isMultiMediaMessage(tiles)).toBe(true);
  });

  /**
   * A message whose only other attachment is a voice note is still a
   * single-photo message, because the voice note never enters the grid.
   */
  it("is not a grid when the second attachment was filtered out", () => {
    const tiles = messageMediaTiles(77, [
      attachment({ id: 901 }),
      attachment({ id: 902, media_type: "voice", mime_type: "audio/m4a" })
    ]);
    expect(isMultiMediaMessage(tiles)).toBe(false);
  });
});

describe("tile columns", () => {
  it("lays 2 and 4 out two across and everything larger three across", () => {
    expect(mediaTileColumns(0)).toBe(1);
    expect(mediaTileColumns(1)).toBe(1);
    expect(mediaTileColumns(2)).toBe(2);
    expect(mediaTileColumns(3)).toBe(3);
    expect(mediaTileColumns(4)).toBe(2);
    expect(mediaTileColumns(5)).toBe(3);
    expect(mediaTileColumns(9)).toBe(3);
  });

  it("never returns a column count that would divide by zero downstream", () => {
    for (let count = 0; count <= 12; count += 1) {
      expect(mediaTileColumns(count)).toBeGreaterThan(0);
    }
  });
});

describe("a tile and the collection are the same item", () => {
  /**
   * MUTATION §37: "tapping the third photo of a multi-photo message opens a
   * different photo".
   *
   * The failure mode this kills is a tile builder that invents its own key
   * shape — `tile-3`, or the bare attachment id, or an index into the message.
   * Any of those still renders three photos in a grid, still opens the viewer,
   * and still lands somewhere; it just lands somewhere wrong. So the assertion
   * is not "the tile has a key" but "the tile's key finds itself in the
   * server-paged collection at the position it occupies there".
   */
  it("finds tile 3 at its own position in the server's collection", () => {
    const tiles = messageMediaTiles(77, [
      attachment({ id: 901 }),
      attachment({ id: 902 }),
      attachment({ id: 903 })
    ]);

    // The collection as the server pages it: other conversations' media either
    // side, with this message's three photos contiguous in the middle.
    const collection = [
      { key: conversationMediaKey(70, 880), attachmentId: 880 },
      { key: conversationMediaKey(74, 895), attachmentId: 895 },
      ...tiles,
      { key: conversationMediaKey(81, 940), attachmentId: 940 }
    ] as Parameters<typeof indexOfConversationMedia>[0];

    expect(indexOfConversationMedia(collection, tiles[2].key)).toBe(4);
    expect(indexOfConversationMedia(collection, tiles[0].key)).toBe(2);
    // And each tile finds itself, rather than all three aliasing one slot.
    const found = tiles.map((tile) => indexOfConversationMedia(collection, tile.key));
    expect(new Set(found).size).toBe(3);
  });

  /**
   * Two messages in the same thread can carry attachments whose ids are close
   * together, and a bare-integer key would be fine right up until they were
   * equal. Messenger media ids come from several tables that overlap, so the
   * key has to be composite.
   */
  it("keeps tiles of different messages distinct even at the same attachment id", () => {
    const [mine] = messageMediaTiles(77, [attachment({ id: 910, message_id: undefined })]);
    const [theirs] = messageMediaTiles(78, [attachment({ id: 910, message_id: undefined })]);
    expect(mine.key).not.toBe(theirs.key);
  });
});
