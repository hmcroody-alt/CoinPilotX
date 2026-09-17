/**
 * Privacy-safe translation telemetry (Stage 12).
 *
 * What is recorded is a *shape*: which provider answered, how long it took, how
 * many characters were involved, which pair was asked for. What is never
 * recorded is text — not the source, not the translation, not a prefix, not a
 * digest of a private message. `characters` is a length, and a length is the
 * only property of private text this module is allowed to know, because the
 * whole point of moving to on-device translation is that the text stays on the
 * device including in its own observability.
 *
 * The pair *is* recorded, for private content as well, because the reporting
 * Stage 12 asks for — which pairs are unsupported, where cloud spend goes — is
 * unanswerable without it, and a language is not content.
 *
 * Nothing here talks to the network. It accumulates counters and a bounded ring
 * of recent events for the diagnostics surface; shipping any of it anywhere is a
 * separate decision that has not been taken.
 */

import type { TranslatableContentType } from "../../api/translation";
import type {
  CloudExclusion,
  TranslationFailureCode,
  TranslationPrivacy,
  TranslationProviderId
} from "./types";

export type TranslationMetricEvent = {
  provider: TranslationProviderId;
  ok: boolean;
  code?: TranslationFailureCode;
  /**
   * Why the billable provider was withheld, when it was. Counted separately
   * from `code` because the cost question ("what is the budget stopping?") and
   * the user question ("why is there no translation?") have different answers
   * for the same event.
   */
  cloudExclusion?: CloudExclusion;
  privacy: TranslationPrivacy;
  contentType: TranslatableContentType;
  sourceLanguage: string | null;
  targetLanguage: string;
  /** Length of the source text. Never the text. */
  characters: number;
  durationMs: number;
  userInitiated: boolean;
  downloadPrepared: boolean;
};

const MAX_RECENT_EVENTS = 200;

const counters = {
  total: 0,
  cache: 0,
  appleOnDevice: 0,
  cloud: 0,
  failures: 0,
  /** Characters that did not reach a billable provider because Apple or the cache answered. */
  charactersAvoided: 0,
  /** Characters actually sent to the billable provider. */
  charactersBilled: 0
};

const failureCodeCounts = new Map<TranslationFailureCode, number>();
const cloudExclusionCounts = new Map<CloudExclusion, number>();
const unsupportedPairs = new Map<string, number>();
const latencies: number[] = [];
const recent: TranslationMetricEvent[] = [];

export function recordTranslationEvent(event: TranslationMetricEvent) {
  counters.total += 1;
  if (event.ok) {
    if (event.provider === "cache") {
      counters.cache += 1;
      counters.charactersAvoided += event.characters;
    } else if (event.provider === "apple_on_device") {
      counters.appleOnDevice += 1;
      counters.charactersAvoided += event.characters;
      pushLatency(event.durationMs);
    } else if (event.provider === "cloud_fallback") {
      counters.cloud += 1;
      counters.charactersBilled += event.characters;
      pushLatency(event.durationMs);
    }
  } else {
    counters.failures += 1;
    if (event.cloudExclusion) {
      cloudExclusionCounts.set(
        event.cloudExclusion,
        (cloudExclusionCounts.get(event.cloudExclusion) ?? 0) + 1
      );
    }
    if (event.code) {
      failureCodeCounts.set(event.code, (failureCodeCounts.get(event.code) ?? 0) + 1);
      if (event.code === "unsupported_language_pair") {
        const pair = `${event.sourceLanguage || "auto"}->${event.targetLanguage}`;
        unsupportedPairs.set(pair, (unsupportedPairs.get(pair) ?? 0) + 1);
      }
    }
  }

  recent.push(event);
  if (recent.length > MAX_RECENT_EVENTS) recent.shift();
}

function pushLatency(durationMs: number) {
  if (!Number.isFinite(durationMs) || durationMs <= 0) return;
  latencies.push(durationMs);
  if (latencies.length > MAX_RECENT_EVENTS) latencies.shift();
}

function percentile(sorted: number[], fraction: number) {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.floor(fraction * sorted.length));
  return sorted[index];
}

export function translationMetricsSnapshot() {
  const sorted = [...latencies].sort((left, right) => left - right);
  const answered = counters.cache + counters.appleOnDevice + counters.cloud;
  const share = (value: number) => (answered === 0 ? 0 : value / answered);
  return {
    total: counters.total,
    answered,
    onDeviceShare: share(counters.appleOnDevice),
    cachedShare: share(counters.cache),
    cloudShare: share(counters.cloud),
    failureRate: counters.total === 0 ? 0 : counters.failures / counters.total,
    charactersAvoided: counters.charactersAvoided,
    charactersBilled: counters.charactersBilled,
    medianLatencyMs: percentile(sorted, 0.5),
    p95LatencyMs: percentile(sorted, 0.95),
    failureCodes: Object.fromEntries(failureCodeCounts),
    cloudExclusions: Object.fromEntries(cloudExclusionCounts),
    unsupportedPairs: Object.fromEntries(unsupportedPairs)
  };
}

export function recentTranslationEvents() {
  return [...recent];
}

export function resetTranslationMetrics() {
  counters.total = 0;
  counters.cache = 0;
  counters.appleOnDevice = 0;
  counters.cloud = 0;
  counters.failures = 0;
  counters.charactersAvoided = 0;
  counters.charactersBilled = 0;
  failureCodeCounts.clear();
  cloudExclusionCounts.clear();
  unsupportedPairs.clear();
  latencies.length = 0;
  recent.length = 0;
}
