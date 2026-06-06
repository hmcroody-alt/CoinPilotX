import { pulseApi } from "./apiClient";

export type PulseUser = {
  user_id: number;
  username?: string;
  full_name?: string;
  email?: string;
  avatar_url?: string;
  premium_status?: string;
};

type SessionResponse = {
  ok: boolean;
  authenticated: boolean;
  user: PulseUser | null;
};

export function getMobileSession() {
  return pulseApi<SessionResponse>("/api/mobile/auth/session");
}

export function loginWithPassword(email: string, password: string) {
  return pulseApi<SessionResponse>("/api/mobile/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
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

export function logoutMobileSession() {
  return pulseApi<{ ok: boolean }>("/api/mobile/auth/logout", { method: "POST" });
}
