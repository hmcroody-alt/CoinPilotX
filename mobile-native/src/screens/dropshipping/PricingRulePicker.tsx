/**
 * Choose how retail price is derived from supplier cost.
 *
 * ## The merchant picks a rule, never a price
 *
 * This control produces a `PricingRule` — a rule *type* and one number — and
 * nothing else. It cannot produce a retail price, and `importSelected` has no
 * parameter that would carry one. The server applies the rule to the cost it
 * re-fetched itself, so a rule is safe to send in a way a price never is: the
 * worst a tampered rule can do is set an odd markup on the merchant's own
 * products, where a tampered price would let a client dictate economics
 * derived from a cost it was never allowed to assert.
 *
 * ## The preview is arithmetic, not a promise
 *
 * `previewPricing` takes costs and a rule and takes no connection. Dragging a
 * markup generates no supplier traffic, and the numbers shown here are computed
 * by the same server-side implementation that will run at import time — so the
 * preview cannot drift from the outcome by having its own copy of the formula.
 *
 * A preview that fails is a note, not a blocker. The rule still applies at
 * import; only the merchant's advance look at it is missing, and refusing the
 * import over that would be punishing them for a failure that costs nothing.
 *
 * ## Unknown cost previews as unknown
 *
 * Costs arrive here as `(number | null)[]` and the nulls are carried through
 * rather than filtered out, because "we could not read this product's cost" is
 * the single most important thing a pricing preview can tell a merchant. A
 * quote with a null `proposedRetailCents` renders as unknown; it does not
 * render as free.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  previewPricing,
  type PricingQuote,
  type PricingRule,
  type PricingRuleType
} from "../../api/dropshipping";
import { MarginPill, NO_VALUE, costText } from "../../components/dropshipping/DropshippingStates";
import { useFormatters } from "../../i18n/hooks";
import { storeLight } from "../../theme/storeLight";

type Props = {
  rule: PricingRule;
  onChange: (rule: PricingRule) => void;
  /**
   * Costs to preview against — the selected cart items, in minor units, nulls
   * included. Omitted means no preview, which is the honest rendering when
   * there is nothing to price.
   */
  sampleCostCents?: (number | null)[];
  currency?: string | null;
};

type RuleMeta = {
  label: string;
  /** What the number means, said in the merchant's terms rather than the API's. */
  hint: string;
  /** Shown inside the field so the unit is never guessed. */
  unit: string;
  /** Server-side bounds, mirrored so the merchant is told before the request. */
  min: number;
  max: number;
  /** COST_PLUS_FIXED is the only rule whose value is money. */
  money?: boolean;
};

/**
 * Bounds here mirror `suppliers.pricing.normalize_rule`. They are duplicated on
 * purpose: the server is the authority and still rejects out-of-range values,
 * but a merchant who types 150 into a target margin deserves to be told why
 * before they press Import, not after nothing imported.
 */
const RULE_META: Record<PricingRuleType, RuleMeta> = {
  MANUAL_PRICE: {
    label: "I'll set prices",
    hint: "Products import with no price. You set each one before publishing.",
    unit: "",
    min: 0,
    max: 0
  },
  COST_PLUS_FIXED: {
    label: "Cost + amount",
    hint: "Add the same amount to every product's cost.",
    unit: "amount",
    min: 0,
    max: 10_000_000,
    money: true
  },
  COST_PLUS_PERCENT: {
    label: "Cost + %",
    hint: "Add a percentage of the cost on top of the cost.",
    unit: "%",
    min: 0,
    max: 100_000
  },
  MULTIPLIER: {
    label: "Multiply cost",
    hint: "Multiply the cost. 2 means you sell at twice what you pay.",
    unit: "×",
    min: 0.01,
    max: 1000
  },
  TARGET_MARGIN: {
    label: "Target margin",
    hint: "Price so this much of the sale price is margin.",
    unit: "%",
    min: 0,
    max: 99.99
  }
};

