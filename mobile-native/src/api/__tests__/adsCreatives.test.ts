/**
 * The Creative Library's model.
 *
 * These tests pin the claims the module was written to make, and two it was
 * written to refuse:
 *
 *   • A library that was never fetched is `unavailable`, not `empty`. An
 *     advertiser with a rejected creative must never read "nothing here yet"
 *     because a request failed (§31).
 *   • Delete is offered only when *both* server columns read `draft`, mirroring
 *     `pulse_advertiser_portal.py:683`. A rejected creative keeps
 *     `status='pending_review'` and would get a 409.
 *   • Submit is offered once, from `draft` only — a second submit writes a
 *     second review-board row and the same ad appears twice in the Policy
 *     Center.
 *   • Rejections sort above unsubmitted drafts inside the group they share.
 *   • A rejection never renders without a reason line.
 */

import { AdCreative, AdsPortal, normalizeAdsPortal } from "../adsPortal";
import {
  CREATIVE_ACTIONS,
  canActOnCreative,
  canDeleteDraft,
  creativeAccountRole,
  creativeActionOffers,
  creativeGroupFor,
  creativeGroups,
  creativeGuidance,
  creativeLibraryModel,
  creativeState,
  creativeStateLabel,
  creativeStateTone
} from "../adsCreatives";

/** One creative row, with the shape `_creative_public` actually sends. */
function creative(over: Partial<AdCreative> = {}): AdCreative {
  return {
    id: 1,
    ad_account_id: 7,
    campaign_id: 5,
    campaign_name: "Spring push",
    title: "Summer banner",
    status: "draft",
    moderation_status: "draft",
    rejection_reason: "",
    media_ready: true,
    ...over
  } as AdCreative;
}

/** A creative that was submitted and then rejected — both columns, as the server leaves them. */
function rejected(over: Partial<AdCreative> = {}): AdCreative {
  return creative({ status: "pending_review", moderation_status: "rejected", ...over });
}

/** A portal carrying only the pieces this module reads. */
function portalWith(creatives: AdCreative[], accounts: unknown[] = [{ id: 7, role: "owner" }]): AdsPortal {
  return normalizeAdsPortal({ creatives, accounts } as never);
}

describe("creativeState", () => {
  it("reads the verdict before the lifecycle, because a rejection leaves status behind", () => {
    // `reject_creative` writes moderation_status only; status stays at the value
    // submit set. Reading `status` first would call this creative "In review".
    expect(creativeState(rejected())).toBe("rejected");
  });

  it("treats a blocked verdict as a rejection, not an unknown state", () => {
    expect(creativeState(creative({ moderation_status: "blocked" }))).toBe("rejected");
  });

  it("calls an archived creative archived whatever its verdict was", () => {
    expect(creativeState(creative({ status: "archived", moderation_status: "approved" }))).toBe("archived");
  });

  it("recognises a submission from either column", () => {
    expect(creativeState(creative({ moderation_status: "pending", status: "draft" }))).toBe("in_review");
    expect(creativeState(creative({ moderation_status: "", status: "pending_review" }))).toBe("in_review");
  });

  it("falls back to draft rather than inventing a state for an unrecognised row", () => {
    expect(creativeState(creative({ status: "", moderation_status: "" }))).toBe("draft");
  });

  it("hands a tone AdsStatusPill already understands", () => {
    expect(creativeStateTone(rejected())).toBe("error");
    expect(creativeStateTone(creative({ moderation_status: "pending" }))).toBe("warning");
    expect(creativeStateTone(creative({ moderation_status: "approved" }))).toBe("success");
    expect(creativeStateLabel(creative({ moderation_status: "pending" }))).toBe("In review");
  });
});

