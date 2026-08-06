import { Platform } from "react-native";

/**
 * In-memory holder for the engineer-access grant.
 *
 * Deliberately NOT backed by AsyncStorage, SecureStore, Zustand, navigation
 * params, or any telemetry sink. The grant lives for exactly as long as the JS
 * runtime does, which is the mission's "current authenticated app session"
 * policy expressed as a storage choice rather than as an expiry check we could
 * forget to run. Killing the app clears it; there is no persisted copy to leak.
 *
 * The raw passcode never reaches this module. It exists only as a local inside
 * the modal's submit handler and is dropped before the response is handled.
 */

export type EngineerGrant = {
  token: string;
  expiresAt: number;
  scope: string[];
};

let grant: EngineerGrant | null = null;
let grantUserId = 0;
let deviceId = "";
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

export function subscribeToEngineerAccess(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Per-launch identifier used to bind the grant and to salt the server's audit
 * hash. Regenerated every cold start on purpose — it must not become a stable
 * cross-session tracking identifier.
 */
export function engineerAccessDeviceId(): string {
  if (!deviceId) {
    const random = Math.random().toString(36).slice(2, 10);
    deviceId = `native-${Platform.OS}-${Date.now().toString(36)}-${random}`;
  }
  return deviceId;
}

/** True only while a non-expired grant is held for this exact account. */
export function hasEngineerAccess(userId?: number | null): boolean {
  if (!grant) return false;
  if (grant.expiresAt * 1000 <= Date.now()) {
    // Expiry is enforced on read as well as by the server, so a grant that
    // lapses while the app sits in the background is already gone by the time
    // the next screen asks.
    clearEngineerAccess();
    return false;
  }
  if (userId != null && Number(userId) !== grantUserId) return false;
  return true;
}

export function engineerAccessToken(): string {
  return hasEngineerAccess() ? grant!.token : "";
}

export function engineerAccessScope(): string[] {
  return hasEngineerAccess() ? [...grant!.scope] : [];
}

export function setEngineerAccess(userId: number, next: EngineerGrant) {
  grant = { token: next.token, expiresAt: Number(next.expiresAt || 0), scope: [...(next.scope || [])] };
  grantUserId = Number(userId || 0);
  notify();
}

/** Called on sign-out, account switch, session expiry, and server revocation. */
export function clearEngineerAccess() {
  if (!grant && !grantUserId) return;
  grant = null;
  grantUserId = 0;
  notify();
}

/**
 * Drop the grant when the authenticated identity changes. Account switching is
 * the case a boolean "isEngineer" flag would get wrong: the flag would survive
 * the switch and hand the new account the previous account's access.
 */
export function reconcileEngineerAccessOwner(userId?: number | null) {
  if (!grant) return;
  if (Number(userId || 0) !== grantUserId) clearEngineerAccess();
}
