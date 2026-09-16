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
  /** Canonical UTC. Render it with `scheduled_timezone`, never on its own. */
  scheduled_start_at: string;
  /** The zone the host chose. Empty on rows written before it was stored. */
  scheduled_timezone: string;
  /** Bumped by every reschedule; reminders for older versions are dropped. */
  schedule_version: number;
  agenda: string;
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
  /**
   * Present only on the response to a create-or-invite call — never on a read.
   *
   * It reports what the server actually did with the guests it was sent, which
   * is not always what was asked: an address belonging to a member is invited
   * as that member and appears in `invited` rather than `invited_contacts`, and
   * anything unusable lands in `skipped` with a reason. The confirmation screen
   * renders this rather than the list the host typed, so "invited" means the
   * server invited them.
   */
  invite_result?: MeetingInviteResult;
  /**
   * Also create-only: the reminder rows that actually exist, not the ladder.
   *
   * The server's default is 24h / 1h / 15m, but planning them is deliberately
   * non-fatal — a reminder store that is down degrades a meeting rather than
   * cancelling one, so the ladder and the rows can disagree. A confirmation
   * screen reciting the ladder from a constant would promise three mails in
   * precisely the case where none were scheduled. Absent means the server did
   * not say; empty means it said none.
   */
  reminders?: MeetingReminder[];
};

/** One planned reminder. `status` is the backend's vocabulary, verbatim. */
export type MeetingReminder = {
  /** Minutes before the start instant. */
  offset_minutes: number;
  send_at: string;
  /**
   * PENDING / SENDING / SENT / FAILED / BOUNCED / SKIPPED / CANCELLED.
   *
   * SKIPPED is not a failure and not a silence: the offset had already elapsed
   * when the meeting was booked (a meeting twenty minutes out cannot honour a
   * 24h reminder). It is reported so the host is told that one is not coming
   * rather than left to infer it.
   */
  status: string;
};

export type MeetingInviteResult = {
  /** Member user ids that now hold an invitation. */
  invited: number[];
  /**
   * The same members, with the labels the host supplied.
   *
   * Same length and order as `invited` — one is the identity, one is what can
   * be shown. A confirmation built from `invited` alone could only say "1
   * member invited", which is exactly the summary that lets a wrong address
   * through unread. `email` is the address the host typed, read back; it is
   * empty on a replayed booking, because the invite row stores an address only
   * for a guest who has no account.
   */
  invited_members: { user_id: number; email: string; name: string }[];
  /** Guests with no account, as stored: the normalized address and a label. */
  invited_contacts: { email: string; name: string }[];
  /** Everyone not invited, each with a machine reason. */
  skipped: { user_id?: number; email?: string; name?: string; reason: string }[];
};

export type MeetingBuckets = {
  live: PrivateMeeting[];
  upcoming: PrivateMeeting[];
  recent: PrivateMeeting[];
};

/**
 * One meeting as a calendar cell sees it — deliberately not a `PrivateMeeting`.
 *
 * `calendar_range` returns a summary, never the full projection: no
 * `meeting_code`, no `call_public_id`, no participant list. A month view that
 * carried host-only codes for forty meetings would be leaking them forty at a
 * time to anyone who could read the response.
 */
export type MeetingCalendarEntry = {
  public_id: string;
  title: string;
  status: MeetingStatus;
  scheduled_start_at: string;
  scheduled_timezone: string;
  duration_minutes: number;
  /** `"YYYY-MM-DD"` in the zone the window was requested in. */
  local_day: string;
  is_host: boolean;
};

export type MeetingCalendarWindow = {
  start: string;
  end: string;
  timezone: string;
  /** `"YYYY-MM-DD"` → count. The indicator dots come from here, not from
   *  re-bucketing `meetings` client-side in some other timezone. */
  days: Record<string, number>;
  meetings: MeetingCalendarEntry[];
  /** The server hit its row cap. Say so rather than silently showing less. */
  truncated: boolean;
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
