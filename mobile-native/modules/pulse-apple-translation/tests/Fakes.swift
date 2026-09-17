import Combine
import Foundation

// Stand-ins for Apple's session and availability APIs, plus a rig that plays the
// part SwiftUI plays in production.

/// A scriptable `AppleTranslationEngine`.
///
/// Records what it was asked, so a test can assert on the thing that actually
/// matters for cost and privacy: whether Apple was called at all, once, or
/// twice.
@MainActor
final class FakeTranslationEngine: AppleTranslationEngine {
  enum Behavior {
    /// Returns `<target>:<sourceText>` tagged with the identifier it was given.
    case echo
    /// Answers with a fixed identifier regardless of what was asked — the
    /// recycled-cell hazard.
    case wrongIdentifier(String)
    /// Answers with a literal response list, for out-of-order and extra-response
    /// cases.
    case fixed([TranslationEngineResponse])
    /// Never returns.
    case hang
    /// Blocks until the gate opens, then echoes.
    case gated(AsyncGate)
    case failing(Error)
  }

  var behavior: Behavior = .echo
  var prepareResult: Result<Void, Error> = .success(())
  /// Answered as Apple's detected source language. Non-nil even when the caller
  /// asked for automatic detection, which is the only way the UI can say
  /// "Translated from French".
  var detectedSourceLanguage: String? = "fr"
  var targetLanguage: String?

  private(set) var translateCalls: [[TranslationEngineRequest]] = []
  private(set) var prepareCount = 0
  private(set) var cancelInFlightCount = 0
  /// Held so a never-resumed continuation is never deallocated, which would
  /// otherwise trip the checked-continuation runtime warning.
  private var parked: [CheckedContinuation<Void, Never>] = []

  var translatedIdentifiers: [String] {
    translateCalls.flatMap { $0.map(\.clientIdentifier) }
  }

  func prepare() async throws {
    prepareCount += 1
    try prepareResult.get()
  }

  func translate(
    _ requests: [TranslationEngineRequest]
  ) async throws -> [TranslationEngineResponse] {
    translateCalls.append(requests)
    // Waiting is decided on entry; the answer is decided on the way out. A test
    // that holds a call at the gate and then rewrites `behavior` is scripting
    // the *response*, and reading the behavior once on entry would discard that
    // rewrite and echo instead — silently passing a test that meant to prove a
    // leader's failure reaches its followers.
    if case .gated(let gate) = behavior {
      await gate.wait()
    }
    return try await respond(to: requests)
  }

  private func respond(
    to requests: [TranslationEngineRequest]
  ) async throws -> [TranslationEngineResponse] {
    switch behavior {
    case .echo, .gated:
      return requests.map(echo)
    case .wrongIdentifier(let identifier):
      return requests.map { request in
        TranslationEngineResponse(
          clientIdentifier: identifier,
          targetText: "leaked:\(request.sourceText)",
          sourceLanguage: detectedSourceLanguage,
          targetLanguage: targetLanguage
        )
      }
    case .fixed(let responses):
      return responses
    case .hang:
      await withCheckedContinuation { continuation in parked.append(continuation) }
      return []
    case .failing(let error):
      throw error
    }
  }

  func cancelInFlight() {
    cancelInFlightCount += 1
  }

  private func echo(_ request: TranslationEngineRequest) -> TranslationEngineResponse {
    TranslationEngineResponse(
      clientIdentifier: request.clientIdentifier,
      targetText: "translated:\(request.sourceText)",
      sourceLanguage: detectedSourceLanguage,
      targetLanguage: targetLanguage
    )
  }
}

/// A scriptable `AppleTranslationAvailability` that also counts how often it was
/// consulted, which is how the cache and same-language short-circuit tests prove
/// the device was *not* asked.
@MainActor
final class FakeAvailability: AppleTranslationAvailability {
  var defaultStatus: AppleLanguageStatus = .installed
  var statusByPair: [TranslationPairKey: AppleLanguageStatus] = [:]
  var languages: [String] = []

  private(set) var queries: [(pair: TranslationPairKey, text: String?)] = []

  var queryCount: Int { queries.count }

