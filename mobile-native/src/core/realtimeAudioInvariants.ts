/**
 * Runtime invariant monitor for the real-time audio foundation.
 *
 * WHY THIS EXISTS IN PRODUCTION BUILDS
 *
 * The architecture tests prove the boundary holds in the repository. The
 * contract tests prove the invariants hold in a simulator. Neither can observe
 * a real device where a race, a delayed cleanup, or a server-side token change
 * produces a state the tests never constructed. A debug-only assertion is no
 * help there: the states that matter appear on a user's phone, under a network
 * condition CI does not have, in a build that has assertions compiled out.
 *
 * So this module runs unconditionally. It is not gated on `__DEV__`.
 *
 * WHAT IT DOES AND DELIBERATELY DOES NOT DO
 *
 * It reports. It does not repair, and it does not decide.
 *
 * Every violation below is already handled by the module that detects it: the
 * ownership policy denies the losing claim, the publisher returns `forbidden`
 * for a viewer, the lease check makes a stale release a no-op, the publisher
 * reconciles a duplicate track. This module is the observer that turns those
 * silent, correct rejections into a signal someone can act on — because an
 * invariant that is enforced but never counted gives you no way to know the
 * enforcement is firing a thousand times a day in production.
 *
 * That separation is deliberate. If this module also took corrective action it
 * would become a second decision-maker for audio state, which is precisely the
 * failure mode the whole boundary exists to prevent.
 *
 * CRASH POLICY
 *
 * Never throw for a state that has already been rejected. A user in a live call
 * must not lose the call because a diagnostic disliked something. The one
 * category permitted to escalate is ownership corruption — a session that is
 * active with no valid owner — because continuing from there means the next
 * cleanup releases the wrong session, and a loud failure is preferable to a
 * silently wrong one. Escalation is opt-in via `setRealtimeAudioInvariantPolicy`
 * and is off by default, so it can be enabled in QA builds without ever being
 * the reason a production call drops.
 *
 * PRIVACY
 *
 * Details are drawn from a fixed vocabulary of mode and outcome names. No user
 * identifier, room name, token, or URL is ever passed through; identifiers are
 * hashed by the telemetry module before emission.
 */
import { emitRealtimeAudioEvent } from "./realtimeAudioTelemetry";

/** The eight states that mean the audio foundation's assumptions were violated. */
export type RealtimeAudioInvariantId =
  /** Two owners believe they hold the microphone. Produces an echo or silence. */
  | "multiple_microphone_owners"
  /** More than one local audio track exists on a room. */
  | "duplicate_microphone_tracks"
  /** A second publication was attempted while one was already in flight. */
  | "duplicate_publication"
  /** A route change was applied while a previous route was still being applied. */
  | "conflicting_route_state"
  /** A cleanup holding an old lease tried to release a newer session. */
  | "stale_cleanup_of_newer_session"
  /** A viewer-mode participant tried to publish the microphone. */
  | "viewer_publication_attempt"
  /** The audio session is active but no owner holds it. */
  | "session_active_without_owner"
  /** A reconnect was attempted against a room already in a terminal state. */
  | "terminal_room_reconnect_attempt";

/** What the detecting module did about it. This module never changes it. */
export type RealtimeAudioInvariantAction =
  /** The invalid action was refused and the valid session was preserved. */
  | "rejected"
  /** The state was repaired by the owning module (e.g. a duplicate unpublished). */
  | "reconciled"
  /** Observed and recorded; nothing to reject because nothing was requested. */
  | "reported";

export type RealtimeAudioInvariantViolation = {
  id: RealtimeAudioInvariantId;
  action: RealtimeAudioInvariantAction;
  /** Mode or outcome name from a fixed vocabulary. Never a user identifier. */
  detail: string;
  at: number;
};

type Policy = {
  /**
   * Throw on `session_active_without_owner`. Off by default: see the crash
   * policy note above. Intended for QA and internal builds.
   */
  throwOnOwnershipCorruption: boolean;
};

const DEFAULT_POLICY: Policy = { throwOnOwnershipCorruption: false };
let policy: Policy = { ...DEFAULT_POLICY };

/**
 * A bounded ring, not a growing array. A device in a reconnect loop can trip an
 * invariant continuously, and a diagnostic that leaks memory during exactly the
 * incident it exists to diagnose is worse than no diagnostic.
 */
const HISTORY_LIMIT = 32;
const history: RealtimeAudioInvariantViolation[] = [];
const counts = new Map<RealtimeAudioInvariantId, number>();

/** Fixed vocabulary. Anything outside it is replaced rather than emitted. */
const SAFE_DETAIL = /^[a-z0-9_]{1,32}$/;

function safeDetail(value: unknown): string {
  const text = String(value ?? "").toLowerCase();
  return SAFE_DETAIL.test(text) ? text : "unspecified";
}

export class RealtimeAudioInvariantError extends Error {
  readonly invariant: RealtimeAudioInvariantId;

