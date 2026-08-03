/**
 * UnreadCountStore — the ONE source of the header bell's unread number.
 *
 * The bell in every seller header (Store, Marketplace, Advertising, Orders,
 * Messages, Events…) and the Activity feed's unread state must be the same
 * number from the same place. Before this store they diverged: the Store
 * dashboard reused `openOrders`, Marketplace hard-coded 0, and only
 * AppNavigator held the real `getNotificationBadgeCounts()` result — in local
 * component state that nothing else could read. Divergent counts are a bug; this
 * consolidates them.
 *
 * The app's idiom for a shared, subscribable value is a module-level store with
 * a listener Set plus the `eventSync` register/invalidate bus (see
 * `core/eventSync.ts`). This follows exactly that: a module singleton, a
 * `useSyncExternalStore` hook for components, and an opt-in `initUnreadCountSync`
 * that wires it to the same sync-invalidation events the rest of the app already
 * fires — so a push arriving (which invalidates "notifications"/"activity") or
 * marking-read refreshes every bell on next render.
 *
 * BELL SCOPE DECISION (documented): the bell shows *notification* unreads
 * (`alertUnreadCount`) and NOT message unreads. Messages are badged separately
 * in the app's global header (a dedicated chat bubble with `chatUnreadCount`),
 * so folding chat into the bell would double-count. `messageCount` is exposed
 * separately for the surfaces that badge messages. `totalCount` (notifications +
 * messages) is available for callers that intentionally want the combined number.
 */

import { useSyncExternalStore } from "react";
import {
  NotificationBadgeCounts,
  alertUnreadCount,
  chatUnreadCount,
  getNotificationBadgeCounts,
  totalUnreadCount
} from "../api/notifications";
import { registerSyncInvalidation } from "./eventSync";

export type UnreadSnapshot = {
  /** The bell number: notification unreads (messages excluded — see header doc). */
  bellCount: number;
  /** Message unreads, badged separately from the bell. */
  messageCount: number;
  /** Notifications + messages, for callers that want the combined figure. */
  totalCount: number;
  /** Epoch ms of the last successful refresh, or 0 if never loaded. */
  loadedAt: number;
  raw: NotificationBadgeCounts;
};

const EMPTY_COUNTS: NotificationBadgeCounts = {};

let snapshot: UnreadSnapshot = deriveSnapshot(EMPTY_COUNTS, 0);
const listeners = new Set<() => void>();

function deriveSnapshot(counts: NotificationBadgeCounts, loadedAt: number): UnreadSnapshot {
  return {
    bellCount: alertUnreadCount(counts),
    messageCount: chatUnreadCount(counts),
    totalCount: totalUnreadCount(counts),
    loadedAt,
    raw: counts
  };
}

function emit() {
  listeners.forEach((l) => l());
}

/** Current snapshot (synchronous). */
export function getUnreadSnapshot(): UnreadSnapshot {
  return snapshot;
}

/**
 * Replace the counts from an authoritative source (a badge-count response, or a
 * `badge_counts` payload piggy-backed on a mark-read / resolve call). Only emits
 * when the derived numbers actually change, so bells don't re-render for nothing.
 */
export function setUnreadCounts(counts: NotificationBadgeCounts | undefined, loadedAt = Date.now()): UnreadSnapshot {
  const next = deriveSnapshot(counts || EMPTY_COUNTS, loadedAt);
  if (
    next.bellCount === snapshot.bellCount &&
    next.messageCount === snapshot.messageCount &&
    next.totalCount === snapshot.totalCount
  ) {
    // Numbers unchanged; keep the newer loadedAt but skip the notify.
    snapshot = { ...snapshot, loadedAt };
    return snapshot;
  }
  snapshot = next;
  emit();
  return snapshot;
}

/**
 * Optimistically zero the bell (mark-all-read). Applied before the backend
 * confirms so the UI is instant; a subsequent `setUnreadCounts` from the
 * confirmed response reconciles. `scope` lets a message-only mark-read leave the
 * bell alone, and vice-versa.
 */
export function applyOptimisticRead(scope: "notifications" | "messages" | "all" = "notifications"): UnreadSnapshot {
  const raw = { ...snapshot.raw };
  if (scope === "notifications" || scope === "all") {
    raw.alert_unread_count = 0;
    raw.unread_count = 0;
    raw.count = 0;
  }
  if (scope === "messages" || scope === "all") {
    raw.chat_unread_count = 0;
  }
  raw.total_unread_count = (scope === "all" ? 0 : undefined) as number | undefined;
  snapshot = deriveSnapshot(raw, snapshot.loadedAt);
  emit();
  return snapshot;
}

/** Subscribe to snapshot changes. Returns an unsubscribe. */
export function subscribeUnread(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

let refreshInFlight: Promise<UnreadSnapshot> | null = null;

/**
 * Pull the authoritative counts from the backend and publish them. De-duped: a
 * concurrent call shares the in-flight request. Failures are swallowed (the bell
 * keeps its last known value) — a missing count must never crash a header.
 */
export async function refreshUnreadCounts(): Promise<UnreadSnapshot> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const counts = await getNotificationBadgeCounts();
      return setUnreadCounts(counts);
    } catch {
      return snapshot;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/**
 * Wire the store to the app's sync-invalidation bus so a push arriving or any
 * subsystem firing "notifications"/"activity" refreshes the bell everywhere.
 * Opt-in (call once from AppNavigator) so importing the store never triggers
 * network — keeps it unit-testable. Returns an unsubscribe.
 */
export function initUnreadCountSync(): () => void {
  const offNotifications = registerSyncInvalidation("notifications", () => {
    void refreshUnreadCounts();
  });
  const offActivity = registerSyncInvalidation("activity", () => {
    void refreshUnreadCounts();
  });
  return () => {
    offNotifications();
    offActivity();
  };
}

/** Test-only reset of the module singleton. */
export function __resetUnreadCounts() {
  snapshot = deriveSnapshot(EMPTY_COUNTS, 0);
  listeners.clear();
  refreshInFlight = null;
}

/* ------------------------------------------------------------------ *
 * React binding
 * ------------------------------------------------------------------ */

/**
 * Subscribe a component to the shared unread snapshot. Every bell that calls
 * this renders the same number and updates together.
 */
export function useUnreadCounts(): UnreadSnapshot {
  return useSyncExternalStore(subscribeUnread, getUnreadSnapshot, getUnreadSnapshot);
}

/** Convenience: just the bell number. */
export function useBellCount(): number {
  return useUnreadCounts().bellCount;
}
