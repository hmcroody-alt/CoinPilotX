/**
 * The Policy Center's dangerous default is `appealable`. A rejected creative
 * with an appeal already open answers a second appeal with a 409 — so a missing
 * flag must read as NOT appealable, never as an invitation to compose. The
 * component fallback matters for the same reason in the other direction: fix
 * guidance has to point at a real part of the ad, so unknown components resolve
 * to creative_text rather than rendering nothing.
 */

import {
  adAppealIsOpen,
  normalizeAdAppeal,
  normalizeAdPolicyCenter,
  normalizeAdPolicyRejection,
  openAppealForCreative
} from "../adsPolicyCenter";

describe("normalizeAdPolicyRejection", () => {
  it("reads appealable only from an explicit server true", () => {
    expect(normalizeAdPolicyRejection({ id: 1, appealable: true }).appealable).toBe(true);
    expect(normalizeAdPolicyRejection({ id: 1, appealable: 1 }).appealable).toBe(false);
    expect(normalizeAdPolicyRejection({ id: 1 }).appealable).toBe(false);
  });

  it("maps the affected component and falls back to creative_text", () => {
    expect(normalizeAdPolicyRejection({ id: 1, affected_component: "media" }).affected_component).toBe(
      "media"
    );
    expect(
      normalizeAdPolicyRejection({ id: 1, affected_component: "DESTINATION" }).affected_component
    ).toBe("destination");
    expect(
      normalizeAdPolicyRejection({ id: 1, affected_component: "vibes" }).affected_component
    ).toBe("creative_text");
  });

  it("keeps the rejection reason verbatim", () => {
    const rejection = normalizeAdPolicyRejection({
      id: 1,
      rejection_reason: "Destination page is unrelated to the ad."
    });
    expect(rejection.rejection_reason).toBe("Destination page is unrelated to the ad.");
  });
});

describe("normalizeAdAppeal / adAppealIsOpen", () => {
  it("defaults status to open and reads it case-insensitively", () => {
    expect(normalizeAdAppeal({ id: 1 }).status).toBe("open");
    expect(adAppealIsOpen({ status: "Open" })).toBe(true);
    expect(adAppealIsOpen({ status: "decided" })).toBe(false);
  });

  it("carries the decision fields for the history list", () => {
    const appeal = normalizeAdAppeal({
      id: 2,
      creative_id: 5,
      status: "decided",
      decision: "upheld",
      decision_reason: "The policy applies as written."
    });
    expect(appeal.decision).toBe("upheld");
    expect(appeal.decision_reason).toBe("The policy applies as written.");
  });
});

describe("normalizeAdPolicyCenter", () => {
  it("maps the board and drops id-less rejected/appeal rows", () => {
    const board = normalizeAdPolicyCenter({
      account_status: "active",
      verification_status: "verified",
      counts: { in_review: 2, approved: 10, rejected: 1, restricted: 0 },
      rejected: [{ id: 5, title: "Banner", appealable: true }, { id: 0 }],
      appeals: [{ id: 3, creative_id: 5, status: "open" }, {}],
      restrictions: [{ creative_id: 5, flag_type: "text_overlay", severity: "warning", details: "d" }]
    });
    expect(board.counts).toEqual({ in_review: 2, approved: 10, rejected: 1, restricted: 0 });
    expect(board.rejected).toHaveLength(1);
    expect(board.appeals).toHaveLength(1);
    expect(board.restrictions[0].flag_type).toBe("text_overlay");
  });

  it("degrades an empty payload to zero counts and empty lists", () => {
    const board = normalizeAdPolicyCenter(undefined);
    expect(board.counts).toEqual({ in_review: 0, approved: 0, rejected: 0, restricted: 0 });
    expect(board.rejected).toEqual([]);
    expect(board.appeals).toEqual([]);
  });
});

describe("openAppealForCreative", () => {
  it("finds the open appeal for one creative and ignores decided ones", () => {
    const appeals = [
      normalizeAdAppeal({ id: 1, creative_id: 5, status: "decided" }),
      normalizeAdAppeal({ id: 2, creative_id: 5, status: "open" }),
      normalizeAdAppeal({ id: 3, creative_id: 9, status: "open" })
    ];
    expect(openAppealForCreative(appeals, 5)?.id).toBe(2);
    expect(openAppealForCreative(appeals, 7)).toBeNull();
  });
});
