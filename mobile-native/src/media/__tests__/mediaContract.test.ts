import { hasRenderableImage, hasRenderableMediaUrl, isMediaUnavailable, renderableMedia } from "../mediaContract";

// The feed serializer emits a fully-shaped media object for every attached row.
// A row whose upload never produced a URL therefore arrives looking like media
// -- correct keys, correct types -- with every URL blank and dimensions at 0.
// Anything that decides layout from array length draws an empty box for it.
// These tests pin the predicate that replaced the length check.
describe("hasRenderableMediaUrl", () => {
  it("rejects the well-shaped but urlless payload the serializer can produce", () => {
    expect(
      hasRenderableMediaUrl({
        id: 1,
        media_type: "image",
        media_url: "",
        valid_url: "",
        thumbnail_url: "",
        poster_url: "",
        width: 0,
        height: 0
      })
    ).toBe(false);
  });

  it("rejects whitespace-only urls, which are blank to a renderer but truthy to JS", () => {
    expect(hasRenderableMediaUrl({ media_url: "   " })).toBe(false);
    expect(hasRenderableMediaUrl({ media_url: "\n\t" })).toBe(false);
  });

  it("rejects null, undefined, and an object with no url keys at all", () => {
    expect(hasRenderableMediaUrl(null)).toBe(false);
    expect(hasRenderableMediaUrl(undefined)).toBe(false);
    expect(hasRenderableMediaUrl({ id: 2, media_type: "image" })).toBe(false);
  });

  it("accepts media carried on any single one of the url fields", () => {
    const url = "https://cdn.example/a.png";
    expect(hasRenderableMediaUrl({ media_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ url })).toBe(true);
    expect(hasRenderableMediaUrl({ playback_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ hls_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ mux_hls_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ cdn_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ valid_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ thumbnail_url: url })).toBe(true);
    expect(hasRenderableMediaUrl({ poster_url: url })).toBe(true);
  });

  it("accepts a video still processing, as long as something is drawable", () => {
    // A poster with no playback URL yet is legitimately renderable.
    expect(
      hasRenderableMediaUrl({ media_type: "video", media_url: "", poster_url: "https://cdn.example/p.jpg" })
    ).toBe(true);
  });
});

describe("hasRenderableImage", () => {
  const good = { media_url: "https://cdn.example/a.png", valid_url: "https://cdn.example/a.png", width: 1024, height: 1280 };

  it("accepts an available image with real dimensions", () => {
    expect(hasRenderableImage(good)).toBe(true);
  });

  it("accepts an image sized by aspect_ratio alone", () => {
    expect(hasRenderableImage({ media_url: "https://cdn.example/a.png", aspect_ratio: 0.8 })).toBe(true);
  });

  it("rejects the serializer's failed-image signature: url present, unavailable, zero dims", () => {
    // media_url is populated unconditionally by the serializer; valid_url is
    // blanked and is_available flips false when the source never materialized.
    expect(
      hasRenderableImage({
        media_url: "https://cdn.example/failed.png",
        valid_url: "",
        is_available: false,
        width: 0,
        height: 0
      })
    ).toBe(false);
  });

  // This case used to assert `false`, and that assertion is what kept the ghost
  // post shipping: it is the exact signature of production post 2409 -- and of
  // 12 of the 13 images ever attached to a pulse post, i.e. every user-uploaded
  // one. `chat_media_uploads.width/height` are nullable and the pulse upload
  // path never filled them, so a perfectly healthy R2 image (is_available=1,
  // processing_status=ready, verification_status=verified, real bytes behind a
  // real CDN URL) arrives 0x0. Rejecting it dropped the media array, collapsed
  // MediaStrip to null, and rendered the post as author + actions + comments
  // with no picture -- while the profile grid, which reads thumbnail_url into a
  // fixed square and asks no dimension question, showed it fine.
  it("accepts an available image that simply never had its dimensions recorded", () => {
    expect(hasRenderableImage({ media_url: "https://cdn.example/a.png", width: 0, height: 0 })).toBe(true);
  });

  // The production record, field for field, as the detail endpoint serializes it.
  it("accepts the real post-2409 signature", () => {
    expect(
      hasRenderableImage({
        id: 739,
        media_type: "image",
        media_url: "https://cdn.coinpilotx.app/pulse_media/1/2026/09/16/x.jpg",
        valid_url: "https://cdn.coinpilotx.app/pulse_media/1/2026/09/16/x.jpg",
        thumbnail_url: "https://cdn.coinpilotx.app/pulse_media/1/2026/09/16/x-cover-.jpg",
        width: 0,
        height: 0,
        aspect_ratio: 0,
        processing_status: "ready",
        is_available: true
      })
    ).toBe(true);
  });

  // Losing a size must not cost the protection the dimension check was standing
  // in for: a row the server has marked gone stays unrenderable regardless.
  it("still rejects unavailable media that also lacks dimensions", () => {
    expect(
      hasRenderableImage({ media_url: "https://cdn.example/gone.png", valid_url: "", is_available: false })
    ).toBe(false);
  });

  it("still rejects media in a terminal processing state", () => {
    expect(
      hasRenderableImage({ media_url: "https://cdn.example/x.png", width: 800, height: 600, processing_status: "failed" })
    ).toBe(false);
  });

  it("rejects when there is no drawable url at all", () => {
    expect(hasRenderableImage({ media_url: "", width: 1024, height: 1280 })).toBe(false);
    expect(hasRenderableImage(null)).toBe(false);
    expect(hasRenderableImage(undefined)).toBe(false);
  });

  it("treats an explicit is_available:false as unrenderable even with dimensions", () => {
    expect(hasRenderableImage({ ...good, is_available: false })).toBe(false);
  });
});

