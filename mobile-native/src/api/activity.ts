import { readJsonCache, writeJsonCache } from "../core/cache";
import { getActiveCalls, loadCachedActiveCalls, PulseCall } from "./calls";
import { loadCachedConversations, listConversations, MessengerConversation } from "./messenger";
import {
  deleteNotification,
  getNotificationBadgeCounts,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  NotificationBadgeCounts,
  PulseNotification,
  resolveNotificationTarget,
  unreadCount
} from "./notifications";

const ACTIVITY_INBOX_CACHE_KEY = "pulsesoc.native.activity.inbox";

export type ActivityCategory =
  | "all"
  | "messages"
  | "calls"
  | "social"
  | "safety"
  | "verification"
  | "marketplace"
  | "creator_growth"
  | "intelligence_alerts";

export type ActivitySource = "notification" | "message_summary" | "call_summary";

export type ActivityInboxItem = {
  id: string;
  source: ActivitySource;
  category: Exclude<ActivityCategory, "all">;
  title: string;
  body: string;
  createdAt?: string;
  unread: boolean;
  priority?: string;
  targetUrl?: string;
  notificationId?: number;
  conversationId?: number;
  callId?: string;
  rawType?: string;
};

export type ActivityInboxState = {
  items: ActivityInboxItem[];
  counts: NotificationBadgeCounts;
  unreadTotal: number;
  categoryCounts: Record<Exclude<ActivityCategory, "all">, number>;
  loadedFromCache?: boolean;
  serverAuthoritative: boolean;
};

export const activityCategories: Array<{ key: ActivityCategory; label: string }> = [
  { key: "all", label: "All" },
  { key: "messages", label: "Messages" },
  { key: "calls", label: "Calls" },
  { key: "social", label: "Social" },
  { key: "safety", label: "Safety" },
  { key: "verification", label: "Verification" },
  { key: "marketplace", label: "Marketplace" },
  { key: "creator_growth", label: "Creator/Growth" },
  { key: "intelligence_alerts", label: "Intelligence" }
];

export async function loadActivityInboxState(params: { limit?: number } = {}): Promise<ActivityInboxState> {
  try {
    const [notificationList, counts, conversations, activeCalls] = await Promise.all([
      listNotifications({ limit: params.limit || 100 }),
      getNotificationBadgeCounts(),
      listConversations().catch(loadCachedConversations),
      getActiveCalls().catch(loadCachedActiveCalls)
    ]);
    const state = normalizeActivityInboxState({
      notifications: notificationList.notifications || [],
      counts,
      conversations,
      calls: activeCalls.calls || [],
      loadedFromCache: false
    });
    await writeJsonCache(ACTIVITY_INBOX_CACHE_KEY, state).catch(() => undefined);
    return state;
  } catch (error) {
    const cached = await loadCachedActivityInboxState();
    if (cached) return { ...cached, loadedFromCache: true };
    throw error;
  }
}

export async function loadCachedActivityInboxState() {
  return readJsonCache<ActivityInboxState>(ACTIVITY_INBOX_CACHE_KEY, normalizeCachedActivityInboxState);
}

export async function markActivityItemRead(item: ActivityInboxItem) {
  if (!item.notificationId) return null;
  return markNotificationRead(item.notificationId);
}

export async function deleteActivityItem(item: ActivityInboxItem) {
  if (!item.notificationId) return null;
  return deleteNotification(item.notificationId);
}

export async function resolveActivityItemTarget(item: ActivityInboxItem) {
  if (item.notificationId) {
    const resolved = await resolveNotificationTarget(item.notificationId);
    return resolved.target_url || item.targetUrl || "/pulse/activity";
  }
  if (item.conversationId) return `/pulse/messages/${encodeURIComponent(String(item.conversationId))}`;
  if (item.callId) return `/pulse/calls/${encodeURIComponent(item.callId)}`;
  return item.targetUrl || "/pulse/activity";
}

export async function markActivityCategoryRead(category: ActivityCategory = "all") {
  return markAllNotificationsRead(category === "all" ? undefined : backendNotificationCategory(category));
}

function normalizeActivityInboxState(input: {
  notifications: PulseNotification[];
  counts: NotificationBadgeCounts;
  conversations: MessengerConversation[];
  calls: PulseCall[];
  loadedFromCache?: boolean;
}): ActivityInboxState {
  const notificationItems = (input.notifications || []).map(notificationToActivityItem);
  const messageItems = conversationsToActivityItems(input.conversations || []);
  const callItems = callsToActivityItems(input.calls || []);
  const items = [...callItems, ...messageItems, ...notificationItems].sort(sortActivityItems);
  return {
    items,
    counts: input.counts || {},
    unreadTotal: unreadCount(input.counts),
    categoryCounts: countUnreadByCategory(items),
    loadedFromCache: input.loadedFromCache,
    serverAuthoritative: true
  };
}

function normalizeCachedActivityInboxState(input: ActivityInboxState): ActivityInboxState {
  const items = Array.isArray(input.items) ? input.items.map(normalizeActivityItem).filter(Boolean) as ActivityInboxItem[] : [];
  return {
    ...input,
    items,
    counts: input.counts || {},
    unreadTotal: Number(input.unreadTotal || unreadCount(input.counts)),
    categoryCounts: countUnreadByCategory(items),
    serverAuthoritative: true
  };
}

