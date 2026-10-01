import { nativeMediaUploadUrl, validateNativeMedia } from "../nativeMediaUpload";

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
  const video = (size: number) => ({
    uri: "file:///tmp/creator.mp4",
    name: "creator.mp4",
    mimeType: "video/mp4",
    mediaType: "video" as const,
    size
  });

  it("accepts a measured 205 MiB creator video for Feed and Reels", () => {
    expect(validateNativeMedia(video(205 * 1024 * 1024), "pulse_post")).toBe("");
  });

  it("keeps Status and creator-video ceilings bounded independently", () => {
    expect(validateNativeMedia(video(351 * 1024 * 1024), "pulse_status")).toContain("350 MB");
    expect(validateNativeMedia(video(701 * 1024 * 1024), "pulse_post")).toContain("700 MB");
  });
});
