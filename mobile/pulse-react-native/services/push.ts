import * as Device from "expo-device";
import * as Linking from "expo-linking";
import * as Notifications from "expo-notifications";
import { Platform, Vibration } from "react-native";
import { pulseApi } from "./apiClient";
import { EXPO_PROJECT_ID } from "./config";

Notifications.setNotificationHandler({
  handleNotification: async notification => {
    const data = notification.request.content.data || {};
    const incomingConversationId = notificationConversationId(data);
    const isActiveConversation = Boolean(incomingConversationId && incomingConversationId === activeConversationId);
    return {
      shouldShowAlert: !isActiveConversation,
      shouldShowBanner: !isActiveConversation,
      shouldShowList: true,
      shouldPlaySound: !isActiveConversation,
      shouldSetBadge: true
    };
  }
});

const ANDROID_CHANNEL_ID = "default";
const ANDROID_MESSAGES_CHANNEL_ID = "pulse-messages-v2";
const ANDROID_LEGACY_MESSAGES_CHANNEL_ID = "messages";
const NOTIFICATION_VIBRATION_PATTERN = [0, 250, 120, 250];
let activeConversationId = "";

export async function registerForPushNotifications() {
  const token = await getNativePushToken();
  if (!token.ok) return token;
  return pulseApi("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: token.token,
      provider: "expo",
      token: token.token,
      subscription: { expo_push_token: token.token },
      device_type: "native",
      platform: Platform.OS,
      app_version: "1.0.0"
    })
  });
}

export async function getNativePushToken(): Promise<{ ok: true; token: string } | { ok: false; message: string }> {
  if (!Device.isDevice) {
    return { ok: false, message: "Push registration requires a physical device." };
  }

  const current = await Notifications.getPermissionsAsync();
  const permission = hasNotificationPermission(current) ? current : await Notifications.requestPermissionsAsync();
  if (!hasNotificationPermission(permission)) {
    return { ok: false, message: "Notifications are off. Open phone Settings, choose PulseSoc, then allow notifications, sound, and vibration." };
  }

  await ensureNotificationPresentation();

  const token = await Notifications.getExpoPushTokenAsync(EXPO_PROJECT_ID ? { projectId: EXPO_PROJECT_ID } : undefined);
  return { ok: true, token: token.data };
}

export async function ensureNotificationPresentation() {
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
      name: "PulseSoc",
      importance: Notifications.AndroidImportance.MAX,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: NOTIFICATION_VIBRATION_PATTERN,
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
      bypassDnd: false
    });
    await Notifications.setNotificationChannelAsync(ANDROID_MESSAGES_CHANNEL_ID, {
      name: "Messages",
      importance: Notifications.AndroidImportance.MAX,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: NOTIFICATION_VIBRATION_PATTERN,
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
      bypassDnd: false
    });
    await Notifications.setNotificationChannelAsync(ANDROID_LEGACY_MESSAGES_CHANNEL_ID, {
      name: "Messages",
      importance: Notifications.AndroidImportance.MAX,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: NOTIFICATION_VIBRATION_PATTERN,
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
      bypassDnd: false
    });
  }
}

export async function unregisterPushNotifications() {
  if (!Device.isDevice) {
    return { ok: false, message: "Push unregister requires a physical device." };
  }

  const token = await Notifications.getExpoPushTokenAsync(EXPO_PROJECT_ID ? { projectId: EXPO_PROJECT_ID } : undefined);
  return pulseApi("/api/push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: token.data,
      provider: "expo",
      token: token.data,
      device_type: "native",
      platform: Platform.OS,
      app_version: "1.0.0"
    })
  });
}

export function wireNotificationLinks(onUrl?: (url: string) => void) {
  return Notifications.addNotificationResponseReceivedListener(response => {
    const url = notificationUrlFromData(response.notification.request.content.data || {});
    if (typeof url === "string" && url.length > 0) {
      if (onUrl) onUrl(url);
      else Linking.openURL(url).catch(() => undefined);
    }
  });
}

export async function getInitialNotificationUrl() {
  const response = await Notifications.getLastNotificationResponseAsync();
  if (!response) return "";
  return notificationUrlFromData(response.notification.request.content.data || {});
}

export function setActiveConversationFromUrl(url: string) {
  activeConversationId = conversationIdFromUrl(url);
}

export function wireNotificationPresentation() {
  ensureNotificationPresentation().catch(() => undefined);
  return Notifications.addNotificationReceivedListener(() => {
    vibrateForNotification();
  });
}

