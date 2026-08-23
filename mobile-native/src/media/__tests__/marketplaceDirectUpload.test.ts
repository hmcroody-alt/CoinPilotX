/**
 * Marketplace product media used to be POSTed as a multipart body to Flask,
 * which buffered the whole file in the web process before forwarding it to R2.
 * Every other surface (Feed, Status, Messages) already uploaded straight to R2
 * through `mediaUploadManager`; Marketplace opted out purely by passing an
 * `endpointPath`.
 *
 * These tests pin the routing decision itself, because it is invisible at the
 * call site: an `endpointPath` reappearing in the composer would silently
 * restore the Flask proxy hop with no other symptom than slower uploads.
 */
import { uploadNativeMedia } from "../nativeMediaUpload";

const mockManagerUpload = jest.fn();
jest.mock("../MediaUploadManager", () => ({
  mediaUploadManager: { upload: (...args: unknown[]) => mockManagerUpload(...args) },
  mediaUploadParallelParts: 4
}));

const productImage = {
  uri: "file:///tmp/product.jpg",
  name: "product.jpg",
  mimeType: "image/jpeg",
  mediaType: "image" as const,
  size: 240 * 1024
};

const productVideo = {
  uri: "file:///tmp/demo.mp4",
  name: "demo.mp4",
  mimeType: "video/mp4",
  mediaType: "video" as const,
  size: 120 * 1024 * 1024
};

beforeEach(() => {
  jest.clearAllMocks();
  mockManagerUpload.mockReturnValue({ promise: Promise.resolve({ ok: true, media_id: 7 }), cancel: jest.fn() });
});

describe("marketplace product media routing", () => {
  it("sends a product image through the direct-to-R2 manager", () => {
    uploadNativeMedia(productImage, { contextType: "marketplace_product", contextId: "draft" });
    expect(mockManagerUpload).toHaveBeenCalledTimes(1);
    expect(mockManagerUpload.mock.calls[0][1]).toMatchObject({ contextType: "marketplace_product" });
  });

  it("sends a product video through the same direct path", () => {
    uploadNativeMedia(productVideo, { contextType: "marketplace_product", contextId: "draft" });
    expect(mockManagerUpload).toHaveBeenCalledTimes(1);
  });

  it("carries the product context through so the server can scope the upload", () => {
    // The attach step refuses any upload whose session was not authorized as
    // `marketplace_product`, so losing this would break product media entirely
    // rather than fail open.
    uploadNativeMedia(productImage, { contextType: "marketplace_product", contextId: "draft" });
    expect(mockManagerUpload.mock.calls[0][1].contextType).toBe("marketplace_product");
  });

  it("still refuses an oversized product image before any network call", async () => {
    const oversized = { ...productImage, size: 9 * 1024 * 1024 };
    const task = uploadNativeMedia(oversized, { contextType: "marketplace_product" });
    await expect(task.promise).rejects.toThrow(/too large/i);
    expect(mockManagerUpload).not.toHaveBeenCalled();
  });
});
