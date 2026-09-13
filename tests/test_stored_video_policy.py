"""The 90-minute stored-video rule, at its edges and across its two languages.

The policy is only as good as the surface names that reach it and the byte
ceiling that has to accommodate it, so those are tested here alongside the
duration arithmetic. A green duration table over an unreachable size limit is a
rule that forbids the feature while appearing to allow it.
"""

from __future__ import annotations

import inspect
import os
import re
import unittest
from pathlib import Path

from services import (
    media_service,
    media_upload_sessions,
    messenger_media_foundation,
    stored_video_policy,
    upload_progress_service,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TS_POLICY = REPO_ROOT / "mobile-native" / "src" / "media" / "storedVideoPolicy.ts"


class TheBoundaryIsWhereTheRuleIsDecided(unittest.TestCase):
    """90:00 is inside the rule and 90:01 is outside it."""

    def test_one_second_under_is_accepted(self):
        self.assertFalse(stored_video_policy.exceeds_limit("post", 89 * 60 + 59))

    def test_exactly_ninety_minutes_is_accepted(self):
        self.assertFalse(stored_video_policy.exceeds_limit("post", 5400))

    def test_one_second_over_is_refused(self):
        self.assertTrue(stored_video_policy.exceeds_limit("post", 5401))

    def test_a_fractional_overshoot_inside_the_same_second_is_accepted(self):
        # Container metadata routinely reports 5400.37. Refusing that would make
        # the advertised limit unreachable in practice: a video the user trimmed
        # to exactly 90:00 would be rejected by rounding noise.
        self.assertFalse(stored_video_policy.exceeds_limit("post", 5400.37))

    def test_an_absent_measurement_is_not_a_violation(self):
        # Duration for a stored video is often only known after the bytes land.
        # Treating "unknown" as "too long" would refuse every upload.
        for absent in (None, 0, -1, "", "not-a-number"):
            self.assertFalse(stored_video_policy.exceeds_limit("post", absent), absent)


class TheSurfaceNamesTheProductActuallySendsResolve(unittest.TestCase):
    """An unregistered name silently shortens a surface to the strictest cap.

    This is the failure mode that is invisible in review: the fallback is
    deliberately the *strictest* value, so a missing alias looks exactly like a
    deliberate product decision to cap that surface at a minute.
    """

    def test_feed_and_reel_context_types_get_the_full_ninety_minutes(self):
        for context_type in ["pulse", "pulse_post", "pulse_reel", "reels", "pulse_camera", "pulse_group", "creator_studio"]:
            self.assertEqual(
                stored_video_policy.max_duration_seconds(context_type),
                stored_video_policy.MAX_STORED_VIDEO_SECONDS,
                context_type,
            )

    def test_messenger_context_types_get_the_full_ninety_minutes(self):
        for context_type in ["pulse_message", "private_chat", "chat", "pulse_comm_v2_direct", "pulse_comm_v2_group"]:
            self.assertEqual(
                stored_video_policy.max_duration_seconds(context_type),
                stored_video_policy.MAX_STORED_VIDEO_SECONDS,
                context_type,
            )

    def test_short_form_and_commerce_surfaces_keep_their_own_caps(self):
        self.assertEqual(stored_video_policy.max_duration_seconds("pulse_status"), stored_video_policy.SHORT_FORM_SECONDS)
        self.assertEqual(stored_video_policy.max_duration_seconds("marketplace_product"), stored_video_policy.MARKETPLACE_SECONDS)
        self.assertEqual(stored_video_policy.max_duration_seconds("pulse_ad_creative"), stored_video_policy.MARKETPLACE_SECONDS)

    def test_an_unknown_surface_gets_the_strictest_cap_not_the_most_permissive(self):
        self.assertEqual(stored_video_policy.max_duration_seconds("surface_nobody_registered"), stored_video_policy.strictest_seconds())
        self.assertFalse(stored_video_policy.is_known_surface("surface_nobody_registered"))

    def test_a_comm_v2_conversation_kind_nobody_enumerated_still_resolves(self):
        # New comm_v2 context types are minted with a discriminator suffix. If they
        # had to be listed one by one, adding a conversation kind would quietly
        # shorten Messenger video to a minute.
        self.assertTrue(stored_video_policy.is_known_surface("pulse_comm_v2_something_new"))


class AnEnvOverrideMayOnlyTighten(unittest.TestCase):
    def setUp(self):
        self._previous = os.environ.get("POST_VIDEO_MAX_SECONDS")

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("POST_VIDEO_MAX_SECONDS", None)
        else:
            os.environ["POST_VIDEO_MAX_SECONDS"] = self._previous

    def test_a_tighter_override_applies(self):
        os.environ["POST_VIDEO_MAX_SECONDS"] = "600"
        self.assertEqual(stored_video_policy.max_duration_seconds("pulse"), 600)

    def test_an_override_above_the_platform_maximum_is_ignored(self):
        os.environ["POST_VIDEO_MAX_SECONDS"] = "99999"
        self.assertEqual(stored_video_policy.max_duration_seconds("pulse"), stored_video_policy.MAX_STORED_VIDEO_SECONDS)

    def test_a_garbage_override_does_not_open_the_gate(self):
        os.environ["POST_VIDEO_MAX_SECONDS"] = "ninety minutes please"
        self.assertEqual(stored_video_policy.max_duration_seconds("pulse"), stored_video_policy.MAX_STORED_VIDEO_SECONDS)


class TheByteCeilingHasToMakeNinetyMinutesPossible(unittest.TestCase):
    """A duration limit behind an impossible size limit is not a limit, it is a ban.

    The synchronous ceiling is 150 MB in production (700 MB by default), which
    over 5400 seconds is 233 kbps and 1.04 Mbps respectively. Neither is a
    watchable 720p stream, so the direct-to-storage path derives its own ceiling
    from the duration policy instead of carrying a second constant that can drift.
    """

    def test_a_ninety_minute_surface_can_carry_a_watchable_bitrate(self):
        limit = media_service.direct_video_limit_bytes("pulse")
        bits_per_second = limit * 8 / stored_video_policy.MAX_STORED_VIDEO_SECONDS
        self.assertGreaterEqual(bits_per_second, 2_500_000, f"{limit} bytes is only {bits_per_second / 1e6:.2f} Mbps over 90 minutes")

    def test_the_direct_ceiling_is_not_the_synchronous_ceiling(self):
        # Sharing one number is how the 700 MB cap ended up governing a 90-minute
        # surface. The synchronous path streams through the web process, so its
        # much smaller limit is correct for itself and wrong for direct upload.
        self.assertGreater(
            media_service._limit_bytes("mp4", "pulse", direct_to_storage=True),
            media_service._limit_bytes("mp4", "pulse"),
        )

    def test_a_short_surface_keeps_a_usable_allowance(self):
        # 60 seconds at the derived bitrate is 24 MB, which would be a regression
        # against the 350 MB Status already allows, so the derivation has a floor.
        self.assertGreaterEqual(media_service.direct_video_limit_bytes("pulse_status"), 350 * 1024 * 1024)

    def test_the_derived_ceiling_tracks_the_duration_table(self):
        self.assertGreater(
            media_service.direct_video_limit_bytes("pulse"),
            media_service.direct_video_limit_bytes("marketplace_product"),
        )

    def test_documents_and_images_are_unaffected_by_the_direct_flag(self):
        # The flag is about video's byte budget. A PDF must not inherit a
        # gigabyte ceiling because it happened to travel the direct path.
        for ext in ["pdf", "jpg", "gif", "mp3"]:
            self.assertEqual(
                media_service._limit_bytes(ext, "pulse", direct_to_storage=True),
                media_service._limit_bytes(ext, "pulse"),
                ext,
            )


class TheSessionRefusesAnOverLongVideoBeforeAnyBytesMove(unittest.TestCase):
    """Rejecting after the upload costs the uploader the whole upload."""

    def _validate(self, duration_ms, context_type="pulse", size=50 * 1024 * 1024):
        return media_upload_sessions._validate("clip.mp4", "video/mp4", size, context_type, duration_ms)

    def test_exactly_ninety_minutes_is_accepted(self):
        active, error = self._validate(5_400_000)
        self.assertEqual(error, "")
        self.assertEqual(active["media_type"], "video")

    def test_one_second_over_ninety_minutes_is_refused(self):
        _, error = self._validate(5_401_000)
        self.assertEqual(error, "video_too_long")

    def test_the_wire_unit_is_milliseconds(self):
        # A seconds-vs-milliseconds mix-up is the bug this field invites, and it
        # fails in both directions, so both directions are pinned. Read as
        # seconds, 5_400_000 would be 62 days and get refused; read as
        # milliseconds it is exactly 90:00 and must pass.
        _, error = self._validate(5_400_000)
        self.assertEqual(error, "", "5_400_000 ms is exactly 90:00 and must be accepted")
        # And 5400 must not be mistaken for the limit itself: as milliseconds it
        # is a 5.4-second clip, which is fine for every surface including Status.
        _, error = self._validate(5400, context_type="pulse_status")
        self.assertEqual(error, "", "5400 ms is 5.4 seconds and must be accepted")
        # A millisecond past the ceiling is still inside the same whole second.
        # The refusal lands a second later, which is the documented boundary.
        _, error = self._validate(5_400_001)
        self.assertEqual(error, "")
        _, error = self._validate(5_401_000)
        self.assertEqual(error, "video_too_long")

    def test_a_status_clip_over_a_minute_is_refused_on_its_own_cap(self):
        _, error = self._validate(61_000, context_type="pulse_status")
        self.assertEqual(error, "video_too_long")

    def test_an_absent_duration_does_not_block_the_session(self):
        for absent in (None, 0, "", "unknown"):
            _, error = self._validate(absent)
            self.assertEqual(error, "", repr(absent))

    def test_an_image_is_never_judged_on_duration(self):
        active, error = media_upload_sessions._validate("photo.jpg", "image/jpeg", 1024 * 1024, "pulse", 9_999_999)
        self.assertEqual(error, "")
        self.assertEqual(active["media_type"], "image")

    def test_a_ninety_minute_video_is_not_refused_for_its_size(self):
        # The two limits have to agree. A 2 GB 90-minute upload that passes the
        # duration check and then fails the size check is the same dead end.
        _, error = self._validate(5_400_000, size=1500 * 1024 * 1024)
        self.assertEqual(error, "")

    def test_the_refusal_names_the_actual_limit_for_the_surface(self):
        # "That media type is not supported" was the old answer to every refusal,
        # and it is why this mission exists. The message has to come from the
        # policy so a Status refusal says one minute and a feed refusal says
        # ninety -- a generic string sends the uploader looking for a file problem.
        self.assertEqual(stored_video_policy.limit_message("pulse_status"), "Videos can be up to 1 minute long.")
        self.assertEqual(stored_video_policy.limit_message("pulse"), "Videos can be up to 90 minutes long.")
        source = inspect.getsource(media_upload_sessions.create_session)
        self.assertIn('"video_too_long": stored_video_policy.limit_message(context_type)', source)

    def test_the_refusal_is_a_payload_too_large_not_a_bad_request(self):
        # 413 is what the client's retry logic reads as "this will never work";
        # 400 invites a retry of the same file.
        source = inspect.getsource(media_upload_sessions.create_session)
        self.assertIn('413 if error in {"file_too_large", "video_too_long"} else 400', source)


class TheSynchronousPathEnforcesTheSameRule(unittest.TestCase):
    """The browser composer posts through Flask, and it is subject to the rule too."""

    class _Stream:
        def __init__(self, size):
            self._size = size
            self.position = 0

        def seek(self, offset, whence=0):
            self.position = self._size if whence == os.SEEK_END else offset

        def tell(self):
            return self.position

    class _Upload:
        def __init__(self, filename, mimetype, size):
            self.filename = filename
            self.mimetype = mimetype
            self.stream = TheSynchronousPathEnforcesTheSameRule._Stream(size)

    def test_an_over_long_video_is_refused_with_the_policy_message(self):
        result = upload_progress_service.validate_media_file(
            self._Upload("clip.mp4", "video/mp4", 10 * 1024 * 1024),
            context_type="pulse",
            duration_ms=5_401_000,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 413)
        self.assertIn("90 minutes", result["message"])

    def test_ninety_minutes_exactly_passes_the_duration_check(self):
        result = upload_progress_service.validate_media_file(
            self._Upload("clip.mp4", "video/mp4", 10 * 1024 * 1024),
            context_type="pulse",
            duration_ms=5_400_000,
        )
        self.assertTrue(result["ok"], result.get("message"))

    def test_an_older_client_that_sends_no_duration_still_uploads(self):
        result = upload_progress_service.validate_media_file(
            self._Upload("clip.mp4", "video/mp4", 10 * 1024 * 1024),
            context_type="pulse",
        )
        self.assertTrue(result["ok"], result.get("message"))


class TheTwoLanguagesAgree(unittest.TestCase):
    """The app mirrors this table so the picker can cap a recording.

    Two hand-maintained copies drift, and the drift is silent: the app would cap a
    surface the server allows, or advertise a limit the server refuses. So the
    numbers are compared, not trusted.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = TS_POLICY.read_text(encoding="utf-8")

    def _ts_record(self, name):
        match = re.search(rf"const {name}: Record<string, (?:number|string)> = \{{(.*?)\n\}};", self.source, re.S)
        self.assertIsNotNone(match, f"{name} not found in {TS_POLICY.name}")
        body = match.group(1)
        pairs = re.findall(r"(\w+)\s*:\s*([A-Za-z_0-9\"]+)", body)
        return {key: value.strip('"') for key, value in pairs}

    def test_the_platform_constants_match(self):
        for name, expected in [
            ("MAX_STORED_VIDEO_SECONDS", stored_video_policy.MAX_STORED_VIDEO_SECONDS),
            ("SHORT_FORM_SECONDS", stored_video_policy.SHORT_FORM_SECONDS),
            ("MARKETPLACE_SECONDS", stored_video_policy.MARKETPLACE_SECONDS),
        ]:
            match = re.search(rf"export const {name} = ([^;]+);", self.source)
            self.assertIsNotNone(match, name)
            self.assertEqual(eval(match.group(1).replace("_", "")), expected, name)  # noqa: S307 - literal arithmetic from our own file

    def test_every_surface_is_registered_in_both(self):
        ts_surfaces = self._ts_record("SURFACE_SECONDS")
        self.assertEqual(set(ts_surfaces), set(stored_video_policy._SURFACE_SECONDS))

    def test_every_alias_points_the_same_way_in_both(self):
        ts_aliases = self._ts_record("ALIASES")
        self.assertEqual(ts_aliases, stored_video_policy._ALIASES)

    def test_the_comm_v2_prefix_rule_exists_in_both(self):
        self.assertIn("pulse_comm_v2", self.source)
        self.assertIn('startsWith("pulse_comm_v2")', self.source)

    def test_the_derived_byte_ceiling_matches(self):
        bitrate = re.search(r"VIDEO_BUDGET_BITS_PER_SECOND = ([0-9_]+);", self.source)
        floor = re.search(r"VIDEO_FLOOR_BYTES = ([^;]+);", self.source)
        self.assertIsNotNone(bitrate)
        self.assertIsNotNone(floor)
        self.assertEqual(int(bitrate.group(1).replace("_", "")), media_service.DIRECT_VIDEO_BUDGET_BITS_PER_SECOND)
        self.assertEqual(eval(floor.group(1)), media_service.DIRECT_VIDEO_FLOOR_BYTES)  # noqa: S307 - literal arithmetic from our own file

    def test_the_typescript_mirror_points_at_this_file(self):
        # The comment in the mirror is a promise that this test exists. A stale
        # path there is how the mirror ends up unverified.
        self.assertIn(Path(__file__).name, self.source)


class TheMillisecondConversionLivesInExactlyOnePlace(unittest.TestCase):
    """Three call sites once each divided by 1000 themselves.

    The unit bug fails open -- 5400 read as milliseconds is 5.4 seconds and passes
    every check -- so a per-call-site conversion is a defect waiting for one copy to
    be written slightly differently.
    """

    def test_milliseconds_are_read_as_milliseconds(self):
        # Read as seconds this would be 62 days, and the clip would be refused.
        self.assertFalse(stored_video_policy.exceeds_limit_ms("post", 5_400_000))
        self.assertTrue(stored_video_policy.exceeds_limit_ms("post", 5_401_000))

    def test_a_seconds_value_arriving_in_the_ms_field_is_not_mistaken_for_a_violation(self):
        # 5400 in the milliseconds field is 5.4 seconds. It must pass, because the
        # alternative -- guessing the caller meant seconds -- would make the unit
        # ambiguous forever.
        self.assertFalse(stored_video_policy.exceeds_limit_ms("pulse_status", 5400))

    def test_garbage_and_absence_convert_to_an_unmeasured_zero(self):
        for absent in (None, "", 0, -5, "not-a-number", [], {}):
            self.assertEqual(stored_video_policy.declared_seconds(absent), 0.0, absent)
            self.assertFalse(stored_video_policy.exceeds_limit_ms("pulse_status", absent), absent)

    def test_no_upload_path_owns_the_unit_itself(self):
        # Each path consults the policy through the millisecond door. Calling the
        # seconds-based `exceeds_limit` from an upload path means that call site has
        # taken ownership of the conversion again.
        for module in (media_upload_sessions, upload_progress_service, messenger_media_foundation):
            source = inspect.getsource(module)
            self.assertIn("exceeds_limit_ms", source, module.__name__)
            self.assertNotIn("stored_video_policy.exceeds_limit(", source, module.__name__)


class TheWebDirectMuxRouteIsNotAHoleInThePolicy(unittest.TestCase):
    """The path most web video actually takes bypassed every validator.

    `/api/pulse/media/mux/direct-upload` hands back a Mux upload URL, so the bytes
    never reach a Flask validator again -- the 25 GB ceiling there was the only
    limit, and a three-hour video passed it. Asserted against source because the
    route needs the whole app booted, and the risk being guarded is "somebody moves
    the check below the point where the upload URL is minted".
    """

    @classmethod
    def setUpClass(cls):
        source = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")
        start = source.index("def api_pulse_media_mux_direct_upload(")
        cls.route = source[start:source.index("\n@webhook_app.route", start)]

    def test_the_route_refuses_an_over_long_video(self):
        self.assertIn("stored_video_policy.exceeds_limit_ms(context_type, payload.get(\"duration_ms\"))", self.route)
        self.assertIn("stored_video_policy.limit_message(context_type)", self.route)
        self.assertIn('error="video_too_long"', self.route)

    def test_the_refusal_happens_before_the_upload_url_is_minted(self):
        # Checking after `create_mux_direct_upload` would leave an orphaned media row
        # and a live Mux upload URL behind every refusal.
        self.assertLess(
            self.route.index("exceeds_limit_ms"),
            self.route.index("create_mux_direct_upload"),
        )

    def test_the_route_default_context_type_is_a_registered_surface(self):
        # The route defaults to "pulse_video". Unregistered, that name would resolve
        # to the strictest cap and refuse every web video over a minute.
        self.assertIn('payload.get("context_type") or "pulse_video"', self.route)
        self.assertTrue(stored_video_policy.is_known_surface("pulse_video"))
        self.assertEqual(
            stored_video_policy.max_duration_seconds("pulse_video"),
            stored_video_policy.MAX_STORED_VIDEO_SECONDS,
        )


class TheWebComposersDeclareDurationThroughOneFunnel(unittest.TestCase):
    """Every web upload surface goes through PulseUploadManager.upload().

    Declaring the duration in each composer instead would mean a new composer is
    unenforced by default, and the omission is invisible: the server simply has no
    duration to judge.
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (REPO_ROOT / "static" / "js" / "pulse_upload_manager.js").read_text(encoding="utf-8")

    def test_the_probe_reports_milliseconds(self):
        self.assertIn("Math.round(el.duration * 1000)", self.source)

    def test_both_transports_carry_the_declaration(self):
        # The synchronous POST reads a form field; the Mux direct-upload start reads
        # JSON. Both are fed from the same probe.
        self.assertIn('form.append("duration_ms", String(durationMs))', self.source)
        self.assertIn('duration_ms: Number(fieldFromForm(form, "duration_ms", 0)) || 0', self.source)

    def test_a_probe_that_never_settles_cannot_hang_publish(self):
        # A container the browser cannot demux fires neither loadedmetadata nor
        # error. Without the timeout the publish button would stay disabled forever.
        self.assertIn("window.setTimeout(() => done(0), 4000)", self.source)

    def test_only_video_is_probed(self):
        self.assertIn("if (!isVideoFile(file, type)) return Promise.resolve(0);", self.source)

    def test_the_object_url_is_revoked(self):
        # A composer where somebody picks six videos in a row would otherwise leak
        # the whole selection for the lifetime of the page.
        self.assertIn("URL.revokeObjectURL(url)", self.source)


if __name__ == "__main__":
    unittest.main()
