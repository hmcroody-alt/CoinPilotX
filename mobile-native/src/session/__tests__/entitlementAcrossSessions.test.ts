/**
 * What happens to the shared entitlement answer as members come and go.
 *
 * The cache is module-level and process-wide, so it outlives every screen and
 * every account. Two obligations follow, and they pull in opposite directions:
 *
 *   drop it, or one member sees another's tier;
 *   re-read it, or the member who just arrived sees nobody's.
 *
 * `resetCanonicalTier` does the first. Until this suite existed nothing did the
 * second on the sign-in path: the reset published "we can't confirm your
 * membership" and left it there, so a premium member's first seconds after
 * signing in were spent looking at a product that could not confirm they had
 * paid for it — until some surface happened to mount and ask on its own.
 *
 * The ORDER is the part worth pinning. A reset after a load discards the load,
 * which is the same bug with the lines swapped and is invisible in any test
 * that only checks that both functions were called.
 *
 * Sign-out is the mirror image and is asserted as a REFUSAL: it must reset and
 * must NOT re-read. There is no session left to ask with, so the request is a
 * guaranteed failure, and a failed entitlement read renders as "we can't
 * confirm your membership" — an outage message shown to somebody who simply
 * logged out.
 */
jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

jest.mock("../../api/auth", () => ({
  login: jest.fn(),
  signup: jest.fn(),
  logout: jest.fn(async () => undefined),
  logoutAll: jest.fn(async () => undefined),
  getSession: jest.fn(async () => null)
}));

jest.mock("../sessionStore", () => ({
  setSessionEnvelope: jest.fn(async () => undefined),
  setCachedSessionUser: jest.fn(async () => undefined),
  getCachedSessionUser: jest.fn(async () => null),
  getSessionEnvelope: jest.fn(async () => null),
  getSessionCookie: jest.fn(async () => null),
  getBiometricUserId: jest.fn(async () => null),
  clearNativeSessionCredentials: jest.fn(async () => undefined),
  clearActiveSessionKeepBiometric: jest.fn(async () => undefined),
  writeBiometricCredential: jest.fn(async () => undefined)
}));

jest.mock("../../api/push", () => ({ unregisterPushDevice: jest.fn(async () => undefined) }));
jest.mock("../../calls/callKitBridge", () => ({ revokeVoipPushRegistration: jest.fn(async () => undefined) }));
jest.mock("../../core/messageNotificationReconciliation", () => ({
  cancelMessageReconciliation: jest.fn()
}));
jest.mock("../../media/mediaCache", () => ({ setMediaCacheScope: jest.fn() }));
jest.mock("../../core/mutations/outbox", () => ({ setOutboxScope: jest.fn() }));
jest.mock("../../media/mediaSessionCleanup", () => ({ clearUserScopedMediaState: jest.fn(async () => undefined) }));

jest.mock("../rememberedAccounts", () => ({
  rememberAccount: jest.fn(async () => undefined),
  forgetAccount: jest.fn(async () => undefined)
}));

jest.mock("../qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: () => false
}));

/**
 * One shared ledger, so the assertions can be about ORDER and not merely about
 * attendance. Two separate `jest.fn()`s can each say they were called and
 * cannot say which came first, which is exactly the distinction that matters
 * here.
 */
const calls: string[] = [];
jest.mock("../../entitlements/useCanonicalTier", () => ({
  resetCanonicalTier: jest.fn(() => {
    calls.push("reset");
  }),
  loadCanonicalTier: jest.fn(() => {
    calls.push("load");
    return Promise.resolve({ state: "resolved", effectiveTier: "PREMIUM" });
  })
}));

import { createAccount, signIn, signOut } from "../auth";
import { login, signup } from "../../api/auth";
import { loadCanonicalTier } from "../../entitlements/useCanonicalTier";

const loginMock = login as jest.Mock;
const signupMock = signup as jest.Mock;
const loadMock = loadCanonicalTier as jest.Mock;

/**
 * A complete envelope, not a minimal one.
 *
 * `persistSessionEnvelope` returns early without a `refresh_token`, so a
 * session fixture missing one never reaches storage — and a test asserting
 * "the entitlement read happens after the session is persisted" would then be
 * comparing against a persist that silently never occurred.
 */
const SESSION = {
  ok: true,
  authenticated: true,
  user: { id: 42, user_id: 42, username: "ada_l", display_name: "Ada" },
  access_token: "tok_live",
  access_token_expires_in: 3600,
  refresh_token: "rt_live",
  refresh_token_expires_in: 2592000
};

beforeEach(() => {
  calls.length = 0;
  loginMock.mockReset();
  signupMock.mockReset();
  // Re-established rather than merely cleared: one test below swaps this
  // implementation out to record ordering against the persist call, and
  // `mockClear` does not put an implementation back. Without this line that
  // test silently disarms every test declared after it.
  loadMock.mockReset();
  loadMock.mockImplementation(() => {
    calls.push("load");
    return Promise.resolve({ state: "resolved", effectiveTier: "PREMIUM" });
  });
  const { setSessionEnvelope } = require("../sessionStore");
  (setSessionEnvelope as jest.Mock).mockReset();
  (setSessionEnvelope as jest.Mock).mockImplementation(async () => undefined);
});

describe("signing in", () => {
  it("drops the previous member's answer and then asks for this one's", async () => {
    loginMock.mockResolvedValue(SESSION);
    await signIn("ada", "engine-1843!");
    expect(calls).toEqual(["reset", "load"]);
  });

  it("asks only after the session is persisted, so the request is authenticated", async () => {
    // An entitlement read issued before the token is stored is answered for
    // nobody, comes back FREE or 401, and publishes that to every gate in the
    // app. Ordering it against the persist call is the only way to see this:
    // the read itself succeeds either way.
    const { setSessionEnvelope } = require("../sessionStore");
    const order: string[] = [];
    (setSessionEnvelope as jest.Mock).mockImplementation(async () => {
      order.push("persist");
    });
    loadMock.mockImplementation(() => {
      order.push("load");
      return Promise.resolve({});
    });
    loginMock.mockResolvedValue(SESSION);

    await signIn("ada", "engine-1843!");

    expect(order.indexOf("persist")).toBeGreaterThan(-1);
    expect(order.indexOf("load")).toBeGreaterThan(order.indexOf("persist"));
  });

  it("does not ask when the sign-in did not produce a member", async () => {
    // A rejected login still resets — the previous member's tier must go
    // regardless — but there is nothing to ask on behalf of, so asking would
    // turn a wrong password into an entitlement error.
    loginMock.mockResolvedValue({ ok: false, authenticated: false, user: null });
    await signIn("ada", "wrong");
    expect(calls).toEqual(["reset"]);
  });
});

describe("creating an account", () => {
  it("asks for the new member's entitlement rather than assuming Free", async () => {
    // A new account may already hold a signup trial grant. Assuming otherwise
    // shows a brand-new member the upsell for something they were just given.
    signupMock.mockResolvedValue(SESSION);
    await createAccount({
      full_name: "Ada Lovelace",
      username: "ada_l",
      email: "ada@example.com",
      password: "engine-1843!"
    });
    expect(calls).toEqual(["reset", "load"]);
  });
});

describe("signing out", () => {
  it("drops the answer and deliberately does not ask for another", async () => {
    await signOut();
    expect(calls).toEqual(["reset"]);
    expect(loadMock).not.toHaveBeenCalled();
  });
});
