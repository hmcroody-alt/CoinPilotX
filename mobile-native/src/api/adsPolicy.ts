/**
 * The Policy Center's model.
 *
 * ## Why this exists
 *
 * §37 names "an inaccessible policy reason or appeal path" as a completion
 * blocker. The reason has been sitting in `portal.review_board` — the board
 * carries, per creative, the automated verdict, the human verdict, the risk
 * score and the written reason — and no client has ever read it. Until now a
 * rejected ad simply stopped delivering and the advertiser had nowhere to look.
 *
 * ## The appeal path, honestly
 *
 * There is a real appeals endpoint. It is `POST /api/business-os/advertising/
 * appeals` at `bot.py:20033`, and it is on the canonical surface, which returns
 * 404 on all 46 of its routes unless the server variable
 * `BUSINESS_OS_ADVERTISING` is set — blank in `.env.example`, with no evidence
 * in the repo that it was ever turned on. See
 * `docs/business_os/ADVERTISING_BACKEND_INVENTORY.md`.
 *
 * So this module does not offer an Appeal button. A button that posts to a route
 * answering 404 is a worse dead end than no button, because it converts "I don't
 * know what to do" into "I did the thing and nothing happened".
 *
 * What it offers instead is the remedy the live surface can actually perform.
 * `CREATIVE_ACTIONS` in `services/pulse_advertiser_portal.py:21` is `duplicate`,
 * `archive`, `delete_draft`, `submit` — so a rejected creative can be edited and
 * resubmitted, and that resubmission is reviewed again. That is a genuine route
 * out of a rejection, it works today, and it is what {@link policyRemedy}
 * returns. Support remains the escalation for a decision the advertiser believes
 * is wrong.
 *
 * ## What is derived here and what is not
 *
 * Every status word, reason and score is read from the server. This module only
 * decides grouping, ordering and which remedy applies — the questions a screen
 * would otherwise answer inline and inconsistently.
 */

import { AdReviewEntry, AdsPortal, reviewIsHumanDecided, reviewOutcome, reviewReasonText } from "./adsPortal";

/* ------------------------------------------------------------------ *
 * Remedies
 * ------------------------------------------------------------------ */

/**
 * What the advertiser can do about one decision.
 *
 * `resubmit` and `edit` are backed by `POST /api/pulse/ads/creatives/<id>/action`.
 * `support` is the escalation for a human decision the advertiser disputes —
 * deliberately not called "appeal", because the appeals workflow is on a surface
 * this deployment does not serve.
 * `wait` is for a decision nobody has made yet, where the honest instruction is
 * to do nothing.
 */
export type PolicyRemedyKind = "wait" | "edit" | "support" | "none";

export type PolicyRemedy = {
  kind: PolicyRemedyKind;
  /** One sentence, in the reader's terms, naming the next action. */
  text: string;
};

/**
 * The remedy for one review entry.
 *
 * The automated/human split is the whole point. An automated flag that no person
 * has looked at is still moving and the instruction is to wait; a rejection a
 * person upheld is final on this surface and the instruction is to change the ad
 * or escalate. Collapsing those two would either tell someone to rewrite an ad
 * that was about to be approved, or tell someone to wait forever.
 */
export function policyRemedy(entry: AdReviewEntry): PolicyRemedy {
  const outcome = reviewOutcome(entry);

  if (outcome === "approved") {
    return { kind: "none", text: "This creative is approved and can deliver." };
  }

  if (outcome === "pending") {
    if (reviewIsHumanDecided(entry)) {
      // A human verdict on a creative the moderation column still calls pending
      // is a state this app should not narrate confidently.
      return {
        kind: "support",
        text: "A reviewer has recorded a decision but this creative's status hasn't updated yet. Contact support if it stays this way."
      };
    }
    return {
      kind: "wait",
      text: "This creative is in review. No action is needed — you'll be notified when a decision is made."
    };
  }

  if (reviewIsHumanDecided(entry)) {
    return {
      kind: "support",
      text: "A reviewer upheld this rejection. Edit the creative to address the reason and resubmit it, or contact support if you believe the decision is wrong."
    };
  }

  return {
    kind: "edit",
    text: "Edit the creative to address the reason above, then resubmit it for review."
  };
}

/* ------------------------------------------------------------------ *
 * Grouping
 * ------------------------------------------------------------------ */

export type PolicyGroupKey = "action" | "review" | "cleared";

export type PolicyGroup = {
  key: PolicyGroupKey;
  title: string;
  /** Why this group exists, shown under the title. Never a count restated as prose. */
  caption: string;
  entries: AdReviewEntry[];
};

const GROUP_TITLE: Record<PolicyGroupKey, string> = {
  action: "Needs your attention",
  review: "In review",
  cleared: "Cleared"
};

const GROUP_CAPTION: Record<PolicyGroupKey, string> = {
  action: "These creatives were rejected. Each one shows the reason and what to do next.",
  review: "Submitted and waiting on a decision. Nothing is required from you.",
  cleared: "Approved and eligible to deliver."
};

/** Which group a decision belongs in. */
export function policyGroupFor(entry: AdReviewEntry): PolicyGroupKey {
  const outcome = reviewOutcome(entry);
  if (outcome === "rejected") return "action";
  if (outcome === "approved") return "cleared";
  return "review";
}

