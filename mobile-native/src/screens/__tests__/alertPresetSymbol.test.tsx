/**
 * Does "Create Alert" on an asset actually arrive with that asset chosen?
 *
 * The mission's rule is that there is exactly one alert system, so tapping
 * Create Alert on SOL must open the canonical alert screen with SOL seeded —
 * not a second create form that happens to look similar. That makes the
 * interesting assertion a rendered one: the symbol field the user is looking at
 * has to say SOL. Asserting that the navigator was called with a `presetSymbol`
 * param would pass just as happily if the screen ignored the param entirely,
 * which is the actual failure worth guarding.
 *
 * Only the network edge of the alerts module is mocked. The form state, the
 * preset parsing and the reset behaviour under test are the real ones.
 */
import React from "react";
import { render, screen, fireEvent, act } from "@testing-library/react-native";

jest.mock("../../api/alerts", () => ({
  ...jest.requireActual("../../api/alerts"),
  getAlertManagementState: jest.fn().mockResolvedValue({
    alerts: [],
    cryptoAlerts: [],
    events: [],
    channel_readiness: {},
    worker: {}
  }),
  loadCachedAlertManagementState: jest.fn().mockResolvedValue(null),
  getAlertChannelReadiness: jest.fn().mockResolvedValue({}),
  getCryptoAlertHistory: jest.fn().mockResolvedValue({ events: [] }),
  loadCachedCryptoAlertHistory: jest.fn().mockResolvedValue(null),
  createCryptoAlert: jest.fn().mockResolvedValue({ ok: true, alert_id: 1, message: "Alert created." })
}));

// The screen is wrapped in the premium hard lock; these tests are about the
// preset symbol, not the gate, so the canonical tier is pinned to entitled.
jest.mock("../../entitlements/useCanonicalTier", () => ({
  useCanonicalTier: () => ({
    state: "resolved",
    effectiveTier: "PREMIUM",
    status: "active",
    source: "stripe",
    expiresAt: null,
    features: {},
    verifiedAt: "2026-01-01T00:00:00Z"
  }),
  loadCanonicalTier: jest.fn().mockResolvedValue(undefined),
  resetCanonicalTier: jest.fn()
}));

import { createCryptoAlert } from "../../api/alerts";
import { activateLocale } from "../../i18n/engine";
import { AlertManagementScreen } from "../AlertManagementScreen";

type ScreenProps = React.ComponentProps<typeof AlertManagementScreen>;

// This screen's copy lives in the `premium` namespace, which is extended-tier and
// so is not in memory by default. Loading the real English catalog keeps the
// assertions below written in the words a member actually reads; without it every
// label degrades to a humanized key and the queries silently stop matching.
beforeAll(async () => {
  await activateLocale("en");
});

function renderScreen(params: Record<string, unknown> | undefined) {
  // The navigator supplies a large prop object at runtime; the screen reads only
  // `route.params`, so the stub covers that plus the handful of navigation
  // methods it can call. Cast once, here, rather than at each use.
  const props = {
    route: { params, key: "alert-management-test", name: "AlertManagement" },
    navigation: { navigate: jest.fn(), setOptions: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => jest.fn()) }
  } as unknown as ScreenProps;
  return render(<AlertManagementScreen {...props} />);
}

describe("Create Alert carries the asset it was opened from", () => {
  it("seeds the symbol field with the asset the user came from", async () => {
    renderScreen({ presetSymbol: "SOL" });
    expect(await screen.findByDisplayValue("SOL")).toBeTruthy();
  });

  it("still defaults to BTC when opened directly, not from an asset", async () => {
    // The preset must be an addition to the canonical entry point, not a
    // replacement for its default.
    renderScreen(undefined);
    expect(await screen.findByDisplayValue("BTC")).toBeTruthy();
  });

  it("uppercases a lowercase symbol so it matches the engine's own casing", async () => {
    renderScreen({ presetSymbol: "sol" });
    expect(await screen.findByDisplayValue("SOL")).toBeTruthy();
  });

  it("sends the preset asset to the canonical create call, not a second path", async () => {
    // The point of the preset is that it flows into the one real alert engine
    // call. If the screen rendered SOL but posted BTC, every test above would
    // still pass and the user would get an alert on the wrong asset.
    renderScreen({ presetSymbol: "SOL" });
    fireEvent.changeText(await screen.findByLabelText("Alert target value"), "200");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });
    expect(createCryptoAlert).toHaveBeenCalledWith(expect.objectContaining({ assetSymbol: "SOL" }));
  });

  it("returns to the preset asset after a save, not to the global default", async () => {
    // Reset runs on a successful save, and it must land on "the asset I came
    // here from" so a second alert on SOL does not silently become one on BTC.
    renderScreen({ presetSymbol: "SOL" });
    fireEvent.changeText(await screen.findByLabelText("Alert target value"), "200");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Create alert" }));
    });
    expect(screen.getByDisplayValue("SOL")).toBeTruthy();
  });
});
