import Constants from "expo-constants";
import { envFlagOn } from "../core/envFlag";

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
// These five read the shared truthy set (see core/envFlag.ts) rather than the
// literal "1" they used to demand. They are still evaluated once at import
// rather than at call time, which is what stops a test from toggling them; every
// gate added since is a call-time accessor for exactly that reason.
export const DIGITAL_COMMERCE_ENABLED = envFlagOn("EXPO_PUBLIC_DIGITAL_COMMERCE_ENABLED");
// Native CallKit + PushKit VoIP (rings the iOS system call UI when the app is
// backgrounded/killed). Requires react-native-callkeep + react-native-voip-push-notification
// pods, the `voip` background mode, and a VoIP push certificate under the COINPLOTXAI APNs
// account (see reports/native_callkit_voip_integration.md). Default OFF until that lands.
export const NATIVE_CALLKIT_ENABLED = envFlagOn("EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED");
export const PULSESOC_QA_MESSENGER_FIXTURES =
  envFlagOn("EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES") &&
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
export const PULSESOC_QA_STATUS_FIXTURES =
  envFlagOn("EXPO_PUBLIC_PULSESOC_QA_STATUS_FIXTURES") &&
  /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
export const PULSESOC_QA_REELS_FIXTURES =
  envFlagOn("EXPO_PUBLIC_PULSESOC_QA_REELS_FIXTURES") &&
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

/**
 * Resolve a media reference the API handed us into something `<Image>` can load.
 *
 * A browser resolves a site-relative `/static/...` against the current origin,
 * so a server that emits one looks correct on web and broken here: React Native
 * has no origin, and `{ uri: "/static/x.png" }` fails silently as an empty box.
 * That is exactly how the PulseSoc Insight account came to render as a blank
 * circle in the feed while looking fine on the website.
 *
 * `api/profile.ts` had already been carrying a private copy of this rule; this
 * is that same rule, exported, so the other normalizers stop being one relative
 * URL away from the same invisible failure.
 */
export function absoluteApiUrl(value: string | null | undefined) {
  const url = String(value || "").trim();
  if (!url) return "";
  if (/^(https?:|data:|file:)/i.test(url)) return url;
  return url.startsWith("/") ? `${PULSE_API_BASE_URL}${url}` : `${PULSE_API_BASE_URL}/${url}`;
}

function normalizeApiBaseUrl(value: string) {
  const url = String(value || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(url)) return "https://pulsesoc.com";
  return url;
}

function normalizeOptionalString(value: string) {
  const text = String(value || "").trim();
  return text || "";
}
