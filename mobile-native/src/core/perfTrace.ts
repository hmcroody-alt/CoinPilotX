import { Platform } from "react-native";

/**
 * Lightweight, production-safe performance tracing.
 *
 * Design goals (see PulseSOC performance mission):
 * - Effectively zero-cost when disabled: every public entry point short-circuits
 *   before doing work, and spans returned while disabled are shared no-ops.
 * - Never leaks private content: attribute keys that commonly carry user data are
 *   dropped, string values are truncated, and there is no default network sink.
 * - Never breaks the app: a throwing sink is swallowed.
 *
 * It captures client-observed durations only. Server-side processing time must be
 * measured separately (e.g. via server timing headers) and is intentionally not
 * inferred here.
 */

export type PerfAttributeValue = string | number | boolean | undefined;
export type PerfAttributes = Record<string, PerfAttributeValue>;

export interface PerfSample {
  /** Stable metric name, e.g. "screen.firstRender", "api.request". */
  name: string;
  /** Client-observed duration in milliseconds, rounded to 2 dp. */
  durationMs: number;
  /** Wall-clock epoch ms when the span started (for correlation, not math). */
  startedAt: number;
  /** Context + per-sample attributes, already sanitized. */
  attributes: PerfAttributes;
}

export type PerfSink = (sample: PerfSample) => void;

/** Monotonic clock in ms; falls back to Date.now where performance is unavailable. */
export function perfNow(): number {
  const perf = (globalThis as { performance?: { now?: () => number } }).performance;
  return typeof perf?.now === "function" ? perf.now() : Date.now();
}

const RING_CAPACITY = 200;
const ring: PerfSample[] = [];
let sink: PerfSink | null = null;
// __DEV__ is injected by the RN/Expo runtime; treat missing as false.
const IS_DEV = typeof __DEV__ !== "undefined" && __DEV__;
let enabled = IS_DEV;
let context: PerfAttributes = { platform: Platform.OS };

/**
 * Attribute keys we refuse to record so private content never lands in a trace.
 * Matching is case-insensitive and substring-based.
 */
const BLOCKED_KEY_FRAGMENTS = [
  "token",
  "password",
  "secret",
  "cookie",
  "authorization",
  "email",
  "phone",
  "body",
  "content",
  "message",
  "caption",
  "query",
  "text",
  "name"
];

function isBlockedKey(key: string): boolean {
  const lower = key.toLowerCase();
  return BLOCKED_KEY_FRAGMENTS.some((fragment) => lower.includes(fragment));
}

function sanitize(attributes?: PerfAttributes): PerfAttributes {
  const out: PerfAttributes = {};
  if (!attributes) return out;
  for (const [key, value] of Object.entries(attributes)) {
    if (value === undefined) continue;
    if (isBlockedKey(key)) continue;
    out[key] = typeof value === "string" ? value.slice(0, 120) : value;
  }
  return out;
}

/** Merge stable context tags (app version, build, device class, network) applied to every sample. */
export function setPerfContext(next: PerfAttributes): void {
  context = { ...context, ...sanitize(next) };
}

export function getPerfContext(): PerfAttributes {
  return { ...context };
}

/**
 * Enable/disable tracing and register (or clear) a telemetry sink. Registering a
 * sink is what opts production into emitting samples; without one, samples stay
 * in the local ring buffer for on-device inspection only.
 */
export function configurePerfTracing(options: { enabled?: boolean; sink?: PerfSink | null }): void {
  if (typeof options.enabled === "boolean") enabled = options.enabled;
  if (options.sink !== undefined) sink = options.sink;
}

export function isPerfTracingEnabled(): boolean {
  return enabled || sink !== null;
}

function record(name: string, durationMs: number, startedAtWall: number, attributes?: PerfAttributes): void {
  if (!isPerfTracingEnabled()) return;
  const sample: PerfSample = {
    name,
    durationMs: Math.round(durationMs * 100) / 100,
    startedAt: startedAtWall,
    attributes: { ...context, ...sanitize(attributes) }
  };
  ring.push(sample);
  if (ring.length > RING_CAPACITY) ring.shift();
  if (IS_DEV) {
    console.log(`⏱ perf ${name} ${sample.durationMs}ms`, sample.attributes);
  }
  try {
    sink?.(sample);
  } catch {
    // Telemetry must never break the app.
  }
}

export interface PerfSpan {
  /** Close the span, recording the elapsed time plus any extra attributes. Idempotent. */
  end(attributes?: PerfAttributes): number;
}

const NOOP_SPAN: PerfSpan = { end: () => 0 };

/** Open a timed span. Returns a shared no-op when tracing is disabled. */
export function startSpan(name: string, attributes?: PerfAttributes): PerfSpan {
  if (!isPerfTracingEnabled()) return NOOP_SPAN;
  const startPerf = perfNow();
  const startWall = Date.now();
  let ended = false;
  return {
    end(extra?: PerfAttributes): number {
      if (ended) return 0;
      ended = true;
      const duration = perfNow() - startPerf;
      record(name, duration, startWall, { ...attributes, ...extra });
      return duration;
    }
  };
}

/** Record a duration you measured yourself (e.g. via perfNow deltas). */
export function recordDuration(name: string, durationMs: number, attributes?: PerfAttributes): void {
  record(name, durationMs, Date.now(), attributes);
}

/** Snapshot of recent samples for an on-device debug overlay or manual export. */
export function getPerfSamples(): PerfSample[] {
  return ring.slice();
}

export function clearPerfSamples(): void {
  ring.length = 0;
}
