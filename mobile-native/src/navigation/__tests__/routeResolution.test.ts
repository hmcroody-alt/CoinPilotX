import { Linking } from "react-native";
import { flattenMasterNavigation } from "../masterNavigation";
import { openNativeRoute } from "../nativeRouteActions";

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
  let openURL: jest.SpyInstance;

  beforeEach(() => {
    openURL = jest.spyOn(Linking, "openURL").mockResolvedValue(true as never);
  });

  afterEach(() => {
    openURL.mockRestore();
  });

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

  it("opens Terms and Privacy Policy through the safe production web fallback, not the support screen", () => {
    const terms = makeNavigation();
    openNativeRoute(terms.navigation, "/terms");
    expect(terms.calls).toHaveLength(0);
    expect(openURL).toHaveBeenCalledWith(expect.stringContaining("/terms"));

    const privacy = makeNavigation();
    openNativeRoute(privacy.navigation, "/privacy");
    expect(privacy.calls).toHaveLength(0);
    expect(openURL).toHaveBeenCalledWith(expect.stringContaining("/privacy"));
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

  it("resolves every registered destination to a real navigation or web fallback (no dead routes)", () => {
    for (const action of flattenMasterNavigation()) {
      openURL.mockClear();
      const { navigation, calls } = makeNavigation();
      openNativeRoute(navigation, action.route);
      const handled = calls.length > 0 || openURL.mock.calls.length > 0;
      expect({ route: action.route, handled }).toEqual({ route: action.route, handled: true });
    }
  });
});
