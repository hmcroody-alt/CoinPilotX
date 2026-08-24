/**
 * Production Dashboard Map — rendered-screen regression suite.
 *
 * The pure mapping is proven in `core/__tests__/dashboardMapNavigation.test.ts`.
 * What only a rendered screen can prove is tested here: that every one of the
 * 11 rail tiles is actually pressable, that a press scrolls to the measured
 * position of exactly that tile's section (heading aligned near the top, never
 * an approximate offset), that repeated taps keep working, that the deep
 * `section` route param lands the same way, that Reduce Motion switches off
 * the smooth animation, and that every tile carries a meaningful localized
 * accessibility label — not "Button".
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import { ScrollView } from "react-native";

const mockImpact = jest.fn().mockResolvedValue(undefined);
jest.mock("expo-haptics", () => ({
  impactAsync: (...args: unknown[]) => mockImpact(...args),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" }
}));

let mockReducedMotion = false;
jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => mockReducedMotion,
  createLogiNexusAmbientPulse: () => ({ start: jest.fn(), stop: jest.fn() })
}));

let mockRouteParams: { section?: string } | undefined;
const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useRoute: () => ({ key: "dashboard", name: "Dashboard", params: mockRouteParams })
}));

jest.mock("../../components/Screen", () => {
  const { View } = jest.requireActual("react-native");
  return {
    LogiNexusScreenShell: ({ children }: { children?: React.ReactNode }) => <View>{children}</View>,
    LogiNexusStatePanel: () => null
  };
});

jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      const path = key.replace(":", ".").split(".");
      let node: unknown = jest.requireActual("../../i18n/catalogs/en/extended.json");
      for (const part of path) node = node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined;
      const template = typeof node === "string" ? node : key;
      return template.replace(/\{\{(\w+)\}\}/g, (_, name) => String(params?.[name] ?? ""));
    }
  })
}));

jest.mock("../../api/dashboard", () => ({
  ...jest.requireActual("../../api/dashboard"),
  loadUserDashboardState: () =>
    Promise.resolve({
      user: null,
      profile: null,
      activity: null,
      calls: [],
      buyerOrders: [],
      creator: null,
      growth: null,
      intelligence: null,
      cards: [],
      quickActions: [],
      moduleGroups: jest.requireActual("../../data/dashboardModules").dashboardModuleGroups,
      dashboardQuickActionLinks: [],
      recentActivity: [],
      warnings: []
    })
}));

import { UserDashboardScreen } from "../UserDashboardScreen";
import {
  DASHBOARD_MAP_SECTIONS,
  DASHBOARD_SECTION_TOP_CLEARANCE
} from "../../core/dashboardMapNavigation";

/** Deterministic fake layout: section n sits at 900 + n * 700 in content coordinates. */
const SECTION_Y = new Map(DASHBOARD_MAP_SECTIONS.map((section, index) => [section.groupKey, 900 + index * 700]));

function layoutAllSections(getByTestId: (id: string) => unknown) {
  for (const section of DASHBOARD_MAP_SECTIONS) {
    fireEvent(getByTestId(`dashboard-map-section-${section.groupKey}`) as never, "layout", {
      nativeEvent: { layout: { x: 0, y: SECTION_Y.get(section.groupKey), width: 390, height: 680 } }
    });
  }
}

async function renderDashboard() {
  const utils = render(<UserDashboardScreen />);
  await waitFor(() => utils.getByTestId("dashboard-map-tile-account-command-center"));
  layoutAllSections(utils.getByTestId);
  return utils;
}

let scrollToSpy: jest.SpyInstance;

beforeEach(() => {
  jest.clearAllMocks();
  mockReducedMotion = false;
  mockRouteParams = undefined;
  scrollToSpy = jest.spyOn(ScrollView.prototype, "scrollTo").mockImplementation(() => undefined);
});

afterEach(() => {
  scrollToSpy.mockRestore();
});

