import { hasRenderableImage, hasRenderableMediaUrl, renderableMedia } from "../mediaContract";

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

  it("rejects a url-bearing image with zero dimensions and no aspect ratio", () => {
    expect(hasRenderableImage({ media_url: "https://cdn.example/a.png", width: 0, height: 0 })).toBe(false);
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
