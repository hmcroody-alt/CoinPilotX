import * as Device from "expo-device";
import * as Linking from "expo-linking";
import * as Notifications from "expo-notifications";
import { Platform, Vibration } from "react-native";
import { pulseApi } from "./apiClient";
import { EXPO_PROJECT_ID } from "./config";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

const ANDROID_CHANNEL_ID = "default";
const NOTIFICATION_VIBRATION_PATTERN = [0, 250, 120, 250];

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
      device_type: "native"
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
    return { ok: false, message: "Push permission was not granted." };
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
      device_type: "native"
    })
  });
}

export function wireNotificationLinks(onUrl?: (url: string) => void) {
  return Notifications.addNotificationResponseReceivedListener(response => {
    const url = response.notification.request.content.data?.url;
    if (typeof url === "string" && url.length > 0) {
      if (onUrl) onUrl(url);
      else Linking.openURL(url).catch(() => undefined);
    }
  });
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
  const url = typeof payload.url === "string" && payload.url.trim() ? payload.url : "/pulse/notifications";
  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
      data: { url },
      sound: "default",
      priority: Notifications.AndroidNotificationPriority.HIGH
    },
    trigger: null
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
