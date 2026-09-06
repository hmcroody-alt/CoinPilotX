/**
 * Private Meetings — session store.
 *
 * PulseSoc is the meeting authority; Agora is transport (mission §4-§5). This
 * module owns MEETING state only — admission, waiting room, roster, lifecycle
 * — and delegates every media concern to the canonical call session store,
 * which remains the single Agora engine owner in the app (§3: zero new
 * ungoverned owners; §59: reuse first, never a second participant registry
 * for media and never a copied call implementation).
 *
 * How the two stores divide the world:
 *
 *  - This store decides WHETHER this user may be in the room (projection
 *    polling: waiting room, admission, removal, meeting end, lock, roles).
 *  - `callSessionStore` decides HOW media flows once they may
 *    (`beginCallSession` with the meeting's room-scope call id; it mints its
 *    own token, and the engine refuses anyone the meeting layer has not
 *    admitted — the waiting room is a missing row, not a hidden button).
 *
 * Leave vs end-for-everyone (§42-§43) is why the meeting UI must NEVER call
 * `hangupCallSession`: the engine's end endpoint has direct-call semantics
 * (creator hang-up or <2 active participants ends the call for everyone).
 * Meetings end through their own routes, and the local media teardown is
 * `finalizeCallSession` — teardown WITHOUT touching the engine's end
 * endpoint. If some other actor tears the call session down anyway (CallKit's
 * end button, a terminal 410 from a status poll after host-removal), the
 * subscription below notices and records the meeting-side leave so the roster
 * stays truthful.
 */

import { useSyncExternalStore } from "react";
import {
  beginCallSession,
  clearCallSession,
  finalizeCallSession,
  getCallSession,
  subscribeCallSession
} from "../../calls/callSessionStore";
import {
  getMeeting,
  joinMeeting,
  leaveMeeting,
  endMeetingForEveryone,
  preflightMeetingMedia,
  reportReconnecting
} from "./api";
import { MeetingRefusal, PrivateMeeting } from "./types";

/** Projection refresh cadence while waiting for admission. */
export const WAITING_ROOM_POLL_MS = 2500;
/** Projection refresh cadence while in the meeting (roster/lock/recording). */
export const IN_MEETING_POLL_MS = 5000;

export type MeetingPhase =
  | "idle"
  | "joining"
  | "waiting_room"
  | "connecting"
  | "in_meeting"
  | "ended";

export type MeetingSessionSnapshot = {
  phase: MeetingPhase;
  /** The meeting ref this session is bound to ("" when idle). */
  meetingRef: string;
  /** Latest authoritative projection from the backend. */
  meeting: PrivateMeeting | null;
  /** Why the session ended (server end_reason or a local teardown class). */
  endedReason: string;
  /** Machine code of the last refusal ("" when none). */
  refusalCode: string;
};

const initialSnapshot: MeetingSessionSnapshot = {
  phase: "idle",
  meetingRef: "",
  meeting: null,
  endedReason: "",
  refusalCode: ""
};

let snapshot: MeetingSessionSnapshot = { ...initialSnapshot };
const listeners = new Set<() => void>();

let pollTimer: ReturnType<typeof setTimeout> | null = null;
let pollInFlight = false;
let unsubscribeCallStore: (() => void) | null = null;
/** Call id we handed to the call store; "" until media is requested. */
let boundCallId = "";
/** True while WE are tearing down, so the subscription does not double-fire. */
let teardownByUs = false;
/** Dedupe for the RECONNECTING presence report (one per episode). */
let reconnectReported = false;

function emit() {
  for (const listener of Array.from(listeners)) listener();
}

function setSnapshot(patch: Partial<MeetingSessionSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  emit();
}

export function getMeetingSession(): MeetingSessionSnapshot {
  return snapshot;
}

export function subscribeMeetingSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useMeetingSession(): MeetingSessionSnapshot {
  return useSyncExternalStore(subscribeMeetingSession, getMeetingSession, getMeetingSession);
}

// --- polling -----------------------------------------------------------------

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function schedulePoll() {
  stopPolling();
  if (snapshot.phase !== "waiting_room" && snapshot.phase !== "in_meeting") return;
  const delay = snapshot.phase === "waiting_room" ? WAITING_ROOM_POLL_MS : IN_MEETING_POLL_MS;
  pollTimer = setTimeout(() => {
    refreshMeetingProjection().catch(() => undefined);
  }, delay);
}

