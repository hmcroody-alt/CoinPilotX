/**
 * The conversation's media collection: one ordered list, built from the server.
 *
 * The bug this module exists to make impossible is subtle and was shipping. A
 * chat thread is a windowed list — React Native mounts the cells near the
 * viewport and recycles the rest. A gallery assembled from mounted cells is
 * therefore a gallery of *the current scroll position*, so tapping the 17th
 * photo in a conversation opened whichever photo happened to be first in the
 * window. It looked correct whenever the window happened to start at the item
 * you tapped, which is most of the time in a short test conversation and almost
 * never in a real one.
 *
 * So the collection is server-paged and lives above the list, and the functions
 * here are deliberately pure: given pages, produce one canonically ordered
 * array; given an item, produce its index in that array. No component state, no
 * mounted-cell awareness, nothing that can differ between two renders.
 *
 * ORDER. The canonical order is ascending `attachmentId`, which is the server's
 * `a.id` — see `MEDIA_HISTORY_ORDER_SQL` in `pulse_communications_v2/service.py`.
 * Attachments are inserted while their message is sent, so ascending id is the
 * same sequence the thread renders top to bottom. Both sides naming the same
 * key is what makes "item 17" mean one thing.
 *
 * IDENTITY. `mediaKey` is `<messageId>:<attachmentId>`, never a bare integer.
 * Messenger media ids come from several tables whose numbers overlap —
 * `media_upload_id`, `attachment_id` and a viewer item's `id` are row ids from
 * different tables and collide freely. A bare integer as a collection key would
 * silently alias two different photos onto one slot.
 */

export type ConversationMediaKind = "image" | "video";

export type ConversationMediaItem = {
  /** `<messageId>:<attachmentId>`. Stable, unique, and not a bare row id. */
  key: string;
  attachmentId: number;
  /** Foundation `message_attachments` id — what the access endpoint is keyed on. */
  mediaUploadId: number;
  messageId: number;
  kind: ConversationMediaKind;
  /** Playback source. For a Mux-backed video this is an HLS manifest, not a file. */
  url: string;
  /**
   * The downloadable original, which for video is a different resource to `url`.
   *
   * The server sends both because they genuinely differ: `url` is what the
   * player streams, `download_url` is the progressive, membership-checked file
   * that Save to Photos and Share need. Empty from a server that predates the
   * split, and from any row whose only URL is a manifest — in both cases the
   * viewer falls back to `url` rather than inventing one.
   */
  downloadUrl: string;
  thumbnailUrl: string;
  mimeType: string;
  width: number;
  height: number;
  durationSeconds: number;
  senderId: number;
  senderName: string;
  createdAt: string;
};

export type ConversationMediaPage = {
  items: ConversationMediaItem[];
  total: number;
  hasOlder: boolean;
  hasNewer: boolean;
  oldestId: number;
  newestId: number;
};

