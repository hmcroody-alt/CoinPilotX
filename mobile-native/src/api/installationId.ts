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
export async function getPushInstallationId() {
  const existing = await SecureStore.getItemAsync(PUSH_INSTALLATION_ID_KEY).catch(() => "");
  if (existing) return existing;
  const generated = `native-${Platform.OS}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  await SecureStore.setItemAsync(PUSH_INSTALLATION_ID_KEY, generated).catch(() => undefined);
  return generated;
}