/** One authoritative projection fetch; deduped like the call store's poll. */
export async function refreshMeetingProjection(): Promise<PrivateMeeting | null> {
  if (!snapshot.meetingRef || pollInFlight) return snapshot.meeting;
  pollInFlight = true;
  try {
    const meeting = await getMeeting(snapshot.meetingRef);
    adoptMeetingProjection(meeting);
    return meeting;
  } catch (error) {
    if (error instanceof MeetingRefusal) {
      // 404 after being in (or queued for) a meeting means the projection no
      // longer admits our existence: removed, denied, or blocked. 410 means
      // the meeting itself is over. Both are terminal for this session.
      if (error.code === "not_found") return endLocally("removed"), null;
      if (error.code === "meeting_over" || error.code === "feature_disabled") {
        return endLocally(error.code === "meeting_over" ? "ended" : "feature_disabled"), null;
      }
    }
    // Transient (network, 503, office re-lock): keep the phase, retry later.
    return null;
  } finally {
    pollInFlight = false;
    schedulePoll();
  }
}

/** Merge an authoritative projection and act on any transition it implies. */
export function adoptMeetingProjection(meeting: PrivateMeeting | null | undefined) {
  if (!meeting || !meeting.public_id) return;
  if (snapshot.meetingRef && meeting.public_id !== snapshot.meetingRef) return;
  setSnapshot({ meeting, meetingRef: meeting.public_id });

  const status = meeting.status;
  if (status === "ENDED" || status === "CANCELLED" || status === "FAILED") {
    endLocally(meeting.end_reason || status.toLowerCase());
    return;
  }

  const myState = meeting.me?.state || "";
  if (myState === "REMOVED" || myState === "BLOCKED" || myState === "DECLINED") {
    endLocally("removed");
    return;
  }

  if (snapshot.phase === "waiting_room" && meeting.me && myState !== "WAITING_ROOM") {
    // Admitted while we were parked — the projection now names the call.
    connectMedia(meeting).catch(() => undefined);
    return;
  }

  if (snapshot.phase === "in_meeting") {
    maybeRestoreJoinedPresence(meeting);
  }
}

// --- media handoff -----------------------------------------------------------

/**
 * Hand the admitted meeting to the canonical call store. The preflight route
 * re-checks BOTH authorities server-side and marks us JOINED; the call store
 * then mints its own token on connect. We keep no token here, ever.
 */
async function connectMedia(meeting: PrivateMeeting): Promise<void> {
  const callId = String(meeting.call_public_id || "");
  if (!callId) {
    // Admitted but the meeting is not LIVE yet (scheduled, not started).
    setSnapshot({ phase: "waiting_room", meeting });
    schedulePoll();
    return;
  }
  setSnapshot({ phase: "connecting", meeting });
  try {
    await preflightMeetingMedia(meeting.public_id);
  } catch (error) {
    if (error instanceof MeetingRefusal && error.code !== "unavailable") {
      endLocally(error.code);
      return;
    }
    // Transient: the call store's own join will still be refused server-side
    // if we are truly not admitted, so failing open here is safe.
  }
  teardownByUs = false;
  reconnectReported = false;
  boundCallId = callId;
  beginCallSession({
    callId,
    callType: "video",
    title: meeting.title || ""
  });
  ensureCallStoreSubscription();
  setSnapshot({ phase: "in_meeting" });
  schedulePoll();
}

/**
 * Watch the canonical store for teardown we did not initiate (CallKit end,
 * terminal 410 after host-removal or end-for-everyone, replaced session) and
 * for reconnect episodes worth reporting as RECONNECTING presence.
 */
function ensureCallStoreSubscription() {
  if (unsubscribeCallStore) return;
  unsubscribeCallStore = subscribeCallSession(() => {
    if (snapshot.phase !== "in_meeting" && snapshot.phase !== "connecting") return;
    const call = getCallSession();

    if (!call.sessionActive || (call.callId && boundCallId && call.callId !== boundCallId)) {
      if (teardownByUs) return;
      // Someone else ended the media session. Record the meeting-side leave
      // (idempotent) so the roster never shows a ghost, then end locally.
      const ref = snapshot.meetingRef;
      if (ref) leaveMeeting(ref).catch(() => undefined);
      endLocally("call_ended");
      return;
    }

    if (call.reconnecting && !reconnectReported) {
      reconnectReported = true;
      const ref = snapshot.meetingRef;
      if (ref) reportReconnecting(ref).catch(() => undefined);
    } else if (!call.reconnecting && call.connected && reconnectReported) {
      reconnectReported = false;
      // Restore JOINED presence after the episode (same logical row rejoins).
      const ref = snapshot.meetingRef;
      if (ref) preflightMeetingMedia(ref).catch(() => undefined);
    }
  });
}

