/**
 * The rule the seller types, and the one thing this module can get wrong.
 *
 * `parsePricingRule` has two jobs, and only one of them is interesting. Refusing
 * a bad number is a convenience — the server refuses it independently, so the
 * cost of a gap there is an error message arriving one tap later. The unit
 * conversion is not a convenience: `COST_PLUS_FIXED` is specified in minor
 * units, nothing downstream re-checks the magnitude, and getting it wrong
 * produces a *successful* batch that adds one cent to every product. That is the
 * failure these tests are for.
 */

import {
  EMPTY_PRICING_DRAFT,
  PRICING_RULE_TYPES,
  parsePricingRule,
  pricingRuleHint,
  pricingRuleLabel,
  pricingRuleSummary,
  pricingRuleUnit,
  type StorePricingRuleType
} from "../storeBulkPricing";

function parse(type: StorePricingRuleType, value: string) {
  return parsePricingRule({ type, value });
}

describe("the fixed-amount rule is the one that changes units", () => {
  /**
   * The whole reason this module exists.
   *
   * A seller adding "$5 to everything" types 5. The server's contract is minor
   * units. Sending 5 is a five-cent markup, which is not an error anywhere: the
   * batch succeeds, every price moves by a cent, and the seller finds out from
   * their margin. Nothing downstream can catch it, because five cents is a
   * perfectly valid markup.
   */
  it("sends dollars as cents", () => {
    expect(parse("COST_PLUS_FIXED", "5").rule).toEqual({ type: "COST_PLUS_FIXED", value: 500 });
  });

  it("keeps the cents the seller typed", () => {
    expect(parse("COST_PLUS_FIXED", "4.99").rule).toEqual({ type: "COST_PLUS_FIXED", value: 499 });
  });

  /**
   * `Math.round`, not truncation. 1.005 * 100 is 100.49999999999999 in binary
   * floating point, so `Math.trunc` would make a $1.005 markup into $1.00 —
   * a rounding this module does not get to make silently.
   */
  it("rounds a sub-cent amount to the nearest cent", () => {
    expect(parse("COST_PLUS_FIXED", "1.005").rule).toEqual({
      type: "COST_PLUS_FIXED",
      value: 100
    });
    expect(parse("COST_PLUS_FIXED", "1.006").rule).toEqual({
      type: "COST_PLUS_FIXED",
      value: 101
    });
  });

  /** And no other rule is touched: a percentage is a percentage. */
  it("leaves every other rule's number exactly as typed", () => {
    expect(parse("COST_PLUS_PERCENT", "20").rule).toEqual({
      type: "COST_PLUS_PERCENT",
      value: 20
    });
    expect(parse("MULTIPLIER", "2.5").rule).toEqual({ type: "MULTIPLIER", value: 2.5 });
    expect(parse("TARGET_MARGIN", "40").rule).toEqual({ type: "TARGET_MARGIN", value: 40 });
  });
});

describe("what it refuses", () => {
  it("refuses an empty field without calling it a mistake", () => {
    const parsed = parse("COST_PLUS_PERCENT", "");
    expect(parsed.rule).toBeNull();
    expect(parsed.error).toBe("Enter a number.");
  });

  it("refuses whitespace, which is an empty field the seller cannot see", () => {
    expect(parse("COST_PLUS_PERCENT", "   ").error).toBe("Enter a number.");
  });

  /**
   * `parseFloat("12abc")` is 12. A seller who typed that did not mean 12, and a
   * bulk reprice is the wrong place to guess at half a number.
   */
  it("refuses a number with something after it", () => {
    expect(parse("COST_PLUS_PERCENT", "12abc").rule).toBeNull();
  });

  it("refuses a multiplier of zero, which would make everything free", () => {
    expect(parse("MULTIPLIER", "0").rule).toBeNull();
  });

  it("refuses a negative markup", () => {
    expect(parse("COST_PLUS_FIXED", "-1").rule).toBeNull();
    expect(parse("COST_PLUS_PERCENT", "-5").rule).toBeNull();
  });

  /**
   * price = cost / (1 - margin/100), so 100% is a division by zero. The bound is
   * arithmetic, not a policy someone picked, and 99.9 has to stay legal.
   */
  it("refuses a 100% margin and allows 99.9", () => {
    expect(parse("TARGET_MARGIN", "100").rule).toBeNull();
    expect(parse("TARGET_MARGIN", "99.9").rule).toEqual({
      type: "TARGET_MARGIN",
      value: 99.9
    });
  });

  it("refuses a multiplier past the server's ceiling", () => {
    expect(parse("MULTIPLIER", "1001").rule).toBeNull();
    expect(parse("MULTIPLIER", "1000").rule).not.toBeNull();
  });

  /** Every refusal says what to do instead. A null rule with no sentence is a dead field. */
  it("never refuses silently", () => {
    (
      [
        ["COST_PLUS_PERCENT", "-1"],
        ["MULTIPLIER", "0"],
        ["TARGET_MARGIN", "100"],
        ["COST_PLUS_FIXED", "-2"],
        ["COST_PLUS_PERCENT", "nope"]
      ] as [StorePricingRuleType, string][]
    ).forEach(([type, value]) => {
      const parsed = parse(type, value);
      expect(parsed.rule).toBeNull();
      expect(parsed.error).toMatch(/\S/);
    });
  });
});

