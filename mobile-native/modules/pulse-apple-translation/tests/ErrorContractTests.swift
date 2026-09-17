import Foundation

// Stage 9's typed failure contract, and Stage 8's cost rule expressed as a
// property of it.
//
// The two boolean tables on `AppleTranslationFailure` are the whole of the
// router's escalation policy as far as the native side is concerned. They are
// asserted here as literal sets rather than case by case, so that adding a
// fifteenth failure is a decision someone has to write down in both directions
// instead of inheriting whichever branch of the switch it happened to land in.

@MainActor
func runErrorContractTests(_ run: TestRun) async {
  run.suite("AppleTranslationFailure")

  let all: [AppleTranslationFailure] = [
    .unsupportedOSVersion, .unsupportedLanguagePair, .modelNotInstalled,
    .downloadCanceled, .downloadFailed, .offlineModelUnavailable, .invalidText,
    .sameLanguage, .requestCanceled, .nativeBridgeUnavailable,
    .translationSessionUnavailable, .providerFailure, .timeout, .invalidLanguage
  ]

  await run.test("has the fourteen cases the wire contract names") {
    run.expectEqual(all.count, 14, "case count")
    run.expectEqual(Set(all.map(\.rawValue)).count, 14, "raw values are distinct")
    // A raw value is a wire value; snake_case is what TypeScript reads.
    for failure in all {
      run.expect(
        failure.rawValue == failure.rawValue.lowercased()
          && !failure.rawValue.contains(" ")
          && !failure.rawValue.contains("-"),
        "\(failure.rawValue) is a snake_case wire value"
      )
    }
  }

  await run.test("marks recoverable exactly the failures a retry could fix") {
    let recoverable = Set(all.filter(\.isRecoverable).map(\.rawValue))
    run.expectEqual(
      recoverable,
      [
        "download_canceled", "download_failed", "offline_model_unavailable",
        "provider_failure", "timeout", "translation_session_unavailable"
      ],
      "recoverable set"
    )
  }

  await run.test("never lets a cancellation become a billable cloud request") {
    // The expensive mistake this guards: a user scrolls away, the request is
    // cancelled, and the router reads the failure as "Apple could not do it" and
    // pays Google for a translation nobody is looking at. Stage 8 forbids it, and
    // the only thing enforcing it is this flag.
    run.expect(
      !AppleTranslationFailure.requestCanceled.permitsCloudFallback,
      "request_canceled must not escalate"
    )
    run.expect(
      !AppleTranslationFailure.sameLanguage.permitsCloudFallback,
      "same_language must not escalate"
    )
    run.expect(
      !AppleTranslationFailure.invalidText.permitsCloudFallback,
      "invalid_text must not escalate"
    )
    run.expect(
      !AppleTranslationFailure.invalidLanguage.permitsCloudFallback,
      "invalid_language must not escalate"
    )

    let withheld = Set(all.filter { !$0.permitsCloudFallback }.map(\.rawValue))
    run.expectEqual(
      withheld,
      ["invalid_text", "same_language", "request_canceled", "invalid_language"],
      "the set of failures that must not escalate"
    )
  }

  await run.test("lets every genuine Apple shortfall reach the cloud") {
    // The inverse claim, and the one that keeps the feature working: an iOS 17
    // device, an unsupported pair and a missing model are all cases where the
    // cloud is the correct answer, not an error message.
    for failure in [
      AppleTranslationFailure.unsupportedOSVersion,
      .unsupportedLanguagePair,
      .modelNotInstalled,
      .nativeBridgeUnavailable,
      .translationSessionUnavailable
    ] {
      run.expect(failure.permitsCloudFallback, "\(failure.rawValue) may escalate")
    }
  }

  await run.test("keeps the operator detail out of what the UI could render") {
    // `detail` is allowed to be diagnostic, but `code` is what the UI switches
    // on, and the two must not be the same field. A UI that fell back to
    // printing `detail` would be printing native internals.
    let error = AppleTranslationError(.providerFailure, detail: "translation_-5")
    run.expectEqual(error.code, "provider_failure", "code is the wire value")
    run.expect(error.detail != error.code, "detail is not the code")
  }
}

