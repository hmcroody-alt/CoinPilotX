/**
 * Progress OS client — the PulseSoc Founding Path.
 *
 * Two rules shape every type in this file, and both are deliberate omissions
 * rather than oversights:
 *
 * 1. **No `userId` parameter anywhere.** Not on the tile, not on the referral
 *    list, not on the detail call. The server derives the subject from the
 *    session, so there is no request this module can construct that means "show
 *    me someone else's referral progress". Privacy here is a property of the
 *    call signatures, not of a check a future caller has to remember.
 *
 * 2. **Nothing is computed locally.** The certified count, Live eligibility
 *    and badge eligibility all arrive decided. This client
 *    formats what it is given and nothing more. A client that could add up
 *    referrals would eventually disagree with the server, and the member would
 *    believe the number that is wrong — the one on their own screen.
 *
 * A referral is identified by `ref`, an opaque server token bound to the
 * viewer. Referred users' Pulse IDs are never sent to the app, so there is no
 * internal identifier for a screen to leak into a header, a share sheet or a
 * crash report.
 */

import { pulseApi } from "./pulseApi";

/* -------------------------------------------------------------------------- *
 * Overview
 * -------------------------------------------------------------------------- */

export type ProgressCampaign = {
  campaign_id: string;
  campaign_version: number;
  name: string;
  status: string;
  target: number;
};

export type ProgressChallenge = {
  qualified: number;
  target: number;
  remaining: number;
  percent: number;
  complete: boolean;
};

/** Counts by stage. Momentum, not a taxonomy — see `progress_api._TABS`. */
export type ProgressBreakdown = {
  invited?: number;
  in_progress?: number;
  qualified?: number;
  in_review?: number;
  closed?: number;
};

export type ProgressNextMilestone = {
  key: string;
  label: string;
  threshold: number;
  remaining: number;
};

export type ProgressOverview = {
  ok?: boolean;
  campaign?: ProgressCampaign;
  challenge?: ProgressChallenge;
  breakdown?: ProgressBreakdown;
  next_milestone?: ProgressNextMilestone | null;
  milestones_earned?: string[];
  founding_member?: boolean;
  track?: string;
  /**
   * The server's standing reminder that this program is not identity
   * verification. Rendered verbatim so the disclaimer cannot drift between
   * platforms by someone editing one copy of it.
   */
  not_verification?: string;
};

/* -------------------------------------------------------------------------- *
 * Milestones
 * -------------------------------------------------------------------------- */

/**
 * `IN_PROGRESS` covers two different situations on purpose: the next rung the
 * member is working toward, and a rung whose threshold is met but whose award
 * row has not been written yet. Both are honestly "on the way"; only
 * `COMPLETED` means the award exists, and only `COMPLETED` may be shown in gold.
 */
export type ProgressMilestoneState = "COMPLETED" | "IN_PROGRESS" | "LOCKED";

export type ProgressMilestone = {
  key: string;
  label: string;
  threshold: number;
  kind: string;
  description?: string;
  state: ProgressMilestoneState;
  earned_at?: string | null;
  progress: number;
};

export type ProgressMilestones = {
  ok?: boolean;
  qualified?: number;
  milestones?: ProgressMilestone[];
};

/* -------------------------------------------------------------------------- *
 * Referrals
 * -------------------------------------------------------------------------- */

export type ProgressReferralTab = "all" | "qualified" | "pending" | "review";

export type ProgressReferral = {
  /** Opaque, viewer-bound. The only handle the app ever holds for a referral. */
  ref: string;
  /** Display name, or a neutral placeholder. Never a Pulse ID. */
  name: string;
  state: string;
  summary: string;
  /** Whether this referral currently counts toward the 30. */
  counts: boolean;
};

export type ProgressReferralList = {
  ok?: boolean;
  tab?: ProgressReferralTab;
  referrals?: ProgressReferral[];
  count?: number;
};

export type ProgressChecklistItem = {
  /** Stable key — the app localizes from this, never from `label`. */
  key: string;
  /** English fallback from the server, for a key the app does not know yet. */
  label: string;
  done: boolean;
};

