import fs from "fs";
import path from "path";
import { flattenMasterNavigation } from "../masterNavigation";
import { linking } from "../linking";
import {
  canonicalNativeRoute,
  MERCHANT_RESERVED_SEGMENTS,
  nativeObjectDestination,
  openNativeRoute
} from "../nativeRouteActions";

type NavigateCall = { screen: string; params?: any };

function makeNavigation() {
  const calls: NavigateCall[] = [];
  const navigation = {
    navigate: (screen: string, params?: any) => {
      calls.push({ screen, params });
    }
  };
  return { navigation, calls };
}

describe("PulseSoc navigation route resolution", () => {
  it("routes Activity, Notifications, and Notification Preferences to distinct destinations", () => {
    const activity = makeNavigation();
    openNativeRoute(activity.navigation, "/pulse/activity");
    expect(activity.calls).toEqual([{ screen: "ActivityInbox", params: { title: "Activity Inbox" } }]);

    const notifications = makeNavigation();
    openNativeRoute(notifications.navigation, "/pulse/notifications");
    expect(notifications.calls).toEqual([{ screen: "NotificationCenter", params: undefined }]);

    const preferences = makeNavigation();
    openNativeRoute(preferences.navigation, "/dashboard/network/notifications");
    expect(preferences.calls).toEqual([{ screen: "NotificationPreferences", params: undefined }]);

    const screens = [activity.calls[0].screen, notifications.calls[0].screen, preferences.calls[0].screen];
    expect(new Set(screens).size).toBe(3);
  });

  it("keeps Terms and Privacy Policy inside native Settings, not a browser", () => {
    const terms = makeNavigation();
    openNativeRoute(terms.navigation, "/terms");
    expect(terms.calls).toEqual([{ screen: "Tabs", params: { screen: "Settings" } }]);

    const privacy = makeNavigation();
    openNativeRoute(privacy.navigation, "/privacy");
    expect(privacy.calls).toEqual([{ screen: "Tabs", params: { screen: "Settings" } }]);
  });

  it.each([
    ["/pulse", "Tabs", "Home"],
    ["/pulse/dashboard", "Tabs", "Dashboard"],
    ["/pulse/search", "Tabs", "Search"],
    ["/pulse/messages", "Tabs", "Messenger"],
    ["/pulse/profile", "Tabs", "Profile"],
    ["/pulse/reels", "Tabs", "Reels"],
    ["/pulse/status", "Tabs", "Status"],
    ["/pulse/marketplace", "Tabs", "Marketplace"],
    ["/pulse/ai", "Tabs", "PulseAI"]
  ])("routes %s to the %s tab (%s)", (route, screen, tab) => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, route);
    expect(calls[0].screen).toBe(screen);
    expect(calls[0].params?.screen).toBe(tab);
  });

  it.each([
    ["/pulse/compose", "Tabs"],
    ["/pulse/camera/photo?target=feed", "CameraStudio"],
    ["/pulse/creator-studio", "CreatorStudio"],
    ["/pulse/profile/edit", "ProfileEdit"],
    ["/pulse/music#pulse-radio", "Music"],
    ["/pulse/premium", "Premium"],
    ["/pulse/undx/actions?org_id=coinplotxai&actor=user%3A7", "UndxActionCenter"],
    ["/scam-shield/scan", "ScamShield"],
    ["/dashboard/creator/content-planner", "ContentPlanner"],
    ["/dashboard/creator/draft-studio", "ContentPlanner"],
    ["/dashboard/account/security", "AccountCenter"]
  ])("routes %s to the %s native destination", (route, screen) => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, route);
    expect(calls.some((call) => call.screen === screen)).toBe(true);
  });

  it("keeps Create Post on the composer-first Home entry", () => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, "/pulse/compose");
    expect(calls[0]).toEqual({ screen: "Tabs", params: { screen: "Home", params: { openComposer: true } } });
  });

  it("opens Camera as the dedicated camera flow, not the combined composer", () => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, "/pulse/camera/photo?target=feed");
    expect(calls[0].screen).toBe("CameraStudio");
    expect(calls[0].params?.mode).toBe("photo");
    expect(calls[0].params?.target).toBe("feed");
  });

  it.each([
    ["/pulse/post/17", "PostDetail", { postId: 17, title: "Post" }],
    ["/pulse/reels/18", "ReelDetail", { reelId: 18, title: "Reel" }],
    ["/pulse/status/19", "StatusDetail", { statusId: 19, title: "Status" }],
    ["/pulse/live/20", "LiveDetail", { liveId: 20, title: "Live" }],
    ["/pulse/marketplace/21", "MarketplaceDetail", { listingId: 21, title: "Marketplace" }],
    ["/pulse/messages/22", "Chat", { conversationId: 22, title: "Conversation" }],
    ["/pulse/notifications/23", "NotificationCenter", { notificationId: 23 }],
    ["/pulse/events/24", "EventDetail", { eventId: 24, title: "Event" }],
    ["/pulse/businesses/acme%20labs", "MerchantProfile", { sellerId: "acme labs", title: "Business" }],
    ["/pulse/ads/25", "GrowthCenter", { contentType: "advertisement", contentId: 25, title: "Advertisement" }],
    ["/pulse/undx/tasks/market%20brief", "Tabs", { screen: "PulseAI", params: { taskId: "market brief" } }]
  ])("routes object link %s to %s", (route, screen, params) => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, route);
    expect(calls).toEqual([{ screen, params }]);
  });

  it("canonicalizes web, custom-scheme, and relative object links identically", () => {
    const routes = [
      "https://pulsesoc.com/pulse/post/42?ref=share",
      "pulsesoc://pulse/post/42?ref=share",
      "pulse/post/42?ref=share"
    ];
    for (const route of routes) {
      expect(canonicalNativeRoute(route).path).toBe("/pulse/post/42");
      expect(nativeObjectDestination(route)?.screen).toBe("PostDetail");
    }
  });

  it("uses the same object resolver for OS-level linking state", () => {
    const state = linking.getStateFromPath?.("https://pulsesoc.com/pulse/notifications/73");
    expect(state?.routes[0]).toEqual({
      name: "NotificationCenter",
      params: { notificationId: 73 }
    });
  });

  it("does not treat the profile editor as a public profile key", () => {
    const { navigation, calls } = makeNavigation();
    openNativeRoute(navigation, "/pulse/profile/edit");
    expect(calls).toEqual([{ screen: "ProfileEdit", params: undefined }]);
  });

  it("resolves every registered destination to a real native navigation target", () => {
    for (const action of flattenMasterNavigation()) {
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, action.route);
      const handled = calls.length > 0;
      expect({ route: action.route, handled }).toEqual({ route: action.route, handled: true });
    }
  });

  /**
   * `/pulse/merchant/<id>` is the canonical store link.
   *
   * `services/app_links.py` emits this spelling for every store URL the server
   * produces, and `linking.ts` maps `pulse/merchant/:sellerId` to MerchantProfile
   * — so a universal link opened from outside the app has always worked. The
   * in-app resolver only knew `store`/`business`, so tapping the identical path
   * inside the app resolved to null and silently did nothing. One path, two
   * resolvers, two answers.
   *
   * The aliases are pinned alongside it because a fix that only handles the
   * singular is the same bug one rename later.
   */
  describe("the canonical merchant path", () => {
    it.each(["/pulse/merchant/10", "/pulse/merchants/10", "/pulse/store/10", "/pulse/stores/10", "/pulse/business/10"])(
      "%s opens the storefront",
      (route) => {
        expect(nativeObjectDestination(route)).toEqual({
          screen: "MerchantProfile",
          params: { sellerId: "10", title: "Business" }
        });
      }
    );

    it("carries a non-numeric seller key through untouched", () => {
      expect(nativeObjectDestination("/pulse/merchant/acme-co")?.params).toEqual({
        sellerId: "acme-co",
        title: "Business"
      });
    });

    /**
     * The reason the matcher needs a lookahead rather than `([^/]+)`. These are
     * real screens with their own `linking.ts` entries; capturing "apply" as a
     * seller id would open an empty storefront instead of the seller application
     * and would break the three entry points `sellerEntryPoints.test.ts` guards.
     *
     * `payouts` is the one that already cost something: it is where Stripe
     * returns a seller at the end of Connect onboarding, and swallowing it as a
     * slug sent every finishing seller to a blank store.
     */
    it.each([
      "/pulse/merchant/apply",
      "/pulse/merchant/dashboard",
      "/pulse/merchant/payouts",
      "/pulse/merchant",
      "/pulse/merchant/"
    ])("%s is not a storefront", (route) => {
      expect(nativeObjectDestination(route)).toBeNull();
    });

    it("reserves exactly what app_links.py reserves for the store descriptor", () => {
      // The list is duplicated across the language boundary because the app
      // resolves deep links offline and cannot ask the server. Duplication is
      // fine; *drift* is the bug, and drift is silent — a segment added to the
      // backend's `reserved_ids` and not here does not fail anything, it just
      // starts opening the wrong screen. So the copy is pinned to its source.
      const appLinks = fs.readFileSync(
        path.resolve(__dirname, "..", "..", "..", "..", "services", "app_links.py"),
        "utf8"
      );
      const store = appLinks.slice(appLinks.indexOf('_d(\n        "store",'));
      const declared = store.slice(0, store.indexOf("display_name")).match(/reserved_ids=frozenset\(\{([^}]*)\}\)/);
      expect(declared).not.toBeNull();
      const backend = (declared as RegExpMatchArray)[1]
        .split(",")
        .map((entry) => entry.trim().replace(/^["']|["']$/g, ""))
        .filter(Boolean);
      expect([...MERCHANT_RESERVED_SEGMENTS].sort()).toEqual(backend.sort());
    });

    it("still sends the seller application to its own screen", () => {
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, "/pulse/merchant/apply");
      expect(calls.map((call) => call.screen)).not.toContain("MerchantProfile");
    });
  });
});
