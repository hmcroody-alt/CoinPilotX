import ExpoModulesCore
import Foundation
import UIKit

// The JS-facing surface of the Apple on-device translation bridge.
//
// Design note: typed failures come back as a *result payload*
// (`{ ok: false, code, recoverable, permitsCloudFallback }`) rather than as a
// thrown native exception. Stage 9 requires fifteen distinguishable reasons and
// Stage 8 requires the JS router to decide per-reason whether billable cloud
// fallback is allowed. A rejected promise would flatten all of that into a
// string, and a stringly-typed native error is exactly what must never reach
// the UI.

public final class PulseAppleTranslationModule: Module {
  public func definition() -> ModuleDefinition {
    Name("PulseAppleTranslation")

    // True only when the Translation framework actually exists on this device.
    Constant("isAvailable") { Self.isFrameworkAvailable }
    Constant("minimumOSVersion") { "18.0" }
    Constant("osVersion") { UIDevice.current.systemVersion }
    Constant("maxTextLength") { AppleTranslationLimits.maxTextLength }

    // MARK: Availability (Stage 4)

    AsyncFunction("getSupportedLanguages") { () async throws -> [String] in
      guard #available(iOS 18.0, *) else { return [] }
      return await AppleTranslationCoordinator.shared.supportedLanguages()
    }

    AsyncFunction("getLanguageStatus") { (
      source: String?,
      target: String
    ) async throws -> [String: Any] in
      let normalizedTarget = LanguageNormalizer.normalize(target)
      guard case .language(let targetTag) = normalizedTarget else {
        return ["status": AppleLanguageStatus.invalid.rawValue, "target": target]
      }

      var sourceTag: String?
      switch LanguageNormalizer.normalize(source) {
      case .language(let tag):
        sourceTag = tag
      case .automatic:
        sourceTag = nil
      case .invalid:
        return ["status": AppleLanguageStatus.invalid.rawValue, "target": targetTag]
      }

      if let sourceTag,
         LanguageNormalizer.modelScope(sourceTag) == LanguageNormalizer.modelScope(targetTag) {
        return [
          "status": AppleLanguageStatus.sameLanguage.rawValue,
          "source": sourceTag,
          "target": targetTag
        ]
      }

      guard #available(iOS 18.0, *) else {
        return [
          "status": AppleLanguageStatus.unsupported.rawValue,
          "target": targetTag,
          "reason": AppleTranslationFailure.unsupportedOSVersion.rawValue
        ]
      }

      let status = await AppleTranslationCoordinator.shared.availabilityStatus(
        source: sourceTag,
        target: targetTag
      )
      var payload: [String: Any] = ["status": status.rawValue, "target": targetTag]
      if let sourceTag { payload["source"] = sourceTag }
      return payload
    }

    // MARK: Translation

    AsyncFunction("translate") { (
      request: AppleTranslationRequest
    ) async throws -> [String: Any] in
      guard #available(iOS 18.0, *) else {
        return Self.failure(.unsupportedOSVersion, requestId: request.requestId)
      }

      let normalizedTarget = LanguageNormalizer.normalize(request.targetLanguage)
      guard case .language(let targetTag) = normalizedTarget else {
        return Self.failure(.invalidLanguage, requestId: request.requestId, detail: "target")
      }

      var sourceTag: String?
      switch LanguageNormalizer.normalize(request.sourceLanguage) {
      case .language(let tag):
        sourceTag = tag
      case .automatic:
        sourceTag = nil
      case .invalid:
        return Self.failure(.invalidLanguage, requestId: request.requestId, detail: "source")
      }

      // Answered here rather than in the coordinator so that a same-language tap
      // never mounts a host, never touches Apple, and — because
      // `sameLanguage.permitsCloudFallback` is false — never reaches Google.
      if let sourceTag,
         LanguageNormalizer.modelScope(sourceTag) == LanguageNormalizer.modelScope(targetTag) {
        return Self.failure(.sameLanguage, requestId: request.requestId)
      }

