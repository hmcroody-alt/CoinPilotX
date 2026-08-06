import { pulseApi, PulseApiError } from "./pulseApi";

export type TranslatableContentType =
  | "post"
  | "comment"
  | "reply"
  | "chat"
  | "marketplace"
  | "product"
  | "business"
  | "review"
  | "support"
  | "profile"
  | "reel"
  | "status";

export type TranslationPolicy = "ask" | "always" | "never";

export type TranslationResult = {
  status: "translated" | "not_required" | "unsupported_language" | "provider_unavailable" | "invalid_request" | "content_changed" | "degraded" | "failed";
  original_text: string;
  translated: boolean;
  skipped?: boolean;
  cached?: boolean;
  reason?: string;
  translated_text?: string;
  source_language: string;
  target_language: string;
  policy: TranslationPolicy;
  content_version?: string;
  provider?: string;
  provider_model?: string;
  translated_at?: string;
  correlation_id?: string;
};

export type SupportedTranslationLanguage = {
  code: string;
  display_name: string;
  translation_support: boolean;
  source_support: boolean;
  target_support: boolean;
};

type TranslationResponse = {
  ok: boolean;
  result: TranslationResult;
};

export type TranslationFailure = {
  message: string;
  retryable: boolean;
  code?: string;
};

/**
 * Codes the server may attach to a failed translation. Transient ones deserve a
 * Retry affordance; permanent ones get a final message with no retry loop.
 */
const RETRYABLE_CODES = new Set([
  "provider_unavailable",
  "translation_unavailable",
  "provider_timeout",
  "provider_quota_exceeded",
  "request_unreachable",
  "session_refresh_temporary",
  "TRANSLATION_UNAVAILABLE",
  "PROVIDER_TIMEOUT",
  "PROVIDER_QUOTA_EXCEEDED"
]);

const PERMANENT_MESSAGES: Record<string, string> = {
  rollout_restricted: "Translation isn't available for your account yet.",
  moderation_blocked: "This content can't be translated.",
  invalid_language: "This language isn't supported for translation.",
  unsupported_content_type: "This content can't be translated.",
  text_too_long: "This content is too long to translate.",
  content_unavailable: "That content is no longer available.",
  provider_not_configured: "Translation isn't set up yet. Please try later.",
  invalid_credentials: "Translation isn't set up yet. Please try later."
};

export function classifyTranslationFailure(error: unknown): TranslationFailure {
  if (error instanceof PulseApiError) {
    const code = error.code || "";
    if (code in PERMANENT_MESSAGES) {
      return { message: PERMANENT_MESSAGES[code], retryable: false, code };
    }
    const retryable = RETRYABLE_CODES.has(code) || error.status >= 500 || error.status === 0;
    return {
      message: retryable
        ? "Translation is temporarily unavailable."
        : error.message || "Translation failed.",
      retryable,
      code: code || undefined
    };
  }
  return { message: "Translation failed.", retryable: true };
}

type TranslationPreferenceResponse = {
  ok: boolean;
  result: {
    source_language: string;
    target_language: string;
    policy: TranslationPolicy;
    updated_at?: string | null;
  };
};

type TranslationPreference = TranslationPreferenceResponse["result"];

const preferenceCache = new Map<string, TranslationPreference>();
const preferenceRequests = new Map<string, Promise<TranslationPreference>>();
const preferenceListeners = new Map<string, Set<(preference: TranslationPreference) => void>>();

function preferenceKey(sourceLanguage: string, targetLanguage: string) {
  return `${sourceLanguage || "auto"}:${targetLanguage}`;
}

function publishPreference(cacheKey: string, preference: TranslationPreference) {
  preferenceCache.set(cacheKey, preference);
  preferenceListeners.get(cacheKey)?.forEach(listener => listener(preference));
}

export function peekTranslationPreference(sourceLanguage: string, targetLanguage: string) {
  return preferenceCache.get(preferenceKey(sourceLanguage, targetLanguage));
}

export function subscribeTranslationPreference(
  sourceLanguage: string,
  targetLanguage: string,
  listener: (preference: TranslationPreference) => void
) {
  const cacheKey = preferenceKey(sourceLanguage, targetLanguage);
  const listeners = preferenceListeners.get(cacheKey) || new Set();
  listeners.add(listener);
  preferenceListeners.set(cacheKey, listeners);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) preferenceListeners.delete(cacheKey);
  };
}

export async function translatePulseContent(input: {
  contentType: TranslatableContentType;
  contentRef: string | number;
  text: string;
  sourceLanguage?: string;
  targetLanguage: string;
  force?: boolean;
}) {
  const response = await pulseApi<TranslationResponse>("/api/pulse/translations", {
    method: "POST",
    body: JSON.stringify({
      content_type: input.contentType,
      content_ref: String(input.contentRef),
      text: input.text,
      source_language: input.sourceLanguage || "auto",
      target_language: input.targetLanguage,
      force: input.force === true
    })
  });
  return response.result;
}

export async function getSupportedTranslationLanguages() {
  const response = await pulseApi<{
    ok: boolean;
    provider: string;
    languages: SupportedTranslationLanguage[];
  }>("/api/pulse/translations/languages");
  return response.languages;
}

export async function getTranslationPreference(sourceLanguage: string, targetLanguage: string) {
  const cacheKey = preferenceKey(sourceLanguage, targetLanguage);
  const cached = preferenceCache.get(cacheKey);
  if (cached) return cached;

  const pending = preferenceRequests.get(cacheKey);
  if (pending) return pending;

  const query = new URLSearchParams({
    source_language: sourceLanguage || "auto",
    target_language: targetLanguage
  });
  const request = pulseApi<TranslationPreferenceResponse>(
    `/api/pulse/translations/preference?${query.toString()}`
  )
    .then(response => {
      publishPreference(cacheKey, response.result);
      return response.result;
    })
    .finally(() => {
      preferenceRequests.delete(cacheKey);
    });
  preferenceRequests.set(cacheKey, request);
  return request;
}

export async function updateTranslationPreference(
  sourceLanguage: string,
  targetLanguage: string,
  policy: TranslationPolicy
) {
  const response = await pulseApi<TranslationPreferenceResponse>(
    "/api/pulse/translations/preference",
    {
      method: "PUT",
      body: JSON.stringify({
        source_language: sourceLanguage || "auto",
        target_language: targetLanguage,
        policy
      })
    }
  );
  publishPreference(preferenceKey(sourceLanguage, targetLanguage), response.result);
  return response.result;
}

export function clearTranslationPreferenceCache() {
  preferenceCache.clear();
  preferenceRequests.clear();
  preferenceListeners.clear();
}

export const clearTranslationPreferenceCacheForTests = clearTranslationPreferenceCache;