  constructor(invariant: RealtimeAudioInvariantId, detail: string) {
    super(`Real-time audio invariant violated: ${invariant} (${detail})`);
    this.name = "RealtimeAudioInvariantError";
    this.invariant = invariant;
  }
}

export function setRealtimeAudioInvariantPolicy(next: Partial<Policy> | null | undefined): void {
  policy = { ...DEFAULT_POLICY, ...(next || {}) };
}

/**
 * Record a violation the detecting module has already handled.
 *
 * Returns the recorded violation so a caller can include it in its own result
 * type. Never returns null, and never alters the caller's control flow — except
 * for the single escalation case described in the crash policy.
 */
export function reportRealtimeAudioInvariant(input: {
  id: RealtimeAudioInvariantId;
  action: RealtimeAudioInvariantAction;
  detail?: unknown;
  sessionId?: unknown;
  correlationId?: unknown;
  roomType?: unknown;
  participantRole?: unknown;
}): RealtimeAudioInvariantViolation {
  const violation: RealtimeAudioInvariantViolation = {
    id: input.id,
    action: input.action,
    detail: safeDetail(input.detail),
    at: Date.now()
  };

  counts.set(violation.id, (counts.get(violation.id) ?? 0) + 1);
  history.push(violation);
  if (history.length > HISTORY_LIMIT) history.splice(0, history.length - HISTORY_LIMIT);

  emitRealtimeAudioEvent({
    name: "invariant_violation",
    sessionId: input.sessionId,
    correlationId: input.correlationId,
    roomType: input.roomType,
    participantRole: input.participantRole,
    outcome: violation.action,
    failureCategory: violation.id
  });

  if (violation.id === "session_active_without_owner" && policy.throwOnOwnershipCorruption) {
    throw new RealtimeAudioInvariantError(violation.id, violation.detail);
  }

  return violation;
}

/**
 * Pure checks. These take an observation and return the violations implied by
 * it. They perform no I/O and touch no audio state, so they are safe to call
 * from a hot path and safe to unit-test exhaustively.
 */
export function checkMicrophoneOwnership(observation: {
  microphoneOwnerIds: readonly string[];
}): RealtimeAudioInvariantId[] {
  return observation.microphoneOwnerIds.length > 1 ? ["multiple_microphone_owners"] : [];
}

export function checkPublicationState(observation: {
  localAudioTrackCount: number;
  inFlightPublications: number;
  isViewer: boolean;
  publishRequested: boolean;
}): RealtimeAudioInvariantId[] {
  const violations: RealtimeAudioInvariantId[] = [];
  if (observation.localAudioTrackCount > 1) violations.push("duplicate_microphone_tracks");
  if (observation.inFlightPublications > 1) violations.push("duplicate_publication");
  if (observation.isViewer && observation.publishRequested) violations.push("viewer_publication_attempt");
  return violations;
}

export function checkSessionOwnership(observation: {
  sessionActive: boolean;
  ownerId: string | null;
}): RealtimeAudioInvariantId[] {
  // An active session with no owner means the next release has no lease to
  // check against, so any caller can stop it — including one belonging to a
  // different feature. This is the ownership-corruption case.
  return observation.sessionActive && !observation.ownerId ? ["session_active_without_owner"] : [];
}

export function checkLeaseFreshness(observation: {
  requestedLeaseId: number;
  activeLeaseId: number;
}): RealtimeAudioInvariantId[] {
  return observation.requestedLeaseId < observation.activeLeaseId ? ["stale_cleanup_of_newer_session"] : [];
}

export function checkRouteState(observation: {
  appliedRoute: string | null;
  pendingRoute: string | null;
}): RealtimeAudioInvariantId[] {
  const { appliedRoute, pendingRoute } = observation;
  // Two routes in flight at once means the last write wins non-deterministically
  // and the earpiece/speaker state can end up inverted from what the UI shows.
  return appliedRoute && pendingRoute && appliedRoute !== pendingRoute ? ["conflicting_route_state"] : [];
}

export function checkReconnectEligibility(observation: {
  terminal: boolean;
  reconnectRequested: boolean;
}): RealtimeAudioInvariantId[] {
  // A terminal room never carries audio again. Retrying against it produces a
  // session that looks alive and is silent — the hardest symptom to diagnose.
  return observation.terminal && observation.reconnectRequested ? ["terminal_room_reconnect_attempt"] : [];
}

/** Snapshot for telemetry dashboards, QA screens, and tests. */
export function getRealtimeAudioInvariantReport(): {
  total: number;
  counts: Record<string, number>;
  recent: RealtimeAudioInvariantViolation[];
} {
  return {
    total: history.length ? Array.from(counts.values()).reduce((sum, n) => sum + n, 0) : 0,
    counts: Object.fromEntries(counts.entries()),
    recent: history.slice(-8).map((entry) => ({ ...entry }))
  };
}

export function resetRealtimeAudioInvariants(): void {
  history.length = 0;
  counts.clear();
  policy = { ...DEFAULT_POLICY };
}
