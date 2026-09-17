/**
 * The per-device half of Stage 8's cost control.
 *
 * Rate limiting by user and by IP has to be server-side, because a client can
 * be reinstalled and a counter it keeps is advisory. What the client can do,
 * and what the server cannot, is refuse to *make* the request: a budget checked
 * here turns a cost spike into a local no-op rather than a rejected HTTP call
 * that still cost a round trip and still filled a log.
 *
 * So this is a second, weaker fence in front of the authoritative one, and it
 * is the fence that protects against the failure the audit actually found —
 * automatic translation quietly billing per character with nothing counting it.
 *
 * The breaker is deliberately in-memory only. Persisting an open breaker would
 * mean a transient provider outage during one session could keep translation
 * off after a relaunch, with no way for a user to clear it; the budget, which
 * is the thing that must not be resettable by force-quitting, is the part that
 * persists.
 */

import { readJsonCache, writeJsonCache } from "../../core/cache";
import type { CloudExclusion } from "./policy";

/**
 * Characters of billable text one device may send per day and per month.
 *
 * Sized against what a person can actually read: a few hundred translated posts
 * or messages a day. A legitimate user does not approach it, so hitting it
 * means either a loop or an automatic path that should not have been billable —
 * both of which are better stopped than served.
 */
const DAILY_CHARACTER_BUDGET = 20_000;
const MONTHLY_CHARACTER_BUDGET = 200_000;

/** Consecutive provider failures that open the breaker. */
const BREAKER_THRESHOLD = 4;
/** How long it stays open. One shot is allowed through when it elapses. */
const BREAKER_COOLDOWN_MS = 5 * 60 * 1000;

const BUDGET_CACHE_KEY = "translation:cloud-budget";

type BudgetState = { day: string; dayChars: number; month: string; monthChars: number };

let budget: BudgetState = { day: "", dayChars: 0, month: "", monthChars: 0 };
let hydrated = false;
let consecutiveFailures = 0;
let breakerOpenedAt = 0;

function dayKey(now: number) {
  return new Date(now).toISOString().slice(0, 10);
}

function monthKey(now: number) {
  return new Date(now).toISOString().slice(0, 7);
}

function rollPeriods(now: number) {
  const day = dayKey(now);
  const month = monthKey(now);
  if (budget.day !== day) budget = { ...budget, day, dayChars: 0 };
  if (budget.month !== month) budget = { ...budget, month, monthChars: 0 };
}

/**
 * Load the persisted counters. Never awaited on a render path — an unhydrated
 * budget reads as zero spend, which is the permissive direction, and the first
 * cloud request of a launch is not the one worth blocking.
 */
export async function hydrateCloudBudget() {
  if (hydrated) return;
  hydrated = true;
  const stored = await readJsonCache<BudgetState>(BUDGET_CACHE_KEY, value => value);
  if (
    stored &&
    typeof stored.dayChars === "number" &&
    typeof stored.monthChars === "number" &&
    typeof stored.day === "string" &&
    typeof stored.month === "string"
  ) {
    budget = stored;
  }
  rollPeriods(Date.now());
}

export type CloudReachability = { allowed: boolean; exclusion: CloudExclusion | null };

/**
 * Whether a cloud request of this size may be made right now.
 *
 * `characters` is charged against the budget *before* the request, not after,
 * because a request that times out still bills. Under-counting a failure is the
 * expensive mistake; over-counting one only costs a translation.
 */
export function cloudRequestAllowed(characters: number, now = Date.now()): CloudReachability {
  if (breakerIsOpen(now)) return { allowed: false, exclusion: "breaker_open" };
  rollPeriods(now);
  if (budget.dayChars + characters > DAILY_CHARACTER_BUDGET) {
    return { allowed: false, exclusion: "budget_exhausted" };
  }
  if (budget.monthChars + characters > MONTHLY_CHARACTER_BUDGET) {
    return { allowed: false, exclusion: "budget_exhausted" };
  }
  return { allowed: true, exclusion: null };
}

export function recordCloudSpend(characters: number, now = Date.now()) {
  rollPeriods(now);
  budget = {
    ...budget,
    dayChars: budget.dayChars + Math.max(0, characters),
    monthChars: budget.monthChars + Math.max(0, characters)
  };
  void writeJsonCache(BUDGET_CACHE_KEY, budget).catch(() => {
    /* best effort; the in-memory counter still bounds this session */
  });
}

export function recordCloudFailure(now = Date.now()) {
  consecutiveFailures += 1;
  if (consecutiveFailures >= BREAKER_THRESHOLD) breakerOpenedAt = now;
}

export function recordCloudSuccess() {
  consecutiveFailures = 0;
  breakerOpenedAt = 0;
}

function breakerIsOpen(now: number) {
  if (breakerOpenedAt === 0) return false;
  if (now - breakerOpenedAt < BREAKER_COOLDOWN_MS) return true;
  // Cooldown elapsed: let exactly one request through to probe the provider. A
  // failure re-opens the breaker because `consecutiveFailures` is still at the
  // threshold, so a sustained outage costs one request per cooldown window
  // rather than an unbounded retry loop.
  breakerOpenedAt = 0;
  return false;
}

export function cloudBudgetSnapshot() {
  return {
    dayChars: budget.dayChars,
    monthChars: budget.monthChars,
    dailyBudget: DAILY_CHARACTER_BUDGET,
    monthlyBudget: MONTHLY_CHARACTER_BUDGET,
    consecutiveFailures,
    breakerOpenedAt
  };
}

export function resetCloudGuardForTests() {
  budget = { day: "", dayChars: 0, month: "", monthChars: 0 };
  hydrated = false;
  consecutiveFailures = 0;
  breakerOpenedAt = 0;
}
