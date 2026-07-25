import { pulseApi } from "./pulseApi";
import { readJsonCache, writeJsonCache } from "../core/cache";

const ACCOUNT_STATE_CACHE_KEY = "pulsesoc.native.account.state";

export type AccountSettings = {
  profile_visibility?: string;
  message_requests?: string;
  notifications_enabled?: string;
  status_replies?: string;
  ads_personalization?: string;
  sci_fi_intensity?: string;
  welcome_experience?: string;
  welcome_sound?: string;
  welcome_haptics?: string;
  reduced_motion?: string;
  language?: string;
  [key: string]: string | undefined;
};

export type AccountStatus = {
  ok?: boolean;
  user_id?: number;
  email?: string;
  plan?: string;
  subscription_plan?: string;
  subscription_status?: string;
  access_label?: string;
  telegram_linked?: boolean;
  telegram_username?: string;
  preferred_language?: string;
  language?: string;
  updated_at?: string;
};

export type AccountSecurity = {
  score?: number;
  label?: string;
  trust_level?: string;
  email?: string;
  email_verified?: boolean;
  phone?: string;
  phone_verified?: boolean;
  two_factor_enabled?: boolean;
  recovery_email?: string;
  recovery_phone?: string;
  recovery_codes_ready?: boolean;
  trusted_devices_count?: number;
  active_sessions_count?: number;
  suspicious_alerts?: number;
};

export type TrustedDevice = {
  id: number;
  device_label?: string;
  last_seen_at?: string;
  created_at?: string;
};

export type SecurityEvent = {
  id: number;
  event_type?: string;
  device_label?: string;
  created_at?: string;
};

export type AccountState = {
  status: AccountStatus;
  settings: AccountSettings;
  security: AccountSecurity;
  trustedDevices: TrustedDevice[];
  securityEvents: SecurityEvent[];
  loadedAt: string;
};

export type AccountActionResponse = {
  ok?: boolean;
  message?: string;
  recovery_codes?: string[];
  settings?: AccountSettings;
};

export type AccountLanguageResponse = {
  ok?: boolean;
  message?: string;
  preferred_language?: string;
  language?: string;
  supported_languages?: string[];
  any_language_supported?: boolean;
};

export async function getAccountStatus() {
  return pulseApi<AccountStatus>("/api/account/status");
}

export async function getAccountSettings() {
  const data = await pulseApi<{ ok?: boolean; settings?: AccountSettings }>("/api/dashboard/account/settings");
  return normalizeSettings(data.settings || {});
}

export async function updateAccountSettings(settings: AccountSettings) {
  const data = await pulseApi<AccountActionResponse>("/api/dashboard/account/settings", {
    method: "POST",
    body: JSON.stringify(settings)
  });
  return {
    ...data,
    settings: normalizeSettings(data.settings || settings)
  };
}

export async function getAccountLanguage() {
  return pulseApi<AccountLanguageResponse>("/api/account/language");
}

export async function updateAccountLanguage(language: string) {
  return pulseApi<AccountLanguageResponse>("/api/account/language", {
    method: "POST",
    body: JSON.stringify({ preferred_language: language, language })
  });
}

export async function getAccountSecurity() {
  const data = await pulseApi<{ ok?: boolean; security?: AccountSecurity }>("/api/account/security");
  return normalizeSecurity(data.security || {});
}

export async function getTrustedDevices() {
  const data = await pulseApi<{ ok?: boolean; devices?: TrustedDevice[] }>("/api/account/trusted-devices");
  return normalizeDevices(data.devices || []);
}

export async function removeTrustedDevice(deviceId: number) {
  return pulseApi<AccountActionResponse>(`/api/account/trusted-devices/${deviceId}`, {
    method: "DELETE"
  });
}

export async function getSecurityEvents() {
  const data = await pulseApi<{ ok?: boolean; events?: SecurityEvent[] }>("/api/account/security-events");
  return normalizeSecurityEvents(data.events || []);
}

