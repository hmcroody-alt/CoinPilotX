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
 *
 * COMMERCE SCOPE DECISION (documented): business↔customer threads are counted in
 * `commerceCount`, never in `messageCount`. The social Messages list requests
 * `include_types={"direct"}` from the server and so has never rendered a
 * business thread; a chat badge that counted them was pointing at a conversation
 * the screen would not show, which is an unread the user cannot clear. The split
 * is made server-side (`notification_service.pulse_badge_counts` returns
 * `commerce_unread_count` alongside `chat_unread_count`); this store only carries
 * it through. `totalCount` mirrors the server's `total_unread_count` and stays
 * notifications + social messages — see `totalUnreadCount` for why that number
 * has to agree with the client's own fallback arithmetic.
 */

import { useSyncExternalStore } from "react";
import {
  NotificationBadgeCounts,
  alertUnreadCount,
  chatUnreadCount,
  commerceUnreadCount,
  getNotificationBadgeCounts,
  totalUnreadCount
} from "../api/notifications";
import { registerSyncInvalidation } from "./eventSync";
import { isFlagValueOn } from "./envFlag";

export type UnreadSnapshot = {
  /** The bell number: notification unreads (messages excluded — see header doc). */
  bellCount: number;
  /** Social message unreads, badged separately from the bell. */
  messageCount: number;
  /**
   * Business↔customer thread unreads, for the Commerce Inbox badge. Kept out of
   * `messageCount` because the social Messages list does not render business
   * threads — counting them there produced an unread nothing could clear.
   */
  commerceCount: number;
  /** Notifications + social messages, for callers that want the combined figure. */
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
    commerceCount: commerceUnreadCount(counts),
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
    next.commerceCount === snapshot.commerceCount &&
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
export function applyOptimisticRead(
  scope: "notifications" | "messages" | "commerce" | "all" = "notifications"
): UnreadSnapshot {
  const raw = { ...snapshot.raw };
  if (scope === "notifications" || scope === "all") {
    raw.alert_unread_count = 0;
    raw.unread_count = 0;
    raw.count = 0;
  }
  if (scope === "messages" || scope === "all") {
    raw.chat_unread_count = 0;
  }
  // Separate scope, because clearing the Commerce Inbox must not blank the
  // social Messages badge and vice-versa — the same reason the two counts were
  // split server-side in the first place.
  if (scope === "commerce" || scope === "all") {
    raw.commerce_unread_count = 0;
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

/* ------------------------------------------------------------------ *
 * Badge scopes
 * ------------------------------------------------------------------ */

export const SCOPED_BADGES_FLAG = "EXPO_PUBLIC_SCOPED_BADGES";

/** True when a build has opted into scope-stating badges. Off by default.
 *  Reads the shared truthy set — see `./envFlag`.
 *
 *  Spelled out rather than read through `SCOPED_BADGES_FLAG`, which holds the
 *  same string: `babel-preset-expo` inlines `process.env.X` only for a
 *  StringLiteral key, so the constant form was never substituted and read
 *  undefined on device. The constant stays because the tests name it. */
export function scopedBadgesEnabled(): boolean {
  return isFlagValueOn(process.env.EXPO_PUBLIC_SCOPED_BADGES);
}

/**
 * What a badge is counting.
 *
 * The defect this closes: `AppNavigator` kept its own copy of the counts and
 * gave the Activity bell `totalUnreadCount` — notifications *plus* messages —
 * while a messages badge sat directly beside it showing the message half again.
 * Two badges, one number counted twice, and neither of them said what it was
 * counting, so there was no way to notice from the screen.
 *
 * A badge is a number with no noun. The noun has to come from somewhere, and the
 * only safe place for it is next to the number's definition — which is here, in
 * the store the number comes from. A label written at the call site can drift
 * from the count it labels; one written here cannot, because it is returned
 * alongside it.
 */
export type BadgeScope = "notifications" | "messages" | "commerce" | "combined";

export type BadgeDescriptor = {
  scope: BadgeScope;
  count: number;
  /**
   * What the number counts, spelled out for a screen reader. A bare "3" beside
   * an icon is ambiguous even to a sighted reader and meaningless spoken.
   */
  spokenLabel: string;
};

/** One badge, read from the shared snapshot. Never from a second source. */
export function badgeFor(scope: BadgeScope, from: UnreadSnapshot = snapshot): BadgeDescriptor {
  const count =
    scope === "notifications"
      ? from.bellCount
      : scope === "messages"
        ? from.messageCount
        : scope === "commerce"
          ? from.commerceCount
          : from.totalCount + from.commerceCount;
  return { scope, count, spokenLabel: badgeSpokenLabel(scope, count) };
}

/**
 * The sentence a badge announces.
 *
 * Zero is spoken as "no unread…" rather than left silent because the label is
 * attached to a control that is still there: a bell with nothing on it should
 * say so, not go quiet and leave the reader wondering whether it failed to load.
 */
export function badgeSpokenLabel(scope: BadgeScope, count: number): string {
  const noun =
    scope === "notifications"
      ? count === 1
        ? "unread notification"
        : "unread notifications"
      : scope === "messages"
        ? count === 1
          ? "unread message"
          : "unread messages"
        : scope === "commerce"
          ? count === 1
            ? "unread order message"
            : "unread order messages"
          : count === 1
            ? "unread notification or message"
            : "unread notifications and messages";
  return count === 0 ? `No ${noun}` : `${count} ${noun}`;
}

/**
 * The three numbers the global navigation renders, all from this store.
 *
 * `activity` is the bell count, not the total. That is the whole correction: the
 * bell sits beside a messages badge, so a bell carrying messages too counts the
 * same unread twice on one strip. The combined figure still exists — it is what
 * the phone's app icon wants, where there is no second badge to double against —
 * and is returned as `combined` rather than quietly reused as `activity`.
 *
 * `commerce` is its own strip badge for the same reason `messages` is: the two
 * lists are different lists. It IS folded into `combined`, because the app icon
 * is the one badge with nothing beside it — an unread the icon omits is an
 * unread that never brings anyone back to the app.
 */
export function navigationBadgesFrom(from: UnreadSnapshot = snapshot): {
  activity: number;
  messages: number;
  commerce: number;
  alerts: number;
  combined: number;
} {
  return {
    activity: from.bellCount,
    messages: from.messageCount,
    commerce: from.commerceCount,
    // The header's "N alerts" chip and the bell are the same notifications, so
    // they must be the same number. They were two fields reading two functions.
    alerts: from.bellCount,
    combined: from.totalCount + from.commerceCount
  };
}

/** Subscribe a component to one scoped badge. */
export function useBadge(scope: BadgeScope): BadgeDescriptor {
  return badgeFor(scope, useUnreadCounts());
}