describe("Production Dashboard Map tiles", () => {
  test.each(DASHBOARD_MAP_SECTIONS.map((s) => [s.sectionId, s.groupKey] as const))(
    "%s tile lands exactly on its own section",
    async (_sectionId, groupKey) => {
      const { getByTestId } = await renderDashboard();
      fireEvent.press(getByTestId(`dashboard-map-tile-${groupKey}`));
      expect(scrollToSpy).toHaveBeenCalledTimes(1);
      expect(scrollToSpy).toHaveBeenCalledWith({
        y: (SECTION_Y.get(groupKey) as number) - DASHBOARD_SECTION_TOP_CLEARANCE,
        animated: true
      });
    }
  );

  it("no tile is a no-op and no two tiles share a destination", async () => {
    const { getByTestId } = await renderDashboard();
    const targets: number[] = [];
    for (const section of DASHBOARD_MAP_SECTIONS) {
      scrollToSpy.mockClear();
      fireEvent.press(getByTestId(`dashboard-map-tile-${section.groupKey}`));
      expect(scrollToSpy).toHaveBeenCalledTimes(1);
      targets.push((scrollToSpy.mock.calls[0][0] as { y: number }).y);
    }
    expect(new Set(targets).size).toBe(11);
  });

  it("keeps the section heading visible below the top edge (clearance offset)", async () => {
    const { getByTestId } = await renderDashboard();
    fireEvent.press(getByTestId("dashboard-map-tile-system-status"));
    const { y } = scrollToSpy.mock.calls[0][0] as { y: number };
    expect(y).toBeLessThan(SECTION_Y.get("system-status") as number);
    expect((SECTION_Y.get("system-status") as number) - y).toBe(DASHBOARD_SECTION_TOP_CLEARANCE);
  });

  it("repeated taps keep landing on the same exact section", async () => {
    const { getByTestId } = await renderDashboard();
    const tile = getByTestId("dashboard-map-tile-crypto-command-center");
    fireEvent.press(tile);
    fireEvent.press(tile);
    fireEvent.press(tile);
    expect(scrollToSpy).toHaveBeenCalledTimes(3);
    const ys = scrollToSpy.mock.calls.map((call) => (call[0] as { y: number }).y);
    expect(new Set(ys).size).toBe(1);
  });

  it("does not push navigation entries for same-page jumps", async () => {
    const { getByTestId } = await renderDashboard();
    fireEvent.press(getByTestId("dashboard-map-tile-pulse-network"));
    expect(mockNavigate).not.toHaveBeenCalled();
  });

  it("fires a light haptic on press", async () => {
    const { getByTestId } = await renderDashboard();
    fireEvent.press(getByTestId("dashboard-map-tile-economy-earnings"));
    expect(mockImpact).toHaveBeenCalledWith("light");
  });
});

describe("Reduce Motion", () => {
  it("jumps without smooth animation when Reduce Motion is on", async () => {
    mockReducedMotion = true;
    const { getByTestId } = await renderDashboard();
    fireEvent.press(getByTestId("dashboard-map-tile-moderation-safety"));
    expect(scrollToSpy).toHaveBeenCalledWith({
      y: (SECTION_Y.get("moderation-safety") as number) - DASHBOARD_SECTION_TOP_CLEARANCE,
      animated: false
    });
  });
});

describe("deep section parameter", () => {
  it("MissionControl(section=\"crypto\") lands on the Crypto section once measured", async () => {
    mockRouteParams = { section: "crypto" };
    const { getByTestId } = await renderDashboard();
    await act(async () => undefined);
    expect(scrollToSpy).toHaveBeenCalledWith({
      y: (SECTION_Y.get("crypto-command-center") as number) - DASHBOARD_SECTION_TOP_CLEARANCE,
      animated: true
    });
  });

  it("ignores unknown section ids instead of landing approximately", async () => {
    mockRouteParams = { section: "not-a-section" };
    await renderDashboard();
    await act(async () => undefined);
    expect(scrollToSpy).not.toHaveBeenCalled();
  });
});

describe("accessibility", () => {
  it("every tile exposes a meaningful localized label", async () => {
    const { getByLabelText } = await renderDashboard();
    expect(getByLabelText("Open Account Command Center")).toBeTruthy();
    expect(getByLabelText("Open Network Command Center")).toBeTruthy();
    expect(getByLabelText("Open Creator Command Center")).toBeTruthy();
    expect(getByLabelText("Open Intelligence Command Center")).toBeTruthy();
    expect(getByLabelText("Open Economy Command Center")).toBeTruthy();
    expect(getByLabelText("Open Media Command Center")).toBeTruthy();
    expect(getByLabelText("Open Crypto Command Center")).toBeTruthy();
    expect(getByLabelText("Open Safety Command Center")).toBeTruthy();
    expect(getByLabelText("Open Ads Command Center")).toBeTruthy();
    expect(getByLabelText("Open AI Command Center")).toBeTruthy();
    expect(getByLabelText("Open System Command Center")).toBeTruthy();
  });
});
