/**
 * The store setup checklist.
 *
 * The status strip says where the store stands in five words. This says why,
 * and it is the only place on the screen that offers a way to change it.
 *
 * Three rules, each of them a reaction to something the screen it replaces got
 * wrong:
 *
 * * **A step with nothing to press carries no button.** `storeReadiness` returns
 *   `action: null` for a completed step and for the review step, where waiting
 *   is the only move. A row that rendered a button anyway would be the dead
 *   control this tier exists to remove.
 * * **State is carried by a shape and by words, never by colour alone.** The
 *   tick and the ring are different glyphs, not the same glyph in two colours,
 *   and the accessibility label says "Done" or "To do" out loud.
 * * **Nothing here truncates.** `numberOfLines` is deliberately absent from the
 *   label and the detail: at the largest text sizes the card grows and the page
 *   scrolls. "Take a listing out of dra…" is worse than a taller card, because
 *   the seller cannot tell what the step was.
 *
 * The font-scale ceilings exist for the same reason they exist on
 * `StoreQuickLinkTile`: refusing to grow the text at all ignores the OS setting,
 * which is its own accessibility failure, so the text grows to a bounded point
 * and the container grows after that. `StoreSetupChecklist.test.tsx` asserts the
 * structural properties, since Jest cannot measure text.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import type { StoreSetupStep } from "../../api/storeDashboard";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";

/** Headline and step label grow this far, then the card grows instead. */
export const CHECKLIST_LABEL_MAX_FONT_SCALE = 1.6;
/** Detail lines are supporting text, so they give first. */
export const CHECKLIST_DETAIL_MAX_FONT_SCALE = 1.4;

export type StoreSetupChecklistProps = {
  /** One sentence from `storeReadiness().headline`. Never assembled here. */
  headline: string;
  steps: readonly StoreSetupStep[];
  /** How many steps are outstanding, already counted by the derivation. */
  remaining: number;
  onStepAction: (step: StoreSetupStep) => void;
  reducedMotion: boolean;
};

export function StoreSetupChecklist({
  headline,
  steps,
  remaining,
  onStepAction,
  reducedMotion
}: StoreSetupChecklistProps) {
  return (
    <View style={styles.card} accessibilityLiveRegion="polite">
      <Text
        style={styles.headline}
        maxFontSizeMultiplier={CHECKLIST_LABEL_MAX_FONT_SCALE}
        testID="store-setup-headline"
      >
        {headline}
      </Text>
      <Text style={styles.progress} maxFontSizeMultiplier={CHECKLIST_DETAIL_MAX_FONT_SCALE}>
        {remaining === 0
          ? "Every setup step is done."
          : remaining === 1
            ? "1 step left."
            : `${remaining} steps left.`}
      </Text>

      {steps.map((step) => (
        <StoreSetupRow
          key={step.key}
          step={step}
          onAction={() => onStepAction(step)}
          reducedMotion={reducedMotion}
        />
      ))}
    </View>
  );
}

function StoreSetupRow({
  step,
  onAction,
  reducedMotion
}: {
  step: StoreSetupStep;
  onAction: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.97);
  return (
    <View style={styles.row} testID={`store-setup-step-${step.key}`}>
      {/* Two different glyphs, not one glyph in two colours: the state survives
          for anyone who cannot tell the two tones apart. */}
      <Ionicons
        name={step.complete ? "checkmark-circle" : "ellipse-outline"}
        size={18}
        color={step.complete ? storeLight.status.success : storeLight.text.muted}
        accessibilityElementsHidden
        importantForAccessibility="no"
      />
      <View
        style={styles.rowBody}
        accessible
        accessibilityRole="text"
        accessibilityLabel={`${step.complete ? "Done" : "To do"}. ${step.label}. ${step.detail}`}
      >
        <Text
          style={[styles.rowLabel, step.complete && styles.rowLabelDone]}
          maxFontSizeMultiplier={CHECKLIST_LABEL_MAX_FONT_SCALE}
        >
          {step.label}
        </Text>
        <Text style={styles.rowDetail} maxFontSizeMultiplier={CHECKLIST_DETAIL_MAX_FONT_SCALE}>
          {step.detail}
        </Text>
      </View>

      {step.action ? (
        <Animated.View style={press.style}>
          <Pressable
            style={styles.rowAction}
            onPress={onAction}
            onPressIn={press.onPressIn}
            onPressOut={press.onPressOut}
            accessibilityRole="button"
            accessibilityLabel={`${step.action.label}. ${step.label}`}
          >
            <Text
              style={styles.rowActionText}
              maxFontSizeMultiplier={CHECKLIST_DETAIL_MAX_FONT_SCALE}
            >
              {step.action.label}
            </Text>
          </Pressable>
        </Animated.View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginHorizontal: storeLight.space.card,
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline
  },
  headline: { fontSize: 15, fontWeight: "800", color: storeLight.text.primary, lineHeight: 21 },
  progress: { fontSize: 12, fontWeight: "600", color: storeLight.text.muted },
  /**
   * `alignItems: "flex-start"` rather than `center`: once a label wraps to three
   * lines at the largest text size, a centred tick floats in the middle of the
   * paragraph instead of sitting beside the thing it marks.
   */
  row: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  rowBody: { flex: 1, gap: 2 },
  rowLabel: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary, lineHeight: 19 },
  rowLabelDone: { color: storeLight.text.muted },
  rowDetail: { fontSize: 12, color: storeLight.text.muted, lineHeight: 17 },
  rowAction: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  rowActionText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary }
});
