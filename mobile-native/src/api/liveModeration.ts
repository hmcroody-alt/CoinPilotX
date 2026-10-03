/**
 * Live viewer moderation: ban and unban, scoped to one broadcast.
 *
 * This lives apart from `api/live.ts` on purpose, for two reasons.
 *
 * The first is governance. `api/live.ts` is a protected real-time audio path
 * (`backend_token_and_room_policy` in
 * config/realtime-audio-protected-paths.json) because it carries the token and
 * room calls that decide who may publish audio. Moderation is not that. Adding
 * it there would have dragged a trust & safety change through the audio change
 * gate for no reason other than file layout.
 *
 * The second is that it is honestly a different domain. The server keeps
 * this authority in one module (`services/live_moderation.py`) precisely so the
 * ban decision has exactly one owner; the client mirroring that shape costs
 * nothing and means a future reader looking for "where do we ban people" finds
 * one file rather than a section of a 500-line API module.
 */

import { pulseApi } from "./pulseApi";

export type LiveModerationAction = "ban" | "unban";

export type LiveModerationResult = {
  ok?: boolean;
  /** "created" | "already_active" | "cleared" | "not_active" */
  status?: string;
  banned?: boolean;
  target_user_id?: number;
  message?: string;
};

/**
 * Ban or unban a viewer from one Live session. Host or co-host only; the
 * server resolves the actor's role from its own state and never trusts a role
 * sent from here.
 *
 * Keyed on the *user*, not on a guest row, because the people most worth
 * banning are in the audience and have no guest row at all. The ban is scoped
 * to this one broadcast: it does not follow the person to the host's next
 * Live, and it is not the social block — that is a separate, permanent control
 * reached from a profile.
 *
 * What it does is narrower than "remove", and the copy around it has to match:
 * a realtime token already issued stays valid until it expires, and there is
 * no kick API on this stack. A ban denies the next token mint, join, replay
 * read, co-host request and invite — so the person is gone at their next
 * reconnect, not at the instant the button is pressed.
 */
export async function viewerModeration(
  liveId: number,
  targetUserId: number,
  action: LiveModerationAction,
  reason?: string
): Promise<LiveModerationResult> {
  return pulseApi<LiveModerationResult>(
    `/api/pulse/live/${liveId}/viewers/${targetUserId}/${action}`,
    { method: "POST", body: JSON.stringify({ source: "native", reason: reason ?? "" }) }
  );
}

export const banViewer = (liveId: number, targetUserId: number, reason?: string) =>
  viewerModeration(liveId, targetUserId, "ban", reason);

export const unbanViewer = (liveId: number, targetUserId: number) =>
  viewerModeration(liveId, targetUserId, "unban");

export type LiveBan = {
  id: number;
  targetUserId: number;
  moderatorUserId: number;
  createdAt: string;
};

/**
 * The bans currently in force on a Live. Moderator-only on the server, which
 * is deliberate: the stored rows carry a private moderator note, so this is
 * not something a viewer can read about themselves. The note is not mapped
 * through here either — nothing in the app needs it, and the surest way to
 * keep a private field private is to have no client type that can hold it.
 */
export async function fetchLiveBans(liveId: number): Promise<LiveBan[]> {
  const data = await pulseApi<{ ok?: boolean; bans?: Record<string, unknown>[] }>(
    `/api/pulse/live/${liveId}/moderation`,
    { method: "GET" }
  );
  return (data.bans ?? []).map((row) => ({
    id: Number(row?.id ?? 0),
    targetUserId: Number(row?.target_user_id ?? 0),
    moderatorUserId: Number(row?.moderator_user_id ?? 0),
    createdAt: String(row?.created_at ?? "")
  }));
}
