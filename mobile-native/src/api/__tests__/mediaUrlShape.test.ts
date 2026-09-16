/**
 * The two synchronous questions a URL chooser can actually answer.
 *
 * `isLoadableMediaUrl` asks "will a native loader attempt this at all", and
 * `isAdaptiveManifest` asks "is this a playlist rather than a file". Both are
 * pure shape tests, and both exist because the alternative failure is silent:
 * a site-relative URL paints an empty box with no error, and a manifest handed
 * to the downloader *succeeds* and then gets rejected by the photo library four
 * layers away from the cause.
 *
 * These are asserted directly rather than only through their callers, because
 * the interesting cases are ones no caller can currently produce. Messenger is
 * served HLS today, so nothing in the app can reach the DASH branch — and an
 * unreachable branch is one nobody would notice being deleted, right up until
 * someone switches DASH on and Save to Photos starts writing playlists again.
 *
 * The server's `_is_adaptive_manifest` in `pulse_communications_v2/service.py`
 * answers the same question and is pinned by the same cases, so the two cannot
 * drift into disagreeing about what a file is.
 */

import { isAdaptiveManifest, isLoadableMediaUrl } from "../config";

describe("isAdaptiveManifest", () => {
  it("recognises both of Mux's playlist formats, signed or bare", () => {
    expect(isAdaptiveManifest("https://stream.mux.com/vod601.m3u8")).toBe(true);
    expect(isAdaptiveManifest("https://stream.mux.com/vod601.mpd")).toBe(true);
  });

  it("still recognises one when a signing token hides the extension", () => {
    // The case that matters. A signed manifest is `.m3u8?token=<jwt>`, so a
    // check against the whole string stops recognising manifests at exactly
    // the moment messenger starts signing them.
    expect(isAdaptiveManifest("https://stream.mux.com/vod601.m3u8?token=eyJ.abc.def")).toBe(true);
    expect(isAdaptiveManifest("https://stream.mux.com/vod601.mpd?token=eyJ.abc.def")).toBe(true);
  });

  it("does not call a file a playlist", () => {
    // A predicate that answered yes too often would strip the download URL off
    // perfectly good video and break Save for anything that never went near
    // Mux. The last case is the false positive a whole-string check produces:
    // a query parameter that happens to end in a playlist name.
    expect(isAdaptiveManifest("https://pulsesoc.com/api/messages/media/87/download")).toBe(false);
    expect(isAdaptiveManifest("https://cdn.example/clip.mp4")).toBe(false);
    expect(isAdaptiveManifest("https://cdn.example/clip.mp4?next=intro.m3u8")).toBe(false);
    expect(isAdaptiveManifest("")).toBe(false);
    expect(isAdaptiveManifest(null)).toBe(false);
    expect(isAdaptiveManifest(undefined)).toBe(false);
  });

  it("ignores case and a trailing fragment", () => {
    expect(isAdaptiveManifest("https://stream.mux.com/VOD601.M3U8")).toBe(true);
    expect(isAdaptiveManifest("https://stream.mux.com/vod601.m3u8#t=3")).toBe(true);
  });

  it("is a different question to loadability", () => {
    // The two are orthogonal and a caller needs both: a manifest is perfectly
    // loadable (it is what the player wants) and a progressive file is
    // perfectly not-a-manifest. Collapsing them into one check is how a player
    // ends up pointed at a file and a downloader at a playlist.
    const manifest = "https://stream.mux.com/vod601.m3u8?token=eyJ.abc.def";
    expect(isLoadableMediaUrl(manifest)).toBe(true);
    expect(isAdaptiveManifest(manifest)).toBe(true);

    const relative = "/api/messages/media/87/download";
    expect(isLoadableMediaUrl(relative)).toBe(false);
    expect(isAdaptiveManifest(relative)).toBe(false);
  });
});
