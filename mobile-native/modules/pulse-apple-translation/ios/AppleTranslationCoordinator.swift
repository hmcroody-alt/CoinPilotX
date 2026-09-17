import Combine
import Foundation
import Translation

// Stage 2 core. Owns the queue, deduplication, cancellation and result
// correlation for Apple's on-device translation — but deliberately does NOT own
// a `TranslationSession`.
//
// Apple only vends a session through `.translationTask(_:action:)` on a mounted
// SwiftUI view, and the session dies with that view's task. So the ownership
// split is:
//
//   * This coordinator is a long-lived queue. It publishes `hosts`, the minimum
//     set of (source, target) pairs that currently have work.
//   * `AppleTranslationHostRoot` renders one `.translationTask` per published
//     host and calls `run(session:descriptor:)`. The session reference exists
//     only inside that call and is cleared in its `defer`.
//
// That is why there is no session singleton here: a stored session would be a
// dangling handle the moment SwiftUI tore the host down.

@available(iOS 18.0, *)
@MainActor
final class AppleTranslationCoordinator: ObservableObject {
  static let shared = AppleTranslationCoordinator()

  // Tunables live in `AppleTranslationLimits` so the Expo module can publish
  // them to JS without tripping this class's iOS 18 availability gate.
  private static let maxActivePairs = AppleTranslationLimits.maxActivePairs
  private static let maxTextLength = AppleTranslationLimits.maxTextLength
  private static let hostMountTimeout = AppleTranslationLimits.hostMountTimeout
  private static let translateTimeout = AppleTranslationLimits.translateTimeout
  private static let pairIdleTimeout = AppleTranslationLimits.pairIdleTimeout
  private static let statusCacheTTL = AppleTranslationLimits.statusCacheTTL

  /// Identifies one mounted host. `generation` exists so a wedged session can be
  /// replaced: bumping it changes the SwiftUI identity, which tears the old
  /// `.translationTask` down and starts a fresh one.
  struct HostDescriptor: Hashable {
    let pair: TranslationPairKey
    let generation: Int
  }

  @Published private(set) var hosts: [HostDescriptor] = []

  /// True while at least one host view is attached. The JS side reads this to
  /// distinguish "no native module" from "module present, host not mounted".
  private(set) var isHostMounted = false

  private struct Job {
    let request: AppleTranslationJobRequest
    let digest: String
    let enqueuedAt: Date
    let continuation: CheckedContinuation<AppleTranslationJobResult, Error>
  }

  private final class PairState {
    /// FIFO of request ids awaiting a session.
    var queued: [String] = []
    /// Parked `nextRequestId` consumer, resumed by `wake(_:)`.
    var waiter: CheckedContinuation<Void, Never>?
    var generation = 0
    /// Which `run` call currently owns this pair. Guards against an outgoing
    /// host's `defer` clobbering an incoming host's state.
    var runToken = 0
    /// Held ONLY for the duration of `run(session:descriptor:)` and cleared in
    /// its `defer`, so its lifetime never exceeds the SwiftUI view's task.
    var session: TranslationSession?
    var lastActivity = Date()
  }

  private var pairs: [TranslationPairKey: PairState] = [:]
  private var jobs: [String: Job] = [:]
  /// digest -> leader request id. Collapses rapid repeated taps and two feed
  /// cells showing the same text into a single call into Apple.
  private var leaderByDigest: [String: String] = [:]
  /// leader request id -> follower request ids.
  private var followers: [String: [String]] = [:]
  /// Request ids cancelled while in flight; their Apple result is discarded.
  private var cancelledRequestIds: Set<String> = []
  private var statusCache: [TranslationPairKey: (status: AppleLanguageStatus, at: Date)] = [:]
  private var nextRunToken = 1
  private var mountedHostCount = 0

  private init() {}

  // MARK: - Host lifecycle

  func hostDidMount() {
    mountedHostCount += 1
    isHostMounted = true
  }

  /// The host view left the tree (screen dismissed, account switched). Every
  /// pending job is failed as cancelled rather than left hanging, and no cloud
  /// fallback is triggered for them — `requestCanceled.permitsCloudFallback` is
  /// false precisely so an unmount cannot bill us.
  ///
  /// Reference counted: React Native mounts the replacement host *before*
  /// unmounting the outgoing one when the `key` changes, so an unguarded reset
  /// here would wipe the incoming host's queue.
  func hostDidUnmount() {
    mountedHostCount = max(0, mountedHostCount - 1)
    guard mountedHostCount == 0 else { return }
    isHostMounted = false
    reset(reason: "host_unmounted")
  }

