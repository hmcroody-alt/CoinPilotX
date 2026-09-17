/**
 * `translateText` — the one function a screen calls to get text translated.
 *
 * ## The order, and why it is fixed
 *
 * 1. a valid device cache entry
 * 2. Apple's on-device engine
 * 3. the billable cloud provider, but only if policy permits it
 * 4. a typed unavailable result
 *
 * Nothing before step 3 costs money or leaves the phone, so the order is also
 * the cost order and the privacy order. It is fixed rather than configurable
 * because the only reason to reorder it would be to prefer the expensive
 * provider, which is the defect this migration exists to remove.
 *
 * ## Falling through is not automatic
 *
 * Apple failing does *not* mean the cloud is tried. The native side reports
 * `permitsCloudFallback` per failure and it is false for cancellation, for
 * same-language and for invalid input — because a user scrolling a translated
 * post out of view must never turn into a charge, and asking Google to
 * translate French into French would be a charge for nothing. The router
 * forwards that decision; it does not second-guess it.
 *
 * ## Deduplication
 *
 * Two cells showing the same text in the same pair share one in-flight promise,
 * keyed by the cache key. Stage 7 requires this for a feed: without it, ten
 * visible copies of the same caption are ten inferences, and on the cloud path
 * ten charges.
 */

import {
  clearTranslationCache,
  currentCacheScope,
  hydrateTranslationCache,
  readTranslationCache,
  translationCacheKey,
  writeTranslationCache
} from "./cache";
import {
  cloudRequestAllowed,
  hydrateCloudBudget,
  recordCloudFailure,
  recordCloudSpend,
  recordCloudSuccess
} from "./cloudGuard";
import { isSameLanguage, normalizeLanguage } from "./languages";
import { recordTranslationEvent } from "./metrics";
import { codeForExclusion, planProviders, readTranslationFlags } from "./policy";
import {
  appleCancelContent,
  appleCancelRequests,
  appleIsUsable,
  appleProviderVersion,
  appleTranslate
} from "./providers/apple";
import { cloudProviderVersion, cloudTranslate } from "./providers/cloud";
import type {
  CloudExclusion,
  TranslationFailure,
  TranslationFailureCode,
  TranslationOutcome,
  TranslationPrivacy,
  TranslationRequest
} from "./types";
import { privacyForContentType } from "./types";

const inFlight = new Map<string, Promise<TranslationOutcome>>();
/** Cache key -> the request ids currently waiting on it, for cancellation. */
const waiters = new Map<string, Set<string>>();
const canceledRequests = new Set<string>();

export async function translateText(request: TranslationRequest): Promise<TranslationOutcome> {
  const text = request.text ?? "";
  if (!text.trim()) return fail(request, "invalid_text", false);

  const target = normalizeLanguage(request.targetLanguage);
  if (target.kind !== "language") return fail(request, "invalid_language", false);

  const source = normalizeLanguage(request.sourceLanguage ?? null);
  if (source.kind === "invalid") return fail(request, "invalid_language", false);
  const sourceTag = source.kind === "language" ? source.tag : null;

  if (sourceTag && isSameLanguage(sourceTag, target.tag)) {
    return fail(request, "same_language", false);
  }

  const normalized: TranslationRequest = {
    ...request,
    text,
    sourceLanguage: sourceTag,
    targetLanguage: target.tag
  };
  const privacy = privacyForContentType(request.contentType);

  // Dedupe on the cache key rather than the request id: two cells showing the
  // same text want the same answer, and they arrive with different ids by
  // construction (Stage 3 requires per-action ids so a recycled cell can drop a
  // stale response).
  const dedupeKey = keyFor(normalized, privacy, "dedupe");
  const existing = inFlight.get(dedupeKey);
  if (existing) {
    trackWaiter(dedupeKey, normalized.requestId);
    return adoptOutcome(await existing, normalized);
  }

  const work = run(normalized, privacy).finally(() => {
    inFlight.delete(dedupeKey);
    waiters.delete(dedupeKey);
  });
  inFlight.set(dedupeKey, work);
  trackWaiter(dedupeKey, normalized.requestId);
  return work;
}

