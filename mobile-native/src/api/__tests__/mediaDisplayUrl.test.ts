import { mediaDisplayUrl } from "../feed";

// The renderability gate (mediaContract.hasRenderableMediaUrl) admits a media
// record on the strength of any of nine URL fields. The display resolver must
// cover the same nine, or a record can pass the gate, reserve its aspect box,
// and resolve to "" -- an <Image uri=""> that draws the permanent blank
// rectangle the gate exists to prevent. These tests pin resolver >= gate.
describe("mediaDisplayUrl covers every gate-honored field", () => {
  it("resolves absolute urls carried on any single gate field", () => {
    const url = "https://cdn.example/a.png";
    expect(mediaDisplayUrl({ media_url: url })).toBe(url);
    expect(mediaDisplayUrl({ url })).toBe(url);
    expect(mediaDisplayUrl({ playback_url: url })).toBe(url);
    expect(mediaDisplayUrl({ hls_url: url })).toBe(url);
    expect(mediaDisplayUrl({ mux_hls_url: url })).toBe(url);
    expect(mediaDisplayUrl({ cdn_url: url })).toBe(url);
    expect(mediaDisplayUrl({ valid_url: url })).toBe(url);
    expect(mediaDisplayUrl({ thumbnail_url: url })).toBe(url);
    expect(mediaDisplayUrl({ poster_url: url })).toBe(url);
  });

  it("skips blank and whitespace-only fields to reach a usable one", () => {
    const url = "https://cdn.example/real.png";
    expect(mediaDisplayUrl({ media_url: "", url: "   ", valid_url: url })).toBe(url);
    expect(mediaDisplayUrl({ media_url: "\n\t", cdn_url: url })).toBe(url);
  });

  it("returns empty string when no field carries a drawable url", () => {
    expect(mediaDisplayUrl({ media_url: "", valid_url: "   " })).toBe("");
    expect(mediaDisplayUrl({})).toBe("");
  });
});
