import { PulseMusicTrack } from "../../api/music";
import { applyCreatorMixerPreset, withMusicLevel } from "../creatorMixer";
import {
  CREATOR_MUSIC_MIN_TAIL_SECONDS,
  clampCreatorMusicStartOffset,
  createCreatorMusicSelection,
  creatorMusicAttributionFields,
  creatorMusicAttributionFieldsForAsset,
  creatorMusicTrackIsUsable,
  creatorMusicTrackRefFromPulseMusic,
  describeCreatorMusicSelection,
  withCreatorMusicMixer,
  withCreatorMusicStartOffset
} from "../creatorMusicSelection";

function track(overrides: Partial<PulseMusicTrack> = {}): PulseMusicTrack {
  return {
    id: "8821",
    title: "Midnight Drive",
    artist: "Ava Lang",
    artistUserId: 5501,
    durationSeconds: 214,
    previewUrl: "https://cdn.pulsesoc.com/music/8821-preview.m4a",
    audioUrl: "https://cdn.pulsesoc.com/music/8821.m4a",
    coverArtUrl: "https://cdn.pulsesoc.com/music/8821.jpg",
    waveform: [0.2, 0.4],
    genre: "synthwave",
    language: "en",
    mood: "night",
    licenseLabel: "pulsesoc_commercial_v2",
    moderationStatus: "approved",
    approvedByAdmin: true,
    active: true,
    playCount: 10,
    usageCount: 3,
    trendScore: 7,
    saveCount: 2,
    shareCount: 1,
    ...overrides
  };
}

describe("track eligibility", () => {
  it("accepts an approved, active, playable track", () => {
    expect(creatorMusicTrackIsUsable(track())).toBe(true);
  });

  it("rejects a track with no playable audio at all", () => {
    expect(creatorMusicTrackIsUsable(track({ audioUrl: "", previewUrl: "" }))).toBe(false);
  });

  it("rejects retired and unapproved tracks", () => {
    expect(creatorMusicTrackIsUsable(track({ active: false }))).toBe(false);
    expect(creatorMusicTrackIsUsable(track({ moderationStatus: "review" }))).toBe(false);
    expect(creatorMusicTrackIsUsable(track({ moderationStatus: "removed" }))).toBe(false);
  });
});

describe("track reference", () => {
  it("prefers the full asset over the preview encode", () => {
    // A 30 second preview would silently truncate a three minute take.
    expect(creatorMusicTrackRefFromPulseMusic(track()).audioUrl).toBe("https://cdn.pulsesoc.com/music/8821.m4a");
  });

  it("falls back to the preview only when there is no full asset", () => {
    expect(creatorMusicTrackRefFromPulseMusic(track({ audioUrl: "" })).audioUrl).toBe(
      "https://cdn.pulsesoc.com/music/8821-preview.m4a"
    );
  });

  it("keeps the identifiers and the rights reference, not just the display strings", () => {
    const ref = creatorMusicTrackRefFromPulseMusic(track());
    expect(ref.trackId).toBe("8821");
    expect(ref.artistUserId).toBe(5501);
    expect(ref.licenseLabel).toBe("pulsesoc_commercial_v2");
  });
});

describe("start offset", () => {
  it("cannot begin before the track does", () => {
    expect(clampCreatorMusicStartOffset(-30, 214)).toBe(0);
  });

  it("always leaves usable music after the start point", () => {
    expect(clampCreatorMusicStartOffset(213, 214)).toBe(214 - CREATOR_MUSIC_MIN_TAIL_SECONDS);
  });

  it("collapses to zero for a track shorter than the tail guard", () => {
    expect(clampCreatorMusicStartOffset(9, 3)).toBe(0);
  });

  it("passes an unknown duration through rather than guessing", () => {
    expect(clampCreatorMusicStartOffset(42, 0)).toBe(42);
  });

  it("re-clamps when the offset is changed on an existing selection", () => {
    const selection = createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 0);
    expect(withCreatorMusicStartOffset(selection, 9999).startOffsetSeconds).toBe(214 - CREATOR_MUSIC_MIN_TAIL_SECONDS);
  });
});

