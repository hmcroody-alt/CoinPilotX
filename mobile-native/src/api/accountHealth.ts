import { readJsonCache, writeJsonCache } from "../core/cache";
import { getSecurityEvents, SecurityEvent } from "./account";
import { PULSE_API_BASE_URL } from "./config";
import { listSupportTickets, SupportTicket } from "./support";
import { pulseApi } from "./pulseApi";
import { VerificationStatus, submitVerificationAppeal } from "./verification";

const ACCOUNT_HEALTH_CACHE_KEY = "pulsesoc.native.account.health.state";

export type AccountHealthSubsystem = {
  key?: string;
  state?: string;
  score?: number;
  status?: string;
  primary_action?: string;
  recommendations?: string[];
  metrics?: {
    warnings?: number;
    strikes?: number;
    restrictions?: number;
    security_alerts?: number;
    appeals_available?: number;
    [key: string]: unknown;
  };
};

export type AccountVerificationSubsystem = {
  status?: VerificationStatus | string;
  score?: number;
  primary_action?: string;
  metrics?: {
    request_id?: number;
    status?: string;
    type?: string;
    [key: string]: unknown;
  };
};

export type AccountHealthState = {
  accountScore: number;
  riskLevel: string;
  status: string;
  score: number;
  warnings: number;
  strikes: number;
  restrictions: number;
  securityAlerts: number;
  appealsAvailable: number;
  primaryAction: string;
  recommendations: string[];
  enforcement: AccountHealthEnforcementItem[];
  appeals: AccountHealthAppealItem[];
  cases: AccountHealthCaseItem[];
  securityEvents: SecurityEvent[];
  loadedAt: string;
};

export type AccountHealthEnforcementItem = {
  key: string;
  label: string;
  count: number;
  status: string;
  detail: string;
};

export type AccountHealthAppealItem = {
  key: string;
  title: string;
  status: string;
  detail: string;
  requestId?: number;
  supported: boolean;
};

export type AccountHealthCaseItem = {
  id: number;
  subject: string;
  status: string;
  issueType: string;
  updatedAt: string;
};

export type AccountHealthActionResponse = {
  ok?: boolean;
  message?: string;
  request_id?: number;
  status?: string;
};

export async function loadAccountHealthState() {
  const accountData = await pulseApi<{ ok?: boolean; account?: Record<string, unknown> }>("/api/dashboard/account/state");
  const account = accountData.account || {};
  const [supportState, securityEvents] = await Promise.all([
    listSupportTickets().catch(() => ({ tickets: [], loadedAt: "" })),
    getSecurityEvents().catch(() => [])
  ]);
  const state = normalizeAccountHealthState(account, supportState.tickets || [], securityEvents || []);
  await writeJsonCache(ACCOUNT_HEALTH_CACHE_KEY, state).catch(() => undefined);
  return state;
}

export async function loadCachedAccountHealthState() {
  return readJsonCache<AccountHealthState>(ACCOUNT_HEALTH_CACHE_KEY, normalizeAccountHealthStateFromCache);
}

export async function submitAccountHealthVerificationAppeal(requestId: number, appealNote: string) {
  return submitVerificationAppeal(requestId, appealNote) as Promise<AccountHealthActionResponse>;
}

export async function openAccountHealthWebFallback(path = "/dashboard/account/health") {
  const safePath = path.startsWith("/") && !path.startsWith("//") ? path : "/dashboard/account/health";
  return {
    ok: false,
    path: safePath,
    status: "native_provider_boundary",
    message: "Account health details remain in the native review boundary until the protected operation is available."
  };
}

