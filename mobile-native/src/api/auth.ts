import { pulseApi } from "./pulseApi";

export type PulseUser = {
  user_id: number;
  username?: string;
  display_name?: string;
  full_name?: string;
  email?: string;
  avatar_url?: string;
  premium_status?: string;
  account_status?: string;
};

/**
 * One outstanding document, exactly as the server describes it. Every field is
 * the server's answer: `version` is what this account would be recorded as
 * accepting, and `path` is where the canonical text lives. The app does not
 * carry a copy of either — `src/screens/settings/legalContent.ts` is a bundled
 * snapshot dated months behind the documents in force, and accepting version X
 * while reading version X-1 is the defect a version ledger exists to prevent.
 */
export type LegalAcceptanceDocument = {
  document: string;
  version: string;
  title: string;
  path: string;
};

/**
 * What the server says is outstanding, and the only credential that answers it.
 *
 * `ticket` is present on the login-refused path and absent on a restored
 * session: a refused login holds no session, so the server signs the account id
 * and the shown versions into a short-lived ticket; a restored session already
 * carries a bearer token and needs no second credential. Both are server-issued
 * — there is no shape of this object the client can author to admit itself.
 */
export type LegalAcceptanceChallenge = {
  documents: LegalAcceptanceDocument[];
  accept_url?: string;
  ticket?: string;
  ttl_seconds?: number;
  expires_at?: number;
};

export type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  user: PulseUser | null;
  refresh_token?: string;
  refresh_token_expires_in?: number;
  access_token?: string;
  access_token_expires_in?: number;
  /**
   * Reported on `/session` and `/refresh` so a session restored across a
   * document revision finds out. True with a live `user` is not a
   * contradiction: the server deliberately informs rather than revoking, so
   * builds that predate the acceptance screen keep working.
   */
  legal_acceptance_required?: boolean;
  legal_acceptance?: LegalAcceptanceChallenge;
};

/**
 * Register can resolve two ways, both `ok: true`:
 *  - `authenticated: true` with a session (phone-only accounts), or
 *  - `authenticated: false, requires_email_confirmation: true` — the common
 *    email path, where the backend has emailed a confirmation *link* (there is
 *    no in-app numeric code) and the account cannot sign in until confirmed.
 * `email_delivery_failed` signals the account exists but the email bounced, so
 * the client should surface resend / change-email affordances prominently.
 */
export type RegisterResponse = SessionResponse & {
  requires_email_confirmation?: boolean;
  email_delivery_failed?: boolean;
  message?: string;
  email?: string;
};

/**
 * `confirmed` is the whole contract. The endpoint used to return `exists` and
 * `email_verified` as well, and both were removed server-side: `exists` told any
 * unauthenticated caller whether an account lived at a given address, which is an
 * enumeration oracle, and `email_verified` restated `confirmed` in a second
 * field. Neither was ever read anywhere in this app — they were declared
 * *required* and consumed nowhere, so the type asserted a leak that no code
 * needed.
 */
export type ConfirmationStatusResponse = {
  ok: boolean;
  confirmed: boolean;
  message?: string;
};

/** The two credential providers the server will verify an assertion from. */
export type FederatedProvider = "apple" | "google";

/**
 * What the server answers when a verified assertion belongs to nobody yet.
 *
 * It is a refusal, not a session: `federated_signup_required` arrives as a 403
 * carrying a short-lived signed ticket, because neither Apple nor Google can
 * answer the age question or agree to the Terms, and an account created before
 * anybody was asked would record a consent that was never given.
 */
export type FederatedSignupTicket = {
  signup_ticket: string;
  provider: FederatedProvider;
  email?: string;
  display_name?: string;
  expires_at?: number;
  ttl_seconds?: number;
};

/**
 * Exchange a provider assertion for a PulseSoc session.
 *
 * `nonce` must be the *same string* that was handed to the provider's sheet.
 * The server compares it against the nonce claim inside the signed token by
 * exact equality, so hashing or re-generating it on either side turns every
 * sign-in into a verification failure.
 */
