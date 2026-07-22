import { PULSE_API_BASE_URL } from "../../api/config";
import {
  ATTACHED_MUSIC_EXCLUSIVE,
  ORIGINAL_AUDIO,
  postMusicToMusicSource,
  resolveAttachedMusicPolicy,
  resolvePostAudioPolicy,
  resolveReelAudioPolicy,
  resolveStatusMusicPolicy
} from "../attachedMusicAudioPolicy";

describe("resolveAttachedMusicPolicy", () => {
  it("mutes original audio and enters exclusive mode when music is attached", () => {
    const policy = resolveAttachedMusicPolicy({ musicUrl: "https://cdn/track.m3u8", volume: 0.5, startSeconds: 3 });
    expect(policy.mode).toBe(ATTACHED_MUSIC_EXCLUSIVE);
    expect(policy.hasAttachedMusic).toBe(true);
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe("https://cdn/track.m3u8");
    expect(policy.musicVolume).toBe(0.5);
    expect(policy.musicStartMs).toBe(3000);
    expect(policy.isLooping).toBe(true);
  });

  it("plays original audio normally when no music is attached", () => {
    const policy = resolveAttachedMusicPolicy({ musicUrl: "" });
    expect(policy.mode).toBe(ORIGINAL_AUDIO);
    expect(policy.hasAttachedMusic).toBe(false);
    expect(policy.muteOriginalAudio).toBe(false);
    expect(policy.musicUrl).toBeUndefined();
  });

  it("treats null/undefined source as original audio", () => {
    expect(resolveAttachedMusicPolicy(null).muteOriginalAudio).toBe(false);
    expect(resolveAttachedMusicPolicy(undefined).mode).toBe(ORIGINAL_AUDIO);
  });

  it("ignores whitespace-only music urls", () => {
    expect(resolveAttachedMusicPolicy({ musicUrl: "   " }).hasAttachedMusic).toBe(false);
  });

  it("clamps volume into the 0..1 range and defaults to 1", () => {
    expect(resolveAttachedMusicPolicy({ musicUrl: "u", volume: 2 }).musicVolume).toBe(1);
    expect(resolveAttachedMusicPolicy({ musicUrl: "u", volume: -3 }).musicVolume).toBe(0);
    expect(resolveAttachedMusicPolicy({ musicUrl: "u", volume: null }).musicVolume).toBe(1);
    expect(resolveAttachedMusicPolicy({ musicUrl: "u" }).musicVolume).toBe(1);
  });

  it("never returns a negative start offset", () => {
    expect(resolveAttachedMusicPolicy({ musicUrl: "u", startSeconds: -5 }).musicStartMs).toBe(0);
  });

  it("honors an explicit non-looping request", () => {
    expect(resolveAttachedMusicPolicy({ musicUrl: "u", isLooping: false }).isLooping).toBe(false);
  });
});

describe("reel adapter", () => {
  it("forces original audio off when a reel has an attached track", () => {
    const policy = resolveReelAudioPolicy({ attached_audio_url: "https://cdn/quiet.m3u8", audio_volume: 0.18, audio_start_time: 0, original_audio_muted: true });
    expect(policy.mode).toBe(ATTACHED_MUSIC_EXCLUSIVE);
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicVolume).toBe(0.18);
  });

  it("normalizes a stray original_audio_muted:false to still mute when music is attached", () => {
    const policy = resolveReelAudioPolicy({ attached_audio_url: "https://cdn/quiet.m3u8", original_audio_muted: false });
    expect(policy.muteOriginalAudio).toBe(true);
  });

  it("does not mute when a reel only carries original audio", () => {
    const policy = resolveReelAudioPolicy({ audio_url: "https://cdn/original.m3u8" });
    expect(policy.mode).toBe(ORIGINAL_AUDIO);
    expect(policy.muteOriginalAudio).toBe(false);
  });

  it("handles a reel with no audio metadata", () => {
    expect(resolveReelAudioPolicy(undefined).muteOriginalAudio).toBe(false);
  });
});

