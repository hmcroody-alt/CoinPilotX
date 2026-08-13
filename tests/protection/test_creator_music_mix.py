"""Contract tests for the server-side creator music mix.

Two properties matter more than the rest and are asserted from several angles:

  1. A take with no music must produce the command it produced before this
     feature existed. The music path is additive or it is a regression.
  2. The mix must be built from the catalogue's original file. There is no code
     path here that reads audio captured by the microphone and calls it music.
"""

import json
import math
import re
import unittest

from services import creator_music_mix as mix


def fields(**overrides):
    base = {
        "music_track_id": "8821",
        "music_artist_user_id": "5501",
        "music_rights_ref": "pulsesoc_commercial_v2",
        "music_title": "Midnight Drive",
        "music_artist": "Ava Lang",
        "music_start_offset_seconds": "32",
        "music_duration_seconds": "214",
        "music_level": "0.45",
        "mic_level": "0.85",
        "music_preset": "balanced",
        "music_duck_enabled": "1",
        "music_duck_threshold_db": "-34",
        "music_duck_depth_db": "7",
        "music_duck_attack_ms": "120",
        "music_duck_release_ms": "420",
    }
    unknown = set(overrides) - set(base)
    if unknown:
        # A typo'd override would otherwise be added as a field nothing reads,
        # and the test would assert against the unmodified default while looking
        # like it exercised the case.
        raise AssertionError(f"unknown upload field(s) in override: {sorted(unknown)}")
    base.update(overrides)
    return base


def spec(**overrides):
    parsed = mix.parse_creator_music_fields(fields(**overrides))
    return mix.build_mix_spec(parsed, "/var/catalog/8821.m4a")


class GainStagingParity(unittest.TestCase):
    """These numbers must match creatorMixer.ts or the phone's preview lies."""

    def test_fader_taper_is_square_law_with_exact_endpoints(self):
        self.assertEqual(mix.fader_to_linear_gain(0), 0.0)
        self.assertEqual(mix.fader_to_linear_gain(1), 1.0)
        self.assertAlmostEqual(mix.fader_to_linear_gain(0.5), 0.25)

    def test_silence_is_a_floor_not_negative_infinity(self):
        self.assertEqual(mix.linear_gain_to_db(0), -120.0)
        self.assertTrue(math.isfinite(mix.music_bus_gain_db(0)))

    def test_headroom_is_applied_on_top_of_the_fader(self):
        self.assertAlmostEqual(mix.music_bus_gain_db(1), mix.MUSIC_HEADROOM_DB, places=6)
        self.assertAlmostEqual(mix.mic_bus_gain_db(1), mix.MIC_HEADROOM_DB, places=6)

    def test_two_sources_at_unity_cannot_sum_to_clipping(self):
        summed = mix.db_to_linear_gain(mix.music_bus_gain_db(1)) + mix.db_to_linear_gain(mix.mic_bus_gain_db(1))
        self.assertLess(summed, 1.65)

    def test_ceiling_sits_below_full_scale_for_encoder_overshoot(self):
        self.assertLess(mix.MIX_CEILING_DB, 0)


class FieldParsing(unittest.TestCase):
    def test_no_track_means_no_music_request(self):
        self.assertEqual(mix.parse_creator_music_fields({}), {})
        self.assertEqual(mix.parse_creator_music_fields({"music_track_id": "  "}), {})

    def test_levels_are_clamped_rather_than_trusted(self):
        parsed = mix.parse_creator_music_fields(fields(music_level="40", mic_level="-3"))
        self.assertEqual(parsed["music_level"], 1.0)
        self.assertEqual(parsed["mic_level"], 0.0)

    def test_ducking_parameters_are_clamped_to_the_musical_range(self):
        parsed = mix.parse_creator_music_fields(
            fields(music_duck_depth_db="90", music_duck_attack_ms="0", music_duck_release_ms="99999")
        )
        self.assertLessEqual(parsed["duck_depth_db"], 18.0)
        self.assertGreaterEqual(parsed["duck_attack_ms"], 20.0)
        self.assertLessEqual(parsed["duck_release_ms"], 4000.0)

    def test_garbage_numbers_fall_back_instead_of_producing_nan(self):
        parsed = mix.parse_creator_music_fields(fields(music_level="banana", music_duck_depth_db="NaN"))
        self.assertTrue(math.isfinite(parsed["music_level"]))
        self.assertTrue(math.isfinite(parsed["duck_depth_db"]))

    def test_unknown_preset_degrades_to_custom(self):
        self.assertEqual(mix.parse_creator_music_fields(fields(music_preset="party"))["preset"], "custom")

    def test_negative_start_offset_is_refused(self):
        self.assertEqual(mix.parse_creator_music_fields(fields(music_start_offset_seconds="-8"))["start_offset_seconds"], 0.0)


