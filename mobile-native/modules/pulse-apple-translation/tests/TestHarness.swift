import Foundation

// A very small test harness.
//
// XCTest on macOS needs a test bundle and a runner, which would mean either an
// Xcode test target in `PulseSoc.xcodeproj` or a SwiftPM package sitting inside
// the Expo module. Both are more machinery than this module needs, and the
// podspec globs `ios/**/*.swift` — a test file placed there would be compiled
// into the shipping app. So the tests live in this sibling directory, get built
// as a plain host executable by
// `scripts/test_apple_translation_swift.sh`, and report through this.
//
// Failures accumulate: one wrong assertion does not abort its test, so a run
// tells you everything that is broken rather than the first thing.

@MainActor
final class TestRun {
  private var failures: [String] = []
  private var passed = 0
  private var failed = 0
  private var assertions = 0
  private var currentTest = "<none>"
  private var currentTestFailed = false

  func suite(_ name: String) {
    print("")
    print("\(name)")
  }

  func test(_ name: String, _ body: () async throws -> Void) async {
    currentTest = name
    currentTestFailed = false
    do {
      try await body()
    } catch {
      record("threw an unexpected error: \(error)")
    }
    if currentTestFailed {
      failed += 1
      print("  FAIL  \(name)")
    } else {
      passed += 1
      print("  ok    \(name)")
    }
  }

  // MARK: - Assertions

  func expect(
    _ condition: Bool,
    _ what: String,
    file: StaticString = #fileID,
    line: UInt = #line
  ) {
    assertions += 1
    guard !condition else { return }
    record("\(what)", file: file, line: line)
  }

  func expectEqual<T: Equatable>(
    _ actual: T,
    _ expected: T,
    _ what: String,
    file: StaticString = #fileID,
    line: UInt = #line
  ) {
    assertions += 1
    guard actual != expected else { return }
    record("\(what)\n          expected: \(expected)\n          actual:   \(actual)", file: file, line: line)
  }

  /// Asserts that `body` throws `AppleTranslationError` with the given code.
  ///
  /// Returns the error so a caller can make further claims about `detail`
  /// without restating the code check.
  @discardableResult
  func expectFailure<T>(
    _ code: AppleTranslationFailure,
    _ what: String,
    file: StaticString = #fileID,
    line: UInt = #line,
    _ body: () async throws -> T
  ) async -> AppleTranslationError? {
    assertions += 1
    do {
      let value = try await body()
      record("\(what)\n          expected failure \(code.rawValue), got success: \(value)", file: file, line: line)
      return nil
    } catch let error as AppleTranslationError {
      if error.failure != code {
        record(
          "\(what)\n          expected failure \(code.rawValue), got \(error.failure.rawValue) (detail: \(error.detail ?? "-"))",
          file: file,
          line: line
        )
      }
      return error
    } catch {
      record("\(what)\n          expected failure \(code.rawValue), got untyped error: \(error)", file: file, line: line)
      return nil
    }
  }

  private func record(_ message: String, file: StaticString = #fileID, line: UInt = 0) {
    currentTestFailed = true
    let location = line == 0 ? "\(file)" : "\(file):\(line)"
    failures.append("  \(currentTest)\n    \(message)\n    at \(location)")
  }

  // MARK: - Result

  func report() -> Int32 {
    print("")
    if failures.isEmpty {
      print("PASS  \(passed) tests, \(assertions) assertions")
      return 0
    }
    print("FAILURES")
    for failure in failures {
      print("")
      print(failure)
    }
    print("")
    print("FAIL  \(passed) passed, \(failed) failed, \(assertions) assertions")
    return 1
  }
}

// MARK: - Waiting

/// Spins the cooperative pool until `condition` holds.
///
/// The coordinator publishes `hosts` and the rig starts run loops in response,
/// so almost every coordinator test has to wait for work to be picked up. A
/// yield loop is used rather than a sleep so the tests run at the speed of the
/// scheduler; the deadline exists only so a broken invariant fails in a second
/// instead of hanging the build.
@MainActor
func waitUntil(
  _ description: String,
  timeout: TimeInterval = 3,
  _ condition: () -> Bool
) async throws {
  let deadline = Date().addingTimeInterval(timeout)
  while !condition() {
    if Date() > deadline {
      throw TestTimeout(description: description)
    }
    await Task.yield()
    // A bare yield never advances wall-clock time, so a condition that depends
    // on a timer would spin until the deadline. A tiny sleep every pass keeps
    // the loop cheap and lets watchdogs actually fire.
    try? await Task.sleep(nanoseconds: 1_000_000)
  }
}

struct TestTimeout: Error, CustomStringConvertible {
  let description: String

  var localizedDescription: String { description }
}

/// A one-shot latch, so a test can hold a fake engine mid-flight, act, and then
/// let it finish.
@MainActor
final class AsyncGate {
  private var waiters: [CheckedContinuation<Void, Never>] = []
  private var isOpen = false

  func wait() async {
    if isOpen { return }
    await withCheckedContinuation { continuation in
      waiters.append(continuation)
    }
  }

  func open() {
    isOpen = true
    let pending = waiters
    waiters.removeAll()
    for continuation in pending { continuation.resume() }
  }
}
