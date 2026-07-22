import { readJsonCache, writeJsonCache } from "../core/cache";
import { pulseApi } from "./pulseApi";
import { blockPulseUser, listSupportTickets, reportPulseTarget, SupportTicket } from "./support";

const SAFETY_CACHE_KEY = "pulsesoc.native.safety.state";
const SAFETY_ACTION_CACHE_KEY = "pulsesoc.native.safety.actions";

export type SafetyActionKind = "block" | "report" | "mute_handoff" | "unblock_handoff";

export type SafetyActionRecord = {
  id: string;
  kind: SafetyActionKind;
  targetType: string;
  targetLabel: string;
  targetId?: number | string;
  reason: string;
  status: string;
  message: string;
  createdAt: string;
  serverId?: number;
  traceId?: string;
  serverAuthoritative: boolean;
};

export type SafetyNetworkState = {
  blockedUsers: number;
  mutedConversations: number;
  networkTrustScore: number;
  relationshipScore: number;
  securityUpdates: number;
  recommendations: string[];
};

export type SafetyState = {
  network: SafetyNetworkState;
  actions: SafetyActionRecord[];
  reports: SafetyActionRecord[];
  blocks: SafetyActionRecord[];
  mutes: SafetyActionRecord[];
  cases: SupportTicket[];
  capabilities: {
    blockCreate: boolean;
    blockList: boolean;
    unblock: boolean;
    userMute: boolean;
    reportCreate: boolean;
    reportHistory: boolean;
  };
  loadedAt: string;
};

export type SafetyActionResponse = {
  ok?: boolean;
  message?: string;
  report_id?: number;
  blocked_user_id?: number;
  public_player_id?: string;
  trace_id?: string;
};

export async function loadSafetyState() {
  const [networkData, actions, supportState] = await Promise.all([
    pulseApi<{ ok?: boolean; metrics?: Record<string, unknown>; intelligence?: Record<string, unknown>; event_bus?: Record<string, unknown> }>("/api/dashboard/network/state").catch(() => ({})),
    loadSafetyActions(),
    listSupportTickets().catch(() => ({ tickets: [], loadedAt: "" }))
  ]);
  const state = normalizeSafetyState(networkData, actions, supportState.tickets || []);
  await writeJsonCache(SAFETY_CACHE_KEY, state).catch(() => undefined);
  return state;
}

export async function loadCachedSafetyState() {
  return readJsonCache<SafetyState>(SAFETY_CACHE_KEY, normalizeSafetyStateFromCache);
}

export async function createSafetyReport(input: { targetType: string; targetId: string; reason: string }) {
  const response = await reportPulseTarget(input.targetType, input.targetId, input.reason) as SafetyActionResponse;
  const record = await recordSafetyAction({
    kind: "report",
    targetType: input.targetType,
    targetLabel: `${input.targetType} ${input.targetId}`,
    targetId: input.targetId,
    reason: input.reason,
    status: "submitted",
    message: response.message || "Report sent to moderation.",
    serverId: Number(response.report_id || 0) || undefined,
    serverAuthoritative: true
  });
  return { response, record };
}

export async function createSafetyBlock(input: { blockedUserId?: string; publicPlayerId?: string; reason: string }) {
  const response = await blockPulseUser({
    blockedUserId: Number(input.blockedUserId || 0) || undefined,
    publicPlayerId: input.publicPlayerId,
    reason: input.reason
  }) as SafetyActionResponse;
  const targetId = response.blocked_user_id || input.blockedUserId || response.public_player_id || input.publicPlayerId || "";
  const record = await recordSafetyAction({
    kind: "block",
    targetType: "user",
    targetLabel: response.public_player_id || input.publicPlayerId || `User ${targetId}`,
    targetId,
    reason: input.reason,
    status: "active",
    message: response.message || "User blocked and sent to moderation.",
    serverId: Number(response.blocked_user_id || 0) || undefined,
    traceId: response.trace_id,
    serverAuthoritative: true
  });
  return { response, record };
}

export async function recordMuteHandoff(input: { target: string; duration: string; reason: string }) {
  return recordSafetyAction({
    kind: "mute_handoff",
    targetType: "user",
    targetLabel: input.target,
    reason: `${input.duration}: ${input.reason}`,
    status: "native provider boundary",
    message: "Native user mute is not exposed as a server-authoritative API yet.",
    serverAuthoritative: false
  });
}

export async function recordUnblockHandoff(input: { target: string; reason: string }) {
  return recordSafetyAction({
    kind: "unblock_handoff",
    targetType: "user",
    targetLabel: input.target,
    reason: input.reason || "User requested unblock controls.",
    status: "native provider boundary",
    message: "Unblock requires protected PulseSoc network safety controls until a user-safe API is exposed.",
    serverAuthoritative: false
  });
}

