import AsyncStorage from "@react-native-async-storage/async-storage";
import { pulseApi, PulseApiError } from "./pulseApi";

/**
 * Deferred referral attribution.
 *
 * A referral link opens the App Store, so the code never reaches the app
 * directly. The backend keeps a server-side click record instead, and
 * `POST /api/mobile/referral/claim` (idempotent) matches the freshly
 * authenticated account against it. We deliberately do NOT read the clipboard
 * to recover a code — iOS shows a paste notification for that, which reads as
 * snooping.
 */
const REFERRAL_CLAIM_ATTEMPTED_KEY = "pulsesoc.native.referral.claimAttempted";

export type ReferralClaimResponse = {
  ok?: boolean;
  claimed?: boolean;
  message?: string;
};

export function claimReferral(code?: string) {
  return pulseApi<ReferralClaimResponse>("/api/mobile/referral/claim", {
    method: "POST",
    body: JSON.stringify(code ? { code } : {})
  });
}

/** Reachability failures are retryable next launch; a server verdict is final. */
function isTransientClaimError(error: unknown): boolean {
  return (
    error instanceof PulseApiError &&
    (error.code === "request_unreachable" || error.code === "request_timeout" || error.status >= 500)
  );
}

/**
 * Fire-and-forget, once per install: called after the session becomes
 * authenticated (which covers both a fresh signup and the first authenticated
 * launch after install). Guarded by a persisted flag so the endpoint is only
 * ever asked once — the endpoint itself is idempotent, the guard just avoids
 * pointless traffic. Must never block or break the auth flow, so every path
 * here swallows its own errors.
 */
export async function claimReferralAttributionOnce(): Promise<void> {
  try {
    const attempted = await AsyncStorage.getItem(REFERRAL_CLAIM_ATTEMPTED_KEY);
    if (attempted) return;
    try {
      await claimReferral();
      await AsyncStorage.setItem(REFERRAL_CLAIM_ATTEMPTED_KEY, new Date().toISOString());
    } catch (error) {
      // A definitive server answer (no match, endpoint disabled, 4xx) still
      // counts as attempted; only pure reachability failures retry next launch.
      if (!isTransientClaimError(error)) {
        await AsyncStorage.setItem(REFERRAL_CLAIM_ATTEMPTED_KEY, new Date().toISOString());
      }
    }
  } catch {
    // Referral attribution is best-effort by design.
  }
}
