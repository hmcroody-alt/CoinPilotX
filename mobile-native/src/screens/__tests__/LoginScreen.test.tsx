import React from "react";
import { Alert } from "react-native";
import { fireEvent, render, waitFor, act } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

const mockNavigate = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: mockNavigate })
}));

const mockSetAuthState = jest.fn();
jest.mock("../../session/auth", () => ({
  signIn: jest.fn(),
  useAuth: () => ({ setAuthState: mockSetAuthState, authState: { status: "signedOut", user: null } })
}));

jest.mock("../../session/qaSimulatorAuth", () => ({
  isQaSimulatorAutoLoginEnabled: () => false,
  createQaSimulatorLocalSession: jest.fn(),
  tryHandleQaSimulatorAuthUrl: jest.fn()
}));

jest.mock("../../session/sessionStore", () => ({
  getCachedSessionUser: jest.fn().mockResolvedValue(null)
}));

jest.mock("expo-haptics", () => ({
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  impactAsync: jest.fn().mockResolvedValue(undefined),
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" },
  ImpactFeedbackStyle: { Light: "light" }
}));

jest.mock("../../session/biometricAuth", () => ({
  authenticateWithBiometrics: jest.fn(),
  confirmAndEnableBiometricLogin: jest.fn(),
  getBiometricCapability: jest.fn(),
  isBiometricEnabledForCurrentSession: jest.fn()
}));

jest.mock("../../session/rememberedAccounts", () => ({
  listRememberedAccounts: jest.fn().mockResolvedValue([])
}));

jest.mock("react-native/Libraries/Linking/Linking", () => ({
  default: {
    getInitialURL: jest.fn().mockResolvedValue(null),
    addEventListener: jest.fn(() => ({ remove: jest.fn() }))
  }
}));

