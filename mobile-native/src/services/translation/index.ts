/**
 * The public face of the translation service.
 *
 * Screens import from here and nowhere else in this directory. That is not
 * style: Stage 1 requires that no screen can call a provider directly, and the
 * guard in `__tests__/providerIsolation.test.ts` enforces it by checking that
 * nothing outside `services/translation/` imports `pulse-apple-translation` or
 * `api/translation`'s `translatePulseContent`.
 */

export { normalizeLanguage, modelScope, isSameLanguage } from "./languages";
export type { LanguageOutcome } from "./languages";

export {
  privacyForContentType
} from "./types";
export type {
  CloudExclusion,
  TranslationAvailability,
  TranslationFailure,
  TranslationFailureCode,
  TranslationOutcome,
  TranslationPrivacy,
  TranslationProviderId,
  TranslationRequest,
  TranslationSuccess
} from "./types";

export { readTranslationFlags } from "./policy";
export type { TranslationFlags } from "./policy";

export {
  cancelTranslationContent,
  cancelTranslationRequests,
  resetTranslationRouter,
  translateText
} from "./router";

export {
  clearTranslationCache,
  hydrateTranslationCache,
  invalidateTranslationContent,
  setTranslationCacheScope
} from "./cache";

export { hydrateCloudBudget, cloudBudgetSnapshot } from "./cloudGuard";

export {
  recentTranslationEvents,
  resetTranslationMetrics,
  translationMetricsSnapshot
} from "./metrics";

export { appleAvailability, appleIsUsable } from "./providers/apple";
