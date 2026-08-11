import { nativeMediaUploadUrl } from "../nativeMediaUpload";

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
