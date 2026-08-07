/**
 * Persistence and subscription for the listing-creation draft.
 *
 * The app carries no external state library — `settings/store.tsx` and
 * `session/sessionStore.ts` are both hand-rolled over AsyncStorage — so this
 * follows the same shape: a module-level snapshot, a subscriber set consumed
 * through `useSyncExternalStore`, and a debounced write to AsyncStorage so
 * typing a title issues one write, not forty.
 *
 * One draft at a time, by design: the key is fixed, publishing clears it, and
 * reopening the composer offers Resume / Start over from whatever survived.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { useSyncExternalStore } from "react";
import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  createListingDraft,
  ListingDraft,
  listingDraftHasContent,
  normalizeListingDraft
} from "./listingDraft";

const DRAFT_CACHE_KEY = "pulsesoc.native.listing_draft.v1";
const AUTOSAVE_DEBOUNCE_MS = 600;

let snapshot: ListingDraft = createListingDraft();
let hydrated = false;
const listeners = new Set<() => void>();
let persistTimer: ReturnType<typeof setTimeout> | null = null;

function emit() {
  listeners.forEach((listener) => listener());
}

export function getListingDraftSnapshot(): ListingDraft {
  return snapshot;
}

export function subscribeListingDraft(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React binding. Re-renders on every draft change, hydration included. */
export function useListingDraft(): ListingDraft {
  return useSyncExternalStore(subscribeListingDraft, getListingDraftSnapshot, getListingDraftSnapshot);
}

/**
 * Reads the persisted draft into the snapshot. Returns the stored draft when
 * one with real content exists (the Resume / Start over prompt keys off this),
 * or null when the composer should open fresh.
 */
export async function hydrateListingDraft(): Promise<ListingDraft | null> {
  const stored = await readJsonCache<ListingDraft>(DRAFT_CACHE_KEY, normalizeListingDraft);
  hydrated = true;
  if (stored && listingDraftHasContent(stored)) {
    snapshot = stored;
    emit();
    return stored;
  }
  return null;
}

export function isListingDraftHydrated(): boolean {
  return hydrated;
}

/**
 * Applies a partial update (or updater function) and schedules the debounced
 * autosave. Every keystroke in the wizard funnels through here.
 */
export function updateListingDraft(
  patch: Partial<ListingDraft> | ((draft: ListingDraft) => ListingDraft)
): ListingDraft {
  snapshot = typeof patch === "function" ? patch(snapshot) : { ...snapshot, ...patch };
  snapshot = { ...snapshot, updatedAt: new Date().toISOString() };
  emit();
  schedulePersist();
  return snapshot;
}

function schedulePersist() {
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(() => {
    persistTimer = null;
    void persistListingDraft();
  }, AUTOSAVE_DEBOUNCE_MS);
}

/** Immediate durable write — the "Save draft" button and step transitions. */
export async function persistListingDraft(): Promise<void> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  await writeJsonCache(DRAFT_CACHE_KEY, snapshot).catch(() => undefined);
}

/** Resets to an empty draft and removes the stored copy. Publish and "Start over". */
export async function clearListingDraft(): Promise<ListingDraft> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  snapshot = createListingDraft();
  emit();
  await AsyncStorage.removeItem(DRAFT_CACHE_KEY).catch(() => undefined);
  return snapshot;
}

export const __testing = {
  DRAFT_CACHE_KEY,
  AUTOSAVE_DEBOUNCE_MS,
  reset() {
    if (persistTimer) {
      clearTimeout(persistTimer);
      persistTimer = null;
    }
    snapshot = createListingDraft();
    hydrated = false;
    listeners.clear();
  }
};
