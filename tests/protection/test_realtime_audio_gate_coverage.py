"""The protection gate must see the Agora runtime the app actually executes.

Why this file exists
--------------------
The real-time audio manifest was written during the LiveKit era and never fully
followed the migration to Agora. Protection is applied by ``categories[].paths``
and nothing else — being named in ``forbidden_apis.allowed_paths`` or in
``import_boundary.modules`` does NOT cause a file to be diff-gated. Three files
that own live audio had drifted out of that list:

``mobile-native/src/live/useAgoraLiveBroadcastRoom.ts``
    The canonical Live runtime. Holds one of the app's two
    ``createAgoraRtcEngine`` call sites and makes thirteen direct Agora audio
    control calls. The manifest named only ``useLiveBroadcastRoom.ts``, which
    the Agora migration reduced to a re-export shim.

``mobile-native/src/calls/callSessionStore.ts``
    The other ``createAgoraRtcEngine`` owner, driving ``enableAudio``,
    ``muteLocalAudioStream`` and ``setEnableSpeakerphone`` for every call.

``mobile-native/src/live/liveAudioMatrix.ts``
    Decides who publishes, who subscribes, and who may own the microphone. It
    imports no Agora symbol by design, which is exactly why a wrong answer here
    survives an Agora-focused review.

These tests run the REAL gate as a subprocess — the same script and the same
manifest CI runs — and assert on its actual exit codes and JSON. They do not
read the manifest and look for filenames, because that would only prove the
manifest says what the manifest says; it would not prove the gate reaches the
same conclusion, which is the thing that failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "realtime_audio_change_gate.py"

CANONICAL_LIVE_RUNTIME = "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts"
CANONICAL_CALL_OWNER = "mobile-native/src/calls/callSessionStore.ts"
AUDIO_POLICY_MATRIX = "mobile-native/src/live/liveAudioMatrix.ts"

# The Agora-era files the pre-fix manifest could not see. A change touching only
# these would have merged with no declaration and no audio validation.
PREVIOUSLY_INVISIBLE = (
    CANONICAL_LIVE_RUNTIME,
    CANONICAL_CALL_OWNER,
    AUDIO_POLICY_MATRIX,
    "mobile-native/src/live-audio/liveAudioEngine.ts",
    "mobile-native/src/live-audio/liveAudioNative.ts",
    "mobile-native/src/live-audio/liveMicrophonePublisher.ts",
    "mobile-native/src/live-audio/livePublisherMedia.ts",
)

# Files with no audio ownership at all. Over-triggering is its own failure: a
# gate that fires on every UI change is one developers learn to route around.
UI_ONLY = (
    "mobile-native/src/screens/SettingsScreen.tsx",
    "mobile-native/src/components/ProfileHeader.tsx",
    "mobile-native/src/screens/BookmarksScreen.tsx",
)

# The multi-guest decision layer, added to the manifest on 2026-09-04. Every one
# of these is a pure module with no Agora import, and every one is consulted --
# directly or one hop away -- by the engine owner before it joins, publishes,
# re-roles or tears down. That combination is the reason they were invisible:
# a reviewer grepping for Agora symbols finds nothing in any of them.
#
# The category each belongs to is asserted as well as the fact of protection,
# because a path silently landing in the wrong category still reads as covered
# while telling a declaration author the wrong thing about what they changed.
MULTI_GUEST_DECISION_LAYER = {
    "mobile-native/src/live/liveSeatReconciliation.ts": "live_seat_and_identity_authority",
    "mobile-native/src/live/liveSessionLifecycle.ts": "live_seat_and_identity_authority",
    "mobile-native/src/live/liveParticipantRegistry.ts": "live_seat_and_identity_authority",
    "mobile-native/src/live/liveStreamQuality.ts": "media_adaptation_policy",
    "mobile-native/src/live/liveMediaOwnership.ts": "microphone_track_and_publication_controller",
    "mobile-native/src/live/liveMusicMixing.ts": "livestream_audio_adapter",
}

# Live modules deliberately left unprotected, with the reason each one cannot
# reach the engine. Asserting these is not symmetry for its own sake: an
# over-broad manifest is the failure mode that makes the gate routine, and
# "someone quietly added the whole live/ directory" is exactly how that happens.
DELIBERATELY_UNPROTECTED_LIVE = (
    "mobile-native/src/live/liveStageLayout.ts",
    "mobile-native/src/live/agoraLiveTelemetry.ts",
    "mobile-native/src/live/liveGuestStage.ts",
    "mobile-native/src/live/liveEventContinuity.ts",
    "mobile-native/src/live/liveStudioReadiness.ts",
)


def run_gate(changed: list[str], *, skip_declaration: bool = True):
    """Invoke the real gate with a synthetic changed-file list."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
        handle.write("\n".join(changed))
        listing = handle.name
    argv = [sys.executable, str(GATE), "--changed-files-from", listing, "--json"]
    if skip_declaration:
        argv.append("--skip-declaration")
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
    Path(listing).unlink(missing_ok=True)
    if proc.returncode not in (0, 1):
        raise AssertionError(f"gate crashed ({proc.returncode}): {proc.stderr}")
    return proc.returncode, json.loads(proc.stdout)


