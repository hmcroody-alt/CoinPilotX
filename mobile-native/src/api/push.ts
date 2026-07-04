import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { pulseApi } from "./pulseApi";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

export async function registerPushDevice() {
  if (!Device.isDevice) {
    return { ok: false, message: "Push registration requires a physical device." };
  }

  const current = await Notifications.getPermissionsAsync();
  const permission = current.granted ? current : await Notifications.requestPermissionsAsync();
  if (!permission.granted) return { ok: false, message: "Push permission was not granted." };

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("messages", {
      name: "Messages",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
      enableVibrate: true
    });
    await Notifications.setNotificationChannelAsync("alerts", {
      name: "Pulse Alerts",
      importance: Notifications.AndroidImportance.HIGH,
      sound: "default",
      enableVibrate: true
    });
  }

  const token = await Notifications.getExpoPushTokenAsync();
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
