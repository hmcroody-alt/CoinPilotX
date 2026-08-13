import { mediaUploadParallelParts } from "../MediaUploadManager";

describe("MediaUploadManager policy", () => {
  it("uses bounded parallelism", () => {
    expect(mediaUploadParallelParts).toBeGreaterThanOrEqual(3);
    expect(mediaUploadParallelParts).toBeLessThanOrEqual(6);
  });
});
