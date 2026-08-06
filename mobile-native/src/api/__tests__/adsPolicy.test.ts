/**
 * The Policy Center's model.
 *
 * These tests pin the four claims the module was written to make and one it was
 * written to refuse:
 *
 *   • A board that was never fetched is `unavailable`, not `empty`. This is the
 *     §31 distinction the whole module hangs on — an advertiser with a rejected
 *     ad must never be told "no decisions" because a request failed.
 *   • The remedy depends on who decided. A person upholding a rejection is a
 *     different situation from an automated flag nobody has looked at, and the
 *     instruction differs accordingly.
 *   • Rejections sort to the top, and within a group the riskiest first — the
 *     server's newest-first ordering answers a different question.
 *   • A zero risk score is not a finding.
 */

import { AdReviewEntry, AdsPortal, normalizeAdsPortal } from "../adsPortal";
import {
  policyCenterModel,
  policyDecidedBy,
  policyDecisionView,
  policyGroupFor,
  policyGroups,
  policyRemedy
} from "../adsPolicy";

/** One review row, with the shape the server actually sends. */
function entry(over: Partial<AdReviewEntry> = {}): AdReviewEntry {
  return {
    review_id: 1,
    review_status: "pending",
    risk_score: 0,
    automated_review_status: "",
    human_review_status: "",
    review_reason: "",
    creative_id: 10,
    title: "Untitled creative",
    moderation_status: "pending",
    rejection_reason: "",
    campaign_id: 5,
    campaign_name: "Spring push",
    ...over
  };
}

/** A portal carrying only the board, which is all this module reads. */
function portalWith(board: AdReviewEntry[]): AdsPortal {
  return normalizeAdsPortal({ review_board: board } as any);
}

describe("policyRemedy", () => {
  it("tells an advertiser to wait on a decision nobody has made", () => {
    const remedy = policyRemedy(entry({ moderation_status: "pending" }));
    expect(remedy.kind).toBe("wait");
    expect(remedy.text).toMatch(/no action is needed/i);
  });

  it("tells an advertiser to edit a rejection an automated check made alone", () => {
    const remedy = policyRemedy(
      entry({ moderation_status: "rejected", automated_review_status: "rejected", human_review_status: "" })
    );
    expect(remedy.kind).toBe("edit");
    expect(remedy.text).toMatch(/resubmit/i);
  });

  it("escalates a rejection a person upheld, and does not call it an appeal", () => {
    const remedy = policyRemedy(
      entry({ moderation_status: "rejected", automated_review_status: "flagged", human_review_status: "rejected" })
    );
    expect(remedy.kind).toBe("support");
    expect(remedy.text).toMatch(/support/i);
    // The appeals route is on the surface this deployment does not serve, so
    // the word must not appear anywhere the reader could take it as an offer.
    expect(remedy.text.toLowerCase()).not.toContain("appeal");
  });

  it("treats a human verdict on a still-pending row as a support case, not a wait", () => {
    const remedy = policyRemedy(entry({ moderation_status: "pending", human_review_status: "approved" }));
    expect(remedy.kind).toBe("support");
  });

  it("asks nothing of an approved creative", () => {
    expect(policyRemedy(entry({ moderation_status: "approved" })).kind).toBe("none");
  });
});

describe("policyGroups", () => {
  it("puts rejections first and drops groups with nothing in them", () => {
    const groups = policyGroups([
      entry({ review_id: 1, moderation_status: "approved" }),
      entry({ review_id: 2, moderation_status: "rejected" })
    ]);
    expect(groups.map((group) => group.key)).toEqual(["action", "cleared"]);
    // "In review" had no rows, so it is not a heading over nothing.
    expect(groups.some((group) => group.key === "review")).toBe(false);
  });

  it("orders by risk within a group, not by the id order the server returns", () => {
    const groups = policyGroups([
      entry({ review_id: 3, moderation_status: "rejected", risk_score: 12 }),
      entry({ review_id: 2, moderation_status: "rejected", risk_score: 91 }),
      entry({ review_id: 1, moderation_status: "rejected", risk_score: 40 })
    ]);
    expect(groups[0].entries.map((row) => row.risk_score)).toEqual([91, 40, 12]);
  });

  it("captions a group without restating its count as prose", () => {
    const groups = policyGroups([entry({ moderation_status: "rejected" })]);
    expect(groups[0].caption).not.toMatch(/\d/);
  });

  it("counts a blocked creative as needing attention", () => {
    expect(policyGroupFor(entry({ moderation_status: "blocked" }))).toBe("action");
  });
});

describe("policyCenterModel", () => {
  it("calls a board that was never fetched unavailable, not empty", () => {
    expect(policyCenterModel(null).state).toBe("unavailable");
    expect(policyCenterModel(undefined).state).toBe("unavailable");
  });

  it("calls a degraded portal unavailable, because its empty arrays are not answers", () => {
    const portal = { ...portalWith([]), degraded: true };
    expect(policyCenterModel(portal).state).toBe("unavailable");
  });

  it("reports zero counts when unavailable, so no tile can render a false all-clear", () => {
    const model = policyCenterModel(null);
    expect(model.actionCount).toBe(0);
    expect(model.reviewCount).toBe(0);
    expect(model.groups).toEqual([]);
  });

  it("calls a board that loaded and holds nothing empty", () => {
    expect(policyCenterModel(portalWith([])).state).toBe("empty");
  });

  it("counts rejections and pending decisions separately", () => {
    const model = policyCenterModel(
      portalWith([
        entry({ review_id: 1, moderation_status: "rejected" }),
        entry({ review_id: 2, moderation_status: "rejected" }),
        entry({ review_id: 3, moderation_status: "pending" }),
        entry({ review_id: 4, moderation_status: "approved" })
      ])
    );
    expect(model.state).toBe("ready");
    expect(model.actionCount).toBe(2);
    expect(model.reviewCount).toBe(1);
  });
});

describe("policyDecisionView", () => {
  it("reports a risk score only when the server recorded one", () => {
    expect(policyDecisionView(entry({ risk_score: 0 })).riskScore).toBeNull();
    expect(policyDecisionView(entry({ risk_score: 77 })).riskScore).toBe(77);
  });

  it("never leaves a rejection without a reason line", () => {
    const view = policyDecisionView(entry({ moderation_status: "rejected", review_reason: "" }));
    expect(view.reason.length).toBeGreaterThan(0);
  });

  it("hands a tone AdsStatusPill already understands", () => {
    expect(policyDecisionView(entry({ moderation_status: "rejected" })).tone).toBe("error");
    expect(policyDecisionView(entry({ moderation_status: "pending" })).tone).toBe("warning");
    expect(policyDecisionView(entry({ moderation_status: "approved" })).tone).toBe("success");
  });

  it("labels a pending row 'In review' rather than echoing the raw status word", () => {
    expect(policyDecisionView(entry({ moderation_status: "pending" })).statusLabel).toBe("In review");
  });
});

describe("policyDecidedBy", () => {
  it("says nothing when the server recorded neither verdict", () => {
    expect(policyDecidedBy(entry({ automated_review_status: "", human_review_status: "" }))).toBe("");
  });

  it("distinguishes a machine decision from one a person reviewed", () => {
    const machine = policyDecidedBy(entry({ automated_review_status: "rejected" }));
    const person = policyDecidedBy(entry({ automated_review_status: "flagged", human_review_status: "rejected" }));
    expect(machine).toMatch(/automated/i);
    expect(machine).toMatch(/hasn't reviewed/i);
    expect(person).toMatch(/by a person/i);
  });
});