  func reset(reason: String) {
    let pending = Array(jobs.keys)
    for pair in pairs.keys { wake(pair) }
    pairs.removeAll()
    hosts.removeAll()
    statusCache.removeAll()
    for requestId in pending {
      finish(requestId, .failure(AppleTranslationError(.requestCanceled, detail: reason)))
    }
    leaderByDigest.removeAll()
    followers.removeAll()
    cancelledRequestIds.removeAll()
  }

  // MARK: - Public API used by the Expo module

  /// Enqueues one translation and suspends until it succeeds, fails with a
  /// typed reason, or is cancelled.
  ///
  /// `request.source` / `request.target` must already be canonical BCP-47 (the
  /// module normalises, so that `same_language` and `invalid_language` can be
  /// answered without ever mounting a host).
  func translate(_ request: AppleTranslationJobRequest) async throws -> AppleTranslationJobResult {
    let trimmed = request.text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else {
      throw AppleTranslationError(.invalidText, detail: "empty")
    }
    guard request.text.count <= Self.maxTextLength else {
      throw AppleTranslationError(.invalidText, detail: "length_\(request.text.count)")
    }

    let digest = TranslationDigest.make(text: request.text, pair: request.pair)

    return try await withTaskCancellationHandler {
      try await withCheckedThrowingContinuation { continuation in
        let job = Job(
          request: request,
          digest: digest,
          enqueuedAt: Date(),
          continuation: continuation
        )
        jobs[request.requestId] = job

        // Deduplicate against an identical in-flight request instead of paying
        // Apple twice. The follower still gets its own request id back, so a
        // recycled cell can still tell whose result this is.
        if let leaderId = leaderByDigest[digest],
           leaderId != request.requestId,
           jobs[leaderId] != nil {
          followers[leaderId, default: []].append(request.requestId)
          return
        }

        leaderByDigest[digest] = request.requestId
        enqueue(request.requestId, pair: request.pair)
      }
    } onCancel: {
      Task { @MainActor [weak self] in
        self?.cancel(requestIds: [request.requestId], reason: "js_task_cancelled")
      }
    }
  }