function normalizeAccountHealthState(account: Record<string, unknown>, tickets: SupportTicket[], securityEvents: SecurityEvent[]): AccountHealthState {
  const subsystems = ((account.subsystems || {}) as Record<string, unknown>) || {};
  const health = (subsystems.account_health || {}) as AccountHealthSubsystem;
  const verification = (subsystems.verification || {}) as AccountVerificationSubsystem;
  const metrics = health.metrics || {};
  const verificationMetrics = verification.metrics || {};
  const warnings = numberValue(metrics.warnings);
  const strikes = numberValue(metrics.strikes);
  const restrictions = numberValue(metrics.restrictions);
  const securityAlerts = numberValue(metrics.security_alerts);
  const appealsAvailable = numberValue(metrics.appeals_available);
  const healthStatus = String(health.status || metrics.status || "secure");
  const verificationStatus = String(verificationMetrics.status || verification.status || "not_started");
  const verificationRequestId = numberValue(verificationMetrics.request_id);
  const recommendations = normalizeStringList([
    ...normalizeUnknownList(account.recommendations),
    ...normalizeUnknownList(health.recommendations)
  ]);
  return {
    accountScore: numberValue(account.account_score),
    riskLevel: String(account.risk_level || riskLevelFor(healthStatus, restrictions, strikes, securityAlerts)),
    status: healthStatus,
    score: numberValue(health.score || metrics.score || (healthStatus === "secure" ? 100 : 55)),
    warnings,
    strikes,
    restrictions,
    securityAlerts,
    appealsAvailable,
    primaryAction: String(health.primary_action || (healthStatus === "secure" ? "Review Account Health" : "Fix Account Issues")),
    recommendations: recommendations.length ? recommendations : ["Account systems are stable. Review recent activity periodically."],
    enforcement: [
      {
        key: "warnings",
        label: "Warnings",
        count: warnings,
        status: warnings > 0 ? "review" : "clear",
        detail: warnings > 0 ? "Review visible account warnings in the protected Account Health flow." : "No active warnings returned by account health."
      },
      {
        key: "strikes",
        label: "Strikes",
        count: strikes,
        status: strikes > 0 ? "appeal-ready" : "clear",
        detail: strikes > 0 ? "Strikes and appeals are decided by PulseSoc's review team." : "No active strikes returned by account health."
      },
      {
        key: "restrictions",
        label: "Restrictions",
        count: restrictions,
        status: restrictions > 0 ? "restricted" : "clear",
        detail: restrictions > 0 ? "Read what each restriction covers before you appeal it." : "No active restrictions returned by account health."
      }
    ],
    appeals: normalizeAppeals(appealsAvailable, verificationStatus, verificationRequestId),
    cases: normalizeCases(tickets),
    securityEvents,
    loadedAt: new Date().toISOString()
  };
}

function normalizeAccountHealthStateFromCache(state: AccountHealthState): AccountHealthState {
  return {
    ...state,
    accountScore: numberValue(state.accountScore),
    score: numberValue(state.score),
    warnings: numberValue(state.warnings),
    strikes: numberValue(state.strikes),
    restrictions: numberValue(state.restrictions),
    securityAlerts: numberValue(state.securityAlerts),
    appealsAvailable: numberValue(state.appealsAvailable),
    recommendations: normalizeStringList(state.recommendations || []),
    enforcement: Array.isArray(state.enforcement) ? state.enforcement : [],
    appeals: Array.isArray(state.appeals) ? state.appeals : [],
    cases: Array.isArray(state.cases) ? state.cases : [],
    securityEvents: Array.isArray(state.securityEvents) ? state.securityEvents : [],
    loadedAt: state.loadedAt || ""
  };
}

function normalizeAppeals(appealsAvailable: number, verificationStatus: string, verificationRequestId: number): AccountHealthAppealItem[] {
  const items: AccountHealthAppealItem[] = [
    {
      key: "account_health",
      title: "Account health appeal",
      status: appealsAvailable > 0 ? "available" : "not needed",
      detail: appealsAvailable > 0 ? "Warnings, strikes, or restrictions may be appealable through the protected Account Health flow." : "You have no appeals open right now.",
      supported: false
    }
  ];
  const verificationAppealable = ["rejected", "suspended", "needs_more_info"].includes(verificationStatus);
  if (verificationRequestId > 0 || verificationAppealable) {
    items.push({
      key: "verification",
      title: "Verification appeal",
      status: verificationStatus,
      detail: verificationAppealable ? "You can appeal a verification decision from the Verification Center." : "Verification appeal is only available after a review decision needs action.",
      requestId: verificationRequestId || undefined,
      supported: verificationAppealable && verificationRequestId > 0
    });
  }
  return items;
}

function normalizeCases(tickets: SupportTicket[]) {
  return (tickets || []).slice(0, 5).map((ticket) => ({
    id: Number(ticket.id || 0),
    subject: ticket.subject || "Support request",
    status: ticket.status || "open",
    issueType: ticket.issue_type || "support",
    updatedAt: ticket.updated_at || ticket.created_at || ""
  })).filter((ticket) => ticket.id > 0);
}

function normalizeUnknownList(items: unknown) {
  return Array.isArray(items) ? items.map((item) => String(item || "").trim()).filter(Boolean) : [];
}

function normalizeStringList(items: string[]) {
  return Array.from(new Set(items.map((item) => String(item || "").trim()).filter(Boolean)));
}

function numberValue(value: unknown) {
  return Number(value || 0);
}

function riskLevelFor(status: string, restrictions: number, strikes: number, securityAlerts: number) {
  if (status === "restricted" || restrictions > 0 || securityAlerts >= 3) return "High";
  if (status !== "secure" || strikes > 0 || securityAlerts > 0) return "Medium";
  return "Low";
}