async function run(
  request: TranslationRequest,
  privacy: TranslationPrivacy
): Promise<TranslationOutcome> {
  const startedAt = Date.now();
  const flags = readTranslationFlags();
  const characters = request.text.length;

  // Neither hydration is awaited before the cache read below on a cold start:
  // `hydrateTranslationCache` resolving late only costs a miss, whereas blocking
  // here would delay the first paint of a translated cell (Stage 7).
  void hydrateTranslationCache();
  void hydrateCloudBudget();

  const cached = readCached(request, privacy);
  if (cached) {
    const outcome: TranslationOutcome = {
      ok: true,
      requestId: request.requestId,
      contentId: request.contentId,
      provider: "cache",
      translatedText: cached.entry.translatedText,
      sourceLanguage: cached.entry.sourceLanguage,
      targetLanguage: cached.entry.targetLanguage,
      cached: true,
      downloadPrepared: false,
      durationMs: Date.now() - startedAt
    };
    emit(request, privacy, outcome, characters);
    return outcome;
  }

  const cloudReachable = cloudRequestAllowed(characters);
  const plan = planProviders(request, flags, cloudReachable);

  let lastFailure: TranslationFailure | null = null;
  let cloudPermitted = true;

  if (plan.providers.includes("apple_on_device") && appleIsUsable()) {
    const attempt = await appleTranslate(request);
    if (attempt.outcome.ok) {
      writeTranslationCache(privacy, keyFor(request, privacy, appleProviderVersion()), {
        translatedText: attempt.outcome.translatedText,
        sourceLanguage: attempt.outcome.sourceLanguage,
        targetLanguage: attempt.outcome.targetLanguage,
        provider: "apple_on_device",
        storedAt: Date.now()
      });
      emit(request, privacy, attempt.outcome, characters);
      return attempt.outcome;
    }
    lastFailure = attempt.outcome;
    cloudPermitted = attempt.permitsCloudFallback;
  } else if (plan.providers.includes("apple_on_device")) {
    // Flag on, but this binary or this OS cannot reach Apple. That is exactly
    // the case the cloud fallback exists for, so it stays permitted.
    lastFailure = failure(request, "unsupported_os_version", false);
  }

  if (!plan.providers.includes("cloud_fallback")) {
    // Two facts, two fields. `code` keeps the specific cause when there is
    // one: "Apple has no model for this pair" is more useful to the user, and
    // to Stage 12's unsupported-pair reporting, than "the cloud is off", which
    // is a fact about the deployment rather than about the request.
    // `cloudExclusion` carries the refusal either way, so an exhausted budget
    // or a tripped breaker stays observable even when a provider failure is
    // what gets shown. Collapsing both into `code` would have made the
    // cost-control refusals unreachable in practice, since the cloud is only
    // ever considered after Apple has already failed.
    const exclusion = plan.cloudExclusion;
    const code: TranslationFailureCode = exclusion
      ? codeForExclusion(exclusion)
      : "translation_disabled";
    const outcome: TranslationFailure = lastFailure
      ? { ...lastFailure, cloudExclusion: exclusion ?? undefined }
      : unavailable(request, code, exclusion ?? undefined);
    return finish(request, privacy, outcome, characters);
  }

  if (!cloudPermitted) {
    return finish(request, privacy, lastFailure ?? failure(request, "request_canceled", false), characters);
  }

  // Charged before the call, not after: a request that times out still bills,
  // and under-counting a failure is the expensive direction of that mistake.
  recordCloudSpend(characters);
  const cloudAttempt = await cloudTranslate(request);
  if (cloudAttempt.outcome.ok) {
    recordCloudSuccess();
    writeTranslationCache(privacy, keyFor(request, privacy, cloudProviderVersion()), {
      translatedText: cloudAttempt.outcome.translatedText,
      sourceLanguage: cloudAttempt.outcome.sourceLanguage,
      targetLanguage: cloudAttempt.outcome.targetLanguage,
      provider: "cloud_fallback",
      storedAt: Date.now()
    });
    emit(request, privacy, cloudAttempt.outcome, characters);
    return cloudAttempt.outcome;
  }

  if (cloudAttempt.outcome.recoverable) recordCloudFailure();
  return finish(request, privacy, cloudAttempt.outcome, characters);
}

function readCached(request: TranslationRequest, privacy: TranslationPrivacy) {
  for (const providerVersion of [appleProviderVersion(), cloudProviderVersion()]) {
    const key = keyFor(request, privacy, providerVersion);
    const entry = readTranslationCache(privacy, key);
    if (entry) return { key, entry };
  }
  return null;
}

function keyFor(request: TranslationRequest, privacy: TranslationPrivacy, providerVersion: string) {
  return translationCacheKey({
    privacy,
    userScope: currentCacheScope(),
    contentId: request.contentId,
    contentVersion: request.contentVersion,
    text: request.text,
    sourceLanguage: request.sourceLanguage ?? null,
    targetLanguage: request.targetLanguage,
    providerVersion
  });
}