class GateSeesCanonicalAgoraPaths(unittest.TestCase):
    """Each owner path, changed alone, must trip the gate on its own."""

    def _assert_protected(self, path: str, expected_category: str):
        code, out = run_gate([path])
        self.assertTrue(
            out["protected"],
            f"{path} changed but the gate reported no protected audio change",
        )
        hits = {h["path"]: h["category"] for h in out["hits"]}
        self.assertIn(path, hits, f"{path} is not among the gate's hits: {hits}")
        self.assertEqual(hits[path], expected_category)
        self.assertEqual(code, 0)  # 0 because declaration checking is skipped

    def test_canonical_live_agora_runtime_is_protected(self):
        self._assert_protected(CANONICAL_LIVE_RUNTIME, "livestream_audio_adapter")

    def test_canonical_call_agora_owner_is_protected(self):
        self._assert_protected(CANONICAL_CALL_OWNER, "audio_and_video_call_adapter")

    def test_live_audio_policy_matrix_is_protected(self):
        self._assert_protected(AUDIO_POLICY_MATRIX, "livestream_audio_adapter")

    def test_the_legacy_shim_is_still_protected(self):
        """Migration shims stay guarded. ``useLiveBroadcastRoom`` is now a
        three-line re-export, but it is the import point four screens bind to,
        so changing what it forwards to silently reroutes Live audio."""
        self._assert_protected(
            "mobile-native/src/live/useLiveBroadcastRoom.ts", "livestream_audio_adapter"
        )

    def test_every_previously_invisible_agora_path_is_now_gated(self):
        """The whole set, each on its own, so one regression cannot hide behind
        another file in the same commit happening to be protected."""
        for path in PREVIOUSLY_INVISIBLE:
            with self.subTest(path=path):
                _, out = run_gate([path])
                self.assertTrue(
                    out["protected"],
                    f"{path} owns real-time audio but the gate ignores it",
                )


