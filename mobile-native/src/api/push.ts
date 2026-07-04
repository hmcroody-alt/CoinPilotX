import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { EXPO_PROJECT_ID } from "./config";
import { pulseApi } from "./pulseApi";

export type PushRegistrationResult = {
  ok?: boolean;
  message?: string;
  [key: string]: unknown;
};

export type PushPermissionState = {
  granted: boolean;
  canAskAgain?: boolean;
  status: string;
  device: boolean;
  message: string;
};

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

export async function registerPushDevice(): Promise<PushRegistrationResult> {
  try {
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

    const token = EXPO_PROJECT_ID
      ? await Notifications.getExpoPushTokenAsync({ projectId: EXPO_PROJECT_ID })
      : await Notifications.getExpoPushTokenAsync();
    return pulseApi<PushRegistrationResult>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint: token.data,
        provider: "expo",
        token: token.data,
        subscription: { expo_push_token: token.data },
        device_type: "native"
      })
    });
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Push registration could not be completed."
    };
  }
}

export async function getPushPermissionState(): Promise<PushPermissionState> {
  if (!Device.isDevice) {
    return {
      granted: false,
      status: "simulator",
      device: false,
      message: "Push registration requires a physical device."
    };
  }
  const permission = await Notifications.getPermissionsAsync();
  return {
    granted: Boolean(permission.granted),
    canAskAgain: permission.canAskAgain,
    status: permission.status,
    device: true,
    message: permission.granted ? "Push permission is granted." : permission.canAskAgain ? "Push permission can be requested." : "Push permission was denied by the device."
  };
}
