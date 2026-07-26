/**
 * The saved state of every piece of content the app currently knows about,
 * held once rather than once per screen.
 *
 * The same post is on screen in several places at once — Home's feed, the
 * profile that opened behind it, a post detail pushed on top, the Saved
 * collection itself. Each of those held its own copy of the post object and
 * updated only its own copy, so saving from one left the others showing Save,
 * and the Saved screen did not learn about a save until it was refetched. The
 * user's report of "the button does not reliably save" is partly this: it did
 * save, and the next screen said otherwise.
 *
 * A module-level store rather than React context because the screens involved
 * are not nested — a context would have to wrap the whole navigator and every
 * consumer would re-render on every unrelated save. Subscribers here are keyed,
 * so a card only re-renders when its own content changes.
 */

import { useCallback, useEffect, useState } from "react";
import { SavableContentType, saveKey } from "./saveContract";

export type SaveState = {
  saved: boolean;
  /** True while a mutation for this item is in flight. */
  pending: boolean;
};

const states = new Map<string, SaveState>();
const listeners = new Map<string, Set<() => void>>();
const globalListeners = new Set<(key: string, state: SaveState) => void>();

function emit(key: string, state: SaveState) {
  listeners.get(key)?.forEach((listener) => listener());
  globalListeners.forEach((listener) => listener(key, state));
}

function write(key: string, next: SaveState) {
  const current = states.get(key);
  if (current && current.saved === next.saved && current.pending === next.pending) return;
  states.set(key, next);
  emit(key, next);
}

/** The store's opinion, or undefined when it has never been told about this item. */
export function peekSaveState(type: SavableContentType, id: number | string): SaveState | undefined {
  return states.get(saveKey(type, id));
}

/**
 * Record what a *fresh* fetch said, without starting a mutation.
 *
 * Call this from a load path — a feed page, the Saved library — where the
 * response is known to be newer than anything the store holds. It overwrites,
 * because that is how the store learns the user unsaved something on the web,
 * or on another device, between two pulls to refresh.
 *
 * It never overwrites a mutation still in flight: a list fetched before that
 * mutation started would otherwise land after it and undo it. That ordering is
 * the whole reason `pending` lives here rather than in whichever screen
 * happened to start the request.
 */
export function observeSavedState(type: SavableContentType, id: number | string, saved: boolean) {
  const key = saveKey(type, id);
  const current = states.get(key);
  if (current?.pending) return;
  write(key, { saved, pending: false });
}

/**
 * Record what a card's own payload said, but only if nothing is known yet.
 *
 * A card does not know how old the object it was handed is. The Saved
 * collection screen pushes a post detail; the detail's payload was fetched
 * before the user saved it three screens ago; if mounting that card were
 * allowed to overwrite, the Save button would revert under them the moment they
 * navigated — which is one of the ways the original bug showed itself. So a
 * mounting card may seed an unknown item and may not correct a known one.
 * Correcting is the job of `observeSavedState`, whose callers know their data
 * is fresh.
 */
export function seedSavedState(type: SavableContentType, id: number | string, saved: boolean) {
  const key = saveKey(type, id);
  if (states.has(key)) return;
  write(key, { saved, pending: false });
}

/** Bulk form of `observeSavedState`, for a freshly loaded page of content. */
export function observeSavedStates(type: SavableContentType, items: Array<{ id: number | string; saved?: boolean | null }>) {
  items.forEach((item) => {
    if (typeof item.saved !== "boolean") return;
    observeSavedState(type, item.id, item.saved);
  });
}

export function markSavePending(type: SavableContentType, id: number | string, saved: boolean) {
  write(saveKey(type, id), { saved, pending: true });
}

export function settleSaveState(type: SavableContentType, id: number | string, saved: boolean) {
  write(saveKey(type, id), { saved, pending: false });
}

/** Test seam. Not exported to screens. */
export function resetSavedStoreForTests() {
  states.clear();
  listeners.clear();
  globalListeners.clear();
}

function subscribe(key: string, listener: () => void) {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(listener);
  return () => {
    set?.delete(listener);
    if (set && set.size === 0) listeners.delete(key);
  };
}

/**
 * Watch every change in the store.
 *
 * The Saved collection screen is the one consumer that cares about items it is
 * not currently rendering: an unsave performed on a feed card three screens
 * away has to remove a row from its list.
 */
export function subscribeToSaveChanges(listener: (key: string, state: SaveState) => void) {
  globalListeners.add(listener);
  return () => {
    globalListeners.delete(listener);
  };
}

/**
 * The saved state to render for one item.
 *
 * `serverSaved` is what the item payload said. It seeds the store on first
 * sight and is otherwise outranked by it, so a card mounted from a stale list
 * still shows the state the user last chose rather than reverting under them.
 */
export function useSavedState(
  type: SavableContentType,
  id: number | string,
  serverSaved?: boolean | null
): SaveState {
  const key = saveKey(type, id);
  const [, forceRender] = useState(0);

  useEffect(() => subscribe(key, () => forceRender((tick) => tick + 1)), [key]);

  useEffect(() => {
    if (typeof serverSaved !== "boolean") return;
    seedSavedState(type, id, serverSaved);
  }, [id, serverSaved, type]);

  const current = states.get(key);
  if (current) return current;
  return { saved: Boolean(serverSaved), pending: false };
}

/**
 * Convenience for lists that need to filter by saved state without subscribing
 * per item — currently the Saved screen, which re-reads on every store change.
 */
export function useSavedStoreVersion(): number {
  const [version, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((current) => current + 1), []);
  useEffect(() => subscribeToSaveChanges(bump), [bump]);
  return version;
}
