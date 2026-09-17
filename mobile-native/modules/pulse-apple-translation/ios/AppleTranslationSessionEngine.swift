import Foundation
import Translation

// The only file in this module that imports Translation and holds a session.
//
// Everything here is adapter: it translates between Apple's types and the
// protocols in `AppleTranslationEngine.swift` and makes no decisions. That is
// deliberate — this file is the part that cannot be unit-tested, so it is kept
// small enough to review by eye.

/// Wraps one live `TranslationSession`.
///
/// A struct, and created only inside the `.translationTask` closure in
/// `AppleTranslationHost.swift`, so it cannot outlive the view's task any more
/// than the session itself can. Nothing stores one beyond the scope of
/// `AppleTranslationCoordinator.run(engine:descriptor:)`, which clears it in a
/// `defer` (Stage 2: no session outside the SwiftUI view lifecycle).
@available(iOS 18.0, *)
struct TranslationSessionEngine: AppleTranslationEngine {
  let session: TranslationSession

  func prepare() async throws {
    try await session.prepareTranslation()
  }

  func translate(
    _ requests: [TranslationEngineRequest]
  ) async throws -> [TranslationEngineResponse] {
    let appleRequests = requests.map {
      TranslationSession.Request(sourceText: $0.sourceText, clientIdentifier: $0.clientIdentifier)
    }
    let responses = try await session.translations(from: appleRequests)
    return responses.map {
      TranslationEngineResponse(
        clientIdentifier: $0.clientIdentifier ?? "",
        targetText: $0.targetText,
        sourceLanguage: $0.sourceLanguage.minimalIdentifier,
        targetLanguage: $0.targetLanguage.minimalIdentifier
      )
    }
  }

  func cancelInFlight() {
    // `cancel()` arrived in iOS 26. On 18.x there is no way to recall work
    // already handed to Apple, which is why the coordinator also discards the
    // late response rather than relying on this call having done anything.
    if #available(iOS 26.0, *) {
      session.cancel()
    }
  }
}

/// Wraps `LanguageAvailability`, which is the device's own authority on what it
/// can translate. Stage 4 forbids a hardcoded list precisely so that a language
/// like Haitian Creole is answered by the device rather than guessed at here.
@available(iOS 18.0, *)
struct TranslationFrameworkAvailability: AppleTranslationAvailability {
  func status(pair: TranslationPairKey, text: String?) async -> AppleLanguageStatus {
    let availability = LanguageAvailability()
    let target = Locale.Language(identifier: pair.target)

    if let source = pair.source {
      let status = await availability.status(
        from: Locale.Language(identifier: source),
        to: target
      )
      return Self.classify(status)
    }

    // Automatic source: Apple needs the text to decide.
    guard let text, !text.isEmpty else { return .temporarilyUnavailable }
    do {
      return Self.classify(try await availability.status(for: text, to: target))
    } catch {
      // A detection failure is not an unsupported pair. Reporting
      // `temporarilyUnavailable` lets the coordinator attempt the translation
      // anyway instead of telling the user Apple cannot handle their language.
      return .temporarilyUnavailable
    }
  }

  func supportedLanguages() async -> [String] {
    await LanguageAvailability().supportedLanguages
      .map { $0.minimalIdentifier }
      .sorted()
  }

  private static func classify(_ status: LanguageAvailability.Status) -> AppleLanguageStatus {
    switch status {
    case .installed:
      return .installed
    case .supported:
      return .supportedDownloadRequired
    case .unsupported:
      return .unsupported
    @unknown default:
      return .temporarilyUnavailable
    }
  }
}

extension AppleTranslationCoordinator {
  /// The app-wide queue. One per process, because the constraint it exists to
  /// enforce — at most `maxActivePairs` sessions alive at once — is a property of
  /// the process, not of a screen.
  ///
  /// Declared here rather than beside the class so that the coordinator's own
  /// file needs no reference to any Translation type, which is what keeps it
  /// testable off-device. Tests build their own instances with a fake.
  @MainActor
  static let shared = AppleTranslationCoordinator(availability: makeAvailability())

  private static func makeAvailability() -> any AppleTranslationAvailability {
    if #available(iOS 18.0, *) {
      return TranslationFrameworkAvailability()
    }
    return UnsupportedOSAvailability()
  }
}
