/**
 * The connectivity authority. One module decides whether PulseSoc can reach its
 * backend, and every screen reads that decision instead of inferring its own.
 *
 * WHY THIS IS NOT NetInfo
 * -----------------------
 * "Wi-Fi is connected" is not the question. A captive portal, an airplane's
 * pay-wall, a hotel router that resolves DNS but drops TCP, a backend outage and
 * a phone in a lift all present as "connected" to the OS and as "nothing loads"
 * to the reader. The only evidence that PulseSoc is reachable is that PulseSoc
 * answered, so this module is built on request outcomes rather than on a radio
 * flag.
 *
 * That also avoids a dependency change. `@react-native-community/netinfo` would
 * touch package.json, package-lock.json and Podfile.lock, all three of which are
 * in `dependency_watch.files` of config/realtime-audio-protected-paths.json --
 * so adding it would drag the realtime-audio battery and a fresh declaration
 * into a mission that has nothing to do with audio, and would need a native
 * rebuild rather than an OTA update. The evidence-based design is both cheaper
 * and more truthful, so there is no trade-off to regret here.
 *
 * THE ASYMMETRY THAT MAKES THIS CORRECT
 * -------------------------------------
 * Success and failure are not equally informative, and treating them as though
 * they were is how these modules usually go wrong.
 *
 * A completed round trip is *proof*. You cannot receive an HTTP status from
 * pulsesoc.com without a working path to it. So one success is enough to
 * declare reachability, immediately, with no corroboration.
 *
 * A failed request proves almost nothing. One request can fail against a
 * perfectly good network -- a single dropped packet, one overloaded worker, a
 * route that 500s for its own reasons. So failure needs corroboration before it
 * is allowed to move the state, and the pessimistic direction is the slow one.
 *
 * THE MISREADING THIS MODULE EXISTS TO PREVENT
 * --------------------------------------------
 * An HTTP error is a *successful* round trip. A 401, a 403, a 404, a 500 --
 * every one of those came back from the server, which means the network worked.
 * Code that catches an error and concludes "offline" marks a signed-out user, a
 * missing record and a backend bug as network failures, then shows all three the
 * same "check your connection" message and hides the real cause.
 *
 * Only two outcomes are transport evidence, and `pulseApi` already labels them:
 * `request_unreachable` (fetch rejected) and `request_timeout` (the budget
 * expired with no response). Nothing else in this file treats an error as a
 * connectivity signal, and `stateLanguage.failureCause` draws the same line for
 * the same reason.
 */

import { AppState } from "react-native";
import type { AppStateStatus } from "react-native";

import { PULSE_API_BASE_URL } from "../api/config";

/**
 * The four states, ordered from healthy to not.
 *
 * `degraded` is separate from `offline` because the correct behaviour differs.
 * Offline means stop trying and serve cache; degraded means the path exists but
 * is unreliable, so heavy work (video prefetch, large uploads) should stand down
 * while a message send should still be attempted.
 *
 * `recovering` is separate from `online` because the moment connectivity returns
 * is not the moment the app is consistent. Queued mutations have not drained and
 * visible screens still hold stale data. A screen that renders "online" during
 * that window is claiming a freshness it does not have, and -- worse -- a user
 * who sends during it can watch their message queue behind an older one. The
 * reconnect orchestrator owns the exit from this state.
 */
export type ConnectivityState = "online" | "degraded" | "offline" | "recovering";

/** What a single request told us about the path to the backend. */
export type ReachabilityEvidence =
  /** A response arrived. Any status. Proof the network works. */
  | "round_trip"
  /** fetch rejected. The request never reached an answer. */
  | "unreachable"
  /** The budget expired with no response. Reached, or hung -- ambiguous. */
  | "timeout";

export type ConnectivitySnapshot = {
  state: ConnectivityState;
  /** When the state last changed. Not when it was last confirmed. */
  since: number;
  /**
   * The last time anything completed a round trip, or 0 if nothing ever has.
   * This is what a "Last updated" line should be derived from -- it is a fact,
   * unlike a state name.
   */
  lastRoundTripAt: number;
  /** Consecutive transport failures with no round trip in between. */
  consecutiveFailures: number;
  /**
   * Round-trip duration of the most recent success, in ms. Used for the
   * degraded decision; 0 when unknown.
   */
  lastLatencyMs: number;
  /**
   * True when the backend answered but reported itself unhealthy. Reachable and
   * broken is a real state and it is not the phone's fault.
   */
  backendUnhealthy: boolean;
};

