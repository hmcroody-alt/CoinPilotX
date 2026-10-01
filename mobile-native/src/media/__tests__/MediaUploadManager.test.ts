import { mediaUploadParallelParts } from "../MediaUploadManager";

describe("MediaUploadManager policy", () => {
  it("uses bounded parallelism", () => {
    expect(mediaUploadParallelParts).toBeGreaterThanOrEqual(3);
    expect(mediaUploadParallelParts).toBeLessThanOrEqual(6);
  });
});

// Regression guard for the camera+music mixed-video publish blocker:
// "Creating blobs from 'ArrayBuffer' and 'ArrayBufferView' are not supported".
// The upload must stream a native-backed RN Blob obtained from fetch(fileUri).blob(),
// and must NEVER read the file into a JS ArrayBuffer/Uint8Array or call expo File.slice()
// (which internally does `new Blob([Uint8Array])` and throws on-device).
jest.mock("../../api/pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    constructor(message: string, status: number) { super(message); this.status = status; }
  }
  return { PulseApiError, pulseApi: jest.fn() };
});

jest.mock("expo-file-system", () => ({
  File: class {
    uri: string;
    exists = true;
    size = 2048;
    constructor(uri: string) { this.uri = uri; }
    // If the transport ever falls back to expo's File.slice, fail loudly — that path is
    // exactly what produced the ArrayBuffer/Blob error on device.
    slice() { throw new Error("expo File.slice must not be used for upload transport"); }
  }
}));

type SendBody = unknown;

class FakeXHR {
  static bodies: SendBody[] = [];
  static DONE = 4;
  readyState = 0;
  status = 200;
  upload: { onprogress?: (e: { loaded: number }) => void } = {};
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open() {}
  setRequestHeader() {}
  getResponseHeader(name: string) { return name.toLowerCase() === "etag" ? '"etag-123"' : null; }
  send(body: SendBody) {
    FakeXHR.bodies.push(body);
    setTimeout(() => {
      this.upload.onprogress?.({ loaded: 2048 });
      this.readyState = FakeXHR.DONE;
      this.onload?.();
    }, 0);
  }
  abort() { this.onabort?.(); }
}

describe("MediaUploadManager native-file transport", () => {
  const asset = {
    uri: "/var/mobile/Containers/Data/pulsesoc-video-mix-TEST.mp4",
    name: "pulsesoc-video-mix-TEST.mp4",
    mimeType: "video/mp4",
    mediaType: "video" as const,
    size: 2048
  };

  let nativeBlob: { size: number; slice: jest.Mock };

  beforeEach(() => {
    jest.resetModules();
    FakeXHR.bodies = [];
    (global as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = FakeXHR;
    // A native-backed RN Blob descriptor — slice() returns a zero-copy view, never bytes.
    nativeBlob = {
      size: 2048,
      slice: jest.fn((start = 0, end = 2048, type = "") => ({ size: end - start, type, __view: true }))
    };
    (global as unknown as { fetch: unknown }).fetch = jest.fn(async () => ({ blob: async () => nativeBlob }));
  });

  function primePulseApi(strategy: "single" | "multipart", partSize: number) {
    const { pulseApi } = require("../../api/pulseApi") as { pulseApi: jest.Mock };
    pulseApi.mockImplementation(async (path: string, init?: { method?: string }) => {
      const method = init?.method || "GET";
      if (path === "/api/pulse/media/uploads" && method === "POST") {
        return {
          ok: true,
          upload_id: "up_1",
          object_key: "obj/1",
          strategy,
          upload_url: strategy === "single" ? "https://storage.example/put" : undefined,
          part_size_bytes: partSize,
          file_size_bytes: 2048,
          completed_parts: [],
          status: "pending"
        };
      }
      if (path.endsWith("/parts/sign")) {
        const body = JSON.parse((init as { body?: string })?.body || "{}");
        return { parts: (body.part_numbers || []).map((n: number) => ({ part_number: n, upload_url: `https://storage.example/part/${n}` })) };
      }
      if (path.endsWith("/finalize")) return { ok: true, media_id: "media_1", media: { id: "media_1" } };
      return { ok: true };
    });
    return pulseApi;
  }

  it("streams a native RN blob from the file URI (single) — no ArrayBuffer, no expo slice", async () => {
    primePulseApi("single", 5 * 1024 * 1024);
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    const task = mediaUploadManager.upload(asset, { contextType: "reel" });
    const result = await task.promise;

    expect((result as { media_id?: string }).media_id).toBe("media_1");
    // Normalizes the bare path to a fetchable file:// URI without double-encoding.
    expect((global.fetch as jest.Mock)).toHaveBeenCalledWith("file:///var/mobile/Containers/Data/pulsesoc-video-mix-TEST.mp4");
    // The exact object sent to the network is the native RN blob — not the expo File,
    // not an ArrayBuffer, not an ArrayBufferView.
    expect(FakeXHR.bodies).toHaveLength(1);
    const sent = FakeXHR.bodies[0];
    expect(sent).toBe(nativeBlob);
    expect(sent instanceof ArrayBuffer).toBe(false);
    expect(ArrayBuffer.isView(sent as ArrayBufferView)).toBe(false);
  });

  it("slices the native RN blob for multipart parts (zero-copy views)", async () => {
    primePulseApi("multipart", 1024); // 2048 bytes -> 2 parts
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    const task = mediaUploadManager.upload({ ...asset, uri: "file:///tmp/pulsesoc-video-mix-M.mp4" }, { contextType: "post" });
    await task.promise;

    // Parts come from the RN blob's slice (a view), never from expo File.slice.
    expect(nativeBlob.slice).toHaveBeenCalledTimes(2);
    expect(FakeXHR.bodies).toHaveLength(2);
    for (const sent of FakeXHR.bodies) {
      expect(sent instanceof ArrayBuffer).toBe(false);
      expect(ArrayBuffer.isView(sent as ArrayBufferView)).toBe(false);
    }
    // Already-scheme'd URI is passed through untouched (no double file:// prefix).
    expect((global.fetch as jest.Mock)).toHaveBeenCalledWith("file:///tmp/pulsesoc-video-mix-M.mp4");
  });
});
