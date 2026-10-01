/**
 * Media download engine — Stages 6, 14, 29, 30, 36 and 38.
 *
 * ## The four failures this exists to make impossible
 *
 * **Duplicate downloads.** Ten chat bubbles showing the same forwarded image
 * mount at once and each asks for it. Without a shared in-flight registry that
 * is ten sockets, ten writes to one path, and a race over which one wins. Here,
 * the first caller creates the task and the other nine `await` the same promise
 * — one socket, one file, ten resolutions. This is also why `downloadMedia` is
 * safe to call from render: calling it again is free.
 *
 * **Phantom success.** Stage 38's requirement, and the reason the download does
 * not write to its final path. Bytes land in `<path>.part`; only a response that
 * actually completed with a 2xx gets moved into place by `commitCachedMedia`. An
 * app killed mid-transfer therefore leaves a `.part` file that no cache lookup
 * can ever resolve — a miss, which is honest — rather than a truncated file at
 * the real path that every surface would happily render as a black rectangle.
 *
 * **Unbounded work.** Concurrency is capped and the rest queue. A 500-message
 * thread scrolled quickly can ask for a hundred files in a second; a hundred
 * parallel transfers on cellular is slower than four in *wall-clock* terms, and
 * it is the shape that makes the UI thread starve and the app get killed for
 * memory. The cap is the Stage 11 "bound concurrent downloads" rule, enforced in
 * the one place downloads are created rather than trusted to each screen.
 *
 * **Retry storms and duplicate media.** Retries are bounded, backed off, and —
 * critically — only attempted for reasons that can plausibly succeed next time.
 * Retrying a 403 or an `unsupported_type` is not resilience, it is a loop. And
 * because a retry resumes the same task against the same cache key, retrying
 * cannot produce a second copy of the media (Stage 14).
 *
 * ## Resume across app launches
 *
 * `expo-file-system` hands back an opaque `resumeData` blob when a transfer is
 * paused. Persisting it under the cache key means a download interrupted by the
 * user backgrounding the app — or by iOS killing it outright — restarts from its
 * byte offset rather than from zero. That is Stage 37/38, and on a large video
 * over cellular it is the difference between a feature and a frustration.
 *
 * The blob is dropped whenever the transfer completes or fails permanently, so a
 * stale offset can never be applied to a file that has since changed.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  createDownloadResumable,
  deleteAsync,
  DownloadResumable,
  type DownloadPauseState,
  type DownloadProgressData
} from "expo-file-system/legacy";

import {
  cacheFileUriFor,
  commitCachedMedia,
  ensureRoomFor,
  lookupCachedMedia,
  MediaCacheFullError,
  mediaCacheKey,
  type MediaCacheEntry
} from "./mediaCache";
import { mediaFailureReason, trackMediaEvent, type MediaFailureReason } from "./mediaTelemetry";

export type MediaDownloadKind = "image" | "video" | "audio" | "file";

export type MediaDownloadProgress = {
  key: string;
  bytesWritten: number;
  /** -1 when the server sent no Content-Length. Render indeterminate, not 0%. */
  bytesExpected: number;
  /** 0..1, or null when the total is unknown — do not fake a number here. */
  fraction: number | null;
};

export type MediaDownloadRequest = {
  url: string;
  /** Canonical media id when the caller has one — produces a stabler cache key. */
  mediaId?: number | string | null;
  mimeType?: string;
  kind?: MediaDownloadKind;
  /** Product surface, for per-surface failure rates. Never a URL. */
  surface?: string;
  /** From the canonical record's `size_bytes`, used to reserve disk up front. */
  expectedBytes?: number;
  headers?: Record<string, string>;
  onProgress?: (progress: MediaDownloadProgress) => void;
};

export class MediaDownloadError extends Error {
  readonly reason: MediaFailureReason;
  constructor(reason: MediaFailureReason, message: string) {
    super(message);
    this.name = "MediaDownloadError";
    this.reason = reason;
  }
}

const RESUME_PREFIX = "pulsesoc.native.mediacache.resume.";
const MAX_CONCURRENT_DOWNLOADS = 3;
const MAX_ATTEMPTS = 3;
const BASE_BACKOFF_MS = 600;

/** Reasons where trying again is resilience rather than a loop. */
const RETRYABLE = new Set<MediaFailureReason>(["network", "timeout", "unavailable", "unknown"]);

type ActiveTask = {
  key: string;
  promise: Promise<MediaCacheEntry>;
  resumable: DownloadResumable | null;
  cancelled: boolean;
  listeners: Set<(progress: MediaDownloadProgress) => void>;
};

const active = new Map<string, ActiveTask>();
const queue: Array<() => void> = [];
let running = 0;

/**
 * Fetch media to disk, or return it if already cached.
 *
 * Resolves with a cache entry whose `fileUri` is a real, complete, verified
 * local file — the precondition every downstream action (save to Photos, share
 * as file, decode) needs and none of them should have to check for itself.
 */
