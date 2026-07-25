import {
  ACCESSIBLE_BANNER_MS,
  BannerNotification,
  DEFAULT_BANNER_MS,
  MIN_BANNER_MS,
  dismissBanner,
  initialBannerState,
  presentBanner,
  resolveAutoDismissMs
} from "../notificationBannerLifecycle";

function banner(overrides: Partial<BannerNotification> = {}): BannerNotification {
  return { id: "n1", title: "New message", ...overrides };
}

describe("resolveAutoDismissMs", () => {
  it("auto-dismisses temporary banners after the default duration", () => {
    expect(resolveAutoDismissMs(banner())).toBe(DEFAULT_BANNER_MS);
  });

  it("honors an explicit duration but never dips below the readable minimum", () => {
    expect(resolveAutoDismissMs(banner({ durationMs: 6000 }))).toBe(6000);
    expect(resolveAutoDismissMs(banner({ durationMs: 100 }))).toBe(MIN_BANNER_MS);
  });

  it("extends (never removes) the timer for screen-reader users", () => {
    expect(resolveAutoDismissMs(banner(), { screenReaderEnabled: true })).toBe(ACCESSIBLE_BANNER_MS);
    // A long explicit duration is respected rather than shortened.
    expect(resolveAutoDismissMs(banner({ durationMs: 12000 }), { screenReaderEnabled: true })).toBe(12000);
  });

  it("never auto-dismisses an intentionally persistent banner", () => {
    expect(resolveAutoDismissMs(banner({ persistent: true }))).toBeNull();
    expect(resolveAutoDismissMs(banner({ persistent: true }), { screenReaderEnabled: true })).toBeNull();
  });
});

describe("banner presentation + dismissal", () => {
  it("presents a banner and bumps the token", () => {
    const s0 = initialBannerState();
    const s1 = presentBanner(s0, banner());
    expect(s1.banner?.id).toBe("n1");
    expect(s1.token).toBe(1);
  });

  it("a new banner supersedes the previous one instead of stacking", () => {
    let state = presentBanner(initialBannerState(), banner({ id: "a", title: "A" }));
    state = presentBanner(state, banner({ id: "b", title: "B" }));
    expect(state.banner?.id).toBe("b");
    expect(state.token).toBe(2);
  });

  it("a stale timer from a superseded banner does NOT dismiss the current one", () => {
    let state = presentBanner(initialBannerState(), banner({ id: "a" }));
    const staleToken = state.token; // timer scheduled for banner A
    state = presentBanner(state, banner({ id: "b" })); // B replaces A
    const afterStaleTimer = dismissBanner(state, staleToken);
    // B must still be showing — the old timer is inert.
    expect(afterStaleTimer.banner?.id).toBe("b");
  });

  it("the current banner's own timer dismisses it", () => {
    const state = presentBanner(initialBannerState(), banner());
    const dismissed = dismissBanner(state, state.token);
    expect(dismissed.banner).toBeNull();
  });

  it("a forced dismiss (swipe/tap, no token) clears whatever is showing", () => {
    const state = presentBanner(initialBannerState(), banner());
    expect(dismissBanner(state).banner).toBeNull();
  });

  it("dismissing when nothing is showing is a safe no-op", () => {
    const s0 = initialBannerState();
    expect(dismissBanner(s0)).toEqual(s0);
  });
});
