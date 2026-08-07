#!/usr/bin/env bash
# Removes the quarantine folder holding the files orphaned by removing the
# Business OS construction gate.
#
# The agent that did the removal could not unlink files in this workspace, so it
# moved all 30 orphans into .gate_removed/ instead. Git already records them as
# deleted from their real locations; this folder is untracked leftover.
#
# Run from the repo root:  bash scripts/cleanup_construction_gate_trash.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf .gate_removed
echo "Quarantine folder removed."
