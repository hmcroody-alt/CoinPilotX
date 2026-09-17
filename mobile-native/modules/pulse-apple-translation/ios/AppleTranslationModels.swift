import Foundation

// Pure value types and BCP-47 normalisation for the Apple on-device translation
// bridge. This file deliberately imports neither ExpoModulesCore nor Translation
// so it can be type-checked and unit-tested standalone, and so nothing here
// carries an iOS 18 availability requirement.

/// Tunables shared by the bridge and by JS. Kept here, outside any availability
/// gate, so the Expo module can publish them as constants on iOS 15–17 too.
enum AppleTranslationLimits {
  /// Per-request character ceiling. Longer text is rejected before it reaches
  /// Apple so one pathological post cannot stall a pair's serial queue.
  static let maxTextLength = 5_000
  /// Ceiling on concurrently mounted sessions. Stage 7 forbids a session per
  /// message bubble; in practice a user sees at most a couple of source
  /// languages at once, so four is generous.
  static let maxActivePairs = 4
  /// How long a queued job waits for an `AppleTranslationHost` to mount before
  /// failing. Without this a build that forgot to mount the host would hang the
  /// Translate button forever instead of falling back.
  static let hostMountTimeout: TimeInterval = 8
  /// Ceiling on one translate once a session exists. Excludes model download,
  /// which is user-driven and may legitimately take minutes.
  static let translateTimeout: TimeInterval = 25
  /// A pair with no work for this long is retired so its session is released.
  static let pairIdleTimeout: TimeInterval = 45
  /// How long an availability verdict for an explicit pair is trusted.
  static let statusCacheTTL: TimeInterval = 60
}

/// The (source, target) language pair that identifies one translation session.
///
/// `source == nil` means "let Apple detect the source language" and is a
/// *distinct* session from any explicit source, because
/// `TranslationSession.Configuration(source: nil, ...)` behaves differently
/// from `Configuration(source: .some, ...)`.
struct TranslationPairKey: Hashable {
  let source: String?
  let target: String

  var debugDescription: String {
    "\(source ?? "auto")->\(target)"
  }
}

/// One unit of work handed to the native bridge from JS.
struct AppleTranslationJobRequest {
  /// Unique per user action. Echoed back so a recycled feed cell can discard a
  /// response that belongs to the item it used to be showing.
  let requestId: String
  /// Stable identity of the translated content (e.g. `post:1234`). Used for
  /// bulk cancellation when content scrolls away or is unmounted.
  let contentId: String
  /// Bumped when the underlying content is edited. Carried through so the JS
  /// cache layer can invalidate; the native side only echoes it.
  let contentVersion: String?
  let text: String
  let source: String?
  let target: String
  /// When false, a pair that needs a model download fails fast instead of
  /// triggering Apple's download UI. Lets the UI ask first (Stage 5).
  let allowDownload: Bool

  var pair: TranslationPairKey {
    TranslationPairKey(source: source, target: target)
  }
}

struct AppleTranslationJobResult {
  let requestId: String
  let contentId: String
  let contentVersion: String?
  let translatedText: String
  /// Apple's detected source language, present even when `source` was nil.
  let detectedSourceLanguage: String?
  let targetLanguage: String
  let durationMs: Int
  /// True when this result was produced by another in-flight identical request
  /// rather than by a second call into Apple.
  let deduplicated: Bool
  /// True when a model download had to be prepared before translating.
  let downloadPrepared: Bool
}

/// Stage 4 availability classification. Raw values are the wire contract shared
/// with TypeScript — do not rename without updating
/// `src/services/translation/types.ts`.
enum AppleLanguageStatus: String {
  case installed
  case supportedDownloadRequired = "supported_download_required"
  case unsupported
  case sameLanguage = "same_language"
  case invalid
  case temporarilyUnavailable = "temporarily_unavailable"
}

/// BCP-47 canonicalisation.
///
/// Apple keys language models by `Locale.Language`, so `fr_FR`, `FR` and `fr-fr`
/// must all collapse onto one session or we would mount three hosts for one
/// language pair and cache-miss against ourselves.
enum LanguageNormalizer {
  /// Values that mean "unspecified"; these become `nil` (auto-detect).
  private static let autoTokens: Set<String> = ["", "auto", "und", "unknown", "zxx", "mul"]