export async function presentNativeDeviceAlert(payload: Record<string, unknown>) {
  await ensureNotificationPresentation().catch(() => undefined);
  vibrateForNotification();
  const title = typeof payload.title === "string" && payload.title.trim() ? payload.title : "PulseSoc";
  const body = typeof payload.body === "string" && payload.body.trim() ? payload.body : "New PulseSoc activity.";
  const data = normalizeNotificationData(payload);
  const url = typeof data.url === "string" && data.url.trim() ? data.url : "/pulse/notifications";
  const channelId = data.conversationId ? ANDROID_MESSAGES_CHANNEL_ID : ANDROID_CHANNEL_ID;
  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
      data,
      sound: "default",
      priority: Notifications.AndroidNotificationPriority.HIGH
    },
    trigger: Platform.OS === "android" ? { channelId, seconds: 1 } : null
  });
}

function vibrateForNotification() {
  if (Platform.OS === "ios") {
    Vibration.vibrate([0, 350]);
    return;
  }
  Vibration.vibrate(NOTIFICATION_VIBRATION_PATTERN);
}

function hasNotificationPermission(permission: unknown) {
  const value = permission as { granted?: boolean; status?: string };
  return value.granted === true || value.status === "granted";
}

function normalizeNotificationData(payload: Record<string, unknown>) {
  const conversationId = stringValue(payload.conversationId || payload.conversation_id);
  const messageId = stringValue(payload.messageId || payload.message_id);
  const senderId = stringValue(payload.senderId || payload.sender_id);
  const preferredUrl =
    stringValue(payload.native_url || payload.app_url || payload.mobile_deep_link || payload.deepLink || payload.deep_link || payload.url || payload.target_url) ||
    (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/notifications");
  const url = normalizeNotificationUrl(preferredUrl, conversationId);
  return {
    ...payload,
    url,
    deepLink: url,
    native_url: stringValue(payload.native_url || payload.app_url || payload.mobile_deep_link) || url,
    web_url: stringValue(payload.web_url || payload.url || payload.target_url) || (conversationId ? `/pulse/messages/${conversationId}` : "/pulse/notifications"),
    type: stringValue(payload.type) || (conversationId ? "message" : "notification"),
    conversationId,
    conversation_id: conversationId,
    messageId,
    message_id: messageId,
    senderId,
    sender_id: senderId
  };
}

function notificationUrlFromData(data: Record<string, unknown>) {
  const normalized = normalizeNotificationData(data);
  return stringValue(normalized.url);
}

function notificationConversationId(data: Record<string, unknown>) {
  return stringValue(data.conversationId || data.conversation_id) || conversationIdFromUrl(stringValue(data.url || data.deepLink || data.native_url || data.mobile_deep_link));
}

function conversationIdFromUrl(url: string) {
  const raw = stringValue(url);
  if (!raw) return "";
  const match = raw.match(/(?:^|\/)(?:pulse\/)?messages\/(\d+)/) || raw.match(/[?&](?:conversation|conversation_id|conversationId)=(\d+)/);
  return match ? match[1] : "";
}

function normalizeNotificationUrl(url: string, conversationId = "") {
  const raw = stringValue(url);
  const id = conversationId || conversationIdFromUrl(raw);
  if (!raw && id) return `pulse://pulse/messages-v2?conversation=${id}`;
  if (/^pulse:\/\/messages\/\d+/i.test(raw) && id) return `pulse://pulse/messages-v2?conversation=${id}`;
  if (/^https?:\/\/(?:www\.)?pulsesoc\.com\/pulse\/messages\/\d+/i.test(raw) && id) return `pulse://pulse/messages-v2?conversation=${id}`;
  if (/^\/pulse\/messages\/\d+/i.test(raw) && id) return `pulse://pulse/messages-v2?conversation=${id}`;
  const liveMatch = raw.match(/(?:^https?:\/\/(?:www\.)?pulsesoc\.com)?\/pulse\/live\/(\d+)/i);
  if (liveMatch) return `pulse://live/${liveMatch[1]}`;
  if (/\/pulse\/live\/studio/i.test(raw)) return "pulse://pulse/live/studio";
  const alertMatch = raw.match(/(?:^https?:\/\/(?:www\.)?pulsesoc\.com)?\/pulse\/alerts\/(\d+)/i);
  if (alertMatch) return `pulse://alerts/${alertMatch[1]}`;
  const purchaseMatch = raw.match(/(?:^https?:\/\/(?:www\.)?pulsesoc\.com)?\/pulse\/(?:purchases|orders)\/([\w-]+)/i);
  if (purchaseMatch) return `pulse://purchase/${purchaseMatch[1]}`;
  if (/\/account\/security/i.test(raw)) return "pulse://account/security";
  return raw || "/pulse/notifications";
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value.trim() : value === undefined || value === null ? "" : String(value).trim();
}
