import {
  notificationTargetFamily,
  routeNotificationTarget,
  setNotificationRouteReporter,
  type NotificationRouteReport
} from "../notificationRouting";

describe("routeNotificationTarget native-only policy", () => {
  beforeEach(() => {
    setNotificationRouteReporter(() => undefined);
  });

  afterEach(() => {
    setNotificationRouteReporter(null);
  });

  it("keeps an unmapped internal path native instead of opening the browser", async () => {
    const result = await routeNotificationTarget("/pulse/unmapped-analytics-page");
    expect(result).toMatchObject({
      handled: true,
      target: "/pulse/notifications",
      reason: "native_fallback",
      fallbackFrom: "/pulse/unmapped-analytics-page"
    });
  });

  it("strips query and fragment from the retained fallback diagnostics", async () => {
    const result = await routeNotificationTarget("/pulse/unmapped-page?token=secret#frag");
    expect(result.fallbackFrom).toBe("/pulse/unmapped-page");
  });

  it("keeps public legal documents inside native Settings boundaries", async () => {
    const result = await routeNotificationTarget("/terms");
    expect(result).toMatchObject({
      handled: true,
      target: "/terms",
      reason: "native_legal_boundary"
    });
  });

  it("does not misclassify a lookalike path as a legal exception", async () => {
    const result = await routeNotificationTarget("/terms-of-endearment");
    expect(result.reason).toBe("native_fallback");
  });

  it("rejects non-PulseSoc external hosts before any fallback", async () => {
    const result = await routeNotificationTarget("https://evil.example.com/phish");
    expect(result.target).toBe("/pulse/notifications");
    expect(result.reason).toBe("missing_target");
  });

  it("recovers safely from empty and malformed targets", async () => {
    for (const bad of ["", "   ", "javascript:alert(1)", "/api/private", "//evil", "not a url"]) {
      const result = await routeNotificationTarget(bad);
      expect(result.handled).toBe(true);
      expect(result.target).toBe("/pulse/notifications");
    }
  });

  it("stays stable across repeated taps of the same unmapped target", async () => {
    const first = await routeNotificationTarget("/pulse/unmapped-page");
    const second = await routeNotificationTarget("/pulse/unmapped-page");
    expect(first).toMatchObject(second);
  });

  it("emits structured native_fallback telemetry with a non-sensitive family", async () => {
    const reports: NotificationRouteReport[] = [];
    setNotificationRouteReporter((report) => reports.push(report));
    await routeNotificationTarget("/pulse/creator/analytics/9271?token=secret");
    const fallback = reports.find((r) => r.reason === "native_fallback");
    expect(fallback).toBeDefined();
    expect(fallback?.targetFamily).toBe("/pulse/creator/analytics");
    expect(fallback?.targetFamily).not.toContain("token");
  });
});

describe("notificationTargetFamily", () => {
  it("collapses ids and opaque segments and drops query/fragment", () => {
    expect(notificationTargetFamily("/pulse/post/12345?x=1#y")).toBe("/pulse/post/:id");
    expect(notificationTargetFamily("/pulse/profile/somelongopaquehandle0123456789")).toBe("/pulse/profile/:id");
    expect(notificationTargetFamily("/terms")).toBe("/terms");
  });
});
