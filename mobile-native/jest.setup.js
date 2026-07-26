/**
 * Global test setup.
 *
 * AsyncStorage is a native module, so any test whose import graph reaches
 * `src/core/cache.ts` — which now includes every settings screen, via the
 * preference store — throws at require time without a mock. Registering the
 * package's own in-memory mock here rather than per-file means a new test never
 * fails for a reason that has nothing to do with what it is testing.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

/**
 * No `NativeAnimatedHelper` mock here on purpose. React Native 0.81 no longer
 * exposes that path, and jest-expo's own preset already stubs the animated
 * native module — mocking it again resolves to nothing and fails the suite
 * before a single test runs.
 */
