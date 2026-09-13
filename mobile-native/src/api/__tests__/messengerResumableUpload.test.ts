/**
 * A 90-minute video has to leave the phone in pieces.
 *
 * The transport rules these tests hold down, in the order they matter:
 *
 *  1. The server picks the transport. The client does not guess from the size,
 *     because the threshold would then exist in two places.
 *  2. The resume point is asked of the server. Nothing here tells the server
 *     which parts landed -- it reads its own storage.
 *  3. The bytes never enter JS. The blob is a native descriptor and each part is
 *     a zero-copy slice of it. Reading parts into buffers works on a short clip
 *     and runs a phone out of memory on a long one.
 *  4. A dropped part is re-sent on its own. Restarting the transfer is what makes
 *     a long upload impossible on a real network.
 */

jest.mock("../pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    code: string;
    constructor(message: string, status = 500, code = "") {
      super(message);
      this.status = status;
      this.code = code;
    }
  }
  return { PulseApiError, pulseApi: jest.fn() };
});

jest.mock("expo-file-system", () => ({
  File: class {
    uri: string;
    exists = true;
    size = 0;
    constructor(uri: string) {
      this.uri = uri;
    }
    slice() {
      throw new Error("expo File.slice must not be used for upload transport");
    }
  }
}));

const MB = 1024 * 1024;
const PART_SIZE = 8 * MB;

type Sent = { url: string; size: number; type: string };

class FakeXHR {
  static sent: Sent[] = [];
  static failOnce = new Set<string>();
  static DONE = 4;
  readyState = 0;
  status = 200;
  upload: { onprogress?: (event: { loaded: number }) => void } = {};
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private url = "";

  open(_method: string, url: string) {
    this.url = url;
  }
  setRequestHeader() {}
  getResponseHeader(name: string) {
    return name.toLowerCase() === "etag" ? '"etag-1"' : null;
  }
  send(body: unknown) {
    const slice = body as { size?: number; type?: string };
    setTimeout(() => {
      if (FakeXHR.failOnce.has(this.url)) {
        FakeXHR.failOnce.delete(this.url);
        this.status = 503;
        this.readyState = FakeXHR.DONE;
        this.onload?.();
        return;
      }
      FakeXHR.sent.push({ url: this.url, size: Number(slice?.size || 0), type: String(slice?.type || "") });
      this.upload.onprogress?.({ loaded: Number(slice?.size || 0) });
      this.readyState = FakeXHR.DONE;
      this.onload?.();
    }, 0);
  }
  abort() {
    this.onabort?.();
  }
}

type Call = { path: string; body: Record<string, unknown> };

