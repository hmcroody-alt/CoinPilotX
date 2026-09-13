/**
 * The bulk pricing rule, from what the seller types to what the server is sent
 * — §11, §33, §34.
 *
 * This module turns one text field and one chosen rule type into a
 * {@link MarketplacePricingRule}, and refuses to produce one when it cannot. It
 * computes **no prices**. Not a preview, not an estimate, not a "roughly
 * $12.00" under the field. Every price the seller sees comes back from
 * `previewMarketplaceSellerBatch`, from the same code that will write it — see
 * `_marketplace_batch_price_outcome` in `bot.py`, which the dry run and the
 * commit both call. A second implementation here would be a second answer, and
 * the arrow "$49.00 → $12.00" is only trustworthy because there is one.
 *
 * Two things it does own, because both are about the seller's keyboard rather
 * than about pricing:
 *
 * **1. Dollars in, cents out.** `COST_PLUS_FIXED` is specified in *minor units*
 * (`services/business_os/suppliers/pricing.py`: "retail = cost + fixed markup,
 * in minor units"). A seller typing `5` into "add a fixed amount" means five
 * dollars. Sending `5` sends five cents, and the batch would come back
 * `PRICE_UNCHANGED` on nearly every row — a silent, plausible, completely wrong
 * result. {@link parsePricingRule} multiplies by 100 for that one rule and no
 * other, and there is a test pinning it.
 *
 * **2. Refusing early.** The ranges below mirror `normalize_rule`, so a
 * fat-fingered `1000%` margin is answered by the field rather than by a 400
 * three taps later. This is a courtesy, not a gate: the server validates
 * independently and is the one that decides. If the two ever disagree the
 * server wins, and the cost of that is an error message, not a bad write.
 */

import type { MarketplacePricingRule } from "../api/marketplace";

export type StorePricingRuleType = MarketplacePricingRule["type"];

/**
 * The rules offered, in the order a seller is likely to want them.
 *
 * `MANUAL_PRICE` is not here and cannot be: it is the *absence* of a rule, and
 * "apply no rule to 14 products" is not an action. The engine proposes nothing
 * for it, so a bulk run under manual pricing would return `UNKNOWN_COST` on
 * every row. Typing prices by hand is what the single-product editor is for.
 */
export const PRICING_RULE_TYPES: StorePricingRuleType[] = [
  "COST_PLUS_PERCENT",
  "MULTIPLIER",
  "TARGET_MARGIN",
  "COST_PLUS_FIXED"
];

/** What the seller has typed so far. A string, because a half-typed number is not one. */
export type StorePricingRuleDraft = {
  type: StorePricingRuleType;
  value: string;
};

export const EMPTY_PRICING_DRAFT: StorePricingRuleDraft = {
  type: "COST_PLUS_PERCENT",
  value: ""
};

/** The short name on the rule chip. */
export function pricingRuleLabel(type: StorePricingRuleType): string {
  switch (type) {
    case "COST_PLUS_PERCENT":
      return "Cost + %";
    case "MULTIPLIER":
      return "Multiply cost";
    case "TARGET_MARGIN":
      return "Target margin";
    case "COST_PLUS_FIXED":
      return "Cost + amount";
  }
}

/**
 * What the rule does, in one sentence, above the field.
 *
 * Worth the words: "target margin" and "cost + percent" sound like synonyms and
 * are not. Cost + 50% on a $10 item is $15 and a 33% margin; a 50% target
 * margin on the same item is $20. A seller who picks the wrong one of those
 * prices their whole store a third under.
 */
export function pricingRuleHint(type: StorePricingRuleType): string {
  switch (type) {
    case "COST_PLUS_PERCENT":
      return "Add a percentage of what the product costs you.";
    case "MULTIPLIER":
      return "Multiply what the product costs you.";
    case "TARGET_MARGIN":
      return "Price so this much of the sale is profit.";
    case "COST_PLUS_FIXED":
      return "Add the same amount of money to every product.";
  }
}

/** The unit shown inside the field, so the number is never bare. */
export function pricingRuleUnit(type: StorePricingRuleType): string {
  switch (type) {
    case "COST_PLUS_PERCENT":
    case "TARGET_MARGIN":
      return "%";
    case "MULTIPLIER":
      return "×";
    case "COST_PLUS_FIXED":
      return "$";
  }
}

export type StorePricingRuleParse =
  | { rule: MarketplacePricingRule; error: null }
  | { rule: null; error: string };

/**
 * The typed draft as a rule the server will accept, or the reason it will not.
 *
 * Returns an error rather than throwing, and returns it for an *empty* field
 * too, because the caller's job is to keep the Preview button off until this
 * yields a rule. There is no "default" value: a bulk reprice with no number in
 * it is not a thing a seller can have meant.
 */
export function parsePricingRule(draft: StorePricingRuleDraft): StorePricingRuleParse {
  const raw = draft.value.trim();
  if (!raw) return { rule: null, error: "Enter a number." };

  // `Number` rather than `parseFloat`: parseFloat("12abc") is 12, and a seller
  // who typed "12abc" did not mean 12.
  const value = Number(raw);
  if (!Number.isFinite(value)) return { rule: null, error: "That isn't a number." };

  switch (draft.type) {
    case "COST_PLUS_PERCENT":
      if (value < 0 || value > 100000) {
        return { rule: null, error: "Use a percentage between 0 and 100,000." };
      }
      return { rule: { type: "COST_PLUS_PERCENT", value }, error: null };

    case "MULTIPLIER":
      if (value <= 0 || value > 1000) {
        return { rule: null, error: "Use a multiplier above 0 and up to 1000." };
      }
      return { rule: { type: "MULTIPLIER", value }, error: null };

    case "TARGET_MARGIN":
      // 100 is excluded, and not as a rounding-off of the range: price =
      // cost / (1 - margin/100), so a 100% margin is a division by zero. There
      // is no finite price at which all of a nonzero cost is profit.
      if (value < 0 || value >= 100) {
        return { rule: null, error: "Use a margin between 0 and 99.9." };
      }
      return { rule: { type: "TARGET_MARGIN", value }, error: null };

    case "COST_PLUS_FIXED": {
      if (value < 0 || value > 10000000) {
        return { rule: null, error: "Use an amount between $0 and $10,000,000." };
      }
      // The dollars-to-cents conversion this module exists for. `Math.round`
      // rather than a truncation, so $1.005 is a cent rather than nothing.
      return { rule: { type: "COST_PLUS_FIXED", value: Math.round(value * 100) }, error: null };
    }
  }
}

/**
 * The rule as one phrase — "Cost + 20%" — for the review face and the button.
 *
 * Takes the parsed rule rather than the draft, so what the seller reads back is
 * built from the numbers actually being sent. In particular the fixed-amount
 * rule is rendered by dividing its cents back out, which means a bug in the
 * conversion above shows up in the summary the seller is looking at instead of
 * hiding behind the string they typed.
 */
export function pricingRuleSummary(rule: MarketplacePricingRule): string {
  switch (rule.type) {
    case "COST_PLUS_PERCENT":
      return `Cost + ${rule.value}%`;
    case "MULTIPLIER":
      return `Cost × ${rule.value}`;
    case "TARGET_MARGIN":
      return `${rule.value}% margin`;
    case "COST_PLUS_FIXED":
      return `Cost + $${(rule.value / 100).toFixed(2)}`;
  }
}