export async function downloadMedia(request: MediaDownloadRequest): Promise<MediaCacheEntry> {
  const url = String(request.url || "").trim();
  if (!url) throw new MediaDownloadError("not_found", "No media URL to download.");

  const key = mediaCacheKey({ mediaId: request.mediaId, url });
  if (!key) throw new MediaDownloadError("not_found", "Media has no cacheable identity.");

  const cached = await lookupCachedMedia(key);
  if (cached) return cached;

  const existing = active.get(key);
  if (existing) {
    // Join the transfer already in flight rather than starting a second one.
    if (request.onProgress) existing.listeners.add(request.onProgress);
    return existing.promise;
  }

  // `promise` is filled in on the next line. It starts as a *pending* promise,
  // never a rejected one: a rejected placeholder is unhandled for the tick
  // before it is replaced, and Node 22 terminates the process for that.
  const task: ActiveTask = {
    key,
    promise: new Promise<MediaCacheEntry>(() => undefined),
    resumable: null,
    cancelled: false,
    listeners: new Set()
  };
  if (request.onProgress) task.listeners.add(request.onProgress);
  task.promise = runQueued(() => performDownload(task, key, url, request));
  // Joiners attach their own handlers later; mark the shared promise handled now
  // so a failure that nobody has joined yet is still not an unhandled rejection.
  task.promise.catch(() => undefined);
  active.set(key, task);

  try {
    return await task.promise;
  } finally {
    active.delete(key);
  }
}

/** Cancel an in-flight transfer. The partial file is removed; no entry is committed. */
export async function cancelMediaDownload(key: string): Promise<void> {
  const task = active.get(key);
  if (!task) return;
  task.cancelled = true;
  await task.resumable?.cancelAsync().catch(() => undefined);
  await AsyncStorage.removeItem(`${RESUME_PREFIX}${key}`).catch(() => undefined);
}

/**
 * Pause an in-flight transfer and persist its offset.
 *
 * Calling `downloadMedia` again later picks the offset back up. Used when the
 * app backgrounds mid-transfer (Stage 37).
 */
export async function pauseMediaDownload(key: string): Promise<boolean> {
  const task = active.get(key);
  if (!task?.resumable) return false;
  const state = await task.resumable.pauseAsync().catch(() => null);
  if (!state) return false;
  await persistResumeState(key, state);
  return true;
}

export function activeMediaDownloadCount(): number {
  return active.size;
}

/** Test-only: bounded-concurrency accounting, for asserting the cap holds. */
export function __mediaDownloaderState() {
  return { active: active.size, running, queued: queue.length };
}

async function runQueued<T>(work: () => Promise<T>): Promise<T> {
  if (running >= MAX_CONCURRENT_DOWNLOADS) {
    await new Promise<void>((resolve) => queue.push(resolve));
  }
  running += 1;
  try {
    return await work();
  } finally {
    running -= 1;
    const next = queue.shift();
    if (next) next();
  }
}

async function performDownload(
  task: ActiveTask,
  key: string,
  url: string,
  request: MediaDownloadRequest
): Promise<MediaCacheEntry> {
  const startedAt = Date.now();
  const kind = request.kind || "file";
  const destination = cacheFileUriFor(key, extensionFor(url, request.mimeType));
  const partial = `${destination}.part`;

  trackMediaEvent({ name: "MEDIA_DOWNLOAD_STARTED", key, kind, surface: request.surface });

  let lastReason: MediaFailureReason = "unknown";

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    if (task.cancelled) throw new MediaDownloadError("cancelled", "Download cancelled.");

    try {
      // Reserve disk before touching the network, so a full device is reported
      // as "not enough storage" rather than as a failed transfer.
      await ensureRoomFor(Number(request.expectedBytes) || 0);

      const resumeState = await readResumeState(key);
      const resumable = createDownloadResumable(
        url,
        partial,
        { headers: request.headers },
        (progress: DownloadProgressData) => emitProgress(task, key, progress),
        resumeState?.resumeData
      );
      task.resumable = resumable;

      const result = resumeState?.resumeData
        ? await resumable.resumeAsync()
        : await resumable.downloadAsync();

      if (task.cancelled) throw new MediaDownloadError("cancelled", "Download cancelled.");
      if (!result) throw new MediaDownloadError("cancelled", "Download cancelled.");

      const status = Number(result.status || 0);
      if (status >= 400) {
        // An error body was just written to `.part`. It is not media.
        await deleteAsync(partial, { idempotent: true }).catch(() => undefined);
        throw new MediaDownloadError(statusReason(status), `Media request failed (${status}).`);
      }

      const entry = await commitCachedMedia({
        key,
        fileUri: result.uri || partial,
        destinationUri: destination,
        mimeType: request.mimeType
      });

      if (!entry) {
        // Zero bytes, or the file vanished between download and commit. Stage 32.
        throw new MediaDownloadError("corrupt", "Downloaded media was empty or unreadable.");
      }

      await AsyncStorage.removeItem(`${RESUME_PREFIX}${key}`).catch(() => undefined);
      trackMediaEvent({
        name: "MEDIA_DOWNLOAD_SUCCEEDED",
        key,
        kind,
        surface: request.surface,
        bytes: entry.bytes,
        durationMs: Date.now() - startedAt,
        attempt
      });
      return entry;
    } catch (error) {
      lastReason = error instanceof MediaDownloadError
        ? error.reason
        : error instanceof MediaCacheFullError
          ? "no_disk_space"
          : mediaFailureReason(error);

      const canRetry = attempt < MAX_ATTEMPTS && RETRYABLE.has(lastReason) && !task.cancelled;
      if (!canRetry) break;

      // Keep the partial file: the point of backing off is to resume, not restart.
      await delay(BASE_BACKOFF_MS * 2 ** (attempt - 1));
    }
  }

  await deleteAsync(partial, { idempotent: true }).catch(() => undefined);
  await AsyncStorage.removeItem(`${RESUME_PREFIX}${key}`).catch(() => undefined);
  trackMediaEvent({
    name: "MEDIA_DOWNLOAD_FAILED",
    key,
    kind,
    surface: request.surface,
    durationMs: Date.now() - startedAt,
    reason: lastReason
  });
  throw new MediaDownloadError(lastReason, downloadMessageFor(lastReason));
}