export const CONNECTIVITY_LIMITS = Object.freeze({
  /**
   * How many transport failures in a row before declaring offline.
   *
   * Two, not one. One failure is routinely a dropped packet on a working
   * connection, and a single-failure threshold makes the whole app flip to
   * offline copy during a normal scroll. Two, not five, because the user is
   * already staring at a stalled screen by then -- the honest answer costs less
   * than a longer lie.
   */
  failuresBeforeOffline: 2,
  /**
   * A success slower than this suggests a path that technically works but will
   * not carry video. Chosen as a fraction of the 15s read budget rather than as
   * a round number: past this point a second request is likely to time out.
   */
  degradedLatencyMs: 5_000,
  /**
   * Probe backoff while not online. Capped so a phone left in a tunnel does not
   * probe forever at full rate, and floored high enough that recovery is felt as
   * immediate rather than instant-but-battery-hostile.
   */
  probeBackoffMs: [1_000, 2_000, 4_000, 8_000, 15_000, 30_000] as readonly number[],
  /**
   * The probe's own timeout. Much shorter than a real request: this asks one
   * question -- is there a path -- and a slow answer is a "no" for our purposes.
   */
  probeTimeoutMs: 4_000,
  /**
   * Ceiling on `recovering`. If the orchestrator never reports completion --
   * because it crashed, or was never mounted, or the app was backgrounded
   * mid-drain -- the state must not stay pinned forever. Recovery that takes
   * longer than this has failed at being recovery.
   */
  recoveryCeilingMs: 20_000,
  /**
   * Minimum dwell before `offline` may be re-entered after leaving it.
   *
   * This is the flap damper. A marginal connection produces alternating success
   * and failure, and a state that tracks each one repaints every screen in the
   * app twice a second. Proof of reachability still wins instantly -- the damper
   * only slows the return to the pessimistic state.
   */
  offlineReentryDebounceMs: 3_000,
  /**
   * How much `lastRoundTripAt` must move before subscribers are told about it
   * while the state itself is unchanged.
   *
   * `lastRoundTripAt` advances on every request, and a feed screen makes many.
   * Notifying on each one would re-render every connectivity subscriber in the
   * app dozens of times a minute to move a "last updated" line by a second --
   * so the timestamp is coalesced. A state *change* is never coalesced, and the
   * first confirmed round trip after launch always emits, because "never
   * confirmed" to "confirmed" is a real transition even though the state name
   * does not move.
   */
  roundTripEmitGranularityMs: 30_000
});

let state: ConnectivityState = "online";
let since = Date.now();
let lastRoundTripAt = 0;
let consecutiveFailures = 0;
let lastLatencyMs = 0;
let backendUnhealthy = false;
let leftOfflineAt = 0;
let lastEmittedRoundTripAt = 0;

const listeners = new Set<(snapshot: ConnectivitySnapshot) => void>();

let probeTimer: ReturnType<typeof setTimeout> | null = null;
let probeAttempt = 0;
let probeInFlight = false;
let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
let appStateSubscription: { remove: () => void } | null = null;
/** Whether the active half of the authority is running. See {@link scheduleProbe}. */
let monitoring = false;

/**
 * The initial state is `online`, not `offline`.
 *
 * Nothing has been tried yet, so neither is known -- but the two guesses fail
 * very differently. Guessing offline shows every screen an "you're offline"
 * banner on a cold start with a perfectly good connection, and the first
 * successful request then clears it: a visible lie on every launch. Guessing
 * online costs at most one failed request before the truth arrives, and that
 * request was going to be made anyway.
 */
export function connectivitySnapshot(): ConnectivitySnapshot {
  return {
    state,
    since,
    lastRoundTripAt,
    consecutiveFailures,
    lastLatencyMs,
    backendUnhealthy
  };
}

export function connectivityState(): ConnectivityState {
  return state;
}

/** True when it is worth attempting a network write at all. */
export function canAttemptNetwork(): boolean {
  return state !== "offline";
}

/**
 * True when heavy, deferrable work should stand down.
 *
 * Deliberately not the inverse of {@link canAttemptNetwork}. A degraded path
 * should still carry a message the user just typed; it should not carry a
 * speculative 20MB video prefetch that will compete with it.
 */
export function shouldDeferHeavyWork(): boolean {
  return state !== "online";
}

export function subscribeConnectivity(listener: (snapshot: ConnectivitySnapshot) => void) {
  listeners.add(listener);
  listener(connectivitySnapshot());
  return () => {
    listeners.delete(listener);
  };
}

function emit() {
  lastEmittedRoundTripAt = lastRoundTripAt;
  const snapshot = connectivitySnapshot();
  listeners.forEach((listener) => listener(snapshot));
}

/**
 * Publish an advance in `lastRoundTripAt` that did not change the state.
 *
 * Without this, a subscriber rendering freshness would go stale for as long as
 * the connection stayed healthy -- the state name never changes while online,
 * so nothing would ever wake it. The granularity keeps that from turning into a
 * re-render on every request.
 */