class DuckingDerivation(unittest.TestCase):
    def test_zero_depth_means_no_compression(self):
        self.assertEqual(mix.duck_ratio_for_depth(0), 1.0)

    def test_deeper_ducking_asks_for_a_higher_ratio(self):
        self.assertGreater(mix.duck_ratio_for_depth(10), mix.duck_ratio_for_depth(3.5))

    def test_ratio_never_runs_away_into_gating(self):
        self.assertLessEqual(mix.duck_ratio_for_depth(18), 20.0)
        self.assertTrue(math.isfinite(mix.duck_ratio_for_depth(18)))

    def test_derived_ratio_produces_roughly_the_requested_depth(self):
        for depth in (3.5, 7.0, 10.0):
            ratio = mix.duck_ratio_for_depth(depth)
            produced = mix.ASSUMED_SPEECH_EXCESS_DB * (1 - 1 / ratio)
            self.assertAlmostEqual(produced, depth, delta=0.35)


class Filtergraph(unittest.TestCase):
    def test_mic_is_high_passed_and_music_is_not(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=True)
        self.assertIn(f"highpass=f={mix.MIC_HIGHPASS_HZ:g}", graph)
        # The music lane gets only a subsonic filter, well below the mic's.
        self.assertIn(f"highpass=f={mix.MUSIC_HIGHPASS_HZ:g}", graph)
        self.assertLess(mix.MUSIC_HIGHPASS_HZ, mix.MIC_HIGHPASS_HZ)

    def test_bass_is_regulated_on_the_music_lane(self):
        self.assertIn("bass=g=-2.5", mix.build_music_mix_filtergraph(spec(), has_mic_audio=True))

    def test_voice_focus_trims_more_bass_than_music_focus(self):
        self.assertLess(
            mix.BASS_TRIM_DB_BY_PRESET["voice_focus"],
            mix.BASS_TRIM_DB_BY_PRESET["music_focus"],
        )

    def test_amix_normalisation_is_off_so_faders_survive_the_sum(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=True)
        self.assertIn("normalize=0", graph)

    def test_ducking_is_driven_by_the_post_gain_microphone(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=True)
        self.assertIn("[mic]asplit=2[micmix][micsc]", graph)
        self.assertIn("[music][micsc]sidechaincompress=", graph)

    def test_ducking_off_leaves_no_dangling_sidechain_output(self):
        graph = mix.build_music_mix_filtergraph(spec(music_duck_enabled="0"), has_mic_audio=True)
        self.assertNotIn("sidechaincompress", graph)
        self.assertIn("anullsink", graph)

    def test_limiter_runs_after_loudness_so_the_ceiling_actually_holds(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=True)
        self.assertLess(graph.index("loudnorm"), graph.index("alimiter"))

    def test_silent_take_still_gets_the_master_chain(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=False)
        self.assertNotIn("amix", graph)
        self.assertNotIn("sidechaincompress", graph)
        self.assertIn("loudnorm", graph)
        self.assertIn("alimiter", graph)
        self.assertTrue(graph.endswith("[aout]"))

    def test_every_graph_terminates_in_the_mapped_output_label(self):
        for has_mic in (True, False):
            for duck in ("1", "0"):
                graph = mix.build_music_mix_filtergraph(spec(music_duck_enabled=duck), has_mic_audio=has_mic)
                self.assertTrue(graph.endswith("[aout]"), graph)


