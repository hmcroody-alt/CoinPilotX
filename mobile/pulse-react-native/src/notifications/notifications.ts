import * as Device from "expo-device";
import * as Linking from "expo-linking";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { pulseApi } from "../api/client";
import { EXPO_PROJECT_ID } from "../config";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

export async function registerPushToken() {
  if (!Device.isDevice) {
    return { ok: false, message: "Push registration requires a physical device." };
  }
  const current = await Notifications.getPermissionsAsync();
  const permission = hasNotificationPermission(current) ? current : await Notifications.requestPermissionsAsync();
  if (!hasNotificationPermission(permission)) {
    return { ok: false, message: "Push permission was not granted." };
  }
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "PulseSoc",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: [0, 250, 250, 250]
    });
    await Notifications.setNotificationChannelAsync("messages", {
      name: "Messages",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: [0, 250, 250, 250],
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC
    });
    await Notifications.setNotificationChannelAsync("pulse-messages-v2", {
      name: "Messages",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
      enableVibrate: true,
      vibrationPattern: [0, 250, 250, 250],
      lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC
    });
  }
  const token = await Notifications.getExpoPushTokenAsync(EXPO_PROJECT_ID ? { projectId: EXPO_PROJECT_ID } : undefined);
  return pulseApi("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: token.data,
      token: token.data,
      provider: "expo",
      device_type: "native",
      subscription: { expo_push_token: token.data }
    })
  });
}

export function bindNotificationRouting() {
  return Notifications.addNotificationResponseReceivedListener(response => {
    const data = response.notification.request.content.data || {};
    const conversationId = data.conversationId || data.conversation_id;
    const url = data.native_url || data.app_url || data.mobile_deep_link || data.deepLink || data.deep_link || data.url || (conversationId ? `pulse://pulse/messages-v2?conversation=${conversationId}` : "");
    if (typeof url === "string" && url.length > 0) {
      Linking.openURL(url).catch(() => undefined);
    }
  });
}

function hasNotificationPermission(permission: unknown) {
  const value = permission as { granted?: boolean; status?: string };
  return value.granted === true || value.status === "granted";
}
