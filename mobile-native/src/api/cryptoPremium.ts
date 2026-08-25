/**
 * Premium Crypto Intelligence client — advanced alerts, trigger history, and
 * the premium portfolio read paths.
 *
 * These are the `/api/mobile/crypto/*` contracts. They are deliberately kept
 * separate from `./alerts.ts`: that module speaks the legacy
 * `/api/crypto/alerts` shape the basic Alert Management screen was built on,
 * and the free basic flow must keep working exactly as it does today while
 * this premium surface grows beside it.
 *
 * Premium denial arrives as HTTP 200 with `{ok:false, code:"premium_required"}`.
 * `pulseApi` turns any `ok:false` body into a thrown `PulseApiError` that
 * carries the parsed body in `details`, so gating is detected here with
 * `isPremiumRequired(error)` and rendered by screens as an upsell, never as a
 * generic failure.
 */

import { pulseApi, PulseApiError } from "./pulseApi";

/* ------------------------------------------------------------------ *
 * Alert rule shapes
 * ------------------------------------------------------------------ */

export const ALERT_CONDITION_TYPES = [
  "price_above",
  "price_below",
  "price_crosses_above",
  "price_crosses_below",
  "price_move_pct",
  "price_move_abs",
  "volume_above",
  "volume_below",
  "volume_move_pct",
  "market_cap_above",
  "market_cap_below",
  "market_cap_move_pct",
  "portfolio_value_above",
  "portfolio_value_below",
  "portfolio_move_pct",
  "allocation_above"
] as const;

export type AlertConditionType = (typeof ALERT_CONDITION_TYPES)[number];

export type AlertRuleType = "basic" | "advanced";
export type AlertConditionMatch = "all" | "any";
export type AlertFrequency = "once" | "every_crossing" | "recurring";

export type PremiumAlertCondition = {
  type: AlertConditionType;
  threshold: number;
  direction?: string;
  window_minutes?: number;
};

export type PremiumAlert = {
  id: number;
  asset_id: number | string | null;
  symbol: string;
  name: string;
  rule_type: AlertRuleType;
  conditions: PremiumAlertCondition[];
  match: AlertConditionMatch;
  frequency: AlertFrequency;
  cooldown_seconds: number;
  enabled: boolean;
  status: string;
  last_evaluated_at: string | null;
  last_triggered_at: string | null;
  premium: boolean;
};

export type CryptoAlertCapabilities = {
  advanced_alerts: boolean;
};

export type PremiumAlertList = {
  ok: boolean;
  items: PremiumAlert[];
  capabilities: CryptoAlertCapabilities;
};

/** The client-built payload for create; PATCH sends any subset of it. */
export type PremiumAlertPayload = {
  symbol: string;
  asset_id?: number | string | null;
  name?: string;
  rule_type: AlertRuleType;
  conditions: PremiumAlertCondition[];
  match: AlertConditionMatch;
  frequency: AlertFrequency;
  cooldown_seconds: number;
  enabled?: boolean;
};

export type PremiumAlertMutation = {
  ok: boolean;
  item?: PremiumAlert;
  message?: string;
};

/* ------------------------------------------------------------------ *
 * Alert endpoints
 * ------------------------------------------------------------------ */

const ALERTS_PATH = "/api/mobile/crypto/alerts";

export async function getPremiumAlerts(): Promise<PremiumAlertList> {
  const response = await pulseApi<Partial<PremiumAlertList>>(ALERTS_PATH);
  return {
    ok: response.ok !== false,
    items: (response.items || []).map(normalizePremiumAlert),
    capabilities: normalizeCapabilities(response.capabilities)
  };
}