  /// Codes that Apple/ICU canonicalise away. Left of the arrow still appears in
  /// PulseSoc's own stored user preferences and in legacy Google responses.
  private static let legacyPrimary: [String: String] = [
    "iw": "he",
    "in": "id",
    "ji": "yi",
    "mo": "ro",
    "no": "nb",
    "sh": "sr",
    "tl": "fil"
  ]

  /// Region-flavoured Chinese must become script-flavoured or Apple treats it as
  /// unsupported.
  private static let chineseRegionToScript: [String: String] = [
    "cn": "Hans",
    "sg": "Hans",
    "tw": "Hant",
    "hk": "Hant",
    "mo": "Hant"
  ]

  /// Returns a canonical BCP-47 tag, `nil` for auto-detect, or throws nothing —
  /// invalid input returns `.failure`.
  enum Outcome: Equatable {
    /// Canonical tag, e.g. `fr`, `pt-BR`, `zh-Hans`, `ht`.
    case language(String)
    /// Caller asked for automatic source detection.
        case automatic
    /// Not a parseable language tag.
    case invalid
  }

  static func normalize(_ raw: String?) -> Outcome {
    guard let raw else { return .automatic }
    let cleaned = raw
      .trimmingCharacters(in: .whitespacesAndNewlines)
      .replacingOccurrences(of: "_", with: "-")
    if autoTokens.contains(cleaned.lowercased()) { return .automatic }

    var parts = cleaned.split(separator: "-", omittingEmptySubsequences: true).map(String.init)
    guard !parts.isEmpty else { return .automatic }

    var primary = parts.removeFirst().lowercased()
    guard isValidPrimarySubtag(primary) else { return .invalid }
    if let mapped = legacyPrimary[primary] { primary = mapped }

    var script: String?
    var region: String?
    var variants: [String] = []

    for part in parts {
      if part.count == 4, part.allSatisfy({ $0.isLetter }), script == nil {
        script = part.lowercased().capitalizedASCII
      } else if region == nil, part.count == 2, part.allSatisfy({ $0.isLetter }) {
        region = part.uppercased()
      } else if region == nil, part.count == 3, part.allSatisfy({ $0.isNumber }) {
        region = part
      } else {
        variants.append(part.lowercased())
      }
    }

    // zh-CN / zh-TW carry their script in the region slot.
    if primary == "zh", script == nil, let region, let mapped = chineseRegionToScript[region.lowercased()] {
      script = mapped
      // Keep the region: Apple accepts zh-Hant-TW and it is more specific.
    }

    var tag = primary
    if let script { tag += "-" + script }
    if let region { tag += "-" + region }
    for variant in variants { tag += "-" + variant }
    return .language(tag)
  }

  /// The subtag Apple matches models on — primary + script, region dropped.
  /// `pt-BR` and `pt-PT` share a model; keeping the region would mount two hosts.
  static func modelScope(_ tag: String) -> String {
    let parts = tag.split(separator: "-").map(String.init)
    guard let primary = parts.first else { return tag }
    if parts.count > 1, parts[1].count == 4 {
      return primary + "-" + parts[1]
    }
    return primary
  }

  private static func isValidPrimarySubtag(_ value: String) -> Bool {
    guard value.count >= 2, value.count <= 8 else { return false }
    return value.allSatisfy { $0.isLetter && $0.isASCII }
  }
}

private extension String {
  /// `hans` -> `Hans` without pulling in locale-sensitive capitalisation.
  var capitalizedASCII: String {
    guard let first else { return self }
    return String(first).uppercased() + String(dropFirst())
  }
}

/// Digest used only to deduplicate identical *in-flight* requests.
///
/// `Hasher` is seeded per process, so this value is deliberately NOT stable
/// across launches and must never be persisted or used as a cache key — the
/// durable cache key is computed in TypeScript. It never contains the raw text
/// (Stage 6: no raw private text in cache keys or telemetry).
enum TranslationDigest {
  static func make(text: String, pair: TranslationPairKey) -> String {
    var hasher = Hasher()
    hasher.combine(text)
    hasher.combine(pair.source ?? "auto")
    hasher.combine(pair.target)
    return String(UInt(bitPattern: hasher.finalize()), radix: 36)
  }
}