function notificationToActivityItem(notification: PulseNotification): ActivityInboxItem {
  const category = classifyNotification(notification);
  return {
    id: `notification-${notification.id}`,
    source: "notification",
    category,
    title: notification.title || "PulseSoc update",
    body: notification.body || notification.message || "Open activity",
    createdAt: notification.created_at,
    unread: !notification.read,
    targetUrl: notification.target_url || notification.deep_link || "/pulse/notifications",
    notificationId: notification.id,
    rawType: String(notification.type || notification.category || ""),
    priority: String((notification.metadata?.priority as string) || "")
  };
}

function conversationsToActivityItems(conversations: MessengerConversation[]): ActivityInboxItem[] {
  return conversations
    .filter((conversation) => Number(conversation.unread_count || 0) > 0)
    .slice(0, 8)
    .map((conversation) => ({
      id: `message-${conversation.conversation_id}`,
      source: "message_summary",
      category: "messages",
      title: conversation.title || "Messenger",
      body: conversation.last_message_preview || conversation.latest_message || `${conversation.unread_count} unread message${Number(conversation.unread_count) === 1 ? "" : "s"}`,
      createdAt: conversation.last_activity_at || conversation.updated_at,
      unread: true,
      conversationId: conversation.conversation_id,
      targetUrl: `/pulse/messages/${conversation.conversation_id}`,
      rawType: "messenger_unread"
    }));
}

function callsToActivityItems(calls: PulseCall[]): ActivityInboxItem[] {
  return calls
    .filter((call) => !["ended", "declined", "missed"].includes(String(call.status || "").toLowerCase()))
    .slice(0, 4)
    .map((call) => ({
      id: `call-${call.call_id}`,
      source: "call_summary",
      category: "calls",
      title: call.call_type === "video" ? "Video call in progress" : "Voice call in progress",
      body: `Status: ${String(call.status || "active").replace(/[_-]/g, " ")}`,
      createdAt: call.created_at || call.started_at,
      unread: String(call.status || "").toLowerCase() === "ringing",
      callId: call.call_id,
      conversationId: call.conversation_id,
      targetUrl: `/pulse/calls/${call.call_id}`,
      rawType: "active_call"
    }));
}

function normalizeActivityItem(item: ActivityInboxItem): ActivityInboxItem | null {
  const category = normalizeActivityCategory(item.category);
  if (!category) return null;
  return {
    ...item,
    id: String(item.id || `${item.source}-${item.notificationId || item.conversationId || item.callId || Date.now()}`),
    category,
    title: String(item.title || "PulseSoc activity"),
    body: String(item.body || ""),
    unread: Boolean(item.unread)
  };
}

export function filterActivityItems(items: ActivityInboxItem[], category: ActivityCategory) {
  if (category === "all") return items;
  return items.filter((item) => item.category === category);
}

export function activityCategoryLabel(category: ActivityCategory) {
  return activityCategories.find((item) => item.key === category)?.label || "Activity";
}

function classifyNotification(notification: PulseNotification): Exclude<ActivityCategory, "all"> {
  const signal = [
    notification.category,
    notification.type,
    notification.title,
    notification.body,
    notification.message,
    notification.deep_link,
    notification.target_url
  ].join(" ").toLowerCase();
  if (/(message|messenger|chat|conversation|dm)/.test(signal)) return "messages";
  if (/(call|ring|voice|video)/.test(signal)) return "calls";
  if (/(safety|trust|report|appeal|strike|block|mute|scam|moderation|enforcement)/.test(signal)) return "safety";
  if (/(verification|verified|badge|identity|kyc|document)/.test(signal)) return "verification";
  if (/(marketplace|listing|seller|order|checkout|purchase|product)/.test(signal)) return "marketplace";
  if (/(creator|growth|campaign|promotion|promote|analytics|studio)/.test(signal)) return "creator_growth";
  if (/(intelligence|alert|crypto|market|forecast|signal|price)/.test(signal)) return "intelligence_alerts";
  return "social";
}

function backendNotificationCategory(category: ActivityCategory) {
  if (category === "creator_growth") return "creator";
  if (category === "intelligence_alerts") return "alerts";
  return category;
}

function normalizeActivityCategory(category: string): Exclude<ActivityCategory, "all"> | "" {
  const value = String(category || "").toLowerCase();
  if (
    value === "messages" ||
    value === "calls" ||
    value === "social" ||
    value === "safety" ||
    value === "verification" ||
    value === "marketplace" ||
    value === "creator_growth" ||
    value === "intelligence_alerts"
  ) {
    return value;
  }
  return "";
}

function countUnreadByCategory(items: ActivityInboxItem[]): Record<Exclude<ActivityCategory, "all">, number> {
  return {
    messages: countUnread(items, "messages"),
    calls: countUnread(items, "calls"),
    social: countUnread(items, "social"),
    safety: countUnread(items, "safety"),
    verification: countUnread(items, "verification"),
    marketplace: countUnread(items, "marketplace"),
    creator_growth: countUnread(items, "creator_growth"),
    intelligence_alerts: countUnread(items, "intelligence_alerts")
  };
}

function countUnread(items: ActivityInboxItem[], category: Exclude<ActivityCategory, "all">) {
  return items.filter((item) => item.category === category && item.unread).length;
}

function sortActivityItems(a: ActivityInboxItem, b: ActivityInboxItem) {
  const dateA = Date.parse(a.createdAt || "") || 0;
  const dateB = Date.parse(b.createdAt || "") || 0;
  return dateB - dateA;
}
