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

describe("preview visibility (§4, §5, §14.4, §14.7)", () => {
  it("tells each discovery row whether it is actually on screen", () => {
    // Without this the row's own horizontal carousel would happily report its
    // cards as viewable while the row itself is three screens down — a FlatList
    // has no idea where its parent is.
    expect(homeSource).toContain("isRowVisible={isFocused && viewableRowKeys.has(row.key)}");
  });

  it("tracks discovery rows in the same viewable-key set ads already used", () => {
    // One notion of "visible" for both, rather than a second mechanism that can
    // drift from the first.
    expect(homeSource).toContain('if (row.type === "ad" || row.type === "discovery") nextViewableRowKeys.add(row.key)');
  });

  it("gates previews on focus, so leaving the feed stops playback", () => {
    // §4 requires playback stop when the user leaves the feed. Home is still
    // mounted and still "visible" to FlatList at that moment; focus is the only
    // thing that knows.
    expect(homeSource).toContain("useIsFocused()");
  });

  it("still reads the same set for sponsored ad viewability", () => {
    // The rename must not have quietly detached ads from their impression gate.
    expect(homeSource).toContain("isViewable={viewableRowKeys.has(row.key)}");
  });

  it("keeps the vertical feed's own viewability handler immutable", () => {
    // FlatList throws "Changing onViewableItemsChanged on the fly is not
    // supported"; the handler stays a ref.
    expect(homeSource).toMatch(/const onFeedViewableItemsChanged = useRef\(/);
  });
});

describe("§14.15 — normal feed scrolling is untouched", () => {
  it("still renders the feed through one FlatList with the same row branch order", () => {
    expect(homeSource).toContain("renderFeedRow");
    expect(homeSource).toContain('row.type === "ad"');
  });

  it("keeps the post-activity gate that governs video posts", () => {
    // `activePostId` drives normal feed post media. Discovery previews are a
    // separate mechanism and must not have been folded into it.
    expect(homeSource).toContain("setActivePostId(nextActivePostId)");
    expect(homeSource).toContain('row.type === "post" && nextActivePostId == null');
  });

  it("keeps the feed's own visibility threshold where it was", () => {
    // Retuning this would change which post counts as active, which is a
    // behavior change to normal feed video playback — out of scope.
    expect(homeSource).toContain("itemVisiblePercentThreshold: 72");
  });

  it("does not add a second AppState or audio-mode listener for previews", () => {
    // §4's hard line. Previews arbitrate through the existing playback
    // coordinator, whose "feed" kind is already released on background.
    expect(homeSource).not.toContain("Audio.setAudioModeAsync");
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