function emitRoundTripIfDue() {
  if (lastRoundTripAt - lastEmittedRoundTripAt >= CONNECTIVITY_LIMITS.roundTripEmitGranularityMs) {
    emit();
  }
}

function transition(next: ConnectivityState) {
  if (next === state) return;

  // The flap damper, applied only on the way down. Reachability that has just
  // been proven is never held back; a return to offline inside the window is
  // deferred to the next piece of evidence, which by then has more of it.
  if (next === "offline" && leftOfflineAt && Date.now() - leftOfflineAt < CONNECTIVITY_LIMITS.offlineReentryDebounceMs) {
    if (state !== "degraded") {
      state = "degraded";
      since = Date.now();
      emit();
      scheduleProbe();
    }
    return;
  }

  if (state === "offline") leftOfflineAt = Date.now();

  state = next;
  since = Date.now();

  if (next === "recovering") armRecoveryCeiling();
  else clearRecoveryCeiling();

  if (next === "online") stopProbing();
  else scheduleProbe();

  emit();
}

/**
 * Record what one request outcome proved. Called once, from `pulseApi`, so that
 * every request in the app feeds the authority without a single call site
 * knowing this module exists.
 *
 * `durationMs` is only meaningful for a round trip; callers may pass 0.
 */
export function reportReachability(evidence: ReachabilityEvidence, durationMs = 0) {
  if (evidence === "round_trip") {
    lastRoundTripAt = Date.now();
    consecutiveFailures = 0;
    if (durationMs > 0) lastLatencyMs = durationMs;

    // Proof of a path. The only question left is which healthy-ish state to be
    // in, and whether anyone still has to finish reconnecting.
    if (state === "offline") {
      transition("recovering");
      return;
    }
    if (state === "recovering") {
      // Held deliberately. The orchestrator decides when reconnect work is
      // done; more successful requests during the drain are expected and are
      // not evidence that the drain finished.
      emit();
      return;
    }
    const slow = durationMs > 0 && durationMs > CONNECTIVITY_LIMITS.degradedLatencyMs;
    const next: ConnectivityState = slow || backendUnhealthy ? "degraded" : "online";
    if (next === state) emitRoundTripIfDue();
    else transition(next);
    return;
  }

  consecutiveFailures += 1;

  // A timeout is weaker evidence than a rejection: the request may have reached
  // a backend that is merely slow, which is a degraded path rather than an
  // absent one. It still counts toward the offline threshold, because a path
  // that never answers is not usable either.
  if (consecutiveFailures >= CONNECTIVITY_LIMITS.failuresBeforeOffline) {
    transition("offline");
    return;
  }
  if (state === "online") transition("degraded");
  else emit();
}

/**
 * Translate a `pulseApi` rejection into evidence.
 *
 * Kept here rather than at the call site so the "an HTTP error is a successful
 * round trip" rule is stated in exactly one place. A caller that reimplements
 * this is the defect this module was written to prevent.
 */
export function evidenceFromErrorCode(code: string | undefined, status: number): ReachabilityEvidence {
  if (code === "request_unreachable") return "unreachable";
  if (code === "request_timeout") return "timeout";
  // Anything else carrying a real HTTP status came back from the server, which
  // means the path works. `status === 0` means we never learned one, and that is
  // the only non-labelled case worth treating as a transport failure.
  return status > 0 ? "round_trip" : "unreachable";
}

/**
 * The orchestrator reports that reconnect work has drained and the app is
 * consistent again. This is the only ordinary exit from `recovering`.
 */
export function markRecoveryComplete() {
  if (state !== "recovering") return;
  transition(backendUnhealthy ? "degraded" : "online");
}

/* ------------------------------------------------------------------ probing */

/**
 * WHY PROBE AT ALL
 *
 * Passive evidence only arrives when something makes a request, and an offline
 * app stops making them -- screens fall back to cache and settle. Without a
 * probe the app would sit in `offline` until the user pulled to refresh, which
 * makes recovery feel manual. The probe is what lets a phone coming out of a
 * tunnel repaint itself.
 *
 * It runs only while not online, and backs off, so a genuinely disconnected
 * phone costs one small request every 30 seconds rather than a spin.
 */
function scheduleProbe() {
  // Probing is a lifecycle activity, not a consequence of observing evidence.
  //
  // The authority has two halves: a passive one that anyone may feed and read
  // (pure, synchronous, no timers), and an active one that holds a repeating
  // timer to detect recovery on its own. Only the app's boot path wants the
  // second, and it asks for it by name.
  //
  // Keeping them separate is what stops a module-scope singleton from arming a
  // background timer merely because some unrelated request failed. That is not
  // hypothetical: it made `pulseApiTimeout.test.ts` -- which asserts a
  // successful request leaves no pending work -- fail, because its own timeout
  // cases had quietly armed a probe. The test was right, and a timer nobody
  // asked for is worse in the app than in the suite.
  if (!monitoring) return;
  if (probeTimer) return;
  const backoff = CONNECTIVITY_LIMITS.probeBackoffMs;
  const delay = backoff[Math.min(probeAttempt, backoff.length - 1)] ?? backoff[backoff.length - 1];
  probeTimer = setTimeout(() => {
    probeTimer = null;
    void runProbe();
  }, delay);
}

