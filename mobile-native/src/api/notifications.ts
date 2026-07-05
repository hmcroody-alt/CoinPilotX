import { pulseApi } from "./pulseApi";

export type PulseNotification = {
  id: number;
  type?: string;
  category?: string;
  title?: string;
  body?: string;
  message?: string;
  created_at?: string;
  read_at?: string;
  read?: boolean;
  deep_link?: string;
  target_url?: string;
  metadata?: Record<string, unknown>;
};

export type NotificationListResponse = {
  ok?: boolean;
  notifications?: PulseNotification[];
  items?: PulseNotification[];
  badge_counts?: NotificationBadgeCounts;
};

export type NotificationBadgeCounts = {
  ok?: boolean;
  count?: number;
  unread_count?: number;
  alert_unread_count?: number;
  chat_unread_count?: number;
  total_unread_count?: number;
};

export type NotificationPreferences = {
  [category: string]: {
    in_app?: boolean;
    push?: boolean;
    email?: boolean;
    sms?: boolean;
  };
};

export type NotificationPreferencesResponse = {
  ok?: boolean;
  preferences?: NotificationPreferences;
  experience?: Record<string, unknown>;
  push_status?: Record<string, unknown>;
};

export async function listNotifications(params: { limit?: number; filter?: string; unreadOnly?: boolean } = {}) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit || 50));
  if (params.filter && params.filter !== "all") query.set("filter", params.filter);
  if (params.unreadOnly) query.set("unread", "1");
  const data = await pulseApi<NotificationListResponse>(`/api/pulse/notifications?${query.toString()}`);
  return {
    ...data,
    notifications: normalizeNotifications(data.notifications || data.items || [])
  };
}

export async function getNotificationBadgeCounts() {
  return pulseApi<NotificationBadgeCounts>("/api/pulse/notifications/unread-count");
}

export async function markNotificationRead(notificationId: number) {
  return pulseApi<NotificationBadgeCounts & { ok?: boolean; badge_counts?: NotificationBadgeCounts }>(
    `/api/pulse/notifications/${notificationId}/read`,
    { method: "POST", body: JSON.stringify({ notification_id: notificationId }) }
  );
}

export async function markAllNotificationsRead(category?: string) {
  const query = category ? `?category=${encodeURIComponent(category)}` : "";
  return pulseApi<NotificationBadgeCounts & { ok?: boolean; badge_counts?: NotificationBadgeCounts }>(`/api/pulse/notifications/read-all${query}`, {
    method: "POST",
    body: JSON.stringify(category ? { category } : {})
  });
}

export async function deleteNotification(notificationId: number) {
  return pulseApi<NotificationBadgeCounts & { ok?: boolean; badge_counts?: NotificationBadgeCounts }>(
    `/api/pulse/notifications/${notificationId}`,
    { method: "DELETE", body: JSON.stringify({ notification_id: notificationId }) }
  );
}

export async function resolveNotificationTarget(notificationId: number) {
  return pulseApi<{ ok?: boolean; target_url?: string; fallback_used?: boolean; badge_counts?: NotificationBadgeCounts }>(
    `/api/pulse/notifications/${notificationId}/resolve`,
    { method: "POST", body: JSON.stringify({ notification_id: notificationId, mark_read: true }) }
  );
}

export async function getNotificationPreferences() {
  return pulseApi<NotificationPreferencesResponse>("/api/pulse/notifications/preferences");
}

export async function updateNotificationPreferences(preferences: NotificationPreferences) {
  return pulseApi<NotificationPreferencesResponse>("/api/pulse/notifications/preferences", {
    method: "PATCH",
    body: JSON.stringify({ preferences })
  });
}

export async function getNotificationExperience() {
  return pulseApi<NotificationPreferencesResponse>("/api/notification-preferences");
}

export async function updateNotificationExperience(experience: Record<string, unknown>) {
  return pulseApi<NotificationPreferencesResponse>("/api/notification-preferences", {
    method: "POST",
    body: JSON.stringify({ experience })
  });
}

export function unreadCount(counts?: NotificationBadgeCounts) {
  return Number(counts?.alert_unread_count ?? counts?.unread_count ?? counts?.count ?? 0);
}

export function normalizeNotifications(items: PulseNotification[]) {
  return items
    .map((item) => ({
      ...item,
      id: Number(item.id || 0),
      title: item.title || "PulseSoc notification",
      body: item.body || item.message || "",
      read: Boolean(item.read || item.read_at)
    }))
    .filter((item) => item.id > 0);
}
