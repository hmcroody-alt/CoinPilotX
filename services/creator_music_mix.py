"""Server-side creator music mixing.

A recorded PulseSoc video gets its music here, not on the phone.

The alternative — play the track out of the speaker while the camera records, and
let the microphone pick it up — is the thing this module exists to make
unnecessary. That approach produces a take with the room in it: comb filtering
from the speaker-to-mic path, the creator's voice and the music fighting for the
same AGC, and a master that cannot be separated again for a takedown or a
re-mix. It also sounds obviously amateur on any device better than the phone
that recorded it.

So the phone records picture and voice, and uploads the fader positions and a
track id. This module fetches the original track from the catalog, re-checks
that the creator is still allowed to use it, and mixes the two clean digital
sources with ffmpeg during the transcode that every uploaded video already goes
through. The music never becomes sound until after the video is finished.

The gain maths deliberately mirrors ``mobile-native/src/audio/creatorMixer.ts``
function for function. That duplication is the point: the phone shows the
creator a preview built from those numbers, and a preview that does not predict
the master is worse than no preview at all.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Gain staging. Mirrors creatorMixer.ts.
# ---------------------------------------------------------------------------

MUSIC_HEADROOM_DB = -3.0
MIC_HEADROOM_DB = -1.0
MIX_CEILING_DB = -1.5

#: Integrated loudness target. -14 LUFS is where the major social platforms
#: normalise to, so mastering hotter than this buys nothing and costs dynamics.
LOUDNESS_TARGET_LUFS = -14.0
LOUDNESS_RANGE = 11.0

#: Low-shelf trim applied to the music, per preset, in dB.
#:
#: Phone speakers cannot reproduce the bottom two octaves, so the energy down
#: there is not heard — it is only spent, on the limiter and on the encoder.
#: Trimming it is what keeps a dense track from swallowing the voice's
#: fundamental when the same file is played back through a laptop or a car.
BASS_TRIM_DB_BY_PRESET = {
    "voice_focus": -4.0,
    "balanced": -2.5,
    "music_focus": -1.5,
    "custom": -2.5,
}
BASS_SHELF_HZ = 110.0

#: Where the mic high-pass sits. Below this a phone microphone captures handling
#: noise, wind and desk rumble, and nothing a human voice needs.
MIC_HIGHPASS_HZ = 85.0

#: Subsonic filter on the music. Catalogue masters occasionally carry DC offset
#: or sub-20 Hz energy that eats limiter headroom inaudibly.
MUSIC_HIGHPASS_HZ = 30.0

MIX_SAMPLE_RATE = 48000


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def clamp_mix_level(value: Any) -> float:
    try:
        level = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(level):
        return 0.0
    return clamp(level, 0.0, 1.0)


def fader_to_linear_gain(level: Any) -> float:
    """Square-law fader taper. Identical to creatorMixLevelToLinearGain."""
    clamped = clamp_mix_level(level)
    return clamped * clamped


def linear_gain_to_db(gain: float) -> float:
    if not math.isfinite(gain) or gain <= 0:
        return -120.0
    return 20.0 * math.log10(gain)


def db_to_linear_gain(db: float) -> float:
    if not math.isfinite(db) or db <= -120:
        return 0.0
    return 10.0 ** (db / 20.0)


def music_bus_gain_db(music_level: Any) -> float:
    return linear_gain_to_db(fader_to_linear_gain(music_level)) + MUSIC_HEADROOM_DB


def mic_bus_gain_db(mic_level: Any) -> float:
    return linear_gain_to_db(fader_to_linear_gain(mic_level)) + MIC_HEADROOM_DB


#: Mirrors CREATOR_MUSIC_MIN_TAIL_SECONDS in creatorMusicSelection.ts.
MIN_TAIL_SECONDS = 5.0


def clamp_start_offset(offset_seconds: Any, duration_seconds: Any) -> float:
    """Keep the music cue inside the track, against the *catalogue's* duration.

    The client clamps against the duration it was shown, which is the duration
    the catalogue reported when the picker last loaded. This re-clamp exists
    because that number can be stale — a re-encoded master is a different length
    — and a start point past the end produces a take that is silent from the
    first frame, which reads as "the music feature is broken".
    """
    offset = max(0.0, _as_float(offset_seconds, 0.0))
    duration = max(0.0, _as_float(duration_seconds, 0.0))
    if duration <= 0:
        return round(offset, 2)
    return round(min(offset, max(0.0, duration - MIN_TAIL_SECONDS)), 2)


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreatorMusicMixSpec:
    """Everything needed to mix one take, after the server has had its say."""

    track_id: str
    music_source: str
    start_offset_seconds: float = 0.0
    duration_seconds: float = 0.0
    music_level: float = 0.45
    mic_level: float = 0.85
    preset: str = "balanced"
    duck_enabled: bool = True
    duck_threshold_db: float = -34.0
    duck_depth_db: float = 7.0
    duck_attack_ms: float = 120.0
    duck_release_ms: float = 420.0
    artist_user_id: int = 0
    rights_ref: str = ""
    title: str = ""
    artist: str = ""

    @property
    def music_gain_db(self) -> float:
        return music_bus_gain_db(self.music_level)

    @property
    def mic_gain_db(self) -> float:
        return mic_bus_gain_db(self.mic_level)

    @property
    def bass_trim_db(self) -> float:
        return BASS_TRIM_DB_BY_PRESET.get(self.preset, BASS_TRIM_DB_BY_PRESET["custom"])

    def attribution(self) -> dict[str, Any]:
        """What gets persisted next to the finished video."""
        return {
            "track_id": self.track_id,
            "artist_user_id": self.artist_user_id,
            "rights_ref": self.rights_ref,
            "title": self.title,
            "artist": self.artist,
            "start_offset_seconds": round(self.start_offset_seconds, 2),
            "duration_seconds": round(self.duration_seconds, 2),
            "preset": self.preset,
            "music_level": round(self.music_level, 4),
            "mic_level": round(self.mic_level, 4),
            "music_gain_db": round(self.music_gain_db, 2),
            "mic_gain_db": round(self.mic_gain_db, 2),
            "duck": {
                "enabled": self.duck_enabled,
                "threshold_db": self.duck_threshold_db,
                "depth_db": self.duck_depth_db,
                "attack_ms": self.duck_attack_ms,
                "release_ms": self.duck_release_ms,
            },
        }


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def parse_creator_music_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Read the flat ``music_*`` upload fields into normalised values.

    Returns an empty mapping when no track was chosen, which is the signal that
    this upload takes the ordinary no-music path and must be left byte-identical
    to what it was before this feature existed.

    Nothing in here is trusted. The levels are clamped because a fader position
    of 40 would blow the mix apart, and the ducking parameters are clamped to the
    same musical range the client enforces so a hand-written request cannot ask
    for a 90 dB duck. The track id is *not* resolved here — that is a database
    question, answered by the caller.
    """

    track_id = str(fields.get("music_track_id") or "").strip()
    if not track_id:
        return {}

    preset = str(fields.get("music_preset") or "balanced").strip().lower()
    if preset not in BASS_TRIM_DB_BY_PRESET:
        preset = "custom"

    return {
        "track_id": track_id,
        "artist_user_id": int(_as_float(fields.get("music_artist_user_id"), 0.0)),
        "rights_ref": str(fields.get("music_rights_ref") or "").strip(),
        "title": str(fields.get("music_title") or "").strip(),
        "artist": str(fields.get("music_artist") or "").strip(),
        "start_offset_seconds": max(0.0, _as_float(fields.get("music_start_offset_seconds"), 0.0)),
        "duration_seconds": max(0.0, _as_float(fields.get("music_duration_seconds"), 0.0)),
        "music_level": clamp_mix_level(fields.get("music_level")),
        "mic_level": clamp_mix_level(fields.get("mic_level")),
        "preset": preset,
        "duck_enabled": _as_bool(fields.get("music_duck_enabled"), True),
        "duck_threshold_db": clamp(_as_float(fields.get("music_duck_threshold_db"), -34.0), -70.0, -6.0),
        "duck_depth_db": clamp(_as_float(fields.get("music_duck_depth_db"), 7.0), 0.0, 18.0),
        "duck_attack_ms": clamp(_as_float(fields.get("music_duck_attack_ms"), 120.0), 20.0, 1200.0),
        "duck_release_ms": clamp(_as_float(fields.get("music_duck_release_ms"), 420.0), 80.0, 4000.0),
    }