function num(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

/**
 * Photo or video, and nothing else.
 *
 * Voice notes keep their waveform player and documents keep their document
 * card; neither is a thing you can swipe onto, so neither may become an index
 * in this collection. The server already filters, but a client that classified
 * differently would produce a collection whose length disagreed with the
 * server's `total` — and `total` is what the accessibility label counts with
 * ("Photo from Maria Cherie, 12 of 43"). One classifier, used by both.
 */
export function conversationMediaKind(raw: Record<string, unknown>): ConversationMediaKind | null {
  const declared = text(raw.media_type || raw.kind).toLowerCase();
  const mime = text(raw.mime_type).toLowerCase();
  if (declared === "video" || mime.startsWith("video/")) return "video";
  if (declared === "image" || declared === "photo" || declared === "gif" || mime.startsWith("image/")) return "image";
  return null;
}

export function conversationMediaKey(messageId: number, attachmentId: number): string {
  return `${num(messageId)}:${num(attachmentId)}`;
}

/** One wire row -> one collection item, or null if it is not gallery media. */
export function normalizeConversationMediaItem(raw: Record<string, unknown> | null | undefined): ConversationMediaItem | null {
  if (!raw) return null;
  const kind = conversationMediaKind(raw);
  if (!kind) return null;
  const attachmentId = num(raw.attachment_id) || num(raw.id);
  const messageId = num(raw.message_id);
  if (!attachmentId) return null;
  return {
    key: conversationMediaKey(messageId, attachmentId),
    attachmentId,
    mediaUploadId: num(raw.media_upload_id),
    messageId,
    kind,
    url: text(raw.url),
    downloadUrl: text(raw.download_url),
    thumbnailUrl: text(raw.thumbnail_url),
    mimeType: text(raw.mime_type),
    width: num(raw.width),
    height: num(raw.height),
    durationSeconds: num(raw.duration_seconds),
    senderId: num(raw.sender_user_id) || num(raw.sender_id),
    senderName: text(raw.sender_display_name),
    createdAt: text(raw.created_at)
  };
}

export function normalizeConversationMediaPage(
  payload: Record<string, unknown> | null | undefined
): ConversationMediaPage {
  const raw = Array.isArray(payload?.items) ? (payload!.items as Array<Record<string, unknown>>) : [];
  const items = raw
    .map(normalizeConversationMediaItem)
    .filter((item): item is ConversationMediaItem => item !== null);
  return {
    items: sortConversationMedia(items),
    total: num(payload?.total),
    hasOlder: Boolean(payload?.has_older),
    hasNewer: Boolean(payload?.has_newer),
    oldestId: num(payload?.oldest_id),
    newestId: num(payload?.newest_id)
  };
}

/**
 * Canonical order, applied defensively rather than trusted.
 *
 * The server returns pages ascending in every direction precisely so the client
 * can splice without re-sorting. Sorting anyway costs nothing at these sizes and
 * means a server that ever regressed to a descending backwards page would
 * produce a *slow* gallery rather than one whose indices silently invert.
 */
export function sortConversationMedia(items: ConversationMediaItem[]): ConversationMediaItem[] {
  return [...items].sort((left, right) => left.attachmentId - right.attachmentId);
}

/**
 * Splice a page into the collection, last-write-wins per key.
 *
 * Overlap is normal, not exceptional: a realtime arrival and a page fetch can
 * both carry the same item, and re-fetching a page after a refreshed URL is the
 * intended way to replace a stale one. Dedupe is on `key`, so the same photo
 * arriving twice is one slot — if it were on array position, one duplicate
 * would shift every index after it, including the one the viewer is on.
 */
export function mergeConversationMedia(
  existing: ConversationMediaItem[],
  incoming: ConversationMediaItem[]
): ConversationMediaItem[] {
  if (!incoming.length) return existing;
  const byKey = new Map<string, ConversationMediaItem>();
  for (const item of existing) byKey.set(item.key, item);
  for (const item of incoming) byKey.set(item.key, item);
  return sortConversationMedia([...byKey.values()]);
}

export function indexOfConversationMedia(items: ConversationMediaItem[], key: string): number {
  if (!key) return -1;
  return items.findIndex((item) => item.key === key);
}

/**
 * Drop one item and report where the viewer should land.
 *
 * Requirement: if the item you are looking at is deleted while the viewer is
 * open, the viewer degrades — it does not crash, and it does not sit on a black
 * frame. Staying at the same numeric index means you land on the *next* item,
 * which is the behaviour a person expects from a deletion. At the end of the
 * collection that index no longer exists, so it clamps back one. An emptied
 * collection reports -1, which the caller reads as "close".
 */
export function removeConversationMedia(
  items: ConversationMediaItem[],
  key: string,
  currentIndex: number
): { items: ConversationMediaItem[]; index: number } {
  const removedAt = indexOfConversationMedia(items, key);
  if (removedAt < 0) return { items, index: currentIndex };
  const next = items.filter((item) => item.key !== key);
  if (!next.length) return { items: next, index: -1 };
  const target = removedAt < currentIndex ? currentIndex - 1 : currentIndex;
  return { items: next, index: Math.max(0, Math.min(target, next.length - 1)) };
}

/**
 * The window to keep warm around the active item.
 *
 * Requirement: N+1 and N-1 aggressively, N±2 metadata-warm, and explicitly NOT
 * the whole conversation at full resolution — a 400-photo thread prefetched
 * eagerly is a cache eviction storm and a data bill, and it would evict the
 * neighbours it was supposed to protect.
 */
export const MEDIA_PREFETCH_RADIUS = 1;
export const MEDIA_WARM_RADIUS = 2;

export function prefetchNeighbours(
  items: ConversationMediaItem[],
  index: number,
  radius: number = MEDIA_PREFETCH_RADIUS
): ConversationMediaItem[] {
  if (index < 0 || index >= items.length) return [];
  const out: ConversationMediaItem[] = [];
  for (let offset = 1; offset <= radius; offset += 1) {
    // Forward first: swiping forward is the overwhelmingly common direction, so
    // when the two compete for the same cache budget the next item should win.
    const ahead = items[index + offset];
    const behind = items[index - offset];
    if (ahead) out.push(ahead);
    if (behind) out.push(behind);
  }
  return out;
}
