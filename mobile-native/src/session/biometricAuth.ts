import * as LocalAuthentication from "expo-local-authentication";
import { Platform } from "react-native";
import {
  BiometricSession,
  clearBiometricEnrollment,
  getBiometricUserId,
  getLegacyBiometricSession,
  getSessionEnvelope,
  readBiometricCredential,
  setBiometricUserId,
  setSessionEnvelope,
  writeBiometricCredential
} from "./sessionStore";
import { restoreSession, AuthState } from "./auth";

const UNLOCK_PROMPT = "Unlock PulseSoc";

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
  return getBiometricUserId();
}

/**
 * Is Face ID armed for the account this device is (or was last) signed into?
 *
 * A display predicate, and only a display predicate — the sign-in path never
 * calls it. Probing the credential itself would raise a Face ID sheet on every
 * Settings render, so this leans on the enrollment marker, which `sessionStore`
 * keeps strictly paired with a successfully written credential.
 */
export async function isBiometricEnabledForCurrentSession(): Promise<boolean> {
  const [enabledUserId, envelope] = await Promise.all([getBiometricEnabledUserId(), getSessionEnvelope()]);
  if (!enabledUserId) return false;
  // Signed in as somebody else: this device's enrollment is not this session's.
  if (envelope?.userId && envelope.userId !== enabledUserId) return false;
  return true;
}

/**
 * Bind Face ID to `userId` by placing that user's refresh token behind the
 * keychain's biometric access control.
 *
 * Order matters and is the whole point: the credential is written first and the
 * enrollment marker only after it is confirmed, so a keychain failure can never
 * leave the app claiming Face ID is on with nothing behind it.
 */
export async function enableBiometricLoginForUser(userId: number): Promise<boolean> {
  if (!userId || userId <= 0) return false;
  const envelope = await getSessionEnvelope();
  // Face ID has to protect something. Arming it with no refresh token to bind
  // produces precisely the defect this flow exists to prevent: a settings
  // toggle reading ON and a sign-in screen that cannot honour it.
  if (!envelope?.refreshToken || envelope.userId !== userId) return false;
  const stored = await writeBiometricCredential({
    userId,
    refreshToken: envelope.refreshToken,
    refreshTokenExpiresAt: envelope.refreshTokenExpiresAt
  });
  if (!stored) return false;
  if (!(await setBiometricUserId(userId))) {
    // Marker failed: roll the credential back rather than orphan it.
    await clearBiometricEnrollment();
    return false;
  }
  return true;
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
  return enableBiometricLoginForUser(userId);
}

export async function disableBiometricLogin() {
  await clearBiometricEnrollment();
}

type BiometricPromptResult = { outcome: "success" } | { outcome: "cancelled" | "failed" | "lockout" };

async function promptForBiometrics(): Promise<BiometricPromptResult> {
  const result = await LocalAuthentication.authenticateAsync({
    promptMessage: UNLOCK_PROMPT,
    cancelLabel: "Cancel",
    disableDeviceFallback: false,
    fallbackLabel: "Use passcode"
  }).catch(() => ({ success: false, error: "unknown" as const }));
  if (result.success) return { outcome: "success" };
  const error = "error" in result ? result.error : undefined;
  if (error === "user_cancel" || error === "app_cancel" || error === "system_cancel") return { outcome: "cancelled" };
  if (error === "lockout") return { outcome: "lockout" };
  return { outcome: "failed" };
}

export async function authenticateWithBiometrics(): Promise<BiometricUnlockResult> {
  const capability = await getBiometricCapability();
  if (!capability.available) return { outcome: "not_available" };

  const enabledUserId = await getBiometricEnabledUserId();
  if (!enabledUserId) return { outcome: "no_enrolled_account" };

  const envelope = await getSessionEnvelope();
  const liveToken = envelope?.refreshToken && envelope.userId === enabledUserId ? envelope.refreshToken : "";

  // The read *is* the biometric gate: iOS holds the refresh token behind a
  // `.biometryCurrentSet` access control, raises the Face ID sheet itself, and
  // hands the token back only on a match. Nothing in JavaScript decides this,
  // so there is nothing in JavaScript to patch out.
  const read = await readBiometricCredential(UNLOCK_PROMPT);
  if (read.status === "denied") return { outcome: read.cancelled ? "cancelled" : "failed" };

  let unlocked: BiometricSession | null = read.status === "unlocked" ? read.session : null;
  if (unlocked && unlocked.userId !== enabledUserId) {
    // A credential for a different account than the one enrolled here. Refuse
    // it outright rather than trying to reconcile: the safe reading of a
    // mismatch is that this device's biometric state is no longer trustworthy.
    await disableBiometricLogin();
    return { outcome: "no_enrolled_account" };
  }

  if (!unlocked) {
    // No protected credential came back. Either this device enrolled before the
    // credential moved behind the access control, or iOS discarded the item
    // because the enrolled biometrics changed.
    const legacy = await getLegacyBiometricSession();
    const legacySession = legacy?.refreshToken && legacy.userId === enabledUserId ? legacy : null;
    if (!legacySession && !liveToken) {
      // Nothing left to unlock. Clear the enrollment so Settings and the sign-in
      // screen stop offering Face ID for a credential the OS has thrown away.
      await disableBiometricLogin();
      return { outcome: "no_enrolled_account" };
    }
    // The legacy copy is unprotected, so reading it proved nothing — run the
    // prompt ourselves before honouring it. Success here migrates the token
    // into the protected item further down, and this branch never runs again.
    const prompted = await promptForBiometrics();
    if (prompted.outcome !== "success") return prompted;
    unlocked = legacySession;
  }

  // Only now — after a verified face/finger — promote the Face-ID-gated refresh
  // token into the live envelope so restoreSession() can use it. Doing this
  // before the prompt would let a cold start silently resume without biometrics.
  if (!liveToken && unlocked) {
    await setSessionEnvelope({
      version: 1,
      userId: unlocked.userId,
      accessToken: "",
      accessTokenExpiresAt: 0,
      refreshToken: unlocked.refreshToken,
      refreshTokenExpiresAt: unlocked.refreshTokenExpiresAt
    });
  }
  if (!liveToken && !unlocked) return { outcome: "no_enrolled_account" };

  try {
    const authState = await restoreSession();
    if (authState.status !== "signedIn" || !authState.user || authState.user.user_id !== enabledUserId) {
      await disableBiometricLogin();
      return { outcome: "session_invalid" };
    }
    // The refresh above rotated the token; re-store it behind the access control
    // so the next sign-out keeps a valid one. This is also where a device that
    // came through the legacy branch finishes migrating: the write replaces the
    // unprotected v1 item with a biometrically gated one.
    const rotated = await getSessionEnvelope();
    if (rotated?.refreshToken && rotated.userId === enabledUserId) {
      await writeBiometricCredential({
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
