#!/usr/bin/env bash
set -euo pipefail

apply_patch() {
  PATCH_FILE="$1"

  if patch --dry-run --batch --forward -p1 -i "$PATCH_FILE" >/dev/null 2>&1; then
    echo "Applying $PATCH_FILE..."
    patch --batch --forward -p1 -i "$PATCH_FILE"
  elif patch --dry-run --batch --reverse -p1 -i "$PATCH_FILE" >/dev/null 2>&1; then
    echo "$PATCH_FILE already applied."
  else
    echo "ERROR: $PATCH_FILE cannot be safely applied."
    exit 1
  fi
}

apply_patch "patches/react-native+0.81.5.patch"
