import { useCallback, useRef, useState } from "react";
import { PulseApiError } from "../api/pulseApi";
import { translate } from "../i18n/engine";

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
 * What kind of failure a rejected PulseSoc request was, independent of copy.
 *
 * Split out so the read side and the write side of the save contract cannot
 * drift about what a 401 or a dead socket means, while still wording their
 * messages for what the user was actually doing.
 */
export type PulseFailureKind = "auth" | "forbidden" | "offline" | "server" | "request" | "unknown";

export function classifyPulseFailure(err: unknown): PulseFailureKind {
  if (err instanceof PulseApiError) {
    // `pulseApi` reports a fetch that never produced a response as 503
    // `request_unreachable`, and a session refresh it could not finish as 503
    // `session_refresh_temporary`. Both are connectivity, not a failing server,
    // and telling the user to "try again in a moment" when they are on a plane
    // is the wrong instruction — so these are checked before the 5xx range.
    if (err.status === 0) return "offline";
    if (err.code === "request_unreachable" || err.code === "session_refresh_temporary") return "offline";
    if (err.status === 401) return "auth";
    if (err.status === 403) return "forbidden";
    if (err.status >= 500) return "server";
    if (err.status >= 400) return "request";
    return "unknown";
  }
  if (err instanceof TypeError || (err instanceof Error && /network|offline|timeout/i.test(err.message))) {
    return "offline";
  }
  return "unknown";
}

const SAVED_LIBRARY_ERROR_KEYS: Readonly<Record<PulseFailureKind, string>> = Object.freeze({
  auth: "errors:savedLibrary.signedOut",
  forbidden: "errors:savedLibrary.noAccess",
  offline: "errors:savedLibrary.offline",
  server: "errors:savedLibrary.server",
  request: "errors:savedLibrary.request",
  unknown: "errors:savedLibrary.unknown"
});

/**
 * User-facing copy for a failed *read* of the Saved library.
 *
 * REUSE DECISION. This sits beside `describeSocialActionError` in the module
 * that already owns social error copy, rather than in a new one, because there
 * should not be a second error-describing system. It is a separate function
 * rather than another `action` argument to that one for two reasons:
 *
 *   1. `describeSocialActionError` is write-shaped in its wording — "could not
 *      be completed", "that was already done", "{action} could not be completed
 *      right now" — all of which describe something the user just did. Opening
 *      Saved is a read, and no phrasing of an `action` string fixes a sentence
 *      built around a verb the user did not perform.
 *   2. Its 4xx branch falls through to `err.message`, i.e. whatever the server
 *      said. That is exactly the defect being fixed here: the Flask JSON error
 *      handler answers *every* API path with upload copy, so echoing the server
 *      puts "Upload failed. Please retry…" on a read of the saved library.
 *      Nothing on this path may ever render a raw server string.
 *
 * The two share `classifyPulseFailure`, which is the part worth having in
 * common — the mapping from a transport failure to a category, not the prose.
 */
export function describeSavedLibraryError(err: unknown): string {
  return withTraceReference(translate(SAVED_LIBRARY_ERROR_KEYS[classifyPulseFailure(err)]), err);
}

/**
 * The write actions the Saved screen can perform on the library itself.
 *
 * A closed union rather than a free-text label because each one names a
 * translated sentence in the catalog. A caller that invents a name gets a
 * compile error instead of an untranslated string in production.
 */
export type SavedActionName = "create" | "rename" | "delete" | "remove" | "move";

const SAVED_ACTION_SUBJECT_KEYS: Readonly<Record<SavedActionName, string>> = Object.freeze({
  create: "errors:savedAction.create",
  rename: "errors:savedAction.rename",
  delete: "errors:savedAction.delete",
  remove: "errors:savedAction.remove",
  move: "errors:savedAction.move"
});

const SAVED_ACTION_REASON_KEYS: Readonly<Record<PulseFailureKind, string>> = Object.freeze({
  auth: "errors:savedAction.signedOut",
  forbidden: "errors:savedAction.noAccess",
  offline: "errors:savedAction.offline",
  server: "errors:savedAction.server",
  request: "errors:savedAction.request",
  unknown: "errors:savedAction.unknown"
});

/**
 * User-facing copy for a failed *write* against the Saved library — creating,
 * renaming or deleting a collection, or removing/moving an item.
 *
 * REUSE DECISION. `describeSocialActionError` is the module's existing
 * write-shaped describer and it already takes an action name, so routing these
 * handlers through it was the obvious move. It is the wrong one, for three
 * reasons that all show up on this screen specifically:
 *
 *   1. Its unmapped-4xx branch returns `err.message` — the server's own words.
 *      That is the exact leak this work exists to close: the Flask JSON error
 *      handler answers every failing API path with upload copy, so a 400 from
 *      `/api/pulse/saved/collections` renders "Upload failed. Please retry…"
 *      under a rename box. Adding another action name does not fix a function
 *      whose fallback is "print whatever the server said".
 *   2. Its copy is hardcoded English. The read path on this same screen is now
 *      catalog-driven, so reusing it would show a Spanish user a translated
 *      load failure and an English rename failure in the same view.
 *   3. It discards `details.trace_id`, which the read path deliberately keeps
 *      because support asks for it.
 *
 * So this is `describeSavedLibraryError`'s treatment applied to the write side,
 * not a second system: the same `classifyPulseFailure`, the same
 * `withTraceReference`, the same catalog. Only the prose differs, and it has to
 * — a read says "your library couldn't load", a write has to name the thing the
 * user just tried to change.
 *
 * The sentence is composed from two whole sentences (what failed, then why) via
 * `savedAction.detail` rather than by interpolating a verb into a frame. A noun
 * dropped into a sentence frame has to agree with it, and eleven languages do
 * not agree the same way; two independent sentences concatenate safely in all
 * of them, which is the same reason `savedLibrary.reference` is shaped that way.
 */
export function describeSavedActionError(err: unknown, action: SavedActionName): string {
  const message = translate("errors:savedAction.detail", {
    what: translate(SAVED_ACTION_SUBJECT_KEYS[action]),
    why: translate(SAVED_ACTION_REASON_KEYS[classifyPulseFailure(err)])
  });
  return withTraceReference(message, err);
}

/**
 * Appends the server's trace id to already-safe copy, when it supplied one.
 *
 * Shared by the read and write describers so there is exactly one place that
 * decides what a trace id may look like. Duplicating it would be duplicating a
 * filter, and a filter that exists twice is a filter that will be loosened once.
 */
function withTraceReference(message: string, err: unknown): string {
  const trace = pulseTraceId(err);
  if (!trace) return message;
  return translate("errors:savedLibrary.reference", { message, trace });
}

/**
 * The server's trace id, when it supplied one.
 *
 * Support asks for this id, so it is kept — but only the id is surfaced, never
 * the message it travelled with, and it must look like an id: an unbounded or
 * free-text `trace_id` would be another way for server prose to reach the
 * screen through the door this fix is closing.
 */
function pulseTraceId(err: unknown): string {
  if (!(err instanceof PulseApiError)) return "";
  const raw = err.details?.trace_id;
  if (typeof raw !== "string") return "";
  const clean = raw.trim().slice(0, 64);
  return /^[A-Za-z0-9_-]+$/.test(clean) ? clean : "";
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
