import Foundation

// Stage 9 typed failure contract. Raw values are the wire contract shared with
// TypeScript (`src/services/translation/types.ts`); renaming one is a breaking
// change on both sides.
//
// Deliberately no ExpoModulesCore / Translation import: the enum itself must be
// constructible on every iOS version so that `unsupportedOSVersion` can be
// returned from a device that cannot even load the Translation framework.

enum AppleTranslationFailure: String {
  /// Device is below iOS 18 — the Translation framework does not exist.
  case unsupportedOSVersion = "unsupported_os_version"
  /// Apple reports `.unsupported` for this (source, target) pair.
  case unsupportedLanguagePair = "unsupported_language_pair"
  /// Apple supports the pair but the model is not on this device and the caller
  /// did not permit a download.
  case modelNotInstalled = "model_not_installed"
  /// The user dismissed Apple's download sheet.
  case downloadCanceled = "download_canceled"
  /// The download started and failed (disk, network, Apple-side).
  case downloadFailed = "download_failed"
  /// No network and no local model.
  case offlineModelUnavailable = "offline_model_unavailable"
  /// Empty text, whitespace only, or beyond the per-request length limit.
  case invalidText = "invalid_text"
  /// Source and target resolve to the same language; nothing to do.
  case sameLanguage = "same_language"
  /// The request was cancelled by us (scroll-away, unmount, account switch).
  case requestCanceled = "request_canceled"
  /// The Expo native module is absent (Android, or a build without the module).
  case nativeBridgeUnavailable = "native_bridge_unavailable"
  /// The module exists but no `AppleTranslationHost` view is mounted, so no
  /// `TranslationSession` can be obtained. Distinct from the above because the
  /// fix is a UI-tree problem, not a build problem.
  case translationSessionUnavailable = "translation_session_unavailable"
  /// Apple returned a session but it produced no response for our request id.
  case providerFailure = "provider_failure"
  /// Exceeded the native watchdog.
  case timeout = "timeout"
  /// A malformed language tag reached the bridge.
  case invalidLanguage = "invalid_language"

  /// Whether retrying the identical request could plausibly succeed. The JS
  /// router uses this to decide between "Try again" and a terminal message, and
  /// to decide whether cloud fallback is worth attempting.
  var isRecoverable: Bool {
    switch self {
    case .downloadCanceled, .downloadFailed, .offlineModelUnavailable,
         .providerFailure, .timeout, .translationSessionUnavailable:
      return true
    case .unsupportedOSVersion, .unsupportedLanguagePair, .modelNotInstalled,
         .invalidText, .sameLanguage, .requestCanceled, .nativeBridgeUnavailable,
         .invalidLanguage:
      return false
    }
  }

  /// Whether the JS router should consider the cloud provider for this failure.
  ///
  /// `requestCanceled` and `sameLanguage` must NOT fall through — falling
  /// through on cancellation is how a scroll-away turns into billable Google
  /// traffic (Stage 8: never silently generate expensive cloud traffic).
  var permitsCloudFallback: Bool {
    switch self {
    case .unsupportedOSVersion, .unsupportedLanguagePair, .modelNotInstalled,
         .downloadCanceled, .downloadFailed, .offlineModelUnavailable,
         .nativeBridgeUnavailable, .translationSessionUnavailable,
         .providerFailure, .timeout:
      return true
    case .invalidText, .sameLanguage, .requestCanceled, .invalidLanguage:
      return false
    }
  }
}

/// Error thrown inside the native bridge. Carries a typed reason plus an
/// operator-facing detail string that is *never* surfaced to the UI verbatim
/// (Stage 9: no stack traces, credentials or raw native errors in the UI).
struct AppleTranslationError: Error {
  let failure: AppleTranslationFailure
  /// Short diagnostic for logs only. Must not contain source or translated text.
  let detail: String?

  init(_ failure: AppleTranslationFailure, detail: String? = nil) {
    self.failure = failure
    self.detail = detail
  }

  var code: String { failure.rawValue }
}

/// Maps an arbitrary `Error` coming out of Apple's framework onto the typed
/// contract.
///
/// `TranslationError`'s cases are not exhaustively documented and its case set
/// has changed between iOS 18 and 26, so this matches on the error *domain and
/// code* rather than pattern-matching enum cases. Pattern-matching cases that
/// do not exist on iOS 18 would not compile against the 18.0 availability floor
/// we target.
enum AppleTranslationErrorMapper {
  static func map(_ error: Error, allowDownload: Bool) -> AppleTranslationError {
    if error is CancellationError {
      return AppleTranslationError(.requestCanceled, detail: "swift_task_cancelled")
    }

    let nsError = error as NSError

    // URLError-shaped connectivity problems surfaced through the model fetcher.
    if nsError.domain == NSURLErrorDomain {
      switch nsError.code {
      case NSURLErrorNotConnectedToInternet, NSURLErrorNetworkConnectionLost,
           NSURLErrorDataNotAllowed, NSURLErrorCannotConnectToHost:
        return AppleTranslationError(.offlineModelUnavailable, detail: "urlerror_\(nsError.code)")
      case NSURLErrorCancelled:
        return AppleTranslationError(.downloadCanceled, detail: "urlerror_cancelled")
      case NSURLErrorTimedOut:
        return AppleTranslationError(.timeout, detail: "urlerror_timed_out")
      default:
        return AppleTranslationError(.downloadFailed, detail: "urlerror_\(nsError.code)")
      }
    }

    if nsError.domain == NSCocoaErrorDomain, nsError.code == NSUserCancelledError {
      return AppleTranslationError(.downloadCanceled, detail: "user_cancelled")
    }

    // Apple's own domain. The string form is stable enough to classify on and,
    // unlike the enum cases, is available on every OS version we run on.
    if nsError.domain.contains("Translation") || nsError.domain.contains("NLTranslation") {
      let message = String(describing: error).lowercased()
      if message.contains("cancel") {
        return AppleTranslationError(
          allowDownload ? .downloadCanceled : .requestCanceled,
          detail: "translation_cancelled"
        )
      }
      if message.contains("unsupported") || message.contains("not supported") {
        return AppleTranslationError(.unsupportedLanguagePair, detail: "translation_unsupported")
      }
      if message.contains("download") || message.contains("not installed")
        || message.contains("unavailable") {
        return AppleTranslationError(
          allowDownload ? .downloadFailed : .modelNotInstalled,
          detail: "translation_model_missing"
        )
      }
      return AppleTranslationError(.providerFailure, detail: "translation_\(nsError.code)")
    }

    return AppleTranslationError(
      .providerFailure,
      detail: "\(nsError.domain)_\(nsError.code)"
    )
  }
}
