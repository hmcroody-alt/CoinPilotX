jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

// Isolate the create endpoints from the network; capture the exact request body
// so we can assert the defense-in-depth music metadata is serialized verbatim.
jest.mock("../pulseApi", () => ({
  pulseApi: jest.fn(async () => ({ ok: true }))
}));

import { pulseApi } from "../pulseApi";
import { createReel } from "../reels";
import { createStatus } from "../status";

const apiMock = pulseApi as jest.Mock;

function lastBody() {
  const call = apiMock.mock.calls[apiMock.mock.calls.length - 1];
  return JSON.parse(call[1].body as string);
}

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({ ok: true, reel_id: 1, post_id: 1, status_id: 1 });
});

describe("createReel music payload", () => {
  it("carries attached-audio metadata so playback survives without server enrichment", async () => {
    await createReel({
      media_ids: [10],
      music_track_id: "trk_7",
      attached_audio_url: "https://cdn/track.m3u8",
      original_audio_muted: true,
      audio_start_time: 2,
      audio_volume: 0.6
    });
    const body = lastBody();
    expect(body.music_track_id).toBe("trk_7");
    expect(body.audio_track_id).toBe("trk_7");
    expect(body.attached_audio_url).toBe("https://cdn/track.m3u8");
    expect(body.original_audio_muted).toBe(true);
    expect(body.audio_start_time).toBe(2);
    expect(body.audio_volume).toBe(0.6);
  });

  it("defaults original_audio_muted to true whenever a track id is present", async () => {
    await createReel({ media_ids: [10], music_track_id: "trk_9" });
    expect(lastBody().original_audio_muted).toBe(true);
  });

  it("does not force muting when no music is attached", async () => {
    await createReel({ media_ids: [10] });
    const body = lastBody();
    expect(body.original_audio_muted).toBe(false);
    expect(body.attached_audio_url).toBe("");
  });

  it("keeps baked-in attribution without attaching duplicate playback", async () => {
    await createReel({ media_ids: [10], music_track_id: "trk_9", audio_start_time: 12, original_audio_muted: false, audio_baked_in: true });
    expect(lastBody()).toMatchObject({ music_track_id: "trk_9", sound_start_seconds: 12, original_audio_muted: false, audio_baked_in: true });
  });
});

describe("createStatus music payload", () => {
  it("passes the attached-audio metadata through verbatim", async () => {
    await createStatus({
      status_type: "video",
      media_ids: [5],
      music_track_id: "trk_3",
      attached_audio_url: "https://cdn/status.m3u8",
      original_audio_muted: true,
      audio_start_time: 0,
      audio_volume: 1
    });
    const body = lastBody();
    expect(body.music_track_id).toBe("trk_3");
    expect(body.attached_audio_url).toBe("https://cdn/status.m3u8");
    expect(body.original_audio_muted).toBe(true);
  });
});
