/**
 * What these tests are for.
 *
 * `providerSheets` is the one place where a value leaves PulseSoc, goes into a
 * provider's signed token, and has to come back out matching. The server
 * compares the nonce it is sent against the nonce claim inside the token by
 * exact string equality, so a transformation anywhere on this path -- hashing
 * it, re-generating it, trimming it -- turns *every* federated sign-in into a
 * verification failure, on a path no typechecker can see. That invariant is
 * pinned first and most explicitly below.
 *
 * The rest pin the two ways a member can decline. Google's v16 `signIn()`
 * reports a cancel as a resolved `{ type: "cancelled" }` rather than a thrown
 * error, so the natural-looking implementation reads backing out of the sheet
 * as a success carrying no token and tells the member sign-in failed. Apple
 * does throw. Both are asserted, because the difference is invisible in the
 * call site and a future refactor that unified them would reintroduce the bug.
 */

import { Platform } from "react-native";

const mockAppleSignInAsync = jest.fn();
const mockAppleIsAvailableAsync = jest.fn();
const mockGoogleSignIn = jest.fn();
const mockGoogleConfigure = jest.fn();

jest.mock("expo-apple-authentication", () => ({
  signInAsync: (...args: unknown[]) => mockAppleSignInAsync(...args),
  isAvailableAsync: (...args: unknown[]) => mockAppleIsAvailableAsync(...args),
  AppleAuthenticationScope: { FULL_NAME: 1, EMAIL: 0 }
}));

jest.mock("@react-native-google-signin/google-signin", () => ({
  GoogleSignin: {
    configure: (...args: unknown[]) => mockGoogleConfigure(...args),
    signIn: (...args: unknown[]) => mockGoogleSignIn(...args)
  },
  statusCodes: { SIGN_IN_CANCELLED: "SIGN_IN_CANCELLED" }
}));

let mockExtra: Record<string, unknown> = {};
jest.mock("expo-constants", () => ({
  __esModule: true,
  default: {
    get expoConfig() {
      return { extra: mockExtra };
    }
  }
}));

import {
  createNonce,
  googleIosClientId,
  googleSignInAvailable,
  appleSignInAvailable,
  signInWithAppleSheet,
  signInWithGoogleSheet,
  signInWithProviderSheet,
  ProviderSignInCancelled,
  ProviderSignInFailed
} from "../providerSheets";

const GOOGLE_CLIENT_ID = "1234567890-abcdef.apps.googleusercontent.com";

beforeEach(() => {
  jest.clearAllMocks();
  mockExtra = { googleIosClientId: GOOGLE_CLIENT_ID };
  Platform.OS = "ios";
});

describe("the nonce that crosses the provider boundary", () => {
  it("hands the provider the exact string it reports to PulseSoc — Apple", async () => {
    mockAppleSignInAsync.mockResolvedValue({ identityToken: "apple-jwt", fullName: null });

    const assertion = await signInWithAppleSheet();

    // The server does `token.nonce == request.nonce`. If these two ever differ
    // the sign-in cannot succeed, so this is the whole contract in one line.
    const handedToApple = mockAppleSignInAsync.mock.calls[0][0].nonce;
    expect(assertion.nonce).toBe(handedToApple);
    expect(typeof handedToApple).toBe("string");
    expect(handedToApple.length).toBeGreaterThan(0);
  });

  it("reports a nonce for Google even though the SDK gives nowhere to put it", async () => {
    mockGoogleSignIn.mockResolvedValue({ type: "success", data: { idToken: "google-jwt" } });

    const assertion = await signInWithGoogleSheet();

    // v16's `ConfigureParams` has no nonce field, so unlike Apple there is no
    // call argument to compare against -- the nonce reaches the server but
    // never reaches the token. Asserted so that nobody reads the Apple test
    // above and assumes both providers bind the nonce into the assertion.
    expect(assertion.nonce).toEqual(expect.any(String));
    expect(assertion.nonce.length).toBeGreaterThan(0);
    expect(mockGoogleConfigure).toHaveBeenCalledWith({ iosClientId: GOOGLE_CLIENT_ID });
    expect(JSON.stringify(mockGoogleConfigure.mock.calls[0][0])).not.toContain(assertion.nonce);
  });

  it("never repeats a nonce", () => {
    const seen = new Set(Array.from({ length: 200 }, () => createNonce()));
    expect(seen.size).toBe(200);
  });

  it("refuses to produce a nonce rather than fall back to a guessable one", () => {
    const realCrypto = (globalThis as { crypto?: Crypto }).crypto;
    // A `Math.random` fallback would be worse than this throw: it would look
    // exactly like a nonce and be predictable.
    Object.defineProperty(globalThis, "crypto", { value: undefined, configurable: true });
    try {
      expect(() => createNonce()).toThrow("no_secure_random_source");
    } finally {
      Object.defineProperty(globalThis, "crypto", { value: realCrypto, configurable: true });
    }
  });
});

