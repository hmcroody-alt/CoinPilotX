/**
 * The one way any screen changes a saved state.
 *
 * Screens used to each hold their own `useSocialActionGuard`, which locks per
 * component. Two screens rendering the same post therefore held two separate
 * locks, and tapping Save on a card that was also visible underneath could put
 * two mutations for the same content in flight at once — whichever landed last
 * won, which is not necessarily whichever the user asked for last. The lock has
 * to be as global as the content is, so it lives beside the store rather than
 * inside a component.
 *
 * The sequence counter is the other half. A save followed quickly by an unsave
 * can resolve out of order; applying a response only when it is still the most
 * recent request for that item is what stops the older answer from overwriting
 * the newer one. This mirrors `social/actionGuard.ts`, which solves the same
 * problem for reactions, but keyed globally instead of per screen.
 */

import { useCallback, useState } from "react";
import { describeSocialActionError } from "./actionGuard";
import { SavableContentType, SaveTarget, saveKey, setSavedOnServer } from "./saveContract";
import { markSavePending, peekSaveState, settleSaveState } from "./savedStore";

const inFlight = new Set<string>();
const sequences = new Map<string, number>();

export type SaveActionOutcome = {
  /** The state now shown. Equals the requested state on success. */
  saved: boolean;
  ok: boolean;
  /** Populated only when `ok` is false. */
  message?: string;
  /**
   * The rejection that caused the failure, when there was one.
   *
   * `message` is worded for a Save button, which is right for the card that
   * usually calls this and wrong for the Saved screen, where the same mutation
   * is the "Remove" control. Handing back the raw error lets a caller with
   * better context describe the failure in its own words instead of showing
   * "Save could not be completed" to someone who pressed Remove. Absent when
   * the call was dropped as a duplicate or superseded — nothing failed there,
   * so a caller that reports on `error` alone stays quiet, as it should.
   */
  error?: unknown;
};

/**
 * Assert a saved state for one piece of content.
 *
 * Optimistic: the store flips immediately so every mounted card responds to the
 * tap, and rolls back to the state that was there before if the server refuses.
 * Idempotent by construction — it sends the state it wants rather than a
 * toggle — so a duplicate tap while a request is in flight is simply dropped
 * rather than queued into a reversal.
 */
export async function setSaved(target: SaveTarget, next: boolean): Promise<SaveActionOutcome> {
  const key = saveKey(target.type, target.id);
  const previous = peekSaveState(target.type, target.id)?.saved ?? !next;

  if (inFlight.has(key)) return { ok: false, saved: previous, message: undefined };

  const seq = (sequences.get(key) || 0) + 1;
  sequences.set(key, seq);
  inFlight.add(key);
  markSavePending(target.type, target.id, next);

  try {
    const result = await setSavedOnServer(target, next);
    if (sequences.get(key) !== seq) return { ok: true, saved: result.saved };
    settleSaveState(target.type, target.id, result.saved);
    return { ok: true, saved: result.saved };
  } catch (error) {
    if (sequences.get(key) !== seq) return { ok: false, saved: previous };
    settleSaveState(target.type, target.id, previous);
    return { ok: false, saved: previous, message: describeSocialActionError(error, "Save"), error };
  } finally {
    inFlight.delete(key);
  }
}

/** Toggle helper, for buttons that only know the state they are currently showing. */
export async function toggleSaved(target: SaveTarget, currentlySaved: boolean) {
  return setSaved(target, !currentlySaved);
}

/** Test seam, so one test's in-flight lock cannot leak into the next. */
export function resetSaveActionsForTests() {
  inFlight.clear();
  sequences.clear();
}

/**
 * Screen-level wrapper: the same mutation plus the error string to display.
 *
 * Returned rather than thrown because a failed save is not exceptional — it is
 * a state the card has to show, and a screen that has to wrap every Save press
 * in a try/catch is a screen that will eventually forget to.
 */
export function useSaveAction(onError?: (message: string) => void) {
  const [lastError, setLastError] = useState("");

  const save = useCallback(
    async (target: SaveTarget, next: boolean) => {
      const outcome = await setSaved(target, next);
      if (!outcome.ok && outcome.message) {
        setLastError(outcome.message);
        onError?.(outcome.message);
      } else if (outcome.ok) {
        setLastError("");
      }
      return outcome;
    },
    [onError]
  );

  const toggle = useCallback(
    (type: SavableContentType, id: number | string, currentlySaved: boolean, extra?: Partial<SaveTarget>) =>
      save({ type, id, ...extra }, !currentlySaved),
    [save]
  );

  return { save, toggle, lastError };
}
