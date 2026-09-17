import Foundation

// Entry point for the host executable built by
// `scripts/test_apple_translation_swift.sh`.
//
// Every suite gets the same `TestRun`, so one exit code and one summary cover
// the whole module. Suites are listed rather than discovered — there is no
// runtime reflection here to find them, and an explicit list means a suite that
// stops being called shows up in review as a deleted line.

@main
struct AppleTranslationTests {
  @MainActor
  static func main() async {
    let run = TestRun()

    run.suite("language normalizer")
    await runLanguageNormalizerTests(run)

    run.suite("digest")
    await runDigestTests(run)

    run.suite("error contract")
    await runErrorContractTests(run)

    run.suite("error mapper")
    await runErrorMapperTests(run)

    run.suite("coordinator: queue")
    await runCoordinatorQueueTests(run)

    run.suite("coordinator: correlation")
    await runCoordinatorCorrelationTests(run)

    run.suite("coordinator: cancellation")
    await runCoordinatorCancellationTests(run)

    run.suite("coordinator: policy")
    await runCoordinatorPolicyTests(run)

    run.suite("coordinator: watchdog")
    await runCoordinatorWatchdogTests(run)

    exit(run.report())
  }
}
