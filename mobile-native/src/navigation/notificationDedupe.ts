/**
 * Native-side duplicate-notification guard.
 *
 * A single message must surface exactly one foreground banner. Duplicates can still
 * reach the device even after the backend one-message-one-notification guard — e.g.
 * two device tokens for the same install, a push replayed after reconnect, or a
 * received-listener firing twice after a Fast Refresh. This module recognises the
 * SAME notification by its server-issued identity and drops the repeat.
 *
 * Identity is derived from stable payload IDs only — notification_id, then
 * messageId (+ conversationId), then call_id — falling back to the OS request
 * identifier. It is NEVER derived from the notification title/body: two distinct
 * messages that happen to share identical copy must remain distinct.
 */

type PayloadRecord = Record<string, unknown>;

function flattenNotificationData(data: PayloadRecord | null | undefined): PayloadRecord {
  const base = (data && typeof data === "object" && !Array.isArray(data) ? data : {}) as PayloadRecord;
  const nested =
    base.data && typeof base.data === "object" && !Array.isArray(base.data)
      ? (base.data as PayloadRecord)
      : {};
  return { ...nested, ...base };
}

function firstIdValue(payload: PayloadRecord, ...keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "number" && Number.isFinite(value) && value > 0) return String(Math.trunc(value));
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed && trimmed !== "0") return trimmed.slice(0, 120);
    }
  }
  return "";
}

/**
 * Stable identity for a notification, or "" when nothing usable is present.
 * Prefers server IDs so the same message is recognised across two OS notifications.
 */
export function notificationStableId(
  data: PayloadRecord | null | undefined,
  fallbackIdentifier?: string
): string {
  const payload = flattenNotificationData(data);

  const notificationId = firstIdValue(payload, "notification_id", "notificationId", "local_notification_id");
  if (notificationId) return `note:${notificationId}`;

  const messageId = firstIdValue(payload, "message_id", "messageId");
  if (messageId) {
    const conversationId = firstIdValue(payload, "conversation_id", "conversationId");
    return conversationId ? `msg:${conversationId}:${messageId}` : `msg:${messageId}`;
  }

  const callId = firstIdValue(payload, "call_id", "callId");
  if (callId) return `call:${callId}`;

  const fallback = String(fallbackIdentifier || "").trim();
  return fallback ? `req:${fallback}` : "";
}

export type DedupeStore = Map<string, number>;

export type DedupeOptions = {
  store?: DedupeStore;
  now?: number;
  windowMs?: number;
  maxEntries?: number;
};

const DEFAULT_WINDOW_MS = 60_000;
const DEFAULT_MAX_ENTRIES = 200;

// Module-level cache used by the live app. Tests pass their own store for isolation.
const defaultStore: DedupeStore = new Map();

function pruneStore(store: DedupeStore, now: number, windowMs: number, maxEntries: number): void {
  for (const [key, seenAt] of store) {
    if (now - seenAt > windowMs) store.delete(key);
  }
  if (store.size <= maxEntries) return;
  // Evict oldest first (Map preserves insertion order) until back under the cap.
  const overflow = store.size - maxEntries;
  let removed = 0;
  for (const key of store.keys()) {
    store.delete(key);
    if (++removed >= overflow) break;
  }
}

/**
 * Record a notification id and report whether it is NEW (should surface) within the
 * window. A repeat of the same id inside windowMs returns false. An empty id is
 * always treated as new — a notification we cannot identify is never suppressed.
 */
export function markNotificationSeen(id: string, options: DedupeOptions = {}): boolean {
  if (!id) return true;
  const store = options.store || defaultStore;
  const now = options.now ?? Date.now();
  const windowMs = options.windowMs ?? DEFAULT_WINDOW_MS;
  const maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES;

  const previous = store.get(id);
  if (previous !== undefined && now - previous <= windowMs) {
    store.set(id, now); // refresh so a steady stream of repeats stays suppressed
    return false;
  }
  store.set(id, now);
  pruneStore(store, now, windowMs, maxEntries);
  return true;
}

/** Test/util helper: clear the shared cache. */
export function resetNotificationDedupe(store: DedupeStore = defaultStore): void {
  store.clear();
}