/** Poll-side presence repair: server shows RECONNECTING but media is fine. */
function maybeRestoreJoinedPresence(meeting: PrivateMeeting) {
  const call = getCallSession();
  if (meeting.me?.state === "RECONNECTING" && call.connected && !call.reconnecting) {
    preflightMeetingMedia(meeting.public_id).catch(() => undefined);
  }
}

// --- entry points ------------------------------------------------------------

/**
 * Join by public id or meeting code. Routes by the server's answer:
 * waiting room → park and poll; admitted + live → connect media;
 * admitted + not yet live → park and poll (host will start it).
 */
export async function enterMeeting(ref: string): Promise<MeetingSessionSnapshot> {
  resetMeetingSession();
  setSnapshot({ phase: "joining", meetingRef: String(ref || "") });
  try {
    const meeting = await joinMeeting(ref);
    // join resolves codes; rebind to the canonical public id.
    setSnapshot({ meetingRef: meeting.public_id, meeting });
    const myState = meeting.me?.state || "";
    if (myState === "WAITING_ROOM" || !meeting.call_public_id) {
      setSnapshot({ phase: "waiting_room" });
      schedulePoll();
    } else {
      await connectMedia(meeting);
    }
    return snapshot;
  } catch (error) {
    const code = error instanceof MeetingRefusal ? error.code : "unavailable";
    setSnapshot({ phase: "idle", refusalCode: code });
    throw error;
  }
}

/** A screen already holds a fresh projection (e.g. right after create). */
export async function enterMeetingWithProjection(meeting: PrivateMeeting): Promise<void> {
  resetMeetingSession();
  setSnapshot({
    phase: "joining",
    meetingRef: meeting.public_id,
    meeting
  });
  const myState = meeting.me?.state || "";
  if (myState === "WAITING_ROOM" || !meeting.call_public_id) {
    setSnapshot({ phase: "waiting_room" });
    schedulePoll();
    return;
  }
  await connectMedia(meeting);
}

/** Leave — me only (§42). Never ends the meeting for anyone else. */
export async function leaveCurrentMeeting(): Promise<void> {
  const ref = snapshot.meetingRef;
  teardownByUs = true;
  if (ref) {
    try {
      await leaveMeeting(ref);
    } catch {
      // Idempotent server-side; the sweep finalizes if this never lands.
    }
  }
  finalizeCallSession("meeting_left");
  clearCallSession();
  endLocally("left");
}

/** End for everyone — moderator only (§42). The server ends the room call. */
export async function endCurrentMeetingForEveryone(): Promise<void> {
  const ref = snapshot.meetingRef;
  teardownByUs = true;
  if (ref) {
    await endMeetingForEveryone(ref);
  }
  finalizeCallSession("meeting_ended");
  clearCallSession();
  endLocally("ended_by_host");
}

/** Waiting-room occupant backs out before any admission decision. */
export async function withdrawFromWaitingRoom(): Promise<void> {
  const ref = snapshot.meetingRef;
  teardownByUs = true;
  if (ref) {
    try {
      await leaveMeeting(ref);
    } catch {
      // Best effort; there is no media session to tear down.
    }
  }
  endLocally("left");
}

// --- teardown ----------------------------------------------------------------

function endLocally(reason: string) {
  stopPolling();
  if (unsubscribeCallStore) {
    unsubscribeCallStore();
    unsubscribeCallStore = null;
  }
  boundCallId = "";
  reconnectReported = false;
  setSnapshot({ phase: "ended", endedReason: String(reason || "ended") });
}

/** Full reset to idle (leaving the summary screen, or starting fresh). */
export function resetMeetingSession() {
  stopPolling();
  if (unsubscribeCallStore) {
    unsubscribeCallStore();
    unsubscribeCallStore = null;
  }
  boundCallId = "";
  teardownByUs = false;
  reconnectReported = false;
  snapshot = { ...initialSnapshot };
  emit();
}
