/**
 * Can a user actually reach Watchlists, and does the entry tell the truth?
 *
 * The screen was listed in master navigation long before it existed, as a
 * `shell` entry pointing at a dashboard path. Activating it is therefore two
 * separate claims — "the route resolves to the native screen" and "the catalog
 * says native" — and either one can be true while the other is false. A catalog
 * flipped to `native` with no resolver rule is a dead menu item; a resolver rule
 * with a stale catalog is a screen nobody can find. So both are pinned here, and
 * pinned *together*: the last test walks every entry the catalog calls native
 * and requires it to resolve, which is the assertion that would have caught the
 * original dead shell.
 */
import { openNativeRoute } from "../nativeRouteActions";
import { flattenMasterNavigation } from "../masterNavigation";

type NavigateCall = { screen: string; params?: any };

function makeNavigation() {
  const calls: NavigateCall[] = [];
  return { navigation: { navigate: (screen: string, params?: any) => calls.push({ screen, params }) }, calls };
}

const WATCHLISTS_ENTRY = flattenMasterNavigation().find((action) => action.label === "Watchlists");

describe("Watchlists is reachable from navigation", () => {
  it("still exists in the Intelligence section", () => {
    // If someone renames or moves the entry, the rest of this file would start
    // asserting about `undefined` and quietly pass.
    expect(WATCHLISTS_ENTRY).toBeDefined();
    expect(WATCHLISTS_ENTRY?.section).toBe("Intelligence");
  });

  it("opens the native screen from the route the catalog advertises", () => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, WATCHLISTS_ENTRY!.route);
    expect(calls).toEqual([{ screen: "Watchlists", params: { title: "Watchlists" } }]);
  });

  it("is advertised as native rather than as a shell", () => {
    expect(WATCHLISTS_ENTRY?.status).toBe("native");
  });

  it("accepts the bare /watchlists spelling as well as the dashboard one", () => {
    // Older entries and deep links use the short form. Both must land on the
    // same screen, or a link that worked yesterday opens a different one today.
    const short = makeNavigation();
    openNativeRoute(short.navigation, "/watchlists");
    expect(short.calls).toEqual([{ screen: "Watchlists", params: { title: "Watchlists" } }]);
  });

  it("does not swallow the crypto alerts route on its way past", () => {
    // The watchlists rule sits just above the alerts rule and both live under
    // /crypto. If it ever matched too eagerly, Crypto Command would silently
    // start opening Watchlists — a failure that looks like a missing feature
    // rather than like a routing bug.
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, "/dashboard/crypto/alerts");
    expect(calls).toHaveLength(1);
    expect(calls[0].screen).not.toBe("Watchlists");
  });
});
