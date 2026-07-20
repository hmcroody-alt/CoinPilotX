import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { BIOMETRIC_USER_KEY, getSessionEnvelope } from "./sessionStore";
import { restoreSession, AuthState } from "./auth";

const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.session" : "com.pulsesoc.nativeapp.session"
};

export type BiometricKind = "faceId" | "touchId" | "iris" | "none";

export type BiometricCapability = {
  available: boolean;
  kind: BiometricKind;
  reason?: "no_hardware" | "not_enrolled" | "unavailable";
};

export type BiometricUnlockResult =
  | { outcome: "success"; authState: AuthState }
  | { outcome: "cancelled" }
  | { outcome: "failed" }
  | { outcome: "lockout" }
  | { outcome: "not_available" }
  | { outcome: "no_enrolled_account" }
  | { outcome: "session_invalid" };

export async function getBiometricCapability(): Promise<BiometricCapability> {
  if (Platform.OS === "web") return { available: false, kind: "none", reason: "unavailable" };
  const hasHardware = await LocalAuthentication.hasHardwareAsync().catch(() => false);
  if (!hasHardware) return { available: false, kind: "none", reason: "no_hardware" };
  const enrolled = await LocalAuthentication.isEnrolledAsync().catch(() => false);
  if (!enrolled) return { available: false, kind: "none", reason: "not_enrolled" };
  const types = await LocalAuthentication.supportedAuthenticationTypesAsync().catch(
    () => [] as LocalAuthentication.AuthenticationType[]
  );
  const kind: BiometricKind = types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)
    ? "faceId"
    : types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)
      ? "touchId"
      : types.includes(LocalAuthentication.AuthenticationType.IRIS)
        ? "iris"
        : "none";
  return { available: kind !== "none", kind };
}

export async function getBiometricEnabledUserId(): Promise<number | null> {
  try {
    const raw = await SecureStore.getItemAsync(BIOMETRIC_USER_KEY, KEYCHAIN_OPTIONS);
    const userId = Number(raw || 0);
    return userId > 0 ? userId : null;
  } catch {
    return null;
  }
}

export async function isBiometricEnabledForCurrentSession(): Promise<boolean> {
  const [enabledUserId, envelope] = await Promise.all([getBiometricEnabledUserId(), getSessionEnvelope()]);
  return Boolean(enabledUserId && envelope?.userId && enabledUserId === envelope.userId && envelope.refreshToken);
}

export async function enableBiometricLoginForUser(userId: number) {
  if (!userId) return;
  await SecureStore.setItemAsync(BIOMETRIC_USER_KEY, String(userId), KEYCHAIN_OPTIONS);
}

export async function confirmAndEnableBiometricLogin(userId: number): Promise<boolean> {
  if (!userId) return false;
  const capability = await getBiometricCapability();
  if (!capability.available) return false;
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Confirm to enable Face ID sign-in",
    cancelLabel: "Not now",
    disableDeviceFallback: false
  }).catch(() => ({ success: false }));
  if (!result.success) return false;
  await enableBiometricLoginForUser(userId);
  return true;
}

export async function disableBiometricLogin() {
  await SecureStore.deleteItemAsync(BIOMETRIC_USER_KEY, KEYCHAIN_OPTIONS).catch(() => undefined);
}

export async function authenticateWithBiometrics(): Promise<BiometricUnlockResult> {
  const capability = await getBiometricCapability();
  if (!capability.available) return { outcome: "not_available" };

  const [enabledUserId, envelope] = await Promise.all([getBiometricEnabledUserId(), getSessionEnvelope()]);
  if (!enabledUserId || !envelope?.refreshToken || envelope.userId !== enabledUserId) {
    return { outcome: "no_enrolled_account" };
  }

  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: "Unlock PulseSoc",
    cancelLabel: "Cancel",
    disableDeviceFallback: false,
    fallbackLabel: "Use passcode"
  }).catch(() => ({ success: false, error: "unknown" as const }));

  if (!result.success) {
    const error = "error" in result ? result.error : undefined;
    if (error === "user_cancel" || error === "app_cancel" || error === "system_cancel") return { outcome: "cancelled" };
    if (error === "lockout") return { outcome: "lockout" };
    return { outcome: "failed" };
  }

  try {
    const authState = await restoreSession();
    if (authState.status !== "signedIn" || !authState.user || authState.user.user_id !== enabledUserId) {
      await disableBiometricLogin();
      return { outcome: "session_invalid" };
    }
    return { outcome: "success", authState };
  } catch {
    return { outcome: "session_invalid" };
  }
}