  func cancel(requestIds: [String], reason: String) {
    for requestId in requestIds {
      guard let job = jobs[requestId] else { continue }
      cancelledRequestIds.insert(requestId)
      if let index = pairs[job.request.pair]?.queued.firstIndex(of: requestId) {
        pairs[job.request.pair]?.queued.remove(at: index)
      } else if #available(iOS 26.0, *), let session = pairs[job.request.pair]?.session {
        // Only iOS 26 can actually stop work already handed to Apple. On 18.x
        // we simply drop the response; see `process`.
        session.cancel()
      }
      finish(requestId, .failure(AppleTranslationError(.requestCanceled, detail: reason)))
    }
  }

  /// Bulk cancellation for content that scrolled away or unmounted (Stage 7).
  func cancel(contentIds: [String], reason: String) {
    let targets = Set(contentIds)
    let matching = jobs.values
      .filter { targets.contains($0.request.contentId) }
      .map { $0.request.requestId }
    cancel(requestIds: matching, reason: reason)
  }

  func availabilityStatus(source: String?, target: String) async -> AppleLanguageStatus {
    let pair = TranslationPairKey(source: source, target: target)
    if let cached = statusCache[pair], Date().timeIntervalSince(cached.at) < Self.statusCacheTTL {
      return cached.status
    }
    let status = await Self.probeStatus(pair: pair, text: nil)
    // Auto-detect results depend on the text, so only explicit pairs are cached.
    if source != nil {
      statusCache[pair] = (status, Date())
    }
    return status
  }

  func supportedLanguages() async -> [String] {
    let languages = await LanguageAvailability().supportedLanguages
    return languages.map { $0.minimalIdentifier }.sorted()
  }

  // MARK: - Session loop, driven by AppleTranslationHostRoot

  /// Consumes this pair's queue for as long as SwiftUI keeps the host's task
  /// alive. Returns when the task is cancelled, at which point the session is
  /// released.
  func run(session: TranslationSession, descriptor: HostDescriptor) async {
    let pair = descriptor.pair
    // Attach to an existing pair only. `hosts` is published *by* this
    // coordinator, so the pair must already exist; if `reset` or `retire`
    // removed it while SwiftUI was mounting the task, creating it again here
    // would republish a host with no work and hold its session open forever.
    guard let state = pairs[pair], state.generation == descriptor.generation else { return }

    let token = nextRunToken
    nextRunToken += 1
    state.runToken = token
    state.session = session
    state.lastActivity = Date()
    isHostMounted = true

    defer {
      if let current = pairs[pair], current.runToken == token {
        current.session = nil
      }
    }

    while !Task.isCancelled {
      guard let requestId = await nextRequestId(for: pair, token: token) else { break }
      await process(requestId: requestId, pair: pair, session: session)
      scheduleIdleRetirement(for: pair)
    }
  }

  // MARK: - Queue plumbing

  private func ensurePair(_ pair: TranslationPairKey) -> PairState {
    if let existing = pairs[pair] { return existing }
    let state = PairState()
    pairs[pair] = state
    evictIdlePairIfNeeded(keeping: pair)
    hosts.append(HostDescriptor(pair: pair, generation: state.generation))
    return state
  }

  private func enqueue(_ requestId: String, pair: TranslationPairKey) {
    let state = ensurePair(pair)
    state.queued.append(requestId)
    state.lastActivity = Date()
    wake(pair)
    scheduleHostMountWatchdog(requestId: requestId, pair: pair)
    scheduleJobWatchdog(requestId: requestId, pair: pair)
  }

  private func wake(_ pair: TranslationPairKey) {
    guard let state = pairs[pair], let waiter = state.waiter else { return }
    state.waiter = nil
    waiter.resume()
  }

  private func nextRequestId(for pair: TranslationPairKey, token: Int) async -> String? {
    while true {
      if Task.isCancelled { return nil }
      guard let state = pairs[pair], state.runToken == token else { return nil }
      if !state.queued.isEmpty {
        return state.queued.removeFirst()
      }
      await withTaskCancellationHandler {
        await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
          guard !Task.isCancelled,
                let state = pairs[pair],
                state.runToken == token,
                state.queued.isEmpty else {
            continuation.resume()
            return
          }
          if let previous = state.waiter {
            state.waiter = nil
            previous.resume()
          }
          state.waiter = continuation
        }
      } onCancel: {
        Task { @MainActor [weak self] in self?.wake(pair) }
      }
    }
  }

  private func evictIdlePairIfNeeded(keeping: TranslationPairKey) {
    guard hosts.count >= Self.maxActivePairs else { return }
    let idle = pairs
      .filter { $0.key != keeping && $0.value.queued.isEmpty }
      .min { $0.value.lastActivity < $1.value.lastActivity }
    guard let victim = idle?.key else { return }
    retire(victim)
  }

  private func retire(_ pair: TranslationPairKey) {
    guard let state = pairs[pair], state.queued.isEmpty else { return }
    wake(pair)
    pairs.removeValue(forKey: pair)
    hosts.removeAll { $0.pair == pair }
  }

  private func scheduleIdleRetirement(for pair: TranslationPairKey) {
    let deadline = Self.pairIdleTimeout
    Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: UInt64(deadline * 1_000_000_000))
      guard let self, let state = self.pairs[pair] else { return }
      guard state.queued.isEmpty,
            Date().timeIntervalSince(state.lastActivity) >= deadline else { return }
      self.retire(pair)
    }
  }

  /// Fails a job that never found a session. Fires only while no host is
  /// attached — a queued job behind a busy session is not a mounting failure.
  private func scheduleHostMountWatchdog(requestId: String, pair: TranslationPairKey) {
    Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: UInt64(Self.hostMountTimeout * 1_000_000_000))
      guard let self, self.jobs[requestId] != nil else { return }
      guard self.pairs[pair]?.session == nil else { return }
      self.finish(
        requestId,
        .failure(AppleTranslationError(
          .translationSessionUnavailable,
          detail: self.isHostMounted ? "session_not_vended" : "host_not_mounted"
        ))
      )
    }
  }

  /// Backstop for a session that accepted work and never answered. Also bumps
  /// the host generation so SwiftUI rebuilds the `.translationTask` and the pair
  /// is not wedged for the rest of the app's life.
  private func scheduleJobWatchdog(requestId: String, pair: TranslationPairKey) {
    let budget = Self.hostMountTimeout + Self.translateTimeout
    Task { @MainActor [weak self] in
      try? await Task.sleep(nanoseconds: UInt64(budget * 1_000_000_000))
      guard let self, self.jobs[requestId] != nil else { return }
      self.finish(requestId, .failure(AppleTranslationError(.timeout, detail: "native_watchdog")))
      self.bumpGeneration(for: pair)
    }
  }

  private func bumpGeneration(for pair: TranslationPairKey) {
    guard let state = pairs[pair] else { return }
    state.generation += 1
    state.session = nil
    wake(pair)
    if let index = hosts.firstIndex(where: { $0.pair == pair }) {
      hosts[index] = HostDescriptor(pair: pair, generation: state.generation)
    }
  }

  // MARK: - Work

  private func process(
    requestId: String,
    pair: TranslationPairKey,
    session: TranslationSession
  ) async {
    guard let job = jobs[requestId] else { return }
    if cancelledRequestIds.contains(requestId) {
      finish(requestId, .failure(AppleTranslationError(.requestCanceled, detail: "pre_dispatch")))
      return
    }
    pairs[pair]?.lastActivity = Date()

    let started = Date()
    var downloadPrepared = false

    do {
      switch await Self.probeStatus(pair: pair, text: pair.source == nil ? job.request.text : nil) {
      case .unsupported:
        throw AppleTranslationError(.unsupportedLanguagePair, detail: pair.debugDescription)
      case .invalid:
        throw AppleTranslationError(.invalidLanguage, detail: pair.debugDescription)
      case .sameLanguage:
        throw AppleTranslationError(.sameLanguage, detail: pair.debugDescription)
      case .supportedDownloadRequired:
        guard job.request.allowDownload else {
          throw AppleTranslationError(.modelNotInstalled, detail: pair.debugDescription)
        }
        try await session.prepareTranslation()
        downloadPrepared = true
      case .installed, .temporarilyUnavailable:
        break
      }

      try Task.checkCancellation()

      let appleRequest = TranslationSession.Request(
        sourceText: job.request.text,
        clientIdentifier: requestId
      )
      let responses = try await session.translations(from: [appleRequest])

      // Correlate on Apple's own client identifier rather than on array
      // position. This is what makes a recycled feed cell safe.
      guard let response = responses.first(where: { $0.clientIdentifier == requestId }) else {
        throw AppleTranslationError(.providerFailure, detail: "client_identifier_mismatch")
      }

      // iOS 18 has no `session.cancel()`, so a cancellation that landed while
      // Apple was working is enforced here by discarding the result.
      if cancelledRequestIds.contains(requestId) {
        finish(requestId, .failure(AppleTranslationError(.requestCanceled, detail: "post_dispatch")))
        return
      }

      finish(requestId, .success(AppleTranslationJobResult(
        requestId: requestId,
        contentId: job.request.contentId,
        contentVersion: job.request.contentVersion,
        translatedText: response.targetText,
        detectedSourceLanguage: response.sourceLanguage.minimalIdentifier,
        targetLanguage: response.targetLanguage.minimalIdentifier,
        durationMs: Int(Date().timeIntervalSince(started) * 1000),
        deduplicated: false,
        downloadPrepared: downloadPrepared
      )))
    } catch let error as AppleTranslationError {
      finish(requestId, .failure(error))
    } catch {
      finish(
        requestId,
        .failure(AppleTranslationErrorMapper.map(error, allowDownload: job.request.allowDownload))
      )
    }
  }

  /// Idempotent: the first caller resolves the continuation, later callers
  /// (watchdogs, cancellation, the real result) find nothing and no-op. That is
  /// what makes the watchdogs safe to fire unconditionally.
  private func finish(_ requestId: String, _ outcome: Result<AppleTranslationJobResult, Error>) {
    guard let job = jobs.removeValue(forKey: requestId) else { return }
    if leaderByDigest[job.digest] == requestId {
      leaderByDigest.removeValue(forKey: job.digest)
    }
    cancelledRequestIds.remove(requestId)
    job.continuation.resume(with: outcome)

    for followerId in followers.removeValue(forKey: requestId) ?? [] {
      guard let follower = jobs.removeValue(forKey: followerId) else { continue }
      cancelledRequestIds.remove(followerId)
      if leaderByDigest[follower.digest] == followerId {
        leaderByDigest.removeValue(forKey: follower.digest)
      }
      switch outcome {
      case .success(let result):
        follower.continuation.resume(returning: AppleTranslationJobResult(
          requestId: followerId,
          contentId: follower.request.contentId,
          contentVersion: follower.request.contentVersion,
          translatedText: result.translatedText,
          detectedSourceLanguage: result.detectedSourceLanguage,
          targetLanguage: result.targetLanguage,
          durationMs: result.durationMs,
          deduplicated: true,
          downloadPrepared: result.downloadPrepared
        ))
      case .failure(let error):
        follower.continuation.resume(throwing: error)
      }
    }
  }

  // MARK: - Availability

  /// Classifies a pair using Apple's programmatic API. Never a hardcoded list —
  /// Stage 4 requires the device to be the authority, which is also what lets
  /// Haitian Creole be answered honestly rather than guessed.
  private static func probeStatus(pair: TranslationPairKey, text: String?) async -> AppleLanguageStatus {
    let target = Locale.Language(identifier: pair.target)
    let availability = LanguageAvailability()

    if let source = pair.source {
      if LanguageNormalizer.modelScope(source) == LanguageNormalizer.modelScope(pair.target) {
        return .sameLanguage
      }
      let status = await availability.status(
        from: Locale.Language(identifier: source),
        to: target
      )
      return classify(status)
    }

    // Auto-detect: Apple needs the text to decide.
    guard let text, !text.isEmpty else { return .temporarilyUnavailable }
    do {
      return classify(try await availability.status(for: text, to: target))
    } catch {
      // Detection failure is not the same as an unsupported pair; leaving it
      // `temporarilyUnavailable` lets `process` try anyway rather than falsely
      // reporting that Apple does not support the language.
      return .temporarilyUnavailable
    }
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
