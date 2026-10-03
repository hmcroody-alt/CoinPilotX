/**
 * The native Apple and Google sign-in sheets, and whether this build has them.
 *
 * Deliberately thin: it opens the provider's own sheet, collects the signed
 * assertion, and returns it. It makes no decision about identity, because the
 * only place that can be decided is the server -- a client that chose which
 * account an assertion belonged to would be a client that could choose any.
 *
 * ## On the nonce, which is not what it looks like
 *
 * Both sheets below take a nonce and the server compares it against the nonce
 * claim inside the signed token. That is worth having -- it ties this token to
 * this attempt for a well-behaved client, and it catches a server or SDK
 * misconfiguration immediately -- but it is *not* replay protection, and it
 * must not be described as such anywhere:
 *
 *  - The nonce is chosen by the client, not minted by the server. An attacker
 *    holding a stolen token can read its nonce claim and present that same
 *    value, so the comparison succeeds.
 *  - `services/oidc_tokens.py:240` compares the nonce only when one is supplied
 *    (`if nonce:`), so a caller can omit it and skip the check outright.
 *
 * The web flow does not have this weakness: there the nonce is server-minted,
 * stored in `oauth_login_state`, and consumed once, which is a genuine
 * single-use guarantee. The native equivalent cannot be built the same way for
 * Google, because `@react-native-google-signin/google-signin`'s `ConfigureParams`
 * has no nonce field at all (checked in the installed v16 types) -- the SDK
 * gives no way to put a server value inside the token.
 *
 * So what actually stands between a leaked native token and a session is: TLS,
 * the audience binding (the token must name *this* app's client id), and the
 * token's own expiry. That is the standard posture for native OIDC and it is
 * the posture here -- stated plainly so nobody later reads the nonce argument
 * and concludes replay is already handled. Making it single-use requires the
 * server to remember the token it has already honoured; that is tracked as
 * follow-up work, not quietly assumed.
 */

import * as AppleAuthentication from "expo-apple-authentication";
import Constants from "expo-constants";
import { Platform } from "react-native";

export type FederatedProvider = "apple" | "google";

export type ProviderAssertion = {
  provider: FederatedProvider;
  idToken: string;
  /** The exact string handed to the provider. Sent to PulseSoc unchanged. */
  nonce: string;
  /**
   * Apple's first-authorisation name payload, JSON-encoded, or "".
   *
   * Apple discloses the member's name once -- in the sheet, on the very first
   * authorisation -- and never again, so unlike the email it cannot be
   * recovered from a later token. The server uses it for the profile name only
   * and never for anything that decides identity or access.
   */
  user: string;
};

/** Raised when the member backs out of the sheet. Not an error to report. */
export class ProviderSignInCancelled extends Error {
  constructor() {
    super("provider_sign_in_cancelled");
    this.name = "ProviderSignInCancelled";
  }
}

/** Raised when the sheet itself failed -- no assertion, and not a cancellation. */
export class ProviderSignInFailed extends Error {
  readonly provider: FederatedProvider;

  constructor(provider: FederatedProvider, cause?: unknown) {
    super(`provider_sign_in_failed:${provider}`);
    this.name = "ProviderSignInFailed";
    this.provider = provider;
    if (cause !== undefined) this.cause = cause;
  }
}

/**
 * A URL-safe random string, used as the nonce.
 *
 * `expo-crypto` is not a dependency of this app, so this uses the global
 * `crypto.getRandomValues` that React Native 0.81 provides. Falling back to
 * `Math.random` would be worse than useless here: it would look like a nonce.
 */
