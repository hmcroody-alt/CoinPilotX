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

export type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  user: PulseUser | null;
  refresh_token?: string;
  refresh_token_expires_in?: number;
  access_token?: string;
  access_token_expires_in?: number;
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

export type ConfirmationStatusResponse = {
  ok: boolean;
  exists: boolean;
  email_verified: boolean;
  confirmed: boolean;
  message?: string;
};

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

export function resendEmailConfirmation(email: string) {
  return pulseApi<{ ok: boolean; message?: string }>("/api/mobile/auth/resend-confirmation", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}