describe("attribution fields", () => {
  it("is empty for no selection, so a no-music upload is byte-identical to before", () => {
    expect(creatorMusicAttributionFields(null)).toEqual({});
    expect(creatorMusicAttributionFields(undefined)).toEqual({});
  });

  it("carries the identifiers the server needs to re-resolve rights", () => {
    const fields = creatorMusicAttributionFields(
      createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 32)
    );
    expect(fields.music_track_id).toBe("8821");
    expect(fields.music_artist_user_id).toBe("5501");
    expect(fields.music_rights_ref).toBe("pulsesoc_commercial_v2");
    expect(fields.music_start_offset_seconds).toBe("32");
    expect(fields.music_duration_seconds).toBe("214");
  });

  it("sends fader positions rather than derived dB", () => {
    // The server derives dB with the same taper. Sending dB would freeze old
    // drafts against a curve that may no longer exist.
    const selection = createCreatorMusicSelection(track(), withMusicLevel(applyCreatorMixerPreset("balanced"), 0.3), 0);
    const fields = creatorMusicAttributionFields(selection);
    expect(fields.music_level).toBe("0.3");
    expect(fields.music_preset).toBe("custom");
    expect(fields.music_duck_depth_db).toBe(String(selection.mixer.ducking.depthDb));
  });

  it("serialises every value as a string, because this rides on a multipart form", () => {
    const fields = creatorMusicAttributionFields(
      createCreatorMusicSelection(track(), applyCreatorMixerPreset("voice_focus"), 12)
    );
    Object.entries(fields).forEach(([key, value]) => {
      expect(typeof value).toBe("string");
      expect(key.startsWith("music_") || key.startsWith("mic_")).toBe(true);
    });
  });

  it("encodes the ducking switch as a flag the server can read without parsing booleans", () => {
    const on = creatorMusicAttributionFields(createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 0));
    expect(on.music_duck_enabled).toBe("1");
    const offMixer = { ...applyCreatorMixerPreset("balanced") };
    offMixer.ducking = { ...offMixer.ducking, enabled: false };
    const off = creatorMusicAttributionFields(createCreatorMusicSelection(track(), offMixer, 0));
    expect(off.music_duck_enabled).toBe("0");
  });
});

describe("the per-asset gate", () => {
  const selection = () => createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 8);

  it("attaches the soundtrack to a video", () => {
    expect(creatorMusicAttributionFieldsForAsset(selection(), "video").music_track_id).toBe("8821");
  });

  it("refuses to attach it to a photo", () => {
    // The upload route persists `music_*` for any media type and marks the row
    // pending; the worker only mixes video. A photo carrying a track id is a row
    // that waits forever to be mixed into a file with no audio track.
    expect(creatorMusicAttributionFieldsForAsset(selection(), "photo")).toEqual({});
  });

  it("refuses to attach it to a media type it does not recognise", () => {
    expect(creatorMusicAttributionFieldsForAsset(selection(), "")).toEqual({});
    expect(creatorMusicAttributionFieldsForAsset(selection(), undefined)).toEqual({});
    expect(creatorMusicAttributionFieldsForAsset(selection(), "livePhoto")).toEqual({});
  });

  it("still recognises a video when the media type arrives cased differently", () => {
    expect(creatorMusicAttributionFieldsForAsset(selection(), "VIDEO").music_track_id).toBe("8821");
  });

  it("sends nothing at all when no track was chosen", () => {
    // The no-music path has to stay byte-identical to the upload the camera sent
    // before this feature existed, or every plain video starts taking the mix
    // branch on the server.
    expect(creatorMusicAttributionFieldsForAsset(null, "video")).toEqual({});
  });
});

describe("selection summary", () => {
  it("reports the bus gains in dB, which is what a bad-sounding take is debugged with", () => {
    const summary = describeCreatorMusicSelection(
      createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 18)
    );
    expect(summary.trackId).toBe("8821");
    expect(summary.startOffsetSeconds).toBe(18);
    expect(summary.musicBusGainDb).toBeLessThan(0);
    expect(summary.micBusGainDb).toBeLessThan(0);
    expect(summary.duckDepthDb).toBeGreaterThan(0);
  });

  it("reports zero duck depth when ducking is disabled rather than the configured depth", () => {
    const selection = createCreatorMusicSelection(track(), applyCreatorMixerPreset("balanced"), 0);
    const disabled = withCreatorMusicMixer(selection, {
      ...selection.mixer,
      ducking: { ...selection.mixer.ducking, enabled: false }
    });
    expect(describeCreatorMusicSelection(disabled).duckDepthDb).toBe(0);
  });
});
