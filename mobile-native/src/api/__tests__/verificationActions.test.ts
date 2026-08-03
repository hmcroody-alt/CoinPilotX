/**
 * The Verification Center shipped with a button that did not read the status:
 * `<ActionButton label="Start verification request" />` rendered for everyone,
 * including people who were already approved. These tests fix the answer to
 * "what may this person do right now" in one place, so no screen has to guess
 * it and no screen can disagree with another about it.
 *
 * The strongest of them is the last one. It walks every status the type allows
 * rather than the handful anyone thought to write a case for, because a status
 * that falls through to a default is exactly how the original defect looked.
 */
import { verificationActions, VerificationStatus } from "../verification";

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

/** Convenience: the labels of every button offered anywhere on the screen. */
function labels(status: VerificationStatus, requestId = 42) {
  const set = verificationActions({ status, requestId });
  return [...set.path, ...set.document, ...set.appeal].map((action) => action.label);
}

describe("verificationActions", () => {
  it("offers a first request only when there is no request", () => {
    const set = verificationActions({ status: "not_started", requestId: 0, selectedTrack: "identity" });
    expect(set.path).toEqual([{ key: "start", label: "Start Identity verification", variant: "primary" }]);
  });

  /**
   * The specific regression. Not merely "does not offer a start button" —
   * nothing offered anywhere on the screen may invite an approved person to
   * begin the thing they have finished.
   */
  it("never invites an approved person to start a verification request", () => {
    for (const requestId of [0, 42]) {
      for (const track of ["identity", "business", "blue_check", "government_id"] as const) {
        const set = verificationActions({ status: "approved", requestId, selectedTrack: track, currentTrack: "identity" });
        const everything = [...set.path, ...set.document, ...set.appeal];
        expect(everything.map((action) => action.key)).not.toContain("start");
        expect(everything.map((action) => action.label).join(" ")).not.toMatch(/start/i);
      }
    }
  });

  /**
   * "Update" and "Add another" are the same endpoint call. Which one a person
   * is offered is decided by whether the track they have selected is the one
   * they are already verified for — so the label has to follow the selection,
   * not a fixed guess.
   */
  it("calls it an update for the approved track and an addition for any other", () => {
    const same = verificationActions({ status: "approved", requestId: 42, selectedTrack: "identity", currentTrack: "identity" });
    expect(same.path).toEqual([{ key: "update", label: "Update your Identity details", variant: "secondary" }]);

    const other = verificationActions({ status: "approved", requestId: 42, selectedTrack: "business", currentTrack: "identity" });
    expect(other.path).toEqual([{ key: "add_another", label: "Add Business verification", variant: "secondary" }]);
  });

  it("offers nothing to press while a reviewer has the request", () => {
    for (const status of ["submitted", "in_review"] as const) {
      const set = verificationActions({ status, requestId: 42 });
      expect(set.path).toEqual([]);
      expect(set.appeal).toEqual([]);
      expect(set.canChooseTrack).toBe(false);
    }
  });

  /** Changing track mid-review would silently mean something the request
   *  endpoint cannot do, so the chooser is inert exactly then. */
  it("freezes the track chooser for every status a reviewer holds", () => {
    const frozen = ALL_STATUSES.filter((status) => !verificationActions({ status, requestId: 42 }).canChooseTrack);
    expect(frozen.sort()).toEqual(["appealed", "in_review", "needs_more_info", "submitted", "suspended"]);
  });

  it("makes sending a document the main thing to do when reviewers asked for one", () => {
    const set = verificationActions({ status: "needs_more_info", requestId: 42 });
    expect(set.document).toEqual([{ key: "document", label: "Choose a document", variant: "primary" }]);
    // The appeal stays available but yields the primary slot: doing what was
    // asked should be easier to reach than disputing it.
    expect(set.appeal[0].variant).toBe("secondary");
  });

  /**
   * `uploadDocument` fails with "start a request first" when `requestId` is 0.
   * Offering the button in that state is the dead-control defect, so the
   * derivation withholds it rather than letting the screen apologise for it.
   */
  it("withholds the document control when there is no request to attach it to", () => {
    for (const status of ALL_STATUSES) {
      expect(verificationActions({ status, requestId: 0 }).document).toEqual([]);
    }
  });

  it("offers an appeal only against a decision that has not already been appealed", () => {
    const appealable = ALL_STATUSES.filter((status) => verificationActions({ status, requestId: 42 }).appeal.length > 0);
    expect(appealable.sort()).toEqual(["needs_more_info", "rejected", "suspended"]);
  });

  it("lets a refused request be appealed or restarted, and a suspended one only appealed", () => {
    expect(labels("rejected")).toEqual(["Start a new Identity request", "Submit appeal"]);
    expect(labels("suspended")).toEqual(["Submit appeal"]);
  });

  it("tolerates a status the server invented, rather than offering nothing at all", () => {
    const set = verificationActions({ status: "totally_new_state" as VerificationStatus, requestId: 0 });
    expect(set.path.map((action) => action.key)).toEqual(["start"]);
    expect(set.headline).toBe("You have not started a verification request yet.");
  });

  it("spells Government ID as a person would write it", () => {
    const set = verificationActions({ status: "not_started", requestId: 0, selectedTrack: "government_id" });
    expect(set.path[0].label).toBe("Start Government ID verification");
  });

  /**
   * Every status must say where things stand in a sentence, because the panel
   * that used to hold the server's `primaryAction` string now holds this. A
   * blank or duplicated headline would leave that panel saying nothing.
   */
  it("gives every status its own plain-English headline", () => {
    const headlines = ALL_STATUSES.map((status) => verificationActions({ status, requestId: 42 }).headline);
    headlines.forEach((headline) => {
      expect(headline.length).toBeGreaterThan(20);
      expect(headline).toMatch(/[.!]$/);
    });
    expect(new Set(headlines).size).toBe(ALL_STATUSES.length);
  });

  /**
   * The copy class Tier 0.3 exists to remove. Nothing this derivation puts on
   * screen may name an endpoint, a route, or who owns a record.
   */
  it("says nothing about servers, routes or endpoints", () => {
    const banned = /server[- ]?(authoritative|owned|side)|endpoint|\/api\/|backend|payload|schema/i;
    for (const status of ALL_STATUSES) {
      const set = verificationActions({ status, requestId: 42 });
      const prose = [set.headline, ...set.path, ...set.document, ...set.appeal].map((item) => (typeof item === "string" ? item : item.label));
      prose.forEach((line) => expect(line).not.toMatch(banned));
    }
  });
});
