import type { ComponentType } from "react";
import type { ViewProps } from "react-native";

/**
 * Stage 4 availability classification, produced by Apple's `LanguageAvailability`
 * rather than any hardcoded list.
 */
export type AppleLanguageStatus =
  | "installed"
  | "supported_download_required"
  | "unsupported"
  | "same_language"
  | "invalid"
  | "temporarily_unavailable";

/**
 * Stage 9 typed failure reasons. Mirrors `AppleTranslationFailure` in
 * `ios/AppleTranslationError.swift`; the two must be changed together.
 */
export type AppleTranslationFailureCode =
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
  | "invalid_language";

export type AppleTranslationRequest = {
  /** Unique per user action. Echoed back so a recycled cell can drop a stale response. */
  requestId: string;
  /** Stable content identity, e.g. `post:1234`. Used for bulk cancellation. */
  contentId: string;
  /** Bumped when the content is edited; echoed back for cache invalidation. */
  contentVersion?: string | null;
  text: string;
  /** Omit or pass `"auto"` to let Apple detect the source language. */
  sourceLanguage?: string | null;
  targetLanguage: string;
  /**
   * Whether Apple may show its language-download UI. Must reflect an explicit
   * user decision — never pass `true` from a render path.
   */
  allowDownload?: boolean;
};

export type AppleTranslationSuccess = {
  ok: true;
  requestId: string;
  contentId: string;
  contentVersion?: string;
  translatedText: string;
  /** Apple's detected source language, present even when none was supplied. */
  detectedSourceLanguage?: string;
  targetLanguage: string;
  durationMs: number;
  /** True when served from another identical in-flight request. */
  deduplicated: boolean;
  /** True when a model download had to be prepared first. */
  downloadPrepared: boolean;
};

export type AppleTranslationFailureResult = {
  ok: false;
  requestId: string;
  code: AppleTranslationFailureCode;
  /** Whether retrying the identical request could plausibly succeed. */
  recoverable: boolean;
  /**
   * Whether the router may consider the billable cloud provider. False for
   * cancellation and same-language so a scroll-away can never cost money.
   */
  permitsCloudFallback: boolean;
  /** Operator-facing diagnostic. Never render this. */
  detail?: string;
};

export type AppleTranslationResult = AppleTranslationSuccess | AppleTranslationFailureResult;

export type AppleLanguageStatusResult = {
  status: AppleLanguageStatus;
  source?: string;
  target: string;
  reason?: AppleTranslationFailureCode;
};

export type AppleTranslationDiagnostics = {
  isAvailable: boolean;
  /** Whether an `AppleTranslationHostView` is currently in the view hierarchy. */
  isHostMounted: boolean;
  activeHosts: number;
};

/** True when the native module is linked into this binary. */
export declare const isAppleTranslationModuleLinked: boolean;
/** True when the module is linked AND this iOS version ships Translation.framework. */
export declare const isAppleTranslationAvailable: boolean;

export declare const appleTranslationLimits: {
  maxTextLength: number;
  minimumOSVersion: string;
  osVersion: string | null;
};

export declare function getSupportedLanguages(): Promise<string[]>;
export declare function getLanguageStatus(
  sourceLanguage: string | null | undefined,
  targetLanguage: string
): Promise<AppleLanguageStatusResult>;
export declare function translate(
  request: AppleTranslationRequest
): Promise<AppleTranslationResult>;
export declare function cancelRequests(requestIds: string[], reason?: string): Promise<void>;
export declare function cancelContent(contentIds: string[], reason?: string): Promise<void>;
export declare function resetAppleTranslation(reason?: string): Promise<void>;
export declare function getDiagnostics(): Promise<AppleTranslationDiagnostics>;

/**
 * Mounting this view is what makes translation possible at all — Apple only
 * vends a `TranslationSession` through a live SwiftUI view. It renders 1×1pt and
 * fully transparent. `null` when the native module is absent.
 */
export declare const AppleTranslationHostView: ComponentType<ViewProps> | null;
