import React from "react";
import { Alert } from "react-native";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: jest.fn() })
}));

const mockSetAuthState = jest.fn();
jest.mock("../../session/auth", () => ({
  signOut: jest.fn(),
  signOutEverywhere: jest.fn(),
  useAuth: () => ({ setAuthState: mockSetAuthState, authState: { status: "signedIn", user: { user_id: 5 } } })
}));

jest.mock("../../api/push", () => ({ registerPushDevice: jest.fn() }));
jest.mock("../../api/support", () => ({ openSupportWebFallback: jest.fn() }));

jest.mock("../../session/biometricAuth", () => ({
  getBiometricCapability: jest.fn(),
  isBiometricEnabledForCurrentSession: jest.fn(),
  confirmAndEnableBiometricLogin: jest.fn(),
  disableBiometricLogin: jest.fn()
}));

import {
  getBiometricCapability,
  isBiometricEnabledForCurrentSession,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin
} from "../../session/biometricAuth";
import { SettingsScreen } from "../SettingsScreen";

const mockedGetBiometricCapability = getBiometricCapability as jest.Mock;
const mockedIsEnabled = isBiometricEnabledForCurrentSession as jest.Mock;
const mockedConfirmEnable = confirmAndEnableBiometricLogin as jest.Mock;
const mockedDisable = disableBiometricLogin as jest.Mock;

describe("SettingsScreen biometric toggle", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
  });

  it("offers to enable Face ID when available and currently disabled", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, kind: "faceId" });
    mockedIsEnabled.mockResolvedValue(false);
    mockedConfirmEnable.mockResolvedValue(true);
    const { findByTestId, getByText } = render(<SettingsScreen />);
    const toggle = await findByTestId("settings-biometric-toggle");
    expect(getByText("Enable Face ID")).toBeTruthy();
    fireEvent.press(toggle);
    await waitFor(() => expect(mockedConfirmEnable).toHaveBeenCalledWith(5));
  });

  it("shows the Turn off control when Face ID is already enabled", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, kind: "faceId" });
    mockedIsEnabled.mockResolvedValue(true);
    const { findByTestId } = render(<SettingsScreen />);
    const toggle = await findByTestId("settings-biometric-toggle");
    expect(toggle.props.accessibilityState.checked).toBe(true);
  });

  it("confirms before disabling and deletes the credential on confirm", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, kind: "faceId" });
    mockedIsEnabled.mockResolvedValue(true);
    mockedDisable.mockResolvedValue(undefined);
    const alertSpy = jest.spyOn(Alert, "alert");
    const { findByTestId } = render(<SettingsScreen />);
    const toggle = await findByTestId("settings-biometric-toggle");
    fireEvent.press(toggle);
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Turn off Face ID?", expect.any(String), expect.any(Array)));
    // Invoke the destructive "Turn off" button handler from the alert.
    const buttons = alertSpy.mock.calls[0][2] as Array<{ text?: string; onPress?: () => void }>;
    const turnOff = buttons.find((b) => b.text === "Turn off");
    await turnOff?.onPress?.();
    expect(mockedDisable).toHaveBeenCalledTimes(1);
  });

  it("does not render the toggle when biometrics are unavailable", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: false, kind: "none", reason: "no_hardware" });
    mockedIsEnabled.mockResolvedValue(false);
    const { queryByTestId, findByText } = render(<SettingsScreen />);
    await findByText("This device does not support Face ID or Touch ID.");
    expect(queryByTestId("settings-biometric-toggle")).toBeNull();
  });
});
