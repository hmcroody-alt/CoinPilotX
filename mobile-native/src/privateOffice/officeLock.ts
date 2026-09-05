/**
 * Private Office second lock — the native half of the server's unlock grant.
 *
 * ## What this module is, and pointedly is not
 *
 * The server decides whether the Office is open: every Office read requires a
 * valid grant minted by `POST /api/private-office/security/unlock`, checked
 * against the session and device it was minted for. This module only *carries*
 * that grant. Losing the token here locks the user out until they unlock
 * again; nothing here can open anything on its own. That asymmetry is why the
 * grant lives in plain module memory: memory is exactly as durable as an
 * unlock should be, and a token that never touches disk cannot be exfiltrated
 * from a backup or read by the next account to sign in on this device.
 *
 * ## The relock preference is a ceiling, not the lock
 *
 * "Relock after N minutes in the background" clears the token locally when the
 * app has been away long enough. The server's grant TTL keeps ticking
 * regardless, so a device that lies about elapsed time only keeps a token the
 * server will refuse anyway. Relock on logout / account switch is handled the
 * same way: the grant is bound server-side to the authenticating credential,
 * so a new session invalidates it without our help — the local clear in
 * `reconcileOwner` just stops the UI from flashing a stale "unlocked" frame.
 *
 * ## Face ID here is convenience, never authority (Stages 7–8)
 *
 * Enabling Face ID stores the *office passcode* in an expo-secure-store item
 * with `requireAuthentication`, under its own keychain service. iOS refuses to
 * return it without a live biometric match, and discards it when the enrolled
 * biometric set changes. A successful read still goes to `/unlock` like any
 * typed passcode — the server mints every grant, and a device with Face ID
 * patched out gains nothing because there is no local "unlocked" bit to flip.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "../native/secureStore";
import { Platform } from "react-native";

/** Header names — must match `services/private_office/security.py`. */
export const OFFICE_GRANT_HEADER = "X-Office-Grant";
export const OFFICE_DEVICE_HEADER = "X-Office-Device";

const DEVICE_ID_KEY = "pulsesoc.native.office.device.v1";
const RELOCK_PREF_KEY = "pulsesoc.native.office.relock.v1";
const BIOMETRIC_MARKER_KEY = "pulsesoc.native.office.biometric.userId";
const BIOMETRIC_PASSCODE_KEY = "pulsesoc.native.office.passcode.v1";

const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.office" : "com.pulsesoc.app.office"
};
// Authenticated and unauthenticated items must not share a keychain service —
// same constraint sessionStore documents for the sign-in credential.
const BIOMETRIC_KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__
    ? "com.pulsesoc.nativeapp.dev.office.biometric"
    : "com.pulsesoc.app.office.biometric",
  requireAuthentication: true
};

/** How long the Office stays unlocked after the app leaves the foreground. */
export type OfficeRelockTiming = "immediate" | "1m" | "5m" | "15m";

export const RELOCK_TIMINGS: readonly OfficeRelockTiming[] = ["immediate", "1m", "5m", "15m"];

const RELOCK_MS: Record<OfficeRelockTiming, number> = {
  immediate: 0,
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000
};

/**
 * The delay a timing actually enforces, in whole minutes.
 *
 * The picker labels itself from this rather than from a number written into the
 * catalogs, so a row can never advertise a window the lock does not honour, and
 * the copy stays translatable (and correctly pluralised and digited) in every
 * language. `immediate` is 0 and is labelled by its own word, not a count.
 */
export function relockMinutes(timing: OfficeRelockTiming): number {
  return Math.round(RELOCK_MS[timing] / 60_000);
}

type OfficeLockState = {
  unlocked: boolean;
  /** In memory only. Never serialized, never logged. */
  grantToken: string;
  /** ms epoch; 0 when locked. Mirrors the server's TTL, does not extend it. */
  expiresAt: number;
  /** The account the grant belongs to; a different signer-in relocks. */
  userId: number;
};

const LOCKED: OfficeLockState = { unlocked: false, grantToken: "", expiresAt: 0, userId: 0 };

let state: OfficeLockState = LOCKED;
let backgroundedAt = 0;
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

