/**
 * Private Meetings — wire types.
 *
 * These mirror the projections in `services/private_office/meetings.py`
 * exactly. The backend is the only authority on meeting state; nothing in
 * this folder invents a field the server did not send. Two projection rules
 * matter to the UI and are worth restating here:
 *
 *  - `meeting_code` exists ONLY on the host's projection. A guest cannot
 *    render (or leak) an invite code it never received.
 *  - `call_public_id` / `channel_name` exist ONLY once the viewer is
 *    admitted. A waiting-room occupant's projection cannot even name the
 *    media channel, so there is nothing client-side to "unlock" early.
 */

export type MeetingStatus =
  | "DRAFT"
  | "SCHEDULED"
  | "STARTING"
  | "WAITING"
  | "LIVE"
  | "ENDING"
  | "ENDED"
  | "CANCELLED"
  | "FAILED";

export type MeetingParticipantState =
  | "INVITED"
  | "RINGING"
  | "WAITING_ROOM"
  | "ADMITTED"
  | "JOINING"
  | "JOINED"
  | "RECONNECTING"
  | "LEFT"
  | "REMOVED"
  | "DECLINED"
  | "EXPIRED"
  | "BLOCKED";

export type MeetingRole = "HOST" | "CO_HOST" | "PARTICIPANT";

/** States that mean "this person is (or may momentarily be) in the room". */
export const PRESENT_STATES: ReadonlySet<MeetingParticipantState> = new Set([
  "ADMITTED",
  "JOINING",
  "JOINED",
  "RECONNECTING"
]);

/** States the projection reports once the viewer has been admitted. */
export const ADMITTED_STATES: ReadonlySet<MeetingParticipantState> = new Set([
  "ADMITTED",
  "JOINING",
  "JOINED",
  "RECONNECTING"
]);

export const MODERATOR_ROLES: ReadonlySet<MeetingRole> = new Set(["HOST", "CO_HOST"]);

export type MeetingParticipant = {
  user_id: number;
  role: MeetingRole;
  state: MeetingParticipantState;
  /** The Agora uid IS the PulseSoc user id — never a parallel identity. */
  rtc_uid: number;
  raised_hand: boolean;
  raised_hand_at: string;
  joined_at: string;
  left_at: string;
};

/** Truthful availability, asserted by the server. UI renders exactly this. */
export type MeetingCapability = {
  available: boolean;
  reason: string;
};

export type MeetingCapabilities = {
  screen_share: MeetingCapability;
  captions: MeetingCapability;
  recording: MeetingCapability;
};

export type PrivateMeeting = {
  public_id: string;
  title: string;
  status: MeetingStatus;
  waiting_room_enabled: boolean;
  locked: boolean;
  scheduled_start_at: string;
  duration_minutes: number;
  started_at: string;
  ended_at: string;
  end_reason: string;
  owner_user_id: number;
  me: MeetingParticipant | null;
  participants: MeetingParticipant[];
  recording_active: boolean;
  capabilities: MeetingCapabilities;
  created_at: string;
  /** Host-only. */
  meeting_code?: string;
  code_rotated_at?: string;
  /** Admitted-only. */
  call_public_id?: string;
  channel_name?: string;
};

export type MeetingBuckets = {
  live: PrivateMeeting[];
  upcoming: PrivateMeeting[];
  recent: PrivateMeeting[];
};

export type MeetingMessageKind = "text" | "reaction" | "system";

export type MeetingMessage = {
  id: number;
  meeting_id: number;
  sender_user_id: number;
  kind: MeetingMessageKind;
  body: string;
  created_at: string;
};

export type MeetingRecording = {
  id: number;
  status: string;
  started_by_user_id: number;
  started_at: string;
  stopped_at: string;
};

/**
 * Provenance is the backend's vocabulary, verbatim. TRANSCRIPT_DERIVED is
 * structurally refused server-side (409 transcript_unavailable) while no
 * transcription provider exists — the app never sends it.
 */
export type MeetingArtifactProvenance =
  | "TRANSCRIPT_DERIVED"
  | "USER_CONFIRMED"
  | "SYSTEM_FACT";

export type MeetingArtifactType =
  | "SUMMARY"
  | "DECISION"
  | "ACTION"
  | "OBLIGATION"
  | "RISK"
  | "NOTE";

export type MeetingArtifact = {
  id: number;
  meeting_id: number;
  artifact_type: string;
  provenance: MeetingArtifactProvenance;
  title: string;
  content: string;
  evidence_refs: string;
  saved_record_id: number;
  created_at: string;
};

/** One server-computed fact. Always SYSTEM_FACT — the server has no LLM path. */
export type MeetingIntelligenceFact = {
  kind: string;
  text: string;
  provenance: MeetingArtifactProvenance;
};

export type MeetingIntelligence = {
  meeting_ref: string;
  generated_at: string;
  facts: MeetingIntelligenceFact[];
  limitations: {
    transcript_available: boolean;
    note: string;
  };
  draft: {
    artifact_type: MeetingArtifactType;
    title: string;
    content: string;
    /** Always true — the server never auto-saves; a human must confirm. */
    requires_confirmation: boolean;
    save_provenance: MeetingArtifactProvenance;
  };
};

/** Coarse, screen-facing refusal classes mapped from backend responses. */
export type MeetingRefusalCode =
  | "feature_disabled"
  | "office_locked"
  | "not_found"
  | "not_admitted"
  | "meeting_locked"
  | "meeting_over"
  | "not_live"
  | "blocked"
  | "forbidden"
  | "invalid"
  | "unavailable";

export class MeetingRefusal extends Error {
  readonly code: MeetingRefusalCode;
  readonly status: number;

  constructor(code: MeetingRefusalCode, status: number, message: string) {
    super(message);
    this.name = "MeetingRefusal";
    this.code = code;
    this.status = status;
  }
}