describe("status adapter", () => {
  it("mutes original audio when a status attaches music", () => {
    const policy = resolveStatusMusicPolicy({ attached_audio_url: "https://cdn/status.m3u8" });
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe("https://cdn/status.m3u8");
  });

  it("falls back to audio_url as the attached track for statuses", () => {
    const policy = resolveStatusMusicPolicy({ audio_url: "https://cdn/status-audio.m3u8" });
    expect(policy.mode).toBe(ATTACHED_MUSIC_EXCLUSIVE);
    expect(policy.musicUrl).toBe("https://cdn/status-audio.m3u8");
  });

  it("treats attribution-only music (no url) as original audio", () => {
    const policy = resolveStatusMusicPolicy({ title: "Orbit Signal", artist: "PulseSoc Audio" });
    expect(policy.hasAttachedMusic).toBe(false);
    expect(policy.muteOriginalAudio).toBe(false);
  });
});

describe("music url absolutization", () => {
  it("prefixes a server-relative track path with the API base so Audio.Sound can load it", () => {
    const policy = resolveAttachedMusicPolicy({ musicUrl: "/static/audit/attached-pulsesoc-music.wav" });
    expect(policy.hasAttachedMusic).toBe(true);
    expect(policy.musicUrl).toBe(`${PULSE_API_BASE_URL}/static/audit/attached-pulsesoc-music.wav`);
  });

  it("leaves an already-absolute http url untouched", () => {
    const policy = resolveAttachedMusicPolicy({ musicUrl: "https://cdn/track.m3u8" });
    expect(policy.musicUrl).toBe("https://cdn/track.m3u8");
  });

  it("absolutizes a bare relative path that does not start with a slash", () => {
    const policy = resolveAttachedMusicPolicy({ musicUrl: "media/track.wav" });
    expect(policy.musicUrl).toBe(`${PULSE_API_BASE_URL}/media/track.wav`);
  });

  it("absolutizes the relative attached_audio_url a published video post carries", () => {
    const policy = resolvePostAudioPolicy({ music: { attached_audio_url: "/static/audit/attached-pulsesoc-music.wav" } });
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe(`${PULSE_API_BASE_URL}/static/audit/attached-pulsesoc-music.wav`);
  });
});

describe("post adapter", () => {
  it("mutes the original video track when a post carries a post-level music object", () => {
    const policy = resolvePostAudioPolicy({
      music: { attached_audio_url: "https://cdn/post-track.m3u8", audio_volume: 0.4, audio_start_time: 2 }
    });
    expect(policy.mode).toBe(ATTACHED_MUSIC_EXCLUSIVE);
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe("https://cdn/post-track.m3u8");
    expect(policy.musicVolume).toBe(0.4);
    expect(policy.musicStartMs).toBe(2000);
  });

  it("reads the top-level mirror fields when there is no music object", () => {
    const policy = resolvePostAudioPolicy({ attached_audio_url: "https://cdn/top.m3u8", audio_start_time: 1 });
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe("https://cdn/top.m3u8");
    expect(policy.musicStartMs).toBe(1000);
  });

  it("falls back to the media record's attached_audio_url", () => {
    const policy = resolvePostAudioPolicy(null, { attached_audio_url: "https://cdn/media.m3u8" });
    expect(policy.musicUrl).toBe("https://cdn/media.m3u8");
    expect(policy.muteOriginalAudio).toBe(true);
  });

  it("prefers the post-level music object over top-level and media fallbacks", () => {
    const source = postMusicToMusicSource(
      { music: { attached_audio_url: "https://cdn/win.m3u8" }, attached_audio_url: "https://cdn/lose.m3u8" },
      { attached_audio_url: "https://cdn/also-lose.m3u8" }
    );
    expect(source.musicUrl).toBe("https://cdn/win.m3u8");
  });

  it("plays original video audio when the post has no attached music", () => {
    const policy = resolvePostAudioPolicy({}, { url: "https://cdn/clip.mp4" });
    expect(policy.mode).toBe(ORIGINAL_AUDIO);
    expect(policy.muteOriginalAudio).toBe(false);
    expect(policy.hasAttachedMusic).toBe(false);
  });

  it("handles a fully empty/nullish post", () => {
    expect(resolvePostAudioPolicy(null, null).muteOriginalAudio).toBe(false);
    expect(resolvePostAudioPolicy(undefined).mode).toBe(ORIGINAL_AUDIO);
  });
});