def build_mix_spec(parsed: Mapping[str, Any], music_source: str) -> CreatorMusicMixSpec:
    """Combine the client's request with the server-resolved track location."""
    payload = dict(parsed)
    payload.pop("music_source", None)
    return CreatorMusicMixSpec(music_source=music_source, **payload)


def spec_from_row(row: Mapping[str, Any], music_source: str) -> CreatorMusicMixSpec | None:
    """Rebuild a spec from the persisted upload row, for the worker."""
    track_id = str(row.get("music_track_id") or "").strip()
    if not track_id or not music_source:
        return None
    try:
        mix = json.loads(row.get("music_mix_json") or "{}")
    except (TypeError, ValueError):
        mix = {}
    if not isinstance(mix, dict):
        mix = {}
    mix["music_track_id"] = track_id
    parsed = parse_creator_music_fields(mix)
    if not parsed:
        return None
    return build_mix_spec(parsed, music_source)


# ---------------------------------------------------------------------------
# Ducking
# ---------------------------------------------------------------------------

#: How far above the threshold a speaking voice is assumed to sit.
#:
#: ``sidechaincompress`` is specified by ratio, not by depth, and the reduction
#: it produces is ``excess * (1 - 1/ratio)`` where ``excess`` is how far the
#: sidechain runs over the threshold. Creators think in "how far does the music
#: step back", so the ratio is derived from the requested depth against this
#: assumed excess. 12 dB is a realistic figure for a phone mic held at
#: conversational distance with the threshold where the client puts it.
ASSUMED_SPEECH_EXCESS_DB = 12.0


