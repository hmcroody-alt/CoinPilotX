/**
 * The billable cloud path, expressed in the provider-neutral contract.
 *
 * This is the *existing* PulseSoc translation endpoint, unchanged: Stage 8
 * requires that Google's code and configuration stay in place until Apple has
 * been proven on a physical device, so nothing here is new behaviour. What is
 * new is that it is no longer the first thing tried and no longer reachable
 * without a policy decision.
 *
 * The server's own vocabulary is wider than the router's, so the map below is
 * the single place the two meet. A code this file does not recognise becomes a
 * recoverable `provider_failure` rather than being passed through, because an
 * unknown string reaching the UI would have no copy and no retry rule.
 */

import { PulseApiError } from "../../../api/pulseApi";
import { translatePulseContent } from "../../../api/translation";
import type { TranslationFailureCode, TranslationOutcome, TranslationRequest } from "../types";
import type { ProviderAttempt } from "./apple";

/** Server error codes, mapped to the router's vocabulary. */
const SERVER_CODES: Record<string, { code: TranslationFailureCode; recoverable: boolean }> = {
  rollout_restricted: { code: "translation_disabled", recoverable: false },
  provider_not_configured: { code: "translation_disabled", recoverable: false },
  invalid_credentials: { code: "translation_disabled", recoverable: false },
  invalid_language: { code: "invalid_language", recoverable: false },
  moderation_blocked: { code: "invalid_text", recoverable: false },
  text_too_long: { code: "invalid_text", recoverable: false },
  unsupported_content_type: { code: "provider_failure", recoverable: false },
  content_unavailable: { code: "provider_failure", recoverable: false },
  provider_quota_exceeded: { code: "rate_limited", recoverable: false },
  PROVIDER_QUOTA_EXCEEDED: { code: "rate_limited", recoverable: false },
  provider_timeout: { code: "timeout", recoverable: true },
  PROVIDER_TIMEOUT: { code: "timeout", recoverable: true },
  provider_unavailable: { code: "provider_failure", recoverable: true },
  translation_unavailable: { code: "provider_failure", recoverable: true },
  TRANSLATION_UNAVAILABLE: { code: "provider_failure", recoverable: true },
  request_unreachable: { code: "provider_failure", recoverable: true },
  session_refresh_temporary: { code: "provider_failure", recoverable: true }
};

/** A 200 response that nonetheless carries no translation. */
const RESULT_STATUSES: Record<string, { code: TranslationFailureCode; recoverable: boolean }> = {
  unsupported_language: { code: "unsupported_language_pair", recoverable: false },
  invalid_request: { code: "invalid_text", recoverable: false },
  provider_unavailable: { code: "provider_failure", recoverable: true },
  content_changed: { code: "provider_failure", recoverable: true },
  degraded: { code: "provider_failure", recoverable: true },
  failed: { code: "provider_failure", recoverable: true }
};

export function cloudProviderVersion(providerModel?: string | null) {
  return `cloud:${providerModel || "unknown"}`;
}

export async function cloudTranslate(request: TranslationRequest): Promise<ProviderAttempt> {
  try {
    const result = await translatePulseContent({
      contentType: request.contentType,
      contentRef: request.contentId,
      text: request.text,
      sourceLanguage: request.sourceLanguage ?? "auto",
      targetLanguage: request.targetLanguage,
      force: request.userInitiated
    });

    if (result.translated_text) {
      return {
        permitsCloudFallback: false,
        outcome: {
          ok: true,
          requestId: request.requestId,
          contentId: request.contentId,
          provider: "cloud_fallback",
          translatedText: result.translated_text,
          sourceLanguage: result.source_language || request.sourceLanguage || null,
          targetLanguage: result.target_language || request.targetLanguage,
          cached: result.cached === true,
          downloadPrepared: false,
          durationMs: 0
        }
      };
    }

    if (result.reason === "same_language" || result.status === "not_required") {
      return {
        permitsCloudFallback: false,
        outcome: failure(request, "same_language", false)
      };
    }

    const mapped = RESULT_STATUSES[result.status] ?? {
      code: "provider_failure" as TranslationFailureCode,
      recoverable: true
    };
    return {
      permitsCloudFallback: false,
      outcome: failure(request, mapped.code, mapped.recoverable, result.reason)
    };
  } catch (error) {
    return { permitsCloudFallback: false, outcome: failureFromError(request, error) };
  }
}

function failureFromError(request: TranslationRequest, error: unknown): TranslationOutcome {
  if (error instanceof PulseApiError) {
    const mapped = SERVER_CODES[error.code || ""];
    if (mapped) return failure(request, mapped.code, mapped.recoverable, error.code);
    // A 5xx or an unreachable network is worth retrying; a 4xx the map does not
    // know about is a contract mismatch and retrying it would only repeat a
    // billable call that the server has already refused.
    const recoverable = error.status >= 500 || error.status === 0;
    return failure(request, "provider_failure", recoverable, error.code || `http_${error.status}`);
  }
  return failure(request, "provider_failure", true, "cloud_threw");
}

function failure(
  request: TranslationRequest,
  code: TranslationFailureCode,
  recoverable: boolean,
  detail?: string
): TranslationOutcome {
  return {
    ok: false,
    requestId: request.requestId,
    contentId: request.contentId,
    provider: "cloud_fallback",
    code,
    recoverable,
    downloadAvailable: false,
    detail
  };
}
