jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

// Keep the session layer isolated from real network + storage. We only care
// about the branching logic in registerAccount / finalizeConfirmedSignup.
jest.mock("../../api/auth", () => ({
  signup: jest.fn(),
  login: jest.fn()
}));

const persistedUsers: unknown[] = [];
jest.mock("../sessionStore", () => ({
  setSessionEnvelope: jest.fn(async () => undefined),
  setCachedSessionUser: jest.fn(async (user: unknown) => {
    persistedUsers.push(user);
  }),
  getSessionEnvelope: jest.fn(async () => null),
  getBiometricSession: jest.fn(async () => null),
  getBiometricUserId: jest.fn(async () => null)
}));

const rememberedAccounts: unknown[] = [];
jest.mock("../rememberedAccounts", () => ({
  rememberAccount: jest.fn(async (user: unknown) => {
    rememberedAccounts.push(user);
  })
}));

jest.mock("../qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: () => false
}));

import { registerAccount, finalizeConfirmedSignup } from "../auth";
import { login, signup } from "../../api/auth";

const signupMock = signup as jest.Mock;
const loginMock = login as jest.Mock;

const basePayload = {
  full_name: "Ada Lovelace",
  username: "ada_l",
  email: "ada@example.com",
  password: "engine-1843!",
  age_confirmed: true,
  email_opt_in: false
};

beforeEach(() => {
  signupMock.mockReset();
  loginMock.mockReset();
  persistedUsers.length = 0;
  rememberedAccounts.length = 0;
});

describe("registerAccount", () => {
  it("returns confirmEmail on the common email path without pretending the user is signed in", async () => {
    signupMock.mockResolvedValue({
      ok: true,
      authenticated: false,
      user: null,
      requires_email_confirmation: true,
      email: "ada@example.com"
    });

    const outcome = await registerAccount(basePayload);

    expect(outcome.kind).toBe("confirmEmail");
    if (outcome.kind === "confirmEmail") {
      expect(outcome.email).toBe("ada@example.com");
      expect(outcome.deliveryFailed).toBe(false);
    }
    // No session must be persisted before confirmation.
    expect(persistedUsers).toHaveLength(0);
  });

  it("surfaces email delivery failure so the UI can promote resend / change-email", async () => {
    signupMock.mockResolvedValue({
      ok: true,
      authenticated: false,
      user: null,
      requires_email_confirmation: true,
      email_delivery_failed: true,
      email: "ada@example.com"
    });

    const outcome = await registerAccount(basePayload);
    expect(outcome).toEqual({ kind: "confirmEmail", email: "ada@example.com", deliveryFailed: true, message: undefined });
  });

  it("falls back to the submitted email when the server omits it", async () => {
    signupMock.mockResolvedValue({ ok: true, authenticated: false, user: null, requires_email_confirmation: true });
    const outcome = await registerAccount(basePayload);
    if (outcome.kind === "confirmEmail") expect(outcome.email).toBe(basePayload.email);
  });

  it("persists a real session on the authenticated (phone-only) path", async () => {
    const user = { user_id: 42, username: "ada_l" };
    signupMock.mockResolvedValue({ ok: true, authenticated: true, user, refresh_token: "r", refresh_token_expires_in: 100 });

    const outcome = await registerAccount(basePayload);

    expect(outcome.kind).toBe("signedIn");
    if (outcome.kind === "signedIn") expect(outcome.state.user).toEqual(user);
    expect(persistedUsers).toContainEqual(user);
    expect(rememberedAccounts).toContainEqual(user);
  });

  it("passes the caller's consent flags through verbatim (no hardcoding)", async () => {
    signupMock.mockResolvedValue({ ok: true, authenticated: false, user: null });
    await registerAccount({ ...basePayload, email_opt_in: true });
    expect(signupMock).toHaveBeenCalledWith(expect.objectContaining({ email_opt_in: true, age_confirmed: true }));
  });

  it("propagates backend errors (duplicate handle, etc.) to the caller", async () => {
    signupMock.mockRejectedValue(new Error("That handle is already taken."));
    await expect(registerAccount(basePayload)).rejects.toThrow("already taken");
  });
});

describe("finalizeConfirmedSignup", () => {
  it("reuses the production login path to exchange confirmed credentials for a session", async () => {
    const user = { user_id: 7, username: "ada_l" };
    loginMock.mockResolvedValue({ ok: true, authenticated: true, user });

    const state = await finalizeConfirmedSignup("ada@example.com", "engine-1843!");

    expect(loginMock).toHaveBeenCalledWith("ada@example.com", "engine-1843!");
    expect(state.status).toBe("signedIn");
    expect(state.user).toEqual(user);
  });

  it("stays signed out if login is not yet accepted (link not tapped)", async () => {
    loginMock.mockResolvedValue({ ok: true, authenticated: false, user: null });
    const state = await finalizeConfirmedSignup("ada@example.com", "engine-1843!");
    expect(state.status).toBe("signedOut");
  });
});