describe("renderableMedia", () => {
  it("drops only the unrenderable entries and preserves order", () => {
    const a = { id: 1, media_url: "https://cdn.example/1.png" };
    const b = { id: 2, media_url: "" };
    const c = { id: 3, poster_url: "https://cdn.example/3.png" };
    expect(renderableMedia([a, b, c])).toEqual([a, c]);
  });

  it("returns an empty array for null, undefined, and all-invalid input", () => {
    expect(renderableMedia(null)).toEqual([]);
    expect(renderableMedia(undefined)).toEqual([]);
    expect(renderableMedia([{ id: 1, media_url: "" }, { id: 2 }])).toEqual([]);
  });

  it("does not mutate the caller's array", () => {
    const list = [{ id: 1, media_url: "" }, { id: 2, media_url: "https://cdn.example/2.png" }];
    renderableMedia(list);
    expect(list).toHaveLength(2);
  });
});

describe("isMediaUnavailable", () => {
  // The shape of production rows 28/29: local-disk video whose bytes went away
  // with a deploy. media_url still points at the old path, so every URL-only
  // gate says "renderable" and the player draws black.
  const lostVideo = {
    id: 28,
    media_type: "video",
    media_url: "/static/uploads/pulse_media/2026/05/24/ScreenRecording.mp4",
    playback_url: "/static/uploads/pulse_media/2026/05/24/ScreenRecording.mp4"
  };

  it("does not fire for healthy media", () => {
    expect(isMediaUnavailable({ ...lostVideo, is_available: true, processing_status: "ready" })).toBe(false);
    expect(isMediaUnavailable({ ...lostVideo })).toBe(false);
  });

  it("fires on the server's explicit unavailable flag", () => {
    expect(isMediaUnavailable({ ...lostVideo, is_available: false })).toBe(true);
  });

  it("fires on a terminal processing state", () => {
    expect(isMediaUnavailable({ ...lostVideo, processing_status: "failed" })).toBe(true);
    expect(isMediaUnavailable({ ...lostVideo, processing_status: "expired" })).toBe(true);
  });

  it("stays quiet for media still working its way through the pipeline", () => {
    expect(isMediaUnavailable({ ...lostVideo, processing_status: "mux_processing" })).toBe(false);
  });

  it("catches what the url gate cannot", () => {
    const lost = { ...lostVideo, is_available: false };
    expect(hasRenderableMediaUrl(lost)).toBe(true);
    expect(isMediaUnavailable(lost)).toBe(true);
  });

  it("is false for an absent record, which is not the same as a lost one", () => {
    expect(isMediaUnavailable(null)).toBe(false);
    expect(isMediaUnavailable(undefined)).toBe(false);
  });
});
