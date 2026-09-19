import { File } from "expo-file-system";
import { PulseApiError } from "../api/pulseApi";

/**
 * The part of resumable upload that is the same on every surface.
 *
 * Extracted from MediaUploadManager so Messenger does not get a second copy of
 * it. What is shared here is transport only: how bytes leave the device and how
 * a dropped connection is retried. What is *not* shared is the session model --
 * posts/reels track an `upload_id` and send their completed-part list back at
 * complete time, while Messenger keys on the attachment and lets the server read
 * its own storage for the resume point. Forcing one session shape over both
 * would mean adopting the weaker of the two.
 */

export const MAX_RETRIES = 5;
export const PARALLEL_PARTS = 4;

export function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Normalize a local media URI for `fetch()` without double-encoding or stripping an
// existing scheme. AVFoundation/expo emit `file://…` already; a bare `/var/…` path gets a
// `file://` prefix. `content://`, `ph://`, `http(s)://` are passed through untouched.
export function toFetchableUri(uri: string) {
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(uri) || uri.startsWith("content://") || uri.startsWith("ph://")) return uri;
  return `file://${uri.startsWith("/") ? "" : "/"}${uri}`;
}

// Obtain a React Native native-backed Blob for the whole file. The bytes never enter the JS
// heap, but they are *entirely* resident in native memory: `fetch(file://…)` reaches
// RCTFileRequestHandler, which memory-maps the file (`NSDataReadingMappedIfSafe`) and hands
// it to RCTNetworkTask, which unconditionally does `[_data appendData:data]` into a fresh
// NSMutableData — copying every mapped page into dirty heap. So the peak cost of this call
// is the full file size, and RN has an explicit `@catch` there for "Request's received data
// too long."
//
// That is fine below MULTIPART_THRESHOLD and fatal above it: a 90-minute video is gigabytes,
// and iOS jetsams the app during this call, before a single byte has been uploaded. Use
// `openPartSource` for anything multipart.
export async function nativeBlobFromUri(uri: string): Promise<Blob> {
  const response = await fetch(toFetchableUri(uri));
  return response.blob();
}

export type PartSource = {
  read: (start: number, end: number) => Promise<Blob | Uint8Array>;
  close: () => void;
};

/**
 * A reader that materializes one part at a time instead of the whole file.
 *
 * expo-file-system's `FileHandle` seeks and reads a byte range natively, so peak memory is
 * one part (times the number of parts in flight) regardless of how long the video is. That
 * is the only thing standing between this engine and a multi-gigabyte upload.
 *
 * The returned `Uint8Array` is base64-encoded by RN's `convertRequestBody` before it reaches
 * the native networking layer, which costs ~1.33x the part size transiently and some CPU.
 * At an 8 MB part against an upload measured in seconds that is noise, and it buys a bound
 * that does not exist otherwise. The wire payload is unaffected — native decodes it back to
 * NSData before sending.
 *
 * Falls back to the whole-file blob when the URI is not a plain file (`ph://` asset
 * references, Android `content://`, remote URLs) because `FileHandle` cannot open those. The
 * fallback carries the memory cost described above, so callers should prefer a file URI.
 */
export async function openPartSource(uri: string, mimeType: string): Promise<PartSource> {
  const fetchable = toFetchableUri(uri);
  if (fetchable.startsWith("file://")) {
    try {
      const handle = new File(fetchable).open();
      return {
        read: async (start, end) => {
          handle.offset = start;
          return handle.readBytes(end - start);
        },
        close: () => {
          try {
            handle.close();
          } catch {
            // An already-closed handle is not an upload failure.
          }
        },
      };
    } catch {
      // Older runtime, unreadable path, or a URI the module declines. Fall through rather
      // than failing the upload outright — the blob path still works, just not for huge files.
    }
  }
  const blob = await nativeBlobFromUri(uri);
  return { read: async (start, end) => blob.slice(start, end, mimeType), close: () => {} };
}

export function transientStatus(status: number) {
  return status === 0 || status === 408 || status === 429 || status >= 500;
}

export async function withRetry<T>(
  operation: () => Promise<T>,
  onRetry: (attempt: number) => void,
  isCancelled: () => boolean
) {
  let lastError: unknown;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt += 1) {
    if (isCancelled()) throw new Error("Upload cancelled.");
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      const status = error instanceof PulseApiError ? error.status : Number((error as { status?: number })?.status || 0);
      if (attempt >= MAX_RETRIES || !transientStatus(status)) throw error;
      onRetry(attempt + 1);
      const jitter = Math.floor(Math.random() * 350);
      await sleep(Math.min(8000, 500 * (2 ** attempt)) + jitter);
    }
  }
  throw lastError;
}

export function uploadBlob(
  url: string,
  blob: Blob | Uint8Array,
  mimeType: string,
  onBytes: (loaded: number) => void,
  register: (xhr: XMLHttpRequest | null) => void
): Promise<{ etag: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); register(xhr);
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", mimeType);
    xhr.upload.onprogress = (event) => onBytes(event.loaded);
    xhr.onerror = () => reject(Object.assign(new Error("Upload transport was interrupted."), { status: 0, category: "network" }));
    xhr.onabort = () => reject(new Error("Upload cancelled."));
    xhr.onload = () => {
      register(null);
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(Object.assign(new Error(`Storage rejected upload (${xhr.status}).`), { status: xhr.status, category: "storage" }));
        return;
      }
      resolve({ etag: String(xhr.getResponseHeader("ETag") || xhr.getResponseHeader("etag") || "").replace(/^W\//, "") });
    };
    xhr.send(blob);
  });
}
