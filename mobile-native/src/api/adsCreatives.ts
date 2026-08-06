/**
 * The Creative Library's model.
 *
 * ## Why this replaces a page of prose
 *
 * The Phase 1 "Creative library" page described the rules the server enforces
 * and listed nothing. That was the honest thing to ship when no client had ever
 * read `portal.creatives`, and it is a dead end now that one does: §37 forbids
 * an empty locked card with no useful destination, and a tile called "Creative
 * library" that opens a rulebook is exactly that one screen deeper.
 *
 * ## Where the list comes from, and why not the obvious endpoint
 *
 * There are two ways to list creatives and they are not equivalent:
 *
 *   • `GET /api/pulse/ads/creatives` → `pulse_ads_service.list_creatives`,
 *     which filters on `a.owner_user_id = ?`. A campaign manager on someone
 *     else's account gets `[]` — not "no creatives", but "this query does not
 *     serve you". Rendering that as an empty library would be a fake zero.
 *   • `portal.creatives` → `pulse_advertiser_portal.list_creatives`, which
 *     scopes by `_account_ids_for_user` (owner *or* active team member), joins
 *     `campaign_name` and `business_name`, and excludes archived rows.
 *
 * This module reads the second. It is the one whose answer matches the question
 * the screen is asking.
 *
 * ## Which actions exist, and which are deliberately absent
 *
 * `CREATIVE_ACTIONS` in `services/pulse_advertiser_portal.py:21` is exactly
 * `duplicate`, `archive`, `delete_draft`, `submit`, all behind
 * `POST /api/pulse/ads/creatives/<id>/action` and all role-checked per account
 * against `WRITE_ROLES`. Those four are offered.
 *
 * Two things a creative library would normally have are not offered, for the
 * same reason the Policy Center offers no Appeal button — a control that cannot
 * complete is worse than an absent one, because it converts "I can't do this
 * here" into "I did it and nothing happened":
 *
 *   • **Replace media.** `replace_creative` requires a `media_asset_id` from
 *     `POST /api/pulse/ads/accounts/<id>/media/upload`, a multipart route this
 *     app has no uploader for. Until that flow exists there is no way to obtain
 *     the id the endpoint demands.
 *   • **Edit copy.** There is no update route for a creative's title or body on
 *     this surface at all. `replace` only swaps media.
 *
 * So the library's answer to "this creative was rejected" is the one the server
 * can actually perform: duplicate it, or delete the draft and create a new one
 * in the campaign editor.
 *
 * ## What is derived here
 *
 * Grouping, ordering, which of the four actions apply to a given row, and the
 * one-line reason when none of them do. Every status word, reason and count is
 * read from the server.
 */

import {
  AD_WRITE_ROLES,
  AdCreative,
  AdsPortal,
  accountRole,
  adWriteBlockedReason,
  canWriteAds
} from "./adsPortal";
import { pulseApi } from "./pulseApi";

/* ------------------------------------------------------------------ *
 * The wire
 * ------------------------------------------------------------------ */

/**
 * The four the server accepts. Not a superset "in case" — an action the server
 * rejects with "Unsupported creative action." is a button that fails.
 */
export const CREATIVE_ACTIONS = ["submit", "duplicate", "archive", "delete_draft"] as const;

export type CreativeAction = (typeof CREATIVE_ACTIONS)[number];

export type CreativeActionResponse = {
  ok?: boolean;
  action?: string;
  creative?: AdCreative;
  creative_id?: number;
  deleted?: boolean;
  status?: string;
  error?: string;
};

/**
 * Run one action.
 *
 * Deliberately thin: the response shape differs per action — `submit` returns
 * the updated creative, `duplicate` returns a new id, `delete_draft` returns
 * `{deleted: true}` — and flattening those into a common shape would throw away
 * the distinction the caller needs to decide what to say afterwards.
 */
