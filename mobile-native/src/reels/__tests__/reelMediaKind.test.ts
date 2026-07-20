jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import {
  classifyReelMedia,
  reelIsActiveLive,
  reelIsLiveContent,
  reelLiveSessionId,
  reelMediaSlides
} from "../reelMediaKind";
import type { PulseReel } from "../../api/reels";

function reel(overrides: Partial<PulseReel> = {}): PulseReel {
  return { id: 1, reel_id: 1, ...overrides } as PulseReel;
}

describe("classifyReelMedia", () => {
  it("classifies a single video as video", () => {
    const item = reel({ media: [{ media_type: "video", media_url: "https://cdn/x.mp4" }] });
    expect(classifyReelMedia(item)).toBe("video");
  });

  it("classifies a legacy video_url-only reel as video", () => {
    const item = reel({ media: [], video_url: "https://cdn/legacy.mp4" });
    expect(classifyReelMedia(item)).toBe("video");
  });

  it("classifies a single image as photo", () => {
    const item = reel({ media: [{ media_type: "image", media_url: "https://cdn/x.jpg" }] });
    expect(classifyReelMedia(item)).toBe("photo");
  });

  it("classifies multiple media as carousel", () => {
    const item = reel({
      media: [
        { media_type: "image", media_url: "https://cdn/a.jpg" },
        { media_type: "video", media_url: "https://cdn/b.mp4" }
      ]
    });
    expect(classifyReelMedia(item)).toBe("carousel");
  });

  it("honors an explicit carousel/gallery content type even with one slide", () => {
    const item = reel({ content_type: "carousel", media: [{ media_type: "image", media_url: "https://cdn/a.jpg" }] });
    expect(classifyReelMedia(item)).toBe("carousel");
  });

  it("classifies an active live session as livestream", () => {
    const item = reel({ content_type: "live", live_session_id: 55, live: { live_session_id: 55, status: "live" } });
    expect(classifyReelMedia(item)).toBe("livestream");
  });

  it("classifies an ended live session as replay", () => {
    const item = reel({ content_type: "live", live_session_id: 55, live: { live_session_id: 55, status: "ended", playback_url: "https://cdn/vod.m3u8" } });
    expect(classifyReelMedia(item)).toBe("replay");
  });

  it("treats an explicit replay content type as replay", () => {
    const item = reel({ content_type: "replay", live_session_id: 55, live: { live_session_id: 55, status: "live" } });
    expect(classifyReelMedia(item)).toBe("replay");
  });

  it("defaults live content with an unknown status to livestream", () => {
    const item = reel({ live_session_id: 77 });
    expect(classifyReelMedia(item)).toBe("livestream");
  });
});

describe("reelIsLiveContent / reelIsActiveLive / reelLiveSessionId", () => {
  it("reads the live session id from either field", () => {
    expect(reelLiveSessionId(reel({ live_session_id: 9 }))).toBe(9);
    expect(reelLiveSessionId(reel({ live: { live_session_id: 12 } }))).toBe(12);
    expect(reelLiveSessionId(reel())).toBe(0);
  });

  it("detects live content and its active/ended state", () => {
    expect(reelIsLiveContent(reel({ live_session_id: 3 }))).toBe(true);
    expect(reelIsLiveContent(reel())).toBe(false);
    expect(reelIsActiveLive(reel({ live_session_id: 3, live: { status: "streaming" } }))).toBe(true);
    expect(reelIsActiveLive(reel({ live_session_id: 3, live: { status: "ended" } }))).toBe(false);
    expect(reelIsActiveLive(reel())).toBe(false);
  });
});

describe("reelMediaSlides", () => {
  it("resolves mux playback ids to hls urls", () => {
    const slides = reelMediaSlides(reel({ media: [{ media_type: "video", mux_playback_id: "abc123" }] }));
    expect(slides).toHaveLength(1);
    expect(slides[0].kind).toBe("video");
    expect(slides[0].url).toBe("https://stream.mux.com/abc123.m3u8");
  });

  it("keeps image slides with their own url as the poster", () => {
    const slides = reelMediaSlides(reel({ media: [{ media_type: "image", media_url: "https://cdn/a.jpg" }] }));
    expect(slides[0].kind).toBe("image");
    expect(slides[0].url).toBe("https://cdn/a.jpg");
    expect(slides[0].poster).toBe("https://cdn/a.jpg");
  });

  it("drops media without a usable url and preserves order", () => {
    const slides = reelMediaSlides(reel({
      media: [
        { media_type: "image", media_url: "https://cdn/a.jpg" },
        { media_type: "video" },
        { media_type: "video", media_url: "https://cdn/c.mp4" }
      ]
    }));
    expect(slides.map((slide) => slide.url)).toEqual(["https://cdn/a.jpg", "https://cdn/c.mp4"]);
  });

  it("falls back to a legacy video_url when no media array is present", () => {
    const slides = reelMediaSlides(reel({ media: [], video_url: "https://cdn/legacy.mp4", poster_url: "https://cdn/p.jpg" }));
    expect(slides).toEqual([{ key: "1:legacy", kind: "video", url: "https://cdn/legacy.mp4", poster: "https://cdn/p.jpg" }]);
  });
});
