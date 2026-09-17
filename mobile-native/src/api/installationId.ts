import * as SecureStore from "../native/secureStore";
import { Platform } from "react-native";

const PUSH_INSTALLATION_ID_KEY = "pulsesoc.native.push.installation_id";

/**
 * The stable per-install device id every push registration on this device files under.
 *
 * This lives in its own module, rather than in `push.ts` where it used to, because the
 * VoIP/CallKit path needs it and `push.ts` calls `Notifications.setNotificationHandler`
 * at module scope. Importing that side effect into `api/calls.ts` — which nearly every
 * screen pulls in — would install the foreground-notification handler far earlier and in
 * far more contexts than it is meant to run. The id itself only needs SecureStore.
 *
 * Both registrations have to use *this* id and no other. The backend suppresses the
 * ordinary incoming-call alert push for exactly those device ids that have an active VoIP
 * token, so the two registrations are joined on this string and nothing else. A VoIP
 * registration under a different id would not fail — it would ring correctly through
 * CallKit and *also* deliver the alert banner, which is the duplicate-ring outcome the
 * whole suppression path exists to prevent.
 *
 * Reads through `native/secureStore` rather than naming `expo-secure-store` directly: the
 * Phase 46 ownership guard requires exactly one module in `src/` to import it, and the key
 * read here is the same one `api/push` already writes under, so there is no new keychain
 * service and no new security policy — only a second reader of an existing item.
 */
/**
 * Shared by concurrent callers so that a read-then-write cannot interleave.
 *
 * `push.ts` (alert registration) and `calls.ts` (VoIP registration) both call this
 * during start-up, and neither awaits the other. Without this, both `await` the read,
 * both see nothing stored, and both mint an id — so the two registrations land under
 * *different* device ids. That is precisely the split the module docstring above warns
 * about: nothing errors, CallKit rings, and the alert banner is no longer suppressed
 * because it is filed under an id with no VoIP token. Measured on device, three ids
 * were minted within about 2 ms.
 */
let inFlight: Promise<string> | null = null;

/**
 * The id has to survive being read on a *locked* phone, so it cannot use the default.
 *
 * `expo-secure-store` defaults to `kSecAttrAccessibleWhenUnlocked`
 * (`SecureStoreOptions.swift`: `keychainAccessible: SecureStoreAccessible = .whenUnlocked`),
 * which makes the item unreadable while the screen is locked. Every moment this id is
 * actually needed is a locked moment: a VoIP push arrives, CallKit rings on the lock
 * screen, the user answers without unlocking, and the accept request has to name the
 * device that answered. `session/sessionStore` already reached the same conclusion for
 * the access token — which is why an authenticated `/accept` works from the lock screen
 * at all — and this id is strictly less sensitive than that token.
 *
 * `THIS_DEVICE_ONLY` because a device id that synced to another device through the
 * iCloud keychain would name the wrong phone, which is the exact confusion it exists to
 * prevent.
 */
const KEYCHAIN_OPTIONS = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY
} as const;

/** Whether this process has already rewritten the stored id under `KEYCHAIN_OPTIONS`. */
let upgraded = false;

/**
 * Rewrite an id stored under the old default so later locked reads can see it.
 *
 * Installs that predate `KEYCHAIN_OPTIONS` hold the id under `whenUnlocked`, and there is
 * no keychain API to ask which accessibility an item has — so the only way to move it is
 * to write it again. Done once per process, after a read that succeeded, which means it
 * runs while the device is unlocked and cannot lose the value it is preserving.
 */
async function upgradeAccessibility(value: string) {
  if (upgraded) return;
  upgraded = true;
  await SecureStore.setItemAsync(PUSH_INSTALLATION_ID_KEY, value, KEYCHAIN_OPTIONS).catch(() => {
    // Leave it un-upgraded so the next call tries again rather than giving up for the
    // life of the process.
    upgraded = false;
  });
}

export async function getPushInstallationId() {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    let existing: string | null = null;
    try {
      existing = await SecureStore.getItemAsync(PUSH_INSTALLATION_ID_KEY);
    } catch {
      // "Absent" and "unreadable" are different facts and only one of them means mint.
      // `searchKeyChain` returns null for `errSecItemNotFound` and *throws* for anything
      // else, so a throw here is the keychain refusing — on iOS, overwhelmingly a locked
      // device. Minting on a refusal would hand out an id that matches no registration,
      // and — now that the item is writable while locked — would then overwrite the real
      // one. Returning empty says "not known right now", which every caller can handle.
      return "";
    }
    if (existing) {
      await upgradeAccessibility(existing);
      return existing;
    }
    const generated = `native-${Platform.OS}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
    await SecureStore.setItemAsync(PUSH_INSTALLATION_ID_KEY, generated, KEYCHAIN_OPTIONS).catch(
      () => undefined
    );
    upgraded = true;
    return generated;
  })();
  try {
    return await inFlight;
  } finally {
    // Deliberately not a process-lifetime memo. SecureStore stays the source of truth
    // because a read can fail transiently while the device is *locked* — and a locked
    // device is exactly when a VoIP push arrives. Caching that failure would pin a
    // freshly minted id for the rest of the process and permanently diverge from the
    // stored one; re-reading lets the next call recover the real id instead.
    inFlight = null;
  }
}