const ORDER: PricingRuleType[] = [
  "COST_PLUS_PERCENT",
  "MULTIPLIER",
  "COST_PLUS_FIXED",
  "TARGET_MARGIN",
  "MANUAL_PRICE"
];

/** Sensible starting numbers, so switching rule never lands on an empty field. */
const DEFAULT_VALUE: Record<PricingRuleType, number | undefined> = {
  MANUAL_PRICE: undefined,
  COST_PLUS_FIXED: 1000,
  COST_PLUS_PERCENT: 60,
  MULTIPLIER: 2,
  TARGET_MARGIN: 40
};

/** How many quotes to summarise. More than this is a wall of numbers. */
const PREVIEW_ROWS = 3;
/** Long enough that typing "125" is one request rather than three. */
const PREVIEW_DEBOUNCE_MS = 350;

/**
 * The number the merchant types, in the units the merchant thinks in.
 *
 * `COST_PLUS_FIXED` is stored in minor units because that is what the server
 * takes, but nobody types "1000" meaning ten dollars. The conversion lives in
 * these two functions and nowhere else.
 */
function valueToField(rule: PricingRule): string {
  if (rule.value === undefined || rule.value === null) return "";
  if (RULE_META[rule.type]?.money) return (rule.value / 100).toFixed(2);
  return String(rule.value);
}

function fieldToValue(type: PricingRuleType, raw: string): number | null {
  const cleaned = raw.replace(/,/g, ".").trim();
  if (!cleaned) return null;
  const parsed = Number(cleaned);
  if (!Number.isFinite(parsed) || parsed < 0) return null;
  return RULE_META[type]?.money ? Math.round(parsed * 100) : parsed;
}