describe("creativeActionOffers", () => {
  it("offers submit only from draft, so one creative can't enter review twice", () => {
    const draftActions = creativeActionOffers(creative()).map((offer) => offer.action);
    expect(draftActions).toContain("submit");

    for (const notDraft of [
      creative({ moderation_status: "pending" }),
      creative({ moderation_status: "approved" }),
      rejected()
    ]) {
      expect(creativeActionOffers(notDraft).map((offer) => offer.action)).not.toContain("submit");
    }
  });

  it("never offers delete on a rejected creative, because the server answers 409", () => {
    // §31 forbids an active-looking control that can't complete. `delete_draft`
    // requires status AND moderation_status to read `draft`; a rejection keeps
    // status at `pending_review`.
    expect(canDeleteDraft(rejected())).toBe(false);
    expect(creativeActionOffers(rejected()).map((offer) => offer.action)).not.toContain("delete_draft");
  });

  it("offers delete only when both columns agree, not on moderation_status alone", () => {
    expect(canDeleteDraft(creative({ status: "draft", moderation_status: "draft" }))).toBe(true);
    expect(canDeleteDraft(creative({ status: "pending_review", moderation_status: "draft" }))).toBe(false);
    expect(canDeleteDraft(creative({ status: "draft", moderation_status: "pending" }))).toBe(false);
  });

  it("always offers duplicate, the only route out of a rejection this surface serves", () => {
    for (const row of [
      creative(),
      rejected(),
      creative({ moderation_status: "approved" }),
      creative({ status: "archived" })
    ]) {
      expect(creativeActionOffers(row).map((offer) => offer.action)).toContain("duplicate");
    }
  });

  it("does not offer archive on something already archived", () => {
    expect(creativeActionOffers(creative({ status: "archived" })).map((offer) => offer.action)).not.toContain(
      "archive"
    );
  });

  it("only ever names actions the server accepts", () => {
    for (const row of [creative(), rejected(), creative({ moderation_status: "approved" })]) {
      for (const offer of creativeActionOffers(row)) {
        expect(CREATIVE_ACTIONS).toContain(offer.action);
      }
    }
  });

  it("marks the irreversible actions destructive and gives each a label for its pending state", () => {
    const offers = creativeActionOffers(creative());
    const byAction = Object.fromEntries(offers.map((offer) => [offer.action, offer]));
    expect(byAction.archive.destructive).toBe(true);
    expect(byAction.delete_draft.destructive).toBe(true);
    expect(byAction.submit.destructive).toBe(false);
    for (const offer of offers) expect(offer.pendingLabel.length).toBeGreaterThan(0);
  });
});

