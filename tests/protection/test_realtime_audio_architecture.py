from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "mobile-native" / "src"

APPROVED_PLATFORM_FILES = {
    NATIVE / "core" / "realtimeAudioEngine.ts",
    NATIVE / "core" / "realtimeMicrophonePublisher.ts",
}


def _source_files():
    yield from NATIVE.rglob("*.ts")
    yield from NATIVE.rglob("*.tsx")


class RealtimeAudioArchitectureTests(unittest.TestCase):
    def test_global_audio_session_mutation_stays_inside_shared_platform(self) -> None:
        forbidden = (
            ".setAppleAudioConfiguration(",
            ".startAudioSession(",
            ".stopAudioSession(",
            ".selectAudioOutput(",
            "audioSession.showAudioRoutePicker(",
        )
        violations = []
        for path in _source_files():
            if "__tests__" in path.parts or path in APPROVED_PLATFORM_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{path.relative_to(ROOT)} uses {marker}")
        self.assertFalse(violations, "Realtime audio-session controls bypassed the platform:\n" + "\n".join(violations))

    def test_livekit_microphone_publication_stays_inside_shared_platform(self) -> None:
        forbidden = (
            "localParticipant.setMicrophoneEnabled(",
            "localParticipant.publishTrack(",
            "localParticipant.unpublishTrack(",
        )
        violations = []
        for path in _source_files():
            if "__tests__" in path.parts or path in APPROVED_PLATFORM_FILES:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{path.relative_to(ROOT)} uses {marker}")
        self.assertFalse(violations, "Unmanaged realtime microphone publication found:\n" + "\n".join(violations))

    def test_calls_and_live_both_use_session_scoped_leases(self) -> None:
        for relative in ("calls/useNativeCallRoom.ts", "live/useLiveBroadcastRoom.ts"):
            text = (NATIVE / relative).read_text(encoding="utf-8")
            self.assertIn("RealtimeAudioLease", text)
            self.assertIn("audioLeaseRef", text)
            self.assertIn("releaseRealtimeAudioSession", text)
            self.assertNotIn("audioOwnerIdRef", text)


if __name__ == "__main__":
    unittest.main()
