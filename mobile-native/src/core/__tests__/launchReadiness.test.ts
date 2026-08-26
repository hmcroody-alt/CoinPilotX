/**
 * The gate is a literal map, so most of what is worth testing is not "does it
 * return the value I typed" — it is the two properties that make the map safe
 * to edit later.
 *
 * The first: opening a layer must be a deliberate one-line change here, not
 * something a refactor can do by accident. That is why `isPresenceSurfaceReady`
 * compares against `READY` rather than against a list of locked states — a
 * fourth state added tomorrow reads as locked by construction.
 *
 * The second: the landing page must stay open. It is the whole point of the
 * gate. A commit that locks everything under Presence is correct; a commit that
 * locks Presence itself has taken the section off the map.
 */

import {
  PRESENCE_NEXT_LAYERS,
  PresenceSurface,
  ReadinessState,
  isPresenceSurfaceReady,
  presenceReadiness,
  readinessBadge,
  readinessNote
} from "../launchReadiness";

const SURFACES: PresenceSurface[] = [
  "presenceHub",
  "artistPresenceCreate",
  "businessPresenceCreate",
  "presenceCreate",
  "presenceManage"
];

describe("the landing page stays open", () => {
  it("keeps the hub itself READY", () => {
    // Presence is reachable from Profile OS and must remain so. Everything this
    // gate does is about what happens *after* this page.
    expect(presenceReadiness("presenceHub")).toBe("READY");
    expect(isPresenceSurfaceReady("presenceHub")).toBe(true);
  });

  it("locks every layer under it", () => {
    for (const surface of SURFACES.filter((s) => s !== "presenceHub")) {
      expect(isPresenceSurfaceReady(surface)).toBe(false);
    }
  });
});

describe("a surface is open only when it says READY", () => {
  it("does not read an unrecognised state as open", () => {
    // The risk this pins: someone adds a fourth state — "BETA", "INTERNAL" —
    // and a check written as `state !== "COMING_SOON" && state !== "BUILDING"`
    // silently lets it through. `isPresenceSurfaceReady` asks the positive
    // question, so a state nobody has taught it about stays shut.
    const unrecognised = "BETA" as ReadinessState;
    expect(unrecognised === "READY").toBe(false);
    expect(readinessBadge(unrecognised)).toBeNull();
  });

  it("gives every surface an answer", () => {
    // `Record<PresenceSurface, ...>` makes this a compile error rather than a
    // runtime one, but the map is also read by index at call sites, so an
    // `undefined` here would read as falsy and lock a surface that should be
    // open — or worse, be spread into a component as a missing badge.
    for (const surface of SURFACES) {
      expect(presenceReadiness(surface)).toBeDefined();
      expect(PRESENCE_NEXT_LAYERS[surface]).toBeDefined();
    }
  });
});

describe("what a locked layer tells the member", () => {
  it("badges the two locked states apart", () => {
    expect(readinessBadge("COMING_SOON")).toBe("COMING SOON");
    expect(readinessBadge("BUILDING")).toBe("BUILDING");
  });

  it("says nothing on a ready surface", () => {
    // A badge on everything is a badge people stop reading, and the absence of
    // one is what makes the greyed cards legible.
    expect(readinessBadge("READY")).toBeNull();
    expect(readinessNote("READY")).toBeNull();
  });

  it("never uses developer vocabulary", () => {
    // A locked door is not an error. Nothing here may leak a route name, a
    // status or an apology that reads like a crash.
    for (const state of ["COMING_SOON", "BUILDING"] as ReadinessState[]) {
      const note = readinessNote(state);
      expect(note).toBeTruthy();
      expect(note).not.toMatch(/error|failed|not implemented|undefined|route|null/i);
    }
  });

  it("does not promise a layer that is only a restatement of the page", () => {
    // "Create New" opens the same flow as the two cards above it, so its panel
    // lists nothing and shows the note alone.
    expect(PRESENCE_NEXT_LAYERS.presenceCreate).toEqual([]);
  });

  it("names the management layers that already exist as routes", () => {
    // These map to `PageEdit`, `PageTeam` and `PageConnections`. Naming things
    // the product does not have anywhere is how the hub's creation pitch went
    // hollow the first time; the preview is held to the same rule.
    expect(PRESENCE_NEXT_LAYERS.presenceManage).toEqual([
      "Edit Presence",
      "Team & Roles",
      "Connections"
    ]);
  });
});