export async function openSafetyWebFallback(path = "/dashboard/network/network-security") {
  const safePath = path.startsWith("/") && !path.startsWith("//") ? path : "/dashboard/network/network-security";
  return {
    ok: false,
    path: safePath,
    status: "native_provider_boundary",
    message: "Safety controls remain inside the native Safety Hub until this protected mutation is available."
  };
}

async function loadSafetyActions() {
  return readJsonCache<SafetyActionRecord[]>(SAFETY_ACTION_CACHE_KEY, normalizeSafetyActions).then((actions) => actions || []);
}

async function recordSafetyAction(action: Omit<SafetyActionRecord, "id" | "createdAt">) {
  const actions = await loadSafetyActions();
  const record: SafetyActionRecord = {
    ...action,
    id: `${action.kind}-${Date.now()}-${Math.round(Math.random() * 100000)}`,
    createdAt: new Date().toISOString()
  };
  await writeJsonCache(SAFETY_ACTION_CACHE_KEY, [record, ...actions].slice(0, 50)).catch(() => undefined);
  return record;
}

function normalizeSafetyState(
  networkData: { metrics?: Record<string, unknown>; intelligence?: Record<string, unknown>; event_bus?: Record<string, unknown> },
  actions: SafetyActionRecord[],
  tickets: SupportTicket[]
): SafetyState {
  const metrics = networkData.metrics || {};
  const intelligence = networkData.intelligence || {};
  const eventBus = networkData.event_bus || {};
  const recommendations = normalizeStringList(intelligence.recommended_next_actions);
  return {
    network: {
      blockedUsers: numberValue(metrics.blocked_users),
      mutedConversations: numberValue(metrics.muted_conversations),
      networkTrustScore: numberValue(metrics.network_trust_score || intelligence.network_health),
      relationshipScore: numberValue(metrics.relationship_score || intelligence.relationship_score),
      securityUpdates: numberValue(eventBus.security_updates || intelligence.risk_alerts),
      recommendations: recommendations.length ? recommendations : ["Review reports and safety controls when behavior feels unsafe."]
    },
    actions,
    reports: actions.filter((action) => action.kind === "report"),
    blocks: actions.filter((action) => action.kind === "block" || action.kind === "unblock_handoff"),
    mutes: actions.filter((action) => action.kind === "mute_handoff"),
    cases: normalizeTickets(tickets),
    capabilities: {
      blockCreate: true,
      blockList: false,
      unblock: false,
      userMute: false,
      reportCreate: true,
      reportHistory: false
    },
    loadedAt: new Date().toISOString()
  };
}

function normalizeSafetyStateFromCache(state: SafetyState): SafetyState {
  return {
    ...state,
    network: {
      blockedUsers: numberValue(state.network?.blockedUsers),
      mutedConversations: numberValue(state.network?.mutedConversations),
      networkTrustScore: numberValue(state.network?.networkTrustScore),
      relationshipScore: numberValue(state.network?.relationshipScore),
      securityUpdates: numberValue(state.network?.securityUpdates),
      recommendations: normalizeStringList(state.network?.recommendations)
    },
    actions: normalizeSafetyActions(state.actions || []),
    reports: normalizeSafetyActions(state.reports || []),
    blocks: normalizeSafetyActions(state.blocks || []),
    mutes: normalizeSafetyActions(state.mutes || []),
    cases: normalizeTickets(state.cases || []),
    capabilities: {
      ...state.capabilities,
      blockCreate: true,
      blockList: false,
      unblock: false,
      userMute: false,
      reportCreate: true,
      reportHistory: false
    },
    loadedAt: state.loadedAt || ""
  };
}

function normalizeSafetyActions(actions: SafetyActionRecord[]) {
  return (Array.isArray(actions) ? actions : [])
    .map((action) => ({
      ...action,
      id: String(action.id || `${action.kind}-${action.createdAt}`),
      kind: action.kind || "report",
      targetType: action.targetType || "item",
      targetLabel: action.targetLabel || "PulseSoc target",
      reason: action.reason || "",
      status: action.status || "recorded",
      message: action.message || "",
      createdAt: action.createdAt || "",
      serverAuthoritative: Boolean(action.serverAuthoritative)
    }))
    .filter((action) => action.id);
}

function normalizeTickets(tickets: SupportTicket[]) {
  return (tickets || []).map((ticket) => ({
    ...ticket,
    id: Number(ticket.id || 0),
    issue_type: ticket.issue_type || "support",
    subject: ticket.subject || "Support request",
    status: ticket.status || "open",
    priority: ticket.priority || "normal"
  })).filter((ticket) => ticket.id > 0);
}

function normalizeStringList(value: unknown) {
  return Array.isArray(value) ? Array.from(new Set(value.map((item) => String(item || "").trim()).filter(Boolean))) : [];
}

function numberValue(value: unknown) {
  return Number(value || 0);
}
