import { readJsonCache, writeJsonCache } from "../core/cache";
import { CanonicalMediaRecord, mediaRecordForCache } from "../media/mediaContract";
import { pulseApi } from "./pulseApi";

const SAVED_CACHE_KEY = "pulsesoc.native.saved.library";

export type SavedContentType =
  | "all"
  | "post"
  | "reel"
  | "status"
  | "video"
  | "marketplace"
  | "room"
  | "group"
  | "teacher"
  | "image"
  | "learning";

export type SavedCollection = {
  id: number;
  name: string;
  slug?: string;
  description?: string;
  is_default?: number | boolean;
  item_count?: number;
  created_at?: string;
  updated_at?: string;
};

export type SavedItem = {
  id: number;
  collection_id?: number;
  collection_name?: string;
  content_type: string;
  content_id: string;
  title: string;
  preview_text?: string;
  thumbnail_url?: string;
  media_url?: string;
  source_url?: string;
  metadata_json?: string;
  created_at?: string;
  updated_at?: string;
  /**
   * Canonical media for the saved row, resolved at read time by the same
   * `media_service.resolve_media` path the feed uses. Resolved rather than
   * snapshotted because playback URLs expire and Mux playback ids can be
   * re-issued — a URL copied at save time would be dead by the time anyone
   * opened their Saved library. Empty when the content has no media.
   */
  media?: CanonicalMediaRecord[];
  /**
   * The underlying content is gone or no longer visible to this viewer.
   *
   * The row still exists and still lists, because a saved item silently
   * vanishing looks like data loss. What it must not do is pretend to be
   * playable: the snapshot title stays, the play affordance does not.
   */
  unavailable?: boolean;
  /**
   * The `pulse_posts` id backing this row, or 0 for types that have none.
   *
   * Reels and posts are the same underlying post to the server but different
   * ids to the client, so this is what lets a reel saved on one screen show as
   * saved on the post card for the same content.
   */
  post_id?: number;
};

export type SavedLibraryResponse = {
  ok?: boolean;
  items?: SavedItem[];
  collections?: SavedCollection[];
  collection_id?: number;
  message?: string;
};

export type SavedParams = {
  type?: SavedContentType;
  collectionId?: number;
  query?: string;
};

export async function listSavedContent(params: SavedParams = {}) {
  const query = new URLSearchParams({
    type: params.type || "all",
    collection_id: String(params.collectionId || 0),
    q: params.query || ""
  });
  const data = await pulseApi<SavedLibraryResponse>(`/api/pulse/saved?${query.toString()}`);
  const normalized = normalizeSavedLibrary(data);
  await cacheSavedLibrary(normalized).catch(() => undefined);
  return normalized;
}

/**
 * Direct binding to the generic library write route.
 *
 * Nothing calls this today: every Save button in the app goes through
 * `social/saveContract.setSavedOnServer`, which asserts a state rather than
 * appending a row and is therefore the only path safe to repeat. This stays as
 * the low-level binding for the one thing that contract cannot express —
 * choosing a destination collection at save time — but the `collection_id`
 * argument has no caller until a collection picker exists, and inventing one
 * here would be UI design rather than wiring.
 */
export async function addSavedItem(payload: {
  content_type: string;
  content_id: string | number;
  collection_id?: number;
  title?: string;
  preview_text?: string;
  source_url?: string;
  thumbnail_url?: string;
  media_url?: string;
}) {
  return pulseApi<SavedLibraryResponse>("/api/pulse/saved", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function removeSavedItem(itemId: number) {
  return pulseApi<{ ok?: boolean; removed?: boolean; message?: string }>(`/api/pulse/saved/${itemId}`, { method: "DELETE" });
}

export async function moveSavedItem(itemId: number, collectionId: number) {
  return pulseApi<{ ok?: boolean; moved?: boolean; message?: string }>(`/api/pulse/saved/${itemId}/move`, {
    method: "POST",
    body: JSON.stringify({ collection_id: collectionId })
  });
}

export async function createSavedCollection(name: string) {
  return pulseApi<SavedLibraryResponse>("/api/pulse/saved/collections", {
    method: "POST",
    body: JSON.stringify({ name })
  });
}

export async function updateSavedCollection(collectionId: number, name: string) {
  return pulseApi<{ ok?: boolean; collection_id?: number; message?: string }>(`/api/pulse/saved/collections/${collectionId}`, {
    method: "PATCH",
    body: JSON.stringify({ name })
  });
}

export async function deleteSavedCollection(collectionId: number) {
  return pulseApi<{ ok?: boolean; message?: string }>(`/api/pulse/saved/collections/${collectionId}`, { method: "DELETE" });
}

export async function loadCachedSavedLibrary() {
  return readJsonCache<SavedLibraryResponse>(SAVED_CACHE_KEY, normalizeSavedLibrary);
}

export async function cacheSavedLibrary(data: SavedLibraryResponse) {
  await writeJsonCache(
    SAVED_CACHE_KEY,
    {
      ...data,
      // Signed playback URLs are stripped before they reach disk, the same rule
      // the feed cache follows: a cached credential outlives its validity and
      // is worth nothing on the next launch anyway.
      items: (data.items || []).slice(0, 120).map((item) => ({
        ...item,
        media: (item.media || []).map(mediaRecordForCache)
      })),
      collections: data.collections || []
    }
  );
}

export function normalizeSavedLibrary(data: SavedLibraryResponse): SavedLibraryResponse {
  return {
    ...data,
    items: normalizeSavedItems(data.items || []),
    collections: normalizeSavedCollections(data.collections || []),
    collection_id: Number(data.collection_id || 0)
  };
}

export function normalizeSavedItems(items: SavedItem[]) {
  return (items || [])
    .map((item) => ({
      ...item,
      id: Number(item.id || 0),
      collection_id: Number(item.collection_id || 0),
      content_type: String(item.content_type || "post"),
      content_id: String(item.content_id || ""),
      title: String(item.title || "Saved item"),
      preview_text: String(item.preview_text || ""),
      thumbnail_url: String(item.thumbnail_url || ""),
      media_url: String(item.media_url || ""),
      source_url: String(item.source_url || "/pulse"),
      collection_name: String(item.collection_name || "Favorites"),
      // Defaults, not overwrites: an older server (or a cache written before
      // this contract) omits these entirely, and the safe reading of "no media
      // field" is "no media" rather than "undefined blows up at the call site".
      media: Array.isArray(item.media) ? item.media : [],
      unavailable: Boolean(item.unavailable),
      post_id: Number(item.post_id || 0)
    }))
    .filter((item) => item.id > 0);
}

export function normalizeSavedCollections(collections: SavedCollection[]) {
  return (collections || [])
    .map((collection) => ({
      ...collection,
      id: Number(collection.id || 0),
      name: String(collection.name || "Favorites"),
      item_count: Number(collection.item_count || 0),
      is_default: Boolean(collection.is_default)
    }))
    .filter((collection) => collection.id > 0);
}