def duck_ratio_for_depth(depth_db: float) -> float:
    """Convert a requested duck depth in dB into a compressor ratio."""
    depth = clamp(_as_float(depth_db, 0.0), 0.0, 18.0)
    if depth <= 0:
        return 1.0
    # Guard the asymptote: as depth approaches the assumed excess the ratio runs
    # to infinity, which in practice means "gate", not "duck".
    fraction = clamp(depth / ASSUMED_SPEECH_EXCESS_DB, 0.0, 0.95)
    return round(clamp(1.0 / (1.0 - fraction), 1.0, 20.0), 3)


# ---------------------------------------------------------------------------
# Filtergraph
# ---------------------------------------------------------------------------


def _fmt(value: float, places: int = 3) -> str:
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text or "0"


def build_music_mix_filtergraph(spec: CreatorMusicMixSpec, has_mic_audio: bool) -> str:
    """The whole signal chain, as one ffmpeg ``-filter_complex`` string.

    Order is not arbitrary. Each source is conditioned in its own lane, then
    trimmed to its bus level, and only then summed — conditioning after the sum
    would mean the mic's high-pass also eats the music's bass, and levelling
    after the sum would mean the limiter, not the creator, decides the balance.

    The mic is split so the same post-gain signal both goes into the mix and
    drives the ducking sidechain. Using the post-gain signal matters: a creator
    who pulls their mic fader down is asking for less voice, and the music should
    step back less, not the same amount.
    """

    music_lane = (
        f"[1:a]aformat=sample_fmts=fltp:sample_rates={MIX_SAMPLE_RATE}:channel_layouts=stereo,"
        f"highpass=f={_fmt(MUSIC_HIGHPASS_HZ)},"
        # Low shelf rather than a peaking cut: the goal is to tilt the bottom
        # end down, not to carve a notch a mastering engineer would hear.
        f"bass=g={_fmt(spec.bass_trim_db)}:f={_fmt(BASS_SHELF_HZ)}:w=0.6,"
        f"volume={_fmt(spec.music_gain_db, 2)}dB[music]"
    )

    if not has_mic_audio:
        # Silent take, or the creator muted the mic. There is nothing to duck
        # against and nothing to sum, so the music becomes the whole bed. Still
        # runs through the master chain so the ceiling and loudness target hold.
        return ";".join([music_lane, f"[music]{_master_chain()}[aout]"])

    mic_lane = (
        f"[0:a]aformat=sample_fmts=fltp:sample_rates={MIX_SAMPLE_RATE}:channel_layouts=stereo,"
        f"highpass=f={_fmt(MIC_HIGHPASS_HZ)},"
        # Gentle broadband denoise. Held deliberately shallow: aggressive
        # spectral subtraction on speech produces the watery artefacts that make
        # a take sound processed, which is worse than a quiet room tone.
        "afftdn=nf=-25,"
        # Voice dynamics. A moderate ratio with a slow-ish release evens out the
        # distance changes a hand-held phone always has, without audibly pumping.
        "acompressor=threshold=-18dB:ratio=3:attack=8:release=180:makeup=2,"
        f"volume={_fmt(spec.mic_gain_db, 2)}dB[mic]"
    )

    lanes = [music_lane, mic_lane, "[mic]asplit=2[micmix][micsc]"]

    if spec.duck_enabled and spec.duck_depth_db > 0:
        threshold = clamp(db_to_linear_gain(spec.duck_threshold_db), 0.000976563, 1.0)
        lanes.append(
            f"[music][micsc]sidechaincompress="
            f"threshold={_fmt(threshold, 6)}:"
            f"ratio={_fmt(duck_ratio_for_depth(spec.duck_depth_db))}:"
            f"attack={_fmt(spec.duck_attack_ms, 1)}:"
            f"release={_fmt(spec.duck_release_ms, 1)}:"
            "makeup=1:level_sc=1[musicbed]"
        )
        music_out = "[musicbed]"
    else:
        # The sidechain tap is still produced by asplit above; discarding it with
        # anullsink keeps the graph valid without a dangling output.
        lanes.append("[micsc]anullsink")
        music_out = "[music]"

    lanes.append(
        # normalize=0 is essential. amix's default halves every input to
        # guarantee no clipping, which would silently undo the creator's fader
        # positions and hand level control to the number of inputs.
        f"{music_out}[micmix]amix=inputs=2:duration=shortest:dropout_transition=0:normalize=0[premaster]"
    )
    lanes.append(f"[premaster]{_master_chain()}[aout]")
    return ";".join(lanes)


