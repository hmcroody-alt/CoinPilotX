/**
 * Media telemetry — Stage 41 of the media reliability foundation.
 *
 * ## Why this is a recorder and not a network client
 *
 * The same reason `discovery/analytics.ts` is one: PulseSoc has no
 * general-purpose client event ingest, and inventing `/api/pulse/media/event`
 * here would produce a client that 404s in production while passing every test.
 * So this module owns what is real today — *what* a media event is — and emits
 * to a swappable sink. The default sink is dev-visible and production-silent;
 * `setMediaTelemetrySink` is the only change needed the day an ingest exists.
 *
 * ## The privacy constraint is in the type, not in a code review note
 *
 * Stage 41 forbids logging message contents, private media URLs, and file
 * contents. A comment saying so is worth nothing at 2am, so `MediaEvent` simply
 * has nowhere to put them: there is no `url` field, no `caption`, no `body`. The
 * only identifier is `key`, which is the cache key — an opaque digest for URL
 * -derived keys, and `id:<n>` for canonical media, neither of which is fetchable
 * by anyone reading a log.
 *
 * `bytes` and `durationMs` are aggregates and safe. `reason` is a short failure
 * code chosen by the caller from a closed vocabulary, not an error message —
 * error messages routinely embed the URL that failed, which is exactly the leak
 * this shape exists to prevent.
 */

export type MediaEventName =
  | "MEDIA_UPLOAD_STARTED"
  | "MEDIA_UPLOAD_SUCCEEDED"
  | "MEDIA_UPLOAD_FAILED"
  | "MEDIA_DOWNLOAD_STARTED"
  | "MEDIA_DOWNLOAD_SUCCEEDED"
  | "MEDIA_DOWNLOAD_FAILED"
  | "MEDIA_CACHE_HIT"
  | "MEDIA_CACHE_MISS"
  | "MEDIA_CACHE_EVICTED"
  | "MEDIA_RENDER_FAILED"
  | "MEDIA_SAVE_SUCCEEDED"
  | "MEDIA_SAVE_FAILED";

/**
 * A closed vocabulary. Callers pick a code; they do not pass `error.message`,
 * because thrown messages from `fetch` and `expo-file-system` embed the full
 * signed URL of whatever failed.
 */
export type MediaFailureReason =
  | "network"
  | "timeout"
  | "cancelled"
  | "not_found"
  | "forbidden"
  | "unsupported_type"
  | "too_large"
  | "corrupt"
  | "checksum_mismatch"
  | "no_disk_space"
  | "permission_denied"
  | "permission_limited"
  | "unavailable"
  | "unknown";

export type MediaEvent = {
  name: MediaEventName;
  /** Cache key — opaque. Never a URL. */
  key?: string;
  kind?: "image" | "video" | "audio" | "file";
  /** Which product surface produced it, for per-surface failure rates. */
  surface?: string;
  bytes?: number;
  durationMs?: number;
  attempt?: number;
  reason?: MediaFailureReason;
};

export type MediaTelemetrySink = (event: MediaEvent) => void;

const defaultSink: MediaTelemetrySink = (event) => {
  if (typeof __DEV__ !== "undefined" && __DEV__) {
    // eslint-disable-next-line no-console
    console.log("[media]", event.name, event.key ?? "", event.reason ?? "");
  }
};

let sink: MediaTelemetrySink = defaultSink;

export function setMediaTelemetrySink(next: MediaTelemetrySink | null) {
  sink = next ?? defaultSink;
}

export function trackMediaEvent(event: MediaEvent) {
  try {
    sink(event);
  } catch {
    // Telemetry must never be able to break a render, a save, or a download.
  }
}

/**
 * Maps a thrown value to a reason code without letting its message escape.
 *
 * Every branch here reads a *structural* property — a status code, an error
 * name, a `code` field — rather than pattern-matching the message string. That
 * is deliberate: message text is the one part of an error that carries the URL.
 */
export function mediaFailureReason(error: unknown): MediaFailureReason {
  if (!error || typeof error !== "object") return "unknown";
  const candidate = error as { name?: string; code?: string; status?: number; reason?: string };
  if (candidate.reason && isKnownReason(candidate.reason)) return candidate.reason;
  if (candidate.name === "AbortError") return "cancelled";
  const status = Number(candidate.status || 0);
  if (status === 404 || status === 410) return "not_found";
  if (status === 401 || status === 403) return "forbidden";
  if (status === 413) return "too_large";
  if (status >= 500) return "unavailable";
  if (candidate.code === "ENOSPC") return "no_disk_space";
  if (candidate.code === "ETIMEDOUT" || candidate.name === "TimeoutError") return "timeout";
  if (candidate.code === "ECONNREFUSED" || candidate.code === "ENOTFOUND" || candidate.code === "ERR_NETWORK") {
    return "network";
  }
  // React Native's `fetch` reports an unreachable host as a bare `TypeError`,
  // with the detail only in the message — which is the one field that carries
  // the signed URL, so it stays unread. The *name* is structural and is as far
  // as this goes. A real programming TypeError is misfiled as `network` by this
  // branch; the cost is one wrong word in a message, since both codes were
  // already retryable and neither changes control flow.
  if (candidate.name === "TypeError") return "network";
  return "unknown";
}

const KNOWN_REASONS = new Set<string>([
  "network",
  "timeout",
  "cancelled",
  "not_found",
  "forbidden",
  "unsupported_type",
  "too_large",
  "corrupt",
  "checksum_mismatch",
  "no_disk_space",
  "permission_denied",
  "permission_limited",
  "unavailable",
  "unknown"
]);

function isKnownReason(value: string): value is MediaFailureReason {
  return KNOWN_REASONS.has(value);
}
