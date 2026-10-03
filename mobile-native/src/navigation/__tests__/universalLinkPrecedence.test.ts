/**
 * An explicit universal link outranks every default destination.
 *
 * `cartDeepLinkIdentity.test.ts` fixed one path through three resolvers. This
 * file pins the reason that path broke in the first place, which outlived the
 * fix: every OS-delivered URL was resolved twice.
 *
 * `linking` is attached to the NavigationContainer whenever a member is signed
 * in, and it declares no custom `subscribe` and no custom `getInitialURL` — so
 * React Navigation resolves every cold and warm URL through `config.screens`
 * itself. App.tsx then ran its *own* `Linking.addEventListener("url")` on a
 * mount-once effect and pushed the same URL into `routeNotificationTarget`
 * about 150ms later. That resolver is correct for a notification payload and
 * wrong here: it ends at `navigateToNotifications()` for anything it does not
 * recognise. Second answer wins, so the Activity Inbox replaced the destination
 * iOS had been given.
 *
 * It was not an edge case. Enumerating `config.screens` and asking both
 * resolvers for each path found 27 of 107 declared paths where the route table
 * resolved correctly and the notification resolver rewrote the answer —
 * `/search`, `/saved` and `/notifications` among them, and all three are
 * claimed in `services/native_app_links.py`'s `APPLE_LINK_COMPONENTS`, so they
 * arrive from the public website on a stock iPhone.
 *
 * So ownership is now explicit, in one function, asserted here:
 *
 *   - signed in  -> `linking` owns the URL, App.tsx does not touch it
 *   - signed out -> no config is attached, so the path is held and replayed
 *     through the same route table a cold launch would have used
 *   - foreign host -> nobody owns it
 */

import { inboundLinkOwner, inboundLinkPath, linking, linkingStateForPath } from "../linking";

/**
 * The screen `routeNotificationTarget` falls back to. Note that the route table
 * sends `/notifications` to `NotificationCenter` instead — the two resolvers do
 * not even agree on which notification screen is the notification screen.
 */
const INBOX_SCREEN = "ActivityInbox";
const NOTIFICATION_SCREENS = [INBOX_SCREEN, "NotificationCenter", "NotificationPreferences"];

function deepestRoute(state: any): { name?: string; params?: any } {
  let route = state?.routes?.[state.routes.length - 1];
  while (route?.state?.routes?.length) {
    route = route.state.routes[route.state.routes.length - 1];
  }
  return route || {};
}

/** Every path `config.screens` declares, nested navigators included. */
function declaredPaths(screens: any): string[] {
  const out: string[] = [];
  for (const key of Object.keys(screens || {})) {
    const value = screens[key];
    if (typeof value === "string") out.push(value);
    else if (value && typeof value === "object") {
      if (typeof value.path === "string") out.push(value.path);
      if (value.screens) out.push(...declaredPaths(value.screens));
    }
  }
  return out;
}

/** `pulse/settings/:section?` is a request for `/pulse/settings/anything`. */
function concretePath(declared: string) {
  return `/${declared.replace(/^\//, "").replace(/:[A-Za-z0-9_]+\??/g, "1").replace(/\/+$/, "")}`;
}

const DECLARED_PATHS = Array.from(new Set(declaredPaths((linking.config as any)?.screens)));

describe("who owns an OS-delivered URL", () => {
  it("hands a signed-in arrival to the navigation container alone", () => {
    // The whole defect in one assertion: while signed in, App.tsx must not
    // claim the URL, because React Navigation has already resolved it.
    expect(inboundLinkOwner("https://pulsesoc.com/saved", true)).toEqual({
      owner: "linking",
      path: "/saved"
    });
  });

  it("holds a signed-out arrival for replay rather than dropping it", () => {
    expect(inboundLinkOwner("https://pulsesoc.com/saved", false)).toEqual({
      owner: "replay",
      path: "/saved"
    });
  });

  it("refuses a link from any other host in either auth state", () => {
    // A URL handler is reachable by anyone who can get a link in front of the
    // member, so the host check is a security boundary, not tidiness.
    for (const signedIn of [true, false]) {
      expect(inboundLinkOwner("https://pulsesoc.com.evil.example/pulse/cart", signedIn).owner).toBe("none");
      expect(inboundLinkOwner("https://notpulsesoc.com/pulse/cart", signedIn).owner).toBe("none");
      expect(inboundLinkOwner("javascript:alert(1)", signedIn).owner).toBe("none");
      expect(inboundLinkOwner("file:///etc/passwd", signedIn).owner).toBe("none");
    }
  });
});

describe("the path a link is asking for", () => {
  it("keeps the query string, which carries the variant and the referrer", () => {
    expect(inboundLinkPath("https://pulsesoc.com/pulse/product/9?variant=3")).toBe(
      "/pulse/product/9?variant=3"
    );
  });

  it("folds the scheme host back into the path", () => {
    // `pulsesoc://pulse/cart` parses with hostname `pulse` and pathname
    // `/cart`; the request is `/pulse/cart`.
    expect(inboundLinkPath("pulsesoc://pulse/cart")).toBe("/pulse/cart");
  });

  it("accepts a pulsesoc.com subdomain but not a lookalike suffix", () => {
    expect(inboundLinkPath("https://www.pulsesoc.com/saved")).toBe("/saved");
    expect(inboundLinkPath("https://evilpulsesoc.com/saved")).toBe("");
  });

  it("bounds the path so a held target cannot be an arbitrary payload", () => {
    const long = inboundLinkPath(`https://pulsesoc.com/pulse/${"a".repeat(1000)}`);
    expect(long.length).toBe(240);
  });
});

describe("a held link replays to where a cold launch would have gone", () => {
  it.each(DECLARED_PATHS.map(concretePath))("places %s somewhere it asked for", (path) => {
    // The 27 divergent paths all failed the second half of this: the route
    // table placed them correctly and the replay — which used to go through
    // `routeNotificationTarget` — rewrote the answer to its own fallback.
    // Replaying through the route table makes a post-login arrival identical
    // to a cold one, so a path only reaches a notification screen by asking.
    const screen = deepestRoute(linkingStateForPath(path)).name;
    expect(screen).toBeDefined();
    if (!/notification|activity|inbox/i.test(path)) {
      expect(NOTIFICATION_SCREENS).not.toContain(screen);
    }
  });

  it("disagrees with the notification resolver about the AASA-claimed paths", () => {
    // Not a hypothetical: these are `components` entries in
    // services/native_app_links.py, so each arrives from the public site on a
    // stock iPhone. The two resolvers genuinely answer differently, which is
    // why ownership has to be exclusive rather than best-effort — whichever
    // one answers second wins, and it used to be this one.
    const claimed = ["/search", "/saved", "/pulse/settings", "/pulse/support"];
    for (const path of claimed) {
      expect(deepestRoute(linkingStateForPath(path)).name).not.toBe(INBOX_SCREEN);
    }
  });

  it("falls back rather than inventing a state for an unknown path", () => {
    // App.tsx only reaches `routeNotificationTarget` when this is undefined, so
    // an unmapped path must not resolve to something plausible-looking here.
    expect(linkingStateForPath("/pulse/unmapped-analytics-page")).toBeUndefined();
  });
});
