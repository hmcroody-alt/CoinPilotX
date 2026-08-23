import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const PREMIUM_STATUS_CACHE_KEY = "pulsesoc.native.premium.status";

export type PremiumStatus = {
  ok?: boolean;
  user_id?: number;
  premium_active?: boolean;
  founder_active?: boolean;
  founder_number?: number;
  plan?: string;
  subscription_status?: string;
  provider_status?: string;
  current_period_end?: string;
  cancel_at_period_end?: boolean;
  billing_portal_available?: boolean;
  billing_url?: string;
  premium_url?: string;
  profile_url?: string;
  home_url?: string;
  message?: string;
  checkout_url?: string;
  billing_portal_url?: string;
  payment?: PremiumPaymentStatus;
  entitlements?: PremiumEntitlement[];
  economy?: PremiumEconomyState;
};

export type PremiumPaymentStatus = {
  configured?: boolean;
  provider?: string;
  message?: string;
  status?: string;
};

export type PremiumEntitlement = {
  key: string;
  label: string;
  status: "active" | "locked" | "unavailable";
  detail: string;
};

export type PremiumEconomyState = {
  ok?: boolean;
  modules?: Array<Record<string, unknown>>;
  items?: Array<Record<string, unknown>>;
  entitlements?: Array<Record<string, unknown>>;
  capabilities?: Array<Record<string, unknown>>;
  message?: string;
};

export type PremiumActionResponse = {
  ok?: boolean;
  url?: string;
  checkout_url?: string;
  fallback_url?: string;
  message?: string;
  payment?: PremiumPaymentStatus;
};

export async function getPremiumStatus() {
  const status = normalizePremiumStatus(await pulseApi<PremiumStatus>("/api/premium/status"));
  const economy = await getPremiumEconomyState().catch(() => null);
  const next = normalizePremiumStatus({ ...status, economy: economy || undefined });
  await cachePremiumStatus(next).catch(() => undefined);
  return next;
}

export async function loadCachedPremiumStatus() {
  return readJsonCache<PremiumStatus>(PREMIUM_STATUS_CACHE_KEY, normalizePremiumStatus);
}

export async function cachePremiumStatus(status: PremiumStatus) {
  await writeJsonCache(PREMIUM_STATUS_CACHE_KEY, normalizePremiumStatus(status));
}

export async function startPremiumCheckout() {
  const result = await pulseApi<PremiumActionResponse>("/api/premium/checkout", {
    method: "POST",
    body: JSON.stringify({ plan_key: "founder_premium", source: "mobile_native_premium" })
  });
  const url = result.checkout_url || result.url;
  if (url) await openPremiumUrl(url);
  return result;
}

export async function openPremiumBillingPortal() {
  const result = await pulseApi<PremiumActionResponse>("/api/premium/billing-portal", {
    method: "POST",
    body: JSON.stringify({ source: "mobile_native_premium" })
  });
  const url = result.url || result.fallback_url;
  if (url) await openPremiumUrl(url);
  return result;
}

export async function openPremiumHub() {
  return {
    ok: false,
    path: "/pulse/premium",
    status: "native_provider_boundary",
    message: "Premium is handled in the PulseSoc Premium Center in the app."
  };
}

export function premiumWebUrl(path = "/pulse/premium") {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${PULSE_API_BASE_URL}${normalized}`;
}

export function normalizePremiumStatus(input: PremiumStatus): PremiumStatus {
  const active = Boolean(input.premium_active || input.founder_active || activeText(input.subscription_status) || activeText(input.provider_status));
  const founderActive = Boolean(input.founder_active);
  const plan = String(input.plan || (founderActive ? "founder_premium" : active ? "premium" : "free"));
  const subscription = String(input.subscription_status || (active ? "active" : "inactive"));
  return {
    ...input,
    premium_active: active,
    founder_active: founderActive,
    founder_number: Number(input.founder_number || 0),
    plan,
    subscription_status: subscription,
    provider_status: String(input.provider_status || ""),
    current_period_end: String(input.current_period_end || ""),
    billing_portal_available: Boolean(input.billing_portal_available),
    premium_url: input.premium_url || "/pulse/premium",
    profile_url: input.profile_url || "/pulse/profile",
    home_url: input.home_url || "/pulse",
    message: String(input.message || (active ? "Premium is active." : "Premium status is inactive.")),
    entitlements: normalizePremiumEntitlements(input)
  };
}

export function premiumPlanLabel(status: PremiumStatus | null) {
  if (!status) return "Unavailable";
  if (status.founder_active) return status.founder_number ? `Founder Premium #${status.founder_number}` : "Founder Premium";
  if (status.premium_active) return labelize(status.plan || "Premium");
  return "Free";
}

