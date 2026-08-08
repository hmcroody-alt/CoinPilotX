/**
 * Persistence and subscription for the campaign-creation draft.
 *
 * Same shape as `marketplace/listingDraftStore.ts` — the app carries no
 * external state library, so this is a module-level snapshot, a subscriber set
 * consumed through `useSyncExternalStore`, and a debounced AsyncStorage write
 * so typing a campaign name issues one write, not forty.
 *
 * One draft at a time, by design: the key is fixed, publishing clears it, and
 * reopening the wizard offers Resume / Start over from whatever survived. The
 * idempotency key rides inside the draft, so a publish retried after an app
 * restart still reuses the original key.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { useSyncExternalStore } from "react";
import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  CampaignDraft,
  campaignDraftHasContent,
  createCampaignDraft,
  normalizeCampaignDraft
} from "./campaignDraft";

const DRAFT_CACHE_KEY = "pulsesoc.native.campaign_draft.v1";
const AUTOSAVE_DEBOUNCE_MS = 600;

let snapshot: CampaignDraft = createCampaignDraft();
let hydrated = false;
const listeners = new Set<() => void>();
let persistTimer: ReturnType<typeof setTimeout> | null = null;

function emit() {
  listeners.forEach((listener) => listener());
}

export function getCampaignDraftSnapshot(): CampaignDraft {
  return snapshot;
}

export function subscribeCampaignDraft(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React binding. Re-renders on every draft change, hydration included. */
export function useCampaignDraft(): CampaignDraft {
  return useSyncExternalStore(subscribeCampaignDraft, getCampaignDraftSnapshot, getCampaignDraftSnapshot);
}

/**
 * Reads the persisted draft into the snapshot. Returns the stored draft when
 * one with real content exists (the Resume / Start over prompt keys off this),
 * or null when the wizard should open fresh.
 */
export async function hydrateCampaignDraft(): Promise<CampaignDraft | null> {
  const stored = await readJsonCache<CampaignDraft>(DRAFT_CACHE_KEY, normalizeCampaignDraft);
  hydrated = true;
  if (stored && campaignDraftHasContent(stored)) {
    snapshot = stored;
    emit();
    return stored;
  }
  return null;
}

export function isCampaignDraftHydrated(): boolean {
  return hydrated;
}

/**
 * Applies a partial update (or updater function) and schedules the debounced
 * autosave. Every keystroke in the wizard funnels through here.
 */
export function updateCampaignDraft(
  patch: Partial<CampaignDraft> | ((draft: CampaignDraft) => CampaignDraft)
): CampaignDraft {
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
    void persistCampaignDraft();
  }, AUTOSAVE_DEBOUNCE_MS);
}

/** Immediate durable write — the "Save draft" action and step transitions. */
export async function persistCampaignDraft(): Promise<void> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  await writeJsonCache(DRAFT_CACHE_KEY, snapshot).catch(() => undefined);
}

/** Resets to an empty draft and removes the stored copy. Publish and "Start over". */
export async function clearCampaignDraft(): Promise<CampaignDraft> {
  if (persistTimer) {
    clearTimeout(persistTimer);
    persistTimer = null;
  }
  snapshot = createCampaignDraft();
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
    snapshot = createCampaignDraft();
    hydrated = false;
    listeners.clear();
  }
};
