import * as Device from "expo-device";
import * as Linking from "expo-linking";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { pulseApi } from "./apiClient";
import { EXPO_PROJECT_ID } from "./config";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

export async function registerForPushNotifications() {
  if (!Device.isDevice) {
    return { ok: false, message: "Push registration requires a physical device." };
  }

  const current = await Notifications.getPermissionsAsync();
  const permission = current.granted ? current : await Notifications.requestPermissionsAsync();
  if (!permission.granted) {
    return { ok: false, message: "Push permission was not granted." };
  }

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("default", {
      name: "Pulse",
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
  }

  const token = await Notifications.getExpoPushTokenAsync(EXPO_PROJECT_ID ? { projectId: EXPO_PROJECT_ID } : undefined);
  return pulseApi("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: token.data,
      provider: "expo",
      token: token.data,
      subscription: { expo_push_token: token.data },
      device_type: "native"
    })
  });
}

export function wireNotificationLinks() {
  return Notifications.addNotificationResponseReceivedListener(response => {
    const data = response.notification.request.content.data || {};
    const conversationId = data.conversationId || data.conversation_id;
    const url = data.deepLink || data.deep_link || data.url || (conversationId ? `/pulse/messages/${conversationId}` : "");
    if (typeof url === "string" && url.length > 0) {
      Linking.openURL(url).catch(() => undefined);
    }
  });
}
