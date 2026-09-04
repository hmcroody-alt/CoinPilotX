/**
 * The guest's journey from "invited" to "on stage", as a pure state machine.
 *
 * A guest joining a Live passes through more states than a viewer ever sees:
 *
 *   INVITED -> ACCEPTED -> PREPARING -> JOINING -> LIVE
 *
 * Each transition exists for a reason that only becomes visible when it is
 * missing:
 *
 *  - PREPARING runs the guest's camera *locally only*. They see themselves,
 *    check their framing and their light, and confirm before anyone else does.
 *    Without this step a guest's first frame on a public broadcast is whatever
 *    the camera happened to be pointing at.
 *
 *  - JOINING is the window between the RTC join call and the first real frame.
 *    The guest is connected but has no media yet. Treating this as "live" is
 *    what produces the empty black tile on the audience's screen — a rectangle
 *    with a name on it and nothing in it, which reads as a broken stream.
 *
 *  - LIVE is only entered once media is actually flowing. It is the single
 *    state in which a tile is rendered to the audience.
 *
 * This module holds no Agora references, no React, and no network calls, so the
 * ordering rules above can be tested exhaustively without a device.
 */

import type { LiveStagePhase } from "./liveParticipantRegistry";

/**
 * The guest's own view of their journey. A superset of LiveStagePhase: the
 * registry only needs to describe people who are already on their way to the
 * stage, whereas the guest themself also has states before and after that.
 */
export type GuestJoinPhase =
  | "idle"
  | "requested"
  | "invited"
  | "accepted"
  | "preparing"
  | "joining"
  | "live"
  | "declined"
  | "removed"
  | "left"
  | "failed";

export type GuestJoinEvent =
  | { type: "invite_received" }
  | { type: "request_sent" }
  | { type: "request_approved" }
  | { type: "invite_accepted" }
  | { type: "invite_declined" }
  | { type: "preview_ready" }
  | { type: "guest_confirmed" }
  | { type: "rtc_joined" }
  | { type: "media_flowing" }
  | { type: "media_lost" }
  | { type: "removed_by_host" }
  | { type: "left_stage" }
  | { type: "failed"; reason: string }
  | { type: "reset" };

export type GuestJoinState = {
  phase: GuestJoinPhase;
  /** Stable invite id when this journey began with a host invite. */
  inviteId: string;
  /** Populated only in the `failed` phase. */
  error: string;
};

export const INITIAL_GUEST_JOIN_STATE: GuestJoinState = {
  phase: "idle",
  inviteId: "",
  error: ""
};

/**
 * Phases in which the guest's camera runs locally but nothing is published.
 * The distinction matters: `preparing` shows the guest to themselves only.
 */
const LOCAL_PREVIEW_PHASES: ReadonlySet<GuestJoinPhase> = new Set(["preparing", "joining", "live"]);

/** Phases in which the guest is publishing into the channel. */
const PUBLISHING_PHASES: ReadonlySet<GuestJoinPhase> = new Set(["joining", "live"]);

/** Terminal phases. Nothing follows these except an explicit reset. */
const TERMINAL_PHASES: ReadonlySet<GuestJoinPhase> = new Set(["declined", "removed", "left", "failed"]);

/**
 * Legal transitions. Written as an explicit table rather than a chain of `if`
 * statements so an illegal jump — "invited" straight to "live", say, which
 * would put a guest on air without ever starting their preview — is impossible
 * to express rather than merely unlikely.
 */
const TRANSITIONS: Record<GuestJoinPhase, Partial<Record<GuestJoinEvent["type"], GuestJoinPhase>>> = {
  idle: {
    invite_received: "invited",
    request_sent: "requested"
  },
  requested: {
    request_approved: "accepted",
    invite_declined: "declined",
    removed_by_host: "removed",
    failed: "failed"
  },
  invited: {
    invite_accepted: "accepted",
    invite_declined: "declined",
    removed_by_host: "removed",
    failed: "failed"
  },
  accepted: {
    // Accepting starts the local preview. It does not start publishing.
    preview_ready: "preparing",
    removed_by_host: "removed",
    left_stage: "left",
    failed: "failed"
  },
  preparing: {
    // The guest confirms what they can see of themselves before anyone else can.
    guest_confirmed: "joining",
    removed_by_host: "removed",
    left_stage: "left",
    failed: "failed"
  },
  joining: {
    rtc_joined: "joining",
    // Only actual media promotes a guest to live. Connection alone does not.
    media_flowing: "live",
    removed_by_host: "removed",
    left_stage: "left",
    failed: "failed"
  },
  live: {
    // Losing media drops back to joining rather than to a black live tile, so
    // the audience sees a reconnecting guest instead of an empty rectangle.
    media_lost: "joining",
    removed_by_host: "removed",
    left_stage: "left",
    failed: "failed"
  },
  declined: {},
  removed: {},
  left: {},
  failed: {}
};

/**
 * Apply an event. Unknown or out-of-order events are ignored rather than
 * throwing: a duplicated realtime event or a late-arriving push must never be
 * able to knock a guest off the stage mid-sentence.
 */
