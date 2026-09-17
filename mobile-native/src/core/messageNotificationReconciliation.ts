import * as Notifications from "expo-notifications";
import { pulseApi } from "../api/pulseApi";
import { getNotificationBadgeCounts } from "../api/notifications";
import { setUnreadCounts } from "./unreadCounts";
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
let flight: Promise<ReconciliationResult> | null = null;
let again = false;
let generation = 0;
export function cancelMessageReconciliation() { generation += 1; }

export function reconcileMessageNotifications(): Promise<ReconciliationResult> {
  if (flight) { again = true; return flight; }
  flight = (async () => {
    let result: ReconciliationResult = { examined: 0, dismissed: 0, preserved: 0, failures: 0, cancelled: false };
    // A trailing pass picks up pushes/read events arriving during enumeration.
    for (let pass = 0; pass < 2; pass += 1) {
      again = false;
      result = await run();
      if (!again || result.cancelled) break;
    }
    return result;
  })().finally(() => { flight = null; });
  return flight;
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
          await Notifications.setBadgeCountAsync(Math.max(0, snapshot.totalCount));
        }
      } catch { result.failures += 1; }
    }
  } catch { result.failures += 1; }
  result.cancelled = !current();
  result.preserved = result.examined - result.dismissed;
  return result;
}
