import { readJsonCache, writeJsonCache } from "../core/cache";
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
      items: (data.items || []).slice(0, 120),
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
      collection_name: String(item.collection_name || "Favorites")
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
