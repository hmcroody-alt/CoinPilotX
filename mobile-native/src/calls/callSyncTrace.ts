/**
 * callSyncTrace — safe, greppable telemetry for call *signaling* state sync.
 *
 * The caller has exactly one way to learn that the callee accepted: the
 * authoritative status poll. It is not in the Agora channel while ringing, so
 * no RTC event can tell it. That makes the poll a single point of failure whose
 * every failure mode was previously silent — `pollNow()` swallows rejections
 * with `.catch(() => undefined)`, so a caller stuck on "ringing" produced no
 * evidence at all, on device or in CI.
 *
 * This module makes that path observable without widening any boundary. It is
 * NOT the real-time audio telemetry channel (`core/realtimeAudioTelemetry.ts`,
 * a protected path) and deliberately records nothing about audio, media tracks
 * or sessions — only signaling state.
 *
 * Privacy contract, enforced by `sanitize()` below and asserted by tests:
 * the only fields ever emitted are the call id, the local role, the previous and
 * next status, the source that produced the transition, and a numeric latency.
 * Tokens, credentials, participant identities, titles and any call content are
 * dropped rather than truncated — there is no code path that can log them.
 */

/** Where a status observation came from. Poll is authoritative; the rest are hints. */
export type CallSyncSource =
  | "start"
  | "accept"
  | "poll"
  | "poll_error"
  | "rtc_hint"
  | "terminal"
  | "app_foreground";

export type CallSyncEvent = {
  /** Backend public call id ("call_xxx"). Not a secret; it is in every URL. */
  callId: string;
  /** Local participant's role in this call. */
  role: "caller" | "callee" | "unknown";
  prevStatus: string;
  nextStatus: string;
  source: CallSyncSource;
  /** ms since the session opened, for measuring acceptance latency. */
  elapsedMs: number;
  /** Failure class for `poll_error` only (e.g. "http_500", "network"). Never a message body. */
  detail?: string;
};

export type CallSyncSink = (event: CallSyncEvent) => void;

const TAG = "PulseSocCallSync";
const RING_CAPACITY = 60;

const ring: CallSyncEvent[] = [];
let sink: CallSyncSink | null = null;
let consoleEnabled = true;

/** Only these keys can ever leave this module. */
function sanitize(event: CallSyncEvent): CallSyncEvent {
  const safe: CallSyncEvent = {
    callId: shortId(event.callId),
    role: event.role === "caller" || event.role === "callee" ? event.role : "unknown",
    prevStatus: statusOf(event.prevStatus),
    nextStatus: statusOf(event.nextStatus),
    source: event.source,
    elapsedMs: Number.isFinite(event.elapsedMs) ? Math.max(0, Math.round(event.elapsedMs)) : 0
  };
  if (event.detail) safe.detail = String(event.detail).slice(0, 32).replace(/[^a-z0-9_.:-]/gi, "");
  return safe;
}

/** Statuses are a closed vocabulary; anything unrecognized is reported as a shape, not a value. */
function statusOf(value: string | undefined): string {
  const raw = String(value || "").toLowerCase();
  if (!raw) return "";
  return /^[a-z_]{1,24}$/.test(raw) ? raw : "unrecognized";
}

function shortId(value: string | undefined): string {
  return String(value || "").slice(0, 24);
}

export function traceCallSync(event: CallSyncEvent) {
  const safe = sanitize(event);
  ring.push(safe);
  if (ring.length > RING_CAPACITY) ring.shift();
  if (consoleEnabled) {
    // eslint-disable-next-line no-console
    console.log(
      `[${TAG}] ${safe.role} ${safe.callId} ${safe.prevStatus || "-"}->${safe.nextStatus || "-"}` +
        ` via=${safe.source} t=${safe.elapsedMs}ms${safe.detail ? ` detail=${safe.detail}` : ""}`
    );
  }
  if (sink) {
    try {
      sink(safe);
    } catch {
      /* a broken sink must never break a call */
    }
  }
}

/** Recent events, oldest first. Used by tests and by the in-app call diagnostics. */
export function readCallSyncTrace(): CallSyncEvent[] {
  return [...ring];
}

export function setCallSyncSink(next: CallSyncSink | null) {
  sink = next;
}

export function __resetCallSyncTraceForTests(options: { console?: boolean } = {}) {
  ring.length = 0;
  sink = null;
  consoleEnabled = options.console !== false;
}