  func status(pair: TranslationPairKey, text: String?) async -> AppleLanguageStatus {
    queries.append((pair, text))
    return statusByPair[pair] ?? defaultStatus
  }

  func supportedLanguages() async -> [String] {
    languages
  }
}

/// Plays SwiftUI's part.
///
/// In production `AppleTranslationHostRoot` renders `ForEach(coordinator.hosts)`
/// and each element's `.translationTask` calls `run(engine:descriptor:)`. This
/// subscribes to the same published array and starts or cancels a run task per
/// descriptor, which reproduces SwiftUI's identity semantics — including the
/// part that matters for the watchdog test, where bumping a pair's generation
/// produces a *new* descriptor and therefore a new session.
@MainActor
final class HostRig {
  let coordinator: AppleTranslationCoordinator
  let availability = FakeAvailability()

  private var runners: [AppleTranslationCoordinator.HostDescriptor: Task<Void, Never>] = [:]
  private var subscription: AnyCancellable?
  private var enginesByPair: [TranslationPairKey: FakeTranslationEngine] = [:]
  private let mountsHosts: Bool

  /// Applied to every engine as it is created, so a test can script behaviour
  /// before the pair it belongs to exists.
  var configureEngine: (FakeTranslationEngine) -> Void = { _ in }

  /// Descriptors this rig has ever been asked to mount, in order. Lets a test
  /// see a generation bump rather than only its result.
  private(set) var mountedDescriptors: [AppleTranslationCoordinator.HostDescriptor] = []

  init(
    timings: AppleTranslationCoordinator.Timings = HostRig.fastTimings,
    mountsHosts: Bool = true
  ) {
    self.mountsHosts = mountsHosts
    coordinator = AppleTranslationCoordinator(availability: availability, timings: timings)
    if mountsHosts {
      coordinator.hostDidMount()
      subscription = coordinator.$hosts.sink { [weak self] descriptors in
        // `@Published` fires before the array is committed, so reconcile from
        // the value handed in, one hop later.
        Task { @MainActor [weak self] in self?.reconcile(descriptors) }
      }
    }
  }

  /// Long enough that a scheduler hiccup does not read as a timeout, short
  /// enough that a genuinely stuck queue fails the run in under a second.
  static let fastTimings = AppleTranslationCoordinator.Timings(
    hostMount: 0.4,
    translate: 2.0,
    pairIdle: 3600,
    statusCacheTTL: 3600
  )

  func engine(for pair: TranslationPairKey) -> FakeTranslationEngine {
    if let existing = enginesByPair[pair] { return existing }
    let engine = FakeTranslationEngine()
    configureEngine(engine)
    enginesByPair[pair] = engine
    return engine
  }

  var soleEngine: FakeTranslationEngine? {
    enginesByPair.count == 1 ? enginesByPair.values.first : nil
  }

  var totalTranslateCalls: Int {
    enginesByPair.values.reduce(0) { $0 + $1.translateCalls.count }
  }

  private func reconcile(_ descriptors: [AppleTranslationCoordinator.HostDescriptor]) {
    let wanted = Set(descriptors)
    for (descriptor, task) in runners where !wanted.contains(descriptor) {
      task.cancel()
      runners.removeValue(forKey: descriptor)
    }
    for descriptor in descriptors where runners[descriptor] == nil {
      mountedDescriptors.append(descriptor)
      let engine = engine(for: descriptor.pair)
      runners[descriptor] = Task { @MainActor in
        await coordinator.run(engine: engine, descriptor: descriptor)
      }
    }
  }

  func teardown() {
    subscription = nil
    for task in runners.values { task.cancel() }
    runners.removeAll()
  }
}

// MARK: - Request construction

func makeRequest(
  id: String = "req-1",
  contentId: String = "post:1",
  contentVersion: String? = nil,
  text: String = "bonjour",
  source: String? = "fr",
  target: String = "en",
  allowDownload: Bool = false
) -> AppleTranslationJobRequest {
  AppleTranslationJobRequest(
    requestId: id,
    contentId: contentId,
    contentVersion: contentVersion,
    text: text,
    source: source,
    target: target,
    allowDownload: allowDownload
  )
}