export function premiumStateLabel(status: PremiumStatus | null) {
  if (!status) return "Not loaded";
  if (status.founder_active) return "Founder active";
  if (status.premium_active) return "Premium active";
  return "Free access";
}

export function normalizePremiumEntitlements(status: PremiumStatus): PremiumEntitlement[] {
  const active = Boolean(status.premium_active);
  const founder = Boolean(status.founder_active);
  const defaults: PremiumEntitlement[] = [
    {
      key: "premium_access",
      label: "Premium access",
      status: active ? "active" : "locked",
      detail: active ? "Server confirms Premium access for this account." : "Included with Premium."
    },
    {
      key: "founder_identity",
      label: "Founder identity",
      status: founder ? "active" : "locked",
      detail: founder ? "Your Founder badge and number are confirmed by PulseSoc." : "Founder badge remains locked until the server confirms membership."
    },
    {
      key: "profile_theme",
      label: "Profile themes",
      status: active ? "active" : "locked",
      detail: "Profile themes are part of Premium."
    },
    {
      key: "creator_readiness",
      label: "Creator readiness",
      status: active ? "active" : "locked",
      detail: "PulseSoc unlocks creator tools as your account qualifies."
    },
    {
      key: "premium_intelligence",
      label: "Premium intelligence",
      status: active ? "active" : "locked",
      detail: "Premium intelligence surfaces continue through existing PulseSoc routes."
    }
  ];

  const serverItems = [
    ...(status.economy?.entitlements || []),
    ...(status.economy?.capabilities || []),
    ...(status.economy?.modules || []),
    ...(status.economy?.items || [])
  ];
  const mapped = serverItems.map(mapServerEntitlement).filter((item): item is PremiumEntitlement => Boolean(item));
  const seen = new Set<string>();
  return [...mapped, ...defaults].filter((item) => {
    if (seen.has(item.key)) return false;
    seen.add(item.key);
    return true;
  });
}

async function getPremiumEconomyState() {
  return pulseApi<PremiumEconomyState>("/api/dashboard/economy/state");
}

async function openPremiumUrl(url: string) {
  const target = /^https?:\/\//i.test(url) ? url : premiumWebUrl(url);
  return {
    ok: false,
    target,
    status: "native_provider_boundary",
    message: "Payment link received. App Store purchasing in the app must complete this flow before release."
  };
}

function mapServerEntitlement(item: Record<string, unknown>): PremiumEntitlement | null {
  const key = String(item.key || item.entitlement_key || item.capability || item.module_key || item.id || "").trim();
  if (!key) return null;
  const label = String(item.label || item.title || item.name || labelize(key));
  const raw = String(item.status || item.state || item.access || "").toLowerCase();
  const active = raw === "active" || raw === "enabled" || raw === "unlocked" || item.active === true || item.available === true;
  const unavailable = raw === "unavailable" || item.available === false;
  return {
    key,
    label,
    status: active ? "active" : unavailable ? "unavailable" : "locked",
    detail: String(item.description || item.summary || item.locked_reason || item.reason || (active ? "Server marks this entitlement active." : "Server marks this entitlement locked."))
  };
}

function activeText(value?: string) {
  return ["active", "trialing", "premium", "founder"].includes(String(value || "").toLowerCase());
}

function labelize(value: string) {
  return String(value || "Premium")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
