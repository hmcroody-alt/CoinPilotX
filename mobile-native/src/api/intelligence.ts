import { Linking } from "react-native";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { NotificationBadgeCounts } from "./notifications";
import { pulseApi } from "./pulseApi";

const INTELLIGENCE_STATE_CACHE_KEY = "pulsesoc.native.intelligence.state";
const ALERTS_CACHE_KEY = "pulsesoc.native.intelligence.alerts";

export type IntelligenceCard = {
  key?: string;
  card_key?: string;
  label?: string;
  state?: string;
  detail?: string;
  route?: string;
  action?: string;
  count?: number;
  confidence?: number;
  metric?: string;
};

export type IntelligenceHub = {
  overall_intelligence_score?: number;
  platform_health?: number;
  safety_score?: number;
  active_threats?: number;
  prediction_confidence?: number;
  new_opportunities?: number;
  personalized_daily_brief?: string;
  recommended_next_actions?: string[];
};

export type IntelligenceState = {
  ok?: boolean;
  intelligence?: {
    hub?: IntelligenceHub;
    metrics?: Record<string, unknown>;
    cards?: IntelligenceCard[];
    modules?: Record<string, unknown>;
    subsystems?: IntelligenceCard[];
  };
  message?: string;
};

export type PulseAlertRule = {
  id: number;
  alert_type?: string;
  asset_symbol?: string;
  symbol?: string;
  condition?: string;
  condition_type?: string;
  threshold?: number | string;
  threshold_value?: number | string;
  target_value?: number | string;
  status?: string;
  active?: boolean | number;
  source?: string;
  source_ref?: string;
  channels?: Record<string, boolean>;
  delivery_statuses?: Record<string, Record<string, unknown>>;
  history_count?: number;
  last_triggered_at?: string;
  last_checked_at?: string;
  updated_at?: string;
  created_at?: string;
  note?: string;
};

export type AlertListResponse = {
  ok?: boolean;
  alerts?: PulseAlertRule[];
  badge_counts?: NotificationBadgeCounts;
  message?: string;
};

export async function getIntelligenceState() {
  const state = normalizeIntelligenceState(await pulseApi<IntelligenceState>("/api/dashboard/intelligence/state"));
  await cacheIntelligenceState(state).catch(() => undefined);
  return state;
}

export async function loadCachedIntelligenceState() {
  return readJsonCache<IntelligenceState>(INTELLIGENCE_STATE_CACHE_KEY, normalizeIntelligenceState);
}

export async function cacheIntelligenceState(state: IntelligenceState) {
  await writeJsonCache(INTELLIGENCE_STATE_CACHE_KEY, normalizeIntelligenceState(state));
}

export async function listCryptoAlerts() {
  const response = normalizeAlertList(await pulseApi<AlertListResponse>("/api/crypto/alerts"));
  await cacheAlertList(response).catch(() => undefined);
  return response;
}

export async function loadCachedAlertList() {
  return readJsonCache<AlertListResponse>(ALERTS_CACHE_KEY, normalizeAlertList);
}

export async function cacheAlertList(response: AlertListResponse) {
  await writeJsonCache(ALERTS_CACHE_KEY, normalizeAlertList(response));
}

export async function openIntelligenceWebFallback(path = "/dashboard/intelligence") {
  const target = /^https?:\/\//i.test(path) ? path : `${PULSE_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  await Linking.openURL(target).catch(() => undefined);
}

export function normalizeIntelligenceState(input: IntelligenceState): IntelligenceState {
  const intelligence = input.intelligence || {};
  const hub = intelligence.hub || {};
  return {
    ...input,
    intelligence: {
      ...intelligence,
      hub: {
        ...hub,
        overall_intelligence_score: Number(hub.overall_intelligence_score || 0),
        platform_health: Number(hub.platform_health || 0),
        safety_score: Number(hub.safety_score || 0),
        active_threats: Number(hub.active_threats || 0),
        prediction_confidence: Number(hub.prediction_confidence || 0),
        new_opportunities: Number(hub.new_opportunities || 0),
        personalized_daily_brief: String(hub.personalized_daily_brief || ""),
        recommended_next_actions: Array.isArray(hub.recommended_next_actions) ? hub.recommended_next_actions.map(String) : []
      },
      metrics: intelligence.metrics || {},
      cards: normalizeIntelligenceCards(intelligence.cards || []),
      subsystems: normalizeIntelligenceCards(intelligence.subsystems || [])
    }
  };
}

export function normalizeAlertList(input: AlertListResponse): AlertListResponse {
  return {
    ...input,
    alerts: normalizeAlertRules(input.alerts || [])
  };
}

export function normalizeAlertRules(alerts: PulseAlertRule[]) {
  return alerts
    .map((alert) => ({
      ...alert,
      id: Number(alert.id || 0),
      asset_symbol: String(alert.asset_symbol || alert.symbol || "MARKET").toUpperCase(),
      condition: String(alert.condition || alert.condition_type || "alert"),
      threshold: alert.threshold ?? alert.threshold_value ?? alert.target_value ?? "",
      status: String(alert.status || (alert.active ? "active" : "paused")),
      source: String(alert.source || "server"),
      channels: alert.channels || {},
      delivery_statuses: alert.delivery_statuses || {},
      history_count: Number(alert.history_count || 0)
    }))
    .filter((alert) => alert.id > 0);
}

export function intelligenceStateLabel(state?: string) {
  return String(state || "READY").replace(/[_-]/g, " ");
}

export function alertConditionLabel(alert: PulseAlertRule) {
  const condition = String(alert.condition || "alert").replace(/_/g, " ");
  const threshold = alert.threshold ?? "";
  return threshold ? `${condition} ${threshold}` : condition;
}

export function alertWebPath(alertId?: number) {
  return alertId ? `/dashboard/crypto/alerts?alert_id=${encodeURIComponent(String(alertId))}` : "/dashboard/crypto/alerts";
}

function normalizeIntelligenceCards(cards: unknown) {
  const normalizedCards = Array.isArray(cards) ? cards : Object.entries(cards as Record<string, IntelligenceCard> || {}).map(([key, value]) => ({
    ...value,
    key: value.key || key
  }));
  return normalizedCards.map((card) => ({
    ...card,
    key: String(card.key || card.card_key || card.label || ""),
    label: String(card.label || "Intelligence"),
    state: String(card.state || "READY"),
    detail: String(card.detail || ""),
    route: String(card.route || "/dashboard/intelligence"),
    action: String(card.action || "Review Intelligence"),
    count: Number(card.count || 0),
    confidence: Number(card.confidence || 0)
  }));
}