describe("creativeGuidance", () => {
  it("never leaves a rejection without a reason line", () => {
    const withReason = creativeGuidance(rejected({ rejection_reason: "Text covers too much of the image." }));
    expect(withReason).toMatch(/Text covers too much/);

    // §37 forbids an inaccessible policy reason. A blank line under a rejection
    // is inaccessible in the way that matters, so the absence is stated.
    const withoutReason = creativeGuidance(rejected({ rejection_reason: "" }));
    expect(withoutReason).toMatch(/no reason was recorded/i);
    expect(withoutReason.length).toBeGreaterThan(0);
  });

  it("says a draft isn't delivering rather than leaving the reader to infer it", () => {
    expect(creativeGuidance(creative())).toMatch(/hasn't been submitted/i);
  });

  it("names the missing media when that is what blocks the draft", () => {
    expect(creativeGuidance(creative({ media_ready: false }))).toMatch(/missing its media/i);
  });

  it("asks nothing of a creative in review", () => {
    expect(creativeGuidance(creative({ moderation_status: "pending" }))).toMatch(/nothing is required/i);
  });
});

describe("creativeGroups", () => {
  it("keeps rejections and unsubmitted drafts together, since both mean money isn't moving", () => {
    expect(creativeGroupFor(rejected())).toBe("action");
    expect(creativeGroupFor(creative())).toBe("action");
  });

  it("puts rejections above drafts inside the group they share", () => {
    const groups = creativeGroups([
      creative({ id: 9 }),
      rejected({ id: 2 }),
      creative({ id: 8 }),
      rejected({ id: 1 })
    ]);
    expect(groups[0].key).toBe("action");
    expect(groups[0].creatives.map((row) => row.id)).toEqual([2, 1, 9, 8]);
  });

  it("orders newest first within a state, matching what the server returns", () => {
    const groups = creativeGroups([creative({ id: 3 }), creative({ id: 11 }), creative({ id: 7 })]);
    expect(groups[0].creatives.map((row) => row.id)).toEqual([11, 7, 3]);
  });

  it("drops empty groups rather than rendering a heading over nothing", () => {
    const groups = creativeGroups([creative({ moderation_status: "approved" })]);
    expect(groups.map((group) => group.key)).toEqual(["live"]);
  });

  it("orders the groups by what the reader has to act on", () => {
    const groups = creativeGroups([
      creative({ id: 1, status: "archived" }),
      creative({ id: 2, moderation_status: "approved" }),
      creative({ id: 3, moderation_status: "pending" }),
      rejected({ id: 4 })
    ]);
    expect(groups.map((group) => group.key)).toEqual(["action", "review", "live", "archived"]);
  });

  it("captions a group without restating its count as prose", () => {
    const groups = creativeGroups([rejected()]);
    expect(groups[0].caption).not.toMatch(/\d/);
  });
});

describe("creativeLibraryModel", () => {
  it("calls a library that was never fetched unavailable, not empty", () => {
    expect(creativeLibraryModel(null).state).toBe("unavailable");
    expect(creativeLibraryModel(undefined).state).toBe("unavailable");
  });

  it("calls a degraded portal unavailable, because its empty arrays are not answers", () => {
    expect(creativeLibraryModel({ ...portalWith([]), degraded: true }).state).toBe("unavailable");
  });

  it("reports zero counts and no write when unavailable, so nothing renders a false all-clear", () => {
    const model = creativeLibraryModel(null);
    expect(model.actionCount).toBe(0);
    expect(model.groups).toEqual([]);
    expect(model.canWrite).toBe(false);
  });

  it("calls a library that loaded and holds nothing empty", () => {
    expect(creativeLibraryModel(portalWith([])).state).toBe("empty");
  });

  it("counts rejections and unsubmitted drafts as the work outstanding", () => {
    const model = creativeLibraryModel(
      portalWith([
        rejected({ id: 1 }),
        creative({ id: 2 }),
        creative({ id: 3, moderation_status: "pending" }),
        creative({ id: 4, moderation_status: "approved" })
      ])
    );
    expect(model.state).toBe("ready");
    expect(model.actionCount).toBe(2);
  });

  it("answers 'can you write anywhere' when no account is named", () => {
    // Offering nothing because the first account happens to be read-only would
    // hide actions that would succeed on the second.
    const portal = portalWith([creative()], [
      { id: 7, role: "viewer" },
      { id: 8, role: "campaign_manager" }
    ]);
    expect(creativeLibraryModel(portal).canWrite).toBe(true);
    expect(creativeLibraryModel(portal).writeBlockedReason).toBeNull();
  });

  it("scopes the answer to one account when one is named", () => {
    const portal = portalWith([creative()], [
      { id: 7, role: "viewer" },
      { id: 8, role: "campaign_manager" }
    ]);
    expect(creativeLibraryModel(portal, 8).canWrite).toBe(true);
    expect(creativeLibraryModel(portal, 7).canWrite).toBe(false);
  });

  it("explains a read-only view instead of silently hiding the controls", () => {
    const portal = portalWith([creative()], [{ id: 7, role: "viewer" }]);
    const unscoped = creativeLibraryModel(portal);
    const scoped = creativeLibraryModel(portal, 7);
    expect(unscoped.canWrite).toBe(false);
    expect(unscoped.writeBlockedReason).toMatch(/read-only/i);
    expect(scoped.writeBlockedReason).toMatch(/read-only/i);
  });

  it("distinguishes an analyst from a viewer when explaining", () => {
    const portal = portalWith([creative()], [{ id: 7, role: "analyst" }]);
    expect(creativeLibraryModel(portal, 7).writeBlockedReason).toMatch(/analyst/i);
  });
});

describe("per-creative authority", () => {
  it("authorises against the account the creative belongs to, not a rolled-up role", () => {
    // `roles.current` reads `owner` for anyone who owns any account at all, and
    // the server re-derives per account and answers 403.
    const portal = portalWith([], [
      { id: 7, role: "owner" },
      { id: 8, role: "viewer" }
    ]);
    expect(canActOnCreative(portal, creative({ ad_account_id: 7 }))).toBe(true);
    expect(canActOnCreative(portal, creative({ ad_account_id: 8 }))).toBe(false);
    expect(creativeAccountRole(portal, creative({ ad_account_id: 8 }))).toBe("viewer");
  });

  it("treats an account the portal doesn't list as a viewer, the least it could be", () => {
    const portal = portalWith([], [{ id: 7, role: "owner" }]);
    expect(creativeAccountRole(portal, creative({ ad_account_id: 999 }))).toBe("viewer");
    expect(canActOnCreative(portal, creative({ ad_account_id: 999 }))).toBe(false);
  });
});
