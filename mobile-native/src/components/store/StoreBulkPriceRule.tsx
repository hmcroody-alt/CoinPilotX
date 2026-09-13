/**
 * The one screen where a seller reprices their whole store — §11, §33.
 *
 * Four chips, one number, and a button that does not apply anything. The button
 * says "Preview changes" because that is what it does: this face cannot write,
 * and the only path from here to a changed price runs through a review listing
 * every row and every new figure. §34, expressed as the absence of an Apply
 * button rather than as a confirmation dialog in front of one.
 *
 * Nothing here multiplies a cost by anything. There is no "≈ $12.00" hint under
 * the field, and its absence is deliberate: a local estimate is a second pricing
 * implementation, it would drift from the server's rounding, and it would be
 * wrong in the one case that matters most — a product whose supplier cost is
 * unknown, where the honest answer is not a number at all. The seller sees
 * prices on the next face, computed by the code that will store them.
 */

import { useMemo } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import {
  PRICING_RULE_TYPES,
  parsePricingRule,
  pricingRuleHint,
  pricingRuleLabel,
  pricingRuleUnit,
  type StorePricingRuleDraft,
  type StorePricingRuleType
} from "../../marketplace/storeBulkPricing";

export type StoreBulkPriceRuleProps = {
  draft: StorePricingRuleDraft;
  onChange: (draft: StorePricingRuleDraft) => void;
  /** How many rows the rule would be previewed against. */
  selectedCount: number;
  /** True while the dry run is in flight. */
  busy: boolean;
  onPreview: () => void;
};

export function StoreBulkPriceRule({
  draft,
  onChange,
  selectedCount,
  busy,
  onPreview
}: StoreBulkPriceRuleProps) {
  const parsed = useMemo(() => parsePricingRule(draft), [draft]);
  // An empty field is not an error the seller has made yet, so the message is
  // held back until they have typed something wrong. The button is off either
  // way — "no number" and "a bad number" are both "not ready" — but only one of
  // them deserves red text under a field the seller has not reached.
  const showError = Boolean(parsed.error) && draft.value.trim().length > 0;
  const ready = Boolean(parsed.rule) && selectedCount > 0 && !busy;

  return (
    <>
      <Text style={styles.title} accessibilityRole="header">
        Edit pricing
      </Text>
      <Text style={styles.subtitle}>
        {selectedCount === 1 ? "1 product selected" : `${selectedCount} products selected`}
      </Text>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyInner}>
        <View style={styles.chips} accessibilityRole="radiogroup">
          {PRICING_RULE_TYPES.map((type: StorePricingRuleType) => {
            const on = type === draft.type;
            return (
              <Pressable
                key={type}
                style={[styles.chip, on ? styles.chipOn : null]}
                // Changing the rule type keeps the number. A seller comparing
                // "cost + 50%" against "50% margin" is changing exactly one
                // thing, and clearing the field would make them retype it to
                // find out the answer.
                onPress={() => onChange({ ...draft, type })}
                disabled={busy}
                accessibilityRole="radio"
                accessibilityState={{ selected: on, disabled: busy }}
                accessibilityLabel={pricingRuleLabel(type)}
                accessibilityHint={pricingRuleHint(type)}
              >
                <Text style={[styles.chipText, on ? styles.chipTextOn : null]}>
                  {pricingRuleLabel(type)}
                </Text>
              </Pressable>
            );
          })}
        </View>

        <Text style={styles.hint}>{pricingRuleHint(draft.type)}</Text>

        <View style={[styles.field, showError ? styles.fieldBad : null]}>
          <Text style={styles.unit}>{pricingRuleUnit(draft.type)}</Text>
          <TextInput
            style={styles.input}
            value={draft.value}
            onChangeText={(value) => onChange({ ...draft, value })}
            editable={!busy}
            keyboardType="decimal-pad"
            placeholder="0"
            placeholderTextColor={storeLight.select.disabledReason}
            accessibilityLabel={`${pricingRuleLabel(draft.type)} value`}
            accessibilityHint={pricingRuleHint(draft.type)}
          />
        </View>
        {showError ? (
          <Text style={styles.error} accessibilityLiveRegion="polite">
            {parsed.error}
          </Text>
        ) : null}

        {/* The one thing a rule cannot do, said before the seller finds out from
            a screen of UNKNOWN COST rows. Cost-based rules need a supplier cost,
            which a hand-written listing has never had. */}
        <Text style={styles.note}>
          Products without a supplier cost can't be priced by a rule. They'll be listed as
          unchanged.
        </Text>
      </ScrollView>

      <Pressable
        style={[styles.primary, ready ? null : styles.primaryOff]}
        onPress={onPreview}
        disabled={!ready}
        accessibilityRole="button"
        accessibilityLabel="Preview changes"
        accessibilityHint="Shows the new price for every selected product before anything is saved"
        accessibilityState={{ disabled: !ready }}
      >
        {busy ? (
          <ActivityIndicator size="small" color={storeLight.cta.text} />
        ) : (
          <Text style={[styles.primaryText, ready ? null : styles.primaryTextOff]}>
            Preview changes
          </Text>
        )}
      </Pressable>
    </>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: 18, fontWeight: "800", color: storeLight.text.primary },
  subtitle: { fontSize: 13, color: storeLight.text.muted, marginTop: 4 },
  body: { marginTop: 12, maxHeight: 300 },
  bodyInner: { paddingBottom: 4 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 14,
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  chipOn: { backgroundColor: storeLight.select.selected, borderColor: storeLight.status.success },
  chipText: { fontSize: 13, fontWeight: "600", color: storeLight.text.muted },
  chipTextOn: { color: storeLight.text.primary, fontWeight: "800" },
  hint: { fontSize: 13, color: storeLight.text.primary, marginTop: 14 },
  field: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 8,
    paddingHorizontal: 14,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  fieldBad: { borderColor: storeLight.status.error },
  unit: { fontSize: 16, fontWeight: "800", color: storeLight.text.muted },
  input: { flex: 1, fontSize: 16, fontWeight: "700", color: storeLight.text.primary, paddingVertical: 8 },
  error: { fontSize: 12, fontWeight: "600", color: storeLight.status.error, marginTop: 6 },
  note: { fontSize: 12, color: storeLight.select.disabledReason, marginTop: 14 },
  primary: {
    marginTop: 14,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.to,
    alignItems: "center",
    justifyContent: "center"
  },
  primaryOff: { backgroundColor: storeLight.bg.skeleton },
  primaryText: { fontSize: 15, fontWeight: "800", color: storeLight.cta.text },
  primaryTextOff: { color: storeLight.text.muted }
});