describe("declining the sheet", () => {
  it("reads Google's resolved cancellation as a cancellation, not a failure", async () => {
    // v16 returns this instead of throwing. Treating it as a success with a
    // missing token shows an error to a member who chose to back out.
    mockGoogleSignIn.mockResolvedValue({ type: "cancelled", data: null });

    await expect(signInWithGoogleSheet()).rejects.toBeInstanceOf(ProviderSignInCancelled);
  });

  it("still honours a thrown cancellation code from the native layer", async () => {
    mockGoogleSignIn.mockRejectedValue(Object.assign(new Error("x"), { code: "SIGN_IN_CANCELLED" }));

    await expect(signInWithGoogleSheet()).rejects.toBeInstanceOf(ProviderSignInCancelled);
  });

  it("distinguishes a success with no token from a cancellation", async () => {
    // `User.idToken` is `string | null`. Nothing to send is a failure; the
    // member did not decline, so offering them "you cancelled" would be a lie.
    mockGoogleSignIn.mockResolvedValue({ type: "success", data: { idToken: null } });

    const error = await signInWithGoogleSheet().catch((e) => e);
    expect(error).toBeInstanceOf(ProviderSignInFailed);
    expect(error).not.toBeInstanceOf(ProviderSignInCancelled);
  });

  it("maps both of Apple's cancellation codes", async () => {
    for (const code of ["ERR_REQUEST_CANCELED", "ERR_CANCELED"]) {
      mockAppleSignInAsync.mockRejectedValue(Object.assign(new Error("x"), { code }));
      await expect(signInWithAppleSheet()).rejects.toBeInstanceOf(ProviderSignInCancelled);
    }
  });

  it("reports a genuine Apple sheet error as a failure carrying the cause", async () => {
    const cause = Object.assign(new Error("boom"), { code: "ERR_SOMETHING_ELSE" });
    mockAppleSignInAsync.mockRejectedValue(cause);

    const error = await signInWithAppleSheet().catch((e) => e);
    expect(error).toBeInstanceOf(ProviderSignInFailed);
    expect(error).not.toBeInstanceOf(ProviderSignInCancelled);
    expect(error.provider).toBe("apple");
    expect(error.cause).toBe(cause);
  });

  it("treats an Apple response with no identity token as a failure", async () => {
    mockAppleSignInAsync.mockResolvedValue({ identityToken: null, fullName: null });

    await expect(signInWithAppleSheet()).rejects.toBeInstanceOf(ProviderSignInFailed);
  });
});

describe("Apple's one-time name disclosure", () => {
  it("shapes the first authorisation's name the way the server already parses", async () => {
    mockAppleSignInAsync.mockResolvedValue({
      identityToken: "apple-jwt",
      fullName: { givenName: "Ada", familyName: "Lovelace" }
    });

    const assertion = await signInWithAppleSheet();

    expect(JSON.parse(assertion.user)).toEqual({
      name: { firstName: "Ada", lastName: "Lovelace" }
    });
  });

  it("sends an empty payload on every later sign-in rather than inventing a name", async () => {
    // Apple discloses the name once and never again. An empty string here must
    // not be allowed to overwrite a profile name the member has since set.
    mockAppleSignInAsync.mockResolvedValue({ identityToken: "apple-jwt", fullName: null });
    expect((await signInWithAppleSheet()).user).toBe("");

    mockAppleSignInAsync.mockResolvedValue({
      identityToken: "apple-jwt",
      fullName: { givenName: null, familyName: null }
    });
    expect((await signInWithAppleSheet()).user).toBe("");
  });

  it("keeps a partial name rather than discarding the only disclosure", async () => {
    mockAppleSignInAsync.mockResolvedValue({
      identityToken: "apple-jwt",
      fullName: { givenName: "Prince", familyName: null }
    });

    expect(JSON.parse((await signInWithAppleSheet()).user)).toEqual({
      name: { firstName: "Prince", lastName: "" }
    });
  });

  it("requests the name scope, because Apple will only ever offer it once", async () => {
    mockAppleSignInAsync.mockResolvedValue({ identityToken: "apple-jwt", fullName: null });

    await signInWithAppleSheet();

    expect(mockAppleSignInAsync.mock.calls[0][0].requestedScopes).toEqual([1, 0]);
  });
});

describe("whether a button may be shown at all", () => {
  it("hides Google when this build configured no client id", () => {
    mockExtra = {};
    expect(googleIosClientId()).toBe("");
    expect(googleSignInAvailable()).toBe(false);
  });

  it("hides Google off iOS, where the configured audience would be the wrong one", () => {
    Platform.OS = "android";
    expect(googleSignInAvailable()).toBe(false);
  });

  it("shows Google only when the id the build was gated on is present", () => {
    expect(googleSignInAvailable()).toBe(true);
  });

  it("opens nothing when asked to sign in with an unconfigured provider", async () => {
    mockExtra = {};

    await expect(signInWithGoogleSheet()).rejects.toBeInstanceOf(ProviderSignInFailed);
    expect(mockGoogleConfigure).not.toHaveBeenCalled();
    expect(mockGoogleSignIn).not.toHaveBeenCalled();
  });

  it("reports Apple unavailable off iOS without consulting the native module", async () => {
    Platform.OS = "android";
    expect(await appleSignInAvailable()).toBe(false);
    expect(mockAppleIsAvailableAsync).not.toHaveBeenCalled();
  });

  it("treats an availability check that throws as unavailable", async () => {
    mockAppleIsAvailableAsync.mockRejectedValue(new Error("no such module"));
    expect(await appleSignInAvailable()).toBe(false);
  });

  it("reports Apple available when the device says so", async () => {
    mockAppleIsAvailableAsync.mockResolvedValue(true);
    expect(await appleSignInAvailable()).toBe(true);
  });
});

describe("provider routing", () => {
  it("sends each provider to its own sheet and labels the assertion accordingly", async () => {
    mockAppleSignInAsync.mockResolvedValue({ identityToken: "apple-jwt", fullName: null });
    mockGoogleSignIn.mockResolvedValue({ type: "success", data: { idToken: "google-jwt" } });

    const apple = await signInWithProviderSheet("apple");
    expect(apple.provider).toBe("apple");
    expect(apple.idToken).toBe("apple-jwt");
    expect(mockGoogleSignIn).not.toHaveBeenCalled();

    const google = await signInWithProviderSheet("google");
    expect(google.provider).toBe("google");
    expect(google.idToken).toBe("google-jwt");
    // Google cannot disclose a name payload; only Apple's sheet does.
    expect(google.user).toBe("");
  });
});
