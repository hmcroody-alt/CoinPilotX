/**
 * Biometric unlock and two-factor, on the screen that now owns them.
 *
 * This file replaces `screens/__tests__/SettingsScreen.biometric.test.tsx`. The
 * behaviour it covered did not disappear when the settings index was rebuilt —
 * it moved here, along with the rest of the account-security surface. The
 * assertions are deliberately about *what the user sees and what gets called*,
 * not about internals, so they survived the move with only the testIDs changing.
 *
 * The controls are now `SettingsSwitch`es rather than bespoke pressables, and
 * destructive paths route through `confirm()` — which is an `Alert.alert` with
 * a two-button array whose second entry resolves the promise. Tests that need
 * to confirm therefore have to invoke that button, exactly as a user would.
 */

import React from "react";
import { Alert } from "react-native";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import type { ReactTestInstance } from "react-test-renderer";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: mockNavigate, goBack: jest.fn() })
}));

jest.mock("../../../session/auth", () => ({
  signOut: jest.fn(),
  signOutEverywhere: jest.fn(),
  useAuth: () => ({
    setAuthState: jest.fn(),
    authState: { status: "signedIn", user: { user_id: 5, email: "roody@example.com" } }
  })
}));

jest.mock("../../../api/auth", () => ({ requestPasswordRecovery: jest.fn() }));

jest.mock("../../../api/account", () => ({
  getAccountSecurity: jest.fn(),
  enableTwoFactor: jest.fn(),
  disableTwoFactor: jest.fn(),
  requestAccountPasswordChange: jest.fn()
}));

jest.mock("../../../session/biometricAuth", () => ({
  getBiometricCapability: jest.fn(),
  isBiometricEnabledForCurrentSession: jest.fn(),
  confirmAndEnableBiometricLogin: jest.fn(),
  disableBiometricLogin: jest.fn()
}));

/**
 * The screen reads and writes preferences. A real `PreferencesProvider` would
 * drag AsyncStorage hydration and a sync timer into every assertion, so the hook
 * is stubbed with a live in-memory value instead — `setGroup` still records the
 * writes, which is the part these tests care about.
 */
