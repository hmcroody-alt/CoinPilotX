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
import { activateLocale } from "../../i18n/engine";

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

/**
 * The copy these tests assert on is real catalog text, and catalogs load
 * lazily. `I18nProvider` awaits that load in the app; a bare `render()` does
 * not, so without this the first frame resolves every key through
 * `humanizeKey` and the assertions compare against "Identifier Mismatch"
 * rather than the sentence the user actually sees.
 */
beforeAll(async () => {
  await activateLocale("en");
});

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

  /**
   * The backend rejects a sign-in five distinguishable ways. The screen used to
   * answer four of them with "that email/username or password doesn't match our
   * records", which is not a softer phrasing of the truth -- it is a different
   * claim, and for the security-gate states it is a false one.
   *
   * The concrete cost: three failed attempts inside five minutes trip the
   * velocity gate, after which a *correct* password is answered 403
   * `login_challenge_required`. Told the password was wrong, the natural move is
   * to retype the same correct password, which cannot succeed and which drives
   * the shared per-IP counter toward its own limit.
   */
  async function submitAndReadError(error: PulseApiError) {
    mockedSignIn.mockRejectedValue(error);
    const screen = render(<LoginScreen />);
    openPulseGate(screen);
    const { getByTestId, findByTestId } = screen;
    await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
    fireEvent.changeText(getByTestId("login-identifier"), "user@example.com");
    fireEvent.changeText(getByTestId("login-password"), "password123");
    fireEvent.press(getByTestId("login-submit"));
    const errorText = await findByTestId("login-form-error");
    return String(errorText.props.children);
  }

  it("names the security challenge instead of blaming the password", async () => {
    const shown = await submitAndReadError(
      new PulseApiError("Complete the security challenge to continue.", 403, "login_challenge_required")
    );
    expect(shown).toMatch(/confirm the challenge/i);
    expect(shown).not.toMatch(/match our records/i);
  });

  it("tells the user to confirm their email when that is what is missing", async () => {
    const shown = await submitAndReadError(
      new PulseApiError("Please confirm your email before logging in.", 403, "email_not_confirmed")
    );
    expect(shown).toMatch(/confirm your email/i);
    expect(shown).not.toMatch(/match our records/i);
  });

  it("reports a restricted account as restricted", async () => {
    const shown = await submitAndReadError(
      new PulseApiError("This account is not active.", 403, "account_restricted")
    );
    expect(shown).toMatch(/can't sign in right now/i);
    expect(shown).not.toMatch(/match our records/i);
  });

  it("reads the rate-limit state from the code, not only from the 429", async () => {
    // `login_rate_limited` is issued at 429 today. Sorting on status alone would
    // still work now and quietly mis-sort it the day the gate picks 403.
    //
    // Asserted against the catalog sentence, not the server's. Both contain
    // "too many", so a looser matcher passes even when the code lookup is gone
    // and the English message is being echoed through the unknown-code path --
    // which is the exact regression this test exists to catch. "Wait a moment"
    // appears only in the localized string.
    const shown = await submitAndReadError(
      new PulseApiError("Too many failed login attempts.", 403, "login_rate_limited")
    );
    expect(shown).toBe("Too many attempts. Please wait a moment and try again.");
  });

  it("shows the server's own words for a rejection code it does not know", async () => {
    // A state added to the backend after this build shipped. Substituting a
    // guess here is exactly what hid the challenge; the server's sentence is
    // incomplete only in language, not in truth.
    const shown = await submitAndReadError(
      new PulseApiError("Your account needs manual review before sign-in.", 403, "pending_manual_review")
    );
    expect(shown).toBe("Your account needs manual review before sign-in.");
  });

  it("still says credentials for a bare 401, which the server left ambiguous on purpose", async () => {
    // Unknown account and wrong password answer identically so the endpoint
    // cannot enumerate accounts. The screen must not invent the distinction.
    const shown = await submitAndReadError(new PulseApiError("Email or password is incorrect.", 401));
    expect(shown).toMatch(/doesn't match our records/i);
  });

  it("adds no backend hint on a production build", async () => {
    // The hint below is for QA builds only; jest resolves the default base URL,
    // which classifies as production. An ordinary user must never see it.
    const shown = await submitAndReadError(new PulseApiError("Email or password is incorrect.", 401));
    expect(shown).not.toMatch(/separate accounts/i);
  });

  it("reads the credential refusal from `invalid_credentials`, not the server's sentence", async () => {
    // The named form of the bare 401 above. Pinned to the catalog sentence
    // rather than the server's: both would satisfy a /incorrect|match/ matcher,
    // so a loose one still passes when the code lookup is gone and the English
    // message is being echoed through the unknown-code branch.
    const shown = await submitAndReadError(
      new PulseApiError("Email or password is incorrect.", 401, "invalid_credentials")
    );
    expect(shown).toBe("That email/username or password doesn't match our records.");
  });

  it("says the same thing whether or not the server named the credential refusal", async () => {
    // A server predating `invalid_credentials` sends a bare 401 for the very
    // same state. Two spellings of one refusal must not read as two outcomes.
    const named = await submitAndReadError(
      new PulseApiError("Email or password is incorrect.", 401, "invalid_credentials")
    );
    const bare = await submitAndReadError(new PulseApiError("Email or password is incorrect.", 401));
    expect(named).toBe(bare);
  });

  it("keeps `invalid_credentials` silent about whether the account exists", async () => {
    // The code is deliberately one value for unknown-identifier and for
    // wrong-password. If the screen ever grows separate copy for those, it has
    // undone the server's anti-enumeration policy from the client side.
    const shown = await submitAndReadError(
      new PulseApiError("Email or password is incorrect.", 401, "invalid_credentials")
    );
    expect(shown).not.toMatch(/no account|not found|unknown account|doesn't exist|not registered/i);
  });

  it("renders every known rejection as its own copy, never as the generic fallback", async () => {
    // Mutual exclusion, asserted as a set rather than one case at a time. The
    // regression this guards is one state collapsing onto another's sentence or
    // onto "Unable to sign in.", which no single-case assertion above can catch.
    //
    // Literals are pinned rather than imported from the catalog on purpose:
    // sharing the constant would let a rename move the UI and the expectation
    // together, keeping the suite green while the screen regressed.
    const cases: Array<[string, number, string]> = [
      ["invalid_credentials", 401, "Email or password is incorrect."],
      ["login_challenge_required", 403, "Complete the security challenge to continue."],
      ["login_rate_limited", 429, "Too many failed login attempts."],
      ["email_not_confirmed", 403, "Please confirm your email before logging in."],
      ["account_restricted", 403, "This account is not active."]
    ];
    const shown: string[] = [];
    for (const [code, status, message] of cases) {
      shown.push(await submitAndReadError(new PulseApiError(message, status, code)));
    }

    expect(shown).toEqual([
      "That email/username or password doesn't match our records.",
      "For your security, confirm the challenge to continue signing in.",
      "Too many attempts. Please wait a moment and try again.",
      "Confirm your email address before signing in.",
      "This account can't sign in right now. Contact support@pulsesoc.com."
    ]);
    expect(new Set(shown).size).toBe(shown.length);
    expect(shown).not.toContain("Unable to sign in.");
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
