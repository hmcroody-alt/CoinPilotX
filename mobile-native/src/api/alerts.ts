import { readJsonCache, writeJsonCache } from "../core/cache";
import { Platform } from "react-native";
import { pulseApi } from "./pulseApi";

const ALERT_MANAGEMENT_CACHE_KEY = "pulsesoc.native.alert.management";
const ALERT_HISTORY_CACHE_PREFIX = "pulsesoc.native.alert.history.";

export type AlertChannel = "in_app" | "email" | "push" | "sms" | "telegram";

export type AlertRule = {
  id: number;
  alert_type?: string;
  asset_symbol?: string;
  symbol?: string;
  target?: string;
  condition?: string;
  condition_type?: string;
  threshold?: number | string;
  threshold_value?: number | string;
  target_value?: number | string;
  status?: string;
  active?: boolean | number;
  channels?: Record<AlertChannel, boolean>;
  delivery_statuses?: Record<string, Record<string, unknown>>;
  history_count?: number;
  last_triggered_at?: string;
  last_checked_at?: string;
  trigger_count?: number;
  cooldown_seconds?: number;
  source?: string;
  source_ref?: string;
  created_at?: string;
  updated_at?: string;
  deleted_at?: string;
};

export type AlertEvent = {
  id?: number;
  alert_rule_id?: number;
  user_id?: number;
  alert_type?: string;
  symbol?: string;
  condition?: string;
  observed_value?: number | string;
  threshold_value?: number | string;
  status?: string;
  delivery_status?: string;
  delivery?: Record<string, unknown>;
  channels?: Record<string, boolean>;
  message?: string;
  created_at?: string;
};

export type ChannelReadiness = {
  ready?: boolean;
  status?: string;
  label?: string;
  message?: string;
  setup_url?: string;
  subscription_count?: number;
  provider_configured?: boolean;
  phone_configured?: boolean;
  phone_verified?: boolean;
  sms_opt_in?: boolean;
  connected?: boolean;
};

export type AlertManagementState = {
  ok?: boolean;
  alerts: AlertRule[];
  cryptoAlerts: AlertRule[];
  events: AlertEvent[];
  channel_readiness: Record<AlertChannel, ChannelReadiness>;
  worker?: Record<string, unknown>;
  message?: string;
};

export type AlertHistoryResponse = {
  ok?: boolean;
  alert?: AlertRule;
  events: AlertEvent[];
  message?: string;
};

export type AlertMutationResponse = {
  ok?: boolean;
  alert_id?: number;
  alert?: AlertRule;
  status?: string;
  message?: string;
  channel_readiness?: Record<AlertChannel, ChannelReadiness>;
};

export type AlertFormPayload = {
  assetSymbol: string;
  targetValue: string;
  condition: string;
  notifyInApp: boolean;
  notifyEmail: boolean;
  notifyPush: boolean;
  notifySMS: boolean;
  notifyTelegram: boolean;
  note?: string;
};

export const ALERT_CHANNELS: AlertChannel[] = ["in_app", "email", "push", "sms", "telegram"];

export const ALERT_CONDITIONS = [
  { value: "above", label: "Above" },
  { value: "below", label: "Below" },
  { value: "moves_up_percent", label: "Moves up %" },
  { value: "moves_down_percent", label: "Moves down %" },
  { value: "volatility_above", label: "Volatility above %" }
];

export async function getAlertManagementState() {
  const [general, crypto] = await Promise.all([
    pulseApi<Partial<AlertManagementState>>(`/api/alerts?push_permission=${encodeURIComponent(currentPushPermission())}`),
    pulseApi<{ ok?: boolean; alerts?: AlertRule[]; message?: string }>("/api/crypto/alerts").catch(() => ({ alerts: [] }))
  ]);
  const state = normalizeAlertManagementState({
    ...general,
    cryptoAlerts: crypto.alerts || [],
    alerts: crypto.alerts?.length ? crypto.alerts : general.alerts || []
  });
  await cacheAlertManagementState(state).catch(() => undefined);
  return state;
}

export async function loadCachedAlertManagementState() {
  return readJsonCache<AlertManagementState>(ALERT_MANAGEMENT_CACHE_KEY, normalizeAlertManagementState);
}

export async function cacheAlertManagementState(state: AlertManagementState) {
  await writeJsonCache(ALERT_MANAGEMENT_CACHE_KEY, normalizeAlertManagementState(state));
}

