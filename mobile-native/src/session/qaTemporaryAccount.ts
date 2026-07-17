import { PULSE_API_BASE_URL } from "../api/config";

const QA_TEMP_USERNAME_PREFIX = "nativeqa_";
const QA_TEMP_DISPLAY_NAME = "PulseSoc Native QA";

export function isTemporaryQaUser(user: unknown) {
  const input = (user || {}) as Record<string, unknown>;
  const username = String(input.username || "").trim().toLowerCase();
  const displayName = String(input.display_name || input.full_name || "").trim();
  return username.startsWith(QA_TEMP_USERNAME_PREFIX) || displayName === QA_TEMP_DISPLAY_NAME;
}

export function canUseTemporaryQaAccount() {
  return (
    isLocalApiBaseUrl(PULSE_API_BASE_URL) &&
    process.env.EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN === "1" &&
    process.env.EXPO_PUBLIC_PULSESOC_QA_ALLOW_TEMP_ACCOUNT === "1" &&
    process.env.EXPO_PUBLIC_PULSESOC_DISABLE_TEMP_QA_ACCOUNT !== "1"
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