/**
 * The board, grouped and ordered.
 *
 * Rejections first, and within a group the highest risk score first, because the
 * ordering the server uses (`ORDER BY rb.id DESC`, newest first) answers a
 * different question than "what is stopping my ads from running".
 *
 * Empty groups are dropped rather than rendered as a heading over nothing.
 */
export function policyGroups(entries: AdReviewEntry[]): PolicyGroup[] {
  const order: PolicyGroupKey[] = ["action", "review", "cleared"];
  return order
    .map((key) => ({
      key,
      title: GROUP_TITLE[key],
      caption: GROUP_CAPTION[key],
      entries: entries
        .filter((entry) => policyGroupFor(entry) === key)
        .sort((a, b) => Number(b.risk_score || 0) - Number(a.risk_score || 0))
    }))
    .filter((group) => group.entries.length > 0);
}

/* ------------------------------------------------------------------ *
 * The screen model
 * ------------------------------------------------------------------ */

/**
 * `unavailable` and `empty` are different answers and the screen must say
 * different things. §31: an empty result is "No activity yet"; a request that
 * failed or was never made is "Unavailable". A portal that fell back to the
 * five-call fan-out never asked about policy at all, which is the second case.
 */
export type PolicyCenterState = "loading" | "ready" | "empty" | "unavailable";

export type PolicyCenterModel = {
  state: PolicyCenterState;
  groups: PolicyGroup[];
  /** Rejections outstanding. Drives the manager's tile subtitle. */
  actionCount: number;
  /** Decisions pending. */
  reviewCount: number;
};

/**
 * Build the model from a loaded portal, or from `null`.
 *
 * `null` means the portal request did not succeed, so the board was never
 * fetched. That is `unavailable`, not "no policy decisions" — a screen that
 * showed "Nothing to review" here would be telling an advertiser with a rejected
 * ad that everything is fine.
 */
export function policyCenterModel(portal: AdsPortal | null | undefined): PolicyCenterModel {
  if (!portal || portal.degraded) {
    return { state: "unavailable", groups: [], actionCount: 0, reviewCount: 0 };
  }

  const groups = policyGroups(portal.review_board);
  const actionCount = portal.review_board.filter((entry) => policyGroupFor(entry) === "action").length;
  const reviewCount = portal.review_board.filter((entry) => policyGroupFor(entry) === "review").length;

  return {
    state: groups.length > 0 ? "ready" : "empty",
    groups,
    actionCount,
    reviewCount
  };
}

/* ------------------------------------------------------------------ *
 * Presentation of a single decision
 * ------------------------------------------------------------------ */

/**
 * A subset of `CampaignTone`, so a decision can be handed straight to
 * `AdsStatusPill` without a second mapping table that could disagree with this
 * one about what red means.
 */
export type PolicyTone = "error" | "warning" | "success" | "neutral";

export type PolicyDecisionView = {
  /** The creative's title, never blank — `normalizeAdReviewBoard` guarantees it. */
  title: string;
  /** The campaign it belongs to, or an empty string when the join gave nothing. */
  campaign: string;
  /** `Rejected` | `In review` | `Approved`. */
  statusLabel: string;
  tone: PolicyTone;
  /** The stated reason, or an explicit admission that none was recorded. */
  reason: string;
  /**
   * Who decided, in one line. Separate from the reason because "an automated
   * check flagged this" and "a reviewer upheld it" carry different weight and
   * different remedies.
   */
  decidedBy: string;
  remedy: PolicyRemedy;
  /**
   * The risk score, 0–100, only when the server recorded one above zero. A score
   * of zero on an unscored row is not a finding, and presenting it as one would
   * be the fake-zero §31 prohibits.
   */
  riskScore: number | null;
};

const STATUS_LABEL: Record<"approved" | "rejected" | "pending", string> = {
  approved: "Approved",
  rejected: "Rejected",
  pending: "In review"
};

const STATUS_TONE: Record<"approved" | "rejected" | "pending", PolicyTone> = {
  approved: "success",
  rejected: "error",
  pending: "warning"
};

/** Everything one card needs, so the component holds no policy logic. */
export function policyDecisionView(entry: AdReviewEntry): PolicyDecisionView {
  const outcome = reviewOutcome(entry);
  const score = Number(entry.risk_score || 0);
  return {
    title: String(entry.title || "Untitled creative"),
    campaign: String(entry.campaign_name || ""),
    statusLabel: STATUS_LABEL[outcome],
    tone: STATUS_TONE[outcome],
    reason: reviewReasonText(entry),
    decidedBy: policyDecidedBy(entry),
    remedy: policyRemedy(entry),
    riskScore: score > 0 ? score : null
  };
}

/**
 * Who made the call.
 *
 * Deliberately silent when the server recorded neither verdict: inventing
 * "reviewed automatically" for a row with no automated status would be asserting
 * something nobody said.
 */
export function policyDecidedBy(entry: AdReviewEntry): string {
  const automated = String(entry.automated_review_status || "").trim();
  const human = String(entry.human_review_status || "").trim();

  if (reviewIsHumanDecided(entry)) {
    return automated
      ? "Flagged by an automated check and reviewed by a person."
      : "Reviewed by a person.";
  }
  if (automated && automated.toLowerCase() !== "pending") {
    return "Decided by an automated check. A person hasn't reviewed it.";
  }
  if (human.toLowerCase() === "pending") {
    return "Waiting on a person to review it.";
  }
  return "";
}
