import * as Notifications from "expo-notifications";
import { pulseApi } from "../api/pulseApi";
import { getNotificationBadgeCounts } from "../api/notifications";
import { badgeFor, setUnreadCounts } from "./unreadCounts";
import { outboxScope, pendingMutations } from "./mutations/outbox";

const MESSAGE_TYPES = new Set(["message", "new_message", "chat_message", "private_message", "group_message", "image_message", "video_message", "voice_message", "file_message"]);
const id = (v: unknown): number => typeof v !== "boolean" && /^\d+$/.test(String(v)) && Number.isSafeInteger(Number(v)) && Number(v) > 0 ? Number(v) : 0;
export type MessageNotification = {
  key: string; messageId: number; conversationId: number; recipientUserId?: number;
  schemaVersion?: number; notificationType?: string; messageNamespace?: string;
  notificationId?: number; type?: string;
};

// Never inspect presentation text. Legacy identity is verified by the server.
export function parseMessageNotification(key: string, data: Record<string, unknown>, account: number): MessageNotification | null {
  const messageId = id(data.messageId ?? data.message_id);
  const conversationId = id(data.conversationId ?? data.conversation_id);
  if (!key || !messageId || !conversationId) return null;
  if (data.schemaVersion !== undefined) {
    if (data.schemaVersion !== 1 || data.notificationType !== "message" || data.messageNamespace !== "comm_v2" || id(data.recipientUserId) !== account) return null;
    return { key, messageId, conversationId, recipientUserId: account, schemaVersion: 1, notificationType: "message", messageNamespace: "comm_v2" };
  }
  if (data.notificationType !== undefined && data.notificationType !== "message") return null;
  const type = String(data.type || "");
  const notificationId = id(data.notification_id);
  if (!MESSAGE_TYPES.has(type) || !notificationId) return null;
  return { key, messageId, conversationId, notificationId, type };
}

export type ReconciliationResult = { examined: number; dismissed: number; preserved: number; failures: number; cancelled: boolean };
type Flight = { epoch: number; again: boolean; promise: Promise<ReconciliationResult> };
let flight: Flight | null = null;
let generation = 0;
export function cancelMessageReconciliation() { generation += 1; }

export function reconcileMessageNotifications(): Promise<ReconciliationResult> {
  // Coalescing is scoped to one identity, not global. A caller arriving after
  // `cancelMessageReconciliation` -- which is how an account switch announces
  // itself -- belongs to a different account than the pass in flight, and that
  // pass is about to abandon itself because its own scope check now fails.
  // Handing the shared promise over would report a completed reconciliation to
  // a caller whose Notification Center was never enumerated, so the incoming
  // account's stale alerts would sit there until some later lifecycle trigger
  // happened to fire. Nothing errors and nothing logs, which is what makes the
  // extra field worth it.
  const joinable = flight;
  if (joinable && joinable.epoch === generation) { joinable.again = true; return joinable.promise; }
  // `again` rides on the entry rather than the module for the same reason: two
  // passes from different generations can briefly overlap, and a shared flag
  // lets the incoming pass clear the outgoing one's trailing-pass request.
  const entry: Flight = { epoch: generation, again: false, promise: null as unknown as Promise<ReconciliationResult> };
  entry.promise = (async () => {
    let result: ReconciliationResult = { examined: 0, dismissed: 0, preserved: 0, failures: 0, cancelled: false };
    // A trailing pass picks up pushes/read events arriving during enumeration.
    for (let pass = 0; pass < 2; pass += 1) {
      entry.again = false;
      result = await run();
      if (!entry.again || result.cancelled) break;
    }
    return result;
    // Only clear our own slot. A pass started after a switch has already
    // replaced `flight`, and a blind `flight = null` here would drop it.
  })().finally(() => { if (flight === entry) flight = null; });
  flight = entry;
  return entry.promise;
}

async function run(): Promise<ReconciliationResult> {
  const result: ReconciliationResult = { examined: 0, dismissed: 0, preserved: 0, failures: 0, cancelled: false };
  const scope = outboxScope();
  const account = id(scope.slice(1));
  const epoch = generation;
  const current = () => epoch === generation && scope === outboxScope();
  if (!account || !scope.startsWith("u")) return result;
  try {
    const presented = await Notifications.getPresentedNotificationsAsync();
    const pending = await pendingMutations();
    const candidates = presented.map(n => parseMessageNotification(n.request.identifier, n.request.content.data || {}, account)).filter((n): n is MessageNotification => n !== null);
    result.examined = presented.length;
    for (let offset = 0; offset < candidates.length; offset += 100) {
      if (!current()) break;
      const entries = candidates.slice(offset, offset + 100);
      const removable = new Set<string>();
      // Durable local reads apply ONLY to explicitly displayed IDs, never to a
      // timestamp or a guessed conversation watermark, and only to scoped pushes.
      for (const entry of entries) {
        if (entry.recipientUserId === account && pending.some(op => {
          if (op.type !== "messenger.read") return false;
          const p = op.payload as { conversationId: number; messageIds: number[] };
          return p.conversationId === entry.conversationId && p.messageIds.includes(entry.messageId);
        })) removable.add(entry.key);
      }
      const locallyDismissed = new Set<string>();
      for (const key of removable) {
        if (!current()) break;
        try {
          await Notifications.dismissNotificationAsync(key);
          result.dismissed += 1;
          locallyDismissed.add(key);
        }
        catch { result.failures += 1; }
      }
      removable.clear();
      if (!current()) break;
      try {
        const response = await pulseApi<{ ok: boolean; recipientUserId: number; dismiss: string[] }>("/api/pulse/communications/v2/notifications/reconcile", { method: "POST", body: JSON.stringify({ entries }) });
        if (response.ok && response.recipientUserId === account && Array.isArray(response.dismiss)) {
          const allowed = new Set(entries.map(e => e.key));
          response.dismiss.forEach(key => { if (allowed.has(key) && !locallyDismissed.has(key)) removable.add(key); });
        }
      } catch { result.failures += 1; }
      for (const key of removable) {
        if (!current()) break;
        try { await Notifications.dismissNotificationAsync(key); result.dismissed += 1; }
        catch { result.failures += 1; }
      }
    }
    if (current()) {
      // Failure leaves the last badge intact; never publish an empty fallback.
      try {
        const counts = await getNotificationBadgeCounts();
        if (current()) {
          const snapshot = setUnreadCounts(counts);
          // The icon takes the "combined" scope, not `totalCount`: `totalCount`
          // is notifications + *social* messages, so a business↔customer unread
          // left the icon blank and nothing brought the seller back to the app.
          // Read through `badgeFor` so the icon and the in-app combined badge
          // stay one definition — re-deriving the sum here is how they drift.
          await Notifications.setBadgeCountAsync(Math.max(0, badgeFor("combined", snapshot).count));
        }
      } catch { result.failures += 1; }
    }
  } catch { result.failures += 1; }
  result.cancelled = !current();
  result.preserved = result.examined - result.dismissed;
  return result;
}
