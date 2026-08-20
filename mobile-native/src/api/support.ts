import { pulseApi } from "./pulseApi";
import { readJsonCache, writeJsonCache } from "../core/cache";

const SUPPORT_CACHE_KEY = "pulsesoc.native.support.state";

export type SupportTicket = {
  id: number;
  issue_type?: string;
  subject?: string;
  status?: string;
  priority?: string;
  created_at?: string;
  updated_at?: string;
};

export type SupportTicketPayload = {
  name?: string;
  email: string;
  issue_type: string;
  subject: string;
  message: string;
};

export type SecurityReportPayload = {
  email?: string;
  report_type: string;
  target?: string;
  description: string;
};

export type ScamShieldResult = {
  ok?: boolean;
  scan_id?: number;
  risk_level?: string;
  risk_score?: number;
  summary?: string;
  red_flags?: string[];
  safe_actions?: string[];
  confidence?: number;
  message?: string;
  response?: string;
};

export type SupportState = {
  tickets: SupportTicket[];
  loadedAt: string;
};

export type SupportActionResponse = {
  ok?: boolean;
  message?: string;
  ticket_id?: number;
  report_id?: number;
  /** Human-quotable ticket reference, format PS-YYYY-XXXXXXXX. */
  reference?: string;
};

export async function listSupportTickets() {
  const data = await pulseApi<{ ok?: boolean; tickets?: SupportTicket[] }>("/api/support/ticket");
  const tickets = normalizeTickets(data.tickets || []);
  const state = { tickets, loadedAt: new Date().toISOString() };
  await writeJsonCache(SUPPORT_CACHE_KEY, state).catch(() => undefined);
  return state;
}

export async function loadCachedSupportState() {
  return readJsonCache<SupportState>(SUPPORT_CACHE_KEY, (state) => ({
    tickets: normalizeTickets(state.tickets || []),
    loadedAt: state.loadedAt || ""
  }));
}

export async function createSupportTicket(payload: SupportTicketPayload) {
  return pulseApi<SupportActionResponse>("/api/support/ticket", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function submitSecurityReport(payload: SecurityReportPayload) {
  return pulseApi<SupportActionResponse>("/api/security/report", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export async function scanScamShield(text: string, scanType = "auto") {
  return pulseApi<ScamShieldResult>("/api/scam-shield/scan", {
    method: "POST",
    body: JSON.stringify({ text, scan_type: scanType })
  });
}

export async function reportPulseTarget(targetType: string, targetId: number | string, reason = "Needs review") {
  return pulseApi<SupportActionResponse>("/api/pulse/report", {
    method: "POST",
    body: JSON.stringify({ target_type: targetType, target_id: targetId, reason })
  });
}

export async function blockPulseUser(input: { blockedUserId?: number; publicPlayerId?: string; reason?: string }) {
  return pulseApi<SupportActionResponse>("/api/pulse/block", {
    method: "POST",
    body: JSON.stringify({
      blocked_user_id: input.blockedUserId || 0,
      public_player_id: input.publicPlayerId || "",
      reason: input.reason || "Blocked from native Trust & Safety"
    })
  });
}

export async function openSupportWebFallback(path = "/pulse/help") {
  const safePath = path.startsWith("/") && !path.startsWith("//") ? path : "/pulse/help";
  return {
    ok: false,
    path: safePath,
    status: "native_provider_boundary",
    message: "Support content remains inside native trust and safety surfaces for App Review."
  };
}

function normalizeTickets(tickets: SupportTicket[]) {
  return (tickets || [])
    .map((ticket) => ({
      ...ticket,
      id: Number(ticket.id || 0),
      issue_type: ticket.issue_type || "support",
      subject: ticket.subject || "Support request",
      status: ticket.status || "open",
      priority: ticket.priority || "normal"
    }))
    .filter((ticket) => ticket.id > 0);
}
