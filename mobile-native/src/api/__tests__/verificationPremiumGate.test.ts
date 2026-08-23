/**
 * Premium opens the Blue Check *application*. It does not buy the checkmark.
 *
 * The client half of that promise has two failure modes, and they pull in
 * opposite directions:
 *
 *   * showing a submit button to someone the server will refuse — the app
 *     promises something it cannot deliver, and the person finds out by being
 *     rejected;
 *   * hiding a submit button from someone who has already applied — a lapsed
 *     membership appears to have cancelled a review that is in fact still
 *     running.
 *
 * The lock therefore removes only the actions that would POST to the gated
 * endpoint, and never the status, the document handoff, or the appeal.
 */
const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import { loadVerificationState, verificationActions, verificationTracks, VerificationStatus } from "../verification";

jest.mock("../../core/cache", () => ({
  readJsonCache: jest.fn(async () => null),
  writeJsonCache: jest.fn(async () => undefined)
}));

const ALL_STATUSES: VerificationStatus[] = [
  "not_started",
  "draft",
  "submitted",
  "in_review",
  "needs_more_info",
  "approved",
  "rejected",
  "suspended",
  "appealed"
];

function locked(status: VerificationStatus, requestId = 42) {
  return verificationActions({ status, requestId, selectedTrack: "blue_check", currentTrack: "blue_check", canSubmitSelectedTrack: false });
}

function unlocked(status: VerificationStatus, requestId = 42) {
  return verificationActions({ status, requestId, selectedTrack: "blue_check", currentTrack: "blue_check", canSubmitSelectedTrack: true });
}

describe("the Blue Check track is the only one Premium gates", () => {
  it("marks exactly one track as needing a membership", () => {
    const gated = verificationTracks.filter((track) => track.premiumGated).map((track) => track.key);
    expect(gated).toEqual(["blue_check"]);
  });

  /**
   * Identity and Government ID are account safety, not a product tier. Putting
   * either behind a paywall would sell basic security back to the user.
   */
  it("leaves identity and government ID open to everyone", () => {
    for (const key of ["identity", "government_id", "business"] as const) {
      expect(verificationTracks.find((track) => track.key === key)?.premiumGated).toBe(false);
    }
  });
});

describe("a locked Blue Check track", () => {
  it("offers nothing that would post to the gated endpoint", () => {
    for (const status of ALL_STATUSES) {
      expect(locked(status).path).toEqual([]);
    }
  });

  it("reports itself as locked so the screen can offer the membership", () => {
    expect(locked("not_started", 0).premiumLocked).toBe(true);
  });

  /**
   * The riskiest sentence in the feature. It is read at the exact moment
   * someone is deciding whether to pay, so it must not let them believe the
   * payment produces a checkmark.
   */
  it("names the membership without promising the badge", () => {
    const headline = locked("not_started", 0).headline;
    expect(headline).toMatch(/Premium/);
    expect(headline).toMatch(/reviewer still decides/i);
    for (const lie of [/get verified/i, /guaranteed/i, /instant/i, /faster/i, /priority/i, /\$/]) {
      expect(headline).not.toMatch(lie);
    }
  });

  /**
   * Stage 9, on the client. Someone whose membership lapsed mid-review still
   * has a live case; the screen must keep telling them the truth about it
   * rather than replacing the status with a sales pitch.
   */
  it("keeps the real status for a request already with reviewers", () => {
    for (const status of ["submitted", "in_review", "needs_more_info", "suspended", "appealed"] as const) {
      expect(locked(status).headline).toBe(unlocked(status).headline);
      expect(locked(status).headline).not.toMatch(/Premium/);
    }
  });

  it("still lets a live request receive documents and be appealed", () => {
    expect(locked("needs_more_info").document.length).toBeGreaterThan(0);
    expect(locked("needs_more_info").appeal.length).toBeGreaterThan(0);
    expect(locked("rejected").appeal.length).toBeGreaterThan(0);
  });

  it("leaves the track chooser usable so another track can be picked", () => {
    expect(locked("not_started", 0).canChooseTrack).toBe(true);
  });
});

describe("an unlocked Blue Check track", () => {
  it("behaves exactly as the track did before the gate existed", () => {
    for (const status of ALL_STATUSES) {
      const before = verificationActions({ status, requestId: 42, selectedTrack: "blue_check", currentTrack: "blue_check" });
      expect(unlocked(status)).toEqual({ ...before, premiumLocked: false });
    }
  });

  it("offers a first application to a member who has not applied", () => {
    const set = unlocked("not_started", 0);
    expect(set.path.map((action) => action.key)).toEqual(["start"]);
    expect(set.premiumLocked).toBe(false);
  });
});

describe("the entitlement comes from the server, never from the Premium badge", () => {
  function respond(premium: Record<string, unknown>) {
    mockPulseApi.mockImplementation(async (path: string) => {
      if (path === "/api/dashboard/account/state") return { ok: true, account: { subsystems: { verification: {} } } };
      if (path === "/api/pulse/profile/me") return { ok: true, user: {} };
      if (path === "/api/premium/status") return premium;
      throw new Error(`unexpected call: ${path}`);
    });
  }

  beforeEach(() => mockPulseApi.mockReset());

  it("unlocks only on the server's explicit yes", async () => {
    respond({ premium_active: true, blue_check_apply: true });
    expect((await loadVerificationState()).blueCheckApplyAllowed).toBe(true);
  });

  /**
   * Holding a membership and holding this capability are two different facts.
   * Inferring the second from the first is how a client ends up rendering a
   * button the server refuses — for a member on a plan that does not grant it,
   * or one whose account is on hold.
   */
  it("stays locked for a Premium member the server did not grant it to", async () => {
    respond({ premium_active: true, blue_check_apply: false });
    expect((await loadVerificationState()).blueCheckApplyAllowed).toBe(false);
  });

  it("stays locked when the server says nothing at all", async () => {
    respond({ premium_active: true });
    expect((await loadVerificationState()).blueCheckApplyAllowed).toBe(false);
  });

  /**
   * An older server, or a truthy-looking value from a proxy, must not read as
   * permission. Only a real boolean true opens the form.
   */
  it("treats a non-boolean answer as locked", async () => {
    for (const value of ["true", 1, "yes", {}]) {
      respond({ premium_active: true, blue_check_apply: value });
      expect((await loadVerificationState()).blueCheckApplyAllowed).toBe(false);
    }
  });

  it("stays locked when the premium call fails outright", async () => {
    mockPulseApi.mockImplementation(async (path: string) => {
      if (path === "/api/premium/status") throw new Error("offline");
      if (path === "/api/dashboard/account/state") return { ok: true, account: { subsystems: { verification: {} } } };
      return { ok: true, user: {} };
    });
    expect((await loadVerificationState()).blueCheckApplyAllowed).toBe(false);
  });
});
