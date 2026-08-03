import {
  clearRefreshCoordinatorForTests,
  registerRefreshDestination,
  registeredRefreshDestinations,
  resolveNavigationTap,
  scrollRefreshDestinationToTop,
  triggerRefreshDestination
} from "../refreshCoordinator";

describe("refreshCoordinator", () => {
  beforeEach(() => {
    clearRefreshCoordinatorForTests();
  });

  it("treats inactive destinations as navigation, not refresh", () => {
    expect(resolveNavigationTap({ active: false, destination: "home", controlId: "bottom:Home", now: 100 })).toEqual({ type: "navigate" });
  });

  it("returns root on first active tap and refresh intent on governed double tap", () => {
    expect(resolveNavigationTap({ active: true, destination: "profile", controlId: "bottom:Profile", now: 100 })).toEqual({
      type: "root",
      destination: "profile"
    });

    expect(resolveNavigationTap({ active: true, destination: "profile", controlId: "bottom:Profile", now: 360 })).toEqual({
      type: "refresh",
      intent: {
        destination: "profile",
        source: "double-tap",
        scrollToTop: true,
        preserveFilters: true,
        preserveDrafts: true
      }
    });
  });

  it("requires the same destination and same navigation control for a double tap", () => {
    expect(resolveNavigationTap({ active: true, destination: "home", controlId: "bottom:Home", now: 100 }).type).toBe("root");
    expect(resolveNavigationTap({ active: true, destination: "reels", controlId: "bottom:Reels", now: 220 })).toEqual({
      type: "root",
      destination: "reels"
    });
    expect(resolveNavigationTap({ active: true, destination: "reels", controlId: "header:Reels", now: 300 })).toEqual({
      type: "root",
      destination: "reels"
    });
  });

  it("does not assign refresh behavior to Create", () => {
    expect(resolveNavigationTap({ active: true, destination: null, controlId: "bottom:Create", now: 100 })).toEqual({ type: "create" });
    expect(registeredRefreshDestinations()).not.toContain("create");
  });

  it("fails duplicate destination registration", () => {
    const first = { scrollToTop: jest.fn(), refresh: jest.fn() };
    const second = { scrollToTop: jest.fn(), refresh: jest.fn() };
    const unregister = registerRefreshDestination("social-messages", first);
    expect(() => registerRefreshDestination("social-messages", second)).toThrow("Refresh destination already registered");
    unregister();
  });

  it("runs one refresh while a destination is already refreshing", () => {
    const scrollToTop = jest.fn();
    const refresh = jest.fn();
    registerRefreshDestination("home", {
      scrollToTop,
      refresh,
      isRefreshing: () => true
    });

    const intent = {
      destination: "home" as const,
      source: "double-tap" as const,
      scrollToTop: true,
      preserveFilters: true,
      preserveDrafts: true as const
    };

    expect(triggerRefreshDestination(intent)).toBe(false);
    expect(scrollToTop).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("separates root scrolling from refresh execution", () => {
    const scrollToTop = jest.fn();
    const refresh = jest.fn();
    registerRefreshDestination("marketplace-buying", { scrollToTop, refresh });

    expect(scrollRefreshDestinationToTop("marketplace-buying")).toBe(true);
    expect(scrollToTop).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });
});
