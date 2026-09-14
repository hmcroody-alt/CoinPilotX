/**
 * Video Statuses must resolve to the transcoded stream, not the upload.
 *
 * Production failure this pins: `statusMediaUrl` resolved `valid_url` first,
 * and for a video the backend sets `valid_url` to the original `.mov` on the
 * media CDN. Cloudflare serves those with a managed challenge -- 403 and an
 * HTML body -- so AVPlayer received a web page, never loaded, and the Status
 * viewer showed a black rectangle underneath fully working chrome. Photo
 * Statuses were unaffected, because their originals are plain JPEGs that the
 * CDN serves normally, which is why the bug looked like "videos are broken"
 * rather than "the URL order is wrong".
 */

import { statusMediaUrl, statusPosterUrl } from "../status";
import type { PulseStatus } from "../status";

const CDN = "https://cdn.coinpilotx.app/pulse_media/4/2026/09/11/abc";

function videoStatus(media: Record<string, unknown>): PulseStatus {
  return { id: 1, media: [media] } as unknown as PulseStatus;
}

describe("statusMediaUrl", () => {
  it("prefers the Mux playback URL over the raw upload for video", () => {
    const url = statusMediaUrl(
      videoStatus({
        media_type: "video",
        valid_url: `${CDN}/clip.mov`,
        media_url: `${CDN}/clip.mov`,
        cdn_url: `${CDN}/clip.mov`,
        playback_url: "https://stream.mux.com/pid.m3u8",
        poster_url: `${CDN}/clip-cover-large.jpg`
      })
    );
    expect(url).toBe("https://stream.mux.com/pid.m3u8");
    expect(url).not.toContain(".mov");
  });

  it("falls back to hls_url, then mux_hls_url, before the original", () => {
    expect(
      statusMediaUrl(
        videoStatus({ media_type: "video", valid_url: `${CDN}/clip.mov`, hls_url: "https://stream.mux.com/h.m3u8" })
      )
    ).toBe("https://stream.mux.com/h.m3u8");

    expect(
      statusMediaUrl(
        videoStatus({ media_type: "video", valid_url: `${CDN}/clip.mov`, mux_hls_url: "https://stream.mux.com/m.m3u8" })
      )
    ).toBe("https://stream.mux.com/m.m3u8");
  });

  it("still serves the original when a video has no transcode yet", () => {
    expect(statusMediaUrl(videoStatus({ media_type: "video", valid_url: `${CDN}/clip.mov` }))).toBe(`${CDN}/clip.mov`);
  });

  it("leaves photo Statuses on the image file, never a /stream route", () => {
    const url = statusMediaUrl(
      videoStatus({
        media_type: "image",
        valid_url: `${CDN}/shot.jpg`,
        media_url: `${CDN}/shot.jpg`,
        // The backend fills playback_url for images too, with a first-party
        // stream route. An <Image> must not be pointed at it.
        playback_url: "/api/pulse/media/734/stream"
      })
    );
    expect(url).toBe(`${CDN}/shot.jpg`);
    expect(url).not.toContain("/stream");
  });

  it("builds the stream from the Mux playback id before trusting any stored URL", () => {
    // Mux is the transcode pipeline, so the id is the primary source, matching
    // reelVideoUrl and every web surface. A stored playback_url that disagrees
    // with the id loses: the id is what Mux itself is authoritative about.
    expect(
      statusMediaUrl(
        videoStatus({
          media_type: "video",
          mux_playback_id: "pid123",
          playback_url: "https://stream.mux.com/stale.m3u8",
          valid_url: `${CDN}/clip.mov`
        })
      )
    ).toBe("https://stream.mux.com/pid123.m3u8");
  });

  it("plays from the playback id even when no URL column was ever persisted", () => {
    // This is the case the fallback chain cannot serve: without the id the only
    // candidate left is valid_url, which is the .mov Cloudflare answers with a
    // challenge page -- a black Status.
    const url = statusMediaUrl(videoStatus({ media_type: "video", mux_playback_id: "pid456", valid_url: `${CDN}/clip.mov` }));
    expect(url).toBe("https://stream.mux.com/pid456.m3u8");
    expect(url).not.toContain(".mov");
  });

  it("ignores a playback id on a photo, which has no Mux asset to stream", () => {
    expect(
      statusMediaUrl(
        videoStatus({ media_type: "image", mux_playback_id: "pid789", valid_url: `${CDN}/shot.jpg` })
      )
    ).toBe(`${CDN}/shot.jpg`);
  });

  it("returns empty rather than a partial URL when a Status carries no media", () => {
    expect(statusMediaUrl({ id: 1 } as unknown as PulseStatus)).toBe("");
  });

  it("keeps the generated cover as the poster so a paused video is never blank", () => {
    expect(
      statusPosterUrl(
        videoStatus({
          media_type: "video",
          poster_url: `${CDN}/clip-cover-large.jpg`,
          valid_url: `${CDN}/clip.mov`,
          playback_url: "https://stream.mux.com/pid.m3u8"
        })
      )
    ).toBe(`${CDN}/clip-cover-large.jpg`);
  });
});