export function guestJoinReducer(state: GuestJoinState, event: GuestJoinEvent): GuestJoinState {
  if (event.type === "reset") return { ...INITIAL_GUEST_JOIN_STATE };
  const next = TRANSITIONS[state.phase]?.[event.type];
  if (!next) return state;
  if (next === state.phase && event.type !== "rtc_joined") return state;
  return {
    ...state,
    phase: next,
    error: event.type === "failed" ? String(event.reason || "").slice(0, 200) : ""
  };
}

/** Whether the guest's local camera should be running. */
export function shouldRunLocalPreview(state: GuestJoinState): boolean {
  return LOCAL_PREVIEW_PHASES.has(state.phase);
}

/**
 * Whether the guest is publishing into the channel.
 *
 * Deliberately narrower than `shouldRunLocalPreview`. In `preparing` the camera
 * is on and nothing leaves the device — that gap is the whole point of the
 * preview step.
 */
export function shouldPublish(state: GuestJoinState): boolean {
  return PUBLISHING_PHASES.has(state.phase);
}

/**
 * Whether the audience should be shown a tile for this guest.
 *
 * The strictest of the three. A guest is only rendered once media is confirmed
 * flowing, which is what keeps an empty black tile off the broadcast.
 */
export function shouldAppearOnStage(state: GuestJoinState): boolean {
  return state.phase === "live";
}

export function isTerminal(state: GuestJoinState): boolean {
  return TERMINAL_PHASES.has(state.phase);
}

/**
 * The guest's phase expressed in the registry's vocabulary, so the local guest
 * and every remote participant are described by one set of words.
 */
export function toStagePhase(phase: GuestJoinPhase): LiveStagePhase {
  switch (phase) {
    case "invited":
      return "invited";
    case "requested":
    case "accepted":
      return "accepted";
    case "preparing":
      return "preparing";
    case "joining":
      return "joining";
    case "live":
      return "live";
    default:
      return "left";
  }
}

/**
 * i18n key describing what the guest is waiting for. Returning a key rather
 * than a string keeps this module free of copy and satisfies the hardcoded
 * string gate.
 */
export function guestWaitingStateKey(state: GuestJoinState): string {
  switch (state.phase) {
    case "invited":
      return "live.guest.state.invited";
    case "requested":
      return "live.guest.state.requested";
    case "accepted":
      return "live.guest.state.accepted";
    case "preparing":
      return "live.guest.state.preparing";
    case "joining":
      return "live.guest.state.joining";
    case "live":
      return "live.guest.state.live";
    case "declined":
      return "live.guest.state.declined";
    case "removed":
      return "live.guest.state.removed";
    case "left":
      return "live.guest.state.left";
    case "failed":
      return "live.guest.state.failed";
    default:
      return "live.guest.state.idle";
  }
}

// ---------------------------------------------------------------------------
// Invites
// ---------------------------------------------------------------------------

export type LiveInvite = {
  /** Server-issued stable id. The deduplication key. */
  inviteId: string;
  liveId: number;
  requestId: number;
  userId: number;
  invitedBy: number;
  inviterName: string;
  displayName: string;
  avatarUrl: string;
  role: string;
  message: string;
  status: string;
  expiresAt: string;
  expired: boolean;
};

function str(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return fallback;
}

function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function normalizeLiveInvite(raw: Record<string, unknown> | null | undefined): LiveInvite | null {
  const data = raw || {};
  const inviteId = str(data.invite_id).trim();
  // No stable id means no way to deduplicate, and an invite that cannot be
  // deduplicated will eventually be shown twice. Reject it instead.
  if (!inviteId) return null;
  const userId = num(data.user_id);
  if (userId <= 0) return null;
  return {
    inviteId,
    liveId: num(data.live_id),
    requestId: num(data.request_id ?? data.id),
    userId,
    invitedBy: num(data.invited_by),
    inviterName: str(data.inviter_name, ""),
    displayName: str(data.display_name, ""),
    avatarUrl: str(data.avatar_url),
    role: str(data.role, "cohost"),
    message: str(data.request_message),
    status: str(data.status, "invited"),
    expiresAt: str(data.expires_at),
    expired: data.expired === true
  };
}

/**
 * Deduplicate invites by their stable id, newest wins.
 *
 * The same invite legitimately arrives more than once: a push notification, a
 * realtime event and a polled `/invites` response can all describe it. Keying
 * on the server's invite id means the guest sees one prompt regardless.
 */
export function mergeLiveInvites(...batches: unknown[]): LiveInvite[] {
  const byId = new Map<string, LiveInvite>();
  for (const batch of batches) {
    const list = Array.isArray(batch) ? batch : [];
    for (const entry of list) {
      const invite = normalizeLiveInvite(entry as Record<string, unknown>);
      if (!invite) continue;
      byId.set(invite.inviteId, invite);
    }
  }
  return Array.from(byId.values()).filter((invite) => !invite.expired && invite.status === "invited");
}

/** Whether an invite is still actionable at the given moment. */
export function isInviteActionable(invite: LiveInvite | null | undefined, now: Date = new Date()): boolean {
  if (!invite || invite.expired || invite.status !== "invited") return false;
  if (!invite.expiresAt) return true;
  const expiry = Date.parse(invite.expiresAt.endsWith("Z") ? invite.expiresAt : `${invite.expiresAt}Z`);
  if (!Number.isFinite(expiry)) return false;
  return now.getTime() < expiry;
}
