import { PULSE_API_BASE_URL } from "../api/config";
import { envFlagOn } from "../core/envFlag";

const QA_TEMP_USERNAME_PREFIX = "nativeqa_";
const QA_TEMP_DISPLAY_NAME = "PulseSoc Native QA";

export function isTemporaryQaUser(user: unknown) {
  const input = (user || {}) as Record<string, unknown>;
  const username = String(input.username || "").trim().toLowerCase();
  const displayName = String(input.display_name || input.full_name || "").trim();
  return username.startsWith(QA_TEMP_USERNAME_PREFIX) || displayName === QA_TEMP_DISPLAY_NAME;
}

/**
 * Whether this build may sign itself in as a throwaway QA account.
 *
 * Four conditions, and the shape of them matters more than any one term.
 *
 * The first is the fence: the API base URL has to be a loopback address. Two
 * opt-in flags follow, both off unless somebody sets them. The last term is the
 * only inverted flag in the app — `DISABLE_TEMP_QA_ACCOUNT` is a kill switch, so
 * its permissive side is the default and it can only ever subtract from a gate
 * the three preceding terms have already had to open. That is what keeps a
 * default build safe: not this flag, but the localhost check in front of it.
 *
 * All three now read the shared set in `core/envFlag.ts`, including the
 * inverted one — "off" has to mean the same thing on a kill switch as it does
 * on an opt-in, or the switch fails open for whoever spells it `true`.
 */
export function canUseTemporaryQaAccount() {
  return (
    isLocalApiBaseUrl(PULSE_API_BASE_URL) &&
    envFlagOn("EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN") &&
    envFlagOn("EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT") &&
    !envFlagOn("EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT")
  );
}

export function shouldRejectTemporaryQaUser(user: unknown) {
  return isTemporaryQaUser(user) && !canUseTemporaryQaAccount();
}

export function isLocalApiBaseUrl(value: string) {
  try {
    const parsed = new URL(value);
    return ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
  } catch {
    return false;
  }
}