export async function createPremiumAlert(payload: PremiumAlertPayload): Promise<PremiumAlertMutation> {
  const response = await pulseApi<Partial<PremiumAlertMutation> & { item?: PremiumAlert }>(ALERTS_PATH, {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return normalizeMutation(response);
}

export async function updatePremiumAlert(
  alertId: number,
  payload: Partial<PremiumAlertPayload>
): Promise<PremiumAlertMutation> {
  const response = await pulseApi<Partial<PremiumAlertMutation> & { item?: PremiumAlert }>(
    `${ALERTS_PATH}/${encodeURIComponent(String(alertId))}`,
    { method: "PATCH", body: JSON.stringify(payload) }
  );
  return normalizeMutation(response);
}

export async function deletePremiumAlert(alertId: number): Promise<PremiumAlertMutation> {
  const response = await pulseApi<Partial<PremiumAlertMutation>>(
    `${ALERTS_PATH}/${encodeURIComponent(String(alertId))}`,
    { method: "DELETE" }
  );
  return normalizeMutation(response);
}

/* ------------------------------------------------------------------ *
 * Trigger history
 * ------------------------------------------------------------------ */

export type PremiumAlertTrigger = {
  alert_id: number;
  symbol: string;
  condition_summary: string;
  observed_value: number | string | null;
  triggered_at: string;
  notification_result: string;
};

export type PremiumAlertHistory = {
  ok: boolean;
  items: PremiumAlertTrigger[];
  has_more: boolean;
};

export async function getPremiumAlertHistory(options: {
  limit?: number;
  offset?: number;
  alertId?: number;
} = {}): Promise<PremiumAlertHistory> {
  const parameters = new URLSearchParams();
  parameters.set("limit", String(options.limit ?? 30));
  parameters.set("offset", String(options.offset ?? 0));
  if (options.alertId) parameters.set("alert_id", String(options.alertId));
  const response = await pulseApi<Partial<PremiumAlertHistory>>(`${ALERTS_PATH}/history?${parameters.toString()}`);
  return {
    ok: response.ok !== false,
    items: (response.items || []).map((item) => ({
      alert_id: Number(item.alert_id || 0),
      symbol: String(item.symbol || "").toUpperCase(),
      condition_summary: String(item.condition_summary || ""),
      observed_value: item.observed_value ?? null,
      triggered_at: String(item.triggered_at || ""),
      notification_result: String(item.notification_result || "")
    })),
    has_more: Boolean(response.has_more)
  };
}

/* ------------------------------------------------------------------ *
 * Portfolio
 * ------------------------------------------------------------------ */

export type PortfolioHolding = {
  asset_id: number | string | null;
  symbol: string;
  name: string;
  amount: number;
  current_price: number;
  current_value: number;
  allocation_pct: number;
  average_buy_price: number | null;
  unrealized_pl: number | null;
};

export type PortfolioConcentration = {
  top_symbol: string;
  top_pct: number;
};

export type PortfolioSnapshot = {
  ok: true;
  total_value: number;
  calculated_at: string;
  market_data_observed_at: string;
  change_24h_pct: number | null;
  unrealized_pl: number | null;
  holdings: PortfolioHolding[];
  concentration: PortfolioConcentration;
};

export async function getPremiumPortfolio(): Promise<PortfolioSnapshot> {
  const response = await pulseApi<Partial<PortfolioSnapshot>>("/api/mobile/crypto/portfolio");
  return {
    ok: true,
    total_value: Number(response.total_value || 0),
    calculated_at: String(response.calculated_at || ""),
    market_data_observed_at: String(response.market_data_observed_at || ""),
    // Distinguish "server said null" from "server said 0": a null 24h change
    // renders as "--"; a real zero renders as 0%. Coercing here would fake data.
    change_24h_pct: response.change_24h_pct === null || response.change_24h_pct === undefined
      ? null
      : Number(response.change_24h_pct),
    unrealized_pl: response.unrealized_pl === null || response.unrealized_pl === undefined
      ? null
      : Number(response.unrealized_pl),
    holdings: (response.holdings || []).map((holding) => ({
      asset_id: holding.asset_id ?? null,
      symbol: String(holding.symbol || "").toUpperCase(),
      name: String(holding.name || holding.symbol || ""),
      amount: Number(holding.amount || 0),
      current_price: Number(holding.current_price || 0),
      current_value: Number(holding.current_value || 0),
      allocation_pct: Number(holding.allocation_pct || 0),
      average_buy_price: holding.average_buy_price === null || holding.average_buy_price === undefined
        ? null
        : Number(holding.average_buy_price),
      unrealized_pl: holding.unrealized_pl === null || holding.unrealized_pl === undefined
        ? null
        : Number(holding.unrealized_pl)
    })),
    concentration: {
      top_symbol: String(response.concentration?.top_symbol || "").toUpperCase(),
      top_pct: Number(response.concentration?.top_pct || 0)
    }
  };
}

export const PORTFOLIO_PERIODS = ["24h", "7d", "30d", "90d", "1y", "all"] as const;
export type PortfolioPeriod = (typeof PORTFOLIO_PERIODS)[number];
export type PortfolioCoverage = "full" | "partial" | "none";

export type PortfolioHistory = {
  ok: boolean;
  period: PortfolioPeriod;
  points: { t: number; value: number }[];
  coverage: PortfolioCoverage;
};

export async function getPremiumPortfolioHistory(period: PortfolioPeriod): Promise<PortfolioHistory> {
  const response = await pulseApi<Partial<PortfolioHistory>>(
    `/api/mobile/crypto/portfolio/history?period=${encodeURIComponent(period)}`
  );
  return {
    ok: response.ok !== false,
    period: (PORTFOLIO_PERIODS as readonly string[]).includes(String(response.period))
      ? (response.period as PortfolioPeriod)
      : period,
    points: (response.points || [])
      .map((point) => ({ t: Number(point.t || 0), value: Number(point.value || 0) }))
      .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.value)),
    coverage: response.coverage === "partial" || response.coverage === "none" ? response.coverage : "full"
  };
}

