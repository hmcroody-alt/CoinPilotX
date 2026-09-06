/**
 * Private Meetings — API layer.
 *
 * Every call goes through `pulseApi` (bearer + refresh + cookie) AND carries
 * the Office grant headers from `officeRequestHeaders`: meetings sit behind
 * BOTH locks (session auth and the Private Office second lock, HTTP 423), and
 * this file is the only place a meetings screen talks to the network, so both
 * are structurally impossible to forget.
 *
 * Media note (mission §5/§59): this module NEVER touches Agora. The media
 * token is minted server-side against the meeting's room-scope call, and the
 * canonical call session store fetches its own token when it connects. The
 * `/token` route exists as the meeting-side preflight — it re-checks
 * admission + liveness and marks the participant JOINED — but no token string
 * is ever stored, logged, or passed around by hand here.
 */

import { pulseApi, PulseApiError } from "../../api/pulseApi";
import { officeRequestHeaders } from "../officeLock";
import {
  MeetingArtifact,
  MeetingBuckets,
  MeetingIntelligence,
  MeetingMessage,
  MeetingParticipant,
  MeetingRecording,
  MeetingRefusal,
  MeetingRefusalCode,
  PrivateMeeting
} from "./types";

const BASE = "/api/private-office/meetings";

/** Backend machine codes → screen-facing refusal classes. */
const CODE_MAP: Record<string, MeetingRefusalCode> = {
  feature_disabled: "feature_disabled",
  not_entitled: "feature_disabled",
  office_locked: "office_locked",
  not_found: "not_found",
  not_admitted: "not_admitted",
  meeting_locked: "meeting_locked",
  meeting_over: "meeting_over",
  not_live: "not_live",
  blocked: "blocked",
  forbidden: "forbidden"
};

function toRefusal(error: unknown): MeetingRefusal {
  if (error instanceof PulseApiError) {
    const raw = String(
      (error.details && (error.details.code as string)) || error.code || ""
    );
    let code: MeetingRefusalCode | undefined = CODE_MAP[raw];
    if (!code) {
      if (error.status === 423) code = "office_locked";
      else if (error.status === 404) code = "not_found";
      else if (error.status === 410) code = "meeting_over";
      else if (error.status === 423) code = "office_locked";
      else if (error.status === 403) code = "forbidden";
      else if (error.status >= 500) code = "unavailable";
      else code = "invalid";
    }
    return new MeetingRefusal(code, error.status, error.message);
  }
  return new MeetingRefusal(
    "unavailable",
    0,
    error instanceof Error ? error.message : "Request failed."
  );
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  try {
    const headers = {
      ...(await officeRequestHeaders()),
      ...(init.body ? { "Content-Type": "application/json" } : {})
    };
    return await pulseApi<T>(path, { ...init, headers });
  } catch (error) {
    throw toRefusal(error);
  }
}

function post<T>(path: string, body?: Record<string, unknown>): Promise<T> {
  return call<T>(path, {
    method: "POST",
    ...(body ? { body: JSON.stringify(body) } : {})
  });
}

// --- home: create / schedule / list -----------------------------------------

export async function createInstantMeeting(params: {
  title?: string;
  waitingRoomEnabled?: boolean;
}): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(BASE, {
    instant: true,
    title: params.title || "",
    waiting_room_enabled: params.waitingRoomEnabled !== false
  });
  return data.meeting;
}

export async function scheduleMeeting(params: {
  title?: string;
  scheduledStartAt: string;
  durationMinutes?: number;
  waitingRoomEnabled?: boolean;
}): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(BASE, {
    title: params.title || "",
    scheduled_start_at: params.scheduledStartAt,
    duration_minutes: params.durationMinutes || 0,
    waiting_room_enabled: params.waitingRoomEnabled !== false
  });
  return data.meeting;
}

export async function listMeetings(limit = 20): Promise<MeetingBuckets> {
  const data = await call<{ meetings: MeetingBuckets }>(
    `${BASE}?limit=${Math.max(1, Math.min(limit, 50))}`
  );
  return {
    live: data.meetings?.live || [],
    upcoming: data.meetings?.upcoming || [],
    recent: data.meetings?.recent || []
  };
}

export async function getMeeting(ref: string): Promise<PrivateMeeting> {
  const data = await call<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}`
  );
  return data.meeting;
}

// --- lifecycle ---------------------------------------------------------------

export async function startMeeting(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/start`
  );
  return data.meeting;
}

export async function cancelMeeting(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/cancel`
  );
  return data.meeting;
}

/** Join by public id OR meeting code — the server resolves either. */
export async function joinMeeting(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/join`
  );
  return data.meeting;
}

/** Leave — me only. Idempotent; never ends the meeting for anyone else. */
export async function leaveMeeting(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/leave`
  );
  return data.meeting;
}

/** End for everyone — moderator only, idempotent, ends the room call. */
export async function endMeetingForEveryone(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/end`
  );
  return data.meeting;
}

