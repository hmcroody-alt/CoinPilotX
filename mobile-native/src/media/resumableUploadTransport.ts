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

// Obtain a React Native native-backed Blob that streams from the filesystem. The blob is a
// descriptor (blobId + offset + size) — the bytes stay in native memory and never enter JS,
// so there is no ArrayBuffer/Uint8Array round-trip. `blob.slice()` returns a zero-copy view
// over the same native data, which is what makes multipart part uploads memory-safe.
export async function nativeBlobFromUri(uri: string): Promise<Blob> {
  const response = await fetch(toFetchableUri(uri));
  return response.blob();
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
  blob: Blob,
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
