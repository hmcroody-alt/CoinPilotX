/**
 * The face where a seller re-files a selection — §33, §34.
 *
 * Structurally `StoreBulkPriceRule`: suggestions, a field, and a button that
 * previews rather than applies. The button says "Preview changes" for the same
 * reason it does there — this face cannot write, and the only path from here to a
 * changed category runs through a review listing every row and every new filing.
 *
 * Two things it says that a price face does not have to:
 *
 * **Moving clears the subcategory.** Said in plain words above the optional
 * field, because it is the one consequence a seller would not predict and cannot
 * undo in bulk. "Education / Crypto Basics" moved to "Home & Kitchen" keeps no
 * child, and a seller who wanted one types it here.
 *
 * **How many are already there.** A local count, labelled as a preview of the
 * blocks rather than as a verdict, because re-filing a selection that is
 * partly-already-filed is the normal case and a screenful of "Already in that
 * category" on the next face reads as a malfunction.
 */

import { useMemo } from "react";
import { ActivityIndicator, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import {
  CATEGORY_MAX,
  alreadyThere,
  parseCategoryTarget,
  type StoreCategoryDraft
} from "../../marketplace/storeBulkCategory";

export type StoreBulkCategoryPickerProps = {
  draft: StoreCategoryDraft;
  onChange: (draft: StoreCategoryDraft) => void;
  /** Aisles this store already uses, most-used first. Suggestions, not a list to pick from. */
  suggestions: string[];
  /** The selected rows' current filing, for the "already there" count. */
  selectedFilings: { category: string; subcategory: string }[];
  busy: boolean;
  onPreview: () => void;
};

export function StoreBulkCategoryPicker({
  draft,
  onChange,
  suggestions,
  selectedFilings,
  busy,
  onPreview
}: StoreBulkCategoryPickerProps) {
  const parsed = useMemo(() => parseCategoryTarget(draft), [draft]);
  const selectedCount = selectedFilings.length;
  const ready = Boolean(parsed.target) && selectedCount > 0 && !busy;
  // Only meaningful once there is a target, and only worth saying when it is not
  // the whole selection: "14 of 14 are already there" is a sentence the seller
  // would read as an error, and the empty confirm face says it better.
  const settled = parsed.target ? alreadyThere(selectedFilings, parsed.target) : 0;

  return (
    <>
      <Text style={styles.title} accessibilityRole="header">
        Set category
      </Text>
      <Text style={styles.subtitle}>
        {selectedCount === 1 ? "1 product selected" : `${selectedCount} products selected`}
      </Text>

      <ScrollView style={styles.body} contentContainerStyle={styles.bodyInner}>
        {suggestions.length > 0 ? (
          <>
            <Text style={styles.groupHead}>Already in your store</Text>
            <View style={styles.chips}>
              {suggestions.map((name) => {
                const on = name === draft.category.trim();
                return (
                  <Pressable
                    key={name}
                    style={[styles.chip, on ? styles.chipOn : null]}
                    // Tapping a suggestion clears the subcategory field as well
                    // as filling the parent, because the field's contents belong
                    // to whichever parent was in the box when they were typed.
                    // Leaving them would build the incoherent pair by hand.
                    onPress={() => onChange({ category: name, subcategory: "" })}
                    disabled={busy}
                    accessibilityRole="button"
                    accessibilityLabel={`Move to ${name}`}
                    accessibilityState={{ selected: on, disabled: busy }}
                  >
                    <Text style={[styles.chipText, on ? styles.chipTextOn : null]}>{name}</Text>
                  </Pressable>
                );
              })}
            </View>
          </>
        ) : null}

        <Text style={styles.groupHead}>Category</Text>
        <View style={styles.field}>
          <TextInput
            style={styles.input}
            value={draft.category}
            onChangeText={(category) => onChange({ ...draft, category })}
            editable={!busy}
            maxLength={CATEGORY_MAX}
            autoCapitalize="words"
            placeholder="Home & Kitchen"
            // `text.muted`, not `select.disabledReason`: that token's own
            // docstring scopes it to "the *reason* a row is blocked", and it is a
            // rust amber. An empty field the seller has not touched yet is the
            // opening state of this face, not a fault, and an amber placeholder
            // makes the picker open looking like it is already complaining.
            placeholderTextColor={storeLight.text.muted}
            // "New", where the heading above says only "Category", because the
            // selection arrives here filed under several different aisles and
            // there is no single current one this box could be showing. A field
            // labelled "Category" beside fourteen products reads as "their
            // category" and invites the seller to believe it was pre-filled.
            accessibilityLabel="New category"
          />
        </View>

        <Text style={styles.groupHead}>Subcategory (optional)</Text>
        <View style={styles.field}>
          <TextInput
            style={styles.input}
            value={draft.subcategory}
            onChangeText={(subcategory) => onChange({ ...draft, subcategory })}
            editable={!busy}
            maxLength={CATEGORY_MAX}
            autoCapitalize="words"
            placeholder="Lighting"
            placeholderTextColor={storeLight.text.muted}
            accessibilityLabel="New subcategory, optional"
          />
        </View>
        <Text style={styles.note}>
          Leaving this empty clears any subcategory these products have now.
        </Text>

        {settled > 0 && settled < selectedCount ? (
          <Text style={styles.note} accessibilityLiveRegion="polite">
            {settled === 1
              ? "1 product is already filed there and will stay as it is."
              : `${settled} products are already filed there and will stay as they are.`}
          </Text>
        ) : null}

        {/* The consequence a seller is entitled to know before the tap, not only
            in the per-row preview: a category change is material, so any live
            product in the selection goes back to the review queue and off sale
            until a moderator clears it. */}
        <Text style={styles.noteWarn}>
          Products that are live will go back to review after moving.
        </Text>
      </ScrollView>

      <Pressable
        style={[styles.primary, ready ? null : styles.primaryOff]}
        onPress={onPreview}
        disabled={!ready}
        accessibilityRole="button"
        accessibilityLabel="Preview changes"
        accessibilityHint="Shows the new category for every selected product before anything is saved"
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
  body: { marginTop: 12, maxHeight: 320 },
  bodyInner: { paddingBottom: 4 },
  groupHead: {
    fontSize: 12,
    fontWeight: "800",
    color: storeLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4,
    marginTop: 14,
    marginBottom: 8
  },
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
  field: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  input: { flex: 1, fontSize: 16, fontWeight: "700", color: storeLight.text.primary, paddingVertical: 8 },
  // Two weights, because these lines are not all the same kind of sentence.
  // "Leaving this empty clears the subcategory" and "3 are already filed there"
  // are the field telling the seller how it behaves — gray. "Live products go
  // back to review" is the one that costs them a storefront — amber, the same
  // amber `subtitleWarn`/`lineWarning` use in `StoreBulkSheet`, so a warning
  // looks the same everywhere in this flow. Painting all three amber (which is
  // what `select.disabledReason` did here) spends the warning colour on hints
  // and leaves the real hazard indistinguishable from them.
  note: { fontSize: 12, color: storeLight.text.muted, marginTop: 12 },
  noteWarn: { fontSize: 12, fontWeight: "700", color: storeLight.status.warning, marginTop: 12 },
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