export function createNonce(byteLength = 32): string {
  const bytes = new Uint8Array(byteLength);
  const source = (globalThis as { crypto?: Crypto }).crypto;
  if (!source?.getRandomValues) {
    throw new Error("no_secure_random_source");
  }
  source.getRandomValues(bytes);
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

/**
 * The Google iOS OAuth client id, or "" when this build has none.
 *
 * Read from the resolved Expo config rather than `process.env` because
 * `EXPO_PUBLIC_*` reads are inlined at build time by Babel and are therefore
 * dead in a Release build unless the reference is static -- `app.config.js`
 * resolves it once and publishes it through `extra`, which survives.
 */
export function googleIosClientId(): string {
  const extra = Constants.expoConfig?.extra as { googleIosClientId?: string } | undefined;
  return String(extra?.googleIosClientId || "");
}

/**
 * Whether the Google button should be rendered at all.
 *
 * Gated on the same value the build is gated on, so the button cannot appear in
 * a binary whose native Google module was never configured -- a button that
 * opens nothing is worse than no button.
 *
 * Gated on iOS as well, because the only audience this build configures is an
 * *iOS* OAuth client. Android's sheet needs its own client id and a
 * `webClientId` to mint a token the server would accept; handing it the iOS one
 * yields a token whose `aud` the server rejects, which is a button that opens a
 * sheet and then fails. False here until that client exists.
 */
export function googleSignInAvailable(): boolean {
  return Platform.OS === "ios" && googleIosClientId().length > 0;
}

/** Whether this device can offer Sign in with Apple. False on simulators pre-iOS 13. */
export async function appleSignInAvailable(): Promise<boolean> {
  if (Platform.OS !== "ios") return false;
  try {
    return await AppleAuthentication.isAvailableAsync();
  } catch {
    return false;
  }
}

function isAppleCancellation(error: unknown): boolean {
  const code = (error as { code?: string } | null)?.code;
  return code === "ERR_REQUEST_CANCELED" || code === "ERR_CANCELED";
}

/**
 * Open Apple's sheet and return the signed assertion.
 *
 * The full-name scope is requested alongside email because Apple will only ever
 * offer it once; not asking on the first authorisation means never being able
 * to ask again.
 */
export async function signInWithAppleSheet(): Promise<ProviderAssertion> {
  const nonce = createNonce();
  let credential: AppleAuthentication.AppleAuthenticationCredential;
  try {
    credential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL
      ],
      nonce
    });
  } catch (error) {
    if (isAppleCancellation(error)) throw new ProviderSignInCancelled();
    throw new ProviderSignInFailed("apple", error);
  }

  if (!credential.identityToken) throw new ProviderSignInFailed("apple");

  // Shaped the way the web callback already accepts Apple's name payload, so
  // one server-side parser serves both surfaces.
  const name = credential.fullName;
  const user =
    name?.givenName || name?.familyName
      ? JSON.stringify({
          name: { firstName: name?.givenName || "", lastName: name?.familyName || "" }
        })
      : "";

  return { provider: "apple", idToken: credential.identityToken, nonce, user };
}

/**
 * Open Google's sheet and return the signed assertion.
 *
 * The module is loaded lazily because it is only present in a build whose
 * config plugin was applied, and that plugin is only applied when an iOS client
 * id is configured (`app.config.js`). A static import would make the whole
 * screen fail to load in a build without it.
 *
 * Lazy here means `require` inside the function, the form
 * `src/calls/callSessionStore.ts:605` already uses for Agora -- not
 * `await import`. Both are lazy under Metro, but Metro is not the only thing
 * that loads this file: under Jest an `await import` stays a real ESM dynamic
 * import and throws `A dynamic import callback was invoked without
 * --experimental-vm-modules`, which this function would then report as a failed
 * Google sign-in. That turns the entire provider path into something that
 * cannot be tested, and an untestable auth path is how the cancellation bug
 * above survives.
 *
 * Scopes are left at the SDK default of email + profile. Asking for more would
 * mean a consent screen listing permissions PulseSoc does not use.
 *
 * ## Cancellation arrives two different ways
 *
 * In v16 backing out of the sheet is a *resolved* value -- `signIn()` returns
 * `{ type: "cancelled", data: null }` (`lib/typescript/src/types.d.ts`,
 * `CancelledResponse`) rather than rejecting. Checking only for a thrown
 * `statusCodes.SIGN_IN_CANCELLED` would therefore read a cancel as a success
 * carrying no token, and show a member who deliberately tapped Cancel an error
 * telling them sign-in failed. Both paths are handled: the returned discriminant
 * is the one v16 actually uses, and the thrown code is kept because the native
 * layer still raises it for interrupted attempts.
 */
export async function signInWithGoogleSheet(): Promise<ProviderAssertion> {
  const clientId = googleIosClientId();
  if (!clientId) throw new ProviderSignInFailed("google");

  const nonce = createNonce();
  try {
    const { GoogleSignin, statusCodes } = require("@react-native-google-signin/google-signin") as typeof import("@react-native-google-signin/google-signin");
    GoogleSignin.configure({ iosClientId: clientId });
    try {
      const response = await GoogleSignin.signIn();
      if (response.type === "cancelled") throw new ProviderSignInCancelled();
      // `User.idToken` is `string | null`: a success with no JWT is not a
      // cancellation, it is a sheet that gave us nothing to send.
      const idToken = response.data.idToken;
      if (!idToken) throw new ProviderSignInFailed("google");
      return { provider: "google", idToken, nonce, user: "" };
    } catch (error) {
      const code = (error as { code?: string } | null)?.code;
      if (code === statusCodes.SIGN_IN_CANCELLED) throw new ProviderSignInCancelled();
      throw error;
    }
  } catch (error) {
    if (error instanceof ProviderSignInCancelled) throw error;
    if (error instanceof ProviderSignInFailed) throw error;
    throw new ProviderSignInFailed("google", error);
  }
}

export async function signInWithProviderSheet(
  provider: FederatedProvider
): Promise<ProviderAssertion> {
  return provider === "apple" ? signInWithAppleSheet() : signInWithGoogleSheet();
}