export async function setMeetingLocked(ref: string, locked: boolean): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/lock`,
    { locked }
  );
  return data.meeting;
}

export async function rotateMeetingCode(ref: string): Promise<PrivateMeeting> {
  const data = await post<{ meeting: PrivateMeeting }>(
    `${BASE}/${encodeURIComponent(ref)}/rotate-code`
  );
  return data.meeting;
}

// --- moderation --------------------------------------------------------------

export async function admitParticipant(ref: string, userId: number): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/admit`,
    { user_id: userId }
  );
  return data.participant;
}

export async function denyParticipant(ref: string, userId: number): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/deny`,
    { user_id: userId }
  );
  return data.participant;
}

export async function removeParticipant(ref: string, userId: number): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/remove`,
    { user_id: userId }
  );
  return data.participant;
}

export async function setParticipantRole(
  ref: string,
  userId: number,
  role: "CO_HOST" | "PARTICIPANT"
): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/role`,
    { user_id: userId, role }
  );
  return data.participant;
}

// --- invites -----------------------------------------------------------------

export async function inviteUsers(
  ref: string,
  userIds: number[],
  message = ""
): Promise<{ invited: number[] }> {
  return post(`${BASE}/${encodeURIComponent(ref)}/invites`, {
    user_ids: userIds,
    message
  });
}

export async function respondToInvite(ref: string, accept: boolean): Promise<unknown> {
  const data = await post<{ result: unknown }>(
    `${BASE}/${encodeURIComponent(ref)}/invites/respond`,
    { accept }
  );
  return data.result;
}

// --- in-meeting: chat, reactions, raise hand ---------------------------------

export async function sendMeetingMessage(
  ref: string,
  body: string,
  kind: "text" | "reaction" = "text"
): Promise<MeetingMessage> {
  const data = await post<{ message: MeetingMessage }>(
    `${BASE}/${encodeURIComponent(ref)}/messages`,
    { body, kind }
  );
  return data.message;
}

export async function listMeetingMessages(
  ref: string,
  sinceId = 0,
  limit = 50
): Promise<MeetingMessage[]> {
  const data = await call<{ messages: MeetingMessage[] }>(
    `${BASE}/${encodeURIComponent(ref)}/messages?since_id=${sinceId}&limit=${limit}`
  );
  return data.messages || [];
}

export async function setRaisedHand(ref: string, raised: boolean): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/hand`,
    { raised }
  );
  return data.participant;
}

// --- presence + media preflight ----------------------------------------------

/**
 * Meeting-side media preflight. Confirms admission + liveness against BOTH
 * authorities (meeting projection, then engine row) and marks this
 * participant JOINED. The token in the response is intentionally discarded:
 * the canonical call session store mints its own on connect, and holding a
 * second copy here would only create a place for it to leak.
 */
export async function preflightMeetingMedia(ref: string): Promise<MeetingParticipant | null> {
  const data = await post<{ me: MeetingParticipant | null }>(
    `${BASE}/${encodeURIComponent(ref)}/token`
  );
  return data.me || null;
}

export async function reportReconnecting(ref: string): Promise<MeetingParticipant> {
  const data = await post<{ participant: MeetingParticipant }>(
    `${BASE}/${encodeURIComponent(ref)}/reconnecting`
  );
  return data.participant;
}

// --- recording (metadata authority) ------------------------------------------

export async function startMeetingRecording(ref: string): Promise<MeetingRecording> {
  const data = await post<{ recording: MeetingRecording }>(
    `${BASE}/${encodeURIComponent(ref)}/recording/start`
  );
  return data.recording;
}

export async function stopMeetingRecording(ref: string): Promise<MeetingRecording> {
  const data = await post<{ recording: MeetingRecording }>(
    `${BASE}/${encodeURIComponent(ref)}/recording/stop`
  );
  return data.recording;
}

// --- artifacts (UNDX save-to-office; provenance is mandatory) -----------------

export async function saveMeetingArtifact(
  ref: string,
  artifact: {
    artifactType: string;
    provenance: string;
    title: string;
    content: string;
    evidenceRefs?: string;
  }
): Promise<MeetingArtifact> {
  const data = await post<{ artifact: MeetingArtifact }>(
    `${BASE}/${encodeURIComponent(ref)}/artifacts`,
    {
      artifact_type: artifact.artifactType,
      provenance: artifact.provenance,
      title: artifact.title,
      content: artifact.content,
      evidence_refs: artifact.evidenceRefs || ""
    }
  );
  return data.artifact;
}

export async function listMeetingArtifacts(ref: string, limit = 20): Promise<MeetingArtifact[]> {
  const data = await call<{ artifacts: MeetingArtifact[] }>(
    `${BASE}/${encodeURIComponent(ref)}/artifacts?limit=${limit}`
  );
  return data.artifacts || [];
}

/**
 * Server-computed meeting intelligence (§32-36). Deterministic SYSTEM_FACT
 * lines only — the server has no model call on this path, so nothing here can
 * describe what was said, only what happened. The returned draft is a
 * proposal: saving it goes through saveMeetingArtifact with USER_CONFIRMED
 * provenance after the human has reviewed it.
 */
export async function getMeetingIntelligence(ref: string): Promise<MeetingIntelligence> {
  const data = await call<{ intelligence: MeetingIntelligence }>(
    `${BASE}/${encodeURIComponent(ref)}/intelligence`
  );
  return data.intelligence;
}