const mockSetGroup = jest.fn().mockResolvedValue(undefined);
let mockSecurityPreferences = {
  twoFactorEnabled: false,
  biometricUnlock: false,
  loginAlerts: true,
  requirePasswordForSensitiveChanges: true
};
jest.mock("../../../settings/store", () => ({
  usePreferenceGroup: () => ({
    value: mockSecurityPreferences,
    setGroup: mockSetGroup,
    pending: false,
    status: "idle",
    error: null
  }),
  // `SettingsShell` renders a `SyncStatusBar` that reads the whole context.
  // Mocking the module replaces every export, so this one has to be stubbed
  // too or the shell throws before the screen under test ever renders.
  usePreferences: () => ({
    preferences: { security: mockSecurityPreferences },
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

import { getAccountSecurity, enableTwoFactor, disableTwoFactor, requestAccountPasswordChange } from "../../../api/account";
import {
  getBiometricCapability,
  isBiometricEnabledForCurrentSession,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin
} from "../../../session/biometricAuth";
import { SecuritySettingsScreen } from "../SecuritySettingsScreen";

const mockedCapability = getBiometricCapability as jest.Mock;
const mockedIsEnabled = isBiometricEnabledForCurrentSession as jest.Mock;
const mockedConfirmEnable = confirmAndEnableBiometricLogin as jest.Mock;
const mockedDisable = disableBiometricLogin as jest.Mock;
const mockedGetSecurity = getAccountSecurity as jest.Mock;
const mockedEnable2fa = enableTwoFactor as jest.Mock;
const mockedDisable2fa = disableTwoFactor as jest.Mock;
const mockedPasswordChange = requestAccountPasswordChange as jest.Mock;

/**
 * Flip a `SettingsSwitch` the way a member does, and only once it will listen.
 *
 * The switch itself is an accessory *inside* the row, so `fireEvent` finds the
 * handler by walking up to the `SettingsSwitch` element — and on the way it asks
 * the row's Pressability whether it would accept a touch at all. That answer
 * comes from `onStartShouldSetResponder`, which reads a config applied in a
 * passive effect: one flush later than the `disabled` prop the row has already
 * rendered. In that window the row looks enabled, `fireEvent` finds no handler
 * it is allowed to call, and returns silently — the tap is dropped with no
 * error, and the assertion that follows reports zero calls.
 *
 * So the wait is on the responder itself rather than on the rendered mirror of
 * it. Callers still assert `accessibilityState` separately: that is what the
 * member can see, and it is worth its own assertion.
 */
async function flipSwitch(row: ReactTestInstance, next: boolean) {
  await waitFor(() => expect(row.props.onStartShouldSetResponder()).toBe(true));
  fireEvent(row, "valueChange", next);
}

/** Press the confirm (second) button of the most recent `confirm()` alert. */
async function confirmLatestAlert(spy: jest.SpyInstance) {
  await waitFor(() => expect(spy).toHaveBeenCalled());
  const buttons = spy.mock.calls[spy.mock.calls.length - 1][2] as Array<{ onPress?: () => void }>;
  await act(async () => {
    await buttons[buttons.length - 1].onPress?.();
  });
}

describe("SecuritySettingsScreen", () => {
  let alertSpy: jest.SpyInstance;

  beforeEach(() => {
    jest.clearAllMocks();
    mockSecurityPreferences = {
      twoFactorEnabled: false,
      biometricUnlock: false,
      loginAlerts: true,
      requirePasswordForSensitiveChanges: true
    };
    alertSpy = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    mockedGetSecurity.mockResolvedValue({
      email: "roody@example.com",
      two_factor_enabled: false,
      active_sessions_count: 2
    });
  });

  afterEach(() => alertSpy.mockRestore());

  describe("password change email", () => {
    it("calls the authenticated account endpoint and reports accepted delivery honestly", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedPasswordChange.mockResolvedValue({ ok: true, message: "A password-change link will be sent." });

      const { findByTestId } = render(<SecuritySettingsScreen />);
      fireEvent.press(await findByTestId("security-change-password"));
      await confirmLatestAlert(alertSpy);

      await waitFor(() => expect(mockedPasswordChange).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Check your email", expect.stringContaining("password-change link")));
    });

    it("blocks a second request while the first request is in flight", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      let resolveRequest: (value: { ok: boolean }) => void = () => undefined;
      mockedPasswordChange.mockImplementation(() => new Promise((resolve) => { resolveRequest = resolve; }));

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const row = await findByTestId("security-change-password");
      fireEvent.press(row);
      await confirmLatestAlert(alertSpy);
      await waitFor(() => expect(mockedPasswordChange).toHaveBeenCalledTimes(1));
      expect(row.props.accessibilityState.disabled).toBe(true);

      fireEvent.press(row);
      expect(mockedPasswordChange).toHaveBeenCalledTimes(1);
      resolveRequest({ ok: true });
      await waitFor(() => expect(row.props.accessibilityState.disabled).toBeFalsy());
    });

    it("shows a retryable error and does not claim success when the request fails", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedPasswordChange.mockRejectedValue(new Error("Please wait a few minutes and try again."));

      const { findByTestId } = render(<SecuritySettingsScreen />);
      fireEvent.press(await findByTestId("security-change-password"));
      await confirmLatestAlert(alertSpy);

      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Couldn't send the link", "Please wait a few minutes and try again."));
      expect(alertSpy).not.toHaveBeenCalledWith("Check your email", expect.anything());
    });
  });

  describe("biometrics", () => {
    it("offers to enable Face ID when available and currently disabled", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedConfirmEnable.mockResolvedValue(true);

      const { findByTestId, getByText } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-biometric-toggle");
      expect(getByText("Unlock with Face ID")).toBeTruthy();
      expect(toggle.props.accessibilityState.checked).toBe(false);

      // The switch is deliberately disabled until the capability probe resolves
      // — tapping before we know whether a sensor exists would prompt for
      // enrolment we might not be able to complete. So wait for it, as a user
      // physically would.
      await waitFor(() => expect(toggle.props.accessibilityState.disabled).toBeFalsy());
      await flipSwitch(toggle, true);
      await waitFor(() => expect(mockedConfirmEnable).toHaveBeenCalledWith(5));
      // The preference is a mirror of the keychain — it must only be written
      // after the real enrolment succeeded.
      await waitFor(() => expect(mockSetGroup).toHaveBeenCalledWith({ biometricUnlock: true }));
    });

    it("reflects an already-enabled sensor", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(true);

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-biometric-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.checked).toBe(true));
    });

    it("confirms before disabling, then deletes the credential", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(true);
      mockedDisable.mockResolvedValue(undefined);

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-biometric-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.checked).toBe(true));

      alertSpy.mockClear();
      await flipSwitch(toggle, false);
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Turn off Face ID?", expect.any(String), expect.any(Array), expect.anything()));
      await confirmLatestAlert(alertSpy);
      await waitFor(() => expect(mockedDisable).toHaveBeenCalledTimes(1));
    });

    it("does not delete the credential when the confirmation is cancelled", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(true);

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-biometric-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.checked).toBe(true));

      alertSpy.mockClear();
      await flipSwitch(toggle, false);
      await waitFor(() => expect(alertSpy).toHaveBeenCalled());
      const buttons = alertSpy.mock.calls[0][2] as Array<{ onPress?: () => void }>;
      await buttons[0].onPress?.(); // cancel
      expect(mockedDisable).not.toHaveBeenCalled();
    });

    it("explains rather than toggling when there is no sensor", async () => {
      mockedCapability.mockResolvedValue({ available: false, kind: "none", reason: "no_hardware" });
      mockedIsEnabled.mockResolvedValue(false);

      const { queryByTestId, findByTestId, findByText } = render(<SecuritySettingsScreen />);
      await findByTestId("security-biometric-unavailable");
      await findByText(/doesn't have a biometric sensor/i);
      expect(queryByTestId("security-biometric-toggle")).toBeNull();
    });

    it("offers a route to device settings when the sensor exists but is unenrolled", async () => {
      mockedCapability.mockResolvedValue({ available: false, kind: "faceId", reason: "not_enrolled" });
      mockedIsEnabled.mockResolvedValue(false);

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const row = await findByTestId("security-biometric-unavailable");
      // A dead end here is the failure mode worth guarding: the row must be
      // actionable, not merely informative.
      expect(row.props.accessibilityRole).toBe("button");
    });
  });

  describe("two-factor", () => {
    it("mirrors the account state into the preference when they disagree", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedGetSecurity.mockResolvedValue({ email: "roody@example.com", two_factor_enabled: true });

      render(<SecuritySettingsScreen />);
      await waitFor(() => expect(mockSetGroup).toHaveBeenCalledWith({ twoFactorEnabled: true }));
    });

    it("enables without a confirmation prompt", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedEnable2fa.mockResolvedValue({ message: "Two-factor is on." });

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-two-factor-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.disabled).toBeFalsy());

      await flipSwitch(toggle, true);
      await waitFor(() => expect(mockedEnable2fa).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(mockSetGroup).toHaveBeenCalledWith({ twoFactorEnabled: true }));
    });

    it("requires confirmation before turning protection off", async () => {
      mockSecurityPreferences = { ...mockSecurityPreferences, twoFactorEnabled: true };
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedGetSecurity.mockResolvedValue({ email: "roody@example.com", two_factor_enabled: true });
      mockedDisable2fa.mockResolvedValue({ message: "Removed." });

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-two-factor-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.disabled).toBeFalsy());

      alertSpy.mockClear();
      await flipSwitch(toggle, false);
      await waitFor(() =>
        expect(alertSpy).toHaveBeenCalledWith(
          "Turn off two-factor authentication?",
          expect.any(String),
          expect.any(Array),
          expect.anything()
        )
      );
      expect(mockedDisable2fa).not.toHaveBeenCalled();
      await confirmLatestAlert(alertSpy);
      await waitFor(() => expect(mockedDisable2fa).toHaveBeenCalledTimes(1));
    });

    it("leaves the preference untouched when the account mutation fails", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedEnable2fa.mockRejectedValue(new Error("Server said no"));

      const { findByTestId } = render(<SecuritySettingsScreen />);
      const toggle = await findByTestId("security-two-factor-toggle");
      await waitFor(() => expect(toggle.props.accessibilityState.disabled).toBeFalsy());

      await flipSwitch(toggle, true);
      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Couldn't turn on two-factor", "Server said no"));
      // The whole point of writing the account first: a failed mutation must
      // never leave the switch claiming the account is protected.
      expect(mockSetGroup).not.toHaveBeenCalledWith({ twoFactorEnabled: true });
    });
  });

  describe("resilience", () => {
    it("still renders when the account security read fails", async () => {
      mockedCapability.mockResolvedValue({ available: true, kind: "faceId" });
      mockedIsEnabled.mockResolvedValue(false);
      mockedGetSecurity.mockRejectedValue(new Error("offline"));

      const { findByText } = render(<SecuritySettingsScreen />);
      await findByText(/last state we saw on this device/i);
    });
  });
});