export function subscribeOfficeLock(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Snapshot for useSyncExternalStore — stable until the lock state changes. */
export function getOfficeLockSnapshot(): OfficeLockState {
  return state;
}

export function isOfficeUnlocked(userId?: number): boolean {
  if (!state.unlocked || !state.grantToken) return false;
  if (state.expiresAt && state.expiresAt <= Date.now()) return false;
  if (userId && state.userId && state.userId !== userId) return false;
  return true;
}

/**
 * Record a freshly minted grant. `expiresAt` arrives as the server's ISO
 * string; an unparseable one degrades to "trust the server per request", which
 * is safe because the server enforces its own copy of the expiry.
 */
export function setOfficeUnlocked(grantToken: string, expiresAtIso: string, userId: number) {
  const token = String(grantToken || "");
  if (!token) return;
  const parsed = Date.parse(String(expiresAtIso || ""));
  state = {
    unlocked: true,
    grantToken: token,
    expiresAt: Number.isFinite(parsed) ? parsed : 0,
    userId: Number(userId) > 0 ? Number(userId) : 0
  };
  notify();
}

/** Drop the grant locally. The server-side revocation is the caller's job. */
export function lockOfficeLocally() {
  if (!state.unlocked && !state.grantToken) return;
  state = LOCKED;
  notify();
}

/**
 * The current grant token, for header injection. Expiry is checked here too so
 * an expired token is never even offered — the server would refuse it, but a
 * 423 the client could have predicted is a wasted round trip.
 */
export function currentOfficeGrantToken(): string {
  return isOfficeUnlocked() ? state.grantToken : "";
}

/**
 * Headers every Office data read must carry. The device header is stable and
 * harmless on its own; the grant header only appears while unlocked.
 */
export async function officeRequestHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = {
    [OFFICE_DEVICE_HEADER]: await getOfficeDeviceId()
  };
  const token = currentOfficeGrantToken();
  if (token) headers[OFFICE_GRANT_HEADER] = token;
  return headers;
}

/* --- device binding ------------------------------------------------------ */

let cachedDeviceId = "";

function randomDeviceId(): string {
  // An opaque, per-install label the server binds grants to. It is not a
  // secret — the grant token is — so Math.random-derived entropy suffices.
  let out = "od1-";
  for (let i = 0; i < 4; i += 1) {
    out += Math.floor((1 + Math.random()) * 0x10000000).toString(16);
  }
  return out + "-" + Date.now().toString(36);
}

export async function getOfficeDeviceId(): Promise<string> {
  if (cachedDeviceId) return cachedDeviceId;
  try {
    const existing =
      Platform.OS === "web"
        ? await AsyncStorage.getItem(DEVICE_ID_KEY)
        : await SecureStore.getItemAsync(DEVICE_ID_KEY, KEYCHAIN_OPTIONS);
    if (existing) {
      cachedDeviceId = existing;
      return existing;
    }
  } catch {
    // Unreadable storage → fall through and mint a session-scoped id below.
  }
  const minted = randomDeviceId();
  cachedDeviceId = minted;
  try {
    if (Platform.OS === "web") await AsyncStorage.setItem(DEVICE_ID_KEY, minted);
    else await SecureStore.setItemAsync(DEVICE_ID_KEY, minted, KEYCHAIN_OPTIONS);
  } catch {
    // Persist failure means the id rotates next launch — grants simply expire
    // early on this device, which fails toward locked.
  }
  return minted;
}

/* --- relock lifecycle (Stage 6) ------------------------------------------ */

export async function getOfficeRelockTiming(): Promise<OfficeRelockTiming> {
  try {
    const raw = await AsyncStorage.getItem(RELOCK_PREF_KEY);
    return (RELOCK_TIMINGS as readonly string[]).includes(String(raw))
      ? (raw as OfficeRelockTiming)
      : "immediate";
  } catch {
    return "immediate";
  }
}

export async function setOfficeRelockTiming(timing: OfficeRelockTiming) {
  if (!RELOCK_TIMINGS.includes(timing)) return;
  await AsyncStorage.setItem(RELOCK_PREF_KEY, timing).catch(() => undefined);
}

/**
 * Called when the app leaves the foreground. "Immediately" locks right here so
 * the app-switcher snapshot and the next foreground both open on the lock
 * screen; the timed settings only stamp the clock.
 */
export async function noteOfficeBackgrounded() {
  backgroundedAt = Date.now();
  const timing = await getOfficeRelockTiming();
  if (timing === "immediate") lockOfficeLocally();
}

/** Called when the app returns to the foreground. */
export async function noteOfficeForegrounded() {
  if (!backgroundedAt || !state.unlocked) {
    backgroundedAt = 0;
    return;
  }
  const timing = await getOfficeRelockTiming();
  const elapsed = Date.now() - backgroundedAt;
  backgroundedAt = 0;
  if (elapsed >= RELOCK_MS[timing]) lockOfficeLocally();
}