export async function verifyEmail() {
  return pulseApi<AccountActionResponse>("/api/account/verify-email", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function verifyPhone() {
  return pulseApi<AccountActionResponse>("/api/account/verify-phone", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function enableTwoFactor() {
  return pulseApi<AccountActionResponse>("/api/account/2fa/enable", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function disableTwoFactor() {
  return pulseApi<AccountActionResponse>("/api/account/2fa/disable", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function generateRecoveryCodes() {
  return pulseApi<AccountActionResponse>("/api/account/recovery-codes/generate", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function reauthenticate() {
  return pulseApi<AccountActionResponse>("/api/account/reauthenticate", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function revokeAllSessions() {
  return pulseApi<AccountActionResponse>("/api/account/sessions/revoke-all", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export async function loadAccountState() {
  const [status, settings, security, trustedDevices, securityEvents] = await Promise.all([
    getAccountStatus(),
    getAccountSettings(),
    getAccountSecurity(),
    getTrustedDevices(),
    getSecurityEvents()
  ]);
  const state = normalizeAccountState({
    status,
    settings,
    security,
    trustedDevices,
    securityEvents,
    loadedAt: new Date().toISOString()
  });
  await cacheAccountState(state);
  return state;
}

export async function loadCachedAccountState() {
  return readJsonCache<AccountState>(ACCOUNT_STATE_CACHE_KEY, normalizeAccountState);
}

export async function cacheAccountState(state: AccountState) {
  await writeJsonCache(ACCOUNT_STATE_CACHE_KEY, normalizeAccountState(state));
}

export async function openAccountWebFallback(path = "/pulse/settings/account") {
  const safePath = path.startsWith("/") && !path.startsWith("//") ? path : "/pulse/settings/account";
  return {
    ok: false,
    path: safePath,
    status: "native_provider_boundary",
    message: "Account action remains inside the native Account Center until this protected operation exposes a native mutation."
  };
}

function normalizeAccountState(input: AccountState): AccountState {
  return {
    status: input.status || {},
    settings: normalizeSettings(input.settings || {}),
    security: normalizeSecurity(input.security || {}),
    trustedDevices: normalizeDevices(input.trustedDevices || []),
    securityEvents: normalizeSecurityEvents(input.securityEvents || []),
    loadedAt: input.loadedAt || new Date().toISOString()
  };
}

function normalizeSettings(input: AccountSettings): AccountSettings {
  const settings = input || {};
  return {
    profile_visibility: settings.profile_visibility || "public",
    message_requests: settings.message_requests || "everyone",
    notifications_enabled: settings.notifications_enabled || "true",
    status_replies: settings.status_replies || "everyone",
    ads_personalization: settings.ads_personalization || "false",
    sci_fi_intensity: settings.sci_fi_intensity || "medium",
    welcome_experience: settings.welcome_experience || "true",
    welcome_sound: settings.welcome_sound || "false",
    welcome_haptics: settings.welcome_haptics || "true",
    reduced_motion: settings.reduced_motion || "system",
    language: settings.language || "en",
    ...settings
  };
}

function normalizeSecurity(input: AccountSecurity): AccountSecurity {
  const security = input || {};
  return {
    ...security,
    score: Number(security.score || 0),
    email_verified: Boolean(security.email_verified),
    phone_verified: Boolean(security.phone_verified),
    two_factor_enabled: Boolean(security.two_factor_enabled),
    recovery_codes_ready: Boolean(security.recovery_codes_ready),
    trusted_devices_count: Number(security.trusted_devices_count || 0),
    active_sessions_count: Number(security.active_sessions_count || 0),
    suspicious_alerts: Number(security.suspicious_alerts || 0)
  };
}

function normalizeDevices(devices: TrustedDevice[]) {
  return devices
    .map((device) => ({
      ...device,
      id: Number(device.id || 0),
      device_label: device.device_label || "Trusted device"
    }))
    .filter((device) => device.id > 0);
}

function normalizeSecurityEvents(events: SecurityEvent[]) {
  return events
    .map((event) => ({
      ...event,
      id: Number(event.id || 0),
      event_type: event.event_type || "security_event",
      device_label: event.device_label || "PulseSoc",
      created_at: event.created_at || ""
    }))
    .filter((event) => event.id > 0);
}
