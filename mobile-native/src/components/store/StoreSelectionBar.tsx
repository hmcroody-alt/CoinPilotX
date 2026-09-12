/**
 * The bar that replaces the tab bar while the seller is picking rows — §16–§20,
 * and the launch point for §21's action sheet.
 *
 * It carries exactly three things, and the order is the argument:
 *
 *   [ Select all 6 shown ]   4 selected · 2 not shown   [ Done ]
 *
 * 1. **What Select All will take**, stated in the label rather than left to the
 *    word "All". A seller who has filtered to six rows and taps a control
 *    reading "Select all" has no way to know whether they just took six or two
 *    hundred; "Select all 6 shown" removes the question.
 * 2. **What is currently selected**, including the part that is not on screen.
 *    A selection deliberately survives a filter change, so the count is the only
 *    thing standing between a seller and a bulk action on rows they are not
 *    looking at. When the two numbers differ the bar says so.
 * 3. **The way out.** Selection mode hides the Edit buttons and repurposes every
 *    row tap, so leaving must be one tap and must never be hidden behind a
 *    gesture.
 *
 * All three strings are computed by `marketplace/storeSelection` and passed in
 * already built. This component owns no selection logic at all — it cannot
 * decide that a row is selected, only draw that it is — which is what keeps the
 * dangerous half testable without rendering anything.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";
import type { SelectAllState } from "../../marketplace/storeSelection";

/**
 * The tri-state mark for Select All.
 *
 * "some" draws a dash, not a tick. A checkbox showing *checked* when four of
 * twelve rows are picked is a lie told in one glyph, and it is the state a
 * seller is most likely to act on without re-reading the count beside it.
 */
function SelectAllMark({ state }: { state: SelectAllState }) {
  return (
    <View style={[styles.mark, state === "none" ? null : styles.markOn]}>
      {state === "all" ? <Text style={styles.markGlyph}>✓</Text> : null}
      {state === "some" ? <View style={styles.markDash} /> : null}
    </View>
  );
}

export type StoreSelectionBarProps = {
  /** From `selectAllState` — drives the tick / dash / empty box. */
  selectAllState: SelectAllState;
  /** From `selectAllLabel`, e.g. "Select all 6 shown". Already a sentence. */
  selectAllLabel: string;
  onToggleAll: () => void;
  /**
   * From `selectionSummary`, e.g. "4 selected · 2 not shown".
   *
   * `null` for an empty selection, which renders a prompt instead of "0
   * selected" — a zero here reads as a broken counter rather than as an
   * invitation.
   */
  summary: string | null;
  onDone: () => void;
  reducedMotion: boolean;
};

export function StoreSelectionBar({
  selectAllState,
  selectAllLabel,
  onToggleAll,
  summary,
  onDone,
  reducedMotion
}: StoreSelectionBarProps) {
  const allPress = useStorePress(reducedMotion, 0.97);
  const donePress = useStorePress(reducedMotion, 0.96);

  return (
    <View style={styles.bar}>
      <Animated.View style={allPress.style}>
        <Pressable
          style={styles.selectAll}
          onPress={onToggleAll}
          onPressIn={allPress.onPressIn}
          onPressOut={allPress.onPressOut}
          hitSlop={6}
          accessibilityRole="checkbox"
          accessibilityState={{ checked: selectAllState === "all" }}
          // The label already says how many and of what, so it is the whole
          // announcement. "Select all, checkbox, partially checked" would tell a
          // screen-reader user the state and not the scope, which is backwards:
          // the scope is the part that can surprise them.
          accessibilityLabel={selectAllLabel}
        >
          <SelectAllMark state={selectAllState} />
          <Text style={styles.selectAllText} numberOfLines={1}>
            {selectAllLabel}
          </Text>
        </Pressable>
      </Animated.View>

      <Text
        style={[styles.summary, summary ? null : styles.summaryEmpty]}
        numberOfLines={1}
        // Announced as it changes, because in selection mode the count is the
        // only feedback a row tap produces — the rows themselves are silent.
        accessibilityLiveRegion="polite"
      >
        {summary ?? "Tap listings to select"}
      </Text>

      <Animated.View style={donePress.style}>
        <Pressable
          style={styles.done}
          onPress={onDone}
          onPressIn={donePress.onPressIn}
          onPressOut={donePress.onPressOut}
          accessibilityRole="button"
          accessibilityLabel="Done selecting"
          accessibilityHint="Leaves selection mode and clears the selection"
        >
          <Text style={styles.doneText}>Done</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: "row",
    alignItems: "center",
    gap: storeLight.space.gutter,
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 8,
    backgroundColor: storeLight.select.selected,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.select.selectedBorder,
    minHeight: storeLight.size.tapTarget
  },
  selectAll: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: storeLight.size.tapTarget,
    paddingRight: 4
  },
  selectAllText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  mark: {
    width: 20,
    height: 20,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  markOn: {
    borderColor: storeLight.select.selectedBorder,
    backgroundColor: storeLight.select.selectedBorder
  },
  markGlyph: { color: storeLight.text.onDark, fontSize: 13, fontWeight: "900", lineHeight: 15 },
  markDash: { width: 10, height: 2, borderRadius: 1, backgroundColor: storeLight.text.onDark },
  summary: { flex: 1, fontSize: 12, color: storeLight.text.primary, textAlign: "right" },
  summaryEmpty: { color: storeLight.text.muted, fontStyle: "italic" },
  done: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.select.selectedBorder,
    backgroundColor: storeLight.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  doneText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary }
});
