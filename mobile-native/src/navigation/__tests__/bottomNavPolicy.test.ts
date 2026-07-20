import { BOTTOM_NAV_POLICY, isScrollResponsivePolicy, resolveBottomNavPolicy } from "../bottomNavPolicy";

describe("bottom navigation screen policy", () => {
  it("makes the immersive/feed tabs scroll-responsive", () => {
    for (const tab of ["Home", "Reels", "Profile", "Search", "Notifications", "Marketplace"]) {
      expect(resolveBottomNavPolicy(tab)).toBe("scroll-responsive");
      expect(isScrollResponsivePolicy(tab)).toBe(true);
    }
  });

  it("keeps dense control surfaces always visible", () => {
    for (const tab of ["Dashboard", "Settings", "Live", "PulseAI"]) {
      expect(resolveBottomNavPolicy(tab)).toBe("always-visible");
      expect(isScrollResponsivePolicy(tab)).toBe(false);
    }
  });

  it("treats the Create redirect tab as not-rendered", () => {
    expect(resolveBottomNavPolicy("Create")).toBe("not-rendered");
  });

  it("defaults unknown routes to scroll-responsive", () => {
    expect(resolveBottomNavPolicy(undefined)).toBe("scroll-responsive");
    expect(resolveBottomNavPolicy("SomeStackScreen")).toBe("scroll-responsive");
  });

  it("declares a policy for every registered tab", () => {
    expect(Object.keys(BOTTOM_NAV_POLICY).length).toBeGreaterThanOrEqual(15);
  });
});