export async function createCryptoAlert(payload: AlertFormPayload) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>("/api/crypto/alerts", {
    method: "POST",
    body: JSON.stringify(normalizeFormPayload(payload))
  }));
}

export async function updateCryptoAlert(alertId: number, payload: AlertFormPayload) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/crypto/alerts/${encodeURIComponent(String(alertId))}`, {
    method: "PATCH",
    body: JSON.stringify(normalizeFormPayload(payload))
  }));
}

export async function duplicateCryptoAlert(alertId: number) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/crypto/alerts/${encodeURIComponent(String(alertId))}/duplicate`, {
    method: "POST"
  }));
}

export async function getCryptoAlertHistory(alertId: number) {
  const history = normalizeAlertHistory(await pulseApi<AlertHistoryResponse>(`/api/crypto/alerts/${encodeURIComponent(String(alertId))}/history`));
  await writeJsonCache(`${ALERT_HISTORY_CACHE_PREFIX}${alertId}`, history).catch(() => undefined);
  return history;
}

export async function loadCachedCryptoAlertHistory(alertId: number) {
  return readJsonCache<AlertHistoryResponse>(`${ALERT_HISTORY_CACHE_PREFIX}${alertId}`, normalizeAlertHistory);
}

export async function pauseAlert(alertId: number) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/alerts/${encodeURIComponent(String(alertId))}/pause`, { method: "POST" }));
}

export async function resumeAlert(alertId: number) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/alerts/${encodeURIComponent(String(alertId))}/resume`, { method: "POST" }));
}

export async function deleteAlert(alertId: number) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/alerts/${encodeURIComponent(String(alertId))}/delete`, { method: "POST" }));
}

export async function testAlert(alertId: number) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/alerts/${encodeURIComponent(String(alertId))}/test`, { method: "POST" }));
}

export async function getAlertEvents(limit = 50) {
  const response = await pulseApi<{ ok?: boolean; events?: AlertEvent[]; message?: string }>(`/api/alerts/events?limit=${encodeURIComponent(String(limit))}`);
  return {
    ...response,
    events: normalizeAlertEvents(response.events || [])
  };
}

export async function getAlertChannelReadiness() {
  const response = await pulseApi<{ ok?: boolean; channel_readiness?: Record<AlertChannel, ChannelReadiness> }>(
    `/api/alerts/channel-readiness?push_permission=${encodeURIComponent(currentPushPermission())}`
  );
  return normalizeChannelReadiness(response.channel_readiness || {});
}

export async function testAlertChannel(channel: AlertChannel) {
  return normalizeMutation(await pulseApi<AlertMutationResponse>(`/api/alerts/test/${encodeURIComponent(channel)}`, {
    method: "POST",
    body: JSON.stringify({ permission: currentPushPermission() })
  }));
}

export function normalizeAlertManagementState(input: Partial<AlertManagementState> = {}): AlertManagementState {
  return {
    ...input,
    alerts: normalizeAlertRules(input.alerts || []),
    cryptoAlerts: normalizeAlertRules(input.cryptoAlerts || []),
    events: normalizeAlertEvents(input.events || []),
    channel_readiness: normalizeChannelReadiness(input.channel_readiness || {}),
    worker: input.worker || {}
  };
}

export function normalizeAlertHistory(input: Partial<AlertHistoryResponse> = {}): AlertHistoryResponse {
  return {
    ...input,
    alert: input.alert ? normalizeAlertRules([input.alert])[0] : undefined,
    events: normalizeAlertEvents(input.events || [])
  };
}

export function normalizeAlertRules(alerts: AlertRule[]) {
  return alerts
    .map((alert) => {
      const threshold = alert.threshold ?? alert.threshold_value ?? alert.target_value ?? "";
      return {
        ...alert,
        id: Number(alert.id || 0),
        alert_type: String(alert.alert_type || "coin_price"),
        asset_symbol: String(alert.asset_symbol || alert.symbol || alert.target || "MARKET").toUpperCase(),
        symbol: String(alert.symbol || alert.asset_symbol || alert.target || "MARKET").toUpperCase(),
        condition: String(alert.condition || alert.condition_type || "above"),
        condition_type: String(alert.condition_type || alert.condition || "above"),
        threshold,
        threshold_value: threshold,
        target_value: threshold,
        status: String(alert.status || (alert.active ? "active" : "paused")),
        channels: normalizeChannels(alert.channels || {}),
        delivery_statuses: alert.delivery_statuses || {},
        history_count: Number(alert.history_count || 0),
        trigger_count: Number(alert.trigger_count || 0),
        cooldown_seconds: Number(alert.cooldown_seconds || 0),
        source: String(alert.source || "server")
      };
    })
    .filter((alert) => alert.id > 0);
}