export async function runCreativeAction(creativeId: number, action: CreativeAction) {
  return pulseApi<CreativeActionResponse>(
    `/api/pulse/ads/creatives/${encodeURIComponent(String(creativeId))}/action`,
    { method: "POST", body: JSON.stringify({ action }) }
  );
}

/* ------------------------------------------------------------------ *
 * State
 * ------------------------------------------------------------------ */

/**
 * Where a creative is in its life.
 *
 * Derived from two server columns, not one. `status` is the lifecycle
 * (`draft` | `pending_review` | `approved` | `archived`) and `moderation_status`
 * is the verdict (`draft` | `pending` | `approved` | `rejected`). They can
 * disagree — `submit_creative_for_review` writes `pending_review`/`pending`
 * together, but `approve_creative` sets both to `approved` while a rejection
 * leaves `status` untouched — so the verdict is read first and the lifecycle
 * only settles what a `draft` verdict means.
 */
export type CreativeState = "draft" | "in_review" | "approved" | "rejected" | "archived";

export function creativeState(creative: AdCreative): CreativeState {
  const moderation = String(creative.moderation_status || "").toLowerCase();
  const status = String(creative.status || "").toLowerCase();

  if (status === "archived") return "archived";
  if (moderation === "rejected" || moderation === "blocked") return "rejected";
  if (moderation === "approved") return "approved";
  if (moderation === "pending" || status === "pending_review") return "in_review";
  return "draft";
}

/** A subset of `CampaignTone`, so a creative can be handed straight to `AdsStatusPill`. */
export type CreativeTone = "success" | "warning" | "error" | "neutral";

const STATE_LABEL: Record<CreativeState, string> = {
  draft: "Draft",
  in_review: "In review",
  approved: "Approved",
  rejected: "Rejected",
  archived: "Archived"
};

const STATE_TONE: Record<CreativeState, CreativeTone> = {
  draft: "neutral",
  in_review: "warning",
  approved: "success",
  rejected: "error",
  archived: "neutral"
};

export function creativeStateLabel(creative: AdCreative): string {
  return STATE_LABEL[creativeState(creative)];
}

export function creativeStateTone(creative: AdCreative): CreativeTone {
  return STATE_TONE[creativeState(creative)];
}

/* ------------------------------------------------------------------ *
 * Which actions apply
 * ------------------------------------------------------------------ */

export type CreativeActionOffer = {
  action: CreativeAction;
  label: string;
  /** Shown while the request is in flight and read out by screen readers. */
  pendingLabel: string;
  /** `true` for anything that cannot be undone from this screen. */
  destructive: boolean;
};

const ACTION_LABEL: Record<CreativeAction, { label: string; pendingLabel: string; destructive: boolean }> = {
  submit: { label: "Submit for review", pendingLabel: "Submitting…", destructive: false },
  duplicate: { label: "Duplicate", pendingLabel: "Duplicating…", destructive: false },
  archive: { label: "Archive", pendingLabel: "Archiving…", destructive: true },
  delete_draft: { label: "Delete draft", pendingLabel: "Deleting…", destructive: true }
};

/**
 * The actions this creative can actually take, in the order they should appear.
 *
 * Every exclusion here mirrors a server rule rather than a taste:
 *
 *   • `submit` only from `draft`. Submitting twice writes a second moderation
 *     queue row and a second review-board row for the same creative
 *     (`submit_creative_for_review`), which is how one ad comes to appear twice
 *     in the Policy Center.
 *   • `delete_draft` requires `status` *and* `moderation_status` to both read
 *     `draft`; the server answers 409 with "Only draft creatives can be deleted"
 *     otherwise. A rejected creative keeps `status='pending_review'`, so Delete
 *     must not be offered on one even though its verdict is final.
 *   • `archive` is not offered on something already archived. The portal list
 *     excludes archived rows, so this is a guard rather than a common case.
 *   • `duplicate` works from any state and is the only route out of a rejection
 *     that this surface serves, so it is always present.
 */
