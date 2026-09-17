import { PulseApiError, pulseApi } from "./pulseApi";

/**
 * The owner's music takedown authority, from the phone.
 *
 * Every function here is a thin wrapper over a server endpoint that re-resolves
 * the caller and re-checks its own named permission. Nothing in this file is a
 * gate. `fetchMusicCapabilities` exists so the UI can be drawn from a server
 * answer instead of a guess about the signed-in role, but a build that ignored
 * it entirely and called `takedownMusicTrack` directly would be refused by the
 * server exactly as it is now — that is the property that makes it safe to ship
 * this module to every user rather than only to owners.
 *
 * Failures are read off `PulseApiError.code`, which `pulseApi` populates from
 * the body's `error_code`. The server answers a handful of refusals that mean
 * genuinely different things to a person — a stale state, a missing step-up, a
 * legal hold — and collapsing them into one "something went wrong" would leave
 * the owner unable to tell "try again" from "this is blocked on purpose".
 */

export const MUSIC_PERMISSIONS = [
  "music.view_all",
  "music.moderate",
  "music.takedown",
  "music.restore",
  "music.purge",
  "music.manage_rights"
] as const;

export type MusicPermission = (typeof MUSIC_PERMISSIONS)[number];

export type MusicLifecycleState =
  | "ACTIVE"
  | "TAKEN_DOWN"
  | "QUARANTINED"
  | "PURGE_PENDING"
  | "PURGED";

export type MusicCapabilities = {
  /** Whether to draw the owner section at all. */
  hasAuthority: boolean;
  permissions: Record<MusicPermission, boolean>;
  /** The server's own vocabulary, so this client does not keep a second copy. */
  states: string[];
  reasonCodes: string[];
  stepUpTtlSeconds: number;
};

export type MusicTrackImpact = {
  trackId: number;
  title: string;
  artist: string;
  uploaderUserId: number;
  state: MusicLifecycleState;
  legalHold: boolean;
  reasonCode: string;
  reasonNote: string;
  purgeScheduledAt: string;
  purgedAt: string;
  references: { reels: number; content: number; statuses: number; total: number };
  openReports: number;
  playCount: number;
  usageCount: number;
  /**
   * The honest CDN caveat, carried through from the server rather than
   * reworded here. R2 serves these objects `immutable` for a year, so a
   * takedown stops the server handing the url out but does not reach a client
   * that already has it. Only a purge deletes the bytes.
   */
  cachedCopiesRemainUntilPurge: boolean;
  reasonCodes: string[];
};

export type MusicMutationResult = {
  trackId: number;
  previousState: MusicLifecycleState;
  state: MusicLifecycleState;
  /**
   * False when the action had already been applied. The server answers 200 for
   * this rather than erroring, so a retried request does not report a failure
   * for work that succeeded; the UI should say "already taken down", not
   * "takedown failed".
   */
  changed: boolean;
  actionId: number;
  affectedReferenceCount: number;
};

export type MusicMutationResponse = {
  action: string;
  requestId: string;
  changed: boolean;
  results: MusicMutationResult[];
  deletedObjectCount: number;
};

export type MusicAuditEntry = {
  actionId: number;
  trackId: number;
  action: string;
  previousState: string;
  newState: string;
  actorUserId: number;
  actorRole: string;
  reasonCode: string;
  reasonNote: string;
  affectedReferenceCount: number;
  requestId: string;
  createdAt: string;
  restoredAt: string;
  relatedActionId: number;
};

export type MusicReasonCode =
  | "COPYRIGHT"
  | "LICENSING_EXPIRED"
  | "POLICY_VIOLATION"
  | "UNAUTHORIZED_UPLOAD"
  | "DUPLICATE"
  | "MALWARE_OR_UNSAFE_FILE"
  | "PRIVACY_REQUEST"
  | "UPLOADER_REQUEST"
  | "OWNER_DECISION"
  | "OTHER";

export type MusicMutationInput = {
  reasonCode: MusicReasonCode | string;
  reasonNote?: string;
  /**
   * The state the owner believed the track was in when they decided. The server
   * answers 409 if it has moved since, which is what stops a second moderator's
   * restore from being silently undone by a takedown decided against the older
   * screen.
   */
  expectedState?: MusicLifecycleState;
  /** Apply to several tracks in one transaction. */
  trackIds?: number[];
};

const NO_PERMISSIONS: Record<MusicPermission, boolean> = MUSIC_PERMISSIONS.reduce(
  (acc, name) => {
    acc[name] = false;
    return acc;
  },
  {} as Record<MusicPermission, boolean>
);

