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
  static urls: string[] = [];
  // URLs that should be rejected once with the given status before succeeding, so a
  // test can reproduce a signature that aged out mid-batch.
  static rejectOnce = new Map<string, number>();
  static DONE = 4;
  readyState = 0;
  status = 200;
  url = "";
  upload: { onprogress?: (e: { loaded: number }) => void } = {};
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open(_method: string, url: string) { this.url = url; }
  setRequestHeader() {}
  getResponseHeader(name: string) { return name.toLowerCase() === "etag" ? '"etag-123"' : null; }
  send(body: SendBody) {
    FakeXHR.bodies.push(body);
    FakeXHR.urls.push(this.url);
    const rejection = FakeXHR.rejectOnce.get(this.url);
    if (rejection) FakeXHR.rejectOnce.delete(this.url);
    setTimeout(() => {
      if (rejection) {
        this.status = rejection;
        this.readyState = FakeXHR.DONE;
        this.onload?.();
        return;
      }
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
    FakeXHR.urls = [];
    FakeXHR.rejectOnce = new Map();
    (global as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = FakeXHR;
    // A native-backed RN Blob descriptor — slice() returns a zero-copy view, never bytes.
    nativeBlob = {
      size: 2048,
      slice: jest.fn((start = 0, end = 2048, type = "") => ({ size: end - start, type, __view: true }))
    };
    (global as unknown as { fetch: unknown }).fetch = jest.fn(async () => ({ blob: async () => nativeBlob }));
  });

  function primePulseApi(strategy: "single" | "multipart", partSize: number, maxPartsPerRequest?: number) {
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
          status: "pending",
          ...(maxPartsPerRequest ? { max_parts_per_request: maxPartsPerRequest } : {})
        };
      }
      if (path.endsWith("/parts/sign")) {
        const body = JSON.parse((init as { body?: string })?.body || "{}");
        // Mirrors the server: anything past the cap is dropped without an error.
        const asked: number[] = body.part_numbers || [];
        const honoured = maxPartsPerRequest ? asked.slice(0, maxPartsPerRequest) : asked;
        return { parts: honoured.map((n: number) => ({ part_number: n, upload_url: `https://storage.example/part/${n}` })) };
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

  it("declares the clip's duration when opening the session, in milliseconds", async () => {
    // The server refuses an over-long video at session creation. If the client
    // never declares a duration there is nothing to refuse, and the uploader
    // discovers the 90-minute rule after uploading a two-hour video.
    const pulseApi = primePulseApi("single", 5 * 1024 * 1024);
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    await mediaUploadManager.upload({ ...asset, duration: 5_400_000 }, { contextType: "pulse_post" }).promise;

    const create = pulseApi.mock.calls.find(([path, init]) => path === "/api/pulse/media/uploads" && init?.method === "POST");
    expect(create).toBeDefined();
    const body = JSON.parse((create as [string, { body: string }])[1].body);
    expect(body.duration_ms).toBe(5_400_000);
    expect(body.context_type).toBe("pulse_post");
  });

  it("omits nothing when the picker reported no duration — it sends zero, not a guess", async () => {
    // Zero means "unmeasured" to the policy, which is not a violation. Inventing
    // a value here would be the client deciding a question the server owns.
    const pulseApi = primePulseApi("single", 5 * 1024 * 1024);
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    await mediaUploadManager.upload(asset, { contextType: "pulse_post" }).promise;

    const create = pulseApi.mock.calls.find(([path, init]) => path === "/api/pulse/media/uploads" && init?.method === "POST");
    const body = JSON.parse((create as [string, { body: string }])[1].body);
    expect(body.duration_ms).toBe(0);
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

  it("signs parts in one batch per round trip, not one request per part", async () => {
    // The cost this guards is sequential latency, not bandwidth: signing one part at a
    // time put a full round trip in front of every part. Asserting on the number of
    // sign calls is the only way to see it — the bytes transferred are identical either
    // way, so a timing or throughput assertion would pass on the slow version too.
    const pulseApi = primePulseApi("multipart", 256, 8); // 2048 bytes -> 8 parts, cap 8
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    await mediaUploadManager.upload({ ...asset, uri: "file:///tmp/batch.mp4" }, { contextType: "post" }).promise;

    const signCalls = pulseApi.mock.calls.filter(([path]) => String(path).endsWith("/parts/sign"));
    expect(signCalls).toHaveLength(1);
    expect(JSON.parse((signCalls[0] as [string, { body: string }])[1].body).part_numbers).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    // Every part still uploaded exactly once.
    expect(FakeXHR.bodies).toHaveLength(8);
  });

  it("never asks for more signatures than the server advertises", async () => {
    // `sign_parts` truncates an oversized batch silently. A client that asked for more
    // than the cap would upload only the parts it got back and then fail at `complete`
    // with a gap in the part list — far from the real cause.
    const pulseApi = primePulseApi("multipart", 256, 3); // 8 parts, cap 3
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    await mediaUploadManager.upload({ ...asset, uri: "file:///tmp/capped.mp4" }, { contextType: "post" }).promise;

    const signCalls = pulseApi.mock.calls.filter(([path]) => String(path).endsWith("/parts/sign"));
    for (const call of signCalls) {
      expect(JSON.parse((call as [string, { body: string }])[1].body).part_numbers.length).toBeLessThanOrEqual(3);
    }
    expect(FakeXHR.bodies).toHaveLength(8);
    const completed = pulseApi.mock.calls.find(([path]) => String(path).endsWith("/complete"));
    expect(JSON.parse((completed as [string, { body: string }])[1].body).parts.map((p: { part_number: number }) => p.part_number))
      .toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it("falls back to one part per request when the server advertises no cap", async () => {
    // An upload session persisted before the cap was published resumes without the
    // field. Guessing a batch size there could silently exceed an older server's limit.
    const pulseApi = primePulseApi("multipart", 512); // 4 parts, no advertised cap
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    await mediaUploadManager.upload({ ...asset, uri: "file:///tmp/nocap.mp4" }, { contextType: "post" }).promise;

    const signCalls = pulseApi.mock.calls.filter(([path]) => String(path).endsWith("/parts/sign"));
    expect(signCalls).toHaveLength(4);
    for (const call of signCalls) {
      expect(JSON.parse((call as [string, { body: string }])[1].body).part_numbers).toHaveLength(1);
    }
  });

  it("re-signs a part whose batched signature aged out mid-batch", async () => {
    // Batching widens the gap between minting a signature and using it, so the last part
    // of a batch can outlive its URL on a slow link. That arrives as 403, which
    // `transientStatus` deliberately does not retry — without an explicit re-sign the
    // whole upload would fail at the point batching made most likely.
    const pulseApi = primePulseApi("multipart", 256, 8);
    FakeXHR.rejectOnce.set("https://storage.example/part/8", 403);
    const { mediaUploadManager } = require("../MediaUploadManager") as typeof import("../MediaUploadManager");

    const result = await mediaUploadManager.upload({ ...asset, uri: "file:///tmp/expiring.mp4" }, { contextType: "post" }).promise;

    expect((result as { media_id?: string }).media_id).toBe("media_1");
    // The batch, plus a single-part re-sign for the one that expired.
    const signCalls = pulseApi.mock.calls.filter(([path]) => String(path).endsWith("/parts/sign"));
    expect(signCalls).toHaveLength(2);
    expect(JSON.parse((signCalls[1] as [string, { body: string }])[1].body).part_numbers).toEqual([8]);
    // Part 8 was attempted twice; every other part exactly once.
    expect(FakeXHR.urls.filter((url) => url === "https://storage.example/part/8")).toHaveLength(2);
    expect(FakeXHR.urls.filter((url) => url === "https://storage.example/part/7")).toHaveLength(1);
  });
});
