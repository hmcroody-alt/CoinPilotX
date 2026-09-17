#!/usr/bin/env bash
# Type-checks the Apple on-device translation native module against the REAL
# iOS SDK and the REAL prebuilt ExpoModulesCore swiftmodule.
#
# Why this exists: a Swift file that merely *looks* right compiles fine against
# a hand-written stub and then fails against the actual Pods headers. This
# script is the cheap gate that runs without a full xcodebuild.
#
# Usage:
#   scripts/typecheck_apple_translation_swift.sh [products-dir]
#
# `products-dir` defaults to the warm device build's products directory. Pass a
# simulator products dir to check the simulator slice instead.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE_DIR="$REPO_ROOT/mobile-native/modules/pulse-apple-translation/ios"

DEFAULT_PRODUCTS="$HOME/Library/Developer/Xcode/DerivedData/pulsesoc-device-build/Build/Products/Release-iphoneos"
PRODUCTS="${1:-$DEFAULT_PRODUCTS}"

# The deployment target must stay at the project's floor. Raising it here would
# hide exactly the bug this module is designed to avoid: an iOS 26-only symbol
# compiling clean and then trapping on the iOS 18 test device.
TARGET_TRIPLE="arm64-apple-ios15.1"
SDK_NAME="iphoneos"
case "$PRODUCTS" in
  *simulator*) SDK_NAME="iphonesimulator"; TARGET_TRIPLE="arm64-apple-ios15.1-simulator" ;;
esac
SDK_PATH="$(xcrun --sdk "$SDK_NAME" --show-sdk-path)"

# Files that import neither ExpoModulesCore nor React. These are checked even
# when no build products exist, which keeps the gate useful on a cold checkout.
PURE_SOURCES=(
  "$MODULE_DIR/AppleTranslationModels.swift"
  "$MODULE_DIR/AppleTranslationError.swift"
  "$MODULE_DIR/AppleTranslationEngine.swift"
  "$MODULE_DIR/AppleTranslationCoordinator.swift"
  "$MODULE_DIR/AppleTranslationSessionEngine.swift"
  "$MODULE_DIR/AppleTranslationHost.swift"
)

echo "== Apple translation: pure Swift type-check ($TARGET_TRIPLE) =="
xcrun swiftc -typecheck \
  -sdk "$SDK_PATH" \
  -target "$TARGET_TRIPLE" \
  -swift-version 5 \
  "${PURE_SOURCES[@]}"
echo "   OK"

if [ ! -d "$PRODUCTS/ExpoModulesCore/ExpoModulesCore.swiftmodule" ]; then
  echo "== Apple translation: bridge type-check SKIPPED =="
  echo "   No ExpoModulesCore.swiftmodule under:"
  echo "     $PRODUCTS"
  echo "   Build the app once, then re-run. The pure check above still ran."
  exit 0
fi

INCLUDES=(-I "$PRODUCTS")
for dir in "$PRODUCTS"/*/; do
  INCLUDES+=(-I "$dir")
done

# ExpoModulesCore's swiftmodule has a companion clang module whose umbrella
# header pulls in the pod's public ObjC headers. Without these, swiftc reports
# the misleading "cannot load underlying module for 'ExpoModulesCore'".
PODS_DIR="${PODS_DIR:-}"
if [ -z "$PODS_DIR" ]; then
  for candidate in \
    "$REPO_ROOT/mobile-native/ios/Pods" \
    "$HOME/Desktop/cpx-prefetch-iso/mobile-native/ios/Pods" \
    "$HOME/Desktop/CoinPilotX/mobile-native/ios/Pods"
  do
    if [ -d "$candidate/Headers/Public" ]; then PODS_DIR="$candidate"; break; fi
  done
fi

if [ -n "$PODS_DIR" ]; then
  INCLUDES+=(-Xcc -I"$PODS_DIR/Headers/Public")
  for dir in "$PODS_DIR"/Headers/Public/*/; do
    INCLUDES+=(-Xcc -I"$dir")
  done
fi

# CocoaPods emits `<Pod>.modulemap`, not `module.modulemap`, so a bare -I never
# finds it. Point clang at each one explicitly. Note the module NAME does not
# always match the pod name — React-Core declares `module React` — which is why
# these are collected by glob rather than named.
#
# Order matters and duplicates are fatal: clang rejects two definitions of the
# same module name. The built products are preferred, and a pod's
# Target Support Files copy is added only for module names the products dir does
# not already provide (which is how `module React` gets in — React-Core ships no
# modulemap into the products directory).
declared_module_name() {
  awk '/^[[:space:]]*(explicit[[:space:]]+)?framework?[[:space:]]*module|^[[:space:]]*module/ {
         for (i = 1; i <= NF; i++) if ($i == "module") { print $(i + 1); exit }
       }' "$1"
}

seen_modules=""
add_modulemap() {
  local map="$1"
  local name
  name="$(declared_module_name "$map")"
  [ -z "$name" ] && return 0
  case " $seen_modules " in *" $name "*) return 0 ;; esac
  seen_modules="$seen_modules $name"
  INCLUDES+=(-Xcc -fmodule-map-file="$map")
  INCLUDES+=(-Xcc -I"$(dirname "$map")")
}

for map in "$PRODUCTS"/*/*.modulemap; do
  [ -f "$map" ] && add_modulemap "$map"
done
if [ -n "$PODS_DIR" ] && [ -d "$PODS_DIR/Target Support Files" ]; then
  while IFS= read -r map; do
    add_modulemap "$map"
  done < <(find "$PODS_DIR/Target Support Files" -name "*.modulemap" 2>/dev/null | sort)
fi

echo "== Apple translation: full bridge type-check against ExpoModulesCore =="
echo "   products: $PRODUCTS"
echo "   pods:     ${PODS_DIR:-<none found>}"
xcrun swiftc -typecheck \
  -sdk "$SDK_PATH" \
  -target "$TARGET_TRIPLE" \
  -swift-version 5 \
  "${INCLUDES[@]}" \
  "$MODULE_DIR"/*.swift
echo "   OK"
