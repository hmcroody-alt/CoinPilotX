import Foundation

// The seam between the coordinator's bookkeeping and Apple's framework.
//
// `TranslationSession` cannot be constructed: Apple only vends one from
// `.translationTask`, and only to a mounted SwiftUI view. That is a reasonable
// API and a serious testing problem, because the interesting logic in this
// module is not the translating — it is the queue, the deduplication, the
// cancellation and the correlation of a response back to the request that asked
// for it. All of that decides whether a recycled feed cell shows another item's
// text, and none of it could be exercised while the only entry point demanded an
// object no test can make.
//
// So the coordinator talks to these two protocols instead. `TranslationSession`
// and `LanguageAvailability` reach them through adapters in
// `AppleTranslationSessionEngine.swift`, which is the only file in the module
// that imports Translation. The adapters hold no logic worth testing; the logic
// is all on this side of the seam, where a fake can drive it.
//
// This file imports Foundation only, and carries no availability gate, which is
// also what lets the coordinator compile and run on a machine with no iOS SDK at
// all — see `scripts/test_apple_translation_swift.sh`.

/// One unit of work as the coordinator states it, in Apple's shape but not
/// Apple's types.
struct TranslationEngineRequest {
  /// Echoed back by the engine so the response can be matched to the request.
  /// This is Apple's `clientIdentifier`, and it is our request id.
  let clientIdentifier: String
  let sourceText: String
}

struct TranslationEngineResponse {
  let clientIdentifier: String
  let targetText: String
  /// Minimal BCP-47 identifier of the language Apple actually read, which is the
  /// only place a detected source language can come from.
  let sourceLanguage: String?
  let targetLanguage: String?
}

/// A live translation session, abstracted to the three things we ask of it.
///
/// Main-actor isolated because the coordinator is, and because that is where
/// Apple's session was already being driven from before this seam existed.
@MainActor
protocol AppleTranslationEngine {
  /// Downloads and prepares the language model. Only ever reached after the user
  /// has agreed to a download (Stage 5), because on Apple's side this is what
  /// presents the system sheet.
  func prepare() async throws

  /// Translates a batch and returns responses tagged with the identifier each
  /// request carried.
  func translate(_ requests: [TranslationEngineRequest]) async throws -> [TranslationEngineResponse]

  /// Best-effort stop for work already handed to Apple. A no-op on OS versions
  /// that cannot interrupt a session, which is why the coordinator never treats
  /// this as sufficient on its own and always also discards the late response.
  func cancelInFlight()
}

/// Apple's answer to "can this device translate this pair", asked through
/// `LanguageAvailability` in production.
///
/// The same-language and invalid-tag verdicts are deliberately NOT part of this
/// protocol: those are decidable from the tags alone, the coordinator decides
/// them before asking, and a pair that needs no translation must never reach
/// Apple at all.
@MainActor
protocol AppleTranslationAvailability {
  /// `text` is non-nil only for an automatic-source request, where Apple needs
  /// the content to decide.
  func status(pair: TranslationPairKey, text: String?) async -> AppleLanguageStatus

  /// Minimal BCP-47 identifiers of every language this device can translate.
  func supportedLanguages() async -> [String]
}

/// Used below iOS 18, where the Translation framework does not exist.
///
/// Answering `.unsupported` rather than `.temporarilyUnavailable` is the point:
/// `unsupportedLanguagePair` and `unsupportedOSVersion` both permit cloud
/// fallback, so an iOS 17 device degrades to the cloud instead of showing a
/// failure, and it does so without ever pretending a retry might help.
struct UnsupportedOSAvailability: AppleTranslationAvailability {
  func status(pair: TranslationPairKey, text: String?) async -> AppleLanguageStatus {
    .unsupported
  }

  func supportedLanguages() async -> [String] { [] }
}
