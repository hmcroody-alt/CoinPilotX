import Foundation

// The queue, the deduplication, the cancellation and the correlation.
//
// This is the part of the module that decides whether the right words appear
// under the right post, and it is the part that could not be tested at all until
// `AppleTranslationEngine` existed, because its only entry point demanded a
// `TranslationSession` and there is no way to make one.
//
// `HostRig` plays SwiftUI's part: it subscribes to the published `hosts` array
// and starts or cancels one run loop per descriptor, which is exactly what
// `ForEach` + `.translationTask` does in `AppleTranslationHost.swift`.

@MainActor
func runCoordinatorQueueTests(_ run: TestRun) async {
  run.suite("AppleTranslationCoordinator — queue")

  await run.test("translates and reports the language Apple actually read") {
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = { $0.detectedSourceLanguage = "fr" }

    let result = try await rig.coordinator.translate(
      makeRequest(id: "r1", contentId: "post:9", contentVersion: "v3", text: "bonjour", source: nil)
    )

    run.expectEqual(result.translatedText, "translated:bonjour", "translated text")
    run.expectEqual(result.requestId, "r1", "echoes the request id")
    run.expectEqual(result.contentId, "post:9", "echoes the content id")
    run.expectEqual(result.contentVersion, "v3", "echoes the content version")
    run.expectEqual(result.detectedSourceLanguage, "fr", "detected source language")
    run.expectEqual(result.targetLanguage, "en", "target language")
    run.expect(!result.deduplicated, "a lone request is not a duplicate")
    run.expect(!result.downloadPrepared, "an installed model needs no download")
  }

  await run.test("falls back to the requested target when the engine names none") {
    // Apple echoes the target back, but a `nil` there must not surface as an
    // empty language on the "Translated from X" label.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = { $0.targetLanguage = nil }

    let result = try await rig.coordinator.translate(makeRequest(target: "es"))
    run.expectEqual(result.targetLanguage, "es", "target language")
  }

  await run.test("runs one pair's work serially through a single session") {
    // Stage 7 forbids a session per message bubble. Three requests on one pair
    // must queue behind one another, and while the first is in flight the engine
    // must have been entered exactly once.
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    var tasks: [Task<AppleTranslationJobResult, Error>] = []
    for index in 1...3 {
      let request = makeRequest(id: "r\(index)", contentId: "post:\(index)", text: "texte \(index)")
      tasks.append(Task { try await rig.coordinator.translate(request) })
      await Task.yield()
      await Task.yield()
    }

    try await waitUntil("first request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    run.expectEqual(rig.coordinator.hosts.count, 1, "one host for one pair")
    run.expectEqual(rig.soleEngine?.translateCalls.count, 1, "only one call in flight")

    gate.open()
    var texts: [String] = []
    for task in tasks { texts.append(try await task.value.translatedText) }

    run.expectEqual(
      rig.soleEngine?.translatedIdentifiers,
      ["r1", "r2", "r3"],
      "served in the order they were asked"
    )
    run.expectEqual(
      texts,
      ["translated:texte 1", "translated:texte 2", "translated:texte 3"],
      "each request got its own text back"
    )
    run.expectEqual(rig.soleEngine?.translateCalls.count, 3, "one engine call per request")
  }

  await run.test("gives each language pair its own session and no more") {
    let rig = HostRig()
    defer { rig.teardown() }

    _ = try await rig.coordinator.translate(makeRequest(id: "a", text: "bonjour", source: "fr"))
    _ = try await rig.coordinator.translate(makeRequest(id: "b", text: "hola", source: "es"))

    run.expectEqual(rig.coordinator.hosts.count, 2, "two pairs, two hosts")
    run.expectEqual(
      Set(rig.coordinator.hosts.map(\.pair)),
      [
        TranslationPairKey(source: "fr", target: "en"),
        TranslationPairKey(source: "es", target: "en")
      ],
      "one host per pair"
    )
  }

  await run.test("retires an idle pair rather than growing past the session ceiling") {
    let rig = HostRig()
    defer { rig.teardown() }

    for (index, source) in ["fr", "es", "de", "it", "pt"].enumerated() {
      _ = try await rig.coordinator.translate(
        makeRequest(id: "r\(index)", text: "texte", source: source)
      )
      run.expect(
        rig.coordinator.hosts.count <= AppleTranslationLimits.maxActivePairs,
        "after \(index + 1) pairs, hosts is \(rig.coordinator.hosts.count)"
      )
    }
  }

  await run.test("collapses two identical in-flight requests into one Apple call") {
    // Two feed cells showing the same quoted text, or a double tap. Paying Apple
    // twice is cheap; the reason this matters is that each extra call is also an
    // extra chance for the cloud to be reached if it fails.
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    let leader = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "leader", contentId: "post:1", text: "même texte")
      )
    }
    try await waitUntil("leader reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    let follower = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "follower", contentId: "post:2", text: "même texte")
      )
    }
    await Task.yield()
    await Task.yield()

    gate.open()
    let leaderResult = try await leader.value
    let followerResult = try await follower.value

    run.expectEqual(rig.soleEngine?.translateCalls.count, 1, "Apple was called once")
    run.expect(!leaderResult.deduplicated, "the leader did the work")
    run.expect(followerResult.deduplicated, "the follower was served from it")
    run.expectEqual(followerResult.requestId, "follower", "follower keeps its own request id")
    run.expectEqual(followerResult.contentId, "post:2", "follower keeps its own content id")
    run.expectEqual(
      followerResult.translatedText,
      leaderResult.translatedText,
      "and gets the same words"
    )
  }

  await run.test("passes the leader's failure to its followers") {
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = {
      $0.behavior = .gated(gate)
      $0.detectedSourceLanguage = nil
    }
    rig.availability.defaultStatus = .installed

    let leader = Task {
      try await rig.coordinator.translate(makeRequest(id: "leader", text: "même texte"))
    }
    try await waitUntil("leader reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    rig.soleEngine?.behavior = .wrongIdentifier("someone-else")
    let follower = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "follower", contentId: "post:2", text: "même texte")
      )
    }
    await Task.yield()
    gate.open()

    await run.expectFailure(.providerFailure, "leader fails") { try await leader.value }
    await run.expectFailure(.providerFailure, "follower fails the same way") {
      try await follower.value
    }
  }
}

