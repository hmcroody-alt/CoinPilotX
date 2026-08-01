import { pulseApi } from "./pulseApi";

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