/* ------------------------------------------------------------------ *
 * Premium gating
 * ------------------------------------------------------------------ */

/**
 * True when the server answered `{ok:false, code:"premium_required"}`. The body
 * rides on `PulseApiError.details`; `code` on the error itself is populated from
 * `error_code`/`error`, so both are checked to stay robust to either spelling.
 */
export function isPremiumRequired(error: unknown): boolean {
  if (!(error instanceof PulseApiError)) return false;
  if (error.code === "premium_required") return true;
  return String(error.details?.code || "") === "premium_required";
}

/** The gated capability name, when the denial carried one. */
export function premiumRequiredCapability(error: unknown): string {
  if (!(error instanceof PulseApiError)) return "";
  return String(error.details?.capability || "");
}

/* ------------------------------------------------------------------ *
 * Normalizers
 * ------------------------------------------------------------------ */

export function normalizePremiumAlert(input: Partial<PremiumAlert>): PremiumAlert {
  return {
    id: Number(input.id || 0),
    asset_id: input.asset_id ?? null,
    symbol: String(input.symbol || "").toUpperCase(),
    name: String(input.name || input.symbol || ""),
    rule_type: input.rule_type === "advanced" ? "advanced" : "basic",
    conditions: (input.conditions || []).map((condition) => ({
      type: condition.type as AlertConditionType,
      threshold: Number(condition.threshold),
      ...(condition.direction ? { direction: String(condition.direction) } : {}),
      ...(condition.window_minutes !== undefined && condition.window_minutes !== null
        ? { window_minutes: Number(condition.window_minutes) }
        : {})
    })),
    match: input.match === "any" ? "any" : "all",
    frequency: input.frequency === "every_crossing" || input.frequency === "recurring" ? input.frequency : "once",
    cooldown_seconds: Number(input.cooldown_seconds || 0),
    enabled: input.enabled !== false,
    status: String(input.status || (input.enabled === false ? "paused" : "active")),
    last_evaluated_at: input.last_evaluated_at ? String(input.last_evaluated_at) : null,
    last_triggered_at: input.last_triggered_at ? String(input.last_triggered_at) : null,
    premium: Boolean(input.premium)
  };
}

function normalizeCapabilities(input?: Partial<CryptoAlertCapabilities>): CryptoAlertCapabilities {
  return { advanced_alerts: Boolean(input?.advanced_alerts) };
}

function normalizeMutation(input: Partial<PremiumAlertMutation> & { item?: Partial<PremiumAlert> }): PremiumAlertMutation {
  return {
    ok: input.ok !== false,
    item: input.item ? normalizePremiumAlert(input.item) : undefined,
    message: input.message ? String(input.message) : undefined
  };
}