@MainActor
func runCoordinatorCorrelationTests(_ run: TestRun) async {
  run.suite("AppleTranslationCoordinator — result correlation")

  await run.test("refuses a response tagged for a different request") {
    // The recycled-cell hazard, stated directly. If this correlated on array
    // position instead of on the identifier, the user would see another post's
    // translation under their own — a wrong answer that looks like a right one.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = { $0.behavior = .wrongIdentifier("a-different-request") }

    let error = await run.expectFailure(.providerFailure, "mismatched identifier is refused") {
      try await rig.coordinator.translate(makeRequest(id: "mine", text: "mon texte"))
    }
    run.expectEqual(error?.detail, "client_identifier_mismatch", "and says why")
    run.expect(
      error?.failure.permitsCloudFallback == true,
      "and may escalate, because Apple genuinely did not answer us"
    )
  }

  await run.test("picks its own response out of an unordered batch") {
    // The positive control for the test above: without it, a correlation bug
    // that rejected *every* response would also pass.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = {
      $0.behavior = .fixed([
        TranslationEngineResponse(
          clientIdentifier: "someone-elses-request",
          targetText: "not yours",
          sourceLanguage: "de",
          targetLanguage: "en"
        ),
        TranslationEngineResponse(
          clientIdentifier: "mine",
          targetText: "yours",
          sourceLanguage: "fr",
          targetLanguage: "en"
        )
      ])
    }

    let result = try await rig.coordinator.translate(makeRequest(id: "mine", text: "mon texte"))
    run.expectEqual(result.translatedText, "yours", "took the response addressed to it")
    run.expectEqual(result.detectedSourceLanguage, "fr", "and that response's source language")
  }
}

