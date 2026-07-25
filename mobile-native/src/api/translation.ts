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
  translated: boolean;
  skipped?: boolean;
  cached?: boolean;
  reason?: string;
  translated_text?: string;
  source_language: string;
  target_language: string;
  policy: TranslationPolicy;
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

function preferenceKey(sourceLanguage: string, targetLanguage: string) {
  return `${sourceLanguage || "auto"}:${targetLanguage}`;
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
      preferenceCache.set(cacheKey, response.result);
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
  preferenceCache.set(preferenceKey(sourceLanguage, targetLanguage), response.result);
  return response.result;
}

export function clearTranslationPreferenceCacheForTests() {
  preferenceCache.clear();
  preferenceRequests.clear();
}