describe("Messenger resumable upload transport", () => {
  let calls: Call[];
  let blob: { size: number; slice: jest.Mock };

  function record(path: string, init?: { method?: string; body?: unknown }): Call {
    let body: Record<string, unknown> = {};
    const raw = init?.body;
    if (typeof raw === "string") {
      try {
        body = JSON.parse(raw);
      } catch {
        body = {};
      }
    } else if (raw) {
      body = { form: raw };
    }
    const call = { path, body };
    calls.push(call);
    return call;
  }

  function primeServer(options: {
    sizeBytes: number;
    method?: "direct" | "resumable";
    missingParts?: number[];
    bytesStored?: number;
    maxPartsPerRequest?: number;
  }) {
    const { pulseApi } = require("../pulseApi") as { pulseApi: jest.Mock };
    const method = options.method || "resumable";
    const partCount = Math.max(1, Math.ceil(options.sizeBytes / PART_SIZE));
    pulseApi.mockImplementation(async (path: string, init?: { method?: string; body?: unknown }) => {
      const call = record(path, init);
      if (path === "/api/messages/media/init") {
        if (method === "direct") {
          return { ok: true, attachment_id: 55, upload_method: "direct", upload_url: "/api/messages/media/upload" };
        }
        return {
          ok: true,
          attachment_id: 55,
          upload_method: "resumable",
          upload_url: "/api/messages/media/upload/parts",
          part_size_bytes: PART_SIZE,
          part_count: partCount,
          max_parts_per_request: options.maxPartsPerRequest || 12,
          session_expires_at: "2099-01-01T00:00:00Z"
        };
      }
      if (path === "/api/messages/media/upload/state") {
        return {
          ok: true,
          missing_parts: options.missingParts ?? Array.from({ length: partCount }, (_, index) => index + 1),
          bytes_stored: options.bytesStored || 0,
          size_bytes: options.sizeBytes,
          part_size_bytes: PART_SIZE,
          part_count: partCount
        };
      }
      if (path === "/api/messages/media/upload/parts") {
        const numbers = (call.body.part_numbers as number[]) || [];
        return {
          ok: true,
          parts: numbers.map((number) => ({ part_number: number, upload_url: `https://storage.example/part/${number}` }))
        };
      }
      if (path === "/api/messages/media/upload/finish") {
        return { ok: true, media_id: 901, size_bytes: options.sizeBytes, download_url: "/api/messages/media/55/download" };
      }
      if (path === "/api/messages/media/upload" || path === "/api/messages/media/complete") {
        return { ok: true, media_id: 902, size_bytes: options.sizeBytes };
      }
      return { ok: true };
    });
    return pulseApi;
  }

  function send(sizeBytes: number, extra: Record<string, unknown> = {}) {
    const messenger = require("../messenger") as typeof import("../messenger");
    return messenger.uploadMessengerMedia({
      conversationId: 44,
      uri: "/var/mobile/Containers/Data/clip-90m.mp4",
      name: "clip-90m.mp4",
      mimeType: "video/mp4",
      sizeBytes,
      ...extra
    });
  }

  const pathsOf = () => calls.map((call) => call.path);
  const partsSigned = () =>
    calls.filter((call) => call.path === "/api/messages/media/upload/parts").map((call) => call.body.part_numbers as number[]);

  beforeEach(() => {
    jest.resetModules();
    calls = [];
    FakeXHR.sent = [];
    FakeXHR.failOnce = new Set();
    (global as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = FakeXHR;
    blob = {
      size: 0,
      slice: jest.fn((start = 0, end = 0, type = "") => ({ size: end - start, type, __view: true, start, end }))
    };
    (global as unknown as { fetch: unknown }).fetch = jest.fn(async () => ({ blob: async () => blob }));
  });

  it("keeps a small attachment on the single-request path", async () => {
    primeServer({ sizeBytes: 2 * MB, method: "direct" });
    await send(2 * MB);
    expect(pathsOf()).toContain("/api/messages/media/upload");
    expect(pathsOf()).not.toContain("/api/messages/media/upload/parts");
    expect(FakeXHR.sent).toEqual([]);
  });

  it("sends a long video as parts and never through the single-request route", async () => {
    // Deliberately not a whole number of parts. An earlier version used 40 MB --
    // exactly five 8 MB parts -- so the short-tail assertion below was comparing
    // PART_SIZE against itself and could not fail. A size that divides evenly
    // tests the arithmetic it looks like it is testing and nothing more.
    const size = 43 * MB;
    const tail = size - 5 * PART_SIZE;
    expect(tail).toBeGreaterThan(0);
    expect(tail).toBeLessThan(PART_SIZE);
    primeServer({ sizeBytes: size });
    const result = await send(size);
    expect(result.media_id).toBe(901);
    expect(pathsOf()).not.toContain("/api/messages/media/upload");
    expect(pathsOf()).toContain("/api/messages/media/upload/finish");
    // Six parts: five full and a short tail. Slicing a full width past the end
    // pads the object with zeros, and the byte total then disagrees with the
    // declared size -- so the server refuses an upload that was complete.
    expect(FakeXHR.sent.map((item) => item.size).sort((a, b) => b - a)).toEqual([
      PART_SIZE,
      PART_SIZE,
      PART_SIZE,
      PART_SIZE,
      PART_SIZE,
      tail
    ]);
    expect(FakeXHR.sent.reduce((total, item) => total + item.size, 0)).toBe(size);
  });

  it("slices the native blob rather than reading bytes into JS", async () => {
    const size = 20 * MB;
    primeServer({ sizeBytes: size });
    await send(size);
    expect(global.fetch).toHaveBeenCalledWith("file:///var/mobile/Containers/Data/clip-90m.mp4");
    expect(blob.slice).toHaveBeenCalledWith(0, PART_SIZE, "video/mp4");
    expect(blob.slice).toHaveBeenCalledWith(PART_SIZE, 2 * PART_SIZE, "video/mp4");
    // The tail stops at the end of the file, not at the end of a part.
    expect(blob.slice).toHaveBeenCalledWith(2 * PART_SIZE, size, "video/mp4");
    expect(blob.slice).not.toHaveBeenCalledWith(2 * PART_SIZE, 3 * PART_SIZE, "video/mp4");
    // Every body sent is a view produced by slice(), never a buffer.
    expect(FakeXHR.sent.every((item) => item.type === "video/mp4")).toBe(true);
  });

  it("resumes from what the server says is stored, not from part one", async () => {
    const size = 40 * MB;
    primeServer({ sizeBytes: size, missingParts: [4, 5], bytesStored: 3 * PART_SIZE });
    await send(size);
    expect(partsSigned().flat()).toEqual([4, 5]);
    expect(FakeXHR.sent.map((item) => item.url)).toEqual([
      "https://storage.example/part/4",
      "https://storage.example/part/5"
    ]);
    // Asked before anything was sent: the gap has to be known first.
    expect(pathsOf().indexOf("/api/messages/media/upload/state")).toBeLessThan(
      pathsOf().indexOf("/api/messages/media/upload/parts")
    );
  });

  it("does not tell the server which parts it thinks landed", async () => {
    const size = 40 * MB;
    primeServer({ sizeBytes: size, missingParts: [2, 3] });
    await send(size);
    const claims = calls.filter((call) =>
      ["parts", "completed_parts", "uploaded_parts", "etags"].some((field) => field in call.body)
    );
    expect(claims).toEqual([]);
  });

  it("batches signature requests instead of one round trip per part", async () => {
    const size = 40 * MB;
    primeServer({ sizeBytes: size, maxPartsPerRequest: 3 });
    await send(size);
    const batches = partsSigned();
    expect(batches.every((batch) => batch.length <= 3)).toBe(true);
    expect(batches.flat().sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5]);
    expect(batches.length).toBeLessThan(5);
  });

  it("re-sends only the part that dropped", async () => {
    const size = 24 * MB;
    primeServer({ sizeBytes: size });
    FakeXHR.failOnce.add("https://storage.example/part/2");
    await send(size);
    const urls = FakeXHR.sent.map((item) => item.url).sort();
    expect(urls).toEqual([
      "https://storage.example/part/1",
      "https://storage.example/part/2",
      "https://storage.example/part/3"
    ]);
    expect(FakeXHR.sent.reduce((total, item) => total + item.size, 0)).toBe(size);
  }, 20000);

  it("forwards the duration so the server can refuse an over-long video", async () => {
    const size = 24 * MB;
    primeServer({ sizeBytes: size });
    await send(size, { durationSeconds: 5400 });
    const finish = calls.find((call) => call.path === "/api/messages/media/upload/finish");
    expect(finish?.body.duration_ms).toBe(5400 * 1000);
  });

  it("reports progress against the real total, including bytes already stored", async () => {
    const size = 40 * MB;
    primeServer({ sizeBytes: size, missingParts: [4, 5], bytesStored: 3 * PART_SIZE });
    const seen: number[] = [];
    await send(size, { onProgress: (value: { percent: number }) => seen.push(value.percent) });
    expect(seen.length).toBeGreaterThan(0);
    expect(Math.min(...seen)).toBeGreaterThanOrEqual(60);
    expect(Math.max(...seen)).toBeLessThanOrEqual(99);
  });
});
