import { nativeMediaUploadUrl, validateNativeMedia } from "../nativeMediaUpload";
import { MAX_STORED_VIDEO_SECONDS, maxVideoBytes } from "../storedVideoPolicy";

describe("nativeMediaUploadUrl", () => {
  it("keeps the generic PulseSoc media upload endpoint by default", () => {
    expect(nativeMediaUploadUrl()).toContain("/api/pulse/media/upload");
  });

  it("allows Marketplace listings to use the canonical product-media route", () => {
    expect(nativeMediaUploadUrl("/api/pulse/marketplace/media/upload")).toContain(
      "/api/pulse/marketplace/media/upload"
    );
  });

  it("preserves absolute URLs for test harnesses and signed upload targets", () => {
    expect(nativeMediaUploadUrl("https://uploads.example.test/direct")).toBe("https://uploads.example.test/direct");
  });
});

describe("native video size policy", () => {
  const video = (size: number, durationMs?: number) => ({
    uri: "file:///tmp/creator.mp4",
    name: "creator.mp4",
    mimeType: "video/mp4",
    mediaType: "video" as const,
    size,
    duration: durationMs
  });

  it("accepts a measured 205 MiB creator video for Feed and Reels", () => {
    expect(validateNativeMedia(video(205 * 1024 * 1024), "pulse_post")).toBe("");
  });

  it("lets a 90-minute video through at a watchable bitrate", () => {
    // The old flat 700 MiB ceiling was 1.04 Mbps over 90 minutes, so the app
    // refused video the duration policy claimed to allow. Asserting the property
    // rather than a literal keeps this honest if the budget is ever retuned.
    const bitrate = (maxVideoBytes("pulse_post") * 8) / MAX_STORED_VIDEO_SECONDS;
    expect(bitrate).toBeGreaterThanOrEqual(2_500_000);
    expect(validateNativeMedia(video(1500 * 1024 * 1024, 5_400_000), "pulse_post")).toBe("");
  });

  it("keeps Status and creator-video ceilings bounded independently", () => {
    expect(validateNativeMedia(video(351 * 1024 * 1024), "pulse_status")).toContain("350 MB");
    expect(validateNativeMedia(video(maxVideoBytes("pulse_post") + 1), "pulse_post")).toContain("too large");
    expect(maxVideoBytes("pulse_post")).toBeGreaterThan(maxVideoBytes("pulse_status"));
  });

  it("refuses an over-long video before the upload starts, naming the real limit", () => {
    expect(validateNativeMedia(video(50 * 1024 * 1024, 5_401_000), "pulse_post")).toBe("Videos can be up to 90 minutes long.");
    expect(validateNativeMedia(video(50 * 1024 * 1024, 61_000), "pulse_status")).toBe("Videos can be up to 1 minute long.");
  });

  it("accepts exactly 90 minutes and reads the picker's milliseconds as milliseconds", () => {
    expect(validateNativeMedia(video(50 * 1024 * 1024, 5_400_000), "pulse_post")).toBe("");
    // Read as seconds this would be 62 days and the clip would be refused.
    expect(validateNativeMedia(video(50 * 1024 * 1024, 30_000), "pulse_status")).toBe("");
  });

  it("does not refuse a clip whose duration the picker never reported", () => {
    expect(validateNativeMedia(video(50 * 1024 * 1024, undefined), "pulse_post")).toBe("");
    expect(validateNativeMedia(video(50 * 1024 * 1024, 0), "pulse_status")).toBe("");
  });
});