@MainActor
func runErrorMapperTests(_ run: TestRun) async {
  run.suite("AppleTranslationErrorMapper")

  await run.test("maps Swift task cancellation to a cancellation") {
    let mapped = AppleTranslationErrorMapper.map(CancellationError(), allowDownload: false)
    run.expectEqual(mapped.failure, .requestCanceled, "CancellationError")
    run.expect(
      !mapped.failure.permitsCloudFallback,
      "and therefore does not bill us"
    )
  }

  await run.test("reads connectivity failures as a missing offline model") {
    for code in [
      NSURLErrorNotConnectedToInternet,
      NSURLErrorNetworkConnectionLost,
      NSURLErrorDataNotAllowed,
      NSURLErrorCannotConnectToHost
    ] {
      let mapped = AppleTranslationErrorMapper.map(
        NSError(domain: NSURLErrorDomain, code: code),
        allowDownload: true
      )
      run.expectEqual(mapped.failure, .offlineModelUnavailable, "URL error \(code)")
    }
  }

  await run.test("separates a cancelled download from a failed one") {
    run.expectEqual(
      AppleTranslationErrorMapper.map(
        NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled),
        allowDownload: true
      ).failure,
      .downloadCanceled,
      "cancelled transfer"
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(
        NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut),
        allowDownload: true
      ).failure,
      .timeout,
      "timed out transfer"
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(
        NSError(domain: NSURLErrorDomain, code: NSURLErrorBadServerResponse),
        allowDownload: true
      ).failure,
      .downloadFailed,
      "any other transfer problem"
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(
        NSError(domain: NSCocoaErrorDomain, code: NSUserCancelledError),
        allowDownload: true
      ).failure,
      .downloadCanceled,
      "user dismissed Apple's sheet"
    )
  }

  await run.test("classifies Apple's own errors by message, not by enum case") {
    // `TranslationError`'s case set changed between iOS 18 and 26, so pattern
    // matching cases would not compile against the 18.0 floor. Matching the
    // domain and the message is the deliberate alternative.
    let unsupported = NSError(
      domain: "TranslationErrorDomain",
      code: 2,
      userInfo: [NSLocalizedDescriptionKey: "The language pair is unsupported"]
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(unsupported, allowDownload: true).failure,
      .unsupportedLanguagePair,
      "unsupported pair"
    )

    let missingModel = NSError(
      domain: "TranslationErrorDomain",
      code: 3,
      userInfo: [NSLocalizedDescriptionKey: "Language not installed"]
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(missingModel, allowDownload: false).failure,
      .modelNotInstalled,
      "model missing and no consent to download"
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(missingModel, allowDownload: true).failure,
      .downloadFailed,
      "model missing after the user agreed to download"
    )
  }

  await run.test("reads a cancellation differently depending on who asked") {
    // The same message means two different things: during a download the user
    // dismissed a sheet and should be offered it again; outside one, we
    // cancelled the request ourselves and nothing should be said at all.
    let cancelled = NSError(
      domain: "TranslationErrorDomain",
      code: 1,
      userInfo: [NSLocalizedDescriptionKey: "The operation was cancelled"]
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(cancelled, allowDownload: true).failure,
      .downloadCanceled,
      "cancelled during a download"
    )
    run.expectEqual(
      AppleTranslationErrorMapper.map(cancelled, allowDownload: false).failure,
      .requestCanceled,
      "cancelled outside a download"
    )
  }

  await run.test("falls back to a provider failure for an unrecognised domain") {
    let mapped = AppleTranslationErrorMapper.map(
      NSError(domain: "SomeFutureAppleDomain", code: 42),
      allowDownload: false
    )
    run.expectEqual(mapped.failure, .providerFailure, "unknown domain")
    run.expect(mapped.failure.permitsCloudFallback, "and may escalate, since Apple did not answer")
  }

  await run.test("does not copy the failing text into the diagnostic") {
    // Apple puts the offending content in `localizedDescription` often enough
    // that this is a real leak path: `detail` reaches logs and metrics, and
    // Stage 12 forbids raw text in both.
    let leaky = NSError(
      domain: "TranslationErrorDomain",
      code: 7,
      userInfo: [
        NSLocalizedDescriptionKey: "Could not translate 'my password is hunter2'",
        NSLocalizedFailureReasonErrorKey: "hunter2"
      ]
    )
    let detail = AppleTranslationErrorMapper.map(leaky, allowDownload: false).detail ?? ""
    run.expect(!detail.contains("hunter2"), "detail does not carry the text")
    run.expectEqual(detail, "translation_7", "detail is the domain and code only")
  }
}
