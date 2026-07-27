import Constants from "expo-constants";

const extra = Constants.expoConfig?.extra || {};
const easConfig = Constants.easConfig || {};

const configuredBaseUrl =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  (typeof extra.pulseApiBaseUrl === "string" ? extra.pulseApiBaseUrl : "https://pulsesoc.com");

export const PULSE_API_BASE_URL = normalizeApiBaseUrl(configuredBaseUrl);
// Digital purchases (Premium checkout/billing, marketplace checkout, payout onboarding)
// currently route to external web/Stripe. Apple Guideline 3.1.1 requires StoreKit for
// in-app digital goods, which is not yet implemented, so these entry points are hidden
// unless a build explicitly opts in. Default OFF for App Store / production builds.
export const DIGITAL_COMMERCE_ENABLED = process.env.EXPO_PUBLIC_DIGITAL_COMMERCE_ENABLED === "1";
// Native CallKit + PushKit VoIP (rings the iOS system call UI when the app is
// backgrounded/killed). Requires react-native-callkeep + react-native-voip-push-notification
// pods, the `voip` background mode, and a VoIP push certificate under the COINPLOTXAI APNs
// account (see reports/native_callkit_voip_integration.md). Default OFF until that lands.
export const NATIVE_CALLKIT_ENABLED = process.env.EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED === "1";
export const PULSESOC_QA_MESSENGER_FIXTURES =
  process.env.EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES === "1" &&
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
export const PULSESOC_QA_STATUS_FIXTURES =
  process.env.EXPO_PUBLIC_PULSESOC_QA_STATUS_FIXTURES === "1" &&
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
export const PULSESOC_QA_REELS_FIXTURES =
  process.env.EXPO_PUBLIC_PULSESOC_QA_REELS_FIXTURES === "1" &&
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
export const APP_VERSION = normalizeOptionalString(
  (typeof Constants.expoConfig?.version === "string" ? Constants.expoConfig.version : "") ||
    (typeof extra.appVersion === "string" ? extra.appVersion : "")
);
export const EXPO_PROJECT_ID = normalizeOptionalString(
  process.env.EXPO_PUBLIC_EXPO_PROJECT_ID ||
    (typeof easConfig.projectId === "string" ? easConfig.projectId : "") ||
    (typeof extra.expoProjectId === "string" ? extra.expoProjectId : "")
);

function normalizeApiBaseUrl(value: string) {
  const url = String(value || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(url)) return "https://pulsesoc.com";
  return url;
}

function normalizeOptionalString(value: string) {
  const text = String(value || "").trim();
  return text || "";
}