def _master_chain() -> str:
    """Loudness then ceiling, in that order.

    ``loudnorm`` moves the whole programme to a target and will happily overshoot
    a sample peak doing it, so the limiter goes last and is the thing that
    actually guarantees the ceiling. The ceiling itself sits below 0 dBFS
    because a lossy encoder reconstructs peaks slightly higher than the samples
    it was given, and a file that measures exactly 0 dBFS will clip on playback.
    """
    ceiling = db_to_linear_gain(MIX_CEILING_DB)
    return (
        f"loudnorm=I={_fmt(LOUDNESS_TARGET_LUFS, 1)}:"
        f"TP={_fmt(MIX_CEILING_DB, 1)}:"
        f"LRA={_fmt(LOUDNESS_RANGE, 1)},"
        f"alimiter=limit={_fmt(ceiling, 6)}:level=disabled"
    )


def build_music_mix_command(
    ffmpeg_path: str,
    source_video: str,
    output_path: str,
    spec: CreatorMusicMixSpec,
    has_mic_audio: bool,
    video_preset: str = "veryfast",
    video_crf: str = "23",
) -> list[str]:
    """The full transcode-with-music command.

    This replaces the ordinary transcode rather than running after it, so a video
    with music is encoded exactly once. Re-encoding a finished master to add
    audio would cost a generation of video quality for no reason.
    """

    command = [ffmpeg_path, "-y", "-i", source_video]

    # Input seeking for the start point: ffmpeg skips to the offset before
    # decoding, which is both faster and frame-accurate enough for a music cue.
    if spec.start_offset_seconds > 0:
        command += ["-ss", _fmt(spec.start_offset_seconds, 3)]
    # A track shorter than the take would otherwise leave the tail silent.
    # Looping is the lesser evil, and matches what Live does with cycle=-1.
    command += ["-stream_loop", "-1", "-i", spec.music_source]

    command += [
        "-filter_complex",
        build_music_mix_filtergraph(spec, has_mic_audio),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        video_preset,
        "-crf",
        video_crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        str(MIX_SAMPLE_RATE),
        "-movflags",
        "+faststart",
        # The looped music input never ends on its own. This is what stops the
        # output running forever when the take has no microphone audio.
        "-shortest",
        output_path,
    ]
    return command


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