class MixCommand(unittest.TestCase):
    def test_music_comes_from_the_catalogue_file_not_the_recording(self):
        command = mix.build_music_mix_command("/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(), True)
        self.assertIn("/var/catalog/8821.m4a", command)
        # Exactly two inputs: the recording, and the original track.
        self.assertEqual(command.count("-i"), 2)

    def test_start_offset_uses_input_seeking_ahead_of_the_music_input(self):
        command = mix.build_music_mix_command("/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(), True)
        self.assertLess(command.index("-ss"), command.index("/var/catalog/8821.m4a"))
        self.assertEqual(command[command.index("-ss") + 1], "32")

    def test_no_offset_emits_no_seek(self):
        command = mix.build_music_mix_command(
            "/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(music_start_offset_seconds="0"), True
        )
        self.assertNotIn("-ss", command)

    def test_short_track_is_looped_and_bounded_by_the_video(self):
        command = mix.build_music_mix_command("/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(), True)
        self.assertIn("-stream_loop", command)
        # Without -shortest an infinitely looped music input never ends.
        self.assertIn("-shortest", command)

    def test_video_is_encoded_once_and_the_picture_stream_is_carried_through(self):
        command = mix.build_music_mix_command("/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(), True)
        self.assertEqual(command.count("-c:v"), 1)
        self.assertIn("0:v:0", command)
        self.assertIn("[aout]", command)

    def test_faststart_is_preserved_so_playback_still_begins_before_download_ends(self):
        command = mix.build_music_mix_command("/usr/bin/ffmpeg", "/tmp/take.mov", "/tmp/out.mp4", spec(), True)
        self.assertIn("+faststart", command)


class Eligibility(unittest.TestCase):
    def approved(self, **overrides):
        track = {
            "active": 1,
            "safety_status": "approved",
            "approved_by_admin": 1,
            "commercial_use_allowed": 1,
            "remix_edit_allowed": 1,
            "audio_url": "https://cdn.pulsesoc.com/music/8821.m4a",
            "uploader_user_id": 5501,
            "license_type": "pulsesoc_commercial_v2",
            "title": "Midnight Drive",
            "artist": "Ava Lang",
            "duration_seconds": 214,
        }
        track.update(overrides)
        return track

    def test_an_approved_track_is_allowed(self):
        verdict = mix.evaluate_track_eligibility(self.approved())
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.rights_ref, "pulsesoc_commercial_v2")
        self.assertEqual(verdict.artist_user_id, 5501)

    def test_a_missing_track_is_refused(self):
        self.assertFalse(mix.evaluate_track_eligibility(None).allowed)

    def test_rights_are_rechecked_at_mix_time_not_taken_from_the_client(self):
        for field, value, reason in (
            ("active", 0, "track_inactive"),
            ("approved_by_admin", 0, "not_approved"),
            ("commercial_use_allowed", 0, "commercial_use_not_allowed"),
            ("remix_edit_allowed", 0, "remix_not_allowed"),
            ("safety_status", "review", "moderation_review"),
            ("safety_status", "removed", "moderation_removed"),
            ("audio_url", "", "no_audio_asset"),
        ):
            verdict = mix.evaluate_track_eligibility(self.approved(**{field: value}))
            self.assertFalse(verdict.allowed, f"{field}={value!r} should be refused")
            self.assertEqual(verdict.reason, reason)

    def test_mixing_requires_remix_permission_because_the_result_is_derivative(self):
        self.assertEqual(
            mix.evaluate_track_eligibility(self.approved(remix_edit_allowed=0)).reason,
            "remix_not_allowed",
        )


class Attribution(unittest.TestCase):
    def test_attribution_persists_identifiers_not_only_display_strings(self):
        payload = spec().attribution()
        self.assertEqual(payload["track_id"], "8821")
        self.assertEqual(payload["artist_user_id"], 5501)
        self.assertEqual(payload["rights_ref"], "pulsesoc_commercial_v2")
        self.assertEqual(payload["start_offset_seconds"], 32.0)
        self.assertEqual(payload["duration_seconds"], 214.0)

    def test_attribution_records_the_resolved_gains_for_later_debugging(self):
        payload = spec().attribution()
        self.assertLess(payload["music_gain_db"], 0)
        self.assertLess(payload["mic_gain_db"], 0)
        self.assertTrue(payload["duck"]["enabled"])

    def test_a_spec_round_trips_through_the_persisted_row(self):
        original = spec()
        row = {
            "music_track_id": original.track_id,
            "music_mix_json": json.dumps(fields()),
        }
        rebuilt = mix.spec_from_row(row, "/var/catalog/8821.m4a")
        self.assertIsNotNone(rebuilt)
        self.assertEqual(rebuilt.music_level, original.music_level)
        self.assertEqual(rebuilt.start_offset_seconds, original.start_offset_seconds)
        self.assertEqual(rebuilt.duck_depth_db, original.duck_depth_db)

    def test_a_row_with_no_track_produces_no_spec(self):
        self.assertIsNone(mix.spec_from_row({"music_track_id": ""}, "/var/catalog/8821.m4a"))

    def test_a_row_with_corrupt_mix_json_still_yields_a_usable_spec(self):
        rebuilt = mix.spec_from_row({"music_track_id": "8821", "music_mix_json": "{not json"}, "/x.m4a")
        self.assertIsNotNone(rebuilt)
        self.assertTrue(math.isfinite(rebuilt.music_level))


class FiltergraphIsWellFormed(unittest.TestCase):
    """Structural validation of the graph, since ffmpeg is not available here.

    ffmpeg rejects a filtergraph at runtime, not at build time, so a dangling or
    twice-consumed label would pass every string assertion in this file and then
    fail every music upload in production. These tests check the property ffmpeg
    itself checks: every label produced inside the graph is consumed exactly
    once, and the only stream left over is the one that gets mapped.
    """

    LABEL = re.compile(r"\[([A-Za-z0-9_]+)\]")

    def graph_labels(self, graph):
        produced, consumed = [], []
        for chain in graph.split(";"):
            # A chain is [in][in]filters[out][out]; inputs lead, outputs trail.
            leading = re.match(r"^((?:\[[A-Za-z0-9_]+\])+)", chain)
            trailing = re.search(r"((?:\[[A-Za-z0-9_]+\])+)$", chain)
            if leading:
                consumed += self.LABEL.findall(leading.group(1))
            if trailing and (not leading or trailing.start() > leading.end() - 1):
                produced += self.LABEL.findall(trailing.group(1))
        return produced, consumed

    def assert_balanced(self, graph):
        produced, consumed = self.graph_labels(graph)
        self.assertEqual(len(produced), len(set(produced)), f"a label is produced twice: {produced}")
        # Input pads (0:a, 1:a) are not word-labels, so everything consumed that
        # is not produced here would be a reference to a stream that never exists.
        internal = [name for name in consumed if name != "aout"]
        for name in internal:
            self.assertIn(name, produced, f"[{name}] is consumed but never produced")
        for name in produced:
            if name == "aout":
                continue
            self.assertEqual(consumed.count(name), 1, f"[{name}] is not consumed exactly once")
        self.assertEqual(produced.count("aout"), 1, "the graph must end in exactly one [aout]")
        self.assertEqual(consumed.count("aout"), 0, "[aout] is mapped, not consumed")

    def test_the_full_ducked_graph_is_balanced(self):
        self.assert_balanced(mix.build_music_mix_filtergraph(spec(), has_mic_audio=True))

    def test_the_graph_without_ducking_is_balanced(self):
        # This is the case that needs anullsink: asplit still makes a sidechain
        # tap that nothing downstream wants.
        self.assert_balanced(mix.build_music_mix_filtergraph(spec(music_duck_enabled="0"), has_mic_audio=True))

    def test_the_zero_depth_graph_is_balanced(self):
        # Ducking nominally on but with no depth takes the same branch.
        self.assert_balanced(mix.build_music_mix_filtergraph(spec(music_duck_depth_db="0"), has_mic_audio=True))

    def test_the_music_only_graph_is_balanced(self):
        self.assert_balanced(mix.build_music_mix_filtergraph(spec(), has_mic_audio=False))

    def test_the_music_only_graph_never_references_a_microphone_stream(self):
        graph = mix.build_music_mix_filtergraph(spec(), has_mic_audio=False)
        self.assertNotIn("[0:a]", graph)
        self.assertNotIn("[mic", graph)

    def test_the_sidechain_tap_is_discarded_when_ducking_is_off(self):
        graph = mix.build_music_mix_filtergraph(spec(music_duck_enabled="0"), has_mic_audio=True)
        self.assertIn("[micsc]anullsink", graph)
        self.assertNotIn("sidechaincompress", graph)

    def test_no_chain_is_empty(self):
        for chain in mix.build_music_mix_filtergraph(spec(), has_mic_audio=True).split(";"):
            self.assertTrue(chain.strip(), "an empty chain would make ffmpeg reject the graph")


class StartOffsetClamping(unittest.TestCase):
    """The server re-clamps against the catalogue's duration, not the client's."""

    def test_a_negative_offset_starts_at_the_beginning(self):
        self.assertEqual(mix.clamp_start_offset(-12, 214), 0.0)

    def test_an_offset_past_the_end_leaves_a_usable_tail(self):
        self.assertEqual(mix.clamp_start_offset(9999, 214), 214 - mix.MIN_TAIL_SECONDS)

    def test_a_track_shorter_than_the_tail_guard_starts_at_zero(self):
        self.assertEqual(mix.clamp_start_offset(9, 3), 0.0)

    def test_an_unknown_duration_passes_the_offset_through_rather_than_guessing(self):
        self.assertEqual(mix.clamp_start_offset(42, 0), 42.0)

    def test_garbage_becomes_the_start_of_the_track_not_nan(self):
        self.assertEqual(mix.clamp_start_offset("abc", "xyz"), 0.0)

    def test_it_agrees_with_the_client_clamp_it_mirrors(self):
        # CREATOR_MUSIC_MIN_TAIL_SECONDS in creatorMusicSelection.ts. If these
        # drift, a start point the picker allowed silently moves on the server.
        self.assertEqual(mix.MIN_TAIL_SECONDS, 5.0)


class RouteWorkerHandoff(unittest.TestCase):
    """The exact JSON the upload route persists must rebuild into a spec.

    This is the seam most likely to rot: the route writes flat ``music_*`` keys
    into ``music_mix_json`` and the worker reads them back through the same
    parser the upload used. A renamed key here would not fail a type check — it
    would quietly publish every take at default levels.
    """

    def stored_row(self, **overrides):
        payload = {
            "music_track_id": "8821",
            "music_artist_user_id": 5501,
            "music_rights_ref": "pulsesoc_commercial_v2",
            "music_title": "Midnight Drive",
            "music_artist": "Ava Lang",
            "music_duration_seconds": 214.0,
            "music_start_offset_seconds": 32.0,
            "music_level": 0.45,
            "mic_level": 0.85,
            "music_preset": "balanced",
            "music_duck_enabled": 1,
            "music_duck_threshold_db": -34.0,
            "music_duck_depth_db": 7.0,
            "music_duck_attack_ms": 120.0,
            "music_duck_release_ms": 420.0,
        }
        payload.update(overrides)
        return {"music_track_id": payload["music_track_id"], "music_mix_json": json.dumps(payload)}

    def test_every_stored_field_survives_the_round_trip(self):
        rebuilt = mix.spec_from_row(self.stored_row(), "/var/catalog/8821.m4a")
        self.assertEqual(rebuilt.track_id, "8821")
        self.assertEqual(rebuilt.artist_user_id, 5501)
        self.assertEqual(rebuilt.rights_ref, "pulsesoc_commercial_v2")
        self.assertEqual(rebuilt.title, "Midnight Drive")
        self.assertEqual(rebuilt.artist, "Ava Lang")
        self.assertEqual(rebuilt.duration_seconds, 214.0)
        self.assertEqual(rebuilt.start_offset_seconds, 32.0)
        self.assertEqual(rebuilt.music_level, 0.45)
        self.assertEqual(rebuilt.mic_level, 0.85)
        self.assertEqual(rebuilt.preset, "balanced")
        self.assertTrue(rebuilt.duck_enabled)
        self.assertEqual(rebuilt.duck_depth_db, 7.0)
        self.assertEqual(rebuilt.duck_attack_ms, 120.0)
        self.assertEqual(rebuilt.duck_release_ms, 420.0)

    def test_the_integer_duck_flag_the_route_writes_reads_back_as_a_boolean(self):
        self.assertFalse(mix.spec_from_row(self.stored_row(music_duck_enabled=0), "/x.m4a").duck_enabled)
        self.assertTrue(mix.spec_from_row(self.stored_row(music_duck_enabled=1), "/x.m4a").duck_enabled)

    def test_a_custom_mix_keeps_its_preset_name_through_storage(self):
        self.assertEqual(mix.spec_from_row(self.stored_row(music_preset="custom"), "/x.m4a").preset, "custom")

    def test_the_worker_mixes_from_the_catalogue_file_it_is_handed(self):
        rebuilt = mix.spec_from_row(self.stored_row(), "/var/catalog/8821.m4a")
        self.assertEqual(rebuilt.music_source, "/var/catalog/8821.m4a")
        command = mix.build_music_mix_command("ffmpeg", "take.mp4", "out.mp4", rebuilt, has_mic_audio=True)
        self.assertIn("/var/catalog/8821.m4a", command)


if __name__ == "__main__":
    unittest.main()
