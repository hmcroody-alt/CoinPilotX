/**
 * The form mirrors server rules so users hear about a bad rule before the round
 * trip. These tests pin the limits the server enforces: at most five conditions
 * per rule, window minutes 15–1440 for windowed types, and the basic/advanced
 * split that decides whether a free account can save the rule.
 */

import {
  MAX_CONDITIONS_PER_RULE,
  MAX_WINDOW_MINUTES,
  MIN_WINDOW_MINUTES,
  buildAlertPayload,
  classifyRuleType,
  conditionRequiresWindow,
  emptyConditionDraft,
  emptyCryptoAlertForm,
  formFromAlert,
  validateCryptoAlertForm,
  CryptoAlertFormState
} from "../cryptoAlertForm";
import { PremiumAlert } from "../../api/cryptoPremium";

function validForm(overrides: Partial<CryptoAlertFormState> = {}): CryptoAlertFormState {
  return {
    symbol: "BTC",
    match: "all",
    frequency: "once",
    cooldownSeconds: "0",
    conditions: [{ type: "price_above", threshold: "50000", windowMinutes: "" }],
    ...overrides
  };
}

function codes(form: CryptoAlertFormState, advancedAllowed = true): string[] {
  return validateCryptoAlertForm(form, { advancedAllowed }).map((issue) => issue.code);
}

describe("condition count limits", () => {
  it("accepts up to the five-condition cap", () => {
    const conditions = Array.from({ length: MAX_CONDITIONS_PER_RULE }, () => ({
      type: "price_above" as const,
      threshold: "1",
      windowMinutes: ""
    }));
    expect(codes(validForm({ conditions }))).toEqual([]);
  });

  it("rejects a sixth condition", () => {
    const conditions = Array.from({ length: MAX_CONDITIONS_PER_RULE + 1 }, () => ({
      type: "price_above" as const,
      threshold: "1",
      windowMinutes: ""
    }));
    expect(codes(validForm({ conditions }))).toContain("too_many_conditions");
  });

  it("rejects an empty condition list", () => {
    expect(codes(validForm({ conditions: [] }))).toContain("no_conditions");
  });
});

describe("window bounds", () => {
  const windowed = (windowMinutes: string): CryptoAlertFormState =>
    validForm({ conditions: [{ type: "price_move_pct", threshold: "5", windowMinutes }] });

  it("accepts the inclusive bounds 15 and 1440", () => {
    expect(codes(windowed(String(MIN_WINDOW_MINUTES)))).toEqual([]);
    expect(codes(windowed(String(MAX_WINDOW_MINUTES)))).toEqual([]);
  });

  it("rejects values outside 15–1440", () => {
    expect(codes(windowed(String(MIN_WINDOW_MINUTES - 1)))).toContain("window_out_of_range");
    expect(codes(windowed(String(MAX_WINDOW_MINUTES + 1)))).toContain("window_out_of_range");
  });

  it("rejects non-integer windows", () => {
    expect(codes(windowed("60.5"))).toContain("window_out_of_range");
  });

  it("requires a window for windowed types and not for plain thresholds", () => {
    expect(codes(windowed(""))).toContain("window_required");
    expect(conditionRequiresWindow("price_move_pct")).toBe(true);
    expect(conditionRequiresWindow("price_above")).toBe(false);
    expect(codes(validForm())).toEqual([]);
  });
});

describe("basic/advanced classification and gating", () => {
  it("classifies a single above/below price condition as basic", () => {
    expect(classifyRuleType(validForm())).toBe("basic");
    expect(
      classifyRuleType(validForm({ conditions: [{ type: "price_below", threshold: "1", windowMinutes: "" }] }))
    ).toBe("basic");
  });

  it("classifies multi-condition or non-basic types as advanced", () => {
    expect(
      classifyRuleType(
        validForm({
          conditions: [
            { type: "price_above", threshold: "1", windowMinutes: "" },
            { type: "price_below", threshold: "2", windowMinutes: "" }
          ]
        })
      )
    ).toBe("advanced");
    expect(
      classifyRuleType(validForm({ conditions: [{ type: "volume_above", threshold: "1", windowMinutes: "" }] }))
    ).toBe("advanced");
  });

  it("keeps basic rules ungated for free accounts", () => {
    expect(codes(validForm(), false)).toEqual([]);
  });

  it("flags advanced rules as premium_required only when advanced is not allowed", () => {
    const advanced = validForm({
      conditions: [{ type: "price_crosses_above", threshold: "1", windowMinutes: "" }]
    });
    expect(codes(advanced, false)).toContain("premium_required");
    expect(codes(advanced, true)).not.toContain("premium_required");
  });

  it("reports the concrete problem before the upsell", () => {
    const broken = validForm({
      conditions: [{ type: "price_crosses_above", threshold: "", windowMinutes: "" }]
    });
    const issues = codes(broken, false);
    expect(issues.indexOf("threshold_required")).toBeLessThan(issues.indexOf("premium_required"));
  });
});

describe("field validation", () => {
  it("validates symbol, threshold, and cooldown", () => {
    expect(codes(validForm({ symbol: "" }))).toContain("symbol_required");
    expect(codes(validForm({ symbol: "b" }))).toContain("symbol_invalid");
    expect(
      codes(validForm({ conditions: [{ type: "price_above", threshold: "abc", windowMinutes: "" }] }))
    ).toContain("threshold_invalid");
    expect(codes(validForm({ cooldownSeconds: "-1" }))).toContain("cooldown_invalid");
    expect(codes(validForm({ cooldownSeconds: String(7 * 24 * 3600 + 1) }))).toContain("cooldown_invalid");
  });
});

describe("payload building", () => {
  it("builds a normalized payload with window_minutes only for windowed types", () => {
    const payload = buildAlertPayload(
      validForm({
        symbol: "  eth ",
        conditions: [
          { type: "price_above", threshold: " 3000 ", windowMinutes: "" },
          { type: "price_move_pct", threshold: "5", windowMinutes: "60" }
        ]
      })
    );
    expect(payload.symbol).toBe("ETH");
    expect(payload.rule_type).toBe("advanced");
    expect(payload.conditions[0]).toEqual({ type: "price_above", threshold: 3000 });
    expect(payload.conditions[1]).toEqual({ type: "price_move_pct", threshold: 5, window_minutes: 60 });
    expect(payload.cooldown_seconds).toBe(0);
  });

  it("round-trips an alert through formFromAlert", () => {
    const alert = {
      id: 7,
      asset_id: 1,
      symbol: "BTC",
      name: "Bitcoin",
      rule_type: "advanced",
      conditions: [{ type: "price_move_pct", threshold: 4, window_minutes: 30 }],
      match: "any",
      frequency: "recurring",
      cooldown_seconds: 600,
      enabled: true,
      status: "active",
      last_evaluated_at: null,
      last_triggered_at: null,
      premium: true
    } as unknown as PremiumAlert;
    const form = formFromAlert(alert);
    expect(form.conditions[0]).toEqual({ type: "price_move_pct", threshold: "4", windowMinutes: "30" });
    const payload = buildAlertPayload(form);
    expect(payload.match).toBe("any");
    expect(payload.frequency).toBe("recurring");
    expect(payload.cooldown_seconds).toBe(600);
  });

  it("seeds sensible defaults", () => {
    const form = emptyCryptoAlertForm("SOL");
    expect(form.symbol).toBe("SOL");
    expect(form.conditions).toHaveLength(1);
    expect(emptyConditionDraft("price_move_pct").windowMinutes).toBe("60");
    expect(emptyConditionDraft().windowMinutes).toBe("");
  });
});
