export type ReelsOfflineDisposition = "QUEUE ALLOWED" | "LOCAL DRAFT ONLY" | "MANUAL RETRY" | "ONLINE REQUIRED" | "NOT APPLICABLE";

export type ReelsOfflineAction =
  | "reaction"
  | "replace_reaction"
  | "remove_reaction"
  | "save"
  | "unsave"
  | "follow"
  | "unfollow"
  | "comment"
  | "reply"
  | "share"
  | "report"
  | "block"
  | "delete_own_comment"
  | "delete_own_reel"
  | "join_live";

/**
 * Reels uses server-authoritative toggle endpoints, so mutations are never
 * replayed from a second native queue. Text may survive as a private local
 * draft; destructive, moderation, identity, and Live actions require online
 * authorization at the moment they are performed.
 */
export const REELS_OFFLINE_POLICY: Record<ReelsOfflineAction, ReelsOfflineDisposition> = {
  reaction: "ONLINE REQUIRED",
  replace_reaction: "ONLINE REQUIRED",
  remove_reaction: "ONLINE REQUIRED",
  save: "ONLINE REQUIRED",
  unsave: "ONLINE REQUIRED",
  follow: "ONLINE REQUIRED",
  unfollow: "ONLINE REQUIRED",
  comment: "LOCAL DRAFT ONLY",
  reply: "LOCAL DRAFT ONLY",
  share: "MANUAL RETRY",
  report: "ONLINE REQUIRED",
  block: "ONLINE REQUIRED",
  delete_own_comment: "ONLINE REQUIRED",
  delete_own_reel: "ONLINE REQUIRED",
  join_live: "ONLINE REQUIRED"
};

export function reelsOfflineDisposition(action: ReelsOfflineAction) {
  return REELS_OFFLINE_POLICY[action];
}
