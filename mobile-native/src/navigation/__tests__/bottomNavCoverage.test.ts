/**
 * Every tab that lives under the dock must handle both halves of doing so.
 *
 * The two halves are independent in the code and were independent in practice:
 * a screen can drive the dock's hide/reveal gesture while padding its list by
 * whatever number happened to be in its stylesheet, and six tab screens did
 * exactly that — the dock behaved, and the last row of content sat underneath
 * it. Nothing caught that, because each screen looked self-consistent.
 *
 * This suite reads sources rather than rendering, on purpose. Rendering these
 * screens means standing up their data layers, and the property being checked
 * is structural: is the wiring present at all. A rendering test would be slower,
 * flakier, and would still only cover the screens someone remembered to add.
 * Driving the list from `BOTTOM_NAV_POLICY` instead means a new scroll-responsive
 * tab is covered the moment it is registered, not when someone thinks to.
 */

import { readFileSync } from "fs";
import { join } from "path";
import { BOTTOM_NAV_POLICY, BottomNavScreenPolicy } from "../bottomNavPolicy";
import { BOTTOM_NAV_CONTENT_CLEARANCE, BOTTOM_NAV_DOCK_PADDING_TOP, BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT } from "../bottomNavMetrics";

const SCREENS = join(__dirname, "..", "..", "screens");

/**
 * Which file renders each tab. Kept here rather than derived from
 * `AppNavigator.tsx` because several tabs share a component and two are
 * rendered through an inline element rather than a `component=` prop — parsing
 * that back out would be a worse source of truth than naming it.
 */
const TAB_SOURCES: Partial<Record<keyof typeof BOTTOM_NAV_POLICY, string>> = {
  Home: "HomeScreen.tsx",
  Reels: "ReelsScreen.tsx",
  Search: "SearchScreen.tsx",
  Saved: "SavedScreen.tsx",
  Groups: "GroupsScreen.tsx",
  Status: "StatusScreen.tsx",
  Messenger: "MessengerScreen.tsx",
  Notifications: "ActivityInboxScreen.tsx",
  Profile: "ProfileScreen.tsx",
  Marketplace: "MarketplaceScreen.tsx",
  Settings: "SettingsScreen.tsx"
};

/**
 * Tabs that drive the gesture but must not reserve clearance.
 *
 * Reels is a full-bleed vertical pager: every row is exactly `viewportHeight`
 * tall so that paging snaps cleanly. Bottom padding on that list would offset
 * the snap points and leave the pager permanently mis-aligned — the dock simply
 * floats over the video, which is the intended look.
 */
const NO_CLEARANCE = new Set<string>(["Reels"]);

/**
 * Screens that reach the dock through a shared shell rather than calling the
 * hooks themselves. The shell is asserted separately below.
 */
const SHELL_BACKED: Record<string, string> = {
  Settings: join(__dirname, "..", "..", "settings", "components", "SettingsShell.tsx")
};

function read(file: string) {
  return readFileSync(join(SCREENS, file), "utf8");
}

const scrollResponsiveTabs = (Object.keys(BOTTOM_NAV_POLICY) as Array<keyof typeof BOTTOM_NAV_POLICY>).filter(
  (tab) => (BOTTOM_NAV_POLICY[tab] as BottomNavScreenPolicy) === "scroll-responsive"
);

describe("dock wiring covers every scroll-responsive tab", () => {
  it("names a source file for each one", () => {
    const missing = scrollResponsiveTabs.filter((tab) => !TAB_SOURCES[tab]);
    expect(missing).toEqual([]);
  });

  it.each(scrollResponsiveTabs)("%s drives the hide/reveal gesture", (tab) => {
    const source = SHELL_BACKED[tab] ? readFileSync(SHELL_BACKED[tab], "utf8") : read(TAB_SOURCES[tab] as string);
    expect(source).toMatch(/useBottomNavSurface|useBottomNavScrollVisibility/);
    expect(source).toMatch(/onScroll=|\.\.\.dock\.handlers/);
  });

  it.each(scrollResponsiveTabs.filter((tab) => !NO_CLEARANCE.has(tab)))("%s reserves dock clearance", (tab) => {
    const source = SHELL_BACKED[tab] ? readFileSync(SHELL_BACKED[tab], "utf8") : read(TAB_SOURCES[tab] as string);
    // Either shape is fine: the hook that returns the padding, or the constant
    // added to the safe-area inset by hand. What is not fine is neither.
    expect(source).toMatch(/dock\.contentPadding|BOTTOM_NAV_CONTENT_CLEARANCE/);
  });

  it.each(scrollResponsiveTabs)("%s does not hardcode a dock-sized bottom padding", (tab) => {
    const source = SHELL_BACKED[tab] ? readFileSync(SHELL_BACKED[tab], "utf8") : read(TAB_SOURCES[tab] as string);
    // Anything in dock territory that is written as a literal is a number that
    // ignores the device's safe-area inset and will drift from the dock's real
    // height. That is the class of bug this whole module exists to remove.
    const literals = source.match(/paddingBottom:\s*(\d+)/g) || [];
    const offenders = literals.filter((match) => Number(match.replace(/\D/g, "")) >= 80);
    expect(offenders).toEqual([]);
  });
});

describe("dock clearance stays in step with the dock", () => {
  it("reserves at least the dock's own laid-out height", () => {
    // The dock is `paddingTop + panel` tall above whatever safe-area padding it
    // draws for itself. A surface reserving less than that hides its own last
    // row. This is the assertion the old standalone `92` would have failed.
    expect(BOTTOM_NAV_CONTENT_CLEARANCE).toBeGreaterThanOrEqual(
      BOTTOM_NAV_DOCK_PADDING_TOP + BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT
    );
  });

  it("lays the dock out from the same constants the clearance is derived from", () => {
    const source = readFileSync(join(__dirname, "..", "GlobalNavigation.tsx"), "utf8");
    expect(source).toContain("BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT");
    expect(source).toContain("BOTTOM_NAV_DOCK_PADDING_TOP");
    // If the panel's height goes back to a literal, the clearance above stops
    // tracking it and the two are free to drift apart again.
    expect(source).not.toMatch(/minHeight:\s*106/);
  });
});
