import Constants from "expo-constants";
import { envFlagOn, isFlagValueOn } from "../core/envFlag";

const extra = Constants.expoConfig?.extra || {};
const easConfig = Constants.easConfig || {};

const configuredBaseUrl =
  process.env.EXPO_PUBLIC_PULSE_API_BASE_URL ||
  (typeof extra.pulseApiBaseUrl === "string" ? extra.pulseApiBaseUrl : "https://pulsesoc.com");

export const PULSE_API_BASE_URL = normalizeApiBaseUrl(configuredBaseUrl);

/** Which backend a build actually reached, classified from the resolved URL. */
export type PulseEnvironment = "production" | "staging" | "local" | "custom";

export const PULSE_ENVIRONMENT: PulseEnvironment = classifyEnvironment(PULSE_API_BASE_URL);

/**
 * Refuse to run against a backend this build was not built to reach.
 *
 * Pointing a QA build at a non-production backend is two separate wishes -- the
 * URL, and the intent -- and until now only the URL was expressed. That is a
 * problem here because *every* way this resolution can fail lands on
 * production: an unset `EXPO_PUBLIC_PULSE_API_BASE_URL` falls through to the
 * literal below, and `normalizeApiBaseUrl` also returns it for any value that
 * does not parse. So a typo'd host, or a variable the bundler failed to inline,
 * silently produces a build that looks like the staging build, is named like
 * the staging build, and is talking to the live site.
 *
 * `EXPO_PUBLIC_PULSE_ENVIRONMENT` states the intent, and this compares the two.
 * It is deliberately a throw rather than a warning: the whole failure mode is
 * that nothing looks wrong. A production build sets nothing here and so cannot
 * trip it -- the check only binds once someone has claimed an environment.
 *
 * Both variables are spelled as string literals on purpose. `babel-preset-expo`
 * substitutes `process.env.X` only for a StringLiteral key, so a name reached
 * through a computed lookup reads `undefined` in a release bundle -- which for
 * this check would mean silently not running at all.
 */
const declaredEnvironment = normalizeOptionalString(
  process.env.EXPO_PUBLIC_PULSE_ENVIRONMENT
).toLowerCase();

/** Non-secret build identity. Host only -- never tokens, keys or credentials. */
export const PULSE_ENVIRONMENT_IDENTITY = {
  appEnvironment: PULSE_ENVIRONMENT.toUpperCase(),
  backendHost: PULSE_API_BASE_URL.replace(/^https?:\/\//i, ""),
  declaredEnvironment: declaredEnvironment ? declaredEnvironment.toUpperCase() : "(undeclared)"
} as const;

// Logged unconditionally: this is the line that makes "which backend am I
// talking to" answerable from a device log instead of by inference.
console.log("[PulseSocEnvironment]", JSON.stringify(PULSE_ENVIRONMENT_IDENTITY));

if (declaredEnvironment && declaredEnvironment !== PULSE_ENVIRONMENT) {
  const detail =
    `declared=${declaredEnvironment} resolved=${PULSE_ENVIRONMENT} host=${PULSE_ENVIRONMENT_IDENTITY.backendHost}`;
  console.error(`[PulseSocEnvironment] MISMATCH ${detail}`);
  throw new Error(
    `PulseSoc build environment mismatch: ${detail}. ` +
      "Refusing to start rather than fall back to the production backend."
  );
}

// Digital purchases (Premium checkout/billing, marketplace checkout, payout onboarding)
// currently route to external web/Stripe. Apple Guideline 3.1.1 requires StoreKit for
// in-app digital goods, which is not yet implemented, so these entry points are hidden
// unless a build explicitly opts in. Default OFF for App Store / production builds.
// These five read the shared truthy set (see core/envFlag.ts) rather than the
// literal "1" they used to demand. They are still evaluated once at import
// rather than at call time, which is what stops a test from toggling them; every
// gate added since is a call-time accessor for exactly that reason.
//
// The two product gates spell their variable literally. `babel-preset-expo`
// inlines `process.env.X` only when the key is a StringLiteral, so a name passed
// through `envFlagOn`'s computed lookup is never substituted and reads undefined
// in a release bundle. The three QA fixture gates below keep the computed form
// on purpose: each is ANDed with a loopback base URL, which no distributed build
// can satisfy, so they are only ever reachable from development.
export const DIGITAL_COMMERCE_ENABLED = isFlagValueOn(process.env.EXPO_PUBLIC_DIGITAL_COMMERCE_ENABLED);
// Native CallKit + PushKit VoIP (rings the iOS system call UI when the app is
// backgrounded/killed). Requires react-native-callkeep + react-native-voip-push-notification
// pods, the `voip` background mode, and a VoIP push certificate under the COINPLOTXAI APNs
// account (see reports/native_callkit_voip_integration.md). Default OFF until that lands.
export const NATIVE_CALLKIT_ENABLED = isFlagValueOn(process.env.EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED);
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

/**
 * Classify a resolved base URL into the environment it belongs to.
 *
 * `production` is matched exactly rather than by substring: a host such as
 * `pulsesoc.com.example.net` must not be read as the live site, and equally a
 * staging host that merely contains `pulsesoc.com` must not be either. Anything
 * unrecognised is `custom`, which is honest -- and because the mismatch check
 * compares against a declared value, an unrecognised host still cannot
 * masquerade as `staging`.
 */
function classifyEnvironment(baseUrl: string): PulseEnvironment {
  const host = String(baseUrl || "").replace(/^https?:\/\//i, "").toLowerCase();
  if (/^(127\.0\.0\.1|localhost|10\.0\.2\.2)(:\d+)?$/.test(host)) return "local";
  if (/^(www\.)?pulsesoc\.com$/.test(host)) return "production";
  if (/(^|[.-])(staging|stage|qa)([.-]|$)/.test(host)) return "staging";
  return "custom";
}

function normalizeOptionalString(value: string) {
  const text = String(value || "").trim();
  return text || "";
}