class GateSeesTheMultiGuestDecisionLayer(unittest.TestCase):
    """The modules that decide what the engine is told, rather than telling it.

    ``liveAudioMatrix`` was already protected on exactly this reasoning -- it
    holds no Agora import, which is why a wrong answer there survives review.
    The multi-guest work added six more modules with the same shape and none of
    them inherited the protection. The sharpest of them is
    ``liveSeatReconciliation``: it is the sole gate on the ``rejoin`` action,
    and ``rejoin`` is the only path that destroys the engine, stops the camera,
    drops the microphone and restarts the audio session. Widening its endpoint
    comparison by one field turns every routine token refresh into a teardown of
    a live broadcast, with no Agora symbol anywhere in the diff.
    """

    def test_each_decision_module_trips_the_gate_on_its_own(self):
        for path, expected_category in MULTI_GUEST_DECISION_LAYER.items():
            with self.subTest(path=path):
                code, out = run_gate([path])
                self.assertTrue(
                    out["protected"],
                    f"{path} decides Live RTC behaviour but the gate ignores it",
                )
                hits = {h["path"]: h["category"] for h in out["hits"]}
                self.assertEqual(hits.get(path), expected_category)
                self.assertEqual(code, 0)  # 0 because declaration checking is skipped

    def test_their_suites_cannot_be_weakened_without_a_declaration(self):
        """Deleting the test is the quietest way to delete the protection.

        These six modules are pure and exhaustively testable without a device,
        so their suites are the entire runtime evidence that the invariants
        hold. A diff that removed both the assertion and the module's ability to
        break would otherwise merge in silence.
        """
        suites = [
            "mobile-native/src/live/__tests__/liveSeatReconciliation.test.ts",
            "mobile-native/src/live/__tests__/liveSessionLifecycle.test.ts",
            "mobile-native/src/live/__tests__/liveParticipantRegistry.test.ts",
            "mobile-native/src/live/__tests__/liveStreamQuality.test.ts",
            "mobile-native/src/live/__tests__/liveMediaOwnership.test.ts",
            "mobile-native/src/live/__tests__/liveMusicMixing.test.ts",
            "mobile-native/src/live/__tests__/multiGuestBroadcastScenarios.test.ts",
        ]
        for path in suites:
            with self.subTest(path=path):
                _, out = run_gate([path])
                self.assertTrue(out["protected"], f"{path} can be weakened silently")


class GateDoesNotOverTrigger(unittest.TestCase):
    def test_ui_only_change_does_not_demand_an_audio_declaration(self):
        code, out = run_gate(list(UI_ONLY), skip_declaration=False)
        self.assertFalse(out["protected"], f"UI-only change wrongly flagged: {out}")
        self.assertEqual(out["hits"], [])
        self.assertEqual(code, 0)

    def test_a_ui_change_alongside_an_audio_change_still_flags_only_the_audio(self):
        _, out = run_gate([*UI_ONLY, CANONICAL_LIVE_RUNTIME])
        hits = {h["path"] for h in out["hits"]}
        self.assertEqual(hits, {CANONICAL_LIVE_RUNTIME})

    def test_live_modules_with_no_engine_reach_stay_unprotected(self):
        """The boundary drawn in ``live_seat_and_identity_authority``.

        Six modules in ``live/`` were added to the manifest and five of their
        neighbours deliberately were not, on the single criterion of whether the
        module can alter engine lifecycle, membership, publication,
        subscription, mic ownership, audio scenario or role transitions.
        ``liveStageLayout`` is the closest call and the clearest illustration:
        the engine owner imports it, but only for ``reduceActiveSpeaker``, which
        smooths Agora's volume indication into a highlight ring. Active speaker
        is never a reordering and never a subscription change, so the module
        cannot move audio.

        Asserting the negative keeps the criterion honest. Without it the
        cheapest response to any future scare is to add another live/ file, and
        a manifest that protects everything protects nothing -- it just makes
        the declaration a formality people learn to fill in without reading.
        """
        code, out = run_gate(list(DELIBERATELY_UNPROTECTED_LIVE), skip_declaration=False)
        self.assertFalse(
            out["protected"],
            "a live module with no engine reach is now gated; if that is "
            "intentional, move it into a category and document why here: "
            f"{out['hits']}",
        )
        self.assertEqual(code, 0)


def _commit_exists(rev: str) -> bool:
    return subprocess.run(
        ["git", "cat-file", "-e", f"{rev}^{{commit}}"],
        cwd=ROOT, capture_output=True,
    ).returncode == 0