describe("what the seller reads back", () => {
  /**
   * The summary is built from the parsed rule, so the fixed-amount case divides
   * the cents back out. That is deliberate: a bug in the conversion shows up in
   * the sentence the seller is looking at, rather than hiding behind the string
   * they typed.
   */
  it("renders the fixed amount back as money", () => {
    const parsed = parse("COST_PLUS_FIXED", "5");
    expect(parsed.rule && pricingRuleSummary(parsed.rule)).toBe("Cost + $5.00");
  });

  it("names each rule distinctly", () => {
    expect(pricingRuleSummary({ type: "COST_PLUS_PERCENT", value: 20 })).toBe("Cost + 20%");
    expect(pricingRuleSummary({ type: "MULTIPLIER", value: 2 })).toBe("Cost × 2");
    expect(pricingRuleSummary({ type: "TARGET_MARGIN", value: 40 })).toBe("40% margin");
  });

  /**
   * "Cost + 50%" and "50% margin" are different prices — $15 and $20 on a $10
   * item — so the two must never read alike. A seller who picks the wrong one
   * underprices their whole store by a third.
   */
  it("does not let cost-plus and target-margin read as synonyms", () => {
    expect(pricingRuleSummary({ type: "COST_PLUS_PERCENT", value: 50 })).not.toBe(
      pricingRuleSummary({ type: "TARGET_MARGIN", value: 50 })
    );
    expect(pricingRuleHint("COST_PLUS_PERCENT")).not.toBe(pricingRuleHint("TARGET_MARGIN"));
  });

  it("gives every offered rule a label, a hint and a unit", () => {
    PRICING_RULE_TYPES.forEach((type) => {
      expect(pricingRuleLabel(type)).toMatch(/\S/);
      expect(pricingRuleHint(type)).toMatch(/\S/);
      expect(pricingRuleUnit(type)).toMatch(/\S/);
    });
  });
});

describe("what is on offer", () => {
  /**
   * `MANUAL_PRICE` is the absence of a rule. Offering it would put a button in
   * front of the seller that runs a batch the engine proposes nothing for, so
   * every row comes back `UNKNOWN_COST` — a screen of failures for a choice that
   * cannot succeed.
   */
  it("does not offer manual pricing as a bulk rule", () => {
    expect(PRICING_RULE_TYPES).not.toContain("MANUAL_PRICE");
  });

  it("offers no rule twice", () => {
    expect(new Set(PRICING_RULE_TYPES).size).toBe(PRICING_RULE_TYPES.length);
  });

  /** The field starts empty, so nothing can be applied by tapping through. */
  it("starts with no number in it", () => {
    expect(EMPTY_PRICING_DRAFT.value).toBe("");
    expect(parsePricingRule(EMPTY_PRICING_DRAFT).rule).toBeNull();
  });

  it("starts on a rule it actually offers", () => {
    expect(PRICING_RULE_TYPES).toContain(EMPTY_PRICING_DRAFT.type);
  });
});
