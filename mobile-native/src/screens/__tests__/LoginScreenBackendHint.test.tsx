/**
 * A QA build must say which backend it is talking to when a sign-in is refused.
 *
 * This is its own file because it is the only one that needs `api/config`
 * mocked, and that mock is file-scoped. Everywhere else jest resolves the
 * default base URL, which classifies as `production` -- so the sibling suite
 * proves the hint stays hidden there, and this one proves it appears here.
 *
 * The failure being prevented is not hypothetical. Staging and production are
 * separate Postgres instances, so an account that exists on one has no row on
 * the other, and a correct password is answered "Email or password is
 * incorrect." The phone gives no indication which environment it was built
 * against -- same name, same icon, same screens -- so the message is true about
 * this database and reads as a claim about the account. Hours go into retyping
 * a password that was never wrong.
 *
 * What is deliberately NOT done: saying whether the account exists. The server
 * answers unknown-account and wrong-password identically so the endpoint cannot
 * be used to enumerate accounts, and the hint must not leak by inference. It
 * describes the build -- which is fixed at compile time and identical whoever
 * types into it -- and never the account.
 */
import React from "react";
import { Alert } from "react-native";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ navigate: jest.fn() })
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
  getBiometricCapability: jest.fn().mockResolvedValue({ available: false, hasHardware: false, kind: "none", reason: "no_hardware" }),
  isBiometricEnabledForCurrentSession: jest.fn().mockResolvedValue(false)
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

/**
 * Spread the real module rather than replace it. `api/config` exports a dozen
 * values that LoginScreen's own imports pull in transitively; a hand-written
 * stub would satisfy the two this test cares about and silently blank the rest.
 */
jest.mock("../../api/config", () => ({
  ...jest.requireActual("../../api/config"),
  PULSE_ENVIRONMENT: "staging",
  PULSE_ENVIRONMENT_IDENTITY: {
    appEnvironment: "STAGING",
    backendHost: "pulsesoc-staging-backend.up.railway.app",
    declaredEnvironment: "STAGING"
  }
}));

import { signIn } from "../../session/auth";
import { PulseApiError } from "../../api/pulseApi";
import { LoginScreen } from "../LoginScreen";
import { activateLocale } from "../../i18n/engine";

const mockedSignIn = signIn as jest.Mock;

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
});

async function submitAndReadError(error: PulseApiError) {
  mockedSignIn.mockRejectedValue(error);
  const screen = render(<LoginScreen />);
  fireEvent.press(screen.getByTestId("pulse-gate-primary"));
  const { getByTestId, findByTestId } = screen;
  await waitFor(() => expect(getByTestId("login-identifier")).toBeTruthy());
  fireEvent.changeText(getByTestId("login-identifier"), "user@example.com");
  fireEvent.changeText(getByTestId("login-password"), "password123");
  fireEvent.press(getByTestId("login-submit"));
  return String((await findByTestId("login-form-error")).props.children);
}

describe("sign-in failure on a non-production build", () => {
  it("names the environment and host it was actually talking to", async () => {
    const shown = await submitAndReadError(new PulseApiError("Email or password is incorrect.", 401));
    expect(shown).toMatch(/doesn't match our records/i);
    expect(shown).toContain("STAGING");
    expect(shown).toContain("pulsesoc-staging-backend.up.railway.app");
    expect(shown).toMatch(/separate accounts/i);
  });

  it("says nothing about whether the account exists", async () => {
    // Both halves of the ambiguity the server preserves must stay unspoken.
    const shown = await submitAndReadError(new PulseApiError("Email or password is incorrect.", 401));
    expect(shown).not.toMatch(/no account|not found|unknown account|doesn't exist|not registered/i);
  });

  it("does not append the hint to states that already explain themselves", async () => {
    // A challenge, an unconfirmed email or a restriction all prove the account
    // is on this backend. Naming the environment there is noise, and worse,
    // implies the environment is the problem when it is not.
    for (const [code, message] of [
      ["login_challenge_required", "Complete the security challenge to continue."],
      ["email_not_confirmed", "Please confirm your email before logging in."],
      ["account_restricted", "This account is not active."]
    ] as Array<[string, string]>) {
      const shown = await submitAndReadError(new PulseApiError(message, 403, code));
      expect(shown).not.toContain("STAGING");
      expect(shown).not.toMatch(/separate accounts/i);
    }
  });

  it("does not append the hint when the network was the problem", async () => {
    const shown = await submitAndReadError(new PulseApiError("unreachable", 503, "request_unreachable"));
    expect(shown).toMatch(/could not be reached/i);
    expect(shown).not.toContain("STAGING");
  });
});
