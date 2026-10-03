/**
 * What this app does with a provider assertion, and what it refuses to do.
 *
 * Three of these are security properties rather than behaviour, and they are
 * the reason this file goes through `signInWithProvider` rather than through a
 * screen:
 *
 *  - The nonce handed to Apple or Google must reach the server as the identical
 *    string. The server compares it against the nonce claim inside the signed
 *    token by exact equality, so any normalisation here breaks every federated
 *    sign-in at once -- and breaks it in a way no typechecker and no screen test
 *    would notice, because both strings would still be perfectly valid strings.
 *
 *  - `account_link_required` must stay a refusal. The server answers 409 when a
 *    verified provider email matches an existing account, because holding an
 *    address today does not prove you registered it. If this client ever
 *    "helpfully" admitted that case, a stranger who registered an account on
 *    someone else's address would be handed that person's provider identity.
 *    So the test asserts not just the outcome but that nothing was persisted.
 *
 *  - Age and terms must travel from what the member actually chose. The whole
 *    reason the server refuses to create an account from a provider token is
 *    that nobody has been asked yet; defaulting either to `true` at this call
 *    site would record a consent that was never given.
 *
 * Run: npx jest src/session/__tests__/federatedSignIn.test.ts
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

jest.mock("../../api/auth", () => ({
  acceptLegalDocuments: jest.fn(),
  federatedSignIn: jest.fn(),
  federatedSignup: jest.fn(),
  getSession: jest.fn(),
  login: jest.fn(),
  logout: jest.fn(),
  logoutAll: jest.fn(),
  signup: jest.fn()
}));

// A real class so the `instanceof PulseApiError` narrowing in the refusal
// readers resolves against the constructor these tests throw.
jest.mock("../../api/pulseApi", () => {
  class PulseApiError extends Error {
    status: number;
    code?: string;
    details?: Record<string, unknown>;
    constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
      super(message);
      this.name = "PulseApiError";
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }
  return { PulseApiError, recoverNativeSession: jest.fn() };
});

// The real cancellation class, for the same narrowing reason.
jest.mock("../../auth/providerSheets", () => {
  class ProviderSignInCancelled extends Error {
    constructor() {
      super("provider_sign_in_cancelled");
      this.name = "ProviderSignInCancelled";
    }
  }
  return {
    ProviderSignInCancelled,
    signInWithProviderSheet: jest.fn()
  };
});

const store = {
  cachedUser: null as unknown,
  written: null as unknown,
  remembered: null as unknown
};

jest.mock("../sessionStore", () => ({
  getSessionCookie: jest.fn(async () => null),
  getSessionEnvelope: jest.fn(async () => null),
  getCachedSessionUser: jest.fn(async () => store.cachedUser),
  setCachedSessionUser: jest.fn(async (user: unknown) => {
    store.cachedUser = user;
  }),
  setSessionEnvelope: jest.fn(async (envelope: unknown) => {
    store.written = envelope;
  }),
  clearNativeSessionCredentials: jest.fn(async () => undefined),
  clearActiveSessionKeepBiometric: jest.fn(async () => undefined),
  getBiometricUserId: jest.fn(async () => null),
  writeBiometricCredential: jest.fn(async () => undefined)
}));

jest.mock("../rememberedAccounts", () => ({
  rememberAccount: jest.fn(async (user: unknown) => {
    store.remembered = user;
  })
}));

const tierResets: number[] = [];
jest.mock("../../entitlements/useCanonicalTier", () => ({
  loadCanonicalTier: jest.fn(async () => undefined),
  resetCanonicalTier: jest.fn(() => {
    tierResets.push(1);
  })
}));

jest.mock("../qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: () => false
}));

import { completeFederatedSignup, signInWithProvider } from "../auth";
import { federatedSignIn, federatedSignup } from "../../api/auth";
import { PulseApiError } from "../../api/pulseApi";
import { ProviderSignInCancelled, signInWithProviderSheet } from "../../auth/providerSheets";

const sheetMock = signInWithProviderSheet as jest.Mock;
const federatedSignInMock = federatedSignIn as jest.Mock;
const federatedSignupMock = federatedSignup as jest.Mock;

const user = { user_id: 77, username: "marla" };
const NONCE = "a3f1c09e7b2d4a6188ff00d1e2c3b4a5";

function liveSession() {
  return { ok: true, authenticated: true, user, access_token: "at", refresh_token: "rt" };
}

function assertion(overrides: Record<string, unknown> = {}) {
  return { provider: "apple", idToken: "signed.jwt", nonce: NONCE, user: "", ...overrides };
}

beforeEach(() => {
  sheetMock.mockReset();
  federatedSignInMock.mockReset();
  federatedSignupMock.mockReset();
  store.cachedUser = null;
  store.written = null;
  store.remembered = null;
  tierResets.length = 0;
});

describe("the nonce that has to survive the round trip", () => {
  it("sends the provider's nonce to PulseSoc byte-for-byte", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("apple");

    // Not `toContain`, not a regex, not a length check: the server does an
    // exact string comparison and so does this.
    expect(federatedSignInMock.mock.calls[0][0].nonce).toBe(NONCE);
  });

  it("does not normalise a nonce that merely looks normalisable", async () => {
    // A mixed-case nonce is the case a well-meaning `.toLowerCase()` would
    // silently break, and it would break it for every member at once.
    const awkward = "AbC123-_xYz";
    sheetMock.mockResolvedValue(assertion({ nonce: awkward }));
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("google");

    expect(federatedSignInMock.mock.calls[0][0].nonce).toBe(awkward);
  });

  it("forwards the provider and token the sheet returned, not the one it was asked for", async () => {
    // The sheet is the authority on which provider answered.
    sheetMock.mockResolvedValue(assertion({ provider: "google", idToken: "google.jwt" }));
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("google");

    expect(federatedSignInMock.mock.calls[0][0]).toMatchObject({
      provider: "google",
      id_token: "google.jwt"
    });
  });
});

describe("Apple's one-time name payload", () => {
  it("sends it when the sheet disclosed one", async () => {
    const payload = JSON.stringify({ name: { firstName: "Ada", lastName: "Lovelace" } });
    sheetMock.mockResolvedValue(assertion({ user: payload }));
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("apple");

    expect(federatedSignInMock.mock.calls[0][0].user).toBe(payload);
  });

  it("omits the key entirely on a later sign-in rather than sending an empty name", async () => {
    sheetMock.mockResolvedValue(assertion({ user: "" }));
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("apple");

    // Sending `user: ""` invites a server that trusts it to blank a profile
    // name the member has since set. Apple never discloses the name twice, so
    // this request genuinely has nothing to say about it.
    expect(federatedSignInMock.mock.calls[0][0]).not.toHaveProperty("user");
  });
});

describe("backing out of the sheet", () => {
  it("is a cancellation, not an error and not a sign-out", async () => {
    sheetMock.mockRejectedValue(new ProviderSignInCancelled());

    const outcome = await signInWithProvider("apple");

    expect(outcome).toEqual({ kind: "cancelled" });
    expect(federatedSignInMock).not.toHaveBeenCalled();
  });

  it("leaves the current member's cached tier alone", async () => {
    sheetMock.mockRejectedValue(new ProviderSignInCancelled());

    await signInWithProvider("apple");

    // Resetting before the sheet opens would discard a signed-in member's tier
    // every time somebody opened a provider sheet and changed their mind,
    // leaving a paying member looking at an upsell they already answered.
    expect(tierResets).toHaveLength(0);
    expect(store.written).toBeNull();
    expect(store.cachedUser).toBeNull();
  });

  it("still reports a genuine sheet failure as an error", async () => {
    sheetMock.mockRejectedValue(new Error("provider_sign_in_failed:google"));

    await expect(signInWithProvider("google")).rejects.toThrow("provider_sign_in_failed:google");
  });
});

describe("an assertion that belongs to nobody yet", () => {
  const ticketDetails = {
    signup_ticket: "signed.signup.ticket",
    provider: "google",
    email: "marla@example.com",
    display_name: "Marla",
    expires_at: 1900000000,
    ttl_seconds: 900
  };

  it("pauses on the server's ticket instead of creating an account", async () => {
    sheetMock.mockResolvedValue(assertion({ provider: "google" }));
    federatedSignInMock.mockRejectedValue(
      new PulseApiError("One more step.", 403, "federated_signup_required", ticketDetails)
    );

    const outcome = await signInWithProvider("google");

    expect(outcome.kind).toBe("signupRequired");
    if (outcome.kind !== "signupRequired") throw new Error("unreachable");
    expect(outcome.ticket.signup_ticket).toBe("signed.signup.ticket");
    expect(outcome.ticket.provider).toBe("google");
    expect(outcome.ticket.email).toBe("marla@example.com");
    // Nothing is admitted: no envelope, no cached user, no remembered account.
    expect(store.written).toBeNull();
    expect(store.cachedUser).toBeNull();
    expect(store.remembered).toBeNull();
  });

  it("refuses a signup refusal carrying no ticket rather than showing a dead consent screen", async () => {
    sheetMock.mockResolvedValue(assertion({ provider: "google" }));
    federatedSignInMock.mockRejectedValue(
      new PulseApiError("One more step.", 403, "federated_signup_required", { provider: "google" })
    );

    // A consent screen with no ticket to spend is a dead end the member cannot
    // leave. Better the error they can retry from.
    await expect(signInWithProvider("google")).rejects.toThrow("One more step.");
  });

  it("refuses a ticket whose provider is not one this app knows", async () => {
    sheetMock.mockResolvedValue(assertion({ provider: "google" }));
    federatedSignInMock.mockRejectedValue(
      new PulseApiError("One more step.", 403, "federated_signup_required", {
        ...ticketDetails,
        provider: "facebook"
      })
    );

    await expect(signInWithProvider("google")).rejects.toThrow("One more step.");
  });
});

describe("a verified provider email that matches an existing account", () => {
  function linkRefusal() {
    return new PulseApiError(
      "A PulseSoc account already uses that email address.",
      409,
      "account_link_required",
      { provider: "apple" }
    );
  }

  it("is refused, because the email proves possession and not ownership", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockRejectedValue(linkRefusal());

    const outcome = await signInWithProvider("apple");

    expect(outcome.kind).toBe("linkRequired");
    if (outcome.kind !== "linkRequired") throw new Error("unreachable");
    expect(outcome.provider).toBe("apple");
    // The server's sentence tells the member what to do next; inventing one
    // here would drift from whatever the server actually enforces.
    expect(outcome.message).toContain("already uses that email address");
  });

  it("admits nothing at all on that path", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockRejectedValue(linkRefusal());

    await signInWithProvider("apple");

    // If this ever regressed, a stranger who registered an account on someone
    // else's address would be handed that person's provider identity.
    expect(store.written).toBeNull();
    expect(store.cachedUser).toBeNull();
    expect(store.remembered).toBeNull();
  });
});

describe("acceptance outstanding on a federated account", () => {
  it("is held at the acceptance step, as on the password path", async () => {
    const challenge = {
      documents: [{ document: "terms", version: "V2", title: "Terms", path: "/terms" }],
      ticket: "legal.ticket"
    };
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockRejectedValue(
      new PulseApiError("Review and accept.", 403, "legal_acceptance_required", {
        legal_acceptance: challenge
      })
    );

    const outcome = await signInWithProvider("apple");

    expect(outcome.kind).toBe("legalAcceptanceRequired");
    if (outcome.kind !== "legalAcceptanceRequired") throw new Error("unreachable");
    expect(outcome.state.phase).toBe("LEGAL_ACCEPTANCE_REQUIRED");
    expect(outcome.state.status).toBe("signedOut");
    expect(outcome.state.legalAcceptance).toEqual(challenge);
    expect(store.written).toBeNull();
  });
});

describe("a provider assertion the server recognises", () => {
  it("admits the member exactly the way the password path does", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockResolvedValue(liveSession());

    const outcome = await signInWithProvider("apple");

    expect(outcome.kind).toBe("signedIn");
    if (outcome.kind !== "signedIn") throw new Error("unreachable");
    expect(outcome.state.phase).toBe("AUTHENTICATED");
    expect(outcome.state.status).toBe("signedIn");
    // `toMatchObject` for the same reason as the two assertions below: the
    // state carries the normalized user, not the fixture.
    expect(outcome.state.user).toMatchObject(user);
    // All three are what make a session real on this device. A federated path
    // that skipped one would be a second, weaker definition of "signed in".
    // The envelope is asserted by its real shape -- the tokens and the account
    // they belong to -- because that is what a later request actually sends.
    expect(store.written).toMatchObject({
      userId: 77,
      accessToken: "at",
      refreshToken: "rt"
    });
    // `toMatchObject`, not `toEqual`: `normalizeSessionUser` fills in the
    // optional profile fields, so the stored user is a superset of this.
    expect(store.cachedUser).toMatchObject(user);
    expect(store.remembered).toMatchObject(user);
  });

  it("clears the previous account's cached tier before admitting this one", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockResolvedValue(liveSession());

    await signInWithProvider("apple");

    expect(tierResets).toHaveLength(1);
  });

  it("does not claim a session when the server returned no user", async () => {
    sheetMock.mockResolvedValue(assertion());
    federatedSignInMock.mockResolvedValue({ ok: true, authenticated: false, user: null });

    const outcome = await signInWithProvider("apple");

    expect(outcome.kind).toBe("signedIn");
    if (outcome.kind !== "signedIn") throw new Error("unreachable");
    expect(outcome.state.phase).toBe("UNAUTHENTICATED");
    expect(store.written).toBeNull();
  });
});

describe("finishing a federated signup", () => {
  it("passes the member's actual answers through, never a default", async () => {
    federatedSignupMock.mockResolvedValue(liveSession());

    await completeFederatedSignup({
      signup_ticket: "signed.signup.ticket",
      age_confirmed: false,
      terms_accepted: false,
      email_opt_in: false
    });

    // `false` is the value that catches a hardcoded `true`. The server refuses
    // to create the account from a provider token precisely because nobody had
    // been asked; answering on the member's behalf here would reintroduce the
    // invented consent that refusal exists to prevent.
    expect(federatedSignupMock.mock.calls[0][0]).toMatchObject({
      age_confirmed: false,
      terms_accepted: false,
      email_opt_in: false
    });
  });

  it("keeps marketing opt-in off unless it was chosen", async () => {
    federatedSignupMock.mockResolvedValue(liveSession());

    await completeFederatedSignup({
      signup_ticket: "t",
      age_confirmed: true,
      terms_accepted: true,
      email_opt_in: false
    });

    expect(federatedSignupMock.mock.calls[0][0].email_opt_in).toBe(false);
  });

  it("admits the new member through the one shared admission path", async () => {
    federatedSignupMock.mockResolvedValue(liveSession());

    const state = await completeFederatedSignup({
      signup_ticket: "t",
      age_confirmed: true,
      terms_accepted: true,
      email_opt_in: true
    });

    expect(state.phase).toBe("AUTHENTICATED");
    expect(store.written).toMatchObject({ userId: 77, accessToken: "at", refreshToken: "rt" });
    // `toMatchObject`, not `toEqual`: `normalizeSessionUser` fills in the
    // optional profile fields, so the stored user is a superset of this.
    expect(store.cachedUser).toMatchObject(user);
    expect(store.remembered).toMatchObject(user);
  });

  it("surfaces an expired or spent ticket as an error the screen reports", async () => {
    federatedSignupMock.mockRejectedValue(
      new PulseApiError("That sign-in has expired. Please try again.", 400, "invalid_provider_response")
    );

    await expect(
      completeFederatedSignup({
        signup_ticket: "stale",
        age_confirmed: true,
        terms_accepted: true,
        email_opt_in: false
      })
    ).rejects.toThrow("That sign-in has expired.");
  });
});
