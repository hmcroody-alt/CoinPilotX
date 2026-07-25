import type { PulseCall, PulseCallParticipant } from "../api/calls";

/**
 * Pure, side-effect-free decisions that drive call ring audio (Issue 6:
 * "ringback/ringtone don't work"). The actual playback lives in
 * callSignalMedia.ts and the wiring in CallScreen / IncomingCallLayer, but the
 * BRANCHING that decides *whether* a tone should sound lived inline in those
 * components and was therefore impossible to unit-test. Extracting it here makes
 * the exact conditions a regression suite can lock in:
 *
 *   - the caller hears `ringback` only while the outgoing call is genuinely
 *     ringing and media has not yet connected;
 *   - the recipient hears `ringtone` only for a fresh inbound call addressed to
 *     them that they have not already dismissed.
 *
 * Getting either predicate subtly wrong is silent: no tone ever plays (or a tone
 * plays forever), which is precisely the reported symptom.
 */

/** Statuses that mean the call is over — no ring audio of any kind should play. */
export const TERMINAL_CALL_STATES = new Set<string>([
  "ended",
  "declined",
  "missed",
  "failed",
  "busy",
  "canceled",
  "cancelled",
  "expired",
  "rejected",
  "disconnected"
]);

/** Statuses that mean an inbound call is still awaiting the recipient's answer. */
export const INBOUND_RINGING_STATES = new Set<string>(["created", "ringing"]);

export function isTerminalCallStatus(status?: string | null): boolean {
  return TERMINAL_CALL_STATES.has(String(status || ""));
}

/**
 * Caller-side ringback gate. The outgoing caller should hear ringback strictly
 * while the backend reports `ringing`, the secure media room has NOT yet
 * connected, and the call is not already terminal. An incoming call never plays
 * ringback (that side plays ringtone instead).
 */
export function shouldPlayRingback(input: {
  direction?: string | null;
  mediaConnected: boolean;
  status?: string | null;
}): boolean {
  if (String(input.direction || "") === "incoming") return false;
  if (input.mediaConnected) return false;
  if (isTerminalCallStatus(input.status)) return false;
  return String(input.status || "") === "ringing";
}

/**
 * Recipient-side ringtone gate. Returns true when `call` is a fresh inbound call
 * that should surface the full-screen incoming UI (and therefore ring). Mirrors
 * the exact rules the incoming-call layer applies:
 *   - a real, non-dismissed call id;
 *   - a status that is still ringing/created;
 *   - and, when the viewer is known, addressed to them as the callee.
 */
export function isIncomingRingingCall(
  call: PulseCall | null | undefined,
  currentUserId: number | undefined,
  ignoredCallIds: { has(callId: string): boolean }
): boolean {
  if (!call || !call.call_id || ignoredCallIds.has(call.call_id)) return false;
  if (!INBOUND_RINGING_STATES.has(String(call.status || ""))) return false;
  if (!currentUserId) return true;
  const participant: PulseCallParticipant | undefined =
    call.participant || (call.participants || []).find((item) => Number(item.user_id) === Number(currentUserId));
  if (!participant) return true;
  return (
    String(participant.role || "").toLowerCase() === "callee" ||
    String(participant.status || "").toLowerCase() === "ringing"
  );
}
