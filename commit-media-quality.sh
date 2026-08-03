#!/bin/bash
# Mission E — commit the governed media quality policy layer.
#
# The sandbox cannot delete .git/index.lock (Operation not permitted), so this
# has to run from your own terminal. It stages an explicit file list so the
# concurrent Orders/Messages work in the tree is NOT swept in.

set -e
cd ~/Desktop/CoinPilotX

# Clear the stale lock left behind by a crashed git process.
rm -f .git/index.lock

git add -- \
  config/realtime-audio-protected-paths.json \
  mobile-native/package.json \
  mobile-native/src/api/calls.ts \
  mobile-native/src/calls/useNativeCallRoom.ts \
  mobile-native/src/core/mediaQualityPolicy.ts \
  mobile-native/src/core/mediaAdaptationController.ts \
  mobile-native/src/core/mediaQualityFlags.ts \
  mobile-native/src/core/mediaQualityTelemetry.ts \
  mobile-native/src/core/__tests__/mediaQualityPolicy.test.ts \
  mobile-native/src/core/__tests__/mediaAdaptationController.test.ts \
  mobile-native/src/core/__tests__/mediaQualityWiring.test.ts \
  mobile-native/src/live/liveSession.ts \
  mobile-native/src/live/useLiveBroadcastRoom.ts \
  mobile-native/src/live/__tests__/liveSession.test.ts \
  reports/realtime_audio_change_declaration.md \
  reports/realtime_audio_change_declaration_history.md \
  reports/pulsesoc_elite_media_quality_report.md

# Confirm exactly 17 files are staged and nothing else.
echo "--- staged ---"
git diff --cached --name-only

git commit -m "feat(media-quality): add governed quality policy layer

Resolves audio and video settings through a single pure policy layer that
both room adapters hand to new Room({...}), behind
REALTIME_MEDIA_QUALITY_V2_ENABLED (default off). The stable profile
reproduces the verified baseline byte for byte, so the kill switch restores
known-good behaviour immediately.

Audio is never placed on the degradation ladder: the adaptation reducer
degrades bitrate, then resolution, then frame rate, and cannot deactivate
the microphone or reconnect the room. Viewer publish permissions and the
audio ownership, publication, subscription, routing, and cleanup
architecture are unchanged.

Adds the four policy modules to the protected-path manifest and the import
boundary, plus three regression suites proving stable equals baseline,
elite cannot bypass audio ownership, and disabling V2 restores stable.
Cross-checks the frozen audio literals against the tagged commit
realtime-audio-stable-v1 rather than against editable constants."

git push origin codex/store-dashboard-live

echo "--- done ---"
git log --oneline -1
