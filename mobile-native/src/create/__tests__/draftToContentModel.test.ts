jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import { draftToContentModel, ComposerDraftInput } from "../draftToContentModel";
import { mediaDisplayUrl } from "../../api/feed";
import { reelVideoUrl } from "../../api/reels";
import { statusMediaUrl } from "../../api/status";
import { resolveReelAudioPolicy, resolveStatusMusicPolicy } from "../../core/attachedMusicAudioPolicy";
import { NativeMediaAsset } from "../../media/nativeMediaUpload";

const NOW = Date.UTC(2026, 6, 20, 12, 0, 0);

function imageAsset(uri = "file:///tmp/photo.jpg"): NativeMediaAsset {
  return { uri, name: "photo.jpg", mimeType: "image/jpeg", mediaType: "image", width: 1080, height: 1350 };
}

function videoAsset(uri = "file:///tmp/clip.mov"): NativeMediaAsset {
  return { uri, name: "clip.mov", mimeType: "video/quicktime", mediaType: "video", width: 1080, height: 1920, duration: 12 };
}

function baseDraft(overrides: Partial<ComposerDraftInput> = {}): ComposerDraftInput {
  return {
    mode: "post",
    body: "",
    visibility: "public",
    topic: "",
    musicTrack: null,
    media: [],
    now: NOW,
    ...overrides
  };
}

const track = {
  id: "42",
  title: "Quiet Orbit",
  artist: "PulseSoc Music",
  previewUrl: "https://cdn.pulsesoc.test/audio/42.mp3",
  durationSeconds: 30,
  licenseLabel: "approved"
};

describe("draftToContentModel", () => {
  it("returns null when nothing is publishable", () => {
    expect(draftToContentModel(baseDraft())).toBeNull();
    expect(draftToContentModel(baseDraft({ body: "   " }))).toBeNull();
  });

  it("builds a text feed post that renders through the canonical post model", () => {
    const result = draftToContentModel(baseDraft({ body: "Hello PulseSoc", topic: "launch" }));
    expect(result?.kind).toBe("post");
    if (result?.kind !== "post") throw new Error("expected post");
    expect(result.post.body).toBe("Hello PulseSoc");
    expect(result.post.visibility).toBe("public");
    // normalizePost always assigns a canonical id/post_id pair.
    expect(result.post.id).toBe(result.post.post_id);
    expect(result.post.media).toHaveLength(0);
  });

  it("preserves local media URIs so the preview renders device files unchanged", () => {
    const result = draftToContentModel(baseDraft({ mode: "post", body: "pic", media: [{ asset: imageAsset(), result: null }] }));
    if (result?.kind !== "post") throw new Error("expected post");
    expect(result.post.media).toHaveLength(1);
    // The shared display helper must return the local file URI verbatim (no
    // API-base prefixing), which is what makes preview == published.
    expect(mediaDisplayUrl(result.post.media![0])).toBe("file:///tmp/photo.jpg");
  });

  it("builds a reel from a single video and exposes the local playback URL", () => {
    const result = draftToContentModel(baseDraft({ mode: "reel", body: "watch", media: [{ asset: videoAsset(), result: null }] }));
    if (result?.kind !== "reel") throw new Error("expected reel");
    expect(reelVideoUrl(result.reel)).toBe("file:///tmp/clip.mov");
    expect(result.reel.caption).toBe("watch");
  });

  it("mutes original audio and attaches approved music on a reel (audio policy)", () => {
    const result = draftToContentModel(baseDraft({ mode: "reel", body: "beat", media: [{ asset: videoAsset(), result: null }], musicTrack: track }));
    if (result?.kind !== "reel") throw new Error("expected reel");
    expect(result.reel.audio?.original_audio_muted).toBe(true);
    const policy = resolveReelAudioPolicy(result.reel.audio);
    expect(policy.hasAttachedMusic).toBe(true);
    expect(policy.muteOriginalAudio).toBe(true);
    expect(policy.musicUrl).toBe(track.previewUrl);
  });

  it("classifies status type from media and attaches music", () => {
    const photoStatus = draftToContentModel(baseDraft({ mode: "status", body: "", media: [{ asset: imageAsset(), result: null }] }));
    if (photoStatus?.kind !== "status") throw new Error("expected status");
    expect(photoStatus.status.status_type).toBe("photo");
    expect(statusMediaUrl(photoStatus.status)).toBe("file:///tmp/photo.jpg");

    const musicStatus = draftToContentModel(baseDraft({ mode: "status", body: "vibes", musicTrack: track }));
    if (musicStatus?.kind !== "status") throw new Error("expected status");
    expect(musicStatus.status.status_type).toBe("music");
    const policy = resolveStatusMusicPolicy(musicStatus.status.music);
    expect(policy.hasAttachedMusic).toBe(true);
    expect(policy.muteOriginalAudio).toBe(true);
  });

  it("maps poll and scam_report drafts to feed posts", () => {
    const poll = draftToContentModel(baseDraft({ mode: "poll", body: "Best chain?" }));
    expect(poll?.kind).toBe("post");
    const scam = draftToContentModel(baseDraft({ mode: "scam_report", body: "Warning about a fake airdrop link" }));
    expect(scam?.kind).toBe("post");
  });

  it("uses the composer identity for the preview author", () => {
    const result = draftToContentModel(baseDraft({ body: "hi", identity: { displayName: "Nova", username: "nova" } }));
    if (result?.kind !== "post") throw new Error("expected post");
    expect(result.post.author?.display_name).toBe("Nova");
    expect(result.post.author?.username).toBe("nova");
  });
});