export function creativeActionOffers(creative: AdCreative): CreativeActionOffer[] {
  const state = creativeState(creative);
  const actions: CreativeAction[] = [];

  if (state === "draft") actions.push("submit");
  actions.push("duplicate");
  if (state !== "archived") actions.push("archive");
  if (canDeleteDraft(creative)) actions.push("delete_draft");

  return actions.map((action) => ({ action, ...ACTION_LABEL[action] }));
}

/** Both columns, because the server checks both. */
export function canDeleteDraft(creative: AdCreative): boolean {
  return (
    String(creative.status || "").toLowerCase() === "draft" &&
    String(creative.moderation_status || "").toLowerCase() === "draft"
  );
}

/**
 * The one line under a creative saying what to do about it.
 *
 * A rejected creative gets the reason the server recorded, or an admission that
 * none was recorded — §37 forbids an inaccessible policy reason, and a blank
 * space under a rejection is inaccessible in the way that matters.
 */
export function creativeGuidance(creative: AdCreative): string {
  const state = creativeState(creative);

  if (state === "rejected") {
    const reason = String(creative.rejection_reason || "").trim();
    return reason
      ? `Rejected: ${reason} Duplicate this creative to start a corrected version.`
      : "This creative was rejected and no reason was recorded. Duplicate it to start a corrected version, or contact support with the campaign name.";
  }
  if (state === "draft") {
    return creative.media_ready
      ? "This draft hasn't been submitted. Nothing is delivering until it's reviewed."
      : "This draft is missing its media, so it can't be submitted from the app yet.";
  }
  if (state === "in_review") {
    return "Submitted and waiting on a decision. Nothing is required from you.";
  }
  if (state === "approved") {
    return "Approved and eligible to deliver in its campaign.";
  }
  return "Archived. It no longer delivers and doesn't count against your campaign.";
}

/* ------------------------------------------------------------------ *
 * Grouping
 * ------------------------------------------------------------------ */

export type CreativeGroupKey = "action" | "review" | "live" | "archived";

export type CreativeGroup = {
  key: CreativeGroupKey;
  title: string;
  /** Why the group exists. Never its count restated as prose. */
  caption: string;
  creatives: AdCreative[];
};

const GROUP_TITLE: Record<CreativeGroupKey, string> = {
  action: "Needs your attention",
  review: "In review",
  live: "Ready to deliver",
  archived: "Archived"
};

const GROUP_CAPTION: Record<CreativeGroupKey, string> = {
  action: "Rejected, or drafted and never submitted. These aren't delivering.",
  review: "Submitted and waiting on a decision. Nothing is required from you.",
  live: "Approved and eligible to deliver in their campaigns.",
  archived: "Kept for reference. These don't deliver."
};

/**
 * Rejections and unsubmitted drafts share a group.
 *
 * They are different situations but the same fact about the advertiser's money:
 * a creative they meant to run is not running, and they have to do something.
 * Splitting them would put the two states a reader most needs to see under
 * separate headings, one of which they might not scroll to.
 */
export function creativeGroupFor(creative: AdCreative): CreativeGroupKey {
  const state = creativeState(creative);
  if (state === "rejected" || state === "draft") return "action";
  if (state === "in_review") return "review";
  if (state === "archived") return "archived";
  return "live";
}

/** The library, grouped and ordered. Empty groups are dropped, not rendered as a heading over nothing. */
export function creativeGroups(creatives: AdCreative[]): CreativeGroup[] {
  const order: CreativeGroupKey[] = ["action", "review", "live", "archived"];
  return order
    .map((key) => ({
      key,
      title: GROUP_TITLE[key],
      caption: GROUP_CAPTION[key],
      // Within a group, rejections before drafts, then newest id first. The
      // server's own ordering is `cr.id DESC` throughout, which answers "what
      // did I make last" rather than "what is stopping my ads".
      creatives: creatives
        .filter((creative) => creativeGroupFor(creative) === key)
        .sort((a, b) => {
          const weight = (creative: AdCreative) => (creativeState(creative) === "rejected" ? 0 : 1);
          const byState = weight(a) - weight(b);
          return byState !== 0 ? byState : Number(b.id || 0) - Number(a.id || 0);
        })
    }))
    .filter((group) => group.creatives.length > 0);
}

