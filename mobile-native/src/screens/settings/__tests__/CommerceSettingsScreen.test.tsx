/**
 * The four controls, and the three things about them that are not obvious from
 * the diff.
 *
 *   - **The master switch writes the key the server reads.** Nothing in the app
 *     consumes `marketplaceRecommendations`; the switch exists to put that
 *     exact name into the synced document, where `viewer_policy` picks it up.
 *     So the assertions below are on the *payload*, not on the switch's own
 *     rendered state. A toggle that flipped visually and wrote nothing, or
 *     wrote a neighbouring key, would satisfy every screenshot and none of the
 *     users.
 *
 *   - **A lapsed pause is not a pause.** The stored value is an instant that
 *     the normalizer deliberately does not clear when it passes, so this screen
 *     is the thing standing between the user and "Paused until 3 August"
 *     rendered forever. Both sides of that are tested against a frozen clock.
 *
 *   - **Overridden controls are disabled, not hidden.** With the master switch
 *     off, frequency / personalization / pause still exist and still show their
 *     state — they are simply inert. Hiding them would leave no way to see what
 *     the settings were, and would make turning the master switch back on a
 *     surprise.
 */

import React from "react";
import { fireEvent, render, within } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: jest.fn(), goBack: jest.fn() })
}));

/**
 * A live in-memory preference group rather than a real `PreferencesProvider`.
 *
 * The provider would bring AsyncStorage hydration and the 400ms sync-coalescing
 * timer into every assertion here, neither of which these tests are about. What
 * matters is preserved: `setGroup` records the exact patch the screen asked
 * for, which is the whole contract with the backend.
 */
const mockSetGroup = jest.fn().mockResolvedValue(undefined);
let mockCommerce = {
  marketplaceRecommendations: true,
  personalizedRecommendations: true,
  frequency: "balanced" as "low" | "balanced" | "more",
  snoozeUntil: ""
};
jest.mock("../../../settings/store", () => ({
  usePreferenceGroup: () => ({
    value: mockCommerce,
    setGroup: mockSetGroup,
    pending: false,
    status: "idle",
    error: null
  }),
  // `SettingsShell` renders a `SyncStatusBar` off the whole context. Mocking
  // the module replaces every export, so this has to be stubbed too or the
  // shell throws before the screen under test renders at all.
  usePreferences: () => ({
    preferences: { commerce: mockCommerce },
    hydrated: true,
    refreshing: false,
    status: "idle",
    error: null,
    pendingGroups: [],
    update: mockSetGroup,
    refresh: jest.fn(),
    resetAll: jest.fn(),
    clearError: jest.fn()
  })
}));

import { CommerceSettingsScreen } from "../CommerceSettingsScreen";

const DAY_MS = 24 * 60 * 60 * 1000;
/** A fixed "now" so the pause arithmetic below is exact rather than approximate. */
const NOW = Date.parse("2026-06-01T12:00:00.000Z");

function setPreferences(patch: Partial<typeof mockCommerce>) {
  mockCommerce = { ...mockCommerce, ...patch };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockCommerce = {
    marketplaceRecommendations: true,
    personalizedRecommendations: true,
    frequency: "balanced",
    snoozeUntil: ""
  };
  jest.useFakeTimers().setSystemTime(NOW);
});

afterEach(() => {
  jest.useRealTimers();
});

/** The last patch handed to `setGroup`, which is what reaches the server. */
function lastPatch() {
  return mockSetGroup.mock.calls[mockSetGroup.mock.calls.length - 1][0];
}

describe("the master switch", () => {
  it("writes the key the ranking engine reads, spelled its way", () => {
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent(getByTestId("commerce-master-switch"), "press");

    // Asserted as an exact object: a patch that also carried, say, a reset
    // frequency would be writing a decision the user did not make.
    expect(lastPatch()).toEqual({ marketplaceRecommendations: false });
  });

  it("turns back on without disturbing anything else", () => {
    setPreferences({ marketplaceRecommendations: false, frequency: "low", personalizedRecommendations: false });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent(getByTestId("commerce-master-switch"), "press");

    expect(lastPatch()).toEqual({ marketplaceRecommendations: true });
  });
});

describe("what the master switch overrides", () => {
  it("leaves the dependent controls on screen, inert, still showing their state", () => {
    setPreferences({ marketplaceRecommendations: false, frequency: "more" });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    // Present — so the user can see what will come back.
    expect(getByTestId("commerce-frequency-more").props.accessibilityState.selected).toBe(true);
    // And inert.
    expect(getByTestId("commerce-frequency-low").props.accessibilityState.disabled).toBe(true);
    expect(getByTestId("commerce-personalization-switch").props.accessibilityState.disabled).toBe(true);
    expect(getByTestId("commerce-pause").props.accessibilityState.disabled).toBe(true);
  });

  it("writes nothing when a disabled control is pressed", () => {
    setPreferences({ marketplaceRecommendations: false });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent.press(getByTestId("commerce-frequency-low"));
    fireEvent.press(getByTestId("commerce-pause"));
    fireEvent(getByTestId("commerce-personalization-switch"), "press");

    // The disabled state is not decoration. A press that still wrote would
    // change a setting the screen is telling the user has no effect.
    expect(mockSetGroup).not.toHaveBeenCalled();
  });

  it("says why, instead of leaving an inert control unexplained", () => {
    setPreferences({ marketplaceRecommendations: false });
    const { getByTestId, getByText } = render(<CommerceSettingsScreen />);

    expect(getByTestId("commerce-frequency-note").props.children).toMatch(/no effect/i);
    expect(getByText("Suggestions are already off.")).toBeTruthy();
  });
});