import { signIn } from "../../session/auth";
import { getCachedSessionUser } from "../../session/sessionStore";
import { PulseApiError } from "../../api/pulseApi";
import {
  authenticateWithBiometrics,
  confirmAndEnableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../../session/biometricAuth";
import { LoginScreen } from "../LoginScreen";

const mockedSignIn = signIn as jest.Mock;
const mockedAuthenticateWithBiometrics = authenticateWithBiometrics as jest.Mock;
const mockedConfirmAndEnableBiometricLogin = confirmAndEnableBiometricLogin as jest.Mock;
const mockedGetBiometricCapability = getBiometricCapability as jest.Mock;
const mockedIsBiometricEnabledForCurrentSession = isBiometricEnabledForCurrentSession as jest.Mock;
const mockedGetCachedSessionUser = getCachedSessionUser as jest.Mock;

function setDefaultBiometricState() {
  mockedGetBiometricCapability.mockResolvedValue({ available: false, hasHardware: false, kind: "none", reason: "no_hardware" });
  mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(false);
  mockedGetCachedSessionUser.mockResolvedValue(null);
}

function openPulseGate(screen: ReturnType<typeof render>) {
  fireEvent.press(screen.getByTestId("pulse-gate-primary"));
}

describe("LoginScreen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    setDefaultBiometricState();
    jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
  });

  it("keeps the arrival screen minimal until the user opens the Pulse Gate", async () => {
    const screen = render(<LoginScreen />);
    expect(screen.getByTestId("pulse-gate-brand")).toBeTruthy();
    expect(screen.getByTestId("pulse-gate-message")).toBeTruthy();
    expect(screen.getByTestId("pulse-gate-primary")).toBeTruthy();
    expect(screen.queryByTestId("login-identifier")).toBeNull();
    expect(screen.queryByTestId("biometric-login-button")).toBeNull();

    openPulseGate(screen);
    await waitFor(() => expect(screen.getByTestId("login-identifier")).toBeTruthy());
    expect(screen.getByTestId("login-password")).toBeTruthy();
    expect(screen.getByTestId("login-submit")).toBeTruthy();
  });

  it("disables the submit button until both fields are filled", async () => {
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    await waitFor(() => expect(screen.getByTestId("login-identifier")).toBeTruthy());
    expect(screen.getByTestId("login-submit").props.accessibilityState?.disabled).toBe(true);
    fireEvent.changeText(screen.getByTestId("login-identifier"), "user@example.com");
    fireEvent.changeText(screen.getByTestId("login-password"), "password123");
    await waitFor(() => expect(screen.getByTestId("login-submit").props.accessibilityState?.disabled).toBe(false));
  });

  it("shows an error message on invalid credentials", async () => {
    mockedSignIn.mockResolvedValue({ status: "signedOut", user: null });
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId, findByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    fireEvent.changeText(getByTestId("login-identifier"), "user@example.com");
    fireEvent.changeText(getByTestId("login-password"), "wrongpass");
    fireEvent.press(getByTestId("login-submit"));
    const errorText = await findByTestId("login-form-error");
    expect(errorText.props.children).toMatch(/doesn't match our records/);
    expect(mockSetAuthState).not.toHaveBeenCalled();
  });

  it("maps a network-unreachable error to an offline message", async () => {
    mockedSignIn.mockRejectedValue(new PulseApiError("unreachable", 503, "request_unreachable"));
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId, findByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    fireEvent.changeText(getByTestId("login-identifier"), "user@example.com");
    fireEvent.changeText(getByTestId("login-password"), "password123");
    fireEvent.press(getByTestId("login-submit"));
    const errorText = await findByTestId("login-form-error");
    expect(errorText.props.children).toMatch(/could not be reached/);
  });

  it("signs the user in and updates auth state on success", async () => {
    mockedSignIn.mockResolvedValue({ status: "signedIn", user: { user_id: 5, username: "alex" } });
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    fireEvent.changeText(getByTestId("login-identifier"), "alex@example.com");
    fireEvent.changeText(getByTestId("login-password"), "password123");
    fireEvent.press(getByTestId("login-submit"));
    await waitFor(() =>
      expect(mockSetAuthState).toHaveBeenCalledWith({ status: "signedIn", user: { user_id: 5, username: "alex" } })
    );
  });

  it("prevents a duplicate submission while one is already in flight", async () => {
    let resolveSignIn: (value: unknown) => void = () => undefined;
    mockedSignIn.mockReturnValue(
      new Promise((resolve) => {
        resolveSignIn = resolve;
      })
    );
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    fireEvent.changeText(getByTestId("login-identifier"), "alex@example.com");
    fireEvent.changeText(getByTestId("login-password"), "password123");
    fireEvent.press(getByTestId("login-submit"));
    fireEvent.press(getByTestId("login-submit"));
    await act(async () => {
      resolveSignIn({ status: "signedIn", user: { user_id: 5 } });
    });
    expect(mockedSignIn).toHaveBeenCalledTimes(1);
  });

  it("shows the Face ID button when biometric login is available and enabled", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    const screen = render(<LoginScreen />);
    expect(screen.queryByTestId("biometric-login-button")).toBeNull();
    openPulseGate(screen);
    expect(await screen.findByTestId("biometric-login-button")).toBeTruthy();
  });

  it("hides the Face ID button when biometrics are unavailable", async () => {
    setDefaultBiometricState();
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId, queryByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    expect(queryByTestId("biometric-login-button")).toBeNull();
  });

  it("signs in via biometrics and updates auth state on success", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    mockedAuthenticateWithBiometrics.mockResolvedValue({
      outcome: "success",
      authState: { status: "signedIn", user: { user_id: 5 } }
    });
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { findByTestId } = screen;
    const biometricButton = await findByTestId("biometric-login-button");
    fireEvent.press(biometricButton);
    await waitFor(() => expect(mockSetAuthState).toHaveBeenCalledWith({ status: "signedIn", user: { user_id: 5 } }));
  });

  it("prompts for manual sign-in when biometric session validation fails", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    mockedAuthenticateWithBiometrics.mockResolvedValue({ outcome: "session_invalid" });
    const alertSpy = jest.spyOn(Alert, "alert");
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { findByTestId } = screen;
    const biometricButton = await findByTestId("biometric-login-button");
    fireEvent.press(biometricButton);
    await waitFor(() => expect(alertSpy).toHaveBeenCalledWith("Sign in required", expect.any(String)));
    expect(mockSetAuthState).not.toHaveBeenCalled();
  });

  it("automatically initiates Face ID once for a returning user with a cached account", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    mockedGetCachedSessionUser.mockResolvedValue({ user_id: 5, username: "alex", display_name: "Alex" });
    mockedAuthenticateWithBiometrics.mockResolvedValue({
      outcome: "success",
      authState: { status: "signedIn", user: { user_id: 5 } }
    });
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    await waitFor(() => expect(mockedAuthenticateWithBiometrics).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockSetAuthState).toHaveBeenCalledWith({ status: "signedIn", user: { user_id: 5 } }));
  });

  it("does not auto-initiate Face ID when there is no cached account", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    mockedGetCachedSessionUser.mockResolvedValue(null);
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { findByTestId } = screen;
    await findByTestId("biometric-login-button");
    // Give the auto-prompt effect's 350ms timer room to fire if it were going to.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 500));
    });
    expect(mockedAuthenticateWithBiometrics).not.toHaveBeenCalled();
  });

  it("does not re-prompt Face ID after the user cancels the automatic prompt", async () => {
    mockedGetBiometricCapability.mockResolvedValue({ available: true, hasHardware: true, kind: "faceId" });
    mockedIsBiometricEnabledForCurrentSession.mockResolvedValue(true);
    mockedGetCachedSessionUser.mockResolvedValue({ user_id: 5, username: "alex", display_name: "Alex" });
    mockedAuthenticateWithBiometrics.mockResolvedValue({ outcome: "cancelled" });
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    await waitFor(() => expect(mockedAuthenticateWithBiometrics).toHaveBeenCalledTimes(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 600));
    });
    expect(mockedAuthenticateWithBiometrics).toHaveBeenCalledTimes(1);
    expect(mockSetAuthState).not.toHaveBeenCalled();
  });

  it("navigates to the signup screen when create account is pressed", async () => {
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId } = screen;
    await waitFor(() => expect(getByTestId("create-account-button")).toBeTruthy());
    fireEvent.press(getByTestId("create-account-button"));
    expect(mockNavigate).toHaveBeenCalledWith("Signup");
  });

  it("navigates to account recovery when forgot password is pressed", async () => {
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId } = screen;
    await waitFor(() => expect(getByTestId("forgot-password-link")).toBeTruthy());
    fireEvent.press(getByTestId("forgot-password-link"));
    expect(mockNavigate).toHaveBeenCalledWith("AccountRecovery");
  });
});