function stopProbing() {
  if (probeTimer) {
    clearTimeout(probeTimer);
    probeTimer = null;
  }
  probeAttempt = 0;
}

/**
 * `/health` is the right target: unauthenticated, `Cache-Control: no-store`, and
 * it answers 200 whenever the process is alive. Its `ok` field additionally
 * distinguishes "reachable and working" from "reachable but the database is
 * gone" -- which is a real state the phone should render differently from its
 * own connection being down, and is not something latency could tell us.
 *
 * Deliberately raw `fetch`, not `pulseApi`: the probe must not carry session
 * headers, must not be coalesced with real traffic, and must not recurse back
 * into this module through the reporting hook.
 */
async function runProbe() {
  if (probeInFlight) return;
  probeInFlight = true;
  const startedAt = Date.now();
  const controller = new AbortController();
  const abortTimer = setTimeout(() => controller.abort(), CONNECTIVITY_LIMITS.probeTimeoutMs);
  try {
    const response = await fetch(`${PULSE_API_BASE_URL}/health`, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal
    });
    const elapsed = Date.now() - startedAt;
    let healthy = response.ok;
    try {
      const body = await response.json();
      if (body && typeof body === "object" && "ok" in body) healthy = Boolean((body as { ok?: unknown }).ok);
    } catch {
      // A 200 with an unreadable body still proves the path. Health is unknown,
      // not false -- do not manufacture an outage out of a parse error.
    }
    backendUnhealthy = !healthy;
    probeAttempt = 0;
    reportReachability("round_trip", elapsed);
  } catch {
    probeAttempt += 1;
    reportReachability("unreachable");
  } finally {
    clearTimeout(abortTimer);
    probeInFlight = false;
    if (state !== "online") scheduleProbe();
  }
}

function armRecoveryCeiling() {
  clearRecoveryCeiling();
  // Same split as the probe. The ceiling exists to rescue a *running* app whose
  // orchestrator never reported back; with the active half stopped there is no
  // orchestrator to fail, and the caller is driving the state explicitly.
  if (!monitoring) return;
  recoveryTimer = setTimeout(() => {
    recoveryTimer = null;
    if (state === "recovering") transition(backendUnhealthy ? "degraded" : "online");
  }, CONNECTIVITY_LIMITS.recoveryCeilingMs);
}

function clearRecoveryCeiling() {
  if (recoveryTimer) {
    clearTimeout(recoveryTimer);
    recoveryTimer = null;
  }
}

/* -------------------------------------------------------------- app lifecycle */

/**
 * Returning to the foreground is the single highest-yield moment to re-check.
 * The phone may have changed network, moved continent, or simply been asleep
 * while the state went stale.
 *
 * NOTE: this subscribes to *changes* and never reads `AppState.currentState` at
 * module scope. That property is seeded during app launch and is "inactive" for
 * the whole process lifetime when read from a module singleton, so a foreground
 * check written against it would never fire.
 */
export function startConnectivityMonitor() {
  if (appStateSubscription) return;
  monitoring = true;
  // If evidence arrived before boot finished wiring this up, the state may
  // already be pessimistic with no probe armed to recover from it.
  if (state !== "online") scheduleProbe();
  appStateSubscription = AppState.addEventListener("change", (next: AppStateStatus) => {
    if (next !== "active") return;
    probeAttempt = 0;
    if (state !== "online") {
      stopProbing();
      void runProbe();
    }
  });
}

export function stopConnectivityMonitor() {
  monitoring = false;
  appStateSubscription?.remove();
  appStateSubscription = null;
  stopProbing();
  clearRecoveryCeiling();
}

/** Test seam. Never called by the app. */
export function resetConnectivityForTests(next: Partial<ConnectivitySnapshot> = {}) {
  stopConnectivityMonitor();
  state = next.state ?? "online";
  since = next.since ?? Date.now();
  lastRoundTripAt = next.lastRoundTripAt ?? 0;
  consecutiveFailures = next.consecutiveFailures ?? 0;
  lastLatencyMs = next.lastLatencyMs ?? 0;
  backendUnhealthy = next.backendUnhealthy ?? false;
  leftOfflineAt = 0;
  lastEmittedRoundTripAt = 0;
  probeAttempt = 0;
  probeInFlight = false;
  listeners.clear();
}
