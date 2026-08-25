/**
 * Crypto alert form logic — pure, testable, and shared by the alert center.
 *
 * The rules the server enforces are mirrored here so a user hears about a bad
 * rule before the round trip: at most five conditions per rule, window minutes
 * between 15 and 1440 for windowed types, numeric thresholds, and the
 * basic/advanced split that decides whether a free account can save the rule
 * at all. The server stays authoritative — this module only decides what the
 * form says before submitting, never what the account is entitled to.
 *
 * Everything user-facing is returned as an issue *code*; the screen maps codes
 * to catalog keys. No copy lives here.
 */

import {
  ALERT_CONDITION_TYPES,
  AlertConditionMatch,
  AlertConditionType,
  AlertFrequency,
  PremiumAlert,
  PremiumAlertCondition,
  PremiumAlertPayload
} from "../api/cryptoPremium";

export const MAX_CONDITIONS_PER_RULE = 5;
export const MIN_WINDOW_MINUTES = 15;
export const MAX_WINDOW_MINUTES = 1440;
export const DEFAULT_WINDOW_MINUTES = 60;
export const FREE_BASIC_RULE_LIMIT = 5;
export const PREMIUM_RULE_LIMIT = 100;
export const MAX_COOLDOWN_SECONDS = 7 * 24 * 3600;

/** Condition types a free account may use — the basic above/below pair. */
export const BASIC_CONDITION_TYPES: readonly AlertConditionType[] = ["price_above", "price_below"];

/** Types whose evaluation is over a rolling window and therefore need `window_minutes`. */
export const WINDOWED_CONDITION_TYPES: readonly AlertConditionType[] = [
  "price_move_pct",
  "price_move_abs",
  "volume_move_pct",
  "market_cap_move_pct",
  "portfolio_move_pct"
];

export type ConditionGroupKey = "price" | "volume" | "marketCap" | "portfolio";

/**
 * The picker's grouping. Order within a group is the order the picker renders,
 * with the simplest (above/below) first.
 */
export const CONDITION_GROUPS: readonly { key: ConditionGroupKey; types: readonly AlertConditionType[] }[] = [
  {
    key: "price",
    types: ["price_above", "price_below", "price_crosses_above", "price_crosses_below", "price_move_pct", "price_move_abs"]
  },
  { key: "volume", types: ["volume_above", "volume_below", "volume_move_pct"] },
  { key: "marketCap", types: ["market_cap_above", "market_cap_below", "market_cap_move_pct"] },
  { key: "portfolio", types: ["portfolio_value_above", "portfolio_value_below", "portfolio_move_pct", "allocation_above"] }
];

export const ALERT_FREQUENCIES: readonly AlertFrequency[] = ["once", "every_crossing", "recurring"];

export function conditionRequiresWindow(type: AlertConditionType): boolean {
  return WINDOWED_CONDITION_TYPES.includes(type);
}

export function isBasicConditionType(type: AlertConditionType): boolean {
  return BASIC_CONDITION_TYPES.includes(type);
}

/* ------------------------------------------------------------------ *
 * Form state
 * ------------------------------------------------------------------ */

/** One condition as the form holds it — strings, because inputs hold strings. */
export type ConditionDraft = {
  type: AlertConditionType;
  threshold: string;
  windowMinutes: string;
};

export type CryptoAlertFormState = {
  symbol: string;
  match: AlertConditionMatch;
  frequency: AlertFrequency;
  cooldownSeconds: string;
  conditions: ConditionDraft[];
};

export function emptyConditionDraft(type: AlertConditionType = "price_above"): ConditionDraft {
  return {
    type,
    threshold: "",
    windowMinutes: conditionRequiresWindow(type) ? String(DEFAULT_WINDOW_MINUTES) : ""
  };
}

export function emptyCryptoAlertForm(symbol = ""): CryptoAlertFormState {
  return {
    symbol,
    match: "all",
    frequency: "once",
    cooldownSeconds: "0",
    conditions: [emptyConditionDraft()]
  };
}

/** Seed the form from an existing rule, for editing. */
export function formFromAlert(alert: PremiumAlert): CryptoAlertFormState {
  return {
    symbol: alert.symbol,
    match: alert.match,
    frequency: alert.frequency,
    cooldownSeconds: String(alert.cooldown_seconds || 0),
    conditions: alert.conditions.length
      ? alert.conditions.map((condition) => ({
          type: condition.type,
          threshold: String(condition.threshold),
          windowMinutes:
            condition.window_minutes !== undefined
              ? String(condition.window_minutes)
              : conditionRequiresWindow(condition.type)
                ? String(DEFAULT_WINDOW_MINUTES)
                : ""
        }))
      : [emptyConditionDraft()]
  };
}

