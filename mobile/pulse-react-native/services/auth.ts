import { pulseApi } from "./apiClient";

export type PulseUser = {
  user_id: number;
  username?: string;
  display_name?: string;
  full_name?: string;
  email?: string;
  avatar_url?: string;
  avatar_thumbnail_url?: string;
  premium_status?: string;
  account_status?: string;
};

export type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  user: PulseUser | null;
  requires_email_confirmation?: boolean;
  message?: string;
  refresh_token?: string;
};

export function getMobileSession() {
  return pulseApi<SessionResponse>("/api/mobile/auth/session");
}

export function refreshMobileSession() {
  return pulseApi<SessionResponse>("/api/mobile/auth/refresh", { method: "POST" });
}

export function loginWithPassword(identifier: string, password: string) {
  return pulseApi<SessionResponse>("/api/mobile/auth/login", {
    method: "POST",
    body: JSON.stringify({ identifier, email: identifier, password })
  });
}

export function createAccount(payload: {
  full_name: string;
  username: string;
  email: string;
  password: string;
}) {
  return pulseApi<SessionResponse>("/api/mobile/auth/register", {
    method: "POST",
    body: JSON.stringify({
      ...payload,
      age_confirmed: true,
      email_opt_in: true
    })
  });
}

export function requestPasswordRecovery(email: string) {
  return pulseApi<{ ok: boolean; message: string }>("/api/mobile/auth/recover", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function resendEmailConfirmation(email: string) {
  return pulseApi<{ ok: boolean; message: string }>("/api/mobile/auth/resend-confirmation", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function checkEmailConfirmationStatus(email: string) {
  return pulseApi<{ ok: boolean; exists: boolean; confirmed: boolean; email_verified: boolean; message: string }>("/api/mobile/auth/confirmation-status", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function confirmEmailToken(token: string) {
  return pulseApi<{ ok: boolean; confirmed: boolean; message: string }>("/api/mobile/auth/confirm-email", {
    method: "POST",
    body: JSON.stringify({ token })
  });
}

export function resetPasswordWithToken(token: string, password: string) {
  return pulseApi<{ ok: boolean; message: string }>("/api/mobile/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, password })
  });
}

export function logoutMobileSession() {
  return pulseApi<{ ok: boolean }>("/api/mobile/auth/logout", { method: "POST" });
}