export function PricingRulePicker({ rule, onChange, sampleCostCents, currency }: Props) {
  const formatters = useFormatters();
  const meta = RULE_META[rule.type] || RULE_META.MANUAL_PRICE;

  const [field, setField] = useState(() => valueToField(rule));
  const [quotes, setQuotes] = useState<PricingQuote[] | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);

  const select = useCallback(
    (type: PricingRuleType) => {
      if (type === rule.type) return;
      const value = DEFAULT_VALUE[type];
      setField(value === undefined ? "" : RULE_META[type].money ? (value / 100).toFixed(2) : String(value));
      onChange(value === undefined ? { type } : { type, value });
    },
    [onChange, rule.type]
  );

  const edit = useCallback(
    (raw: string) => {
      setField(raw);
      const value = fieldToValue(rule.type, raw);
      // A half-typed number is not a new rule. The last valid value stays in
      // effect so the Import button never silently starts meaning something
      // different because a field is momentarily "1.".
      if (value === null) return;
      onChange({ type: rule.type, value });
    },
    [onChange, rule.type]
  );

  const outOfRange = useMemo(() => {
    if (rule.type === "MANUAL_PRICE" || rule.value === undefined) return null;
    if (rule.value < meta.min) return `Lowest is ${meta.money ? formatters.currency(meta.min / 100) : meta.min}.`;
    if (rule.value > meta.max) return `Highest is ${meta.money ? formatters.currency(meta.max / 100) : meta.max}.`;
    return null;
  }, [formatters, meta, rule.type, rule.value]);

  // Costs are stringified for the dependency so a caller re-deriving the array
  // each render does not re-request on every keystroke elsewhere on the screen.
  const costKey = useMemo(() => JSON.stringify(sampleCostCents || []), [sampleCostCents]);
  const previewId = useRef(0);

  useEffect(() => {
    const costs: (number | null)[] = JSON.parse(costKey);
    if (rule.type === "MANUAL_PRICE" || costs.length === 0 || outOfRange) {
      setQuotes(null);
      setPreviewFailed(false);
      return;
    }
    const ticket = ++previewId.current;
    const timer = setTimeout(() => {
      previewPricing(costs.slice(0, 24), rule)
        .then((result) => {
          if (ticket !== previewId.current) return;
          setQuotes(result.quotes);
          setPreviewFailed(false);
        })
        .catch(() => {
          if (ticket !== previewId.current) return;
          // Cleared rather than left showing the previous rule's numbers, which
          // would be a preview of a rule the merchant is no longer choosing.
          setQuotes(null);
          setPreviewFailed(true);
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [costKey, outOfRange, rule]);

  const unpriced = useMemo(
    () => (quotes || []).filter((quote) => quote.proposedRetailCents === null).length,
    [quotes]
  );

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Pricing</Text>
      <Text style={styles.body}>{meta.hint}</Text>

      <View style={styles.chips}>
        {ORDER.map((type) => {
          const active = type === rule.type;
          return (
            <Pressable
              key={type}
              style={[styles.chip, active ? styles.chipActive : null]}
              onPress={() => select(type)}
              accessibilityRole="button"
              accessibilityState={{ selected: active }}
              accessibilityLabel={RULE_META[type].label}
            >
              <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>
                {RULE_META[type].label}
              </Text>
            </Pressable>
          );
        })}
      </View>

      {rule.type === "MANUAL_PRICE" ? null : (
        <View style={styles.fieldRow}>
          <TextInput
            style={styles.field}
            value={field}
            onChangeText={edit}
            keyboardType="decimal-pad"
            placeholder="0"
            placeholderTextColor={storeLight.text.muted}
            accessibilityLabel={`${meta.label} value`}
          />
          <Text style={styles.unit}>{meta.money ? currency || "USD" : meta.unit}</Text>
        </View>
      )}

      {outOfRange ? <Text style={styles.warning}>{outOfRange}</Text> : null}

      {quotes && quotes.length > 0 ? (
        <View style={styles.preview}>
          <Text style={styles.previewTitle}>What this would price</Text>
          {quotes.slice(0, PREVIEW_ROWS).map((quote, index) => (
            <View key={index} style={styles.previewRow}>
              <Text style={styles.previewCost}>
                {costText(quote.costCents, currency || null, formatters) || `${NO_VALUE} cost unknown`}
              </Text>
              <Text style={styles.previewArrow}>→</Text>
              <Text style={styles.previewPrice}>
                {costText(quote.proposedRetailCents, currency || null, formatters) || NO_VALUE}
              </Text>
              <MarginPill state={quote.marginState} />
            </View>
          ))}
          {/* The count of unpriceable items is stated rather than hidden: these
              are exactly the products that will import with no price, and a
              merchant who does not know that will look for them later. */}
          {unpriced > 0 ? (
            <Text style={styles.previewNote}>
              {formatters.count(unpriced)} of these have no cost we could read, so no price is
              proposed for them. They'll import as drafts with a price for you to set.
            </Text>
          ) : null}
        </View>
      ) : null}

      {previewFailed ? (
        <Text style={styles.previewNote}>
          We couldn't work out a preview just now. Your rule still applies when you import.
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  title: { fontSize: 15, fontWeight: "700", color: storeLight.text.primary },
  body: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: 36,
    justifyContent: "center",
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  chipActive: { backgroundColor: storeLight.cta.from, borderColor: storeLight.cta.from },
  chipText: { fontSize: 12, fontWeight: "600", color: storeLight.text.primary },
  chipTextActive: { color: storeLight.cta.text },
  fieldRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  field: {
    flex: 1,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    color: storeLight.text.primary,
    fontSize: 15,
    fontWeight: "600"
  },
  unit: { fontSize: 13, fontWeight: "700", color: storeLight.text.muted },
  warning: { fontSize: 12, fontWeight: "600", color: storeLight.status.warning },
  preview: { gap: 6, paddingTop: 6, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: storeLight.border.hairline },
  previewTitle: { fontSize: 12, fontWeight: "700", color: storeLight.text.primary },
  previewRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  previewCost: { fontSize: 12, color: storeLight.text.muted },
  previewArrow: { fontSize: 12, color: storeLight.text.muted },
  previewPrice: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  previewNote: { fontSize: 11, color: storeLight.text.muted, lineHeight: 16 }
});
