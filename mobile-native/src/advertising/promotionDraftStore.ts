/**
 * Persistence and subscription for the "Promote your content" draft.
 *
 * A direct sibling of `campaignDraftStore.ts`: the app carries no external state
 * library, so this is a module-level snapshot, a subscriber set consumed through
 * `useSyncExternalStore`, and a debounced AsyncStorage write so typing a budget
 * issues one write, not forty.
 *
 * One promotion draft at a time, by design: the key is fixed, submitting clears
 * it, and reopening the wizard offers Resume / Start over from whatever
 * survived. The idempotency key rides inside the draft, so a Submit retried
 * after an app restart still reuses the original key and the server dedupes
 * instead of creating a second campaign.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { useSyncExternalStore } from "react";
import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  PromotionDraft,
  createPromotionDraft,
  normalizePromotionDraft,
  promotionDraftHasContent
} from "./promotionDraft";

const DRAFT_CACHE_KEY = "pulsesoc.native.promotion_draft.v1";
const AUTOSAVE_DEBOUNCE_MS = 600;

let snapshot: PromotionDraft = createPromotionDraft();
let hydrated = false;
const listeners = new Set<() => void>();
let persistTimer: ReturnType<typeof setTimeout> | null = null;

function emit() {
  listeners.forEach((listener) => listener());
}

export function getPromotionDraftSnapshot(): PromotionDraft {
  return snapshot;
}

export function subscribePromotionDraft(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React binding. Re-renders on every draft change, hydration included. */
export function usePromotionDraft(): PromotionDraft {
  return useSyncExternalStore(subscribePromotionDraft, getPromotionDraftSnapshot, getPromotionDraftSnapshot);
}

/**
 * Reads the persisted draft into the snapshot. Returns the stored draft when one
 * with real content exists (the Resume / Start over prompt keys off this), or
 * null when the wizard should open fresh.
 */
export async function hydratePromotionDraft(): Promise<PromotionDraft | null> {
  const stored = await readJsonCache<PromotionDraft>(DRAFT_CACHE_KEY, normalizePromotionDraft);
  hydrated = true;
  if (stored && promotionDraftHasContent(stored)) {
    snapshot = stored;
    emit();
    return stored;
  }
  return null;
}

export function isPromotionDraftHydrated(): boolean {
  return hydrated;
}

/**
 * Applies a partial update (or updater function) and schedules the debounced
 * autosave. Every keystroke in the wizard funnels through here.
 */
export function updatePromotionDraft(
  patch: Partial<PromotionDraft> | ((draft: PromotionDraft) => PromotionDraft)
): PromotionDraft {
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
    void persistPromotionDraft();
  }, AUTOSAVE_DEBOUNCE_MS);
}

/** Immediate durable write — the "Save draft" action and step transitions. */
export async function persistPromotionDraft(): Promise<void> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  await writeJsonCache(DRAFT_CACHE_KEY, snapshot).catch(() => undefined);
}

/** Resets to an empty draft and removes the stored copy. Submit and "Start over". */
export async function clearPromotionDraft(): Promise<PromotionDraft> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  snapshot = createPromotionDraft();
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
    snapshot = createPromotionDraft();
    hydrated = false;
    listeners.clear();
  }
};