def source_has_audio_stream(path: str, ffprobe_path: str | None = None) -> bool:
    """Does this recording actually carry a microphone track?

    Answered by probing rather than by trusting the upload's ``has_audio``
    column, because the mic can be muted at the camera after the column was
    written, and mixing against a stream that is not there fails the whole
    transcode.
    """
    probe = ffprobe_path or shutil.which("ffprobe")
    if not probe:
        # Without a probe, assume audio is present. A take that has it and is
        # treated as silent loses the creator's voice; the reverse merely fails
        # loudly and falls back to the plain transcode.
        return True
    try:
        result = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return True
    return bool((result.stdout or "").strip())


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


ELIGIBLE_MODERATION_STATUSES = {"approved", ""}


@dataclass(frozen=True)
class MusicEligibility:
    allowed: bool
    reason: str = ""
    audio_url: str = ""
    artist_user_id: int = 0
    rights_ref: str = ""
    title: str = ""
    artist: str = ""
    duration_seconds: float = 0.0


def evaluate_track_eligibility(track: Mapping[str, Any] | None) -> MusicEligibility:
    """Server-authoritative rights check, run again at mix time.

    Deliberately re-run rather than trusting the check the picker already did.
    A draft can sit on a phone for a week, and in that week a track can be
    reported, have its licence withdrawn, or be pulled by its uploader. The mix
    is the last moment the platform can decline to bake it in.
    """
    if not track:
        return MusicEligibility(False, "track_not_found")

    if not _as_bool(track.get("active"), False):
        return MusicEligibility(False, "track_inactive")

    status = str(track.get("safety_status") or track.get("moderation_status") or "").strip().lower()
    if status not in ELIGIBLE_MODERATION_STATUSES:
        return MusicEligibility(False, f"moderation_{status or 'unknown'}")

    if not _as_bool(track.get("approved_by_admin"), False):
        return MusicEligibility(False, "not_approved")

    if not _as_bool(track.get("commercial_use_allowed"), False):
        return MusicEligibility(False, "commercial_use_not_allowed")

    # Mixing a track under a creator's own voice is a derivative work, which is
    # exactly what the remix permission governs.
    if not _as_bool(track.get("remix_edit_allowed"), False):
        return MusicEligibility(False, "remix_not_allowed")

    audio_url = str(track.get("audio_url") or "").strip()
    if not audio_url:
        return MusicEligibility(False, "no_audio_asset")

    return MusicEligibility(
        True,
        "",
        audio_url=audio_url,
        artist_user_id=int(_as_float(track.get("uploader_user_id") or track.get("artist_user_id"), 0.0)),
        rights_ref=str(track.get("license_type") or "").strip(),
        title=str(track.get("title") or "").strip(),
        artist=str(track.get("artist") or "").strip(),
        duration_seconds=max(0.0, _as_float(track.get("duration_seconds"), 0.0)),
    )