function num(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

/**
 * Ask the server what this account may do.
 *
 * Reads each permission by name rather than trusting the response's own
 * `music_authority` summary for the per-button decisions: the summary answers
 * "draw the section", while an account may hold `music.takedown` and not
 * `music.purge`, and a UI that treated the summary as a master switch would
 * show a purge button that always fails.
 *
 * A failure resolves to "no authority" rather than throwing. The caller is a
 * screen deciding whether to render an extra section, and there is no useful
 * thing for it to do with an error here — but note this is *only* safe because
 * the answer grants nothing. If this response ever became load-bearing for
 * anything but visibility, swallowing the error would become a real hole.
 */
export async function fetchMusicCapabilities(): Promise<MusicCapabilities> {
  try {
    const data = await pulseApi<{
      music_authority?: boolean;
      permissions?: Record<string, boolean>;
      states?: string[];
      reason_codes?: string[];
      step_up_ttl_seconds?: number;
    }>("/api/admin/music/capabilities");
    const granted = data.permissions || {};
    const permissions = MUSIC_PERMISSIONS.reduce((acc, name) => {
      acc[name] = Boolean(granted[name]);
      return acc;
    }, {} as Record<MusicPermission, boolean>);
    return {
      hasAuthority: Boolean(data.music_authority),
      permissions,
      states: Array.isArray(data.states) ? data.states.map(text) : [],
      reasonCodes: Array.isArray(data.reason_codes) ? data.reason_codes.map(text) : [],
      stepUpTtlSeconds: num(data.step_up_ttl_seconds) || 300
    };
  } catch {
    return {
      hasAuthority: false,
      permissions: { ...NO_PERMISSIONS },
      states: [],
      reasonCodes: [],
      stepUpTtlSeconds: 300
    };
  }
}

/** What removing this track would touch, asked before anything is removed. */
export async function fetchMusicTrackImpact(trackId: number | string): Promise<MusicTrackImpact> {
  const data = await pulseApi<{
    track?: Record<string, unknown>;
    references?: Record<string, unknown>;
    open_reports?: number;
    play_count?: number;
    usage_count?: number;
    cached_copies_remain_until_purge?: boolean;
    reason_codes?: string[];
  }>(`/api/admin/music/tracks/${encodeURIComponent(String(trackId))}/impact`);
  const track = data.track || {};
  const references = data.references || {};
  return {
    trackId: num(track.id),
    title: text(track.title),
    artist: text(track.artist),
    uploaderUserId: num(track.uploader_user_id),
    state: (text(track.state) || "ACTIVE") as MusicLifecycleState,
    legalHold: Boolean(track.legal_hold),
    reasonCode: text(track.reason_code),
    reasonNote: text(track.reason_note),
    purgeScheduledAt: text(track.purge_scheduled_at),
    purgedAt: text(track.purged_at),
    references: {
      reels: num(references.reels),
      content: num(references.content),
      statuses: num(references.statuses),
      total: num(references.total)
    },
    openReports: num(data.open_reports),
    playCount: num(data.play_count),
    usageCount: num(data.usage_count),
    cachedCopiesRemainUntilPurge: Boolean(data.cached_copies_remain_until_purge),
    reasonCodes: Array.isArray(data.reason_codes) ? data.reason_codes.map(text) : []
  };
}

function mutationBody(input: MusicMutationInput, extra: Record<string, unknown> = {}) {
  const body: Record<string, unknown> = { reason_code: input.reasonCode, ...extra };
  if (input.reasonNote) body.reason_note = input.reasonNote;
  if (input.expectedState) body.expected_state = input.expectedState;
  if (input.trackIds && input.trackIds.length) body.track_ids = input.trackIds;
  return body;
}

async function mutate(
  trackId: number | string,
  action: string,
  body: Record<string, unknown>
): Promise<MusicMutationResponse> {
  const data = await pulseApi<{
    action?: string;
    request_id?: string;
    changed?: boolean;
    results?: Record<string, unknown>[];
    deleted_object_count?: number;
  }>(`/api/admin/music/tracks/${encodeURIComponent(String(trackId))}/${action}`, {
    method: "POST",
    body: JSON.stringify(body)
  });
  return {
    action: text(data.action) || action,
    requestId: text(data.request_id),
    changed: Boolean(data.changed),
    deletedObjectCount: num(data.deleted_object_count),
    results: (data.results || []).map((row) => ({
      trackId: num(row.track_id),
      previousState: (text(row.previous_state) || "ACTIVE") as MusicLifecycleState,
      state: (text(row.state) || "ACTIVE") as MusicLifecycleState,
      changed: Boolean(row.changed),
      actionId: num(row.action_id),
      affectedReferenceCount: num(row.affected_reference_count)
    }))
  };
}

/**
 * Stop serving the track. The posts, Reels and statuses that used it keep their
 * video, caption and engagement; only the sound stops.
 *
 * `quarantine` additionally marks the track for byte deletion, which is the
 * right choice for copyright and malware: a plain takedown leaves anything
 * already cached at the CDN playable.
 */
export function takedownMusicTrack(
  trackId: number | string,
  input: MusicMutationInput & { quarantine?: boolean }
) {
  return mutate(trackId, "takedown", mutationBody(input, input.quarantine ? { quarantine: true } : {}));
}

/** Undo a takedown. Refused once the track has been purged — there is nothing left. */
export function restoreMusicTrack(trackId: number | string, input: MusicMutationInput) {
  return mutate(trackId, "restore", mutationBody(input));
}

export function scheduleMusicTrackPurge(trackId: number | string, input: MusicMutationInput) {
  return mutate(trackId, "schedule-purge", mutationBody(input));
}

export function cancelMusicTrackPurge(trackId: number | string, input: MusicMutationInput) {
  return mutate(trackId, "cancel-purge", mutationBody(input));
}

/**
 * Delete the bytes. Irreversible, and gated accordingly: the track must already
 * be `PURGE_PENDING`, carry no legal hold, and the caller must have re-entered
 * their password within the step-up window.
 *
 * `confirm_track_id` is sent separately from the path id on purpose — it is the
 * one field a caller has to type, and its whole job is to make a purge
 * impossible to issue by muscle memory or by a retry loop replaying a body.
 */
export function purgeMusicTrack(trackId: number | string, input: MusicMutationInput) {
  return mutate(
    trackId,
    "purge",
    mutationBody({ ...input, trackIds: undefined }, { confirm_track_id: String(trackId) })
  );
}

/**
 * Re-prove the password, granting a short server-side window in which a purge
 * may be issued.
 *
 * Kept separate from the purge call so the password is not carried by a request
 * that a client might retry. Returns the window length so the UI can say how
 * long the owner has rather than guessing.
 */
export async function requestMusicStepUp(password: string): Promise<number> {
  const data = await pulseApi<{ expires_in_seconds?: number }>("/api/admin/music/step-up", {
    method: "POST",
    body: JSON.stringify({ password })
  });
  return num(data.expires_in_seconds) || 300;
}

/** The append-only trail. Newest first. */
export async function fetchMusicTrackAudit(trackId: number | string): Promise<MusicAuditEntry[]> {
  const data = await pulseApi<{ entries?: Record<string, unknown>[] }>(
    `/api/admin/music/tracks/${encodeURIComponent(String(trackId))}/audit`
  );
  return (data.entries || []).map((row) => ({
    actionId: num(row.action_id),
    trackId: num(row.track_id),
    action: text(row.action),
    previousState: text(row.previous_state),
    newState: text(row.new_state),
    actorUserId: num(row.actor_user_id),
    actorRole: text(row.actor_role),
    reasonCode: text(row.reason_code),
    reasonNote: text(row.reason_note),
    affectedReferenceCount: num(row.affected_reference_count),
    requestId: text(row.request_id),
    createdAt: text(row.created_at),
    restoredAt: text(row.restored_at),
    relatedActionId: num(row.related_action_id)
  }));
}

/**
 * Turn a refusal into something worth showing a person.
 *
 * The distinctions matter operationally: a 409 means someone else moved the
 * track and the owner should re-read it, a legal hold means the purge is
 * blocked by a decision rather than by a bug, and a missing step-up means one
 * more step rather than a lost permission. Returning the server's own message
 * for anything unrecognised keeps a newly added `error_code` readable instead
 * of collapsing it into a generic failure.
 */
export function describeMusicAuthorityError(error: unknown): string {
  if (!(error instanceof PulseApiError)) {
    return "Something went wrong. Try again.";
  }
  switch (error.code) {
    case "music_authority_required":
      return "Sign in with an account that has music authority.";
    case "music_permission_denied":
      return "This account does not hold that music permission.";
    case "music_state_conflict":
      return "This track changed since you loaded it. Reload and decide again.";
    case "music_transition_refused":
      return "That action is not available from this track's current state.";
    case "music_legal_hold":
      return "A legal hold blocks purging this track. Clear the hold first.";
    case "music_step_up_required":
      return "Confirm your password before purging.";
    case "music_step_up_failed":
      return "That password did not match.";
    case "music_step_up_unavailable":
      return "Set an admin password before using purge.";
    case "music_purge_confirmation_required":
      return "Type the track id to confirm the purge.";
    case "music_reason_code_invalid":
      return "Choose a reason for this decision.";
    case "music_reason_note_required":
      return "Add a short note explaining this decision.";
    case "music_track_not_found":
      return "That track no longer exists.";
    case "music_track_required":
      return "Choose at least one track.";
    case "music_reference_count_unavailable":
      return "Could not read how much content uses this track. Try again before removing anything.";
    default:
      return error.message || "Something went wrong. Try again.";
  }
}