export function normalizeAlertEvents(events: AlertEvent[]) {
  return events.map((event) => ({
    ...event,
    id: Number(event.id || 0),
    alert_rule_id: Number(event.alert_rule_id || 0),
    symbol: String(event.symbol || "MARKET").toUpperCase(),
    condition: String(event.condition || ""),
    status: String(event.status || event.delivery_status || "recorded"),
    message: String(event.message || ""),
    channels: event.channels || {}
  }));
}

export function normalizeChannelReadiness(input: Record<string, ChannelReadiness>) {
  return ALERT_CHANNELS.reduce((next, channel) => {
    const value = input[channel] || {};
    next[channel] = {
      ...value,
      ready: Boolean(value.ready),
      status: String(value.status || (value.ready ? "ready" : "not_configured")),
      label: String(value.label || (value.ready ? "Ready" : "Needs setup")),
      message: String(value.message || "")
    };
    return next;
  }, {} as Record<AlertChannel, ChannelReadiness>);
}

export function alertConditionLabel(alert: AlertRule) {
  const condition = ALERT_CONDITIONS.find((item) => item.value === alert.condition)?.label || String(alert.condition || "Alert").replace(/_/g, " ");
  const threshold = alert.threshold ?? "";
  return threshold ? `${condition} ${threshold}` : condition;
}

export function alertStatusLabel(status?: string) {
  return String(status || "active").replace(/[_-]/g, " ");
}

export function alertEnabledChannels(alert: AlertRule) {
  return ALERT_CHANNELS.filter((channel) => Boolean(alert.channels?.[channel]));
}

export function alertWebPath(alertId?: number) {
  return alertId ? `/dashboard/crypto/alerts?alert_id=${encodeURIComponent(String(alertId))}` : "/dashboard/crypto/alerts";
}

function normalizeFormPayload(payload: AlertFormPayload) {
  const channels = {
    in_app: Boolean(payload.notifyInApp),
    email: Boolean(payload.notifyEmail),
    push: Boolean(payload.notifyPush),
    sms: Boolean(payload.notifySMS),
    telegram: Boolean(payload.notifyTelegram)
  };
  return {
    assetSymbol: String(payload.assetSymbol || "").trim().toUpperCase(),
    asset_symbol: String(payload.assetSymbol || "").trim().toUpperCase(),
    symbol: String(payload.assetSymbol || "").trim().toUpperCase(),
    targetValue: String(payload.targetValue || "").trim(),
    target_value: String(payload.targetValue || "").trim(),
    threshold: String(payload.targetValue || "").trim(),
    condition: String(payload.condition || "above"),
    condition_type: String(payload.condition || "above"),
    notifyInApp: channels.in_app,
    notifyEmail: channels.email,
    notifyPush: channels.push,
    notifySMS: channels.sms,
    notifyTelegram: channels.telegram,
    channels,
    push_permission: currentPushPermission(),
    note: String(payload.note || "").slice(0, 240)
  };
}

function normalizeMutation(input: AlertMutationResponse): AlertMutationResponse {
  return {
    ...input,
    alert_id: Number(input.alert_id || 0),
    alert: input.alert ? normalizeAlertRules([input.alert])[0] : undefined,
    status: input.status ? String(input.status) : undefined,
    message: String(input.message || (input.ok === false ? "Alert request failed." : "Alert request complete.")),
    channel_readiness: input.channel_readiness ? normalizeChannelReadiness(input.channel_readiness) : undefined
  };
}

function normalizeChannels(input: Partial<Record<AlertChannel, boolean>>) {
  const channels = ALERT_CHANNELS.reduce((next, channel) => {
    next[channel] = Boolean(input[channel]);
    return next;
  }, {} as Record<AlertChannel, boolean>);
  if (!ALERT_CHANNELS.some((channel) => channels[channel])) channels.in_app = true;
  return channels;
}

function currentPushPermission() {
  // Notification is a browser API. Native permission/token authority lives in
  // expo-notifications and is registered with the backend, so do not label a
  // real iOS/Android installation as "unsupported".
  if (Platform.OS !== "web") return "native";
  const notificationApi = (globalThis as { Notification?: { permission?: string } }).Notification;
  return String(notificationApi?.permission || "unsupported");
}