def run_gate_range(base: str, head: str):
    proc = subprocess.run(
        [sys.executable, str(GATE), "--base", base, "--head", head, "--json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode not in (0, 1):
        raise AssertionError(f"gate crashed ({proc.returncode}): {proc.stderr}")
    return proc.returncode, json.loads(proc.stdout)


class DeclarationIsBoundToTheRange(unittest.TestCase):
    """A declaration must describe THIS change, not merely exist.

    Two checks already existed: the declaration must be touched somewhere in the
    range, and it must name every changed protected file. Both are weaker than
    they look in a consolidation range of hundreds of commits — the first is
    satisfied by any declaration edit anywhere in the range, and the second is
    permanently satisfied for any file the declaration has ever named, which is
    how a live declaration in this repo already names files that later changed
    under it.

    So ordering is checked too: a declaration last written BEFORE the audio
    commit it would authorise cannot be describing it.
    """

    # Two real ranges from this repository's history, chosen so both directions
    # are pinned to fixed commits. A live range (``origin/main..HEAD``) would
    # make this class fail whenever the working branch has an audio commit
    # newer than its declaration — which is a true statement about the branch,
    # but it is the GATE's job to report it in CI, not this test's. Mixing the
    # two means a stale declaration reads as a broken mechanism.
    #
    # Stale direction: the declaration was last touched by 00cfe955, and
    # protected audio paths changed six commits later in 5c451905.
    DECL_FIRST_BASE = "00cfe955"
    AUDIO_LATER_HEAD = "5c451905"
    # Fresh direction: 8157d7de is the declaration addendum written to cover the
    # consolidation range, so within this range no audio commit is newer.
    DECL_NEWEST_HEAD = "8157d7de"

    def test_a_declaration_older_than_the_audio_change_is_rejected(self):
        if not (_commit_exists(self.DECL_FIRST_BASE) and _commit_exists(self.AUDIO_LATER_HEAD)):
            self.skipTest("history anchors unavailable in this checkout")
        code, out = run_gate_range(f"{self.DECL_FIRST_BASE}^", self.AUDIO_LATER_HEAD)
        self.assertTrue(out["protected"])
        self.assertTrue(
            any("predates the change it would authorise" in p
                for p in out["declaration_problems"]),
            f"stale declaration accepted: {out['declaration_problems']}",
        )
        self.assertEqual(code, 1, "a stale declaration must fail the gate")

    def test_ordering_check_does_not_fire_when_the_declaration_is_newest(self):
        """The complement, so the check cannot pass by always failing.

        Same protected files as the test above, same gate, opposite verdict —
        the only difference is where the declaration sits in the ordering.
        """
        if not (_commit_exists(self.DECL_FIRST_BASE) and _commit_exists(self.DECL_NEWEST_HEAD)):
            self.skipTest("history anchors unavailable in this checkout")
        _, out = run_gate_range(f"{self.DECL_FIRST_BASE}^", self.DECL_NEWEST_HEAD)
        self.assertTrue(out["protected"], "range chosen for the complement has no audio change")
        self.assertFalse(
            any("predates the change it would authorise" in p
                for p in out["declaration_problems"]),
            "ordering check fired even though the declaration is the newer commit",
        )


class ManifestIntegrity(unittest.TestCase):
    """Guards against the fix rotting: a path that no longer exists is dead
    protection that still reads as coverage."""

    def test_every_protected_path_exists_on_disk(self):
        manifest = json.loads(
            (ROOT / "config" / "realtime-audio-protected-paths.json").read_text()
        )
        missing = [
            path
            for category in manifest["categories"]
            for path in category["paths"]
            if not (ROOT / path).exists()
        ]
        self.assertEqual(missing, [], f"protected paths not on disk: {missing}")

    def test_protection_is_read_from_categories_not_allowlists(self):
        """Pins the failure mode that caused this whole gap: the audio core
        modules were named as allowed owners in six ``forbidden_apis`` rules and
        in ``import_boundary.modules``, which reads like protection and is not.
        Only ``categories[].paths`` gates a diff."""
        manifest = json.loads(
            (ROOT / "config" / "realtime-audio-protected-paths.json").read_text()
        )
        gated = {p for c in manifest["categories"] for p in c["paths"]}
        for path in PREVIOUSLY_INVISIBLE:
            with self.subTest(path=path):
                self.assertIn(path, gated)


if __name__ == "__main__":
    unittest.main()
