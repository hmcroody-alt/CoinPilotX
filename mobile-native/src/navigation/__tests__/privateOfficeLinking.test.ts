/**
 * Deep links under `pulse/private-office/`.
 *
 * Private Office was narrowed to three surfaces. The nine paths that left are
 * still in circulation — agent answers, old notifications, saved links — and
 * before the fallback existed they resolved to `undefined`, which React
 * Navigation drops silently. A dropped tap reads as a broken app, not as a
 * retired feature, so every unrecognised office path opens the office.
 *
 * The risk that fallback introduces is the mirror image: a *live* leaf whose
 * segment is missing from `LIVE_OFFICE_SEGMENTS` would be swallowed by the same
 * rule and quietly resolve to its own parent. That failure is invisible to a
 * test that names paths, because the swallowed path still returns a route. So
 * the coverage below is derived from the config rather than listed: whatever
 * leaves exist must each reach their own screen.
 */

import { linking } from "../linking";

function resolve(path: string) {
  const getState = linking.getStateFromPath;
  if (!getState) throw new Error("linking.getStateFromPath is not defined");
  return getState(path, linking.config as never);
}

function routeName(path: string) {
  const state = resolve(path);
  return state?.routes?.[state.routes.length - 1]?.name;
}

/** Every `pulse/private-office/...` path the static config actually binds. */
function officeLeafPaths(): string[] {
  const found: string[] = [];
  const walk = (node: unknown) => {
    if (typeof node === "string") {
      if (/^pulse\/private-office\/.+/.test(node)) found.push(node);
      return;
    }
    if (node && typeof node === "object") Object.values(node).forEach(walk);
  };
  walk(linking.config);
  return found;
}

describe("private office deep links", () => {
  it("binds at least the three retained surfaces", () => {
    // A guard on the guard: if `officeLeafPaths` ever stops finding anything —
    // a config reshuffle, a renamed key — the derived test below would pass
    // vacuously by iterating an empty list.
    expect(officeLeafPaths().sort()).toEqual([
      "pulse/private-office/meetings",
      "pulse/private-office/people",
      "pulse/private-office/security"
    ]);
  });

  it("sends every bound leaf to its own screen, not to the office", () => {
    officeLeafPaths().forEach((path) => {
      expect({ path, name: routeName(path) }).not.toEqual({ path, name: "PrivateOffice" });
      expect(routeName(path)).toBeTruthy();
    });
  });

  it("opens the office itself", () => {
    expect(routeName("pulse/private-office")).toBe("PrivateOffice");
  });

  it.each([
    "pulse/private-office/facts",
    "pulse/private-office/shield",
    "pulse/private-office/concierge",
    "pulse/private-office/briefings",
    "pulse/private-office/documents"
  ])("lands a retired path on the office: %s", (path) => {
    expect(routeName(path)).toBe("PrivateOffice");
  });

  it("lands a retired path's children on the office too", () => {
    // `PrivateOperations` was a `:view` wildcard, so the withdrawn links have
    // depth: `shield/overview` has to be caught by the same rule as `shield`.
    expect(routeName("pulse/private-office/shield/overview")).toBe("PrivateOffice");
    expect(routeName("pulse/private-office/facts/1234/sources")).toBe("PrivateOffice");
  });

  it("catches a path from a build that does not exist yet", () => {
    expect(routeName("pulse/private-office/some-future-surface")).toBe("PrivateOffice");
  });

  it("tolerates leading and trailing slashes and a query string", () => {
    expect(routeName("/pulse/private-office/facts")).toBe("PrivateOffice");
    expect(routeName("pulse/private-office/facts/")).toBe("PrivateOffice");
    expect(routeName("pulse/private-office/facts?ref=notification")).toBe("PrivateOffice");
  });

  it("leaves paths outside the office alone", () => {
    expect(routeName("pulse/private-officer/facts")).not.toBe("PrivateOffice");
    expect(routeName("pulse/crypto")).toBe("MarketPulse");
  });
});