@MainActor
func runCoordinatorCancellationTests(_ run: TestRun) async {
  run.suite("AppleTranslationCoordinator — cancellation")

  await run.test("drops a queued request without ever calling Apple") {
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    let first = Task {
      try await rig.coordinator.translate(makeRequest(id: "r1", text: "premier"))
    }
    try await waitUntil("first request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    let second = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "r2", contentId: "post:2", text: "deuxième")
      )
    }
    await Task.yield()
    await Task.yield()

    rig.coordinator.cancel(requestIds: ["r2"], reason: "scrolled_away")
    await run.expectFailure(.requestCanceled, "the queued request is cancelled") {
      try await second.value
    }
    run.expectEqual(
      rig.soleEngine?.cancelInFlightCount,
      0,
      "and the live session is not disturbed, because it was never dispatched"
    )

    gate.open()
    _ = try await first.value
    run.expectEqual(
      rig.soleEngine?.translatedIdentifiers,
      ["r1"],
      "Apple never saw the cancelled request"
    )
  }

  await run.test("discards a result that arrives after the request was cancelled") {
    // iOS 18 cannot recall work already handed to Apple. `cancelInFlight` is
    // best effort, so the enforcement has to be here: the answer comes back
    // perfectly valid and must still be thrown away, or a cell that has already
    // been reused shows text for the item it used to hold.
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    let task = Task {
      try await rig.coordinator.translate(makeRequest(id: "r1", text: "en vol"))
    }
    try await waitUntil("request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }

    rig.coordinator.cancel(requestIds: ["r1"], reason: "cell_recycled")
    run.expectEqual(rig.soleEngine?.cancelInFlightCount, 1, "the session was asked to stop")

    gate.open()
    await run.expectFailure(.requestCanceled, "the late answer does not surface") {
      try await task.value
    }

    // And the pair is not wedged by having swallowed a response.
    let next = try await rig.coordinator.translate(
      makeRequest(id: "r2", contentId: "post:2", text: "après")
    )
    run.expectEqual(next.translatedText, "translated:après", "the pair still works afterwards")
  }

  await run.test("cancels by content and leaves other content alone") {
    // What a feed does on scroll-away: it knows the item, not the request.
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    let held = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "r1", contentId: "post:1", text: "premier")
      )
    }
    try await waitUntil("first request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    let doomed = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "r2", contentId: "post:2", text: "deuxième")
      )
    }
    let spared = Task {
      try await rig.coordinator.translate(
        makeRequest(id: "r3", contentId: "post:3", text: "troisième")
      )
    }
    await Task.yield()
    await Task.yield()

    rig.coordinator.cancel(contentIds: ["post:2"], reason: "scrolled_away")
    await run.expectFailure(.requestCanceled, "the scrolled-away item is cancelled") {
      try await doomed.value
    }

    gate.open()
    _ = try await held.value
    let sparedResult = try await spared.value
    run.expectEqual(sparedResult.contentId, "post:3", "the item still on screen is translated")
    run.expect(
      rig.soleEngine?.translatedIdentifiers.contains("r2") == false,
      "and Apple never saw the cancelled one"
    )
  }

  await run.test("fails everything pending when the caller's own task is cancelled") {
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = { $0.behavior = .hang }

    let task = Task {
      try await rig.coordinator.translate(makeRequest(id: "r1", text: "abandonné"))
    }
    try await waitUntil("request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }
    task.cancel()

    await run.expectFailure(.requestCanceled, "a cancelled caller gets a cancellation") {
      try await task.value
    }
  }

  await run.test("clears the queue on logout without letting anything reach the cloud") {
    // Stage 6: a security reset must not leave a job that finishes into a
    // signed-out session. Stage 8: and it must not bill us on the way out, which
    // is why the reason it fails with is a cancellation.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.configureEngine = { $0.behavior = .hang }

    let task = Task {
      try await rig.coordinator.translate(makeRequest(id: "r1", text: "en vol"))
    }
    try await waitUntil("request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }

    rig.coordinator.reset(reason: "logout")
    let error = await run.expectFailure(.requestCanceled, "pending work is cancelled") {
      try await task.value
    }
    run.expectEqual(error?.detail, "logout", "and says what cleared it")
    run.expect(
      error?.failure.permitsCloudFallback == false,
      "and cannot escalate to a billable provider"
    )
    run.expectEqual(rig.coordinator.hosts.count, 0, "no session is left mounted")
  }

  await run.test("survives the replacement host mounting before the outgoing one leaves") {
    // React Native mounts the new host before unmounting the old one when the
    // key changes, so an unguarded unmount would reset the incoming host's
    // queue. The refcount is the only thing preventing that.
    let rig = HostRig()
    defer { rig.teardown() }
    let gate = AsyncGate()
    rig.configureEngine = { $0.behavior = .gated(gate) }

    let task = Task {
      try await rig.coordinator.translate(makeRequest(id: "r1", text: "en vol"))
    }
    try await waitUntil("request reaches the engine") {
      rig.soleEngine?.translateCalls.count == 1
    }

    rig.coordinator.hostDidMount()
    rig.coordinator.hostDidUnmount()
    run.expect(rig.coordinator.isHostMounted, "still mounted after the overlap resolves")

    gate.open()
    let result = try await task.value
    run.expectEqual(result.translatedText, "translated:en vol", "and the in-flight work survived")

    rig.coordinator.hostDidUnmount()
    run.expect(!rig.coordinator.isHostMounted, "the last unmount really unmounts")
  }
}

@MainActor
func runCoordinatorPolicyTests(_ run: TestRun) async {
  run.suite("AppleTranslationCoordinator — availability and consent")

  await run.test("never asks Apple about a pair that is the same language") {
    // `en-GB` to `en-US` is not a translation. Asking Apple would be harmless;
    // the reason this short-circuits is that `same_language` is one of the four
    // failures that must not escalate, so answering it from the tags is what
    // keeps a pointless tap off the billable path entirely.
    let rig = HostRig()
    defer { rig.teardown() }

    await run.expectFailure(.sameLanguage, "same language is refused") {
      try await rig.coordinator.translate(
        makeRequest(text: "hello", source: "en-GB", target: "en-US")
      )
    }
    run.expectEqual(rig.availability.queryCount, 0, "the device was not consulted")
    run.expectEqual(rig.totalTranslateCalls, 0, "and Apple was not called")
  }

  await run.test("does ask about Simplified to Traditional Chinese") {
    // The positive control: a short-circuit on the primary subtag alone would
    // swallow this pair, which is a real translation.
    let rig = HostRig()
    defer { rig.teardown() }

    _ = try await rig.coordinator.translate(
      makeRequest(text: "你好", source: "zh-Hans", target: "zh-Hant")
    )
    run.expectEqual(rig.availability.queryCount, 1, "the device was consulted")
  }

  await run.test("reports an unsupported pair without calling Apple to translate") {
    let rig = HostRig()
    defer { rig.teardown() }
    rig.availability.defaultStatus = .unsupported

    let error = await run.expectFailure(.unsupportedLanguagePair, "unsupported pair") {
      try await rig.coordinator.translate(makeRequest(source: "ht", target: "en"))
    }
    run.expect(
      error?.failure.permitsCloudFallback == true,
      "and may escalate, which is how Haitian Creole still gets translated"
    )
    run.expectEqual(rig.totalTranslateCalls, 0, "no translate call was made")
  }

  await run.test("will not download a language the user has not agreed to") {
    // Stage 5: a download is a request, not a side effect. `prepareTranslation`
    // is what presents Apple's sheet, so calling it from a feed render would put
    // a system modal on screen that the user never asked for.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.availability.defaultStatus = .supportedDownloadRequired

    await run.expectFailure(.modelNotInstalled, "a missing model is reported, not fetched") {
      try await rig.coordinator.translate(makeRequest(allowDownload: false))
    }
    run.expectEqual(rig.soleEngine?.prepareCount, 0, "no download was started")
    run.expectEqual(rig.totalTranslateCalls, 0, "and nothing was translated")
  }

  await run.test("downloads once the user has agreed, and says that it did") {
    let rig = HostRig()
    defer { rig.teardown() }
    rig.availability.defaultStatus = .supportedDownloadRequired

    let result = try await rig.coordinator.translate(makeRequest(allowDownload: true))
    run.expectEqual(rig.soleEngine?.prepareCount, 1, "the model was prepared")
    run.expect(result.downloadPrepared, "and the result says so, so the UI can explain the wait")
    run.expectEqual(result.translatedText, "translated:bonjour", "then it translated")
  }

  await run.test("reads a failed download as offline rather than unsupported") {
    let rig = HostRig()
    defer { rig.teardown() }
    rig.availability.defaultStatus = .supportedDownloadRequired
    rig.configureEngine = {
      $0.prepareResult = .failure(
        NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet)
      )
    }

    let error = await run.expectFailure(.offlineModelUnavailable, "a download with no network") {
      try await rig.coordinator.translate(makeRequest(allowDownload: true))
    }
    run.expect(error?.failure.isRecoverable == true, "and offers a retry, because it is one")
  }

  await run.test("attempts the translation when detection was merely inconclusive") {
    // `temporarilyUnavailable` is what the real probe answers when Apple could
    // not detect the source language. Treating that as unsupported would refuse
    // the mixed-language posts this feature exists for.
    let rig = HostRig()
    defer { rig.teardown() }
    rig.availability.defaultStatus = .temporarilyUnavailable

    let result = try await rig.coordinator.translate(makeRequest(source: nil))
    run.expectEqual(result.translatedText, "translated:bonjour", "it tried anyway")
  }

  await run.test("gives Apple the text only when it needs it to detect a language") {
    // Stage 6: the text is private, so it goes no further than the question
    // being asked requires. An explicit source needs the two tags and nothing
    // else.
    let rig = HostRig()
    defer { rig.teardown() }

    _ = try await rig.coordinator.translate(makeRequest(text: "bonjour", source: "fr"))
    run.expectEqual(rig.availability.queries.first?.text, nil, "explicit source: no text")

    let auto = HostRig()
    defer { auto.teardown() }
    _ = try await auto.coordinator.translate(makeRequest(text: "bonjour", source: nil))
    run.expectEqual(auto.availability.queries.first?.text, "bonjour", "auto-detect: text needed")
  }

  await run.test("rejects text it should not have been given") {
    let rig = HostRig()
    defer { rig.teardown() }

    let empty = await run.expectFailure(.invalidText, "empty text") {
      try await rig.coordinator.translate(makeRequest(text: ""))
    }
    run.expectEqual(empty?.detail, "empty", "and says so")

    await run.expectFailure(.invalidText, "whitespace only") {
      try await rig.coordinator.translate(makeRequest(text: "   \n\t "))
    }

    let long = String(repeating: "a", count: AppleTranslationLimits.maxTextLength + 1)
    let overLong = await run.expectFailure(.invalidText, "beyond the per-request ceiling") {
      try await rig.coordinator.translate(makeRequest(text: long))
    }
    run.expect(
      overLong?.detail?.hasPrefix("length_") == true,
      "and reports the length, not the text"
    )
    run.expect(
      overLong?.detail?.contains("aaaa") == false,
      "the diagnostic does not quote the text"
    )

    run.expectEqual(rig.coordinator.hosts.count, 0, "and no session was mounted for any of them")
    run.expectEqual(rig.availability.queryCount, 0, "and the device was never consulted")
  }

  await run.test("caches an explicit pair's verdict and re-asks for auto-detect") {
    let rig = HostRig(mountsHosts: false)
    defer { rig.teardown() }

    _ = await rig.coordinator.availabilityStatus(source: "fr", target: "en")
    _ = await rig.coordinator.availabilityStatus(source: "fr", target: "en")
    run.expectEqual(rig.availability.queryCount, 1, "an explicit pair is asked once")

    _ = await rig.coordinator.availabilityStatus(source: nil, target: "en")
    _ = await rig.coordinator.availabilityStatus(source: nil, target: "en")
    run.expectEqual(
      rig.availability.queryCount,
      3,
      "auto-detect depends on the text, so it is never cached"
    )
  }

  await run.test("lets a cached verdict expire") {
    let rig = HostRig(
      timings: .init(hostMount: 0.4, translate: 2, pairIdle: 3600, statusCacheTTL: 0.05),
      mountsHosts: false
    )
    defer { rig.teardown() }

    _ = await rig.coordinator.availabilityStatus(source: "fr", target: "en")
    try await Task.sleep(nanoseconds: 120_000_000)
    _ = await rig.coordinator.availabilityStatus(source: "fr", target: "en")
    run.expectEqual(rig.availability.queryCount, 2, "asked again after the TTL")
  }

  await run.test("answers same-language from the tags even when asked directly") {
    let rig = HostRig(mountsHosts: false)
    defer { rig.teardown() }

    let status = await rig.coordinator.availabilityStatus(source: "pt-BR", target: "pt-PT")
    run.expectEqual(status, .sameLanguage, "pt-BR to pt-PT shares one model")
    run.expectEqual(rig.availability.queryCount, 0, "without asking the device")
  }

  await run.test("passes the device's language list through untouched") {
    let rig = HostRig(mountsHosts: false)
    defer { rig.teardown() }
    rig.availability.languages = ["en", "fr", "ht", "zh-Hans"]

    run.expectEqual(
      await rig.coordinator.supportedLanguages(),
      ["en", "fr", "ht", "zh-Hans"],
      "no hardcoded list stands in for the device's answer"
    )
  }

  await run.test("degrades to the cloud on an OS with no Translation framework") {
    let availability = UnsupportedOSAvailability()
    let status = await availability.status(
      pair: TranslationPairKey(source: "fr", target: "en"),
      text: nil
    )
    run.expectEqual(status, .unsupported, "iOS 15-17 reports unsupported")
    run.expectEqual(await availability.supportedLanguages(), [], "and offers no languages")
    run.expect(
      AppleTranslationFailure.unsupportedLanguagePair.permitsCloudFallback,
      "which the router is allowed to escalate"
    )
  }
}

