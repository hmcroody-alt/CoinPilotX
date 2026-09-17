#!/usr/bin/env bash
# Runs the Apple on-device translation module's unit tests.
#
# These build and run as a plain macOS host executable, with no iOS SDK, no
# simulator and no Xcode test target. That is possible only because of the seam
# in `AppleTranslationEngine.swift`: the four sources below import nothing but
# Foundation and Combine, so the queue, deduplication, cancellation and
# correlation logic compiles for the host. Everything that touches Apple's
# `Translation` framework lives in `AppleTranslationSessionEngine.swift` and
# `AppleTranslationHost.swift`, which are deliberately NOT compiled here — they
# hold no decisions, and `scripts/typecheck_apple_translation_swift.sh` is what
# proves they still match the real SDK.
#
# Usage:
#   scripts/test_apple_translation_swift.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE="$REPO_ROOT/mobile-native/modules/pulse-apple-translation"
OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

# Order is irrelevant to swiftc, but the module sources are listed first so a
# compile error reads as "the module is broken" before "a test is broken".
SOURCES=(
  "$MODULE/ios/AppleTranslationModels.swift"
  "$MODULE/ios/AppleTranslationError.swift"
  "$MODULE/ios/AppleTranslationEngine.swift"
  "$MODULE/ios/AppleTranslationCoordinator.swift"
  "$MODULE/tests/TestHarness.swift"
  "$MODULE/tests/Fakes.swift"
  "$MODULE/tests/LanguageNormalizerTests.swift"
  "$MODULE/tests/ErrorContractTests.swift"
  "$MODULE/tests/CoordinatorTests.swift"
  "$MODULE/tests/TestMain.swift"
)

for src in "${SOURCES[@]}"; do
  if [[ ! -f "$src" ]]; then
    echo "missing source: $src" >&2
    exit 1
  fi
done

# A source under ios/ that reaches Apple's framework would compile here only by
# accident of the host SDK having a same-named symbol, so the exclusion above is
# asserted rather than assumed.
if grep -lE '^import (Translation|SwiftUI|ExpoModulesCore)' "${SOURCES[@]}" >/dev/null 2>&1; then
  echo "a source compiled for the host imports an iOS-only framework:" >&2
  grep -lE '^import (Translation|SwiftUI|ExpoModulesCore)' "${SOURCES[@]}" >&2
  exit 1
fi

echo "== Apple translation: host unit tests =="

xcrun swiftc \
  -swift-version 5 \
  -O \
  -o "$OUT/AppleTranslationTests" \
  "${SOURCES[@]}"

"$OUT/AppleTranslationTests"
