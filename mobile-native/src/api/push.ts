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
  installationId: string;
  endpoint: string;
  token: string;
  provider: string;
  nativeToken?: string;
  nativeTokenType?: string;
  platform: string;
  deviceType: string;
};

const PUSH_REGISTRATION_CACHE_KEY = "pulsesoc.native.push.registration";
const PUSH_INSTALLATION_ID_KEY = "pulsesoc.native.push.installation_id";
let activePushRegistration: Promise<PushRegistrationResult> | null = null;

// `handleNotification` runs only for notifications that arrive while the app is
// FOREGROUNDED. In that case PulseSoc renders its own auto-dismissing in-app
// banner (InAppNotificationBanner), so we suppress the OS heads-up banner/alert
// here to avoid stacking two banners for one notification (Issue 4). Sound, the
// notification list, and the badge are kept so the notification still lands in
// Notification Center. Background notifications are unaffected — the OS presents
// those normally.
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: false,
    shouldShowBanner: false,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true
  })
});

export async function registerPushDevice(): Promise<PushRegistrationResult> {
  if (activePushRegistration) return activePushRegistration;
  activePushRegistration = performPushRegistration(true).finally(() => {
    activePushRegistration = null;
  });
  return activePushRegistration;
}

export async function syncPushDeviceRegistration(): Promise<PushRegistrationResult> {
  if (activePushRegistration) return activePushRegistration;
  activePushRegistration = performPushRegistration(false).finally(() => {
    activePushRegistration = null;
  });
  return activePushRegistration;
}

async function performPushRegistration(requestPermission: boolean): Promise<PushRegistrationResult> {
  try {
    if (!Device.isDevice) {
      return { ok: false, message: "Push registration requires a physical device." };
    }

    const current = await Notifications.getPermissionsAsync();
    const permission = current.granted
      ? current
      : requestPermission && current.canAskAgain
        ? await Notifications.requestPermissionsAsync()
        : current;
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
    const cached = await readCachedPushRegistration();
    const installationId = cached?.installationId || await getPushInstallationId();
    if (cached?.endpoint && cached.endpoint !== token.data) {
      await revokePushEndpoint(cached.endpoint, {
        installationId,
        preservePreferences: true,
        reason: "token_refresh"
      }).catch(() => undefined);
    }
    const deviceLabel = [Device.manufacturer, Device.modelName, Device.osName, Device.osVersion].filter(Boolean).join(" ");
    const payload = {
      device_id: installationId,
      installation_id: installationId,
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
        installationId,
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
    const endpoints = Array.from(new Set([currentExpoToken, cached?.endpoint, cached?.token].filter(Boolean) as string[]));
    if (!endpoints.length) {
      await clearCachedPushRegistration();
      await Notifications.setBadgeCountAsync(0).catch(() => undefined);
      return { ok: true, message: "No native push registration was cached for this device." };
    }
    let result: PushRegistrationResult = { ok: true };
    for (const endpoint of endpoints) {
      const revoked = await revokePushEndpoint(endpoint, {
        installationId: cached?.installationId,
        preservePreferences: options.preservePreferences !== false,
        reason: options.reason || "logout",
        provider: cached?.provider,
        deviceType: cached?.deviceType,
        platform: cached?.platform
      });
      if (revoked.ok === false) result = revoked;
    }
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

async function getPushInstallationId() {
  const existing = await SecureStore.getItemAsync(PUSH_INSTALLATION_ID_KEY).catch(() => "");
  if (existing) return existing;
  const generated = `native-${Platform.OS}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  await SecureStore.setItemAsync(PUSH_INSTALLATION_ID_KEY, generated).catch(() => undefined);
  return generated;
}

async function revokePushEndpoint(endpoint: string, options: {
  installationId?: string;
  preservePreferences?: boolean;
  reason?: string;
  provider?: string;
  deviceType?: string;
  platform?: string;
}) {
  return pulseApi<PushRegistrationResult>("/api/push/unsubscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint,
      token: endpoint,
      device_id: options.installationId || undefined,
      provider: options.provider || "expo",
      device_type: options.deviceType || "native",
      platform: options.platform || Platform.OS,
      preserve_preferences: options.preservePreferences !== false,
      logout_cleanup: options.reason === "logout",
      token_refresh: options.reason === "token_refresh"
    })
  });
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
