/**
 * One message that carries several photos, split back into its tiles.
 *
 * The mobile composer sends exactly one attachment per message, so for a long
 * time the client could pretend a message *was* its first attachment — which is
 * what `firstAttachment` in `api/messenger.ts` does. The web composer does not
 * have that limit, and the backend allows several. The result on a phone was a
 * three-photo message that rendered as one photo, with the other two reachable
 * from nowhere.
 *
 * The important part is that a tile is not a new kind of thing. It is a
 * `ConversationMediaItem` — the same shape, built by the same normalizer, keyed
 * the same way — so tile 3 of a message and the third photo of that message in
 * the server-paged collection are the *same key*, and opening the tile lands on
 * it rather than near it. That identity is the whole reason this file is four
 * lines of logic instead of a parallel implementation.
 */

import {
  ConversationMediaItem,
  conversationMediaKey,
  normalizeConversationMediaItem,
  sortConversationMedia
} from "./conversationMediaCollection";

/**
 * The gallery-eligible tiles of one message, in the collection's own order.
 *
 * Voice notes and documents are dropped here exactly as they are dropped from
 * the collection — §28 says they stay a waveform player and a document card and
 * never enter the visual gallery — because both paths run the same classifier.
 *
 * `messageId` is passed in rather than read from the attachment: a row cached
 * before the payload carried `message_id` would key as `0:<attachmentId>` and
 * miss its match in the collection, which is the kind of drift that shows up as
 * "the tile opens the right photo but the counter says 1 of 43".
 */
export function messageMediaTiles(
  messageId: number,
  attachments: Array<Record<string, unknown>> | undefined
): ConversationMediaItem[] {
  if (!Array.isArray(attachments) || attachments.length < 1) return [];
  const tiles: ConversationMediaItem[] = [];
  for (const raw of attachments) {
    const item = normalizeConversationMediaItem(raw);
    if (!item) continue;
    tiles.push(
      item.messageId === messageId
        ? item
        : { ...item, messageId, key: conversationMediaKey(messageId, item.attachmentId) }
    );
  }
  return sortConversationMedia(tiles);
}

/**
 * Does this message need a grid rather than a single preview?
 *
 * Stated as its own function because the single-media path must stay exactly
 * what it was. A one-photo message is the overwhelming majority of messenger
 * media and it already renders correctly at full bubble width; putting it
 * through a grid of one would shrink every photo in every thread to fix a case
 * that mobile cannot even produce.
 */
export function isMultiMediaMessage(tiles: ConversationMediaItem[]): boolean {
  return tiles.length > 1;
}

/**
 * Tile columns. Two across for 2 and 4, three across for anything larger.
 *
 * Instagram/WhatsApp both settle here: 2×1, 2×2, then 3-wide rows. A 3-photo
 * message reads better as one row of three than as a 2+1 with an orphan.
 */
export function mediaTileColumns(count: number): number {
  if (count <= 1) return 1;
  if (count === 2 || count === 4) return 2;
  return 3;
}
