import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import {
  BIOMETRIC_USER_KEY,
  getBiometricSession,
  getSessionEnvelope,
  setBiometricSession,
  setSessionEnvelope
} from "./sessionStore";
import { restoreSession, AuthState } from "./auth";

const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.session" : "com.pulsesoc.app.session"
};

export type BiometricKind = "faceId" | "touchId" | "iris" | "none";

export type BiometricCapability = {
  available: boolean;
  hasHardware: boolean;
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
  if (Platform.OS === "web") return { available: false, hasHardware: false, kind: "none", reason: "unavailable" };
  const hasHardware = await LocalAuthentication.hasHardwareAsync().catch(() => false);
  // Supported types reflect the hardware sensor and are reported even before the
  // user has enrolled a face/finger, so we can label the button correctly in
  // every state (set up / enable / unlock).
  const typesRaw = await LocalAuthentication.supportedAuthenticationTypesAsync().catch(
    () => [] as LocalAuthentication.AuthenticationType[]
  );
  const types = Array.isArray(typesRaw) ? typesRaw : [];
  let kind: BiometricKind = types.includes(LocalAuthentication.AuthenticationType.FACIAL_RECOGNITION)
    ? "faceId"
    : types.includes(LocalAuthentication.AuthenticationType.FINGERPRINT)
      ? "touchId"
      : types.includes(LocalAuthentication.AuthenticationType.IRIS)
        ? "iris"
        : "none";
  // Modern iPhones expose Face ID hardware even if the type list comes back empty
  // before enrollment — default to Face ID so the affordance is never mislabeled.
  if (kind === "none" && hasHardware && Platform.OS === "ios") kind = "faceId";

  if (!hasHardware) return { available: false, hasHardware: false, kind, reason: "no_hardware" };
  const enrolled = await LocalAuthentication.isEnrolledAsync().catch(() => false);
  if (!enrolled) return { available: false, hasHardware: true, kind, reason: "not_enrolled" };
  return { available: kind !== "none", hasHardware: true, kind };
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
  const [enabledUserId, envelope, saved] = await Promise.all([
    getBiometricEnabledUserId(),
    getSessionEnvelope(),
    getBiometricSession()
  ]);
  if (!enabledUserId) return false;
  const liveMatches = Boolean(envelope?.refreshToken && envelope.userId === enabledUserId);
  const savedMatches = Boolean(saved?.refreshToken && saved.userId === enabledUserId);
  return liveMatches || savedMatches;
}

export async function enableBiometricLoginForUser(userId: number) {
  if (!userId) return;
  await SecureStore.setItemAsync(BIOMETRIC_USER_KEY, String(userId), KEYCHAIN_OPTIONS);
  // Snapshot the current refresh token so enrollment survives an ordinary sign-out.
  const envelope = await getSessionEnvelope();
  if (envelope?.refreshToken && envelope.userId === userId) {
    await setBiometricSession({
      userId,
      refreshToken: envelope.refreshToken,
      refreshTokenExpiresAt: envelope.refreshTokenExpiresAt
    });
  }
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
  await setBiometricSession(null);
}

export async function authenticateWithBiometrics(): Promise<BiometricUnlockResult> {
  const capability = await getBiometricCapability();
  if (!capability.available) return { outcome: "not_available" };

  const [enabledUserId, envelope, saved] = await Promise.all([
    getBiometricEnabledUserId(),
    getSessionEnvelope(),
    getBiometricSession()
  ]);
  const liveToken = envelope?.refreshToken && envelope.userId === enabledUserId ? envelope.refreshToken : "";
  const savedToken = saved?.refreshToken && saved.userId === enabledUserId ? saved.refreshToken : "";
  if (!enabledUserId || (!liveToken && !savedToken)) {
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

  // Only now — after a verified face/finger — promote the Face-ID-gated refresh
  // token into the live envelope so restoreSession() can use it. Doing this
  // before the prompt would let a cold start silently resume without biometrics.
  if (!liveToken && savedToken && saved) {
    await setSessionEnvelope({
      version: 1,
      userId: saved.userId,
      accessToken: "",
      accessTokenExpiresAt: 0,
      refreshToken: saved.refreshToken,
      refreshTokenExpiresAt: saved.refreshTokenExpiresAt
    });
  }

  try {
    const authState = await restoreSession();
    if (authState.status !== "signedIn" || !authState.user || authState.user.user_id !== enabledUserId) {
      await disableBiometricLogin();
      return { outcome: "session_invalid" };
    }
    // The refresh above rotated the token; re-snapshot so the next sign-out keeps a valid one.
    const rotated = await getSessionEnvelope();
    if (rotated?.refreshToken && rotated.userId === enabledUserId) {
      await setBiometricSession({
        userId: enabledUserId,
        refreshToken: rotated.refreshToken,
        refreshTokenExpiresAt: rotated.refreshTokenExpiresAt
      });
    }
    return { outcome: "success", authState };
  } catch {
    return { outcome: "session_invalid" };
  }
}