export type ProgressReferralDetail = {
  ok?: boolean;
  state?: string;
  counts_toward_progress?: boolean;
  checklist?: ProgressChecklistItem[];
  summary?: string;
  name?: string;
  ref?: string;
};

/* -------------------------------------------------------------------------- *
 * Missions, activity, invite, static copy
 * -------------------------------------------------------------------------- */

export type ProgressMissionStatus = "COMPLETE" | "IN_PROGRESS" | "AVAILABLE";

export type ProgressMission = {
  mission_id: string;
  /** i18n key. The server ships keys, never display copy. */
  title_key: string;
  objective_type: string;
  target: number;
  current_progress: number;
  status: ProgressMissionStatus;
  /**
   * False means this deployment cannot measure the objective automatically, so
   * the bar reflects a recorded value rather than a live one. Worth surfacing:
   * an unmeasurable mission that looks live is a promise the app cannot keep.
   */
  measurable: boolean;
  completed_at?: string | null;
};

export type ProgressMissions = {
  ok?: boolean;
  track?: string;
  missions?: ProgressMission[];
};

export type ProgressActivityItem = {
  event_type: string;
  /** Display name of the person the event is about, or "" for self-events. */
  name: string;
  created_at: string;
};

export type ProgressActivity = {
  ok?: boolean;
  activity?: ProgressActivityItem[];
};

export type ProgressInvite = {
  ok?: boolean;
  referral_code?: string;
  referral_link?: string;
  error?: string;
};

export type ProgressHowItWorks = {
  ok?: boolean;
  steps?: Array<{ key: string; order: number }>;
  required_posting_days?: number;
  target?: number;
  reward_amount_cents?: number;
  reward_currency?: string;
  reward_interval?: number;
  fairness_note_key?: string;
  not_verification?: string;
};

export type ProgressFaq = {
  ok?: boolean;
  faq?: Array<{ key: string; order: number }>;
};

/* -------------------------------------------------------------------------- *
 * Profile tile
 * -------------------------------------------------------------------------- */

export type ProgressTileState = "START" | "ACTIVE" | "REVIEW" | "COMPLETE";

export type ProgressTile = {
  ok?: boolean;
  state?: ProgressTileState;
  qualified?: number;
  target?: number;
  percent?: number;
  founding_member?: boolean;
};

/* -------------------------------------------------------------------------- *
 * Calls
 * -------------------------------------------------------------------------- */

export async function getProgressOverview() {
  return pulseApi<ProgressOverview>("/api/progress");
}

export async function getProgressTile() {
  return pulseApi<ProgressTile>("/api/progress/tile");
}

export async function getProgressMilestones() {
  return pulseApi<ProgressMilestones>("/api/progress/milestones");
}

export async function getProgressReferrals(tab: ProgressReferralTab = "all") {
  return pulseApi<ProgressReferralList>(
    `/api/progress/referrals?tab=${encodeURIComponent(tab)}`,
  );
}

/** `ref` is the opaque token from the list; there is no id-based variant. */
export async function getProgressReferralDetail(ref: string) {
  return pulseApi<ProgressReferralDetail>(
    `/api/progress/referrals/${encodeURIComponent(ref)}`,
  );
}

export async function getProgressMissions() {
  return pulseApi<ProgressMissions>("/api/progress/missions");
}

export async function getProgressActivity() {
  return pulseApi<ProgressActivity>("/api/progress/activity");
}

export async function getProgressInvite() {
  return pulseApi<ProgressInvite>("/api/progress/invite");
}

export async function getProgressHowItWorks() {
  return pulseApi<ProgressHowItWorks>("/api/progress/how-it-works");
}

export async function getProgressFaq() {
  return pulseApi<ProgressFaq>("/api/progress/faq");
}

/* -------------------------------------------------------------------------- *
 * Formatting helpers
 * -------------------------------------------------------------------------- */

/**
 * Clamp a server percent into a bar width.
 *
 * The server already clamps, so this only guards against a malformed or absent
 * field producing a bar that overflows its track.
 */
export function progressBarPercent(percent: number | undefined): number {
  const value = Number(percent);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}