/**
 * Restamp a shared outcome with the adopting request's own identity.
 *
 * A follower that took the leader's promise must not receive the leader's
 * `requestId`, or the staleness check in the calling component would reject its
 * own answer.
 */
function adoptOutcome(outcome: TranslationOutcome, request: TranslationRequest): TranslationOutcome {
  if (canceledRequests.has(request.requestId)) {
    canceledRequests.delete(request.requestId);
    return failure(request, "request_canceled", false);
  }
  return { ...outcome, requestId: request.requestId, contentId: request.contentId };
}

function trackWaiter(dedupeKey: string, requestId: string) {
  const set = waiters.get(dedupeKey) ?? new Set<string>();
  set.add(requestId);
  waiters.set(dedupeKey, set);
}

function finish(
  request: TranslationRequest,
  privacy: TranslationPrivacy,
  outcome: TranslationOutcome,
  characters: number
) {
  emit(request, privacy, outcome, characters);
  return outcome;
}

function emit(
  request: TranslationRequest,
  privacy: TranslationPrivacy,
  outcome: TranslationOutcome,
  characters: number
) {
  recordTranslationEvent({
    provider: outcome.provider,
    ok: outcome.ok,
    code: outcome.ok ? undefined : outcome.code,
    cloudExclusion: outcome.ok ? undefined : outcome.cloudExclusion,
    privacy,
    contentType: request.contentType,
    sourceLanguage: request.sourceLanguage ?? null,
    targetLanguage: request.targetLanguage,
    characters,
    durationMs: outcome.ok ? outcome.durationMs : 0,
    userInitiated: request.userInitiated,
    downloadPrepared: outcome.ok ? outcome.downloadPrepared : false
  });
}

function failure(
  request: TranslationRequest,
  code: TranslationFailureCode,
  recoverable: boolean,
  detail?: string
): TranslationFailure {
  return {
    ok: false,
    requestId: request.requestId,
    contentId: request.contentId,
    provider: "apple_on_device",
    code,
    recoverable,
    downloadAvailable: false,
    detail
  };
}

function unavailable(
  request: TranslationRequest,
  code: TranslationFailureCode,
  exclusion?: CloudExclusion
): TranslationFailure {
  return {
    ok: false,
    requestId: request.requestId,
    contentId: request.contentId,
    provider: "unavailable",
    code,
    recoverable: false,
    downloadAvailable: false,
    cloudExclusion: exclusion,
    detail: exclusion
  };
}

function fail(
  request: TranslationRequest,
  code: TranslationFailureCode,
  recoverable: boolean
): TranslationOutcome {
  const outcome = unavailable(request, code);
  const privacy = privacyForContentType(request.contentType);
  recordTranslationEvent({
    provider: "unavailable",
    ok: false,
    code,
    privacy,
    contentType: request.contentType,
    sourceLanguage: request.sourceLanguage ?? null,
    targetLanguage: request.targetLanguage,
    characters: (request.text ?? "").length,
    durationMs: 0,
    userInitiated: request.userInitiated,
    downloadPrepared: false
  });
  return { ...outcome, recoverable };
}

/**
 * Cancel by request id.
 *
 * The in-flight promise is deliberately *not* aborted when other waiters are
 * still attached: the work is already running on-device and discarding it would
 * make the remaining waiters start it again. What cancellation guarantees is
 * that this request's caller receives `request_canceled` instead of an answer,
 * which is what a recycled cell needs.
 */
export async function cancelTranslationRequests(requestIds: string[], reason = "canceled") {
  if (requestIds.length === 0) return;
  for (const id of requestIds) canceledRequests.add(id);
  const abandoned: string[] = [];
  for (const [key, set] of waiters) {
    for (const id of requestIds) set.delete(id);
    if (set.size === 0) {
      abandoned.push(...requestIds);
      waiters.delete(key);
    }
  }
  if (abandoned.length > 0) await appleCancelRequests(abandoned, reason);
}

/** Cancel every outstanding translation of these content ids (scroll-away). */
export async function cancelTranslationContent(contentIds: string[], reason = "scrolled_away") {
  if (contentIds.length === 0) return;
  await appleCancelContent(contentIds, reason);
}

/** Logout, account deletion, security reset. */
export function resetTranslationRouter(reason: string) {
  inFlight.clear();
  waiters.clear();
  canceledRequests.clear();
  clearTranslationCache(reason);
}

export function routerStateForTests() {
  return { inFlight: inFlight.size, waiters: waiters.size, canceled: canceledRequests.size };
}