@MainActor
func runCoordinatorWatchdogTests(_ run: TestRun) async {
  run.suite("AppleTranslationCoordinator — watchdogs")

  await run.test("fails a request that no host ever came to serve") {
    // The build that forgot to mount `AppleTranslationHost`. Without this the
    // Translate button would spin forever instead of falling back.
    let rig = HostRig(
      timings: .init(hostMount: 0.1, translate: 2, pairIdle: 3600, statusCacheTTL: 3600),
      mountsHosts: false
    )
    defer { rig.teardown() }

    let error = await run.expectFailure(.translationSessionUnavailable, "no host ever mounted") {
      try await rig.coordinator.translate(makeRequest())
    }
    run.expectEqual(error?.detail, "host_not_mounted", "and names the UI-tree cause")
    run.expect(
      error?.failure.permitsCloudFallback == true,
      "and falls back, so a missing host is a cost problem and not an outage"
    )
  }

  await run.test("distinguishes a mounted host that never vended a session") {
    let rig = HostRig(
      timings: .init(hostMount: 0.1, translate: 2, pairIdle: 3600, statusCacheTTL: 3600),
      mountsHosts: false
    )
    defer { rig.teardown() }
    rig.coordinator.hostDidMount()

    let error = await run.expectFailure(.translationSessionUnavailable, "host up, no session") {
      try await rig.coordinator.translate(makeRequest())
    }
    run.expectEqual(error?.detail, "session_not_vended", "a different cause, named differently")
  }

  await run.test("times out a session that accepted work and went quiet, then replaces it") {
    // A wedged session would otherwise poison its language pair for the rest of
    // the process. Bumping the generation is what makes SwiftUI tear the
    // `.translationTask` down and build a new one.
    let rig = HostRig(
      timings: .init(hostMount: 0.1, translate: 0.15, pairIdle: 3600, statusCacheTTL: 3600)
    )
    defer { rig.teardown() }
    rig.configureEngine = { $0.behavior = .hang }

    let error = await run.expectFailure(.timeout, "a session that never answers") {
      try await rig.coordinator.translate(makeRequest())
    }
    run.expectEqual(error?.detail, "native_watchdog", "and says which watchdog fired")

    try await waitUntil("the wedged host is replaced") {
      rig.mountedDescriptors.contains { $0.generation == 1 }
    }
    run.expect(
      rig.mountedDescriptors.contains { $0.generation == 1 },
      "a fresh session was mounted for the pair"
    )
  }
}
