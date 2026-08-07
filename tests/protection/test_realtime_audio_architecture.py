"""Backend-side reader of the real-time audio protected boundary.

There are two enforcement points for the same rules: this module and
``mobile-native/src/core/__tests__/realtimeAudioArchitecture.test.ts``. Both read
``config/realtime-audio-protected-paths.json`` rather than carrying their own
copy of the rules, because two hand-maintained copies drift and the drift is
invisible until the day one of them stops protecting something.

Why the duplication exists at all: the native suite only runs when someone runs
Jest, and a backend-only pull request may not run it. This module runs in the
Python protection suite, so a backend change that widens the boundary is caught
even when no native test executes.

The previous version of this file hard-coded a two-file allowlist. That was
narrower than reality (it did not know about ``Audio.setAudioModeAsync``) and
could not be kept in step with CI. Everything is now manifest-derived.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "config" / "realtime-audio-protected-paths.json"
NATIVE_SRC = ROOT / "mobile-native" / "src"

MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _source_files():
    """Every non-test .ts/.tsx under mobile-native/src.

    Test files are excluded because they legitimately name the forbidden APIs in
    order to mock them or to assert that they are absent.
    """
    for path in sorted(NATIVE_SRC.rglob("*.ts")):
        if "__tests__" in path.parts or "node_modules" in path.parts:
            continue
        yield path
    for path in sorted(NATIVE_SRC.rglob("*.tsx")):
        if "__tests__" in path.parts or "node_modules" in path.parts:
            continue
        yield path


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


SOURCES = [(p, _relative(p), p.read_text(encoding="utf-8")) for p in _source_files()]


class ManifestIntegrityTests(unittest.TestCase):
    """The manifest has to be usable before anything it says can be enforced."""

    def test_manifest_version_is_the_one_this_reader_understands(self) -> None:
        self.assertEqual(MANIFEST["manifest_version"], 1)

    def test_every_protected_path_points_at_a_file_that_exists(self) -> None:
        missing = []
        for category in MANIFEST["categories"]:
            for rel in category["paths"]:
                if not (ROOT / rel).exists():
                    missing.append(f"{category['id']} -> {rel}")
        for rel in MANIFEST["dependency_watch"]["files"]:
            if not (ROOT / rel).exists():
                missing.append(f"dependency_watch -> {rel}")
        # A manifest entry pointing at a deleted file is worse than no entry:
        # the change gate silently stops protecting whatever replaced it.
        self.assertEqual(missing, [], "protected paths that no longer exist:\n" + "\n".join(missing))

    def test_every_forbidden_rule_can_actually_enforce_something(self) -> None:
        for rule in MANIFEST["forbidden_apis"]:
            self.assertTrue(rule["markers"], f"{rule['id']} has no markers and enforces nothing")
            self.assertTrue(rule["reason"], f"{rule['id']} has no reason and cannot be argued with in review")
            for allowed in rule["allowed_paths"]:
                # No wildcards: a directory allowlist would let a new file
                # dropped into core/ bypass the boundary on the day it is added.
                self.assertNotIn("*", allowed, f"{rule['id']} allowlist uses a wildcard: {allowed}")
                self.assertTrue(
                    allowed.endswith((".ts", ".tsx", ".py")),
                    f"{rule['id']} allowlist entry is not a source file: {allowed}",
                )
                self.assertTrue((ROOT / allowed).exists(), f"{rule['id']} allows a missing file: {allowed}")

    def test_backend_diff_patterns_are_present_for_the_change_gate(self) -> None:
        patterns = MANIFEST["backend_diff_patterns"]
        # bot.py is protected by diff content rather than by path. With no
        # patterns, every backend audio change would pass the gate unnoticed.
        self.assertTrue(patterns)
        for required in ("pulse_livekit_", "LIVEKIT_", "can_publish"):
            self.assertIn(required, patterns)


class ForbiddenApiTests(unittest.TestCase):
    """Each manifest rule, applied to the whole native source tree."""

    def test_forbidden_apis_stay_inside_their_allowlists(self) -> None:
        failures = []
        for rule in MANIFEST["forbidden_apis"]:
            allowed = set(rule["allowed_paths"])
            violations = [
                f"  {rel} uses {marker}"
                for _path, rel, text in SOURCES
                if rel not in allowed
                for marker in rule["markers"]
                if marker in text
            ]
            if violations:
                failures.append(f"{rule['id']}: {rule['title']}\n" + "\n".join(violations) + f"\n  reason: {rule['reason']}")
        self.assertEqual(failures, [], "\n\n".join(failures))

    def test_expo_av_allowlist_stays_frozen_at_the_baseline_size(self) -> None:
        rule = next(r for r in MANIFEST["forbidden_apis"] if r["id"] == "expo_av_global_audio_mode")
        # These files already mutated the global audio mode at the verified
        # baseline. They are frozen rather than rewritten because this hard-lock
        # must not change working runtime behavior. A seventh entry means
        # someone widened the boundary instead of routing through an owner.
        self.assertTrue(rule.get("frozen_at_baseline"))
        self.assertLessEqual(len(rule["allowed_paths"]), rule.get("max_allowed_paths", 6))


class ImportBoundaryTests(unittest.TestCase):
    """The narrow public API: who is allowed to reach the audio core at all."""

    def test_audio_core_is_reachable_only_from_the_approved_adapters(self) -> None:
        boundary = MANIFEST["import_boundary"]
        allowed = set(boundary["allowed_importers"])
        violations = []
        for _path, rel, text in SOURCES:
            if rel in allowed:
                continue
            for module in boundary["modules"]:
                leaf = module.rsplit("/", 1)[-1]
                # Match the module's last segment in any relative specifier, so
                # moving a file one directory deeper does not evade the rule.
                if re.search(rf"""from\s+["'][^"']*\b{re.escape(leaf)}["']""", text):
                    violations.append(f"  {rel} imports {module}")
        self.assertEqual(violations, [], "\n".join(violations) + f"\n\n{boundary['reason']}")

    def test_every_approved_importer_is_a_real_file(self) -> None:
        # A stale entry names a file that no longer exists, and the next file
        # created at that path inherits an exemption nobody granted.
        for rel in MANIFEST["import_boundary"]["allowed_importers"]:
            self.assertTrue((ROOT / rel).exists(), f"approved importer does not exist: {rel}")


class LeaseDisciplineTests(unittest.TestCase):
    def test_calls_and_live_each_own_a_call_grade_publisher_coordinator(self) -> None:
        # Calls and Live no longer share one module. Live owns a copy at
        # src/live-audio/ so a change made for a broadcast cannot reach a call.
        # What still has to hold is that both run the SAME sequence, so this
        # asserts the two are on separate modules and that neither can reach
        # the other's.
        call = (ROOT / "mobile-native/src/calls/useNativeCallRoom.ts").read_text(encoding="utf-8")
        self.assertIn('from "../core/realtimePublisherMedia"', call)
        self.assertIn("initializeCallGradePublisherMedia({", call)
        self.assertNotIn("live-audio/", call, "the call path must never import Live's copy")

        live = (ROOT / "mobile-native/src/live/useLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        self.assertIn('from "../live-audio/livePublisherMedia"', live)
        self.assertIn("initializeCallGradePublisherMedia({", live)
        for module in (
            "realtimeAudioEngine",
            "realtimeMicrophonePublisher",
            "realtimePublisherMedia",
            "realtimeAudioNative",
        ):
            self.assertNotIn(
                f'from "../core/{module}"',
                live,
                f"Live still imports the call-owned {module}; the copy is not authoritative",
            )

    def test_the_live_publisher_copy_stays_identical_to_the_call_original(self) -> None:
        # A copy that is allowed to drift is worse than no copy: it looks like
        # the working implementation while behaving differently. Comments and
        # the Realtime/Live renaming are stripped, so anything left is a real
        # behavioural divergence and fails here.
        def strip(text: str) -> str:
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            text = re.sub(r"//.*$", "", text, flags=re.M)
            text = re.sub(r"[Rr]ealtime|[Ll]ive", "", text)
            return re.sub(r"\s+", "", text)

        original = strip((ROOT / "mobile-native/src/core/realtimePublisherMedia.ts").read_text(encoding="utf-8"))
        copy = strip((ROOT / "mobile-native/src/live-audio/livePublisherMedia.ts").read_text(encoding="utf-8"))
        self.assertEqual(copy, original, "Live's publisher copy has drifted from the call original")

    def test_both_room_adapters_release_audio_by_lease_not_by_owner_name(self) -> None:
        discipline = MANIFEST["required_lease_discipline"]
        for rel in discipline["files"]:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for needle in discipline["must_contain"]:
                self.assertIn(needle, text, f"{rel} is missing {needle}")
            for needle in discipline["must_not_contain"]:
                # audioOwnerIdRef is the pre-baseline owner-name pattern. Its
                # return means a delayed cleanup can once again release a
                # session a newer feature has since acquired.
                self.assertNotIn(needle, text, f"{rel} reintroduced {needle}")

    def test_live_host_output_path_is_explicitly_enabled(self) -> None:
        # AVAudioEngine will not run without an ENABLED output, and with the
        # engine down the input delivers no buffers either - so a host whose
        # output was never enabled publishes a track carrying no energy while
        # the ADM still reports inputEnabled/inputRunning true. Output is
        # normally enabled by subscribing to remote audio; a host subscribes to
        # nobody, so only an explicit initPlayout enables it. Verified silent on
        # P3r7or 2026-08-07, then verified audible after this fix.
        discipline = MANIFEST["required_output_enable_discipline"]
        for rel in discipline["files"]:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for needle in discipline["must_contain"]:
                self.assertIn(needle, text, f"{rel} is missing {needle}")

        # The native half lives in a patch over @livekit/react-native-webrtc.
        # node_modules is gitignored, so a bridge present only there builds
        # green on one machine and is absent everywhere else - and the JS falls
        # back to a no-op rather than raising, making the loss silent all the
        # way to a dead broadcast. The patch file is the only durable record.
        for rel in discipline["patch_files"]:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for needle in discipline["patch_must_contain"]:
                self.assertIn(needle, text, f"{rel} no longer bridges {needle}")

    def test_livekit_sdk_never_configures_the_session_behind_the_coordinator(self) -> None:
        for rel in (
            "mobile-native/src/calls/useNativeCallRoom.ts",
            "mobile-native/src/live/useLiveBroadcastRoom.ts",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            calls = re.findall(r"registerGlobals\(\{[^}]*\}\)", text)
            self.assertTrue(calls, f"{rel} no longer calls registerGlobals")
            for call in calls:
                # Allowing the call without checking its argument would permit
                # the one variant that breaks everything.
                self.assertIn("autoConfigureAudioSession: false", call, f"{rel}: {call}")


class LiveStartupTraceContractTests(unittest.TestCase):
    def test_trace_schema_cannot_drop_required_lifecycle_evidence(self) -> None:
        contract = MANIFEST["live_startup_trace_contract"]
        trace = (ROOT / contract["path"]).read_text(encoding="utf-8")
        for event in contract["required_events"]:
            self.assertIn(f'"{event}"', trace, f"trace no longer declares required event {event}")
        for field in contract["required_fields"]:
            self.assertRegex(trace, rf"\b{re.escape(field)}\b", f"trace no longer declares required field {field}")

    def test_camera_engine_lifecycle_regression_runs_in_critical_ci(self) -> None:
        contract = MANIFEST["live_startup_trace_contract"]
        pkg = json.loads((ROOT / "mobile-native" / "package.json").read_text(encoding="utf-8"))
        critical = pkg["scripts"]["test:realtime-audio-critical"]
        self.assertIn(contract["critical_test"], critical)


class AuthoritativeLiveRuntimeTests(unittest.TestCase):
    def test_live_ui_uses_runtime_commands_not_transport_commands(self) -> None:
        paths = [
            "mobile-native/src/screens/LiveHostSessionScreen.tsx",
            "mobile-native/src/screens/LiveScreen.tsx",
            "mobile-native/src/components/reels/ReelLiveViewerSurface.tsx",
        ]
        forbidden = ("room.connect(", "room.disconnect(", "{ connect, disconnect")
        for rel in paths:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{rel} bypasses LiveRuntime with {marker}")

    def test_screens_do_not_import_livekit_room_transport(self) -> None:
        for folder in (ROOT / "mobile-native/src/screens", ROOT / "mobile-native/src/components"):
            for path in list(folder.rglob("*.ts")) + list(folder.rglob("*.tsx")):
                text = path.read_text(encoding="utf-8")
                self.assertNotRegex(text, r"from\s+['\"]livekit-client['\"]", f"{_relative(path)} imports LiveKit transport")


class DependencyLockTests(unittest.TestCase):
    def test_audio_critical_dependencies_match_the_verified_baseline(self) -> None:
        pkg = json.loads((ROOT / "mobile-native" / "package.json").read_text(encoding="utf-8"))
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        watch = MANIFEST["dependency_watch"]
        for name in watch["must_be_exactly_pinned"]:
            # Equality against the recorded baseline, not a range check: the
            # media stack must not move without someone editing the manifest,
            # which the change gate then treats as a protected change.
            self.assertEqual(deps.get(name), watch["baseline_versions"][name], f"{name} moved off the baseline")

    def test_ios_microphone_and_background_audio_configuration_is_intact(self) -> None:
        app = json.loads((ROOT / "mobile-native" / "app.json").read_text(encoding="utf-8"))
        info_plist = app.get("expo", {}).get("ios", {}).get("infoPlist", {})
        # Without the usage description iOS denies the microphone outright;
        # without the audio background mode a backgrounded call goes silent.
        # Neither failure is visible in a simulator run or a unit test.
        self.assertTrue(str(info_plist.get("NSMicrophoneUsageDescription", "")).strip())
        self.assertIn("audio", info_plist.get("UIBackgroundModes", []))

    def test_the_critical_test_script_covers_the_declared_command(self) -> None:
        pkg = json.loads((ROOT / "mobile-native" / "package.json").read_text(encoding="utf-8"))
        scripts = pkg.get("scripts", {})
        for key in ("test:realtime-audio-critical", "test:realtime-audio", "test:realtime-audio-architecture"):
            # The manifest names these commands as the required validation. A
            # renamed script would make the CI workflow fail open.
            self.assertIn(key, scripts, f"package.json is missing the {key} script named by the manifest")


if __name__ == "__main__":
    unittest.main()
