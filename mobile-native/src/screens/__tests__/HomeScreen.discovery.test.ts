/**
 * §1, §12, §15 — how discovery is attached to Home, asserted against the source.
 *
 * `HomeScreen.tsx` is three thousand lines and owns the feed, the status rail,
 * the composer, ads, the drawer, live polling and the spatial pager. Rendering
 * it in jest to check three lines of wiring would test a hundred unrelated
 * things and would fail for a hundred unrelated reasons. The behavior of the
 * pieces is covered properly elsewhere — `homeComposition.test.ts` runs the real
 * composition, `DiscoveryRowView.test.tsx` runs the real cards.
 *
 * What is left, and what this file covers, is the wiring itself: that the
 * composition happens in the right order, that the render branch exists, and —
 * most importantly — that the things which must NOT have changed did not. This
 * is the same source-reading approach `HomeScreen.layout.test.ts` already uses
 * for the bottom-dock clearance regression.
 */
import { readFileSync } from "fs";
import { join } from "path";

const homeSource = readFileSync(join(__dirname, "..", "HomeScreen.tsx"), "utf8");

describe("composition order (§3)", () => {
  it("still injects ads with the cadence Advertising owns", () => {
    // Discovery composes *around* ads; it does not get to restate their cadence.
    expect(homeSource).toContain("injectAds(posts, availableAds, { interval: 5, leadIn: 3 })");
  });

  it("threads suggestions through the ad-injected rows, not the raw posts", () => {
    // `injectDiscoveryRows(injectAds(...))` and not the reverse: running ads
    // second would let a carousel land between a post and the ad it earned.
    expect(homeSource).toMatch(/injectDiscoveryRows\(\s*injectAds\(/);
  });

  it("passes dismissals and the rotation offset through to placement", () => {
    expect(homeSource).toContain("dismissed: discovery.dismissed");
    expect(homeSource).toContain("rotationOffset: discovery.rotationOffset");
  });
});

describe("the render branch (§2)", () => {
  it("renders discovery rows through the one shared shell", () => {
    // One branch, one component. Seven per-kind branches would be seven places
    // to forget a key, a dismissal check or an analytics call.
    expect(homeSource).toContain('row.type === "discovery"');
    expect(homeSource).toContain("<DiscoveryRowView");
  });

  it("hands the shell the module and its feed position", () => {
    expect(homeSource).toMatch(/module=\{row\.module\}/);
    expect(homeSource).toMatch(/slot=\{row\.slot\}/);
  });

  it("resolves See all per kind rather than always rendering the control", () => {
    // `seeAllFor` returns undefined for kinds with no destination, which is what
    // keeps People from shipping a button that goes nowhere.
    expect(homeSource).toContain("onSeeAll={discovery.seeAllFor(row.module.kind)}");
  });
});

describe("gating (§13, §15)", () => {
  it("suppresses suggestions for a signed-out viewer", () => {
    expect(homeSource).toContain("enabled: isAuthenticated");
  });

  it("keeps the flag check out of Home entirely", () => {
    // The master flag lives in `discovery/flags.ts` and is read by the hook and
    // by `sources.ts`. A second check here would be a second thing to get wrong
    // during rollback.
    expect(homeSource).not.toContain("homeDiscoveryEnabled");
  });

  it("subtracts the status rail so a suggestion never duplicates what is on screen", () => {
    expect(homeSource).toContain("excludeStatusIds: railStatusIds");
  });
});

describe("refresh and return (§3, §12)", () => {
  it("advances the rotation on pull-to-refresh", () => {
    expect(homeSource).toContain("setDiscoveryRefreshToken((token) => token + 1)");
  });

  it("advances it on the tab re-tap refresh too", () => {
    // Both refresh paths, or the tab re-tap becomes the one route that reloads
    // the feed and leaves stale suggestions sitting in it.
    const bumps = homeSource.match(/setDiscoveryRefreshToken\(\(token\) => token \+ 1\)/g) || [];
    expect(bumps.length).toBeGreaterThanOrEqual(2);
  });

  it("does not reload suggestions on focus, so returning keeps the row in place", () => {
    // §12 is satisfied structurally: Home stays mounted on the stack and the
    // module list is keyed only on the refresh token, so `feedRows` is
    // referentially stable across a navigate-and-return and the FlatList keeps
    // its offset. A focus-keyed reload here would undo that.
    expect(homeSource).not.toMatch(/useHomeDiscovery\([^)]*isFocused/s);
  });
});

describe("what must not have changed (§1)", () => {
  it("keeps the Pulse Network hero, status rail and composer", () => {
    expect(homeSource).toContain("PulseNetworkHero");
    expect(homeSource).toContain("statusItems={statusItems}");
    expect(homeSource).toContain("HomePulseComposer");
  });

  it("keeps the feed tabs and the existing refresh control", () => {
    expect(homeSource).toContain("FEED_TABS");
    expect(homeSource).toContain("RefreshControl");
  });

  it("still keys every row the same way", () => {
    // Discovery rows carry their own `key`, so the extractor did not need to
    // change — and if it had, FlatList reuse across the whole feed would shift.
    expect(homeSource).toContain("keyExtractor={(row) => row.key}");
  });

  it("does not turn on spatial paging for Home", () => {
    // §1 forbids it. The flag remains the only thing that decides.
    expect(homeSource).toContain("spatialHomeFeedEnabled()");
  });
});
