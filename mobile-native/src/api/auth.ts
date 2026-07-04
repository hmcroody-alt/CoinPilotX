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

export function signup(payload: { full_name: string; username: string; email: string; password: string }) {
  return pulseApi<SessionResponse>("/api/mobile/auth/register", {
    method: "POST",
    body: JSON.stringify({ ...payload, age_confirmed: true, email_opt_in: true })
  });
}

export function logout() {
  return pulseApi<{ ok: boolean }>("/api/mobile/auth/logout", {
    method: "POST",
    body: JSON.stringify({})
  });
}
