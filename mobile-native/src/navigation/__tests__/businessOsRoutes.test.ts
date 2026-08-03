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

  /**
   * Tier 0.2. A screen that draws its own header and is registered *without*
   * `headerShown: false` renders two headers: two titles, two back chevrons, one
   * stacked on the other. That is what Payments shipped with, and it is a defect
   * no unit render of the screen can catch, because the second header comes from
   * the navigator rather than the screen. It is only visible here, where the
   * registration lives.
   *
   * Each screen's rule is recorded verbatim rather than merely being checked for
   * the substring `false`, for two reasons. Advertising is legitimately
   * conditional — `AdvertisingRoute` picks between a rebuilt manager that draws
   * its own header and a classic screen that does not, so the stack header is
   * kept for exactly one of them — and a test that accepted any conditional
   * would accept that rule being inverted. And a screen quietly gaining a
   * condition where it previously had none is the same defect arriving by a
   * different route.
   *
   * Adding a screen that draws its own header means adding a line here. A
   * reviewer being made to think about which rule it needs is the point.
   */
  const HEADER_RULES: Record<string, string> = {
    BusinessOsPayments: "headerShown: false",
    BusinessOsInsights: "headerShown: false",
    BusinessOsOrders: "headerShown: false",
    BusinessOsMessages: "headerShown: false",
    BusinessOsEvents: "headerShown: false",
    BusinessOsActivity: "headerShown: false",
    MarketplaceManager: "headerShown: false",
    // The one screen whose header is conditional, and the condition itself:
    // true only for the classic screen, which draws no header of its own.
    BusinessOsAdvertising: 'headerShown: route.params?.mode === "classic"'
  };

  /** The registration itself, cut at its closing tag so trailing comments about
   *  the *next* screen cannot be read as part of this one's rule. */
  function registrationFor(name: string) {
    const at = appNavigatorSource.indexOf(`name="${name}"`);
    if (at < 0) return null;
    const close = appNavigatorSource.indexOf("/>", at);
    return appNavigatorSource.slice(at, close < 0 ? undefined : close + 2);
  }

  /** Every value assigned to `headerShown` inside one registration. */
  function headerShownValues(registration: string) {
    return Array.from(registration.matchAll(/headerShown:\s*([^\n,}]+)/g)).map((m) => m[1].trim());
  }

  it("gives every screen that draws its own header exactly the header rule it needs", () => {
    for (const [name, rule] of Object.entries(HEADER_RULES)) {
      const registration = registrationFor(name);
      expect(registration).toBeTruthy();
      // Normalised for whitespace only — the rule itself must match character
      // for character, so an inversion or a new condition fails loudly.
      expect(registration!.replace(/\s+/g, " ")).toContain(rule);
    }
  });

  /**
   * The specific regression. Payments is called from three places, one of which
   * passes no params at all, so a conditional here would reintroduce the double
   * header on the Business hub path while looking fine from Advertising.
   */
  it("hides the Payments stack header unconditionally, whoever navigated there", () => {
    expect(headerShownValues(registrationFor("BusinessOsPayments")!)).toEqual(["false"]);
  });

  it("keeps the consumer Marketplace tab intact outside Business OS", () => {
    // Constraint: consumer discovery must remain reachable independently of the
    // seller tools that moved into Business OS.
    expect(declaredTabRoutes().has("Marketplace")).toBe(true);
  });
});
