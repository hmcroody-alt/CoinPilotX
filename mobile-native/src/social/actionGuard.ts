import { useCallback, useRef, useState } from "react";
import { PulseApiError } from "../api/pulseApi";

/**
 * Duplicate-request prevention and stale-response rejection for optimistic
 * social actions.
 *
 * Two distinct defects motivated this module, and they need two distinct
 * mechanisms — conflating them is why the existing screens are wrong.
 *
 * 1. DUPLICATE REQUESTS. HomeScreen used a single scalar `busyPostId` and
 *    ReelsScreen a single scalar `busyId`. A scalar cannot represent "post 7
 *    and post 9 are both in flight", so the second tap either clobbers the
 *    first one's busy marker or is wrongly blocked. Worse, both screens mostly
 *    only *set* the scalar and never *read* it, so it prevented nothing at all.
 *    Fixed with a ref-held Set keyed by `${action}:${id}` — a ref, not state,
 *    because a lock that is only visible after the next render is not a lock.
 *
 * 2. STALE RESPONSES. Even with duplicates prevented per action+id, a like
 *    followed by an unlike can resolve out of order, and the slower first
 *    response then overwrites the newer state, or its catch rolls back to a
 *    count that is two updates old ("count drift"). Fixed with a monotonic
 *    per-key sequence: a response is applied only if its sequence is still the
 *    latest. This is StatusScreen's `reactionSeqRef` pattern, generalised.
 *
 * The Set drives a React state mirror as well, so buttons can render a spinner
 * and set accessibilityState={{ busy }} — but correctness never depends on the
 * mirror.
 */

export type ActionKey = string;

export type RunOptions<T> = {
  /** Applied immediately, before the request. Return value is ignored. */
  optimistic?: () => void;
  /** Applied on success, only if this call is still the latest for its key. */
  onResult?: (result: T) => void;
  /** Applied on failure, only if this call is still the latest for its key. */
  onRollback?: (error: unknown) => void;
  /**
   * Called when the request fails and this call is still the latest, with
   * ready-made user-facing copy and the raw error. The raw error is passed so a
   * caller with better wording for its own domain — delete, which distinguishes
   * "not yours" from "already gone" via describeDeleteError — can still report
   * through this handler rather than smuggling the message out through
   * `onRollback`. That distinction matters: `onRollback` is for restoring state,
   * and a caller that messages from there looks, to the check below, like a
   * caller that reports nothing at all.
   */
  onError?: (message: string, error: unknown) => void;
  /**
   * When true a second call for the same key is allowed to proceed and simply
   * supersedes the first via the sequence guard. Default false: the second call
   * is dropped. Use true for reaction pickers, where the user legitimately
   * changes their mind mid-flight.
   */
  supersede?: boolean;
};

export type SocialActionGuard = {
  /** True while any request for this key is in flight. Render-safe. */
  isBusy: (key: ActionKey) => boolean;
  /**
   * True while any request for any action on this item is in flight.
   *
   * This is the replacement for the `busyPostId === item.id` / `busyId === reel.id`
   * comparisons the feed screens used to render a card's spinner. Those scalars
   * could only ever mark ONE item, so liking post 7 greyed out post 9's buttons
   * too. Keying by `${action}:${id}` and matching on the id suffix marks exactly
   * the card the user is acting on, and marks it for every action on it.
   */
  isItemBusy: (id: number | string) => boolean;
  /** True while any request at all is in flight. */
  anyBusy: () => boolean;
  /**
   * Run an optimistic action under the guard. Resolves to the result, or to
   * `undefined` when the call was dropped as a duplicate or superseded.
   */
  run: <T>(key: ActionKey, request: () => Promise<T>, options?: RunOptions<T>) => Promise<T | undefined>;
};

export function actionKey(action: string, id: number | string) {
  return `${action}:${id}`;
}

export function useSocialActionGuard(): SocialActionGuard {
  // Authoritative, synchronous. Never read from state for correctness.
  const inFlight = useRef<Set<ActionKey>>(new Set());
  const sequences = useRef<Map<ActionKey, number>>(new Map());
  // Render mirror only.
  const [busyKeys, setBusyKeys] = useState<Set<ActionKey>>(new Set());

  const markBusy = useCallback((key: ActionKey, busy: boolean) => {
    setBusyKeys((current) => {
      if (busy === current.has(key)) return current;
      const next = new Set(current);
      if (busy) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);

  const isBusy = useCallback((key: ActionKey) => busyKeys.has(key), [busyKeys]);
  const anyBusy = useCallback(() => busyKeys.size > 0, [busyKeys]);
  const isItemBusy = useCallback((id: number | string) => {
    // Matched on the suffix rather than by splitting, because an action name is
    // free-form and could itself contain a colon. The id is always last.
    const suffix = `:${id}`;
    for (const key of busyKeys) {
      if (key.endsWith(suffix)) return true;
    }
    return false;
  }, [busyKeys]);

  const run = useCallback(async <T,>(
    key: ActionKey,
    request: () => Promise<T>,
    options: RunOptions<T> = {}
  ): Promise<T | undefined> => {
    const { optimistic, onResult, onRollback, onError, supersede = false } = options;

    if (inFlight.current.has(key) && !supersede) return undefined;

    const seq = (sequences.current.get(key) || 0) + 1;
    sequences.current.set(key, seq);
    inFlight.current.add(key);
    markBusy(key, true);

    const isLatest = () => sequences.current.get(key) === seq;

    try {
      optimistic?.();
      const result = await request();
      if (!isLatest()) return undefined;
      onResult?.(result);
      return result;
    } catch (err) {
      if (!isLatest()) return undefined;
      onRollback?.(err);
      const message = describeSocialActionError(err);
      if (onError) onError(message, err);
      else if (__DEV__) {
        // A social action that fails with no user-visible signal is the defect
        // this module exists to prevent. Loud in development, never silent.
        console.warn(`[social] ${key} failed with no onError handler: ${message}`);
      }
      return undefined;
    } finally {
      if (isLatest()) {
        inFlight.current.delete(key);
        markBusy(key, false);
      }
    }
  }, [markBusy]);

  return { isBusy, isItemBusy, anyBusy, run };
}

/**
 * User-facing copy for any failed social action. Generalises
 * api/status.ts:describeStatusReactionError so every content type reports
 * failures in the same words.
 */
export function describeSocialActionError(err: unknown, action = "That action"): string {
  if (err instanceof PulseApiError) {
    if (err.status === 401) return "Your session expired. Sign in again to continue.";
    if (err.status === 403) return "You do not have permission to do that.";
    if (err.status === 404) return "This content is no longer available.";
    if (err.status === 409) return "That was already done.";
    if (err.status === 429) return "Too many attempts. Wait a moment and try again.";
    if (err.status >= 500) return `${action} could not be completed right now. Try again.`;
    return err.message || `${action} could not be completed.`;
  }
  if (err instanceof TypeError || (err instanceof Error && /network|offline|timeout/i.test(err.message))) {
    return "You're offline. Reconnect and try again.";
  }
  return `${action} could not be completed.`;
}

/**
 * True when the failure is transient and the action is worth retrying once
 * connectivity returns — the basis for offline recovery of queued actions.
 */
export function isRecoverableSocialError(err: unknown): boolean {
  if (err instanceof PulseApiError) return err.status === 0 || err.status === 429 || err.status >= 500;
  if (err instanceof TypeError) return true;
  return err instanceof Error && /network|offline|timeout/i.test(err.message);
}
