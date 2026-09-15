/**
 * The only supported source for the chat media gallery.
 *
 * Deliberately a separate module from `messenger.ts` so that the rule it
 * enforces is visible in one file: the gallery's collection comes from
 * `GET /conversations/:id/media` and from nowhere else. Scraping media out of
 * rendered message state is what produced a gallery that opened on the wrong
 * item, and the fix is not "scrape more carefully" — it is having a paginated
 * endpoint that is the authority on order, membership and completeness.
 */

import { pulseApi } from "./pulseApi";
import {
  ConversationMediaPage,
  normalizeConversationMediaPage
} from "../media/conversationMediaCollection";

const MESSENGER_API = "/api/pulse/communications/v2";

export type ConversationMediaQuery = {
  /** Page immediately OLDER than this attachment id. */
  beforeId?: number;
  /** Page immediately NEWER than this attachment id. */
  afterId?: number;
  limit?: number;
  /** Omit for photos and videos together, which is what the gallery wants. */
  mediaType?: "image" | "video";
};

/**
 * Cursors are attachment ids, not offsets.
 *
 * With OFFSET paging, one photo arriving mid-session shifts every subsequent
 * window by one: the next page repeats an item, the collection grows a
 * duplicate, and the index the viewer is sitting on stops pointing at the photo
 * it is displaying. A keyset cursor is a value, so a new arrival at the top of
 * the collection cannot renumber a page below it.
 */
export async function fetchConversationMedia(
  conversationId: number,
  query: ConversationMediaQuery = {}
): Promise<ConversationMediaPage> {
  const params = new URLSearchParams();
  if (query.limit && query.limit > 0) params.set("limit", String(Math.floor(query.limit)));
  if (query.afterId && query.afterId > 0) params.set("after_id", String(Math.floor(query.afterId)));
  else if (query.beforeId && query.beforeId > 0) params.set("before_id", String(Math.floor(query.beforeId)));
  if (query.mediaType) params.set("media_type", query.mediaType);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const data = await pulseApi<Record<string, unknown>>(
    `${MESSENGER_API}/conversations/${Math.floor(conversationId)}/media${suffix}`
  );
  return normalizeConversationMediaPage(data);
}