      let job = AppleTranslationJobRequest(
        requestId: request.requestId,
        contentId: request.contentId,
        contentVersion: request.contentVersion,
        text: request.text,
        source: sourceTag,
        target: targetTag,
        allowDownload: request.allowDownload
      )

      do {
        let result = try await AppleTranslationCoordinator.shared.translate(job)
        var payload: [String: Any] = [
          "ok": true,
          "requestId": result.requestId,
          "contentId": result.contentId,
          "translatedText": result.translatedText,
          "targetLanguage": result.targetLanguage,
          "durationMs": result.durationMs,
          "deduplicated": result.deduplicated,
          "downloadPrepared": result.downloadPrepared
        ]
        if let version = result.contentVersion { payload["contentVersion"] = version }
        if let detected = result.detectedSourceLanguage {
          payload["detectedSourceLanguage"] = detected
        }
        return payload
      } catch let error as AppleTranslationError {
        return Self.failure(error.failure, requestId: request.requestId, detail: error.detail)
      } catch {
        let mapped = AppleTranslationErrorMapper.map(error, allowDownload: request.allowDownload)
        return Self.failure(mapped.failure, requestId: request.requestId, detail: mapped.detail)
      }
    }

    // MARK: Cancellation (Stage 7)

    AsyncFunction("cancelRequests") { (requestIds: [String], reason: String?) async throws in
      guard #available(iOS 18.0, *) else { return }
      await AppleTranslationCoordinator.shared.cancel(
        requestIds: requestIds,
        reason: reason ?? "js_cancel"
      )
    }

    AsyncFunction("cancelContent") { (contentIds: [String], reason: String?) async throws in
      guard #available(iOS 18.0, *) else { return }
      await AppleTranslationCoordinator.shared.cancel(
        contentIds: contentIds,
        reason: reason ?? "js_cancel_content"
      )
    }

    /// Called on logout / account switch / security reset (Stage 6). Fails every
    /// pending job as cancelled, which by contract does not fall through to the
    /// cloud.
    AsyncFunction("reset") { (reason: String?) async throws in
      guard #available(iOS 18.0, *) else { return }
      await AppleTranslationCoordinator.shared.reset(reason: reason ?? "js_reset")
    }

    AsyncFunction("getDiagnostics") { () async throws -> [String: Any] in
      guard #available(iOS 18.0, *) else {
        return ["isAvailable": false, "isHostMounted": false, "activeHosts": 0]
      }
      return await MainActor.run {
        let coordinator = AppleTranslationCoordinator.shared
        return [
          "isAvailable": true,
          "isHostMounted": coordinator.isHostMounted,
          "activeHosts": coordinator.hosts.count
        ]
      }
    }

    // MARK: The mounted SwiftUI host

    View(AppleTranslationHostView.self) {}
  }

  private static var isFrameworkAvailable: Bool {
    if #available(iOS 18.0, *) { return true }
    return false
  }

  private static func failure(
    _ failure: AppleTranslationFailure,
    requestId: String,
    detail: String? = nil
  ) -> [String: Any] {
    var payload: [String: Any] = [
      "ok": false,
      "requestId": requestId,
      "code": failure.rawValue,
      "recoverable": failure.isRecoverable,
      "permitsCloudFallback": failure.permitsCloudFallback
    ]
    if let detail { payload["detail"] = detail }
    return payload
  }
}

/// JS request shape. Field names are the wire contract with
/// `src/services/translation/appleProvider.ts`.
public struct AppleTranslationRequest: Record {
  /// Unique per user action; echoed back so a recycled cell can discard a
  /// response that is no longer its own.
  @Field public var requestId: String = ""
  /// Stable content identity, e.g. `post:1234`. Used for bulk cancellation.
  @Field public var contentId: String = ""
  @Field public var contentVersion: String?
  @Field public var text: String = ""
  /// Omit or pass "auto" to let Apple detect the source language.
  @Field public var sourceLanguage: String?
  @Field public var targetLanguage: String = ""
  /// Must be an explicit user decision. Defaults false so a feed render can
  /// never trigger Apple's download sheet.
  @Field public var allowDownload: Bool = false

  public init() {}
}
