/**
 * The client half of the consent gate: what this app does when the server says
 * an account's acceptance of the documents in force is not on file.
 *
 * The decision being pinned here is that the gate lives in `session/auth` and
 * not in LoginScreen. Three paths reach `signIn` -- the password form, Face ID,
 * and the post-confirmation finalize -- and a gate implemented at the call site
 * is a gate the next path forgets. The tests below go through `signIn` and
 * `restoreSession` rather than through a screen for that reason.
 *
 * The other decision is that `LEGAL_ACCEPTANCE_REQUIRED` resolves to
 * `status: "signedOut"`. That single mapping is what makes the application
 * structurally unreachable while a member is held at the step, instead of
 * merely unrouted, so it is asserted on every path rather than once.
 *
 * Run: npx jest src/session/__tests__/legalAcceptanceGate.test.ts
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

jest.mock("../../api/auth", () => ({
  acceptLegalDocuments: jest.fn(),
  getSession: jest.fn(),
  login: jest.fn(),
  logout: jest.fn(),
  logoutAll: jest.fn(),
  signup: jest.fn()
}));

// A real class from the mock so the `instanceof PulseApiError` narrowing in
// `legalAcceptanceRefusal` resolves against the constructor these tests throw.
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

const store = {
  cookie: null as string | null,
  envelope: null as { refreshToken?: string; userId?: number } | null,
  cachedUser: null as unknown,
  written: null as unknown
};

jest.mock("../sessionStore", () => ({
  getSessionCookie: jest.fn(async () => store.cookie),
  getSessionEnvelope: jest.fn(async () => store.envelope),
  getCachedSessionUser: jest.fn(async () => store.cachedUser),
  setCachedSessionUser: jest.fn(async (user: unknown) => {
    store.cachedUser = user;
  }),
  setSessionEnvelope: jest.fn(async (envelope: unknown) => {
    store.written = envelope;
  }),
  clearNativeSessionCredentials: jest.fn(async () => {
    store.cookie = null;
    store.envelope = null;
  })
}));

jest.mock("../qaTemporaryAccount", () => ({
  shouldRejectTemporaryQaUser: () => false
}));

import { completeLegalAcceptance, restoreSession, signIn } from "../auth";
import { acceptLegalDocuments, getSession, login } from "../../api/auth";
import { PulseApiError, recoverNativeSession } from "../../api/pulseApi";

const acceptMock = acceptLegalDocuments as jest.Mock;
const getSessionMock = getSession as jest.Mock;
const loginMock = login as jest.Mock;
const recoverMock = recoverNativeSession as jest.Mock;

const user = { user_id: 77, username: "marla" };

const documents = [
  { document: "terms", version: "PULSESOC_TERMS_2026_05", title: "Terms of Service", path: "/terms" },
  { document: "privacy", version: "PULSESOC_PRIVACY_2026_05", title: "Privacy Policy", path: "/privacy" }
];

const challenge = { documents, ticket: "signed.ticket", ttl_seconds: 900, accept_url: "https://pulsesoc.com/login" };

function refusal(details: Record<string, unknown> = { legal_acceptance: challenge }) {
  return new PulseApiError("Review and accept to continue.", 403, "legal_acceptance_required", details);
}

beforeEach(() => {
  acceptMock.mockReset();
  getSessionMock.mockReset();
  loginMock.mockReset();
  recoverMock.mockReset();
  store.cookie = null;
  store.envelope = null;
  store.cachedUser = null;
  store.written = null;
});

describe("signing in against an account with acceptance outstanding", () => {
  it("is held at the acceptance step rather than signed in or signed out", async () => {
    loginMock.mockRejectedValue(refusal());

    const state = await signIn("marla@example.com", "correct horse battery staple");

    expect(state.phase).toBe("LEGAL_ACCEPTANCE_REQUIRED");
    expect(state.legalAcceptance).toEqual(challenge);
    // Not SESSION_EXPIRED and not UNAUTHENTICATED: the password was right, and
    // sending the member back to retype it would ask them to answer a
    // credential question that was never the problem.
    expect(state.user).toBeNull();
  });

  it("is not signed in, which is what makes the app unreachable rather than merely unrouted", async () => {
    loginMock.mockRejectedValue(refusal());
    expect((await signIn("marla@example.com", "pw")).status).toBe("signedOut");
  });

  it("persists nothing -- no envelope, no cached user", async () => {
    loginMock.mockRejectedValue(refusal());
    await signIn("marla@example.com", "pw");
    expect(store.written).toBeNull();
    expect(store.cachedUser).toBeNull();
  });

  it("still surfaces every other rejection as an error the screen reports", async () => {
    loginMock.mockRejectedValue(new PulseApiError("Wrong password.", 401, "invalid_credentials"));
    await expect(signIn("marla@example.com", "wrong")).rejects.toThrow("Wrong password.");
  });

  it("refuses to present a step it cannot render", async () => {
    // The code without the documents is not actionable: there would be nothing
    // to read and no way to proceed. Better the server's words in an error than
    // an empty screen with no exit.
    loginMock.mockRejectedValue(refusal({ legal_acceptance: { documents: [], ticket: "t" } }));
    await expect(signIn("marla@example.com", "pw")).rejects.toThrow(PulseApiError);

    loginMock.mockRejectedValue(refusal({}));
    await expect(signIn("marla@example.com", "pw")).rejects.toThrow(PulseApiError);
  });

  it("does not treat an unticketed refusal as the acceptance step", async () => {
    // No ticket means no credential to answer with, so presenting the step
    // would strand the member on a button that can only ever fail.
    loginMock.mockRejectedValue(refusal({ legal_acceptance: { documents } }));
    await expect(signIn("marla@example.com", "pw")).rejects.toThrow(PulseApiError);
  });
});

describe("a session restored across a document revision", () => {
  it("is held at the acceptance step even though the token is still valid", async () => {
    // The server reports rather than revokes -- so this, not a 401, is how a
    // member who was already signed in when the Terms changed finds out.
    getSessionMock.mockResolvedValue({
      authenticated: true,
      user,
      legal_acceptance_required: true,
      legal_acceptance: { documents, accept_url: "https://pulsesoc.com/login" }
    });

    const state = await restoreSession();

    expect(state.phase).toBe("LEGAL_ACCEPTANCE_REQUIRED");
    expect(state.status).toBe("signedOut");
    expect(state.legalAcceptance?.documents).toEqual(documents);
    // No ticket on this path: the device already holds a bearer token, which is
    // what the acceptance call authenticates with.
    expect(state.legalAcceptance?.ticket).toBeUndefined();
    // Carried for display. `stateFor` gives a non-AUTHENTICATED phase no cache
    // scope, so nothing of this member's can be written while they are held.
    expect(state.user).toEqual(expect.objectContaining(user));
  });

  it("is held after a refresh too, not just on the first probe", async () => {
    getSessionMock
      .mockResolvedValueOnce({ authenticated: false, user: null })
      .mockResolvedValueOnce({ authenticated: true, user, legal_acceptance_required: true, legal_acceptance: { documents } });
    recoverMock.mockResolvedValue("refreshed");
    store.envelope = { refreshToken: "psr_live", userId: 77 };

    expect((await restoreSession()).phase).toBe("LEGAL_ACCEPTANCE_REQUIRED");
  });

  it("is admitted normally when nothing is outstanding", async () => {
    getSessionMock.mockResolvedValue({ authenticated: true, user, legal_acceptance_required: false });
    expect((await restoreSession()).phase).toBe("AUTHENTICATED");
  });

  it("is admitted when the flag arrives with no documents", async () => {
    // The flag alone cannot be acted on. Refusing on it would hold the member
    // on a screen with nothing to read and no way forward.
    getSessionMock.mockResolvedValue({ authenticated: true, user, legal_acceptance_required: true, legal_acceptance: { documents: [] } });
    expect((await restoreSession()).phase).toBe("AUTHENTICATED");
  });
});

describe("recording the acceptance", () => {
  it("admits the member and persists the session the gate withheld", async () => {
    acceptMock.mockResolvedValue({
      ok: true,
      authenticated: true,
      user,
      access_token: "mat_new",
      access_token_expires_in: 900,
      refresh_token: "psr_new",
      refresh_token_expires_in: 31_536_000
    });

    const state = await completeLegalAcceptance(challenge);

    expect(state.phase).toBe("AUTHENTICATED");
    expect(state.status).toBe("signedIn");
    expect(store.written).toEqual(expect.objectContaining({ userId: 77, refreshToken: "psr_new" }));
  });

  it("sends the ticket and nothing else", async () => {
    // Not the account id, not the document list, not the version, not a
    // timestamp. Any of those as an input would let a client record a consent
    // nobody gave, or record agreement to text the member never saw.
    acceptMock.mockResolvedValue({ ok: true, authenticated: true, user });
    await completeLegalAcceptance(challenge);
    expect(acceptMock).toHaveBeenCalledWith("signed.ticket");
  });

  it("asks with no ticket on the restored-session path, where the bearer answers", async () => {
    acceptMock.mockResolvedValue({ ok: true, authenticated: true, user });
    await completeLegalAcceptance({ documents });
    expect(acceptMock).toHaveBeenCalledWith(undefined);
  });

  it("propagates a refusal instead of reporting success", async () => {
    // Including the refusal worth distinguishing: the documents moved while the
    // member was reading, so the server rejects the stale ticket and describes
    // the new versions. Swallowing it here is how an unrecorded member walks
    // into the app.
    acceptMock.mockRejectedValue(refusal());
    await expect(completeLegalAcceptance(challenge)).rejects.toThrow(PulseApiError);
  });

  it("does not admit a response with no member in it", async () => {
    acceptMock.mockResolvedValue({ ok: true, authenticated: false, user: null });
    expect((await completeLegalAcceptance(challenge)).phase).toBe("UNAUTHENTICATED");
  });
});
