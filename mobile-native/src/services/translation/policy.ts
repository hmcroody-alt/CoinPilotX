/**
 * Which providers a request is allowed to reach, and why one was excluded.
 *
 * The router asks this before it does any work, so that a refusal is a decision
 * with a name rather than a provider call that happens not to be made. Stage 8
 * requires that the billable path can never be entered by accident; the way
 * that is enforced here is that `cloud_fallback` has to be *earned* by four
 * independent conditions, and the reason the last failing one gives is carried
 * on the plan so the UI can say something true.
 */

import { isFlagValueOn, isFlagValueOnUnlessDisabled } from "../../core/envFlag";
import type {
  CloudExclusion,
  TranslationFailureCode,
  TranslationPrivacy,
  TranslationRequest
} from "./types";
import { privacyForContentType } from "./types";

export type { CloudExclusion } from "./types";

export type TranslationFlags = {
  /** Apple's on-device engine may be used. */
  appleOnDevice: boolean;
  /** The router may fall back to a cloud provider when Apple cannot answer. */
  cloudFallback: boolean;
  /** The configured cloud provider (today Google) is itself enabled. */
  googleProvider: boolean;
  /**
   * An *automatic* request — one the user did not ask for — may escalate to the
   * billable provider. Default off, which is Stage 8's target posture.
   */
  automaticCloud: boolean;
};

/**
 * Flags are read at call time, never cached at module load, so a test can set a
 * variable and call the router without re-importing the module graph.
 *
 * Apple and the cloud fallback both default *on*: enabling Apple only inserts a
 * free, private provider ahead of the one already in production, and Stage 8 is
 * explicit that Google must not be switched off before physical verification.
 * Rollback in either direction is therefore a flag flip, not a revert.
 *
 * `automaticCloud` defaults *off*, and that asymmetry is the whole cost
 * control: the "Always translate" preference keeps working, because on-device
 * translation is free and stays on the phone, but it can no longer silently
 * bill a per-character cloud request for every message that scrolls past.
 */
export function readTranslationFlags(): TranslationFlags {
  return {
    appleOnDevice: isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_APPLE_ON_DEVICE_TRANSLATION_ENABLED),
    cloudFallback: isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_TRANSLATION_CLOUD_FALLBACK_ENABLED),
    googleProvider: isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_GOOGLE_TRANSLATION_ENABLED),
    automaticCloud: isFlagValueOn(process.env.EXPO_PUBLIC_TRANSLATION_AUTOMATIC_ENABLED)
  };
}

export type ProviderPlan = {
  /** Ordered. `cache` is always first and `cloud_fallback` always last. */
  providers: Array<"cache" | "apple_on_device" | "cloud_fallback">;
  privacy: TranslationPrivacy;
  /** Null when the cloud provider is reachable. */
  cloudExclusion: CloudExclusion | null;
};

const EXCLUSION_CODES: Record<CloudExclusion, TranslationFailureCode> = {
  private_content: "cloud_not_permitted_for_private_content",
  flag_disabled: "cloud_fallback_disabled",
  automatic_request: "cloud_fallback_disabled",
  budget_exhausted: "budget_exceeded",
  breaker_open: "rate_limited"
};

export function codeForExclusion(exclusion: CloudExclusion): TranslationFailureCode {
  return EXCLUSION_CODES[exclusion];
}

/**
 * `cloudReachable` is supplied by the caller rather than read here because it
 * depends on the character budget and the circuit breaker, which are stateful
 * and live in `cloudGuard.ts`. Keeping this function pure is what lets the
 * ordering rules be tested without touching that state.
 */
export function planProviders(
  request: Pick<TranslationRequest, "contentType" | "userInitiated">,
  flags: TranslationFlags,
  cloudReachable: { allowed: boolean; exclusion: CloudExclusion | null } = { allowed: true, exclusion: null }
): ProviderPlan {
  const privacy = privacyForContentType(request.contentType);
  const providers: ProviderPlan["providers"] = ["cache"];
  if (flags.appleOnDevice) providers.push("apple_on_device");

  const exclusion = firstCloudExclusion(privacy, request.userInitiated, flags, cloudReachable);
  if (exclusion === null) providers.push("cloud_fallback");

  return { providers, privacy, cloudExclusion: exclusion };
}

function firstCloudExclusion(
  privacy: TranslationPrivacy,
  userInitiated: boolean,
  flags: TranslationFlags,
  cloudReachable: { allowed: boolean; exclusion: CloudExclusion | null }
): CloudExclusion | null {
  // Privacy is checked before the flags on purpose. A private message must not
  // become cloud-eligible by somebody turning a flag on, so this branch has to
  // be unreachable-by-configuration rather than merely off by default.
  if (privacy === "private") return "private_content";
  if (!flags.cloudFallback || !flags.googleProvider) return "flag_disabled";
  if (!userInitiated && !flags.automaticCloud) return "automatic_request";
  if (!cloudReachable.allowed) return cloudReachable.exclusion ?? "budget_exhausted";
  return null;
}
