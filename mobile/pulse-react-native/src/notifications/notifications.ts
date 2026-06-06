import * as Notifications from "expo-notifications";
import * as Linking from "expo-linking";
import { pulseApi } from "../api/client";

export async function registerPushToken() {
  const permission = await Notifications.requestPermissionsAsync();
  if (!permission.granted) return { ok: false, message: "Push permission not granted." };
  const token = await Notifications.getExpoPushTokenAsync();
  return pulseApi("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: token.data,
      provider: "expo",
      token: token.data,
      subscription: { expo_push_token: token.data },
      device_type: "native",
    }),
  });
}

export function bindNotificationRouting() {
  return Notifications.addNotificationResponseReceivedListener(response => {
    const url = response.notification.request.content.data?.url;
    if (typeof url === "string" && url) Linking.openURL(url);
  });
}
