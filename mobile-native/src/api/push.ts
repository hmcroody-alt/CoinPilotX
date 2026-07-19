import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import * as SecureStore from "expo-secure-store";
import Constants from "expo-constants";
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

type CachedPushRegistration = {
  endpoint: string;
  token: string;
  provider: string;
  nativeToken?: string;
  nativeTokenType?: string;
  platform: string;
  deviceType: string;
};

const PUSH_REGISTRATION_CACHE_KEY = "pulsesoc.native.push.registration";

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
    const nativeToken = await Notifications.getDevicePushTokenAsync().catch(() => null);
    const nativeTokenType = String(nativeToken?.type || platformPushProvider());
    const nativeTokenValue = String(nativeToken?.data || "");
    const deviceLabel = [Device.manufacturer, Device.modelName, Device.osName, Device.osVersion].filter(Boolean).join(" ");
    const payload = {
      endpoint: token.data,
      provider: "expo",
      push_provider: "expo",
      native_provider: nativeTokenType,
      token: token.data,
      native_token: nativeTokenValue || undefined,
      apns_token: Platform.OS === "ios" && nativeTokenValue ? nativeTokenValue : undefined,
      fcm_token: Platform.OS === "android" && nativeTokenValue ? nativeTokenValue : undefined,
      subscription: {
        expo_push_token: token.data,
        native_device_token: nativeTokenValue || undefined,
        native_token_type: nativeTokenType,
        permission_status: permission.status
      },
      device_type: "native",
      platform: Platform.OS,
      environment: nativePushEnvironment(),
      app_version: Constants.expoConfig?.version || Constants.nativeAppVersion || "",
      device_label: deviceLabel || "PulseSoc Native device",
      permission_status: permission.status
    };
    const result = await pulseApi<PushRegistrationResult>("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    if (result.ok !== false) {
      await cachePushRegistration({
        endpoint: token.data,
        token: token.data,
        provider: "expo",
        nativeToken: nativeTokenValue || undefined,
        nativeTokenType,
        platform: Platform.OS,
        deviceType: "native"
      });
    }
    return result;
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Push registration could not be completed."
    };
  }
}

export async function unregisterPushDevice(options: { preservePreferences?: boolean; reason?: string } = {}): Promise<PushRegistrationResult> {
  try {
    const cached = await readCachedPushRegistration();
    const permission = Device.isDevice ? await Notifications.getPermissionsAsync().catch(() => null) : null;
    const currentExpoToken = Device.isDevice && permission?.granted
      ? await getCurrentExpoPushToken().catch(() => "")
      : "";
    const endpoint = currentExpoToken || cached?.endpoint || cached?.token || "";
    const nativeToken = cached?.nativeToken || "";
    if (!endpoint && !nativeToken) {
      await clearCachedPushRegistration();
      await Notifications.setBadgeCountAsync(0).catch(() => undefined);
      return { ok: true, message: "No native push registration was cached for this device." };
    }
    const result = await pulseApi<PushRegistrationResult>("/api/push/unsubscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint,
        token: endpoint,
        native_token: nativeToken || undefined,
        provider: cached?.provider || "expo",
        device_type: cached?.deviceType || "native",
        platform: cached?.platform || Platform.OS,
        preserve_preferences: options.preservePreferences !== false,
        logout_cleanup: options.reason === "logout"
      })
    });
    await clearCachedPushRegistration();
    await Notifications.setBadgeCountAsync(0).catch(() => undefined);
    return result;
  } catch (error) {
    return {
      ok: false,
      message: error instanceof Error ? error.message : "Push cleanup could not be completed."
    };
  }
}

async function getCurrentExpoPushToken() {
  const token = EXPO_PROJECT_ID
    ? await Notifications.getExpoPushTokenAsync({ projectId: EXPO_PROJECT_ID })
    : await Notifications.getExpoPushTokenAsync();
  return token.data;
}

async function cachePushRegistration(registration: CachedPushRegistration) {
  await SecureStore.setItemAsync(PUSH_REGISTRATION_CACHE_KEY, JSON.stringify(registration)).catch(() => undefined);
}

async function readCachedPushRegistration() {
  const raw = await SecureStore.getItemAsync(PUSH_REGISTRATION_CACHE_KEY).catch(() => "");
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as CachedPushRegistration;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    await clearCachedPushRegistration();
    return null;
  }
}

async function clearCachedPushRegistration() {
  await SecureStore.deleteItemAsync(PUSH_REGISTRATION_CACHE_KEY).catch(() => undefined);
}

function platformPushProvider() {
  if (Platform.OS === "ios") return "apns";
  if (Platform.OS === "android") return "fcm";
  return Platform.OS;
}

function nativePushEnvironment() {
  const ownership = Constants.appOwnership || "";
  if (__DEV__ || ownership === "expo") return "development";
  return "production";
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
