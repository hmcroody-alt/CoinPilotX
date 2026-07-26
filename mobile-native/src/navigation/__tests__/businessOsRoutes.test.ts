import { readFileSync } from "fs";
import { join } from "path";

import { activeBusinessOsSections, BUSINESS_OS_SECTIONS } from "../../api/businessOs";

/**
 * Business OS is a hub of links. A section pointing at a screen that is not
 * registered produces a silent no-op tap in production — exactly the kind of
 * dead control the unification is supposed to remove. These tests read the
 * navigator source so the registry cannot drift away from reality.
 */

const NAVIGATION_DIR = join(__dirname, "..");
const appNavigatorSource = readFileSync(join(NAVIGATION_DIR, "AppNavigator.tsx"), "utf8");
const typesSource = readFileSync(join(NAVIGATION_DIR, "types.ts"), "utf8");

function registeredStackRoutes() {
  return new Set(Array.from(appNavigatorSource.matchAll(/<Stack\.Screen\s+name="([^"]+)"/g)).map((m) => m[1]));
}

function declaredTabRoutes() {
  const block = typesSource.split("export type AppTabParamList = {")[1]?.split("};")[0] || "";
  return new Set(Array.from(block.matchAll(/^\s{2}([A-Za-z]+)[?]?:/gm)).map((m) => m[1]));
}

describe("Business OS section routes", () => {
  it("finds the navigator source it is guarding", () => {
    expect(registeredStackRoutes().size).toBeGreaterThan(20);
    expect(declaredTabRoutes().size).toBeGreaterThan(5);
  });

  it("registers every stack route a section points at", () => {
    const registered = registeredStackRoutes();
    const missing = activeBusinessOsSections()
      .filter((section) => !section.tab)
      .map((section) => section.route!)
      .filter((route) => !registered.has(route));
    expect(missing).toEqual([]);
  });

  it("declares every tab route a section points at", () => {
    const tabs = declaredTabRoutes();
    const missing = activeBusinessOsSections()
      .filter((section) => section.tab)
      .map((section) => section.route!)
      .filter((route) => !tabs.has(route));
    expect(missing).toEqual([]);
  });

  it("gives unbacked sections no route at all", () => {
    BUSINESS_OS_SECTIONS.filter((section) => !section.backed).forEach((section) => {
      expect(section.route).toBeUndefined();
    });
  });

  it("keeps the consumer Marketplace tab intact outside Business OS", () => {
    // Constraint: consumer discovery must remain reachable independently of the
    // seller tools that moved into Business OS.
    expect(declaredTabRoutes().has("Marketplace")).toBe(true);
  });
});