export function federatedSignIn(payload: {
  provider: FederatedProvider;
  id_token: string;
  nonce: string;
  /** Apple's first-authorisation name payload. Absent on every later sign-in. */
  user?: string;
  preferred_language?: string;
}) {
  return pulseApi<SessionResponse>("/api/mobile/auth/federated", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

/**
 * Which federated providers this server can actually honour from a phone.
 *
 * Asked because the device cannot answer it. `AppleAuthentication.
 * isAvailableAsync()` reports whether *this iPhone* can present the Apple
 * sheet -- true on every one since iOS 13 -- and says nothing about whether
 * the server holds the Apple credentials needed to verify what comes back.
 * Only the server knows that, and only the server knows it *now*: this screen
 * shipped inside a binary, while the configuration it depends on can be set or
 * revoked long afterwards without an App Store release.
 *
 * Resolves to the providers the server will honour. The caller treats a
 * failure as "offer nothing", so a server that cannot answer costs the
 * provider buttons and leaves email/password untouched.
 */
export function getFederatedProviders() {
  return pulseApi<{ ok?: boolean; available?: string[] }>("/api/mobile/auth/providers");
}

/**
 * Finish a federated signup with the answers only the member can give.
 *
 * The ticket carries the verified identity; age and agreement travel in this
 * request and nowhere else, for the reason above.
 */
export function federatedSignup(payload: {
  signup_ticket: string;
  age_confirmed: boolean;
  terms_accepted: boolean;
  email_opt_in?: boolean;
  country?: string;
  preferred_language?: string;
}) {
  return pulseApi<SessionResponse>("/api/mobile/auth/federated/signup", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function getSession() {
  return pulseApi<SessionResponse>("/api/mobile/auth/session");
}

export function login(identifier: string, password: string) {
  return pulseApi<SessionResponse>("/api/mobile/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier, email: identifier, password })
  });
}

export function signup(payload: {
  full_name: string;
  username: string;
  email: string;
  password: string;
  age_confirmed: boolean;
  email_opt_in: boolean;
}) {
  // Consent flags are passed through verbatim from the user's explicit choices —
  // never hardcoded — so marketing opt-in stays off unless the user checks it.
  return pulseApi<RegisterResponse>("/api/mobile/auth/register", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

/**
 * Poll whether the pending account's email has been confirmed via the link.
 * Used by the verification step to advance the flow the moment the user taps
 * the link in their inbox (on this device or any other).
 */
export function getEmailConfirmationStatus(email: string) {
  return pulseApi<ConfirmationStatusResponse>("/api/mobile/auth/confirmation-status", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

/**
 * Correct a mistyped email on an unconfirmed account and re-send the link.
 * Requires the account password (held in-memory during the active flow only).
 */
export function changeUnverifiedEmail(payload: { old_email: string; new_email: string; password: string }) {
  return pulseApi<{ ok: boolean; message?: string; email?: string }>("/api/mobile/auth/change-confirmation-email", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function logout() {
  return pulseApi<{ ok: boolean }>("/api/mobile/auth/logout", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function logoutAll() {
  return pulseApi<{ ok: boolean; revoked_count?: number }>("/api/mobile/auth/logout-all", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function requestPasswordRecovery(email: string) {
  return pulseApi<{ ok: boolean; message?: string }>("/api/mobile/auth/recover", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

/**
 * Record this account's acceptance of every document the server says is
 * outstanding.
 *
 * The body carries the ticket and nothing else. No account id, no document
 * list, no version, no timestamp: the server reads the account from the ticket
 * signature or from the bearer token, and reads the versions from its own
 * config. A client that could name any of those could record a consent nobody
 * gave, or record agreement to text the member never saw.
 *
 * Safe to retry. The ledger is unique on (account, document, version), so a
 * double tap or a replay after a lost response is the same single row.
 */
export function acceptLegalDocuments(ticket?: string) {
  return pulseApi<SessionResponse>("/api/mobile/auth/legal-acceptance", {
    method: "POST",
    body: JSON.stringify(ticket ? { ticket } : {})
  });
}

export function resendEmailConfirmation(email: string) {
  return pulseApi<{ ok: boolean; message?: string }>("/api/mobile/auth/resend-confirmation", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}