/**
 * A rule is basic exactly when it is a single plain above/below price
 * condition — the shape free accounts have always been able to create.
 * Anything else (more conditions, crossings, windows, volume, market cap,
 * portfolio) is advanced and premium-gated.
 */
export function classifyRuleType(form: Pick<CryptoAlertFormState, "conditions">): "basic" | "advanced" {
  if (form.conditions.length !== 1) return "advanced";
  return isBasicConditionType(form.conditions[0].type) ? "basic" : "advanced";
}

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

export type AlertFormIssueCode =
  | "symbol_required"
  | "symbol_invalid"
  | "no_conditions"
  | "too_many_conditions"
  | "condition_type_invalid"
  | "threshold_required"
  | "threshold_invalid"
  | "window_required"
  | "window_out_of_range"
  | "cooldown_invalid"
  | "premium_required";

export type AlertFormIssue = {
  code: AlertFormIssueCode;
  /** Present when the issue is about one condition row. */
  conditionIndex?: number;
};

export type ValidationOptions = {
  /** From `capabilities.advanced_alerts` on the list response. */
  advancedAllowed: boolean;
};

export function validateCryptoAlertForm(form: CryptoAlertFormState, options: ValidationOptions): AlertFormIssue[] {
  const issues: AlertFormIssue[] = [];

  const symbol = form.symbol.trim().toUpperCase();
  if (!symbol) issues.push({ code: "symbol_required" });
  else if (!/^[A-Z0-9.$:-]{2,24}$/.test(symbol)) issues.push({ code: "symbol_invalid" });

  if (!form.conditions.length) issues.push({ code: "no_conditions" });
  if (form.conditions.length > MAX_CONDITIONS_PER_RULE) issues.push({ code: "too_many_conditions" });

  form.conditions.forEach((condition, index) => {
    if (!(ALERT_CONDITION_TYPES as readonly string[]).includes(condition.type)) {
      issues.push({ code: "condition_type_invalid", conditionIndex: index });
      return;
    }
    const rawThreshold = condition.threshold.trim();
    if (!rawThreshold) {
      issues.push({ code: "threshold_required", conditionIndex: index });
    } else {
      const threshold = Number(rawThreshold);
      if (!Number.isFinite(threshold)) issues.push({ code: "threshold_invalid", conditionIndex: index });
    }
    if (conditionRequiresWindow(condition.type)) {
      const rawWindow = condition.windowMinutes.trim();
      if (!rawWindow) {
        issues.push({ code: "window_required", conditionIndex: index });
      } else {
        const window = Number(rawWindow);
        if (!Number.isInteger(window) || window < MIN_WINDOW_MINUTES || window > MAX_WINDOW_MINUTES) {
          issues.push({ code: "window_out_of_range", conditionIndex: index });
        }
      }
    }
  });

  const cooldown = Number(form.cooldownSeconds.trim() || "0");
  if (!Number.isInteger(cooldown) || cooldown < 0 || cooldown > MAX_COOLDOWN_SECONDS) {
    issues.push({ code: "cooldown_invalid" });
  }

  // Gating comes last so a free user fixing a typo sees the typo first and the
  // upsell only once the rule is otherwise saveable.
  if (!options.advancedAllowed && classifyRuleType(form) === "advanced") {
    issues.push({ code: "premium_required" });
  }

  return issues;
}

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

/** Build the create/update payload. Call only after validation passes. */
export function buildAlertPayload(form: CryptoAlertFormState): PremiumAlertPayload {
  const conditions: PremiumAlertCondition[] = form.conditions.map((condition) => ({
    type: condition.type,
    threshold: Number(condition.threshold.trim()),
    ...(conditionRequiresWindow(condition.type)
      ? { window_minutes: Number(condition.windowMinutes.trim()) }
      : {})
  }));
  return {
    symbol: form.symbol.trim().toUpperCase(),
    rule_type: classifyRuleType(form),
    conditions,
    match: form.match,
    frequency: form.frequency,
    cooldown_seconds: Number(form.cooldownSeconds.trim() || "0")
  };
}
