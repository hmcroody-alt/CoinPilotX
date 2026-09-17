/**
 * The provider-neutral translation contract (Stage 1).
 *
 * Every screen that wants text translated states *what* it wants here and never
 * names a provider. `router.translateText` decides between the device cache,
 * Apple's on-device engine and the billable cloud path, and the caller cannot
 * tell which answered except by reading `provider` on the result — which exists
 * for telemetry and for the "Translated from French" label, not for branching.
 *
 * Adding a provider must not require touching a screen. That is the property
 * this file exists to protect, and it is why the shape below carries no Apple
 * concept (no session, no host, no download sheet) and no Google concept (no
 * project id, no glossary, no character count).
 */

import type { TranslatableContentType } from "../../api/translation";

export type TranslationProviderId = "cache" | "apple_on_device" | "cloud_fallback" | "unavailable";

/**
 * Whether the text may leave the device.
 *
 * `private` is not a hint. A `private` request is never sent to the cloud
 * provider and never written to the shared server cache, regardless of flags,
 * because the content is one side of a conversation the sender did not consent
 * to publish. Derived from the content type by {@link privacyForContentType},
 * so no call site can get it wrong by omission.
 */
export type TranslationPrivacy = "private" | "public";

/**
 * Why a translation could not be produced.
 *
 * The first fourteen are the native module's own codes and must stay identical
 * to `AppleTranslationFailure` in
 * `modules/pulse-apple-translation/ios/AppleTranslationError.swift`; the router
 * passes them through rather than collapsing them, because the UI's retry
 * affordance and the download prompt are chosen from them.
 *
 * The remainder are decisions the router itself makes and that no provider can
 * report: a policy refusal, an exhausted budget, a tripped breaker.
 */
export type TranslationFailureCode =
  // Native (mirror of AppleTranslationFailure)
  | "unsupported_os_version"
  | "unsupported_language_pair"
  | "model_not_installed"
  | "download_canceled"
  | "download_failed"
  | "offline_model_unavailable"
  | "invalid_text"
  | "same_language"
  | "request_canceled"
  | "native_bridge_unavailable"
  | "translation_session_unavailable"
  | "provider_failure"
  | "timeout"
  | "invalid_language"
  // Router-level
  | "cloud_fallback_disabled"
  | "cloud_not_permitted_for_private_content"
  | "budget_exceeded"
  | "rate_limited"
  | "translation_disabled";

/**
 * Why the billable provider was not reached. Reported *alongside* `code`, never
 * instead of it: a request can both hit an unsupported pair and be refused the
 * cloud, and collapsing those into one field would leave the cost-control
 * refusals visible only when nothing else went wrong — which is almost never,
 * since the cloud is only considered after Apple has already failed.
 */
export type CloudExclusion =
  | "private_content"
  | "flag_disabled"
  | "automatic_request"
  | "budget_exhausted"
  | "breaker_open";

export type TranslationRequest = {
  /**
   * Unique per user action. Echoed back on the result so a recycled cell can
   * drop a response for the item it used to show.
   */
  requestId: string;
  /** Stable content identity, e.g. `post:1234`. Used for bulk cancellation. */
  contentId: string;
  contentType: TranslatableContentType;
  /**
   * Bumped when the content is edited. Part of the cache key, so an edit
   * invalidates the previous translation rather than showing a stale one.
   */
  contentVersion?: string | null;
  text: string;
  /** Omit or pass `"auto"` to let the provider detect the source language. */
  sourceLanguage?: string | null;
  targetLanguage: string;
  /**
   * Whether the provider may show its language-download UI. Must reflect an
   * explicit user decision — never pass `true` from a render path.
   */
  allowDownload?: boolean;
  /**
   * Whether this request originated from a user gesture. `false` means it came
   * from the "Always translate" preference, and an automatic request is never
   * allowed to reach the billable provider (see `policy.ts`).
   */
  userInitiated: boolean;
};

export type TranslationSuccess = {
  ok: true;
  requestId: string;
  contentId: string;
  provider: Exclude<TranslationProviderId, "unavailable">;
  translatedText: string;
  /** The provider's canonical source tag, present even when none was supplied. */
  sourceLanguage: string | null;
  targetLanguage: string;
  /** True when served from the device cache rather than produced now. */
  cached: boolean;
  /** True when a model download had to be prepared first. */
  downloadPrepared: boolean;
  durationMs: number;
};

export type TranslationFailure = {
  ok: false;
  requestId: string;
  contentId: string;
  /** The provider that produced the terminal failure, or `unavailable`. */
  provider: TranslationProviderId;
  code: TranslationFailureCode;
  /** Whether retrying the identical request could plausibly succeed. */
  recoverable: boolean;
  /**
   * Whether a download prompt is the right affordance. True only for
   * `model_not_installed`, and only when the pair is one Apple supports.
   */
  downloadAvailable: boolean;
  /**
   * Set whenever the billable provider was withheld, whatever `code` says.
   * This is the field the runbook and Stage 12 read to answer "why did this
   * not escalate" — `code` answers "why did this not translate".
   */
  cloudExclusion?: CloudExclusion;
  /** Operator-facing diagnostic. Never render this — see Stage 9. */
  detail?: string;
};

export type TranslationOutcome = TranslationSuccess | TranslationFailure;

/**
 * Availability of a pair, as classified by the provider rather than guessed
 * from a list. Mirrors `AppleLanguageStatus`, plus `cloud_only` for a pair
 * Apple cannot do but the cloud provider can.
 */
export type TranslationAvailability =
  | "installed"
  | "supported_download_required"
  | "cloud_only"
  | "unsupported"
  | "same_language"
  | "invalid"
  | "temporarily_unavailable";

/**
 * Content types whose text is one side of a private exchange.
 *
 * `chat` is a direct message and `support` is a support ticket; both contain
 * text the author addressed to one recipient. Everything else in
 * `TranslatableContentType` is already published to an audience.
 */
const PRIVATE_CONTENT_TYPES = new Set<TranslatableContentType>(["chat", "support"]);

export function privacyForContentType(contentType: TranslatableContentType): TranslationPrivacy {
  return PRIVATE_CONTENT_TYPES.has(contentType) ? "private" : "public";
}