function emitProgress(task: ActiveTask, key: string, progress: DownloadProgressData) {
  const bytesExpected = Number(progress.totalBytesExpectedToWrite || -1);
  const bytesWritten = Number(progress.totalBytesWritten || 0);
  const payload: MediaDownloadProgress = {
    key,
    bytesWritten,
    bytesExpected,
    fraction: bytesExpected > 0 ? Math.min(1, bytesWritten / bytesExpected) : null
  };
  for (const listener of task.listeners) {
    try {
      listener(payload);
    } catch {
      // One screen's progress handler must not abort the transfer for everyone
      // else awaiting the same file.
    }
  }
}

async function persistResumeState(key: string, state: DownloadPauseState) {
  if (!state?.resumeData) return;
  await AsyncStorage.setItem(`${RESUME_PREFIX}${key}`, JSON.stringify(state)).catch(() => undefined);
}

async function readResumeState(key: string): Promise<DownloadPauseState | null> {
  const raw = await AsyncStorage.getItem(`${RESUME_PREFIX}${key}`).catch(() => null);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as DownloadPauseState;
    return parsed?.resumeData ? parsed : null;
  } catch {
    return null;
  }
}

function statusReason(status: number): MediaFailureReason {
  if (status === 404 || status === 410) return "not_found";
  if (status === 401 || status === 403) return "forbidden";
  if (status === 413) return "too_large";
  if (status >= 500) return "unavailable";
  return "unknown";
}

/**
 * User-facing text, chosen from the reason code rather than from the thrown
 * message — so the string is translatable, actionable, and cannot leak a URL.
 */
export function downloadMessageFor(reason: MediaFailureReason): string {
  switch (reason) {
    case "no_disk_space":
      return "Not enough storage on this device. Free up space and try again.";
    case "not_found":
      return "This media is no longer available.";
    case "forbidden":
      return "You do not have access to this media.";
    case "too_large":
      return "This file is too large to download.";
    case "corrupt":
      return "This media file is damaged and cannot be opened.";
    case "cancelled":
      return "Download cancelled.";
    case "network":
    case "timeout":
    case "unavailable":
      return "PulseSoc could not reach this media. Check your connection and try again.";
    default:
      return "PulseSoc could not download this media.";
  }
}

function extensionFor(url: string, mimeType?: string): string {
  const fromMime = mimeType ? MIME_EXTENSIONS[mimeType.split(";")[0].trim().toLowerCase()] : undefined;
  if (fromMime) return fromMime;
  const path = url.split("#")[0].split("?")[0];
  const match = /\.([A-Za-z0-9]{1,5})$/.exec(path);
  return match ? `.${match[1].toLowerCase()}` : "";
}

const MIME_EXTENSIONS: Record<string, string> = {
  "image/jpeg": ".jpg",
  "image/jpg": ".jpg",
  "image/png": ".png",
  "image/gif": ".gif",
  "image/webp": ".webp",
  "image/heic": ".heic",
  "image/heif": ".heif",
  "video/mp4": ".mp4",
  "video/quicktime": ".mov",
  "video/webm": ".webm",
  "audio/mpeg": ".mp3",
  "audio/mp4": ".m4a",
  "audio/aac": ".aac",
  "audio/wav": ".wav",
  "audio/ogg": ".ogg",
  "application/pdf": ".pdf"
};

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