/**
 * Relock when the signed-in account is not the one the grant belongs to
 * (logout, account switch, session invalidation). The server would refuse the
 * grant anyway — its session binding died with the old credential — so this
 * only prevents a stale "unlocked" frame from rendering first.
 */
export function reconcileOfficeOwner(currentUserId: number) {
  if (!state.unlocked) return;
  if (!currentUserId || (state.userId && state.userId !== Number(currentUserId))) {
    lockOfficeLocally();
  }
}

/* --- Face ID convenience credential (Stages 7-8) -------------------------- */

export type OfficeBiometricRead =
  | { status: "unlocked"; passcode: string }
  | { status: "missing" }
  | { status: "denied"; cancelled: boolean };

function isCancelledKeychainError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || "");
  return /cancel/i.test(message);
}

/** Display predicate only — never consulted in place of the credential. */
export async function getOfficeBiometricUserId(): Promise<number | null> {
  try {
    const raw =
      Platform.OS === "web"
        ? await AsyncStorage.getItem(BIOMETRIC_MARKER_KEY)
        : await SecureStore.getItemAsync(BIOMETRIC_MARKER_KEY, KEYCHAIN_OPTIONS);
    const userId = Number(raw || 0);
    return userId > 0 ? userId : null;
  } catch {
    return null;
  }
}

/**
 * Arm Face ID for the Office: passcode goes behind the biometric access
 * control first, marker second, and a marker failure rolls the credential
 * back — the same write ordering `biometricAuth.ts` uses for sign-in, for the
 * same reason: settings must never read ON with nothing behind it.
 */
export async function enableOfficeBiometric(userId: number, passcode: string): Promise<boolean> {
  if (Platform.OS === "web" || !userId || userId <= 0 || !passcode) return false;
  try {
    // Delete-then-add: updating an access-controlled item prompts a second
    // Face ID sheet; adding a fresh one does not.
    await SecureStore.deleteItemAsync(BIOMETRIC_PASSCODE_KEY, BIOMETRIC_KEYCHAIN_OPTIONS).catch(
      () => undefined
    );
    await SecureStore.setItemAsync(
      BIOMETRIC_PASSCODE_KEY,
      JSON.stringify({ userId, passcode }),
      BIOMETRIC_KEYCHAIN_OPTIONS
    );
  } catch {
    return false;
  }
  try {
    await SecureStore.setItemAsync(BIOMETRIC_MARKER_KEY, String(userId), KEYCHAIN_OPTIONS);
  } catch {
    await disableOfficeBiometric();
    return false;
  }
  return true;
}

export async function disableOfficeBiometric() {
  if (Platform.OS === "web") {
    await AsyncStorage.removeItem(BIOMETRIC_MARKER_KEY).catch(() => undefined);
    return;
  }
  await SecureStore.deleteItemAsync(BIOMETRIC_PASSCODE_KEY, BIOMETRIC_KEYCHAIN_OPTIONS).catch(
    () => undefined
  );
  await SecureStore.deleteItemAsync(BIOMETRIC_MARKER_KEY, KEYCHAIN_OPTIONS).catch(() => undefined);
}

/**
 * Read the passcode behind Face ID. The read *is* the biometric gate — iOS
 * raises the sheet and refuses without a match. The caller still submits the
 * passcode to `/unlock`; nothing here unlocks anything by itself.
 */
export async function readOfficeBiometricPasscode(
  userId: number,
  authenticationPrompt: string
): Promise<OfficeBiometricRead> {
  if (Platform.OS === "web") return { status: "missing" };
  let raw: string | null = null;
  try {
    raw = await SecureStore.getItemAsync(BIOMETRIC_PASSCODE_KEY, {
      ...BIOMETRIC_KEYCHAIN_OPTIONS,
      authenticationPrompt
    });
  } catch (error) {
    return { status: "denied", cancelled: isCancelledKeychainError(error) };
  }
  if (!raw) return { status: "missing" };
  try {
    const value = JSON.parse(raw) as { userId?: number; passcode?: string };
    if (!value.passcode || Number(value.userId || 0) !== Number(userId)) {
      // Credential for someone else — the safe reading is that this device's
      // office biometric state is stale. Clear it rather than reconcile.
      await disableOfficeBiometric();
      return { status: "missing" };
    }
    return { status: "unlocked", passcode: String(value.passcode) };
  } catch {
    return { status: "missing" };
  }
}

/** Test hook: reset in-memory state without touching persisted preferences. */
export function __resetOfficeLockForTests() {
  state = LOCKED;
  backgroundedAt = 0;
  cachedDeviceId = "";
}
