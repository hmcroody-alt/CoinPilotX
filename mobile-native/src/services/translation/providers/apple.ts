/**
 * Apple's on-device engine, expressed in the provider-neutral contract.
 *
 * All this file does is marshal. The decision to *use* Apple belongs to
 * `policy.ts`, the decision to fall through to the cloud belongs to `router.ts`,
 * and the session lifecycle belongs to the Swift coordinator — none of which
 * this module knows about.
 *
 * The one judgement it does make is `permitsCloudFallback`, and it takes that
 * verbatim from the native failure rather than re-deriving it from the code.
 * Stage 8 requires that a cancelled request can never become billable, and the
 * native side is where cancellation is observed; re-deciding it here would be a
 * second copy of that rule, which is one copy too many.
 */

import {
  isAppleTranslationAvailable,
  appleTranslationLimits,
  cancelContent,
  cancelRequests,
  getLanguageStatus,
  translate
} from "pulse-apple-translation";
import type { TranslationAvailability, TranslationOutcome, TranslationRequest } from "../types";

export type ProviderAttempt = {
  outcome: TranslationOutcome;
  /** Whether the router may consider the billable provider after this attempt. */
  permitsCloudFallback: boolean;
};

export function appleIsUsable() {
  return isAppleTranslationAvailable;
}

/**
 * Provider plus model identity for the cache key. The OS version stands in for
 * the model version, which Apple does not expose: an OS upgrade can ship a
 * better model, and serving yesterday's output for it would be indistinguishable
 * from the engine not having improved.
 */
export function appleProviderVersion() {
  return `apple:${appleTranslationLimits.osVersion ?? "unknown"}`;
}

export async function appleAvailability(
  sourceLanguage: string | null,
  targetLanguage: string
): Promise<TranslationAvailability> {
  const status = await getLanguageStatus(sourceLanguage, targetLanguage);
  return status.status;
}

export async function appleTranslate(request: TranslationRequest): Promise<ProviderAttempt> {
  const result = await translate({
    requestId: request.requestId,
    contentId: request.contentId,
    contentVersion: request.contentVersion ?? null,
    text: request.text,
    sourceLanguage: request.sourceLanguage ?? null,
    targetLanguage: request.targetLanguage,
    allowDownload: request.allowDownload === true
  });

  if (result.ok) {
    return {
      permitsCloudFallback: false,
      outcome: {
        ok: true,
        // The caller's own identity, not the echo. Native correlation by
        // `clientIdentifier` happens inside the coordinator and is tested there;
        // out here the contract is that a caller gets back the id it passed, so
        // that its own staleness check cannot reject its own answer.
        requestId: request.requestId,
        contentId: request.contentId,
        provider: "apple_on_device",
        translatedText: result.translatedText,
        sourceLanguage: result.detectedSourceLanguage ?? request.sourceLanguage ?? null,
        targetLanguage: result.targetLanguage,
        cached: false,
        downloadPrepared: result.downloadPrepared,
        durationMs: result.durationMs
      }
    };
  }

  return {
    permitsCloudFallback: result.permitsCloudFallback,
    outcome: {
      ok: false,
      requestId: request.requestId,
      contentId: request.contentId,
      provider: "apple_on_device",
      code: result.code,
      recoverable: result.recoverable,
      // A download prompt is only honest when the model is the only thing
      // missing. `unsupported_language_pair` also has no model installed, and
      // offering a download for it would be an affordance that cannot succeed.
      downloadAvailable: result.code === "model_not_installed",
      detail: result.detail
    }
  };
}

export async function appleCancelRequests(requestIds: string[], reason: string) {
  await cancelRequests(requestIds, reason);
}

export async function appleCancelContent(contentIds: string[], reason: string) {
  await cancelContent(contentIds, reason);
}