describe("frequency", () => {
  it.each(["low", "balanced", "more"] as const)("sends %s exactly as the server spells it", (frequency) => {
    // "low" / "balanced" / "more" are keys into `FREQUENCY_MULTIPLIER` on the
    // Python side. A label change here is free; a value change is not.
    setPreferences({ frequency: frequency === "balanced" ? "low" : "balanced" });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent.press(getByTestId(`commerce-frequency-${frequency}`));

    expect(lastPatch()).toEqual({ frequency });
  });

  it("does not rewrite the value that is already chosen", () => {
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent.press(getByTestId("commerce-frequency-balanced"));

    // Re-selecting the current answer is a no-op, not a patch. Sending one
    // would burn a network write and bump the document revision for nothing.
    expect(mockSetGroup).not.toHaveBeenCalled();
  });

  it("exposes exactly one selected option", () => {
    setPreferences({ frequency: "low" });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    const selected = (["low", "balanced", "more"] as const).filter(
      (option) => getByTestId(`commerce-frequency-${option}`).props.accessibilityState.selected
    );
    expect(selected).toEqual(["low"]);
  });

  it("describes what the current choice means, and changes the description with it", () => {
    const balanced = render(<CommerceSettingsScreen />).getByTestId("commerce-frequency-note").props.children;
    setPreferences({ frequency: "more" });
    const more = render(<CommerceSettingsScreen />).getByTestId("commerce-frequency-note").props.children;

    expect(balanced).not.toBe(more);
    // The one invariant worth promising out loud, because "More" is exactly
    // where a user would expect it to stop being true.
    expect(more).toMatch(/never two in a row/i);
  });
});

describe("personalization", () => {
  it("is a separate decision from switching suggestions off", () => {
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent(getByTestId("commerce-personalization-switch"), "press");

    // Not `marketplaceRecommendations: false`. "Don't use my history" and
    // "don't show me products" are different objections and the engine answers
    // them differently — the first still serves, ranked on popularity.
    expect(lastPatch()).toEqual({ personalizedRecommendations: false });
  });
});

describe("the pause", () => {
  it("stores an absolute instant thirty days out, not a duration", () => {
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent.press(getByTestId("commerce-pause"));

    const patch = lastPatch();
    expect(Object.keys(patch)).toEqual(["snoozeUntil"]);
    expect(Date.parse(patch.snoozeUntil)).toBe(NOW + 30 * DAY_MS);
    // The server parses this with `datetime.fromisoformat`, so the shape is
    // part of the contract, not a formatting preference.
    expect(patch.snoozeUntil).toBe(new Date(NOW + 30 * DAY_MS).toISOString());
  });

  it("reports itself as running while the instant is in the future", () => {
    setPreferences({ snoozeUntil: new Date(NOW + 5 * DAY_MS).toISOString() });
    const { getByTestId, queryByTestId } = render(<CommerceSettingsScreen />);

    expect(getByTestId("commerce-pause-status")).toBeTruthy();
    expect(getByTestId("commerce-resume")).toBeTruthy();
    // The offer to start one is gone while one is running — two live pause
    // controls would leave "which of these am I in" unanswerable.
    expect(queryByTestId("commerce-pause")).toBeNull();
  });

  it("names the day it ends", () => {
    setPreferences({ snoozeUntil: new Date(NOW + 5 * DAY_MS).toISOString() });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    const status = within(getByTestId("commerce-pause-status"));
    // A pause with no visible end date is indistinguishable from an off
    // switch, which is the control one section up.
    expect(status.getByText(/^Paused until /)).toBeTruthy();
  });

  it("stops reporting a pause the moment the instant has passed", () => {
    // The stored value is left alone by the normalizer once it lapses — on
    // purpose, so that normalization does not depend on the clock. That makes
    // this screen the only thing preventing a month-old timestamp from
    // rendering as a live pause indefinitely.
    setPreferences({ snoozeUntil: new Date(NOW - 1000).toISOString() });
    const { getByTestId, queryByTestId } = render(<CommerceSettingsScreen />);

    expect(queryByTestId("commerce-pause-status")).toBeNull();
    expect(queryByTestId("commerce-resume")).toBeNull();
    expect(getByTestId("commerce-pause").props.accessibilityState.disabled).toBe(false);
  });

  it("ignores a stored value it cannot read rather than showing an unknown pause", () => {
    setPreferences({ snoozeUntil: "sometime next month" });
    const { queryByTestId, getByTestId } = render(<CommerceSettingsScreen />);

    expect(queryByTestId("commerce-pause-status")).toBeNull();
    expect(getByTestId("commerce-pause")).toBeTruthy();
  });

  it("ends the pause with an explicit empty string", () => {
    setPreferences({ snoozeUntil: new Date(NOW + 5 * DAY_MS).toISOString() });
    const { getByTestId } = render(<CommerceSettingsScreen />);

    fireEvent.press(getByTestId("commerce-resume"));

    // Not an omitted key: the patch is a partial group, so leaving
    // `snoozeUntil` out means "unchanged" and the pause would keep running.
    expect(lastPatch()).toEqual({ snoozeUntil: "" });
  });
});