/* ------------------------------------------------------------------ *
 * The screen model
 * ------------------------------------------------------------------ */

/**
 * `unavailable` and `empty` are different answers. §31: an empty result is "No
 * activity yet"; a request that failed or was never made is "Unavailable". A
 * portal that fell back to the five-call fan-out never asked about creatives at
 * all, which is the second case.
 */
export type CreativeLibraryState = "loading" | "ready" | "empty" | "unavailable";

export type CreativeLibraryModel = {
  state: CreativeLibraryState;
  groups: CreativeGroup[];
  /** Rejected or never-submitted. Drives the manager tile's subtitle. */
  actionCount: number;
  /**
   * Whether this reader may run any of the four actions.
   *
   * Derived per account, not from `roles.current` — the rollup reads `owner` for
   * someone who owns any account at all, and `_require_account_role` re-derives
   * per account and answers 403. A viewer offered a Submit button would get a
   * failure the app couldn't explain.
   */
  canWrite: boolean;
  /** Why the actions are absent, in the reader's terms, or `null` when they aren't. */
  writeBlockedReason: string | null;
};

/**
 * Build the model from a loaded portal, or from `null`.
 *
 * `null` or `degraded` means the creative list was never fetched. That is
 * `unavailable`, not "you have no creatives" — a library that showed "Nothing
 * here yet" to an advertiser with a rejected ad would be lying about the one
 * thing they came to check.
 */
export function creativeLibraryModel(
  portal: AdsPortal | null | undefined,
  accountId?: number
): CreativeLibraryModel {
  if (!portal || portal.degraded) {
    return {
      state: "unavailable",
      groups: [],
      actionCount: 0,
      canWrite: false,
      writeBlockedReason: null
    };
  }

  // No account named means the reader is looking at the library as a whole, and
  // the honest permission answer is "can you write anywhere" — offering nothing
  // because the first account happens to be read-only would hide actions that
  // would succeed.
  const scoped = accountId !== undefined && accountId !== null;
  const canWrite = scoped
    ? canWriteAds(portal, accountId)
    : portal.accounts.some((account) => AD_WRITE_ROLES.includes(String(account.role || "viewer")));

  const groups = creativeGroups(portal.creatives);
  const actionCount = portal.creatives.filter(
    (creative) => creativeGroupFor(creative) === "action"
  ).length;

  return {
    state: groups.length > 0 ? "ready" : "empty",
    groups,
    actionCount,
    canWrite,
    writeBlockedReason: canWrite
      ? null
      : scoped
        ? adWriteBlockedReason(portal, accountId)
        : "Your access to these accounts is read-only. An account owner can change your role."
  };
}

/**
 * The role this reader holds on the account a creative belongs to.
 *
 * Exported because an action is authorised per account and a library can span
 * several — a reader who owns one account and views another must see actions on
 * the first and not the second.
 */
export function creativeAccountRole(portal: AdsPortal | null | undefined, creative: AdCreative): string {
  return accountRole(portal, Number(creative.ad_account_id || 0));
}

/** Whether this specific creative's account allows writes. */
export function canActOnCreative(portal: AdsPortal | null | undefined, creative: AdCreative): boolean {
  return canWriteAds(portal, Number(creative.ad_account_id || 0));
}

/**
 * Why this creative has no action buttons, in the reader's terms.
 *
 * A row with the controls silently missing is indistinguishable from a row that
 * has no actions available, and §31 forbids leaving the reader to guess which.
 * The reason names the role and who can change it.
 */
export function creativeWriteBlockedReason(
  portal: AdsPortal | null | undefined,
  creative: AdCreative
): string | null {
  return adWriteBlockedReason(portal, Number(creative.ad_account_id || 0));
}
