import { Linking } from "react-native";
import {
  notificationTargetFamily,
  routeNotificationTarget,
  setNotificationRouteReporter,
  type NotificationRouteReport
} from "../notificationRouting";

describe("routeNotificationTarget web fallback policy", () => {
  let openURL: jest.SpyInstance;
  let canOpenURL: jest.SpyInstance;

  beforeEach(() => {
    openURL = jest.spyOn(Linking, "openURL").mockResolvedValue(true as never);
    canOpenURL = jest.spyOn(Linking, "canOpenURL").mockResolvedValue(true as never);
    setNotificationRouteReporter(() => undefined);
  });

  afterEach(() => {
    openURL.mockRestore();
    canOpenURL.mockRestore();
    setNotificationRouteReporter(null);
  });

  it("keeps an unmapped internal path native instead of opening the browser", async () => {
    const result = await routeNotificationTarget("/pulse/unmapped-analytics-page");
    expect(openURL).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      handled: true,
      target: "/pulse/notifications",
      reason: "native_fallback",
      fallbackFrom: "/pulse/unmapped-analytics-page"
    });
  });

  it("strips query and fragment from the retained fallback diagnostics", async () => {
    const result = await routeNotificationTarget("/pulse/unmapped-page?token=secret#frag");
    expect(openURL).not.toHaveBeenCalled();
    expect(result.fallbackFrom).toBe("/pulse/unmapped-page");
  });

  it("still opens public legal documents through the intentional web exception", async () => {
    const result = await routeNotificationTarget("/terms");
    expect(openURL).toHaveBeenCalledWith(expect.stringContaining("/terms"));
    expect(result).toMatchObject({
      handled: true,
      target: "/terms",
      reason: "intentional_web_exception"
    });
  });

  it("does not misclassify a lookalike path as a legal exception", async () => {
    const result = await routeNotificationTarget("/terms-of-endearment");
    expect(openURL).not.toHaveBeenCalled();
    expect(result.reason).toBe("native_fallback");
  });

  it("rejects non-PulseSoc external hosts before any fallback", async () => {
    const result = await routeNotificationTarget("https://evil.example.com/phish");
    expect(openURL).not.toHaveBeenCalled();
    expect(result.target).toBe("/pulse/notifications");
    expect(result.reason).toBe("missing_target");
  });

  it("recovers safely from empty and malformed targets", async () => {
    for (const bad of ["", "   ", "javascript:alert(1)", "/api/private", "//evil", "not a url"]) {
      openURL.mockClear();
      const result = await routeNotificationTarget(bad);
      expect(openURL).not.toHaveBeenCalled();
      expect(result.handled).toBe(true);
      expect(result.target).toBe("/pulse/notifications");
    }
  });

  it("stays stable across repeated taps of the same unmapped target", async () => {
    const first = await routeNotificationTarget("/pulse/unmapped-page");
    const second = await routeNotificationTarget("/pulse/unmapped-page");
    expect(openURL).not.toHaveBeenCalled();
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
