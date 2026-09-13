/**
 * The docked bar that turns a selection into an action — §21, §33, §34.
 *
 * `StoreSelectionBar` sits under the tabs and answers "what have I picked".
 * This one sits at the bottom of the screen and answers "what will happen to
 * it", which is a different question and deserves the thumb-reachable half of
 * the screen:
 *
 *   [ Publish | Hide ]
 *   [        Publish 14 · 4 blocked        ]
 *
 * Three decisions:
 *
 * 1. **The count is on the button.** §34 asks for the shape of the outcome
 *    before the seller commits, and the button is the last thing they read. A
 *    button reading "Publish" that publishes fourteen of eighteen is the exact
 *    failure; "Publish 14 · 4 blocked" is the same tap with the answer already
 *    on it. The label is built by `bulkActionLabel` from the same partition the
 *    row washes use, so the number here and the greyed rows above cannot
 *    disagree.
 *
 * 2. **The action switch is visible, not a menu.** Publish and Hide do opposite
 *    things, and which one is armed changes which rows are greyed out. Hiding
 *    that behind a sheet means the seller reads the greying before they can see
 *    what it is greying *for*.
 *
 * 3. **Zero eligible disables the button, and says why.** "Nothing to publish"
 *    with fourteen rows selected is information — every one of them is blocked —
 *    where a greyed button with no label is a dead end.
 */

import { ActivityIndicator, Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";
import type { StoreBulkAction } from "../../marketplace/storeSelection";

const ACTION_LABEL: Record<StoreBulkAction, string> = {
  publish: "Publish",
  hide: "Hide"
};

const ACTIONS: StoreBulkAction[] = ["publish", "hide"];

export type StoreBulkBarProps = {
  action: StoreBulkAction;
  onChangeAction: (action: StoreBulkAction) => void;
  /** From `bulkActionLabel`, e.g. "Publish 14 · 4 blocked". Already a sentence. */
  ctaLabel: string;
  /** How many rows the action will actually touch. Zero disables the CTA. */
  eligibleCount: number;
  onPress: () => void;
  /** True while a batch is in flight — the CTA is disabled and spins. */
  busy: boolean;
  reducedMotion: boolean;
};

export function StoreBulkBar({
  action,
  onChangeAction,
  ctaLabel,
  eligibleCount,
  onPress,
  busy,
  reducedMotion
}: StoreBulkBarProps) {
  const press = useStorePress(reducedMotion, 0.98);
  const disabled = busy || eligibleCount === 0;

  return (
    <View style={styles.bar}>
      <View style={styles.switcher} accessibilityRole="radiogroup">
        {ACTIONS.map((candidate) => {
          const on = candidate === action;
          return (
            <Pressable
              key={candidate}
              style={[styles.switchItem, on ? styles.switchItemOn : null]}
              onPress={() => onChangeAction(candidate)}
              disabled={busy}
              accessibilityRole="radio"
              accessibilityState={{ selected: on, disabled: busy }}
              accessibilityLabel={ACTION_LABEL[candidate]}
              accessibilityHint="Changes which listings the bulk action can touch"
            >
              <Text style={[styles.switchText, on ? styles.switchTextOn : null]}>
                {ACTION_LABEL[candidate]}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <Animated.View style={press.style}>
        <Pressable
          style={[styles.cta, disabled ? styles.ctaOff : null]}
          onPress={onPress}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          disabled={disabled}
          accessibilityRole="button"
          // The label is the count sentence, so a screen-reader user hears the
          // same thing a sighted one reads: how many, and how many will not.
          accessibilityLabel={ctaLabel}
          accessibilityState={{ disabled }}
        >
          {busy ? (
            <ActivityIndicator size="small" color={storeLight.cta.text} />
          ) : (
            <Text style={[styles.ctaText, disabled ? styles.ctaTextOff : null]} numberOfLines={1}>
              {ctaLabel}
            </Text>
          )}
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
    paddingTop: 10,
    paddingBottom: 10,
    backgroundColor: storeLight.bg.card,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: storeLight.border.hairline
  },
  switcher: {
    flexDirection: "row",
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    overflow: "hidden"
  },
  switchItem: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: storeLight.bg.card
  },
  switchItemOn: { backgroundColor: storeLight.select.selected },
  switchText: { fontSize: 13, fontWeight: "600", color: storeLight.text.muted },
  switchTextOn: { color: storeLight.text.primary, fontWeight: "800" },
  cta: {
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 18,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.to,
    alignItems: "center",
    justifyContent: "center"
  },
  ctaOff: { backgroundColor: storeLight.bg.skeleton },
  ctaText: { fontSize: 14, fontWeight: "800", color: storeLight.cta.text },
  ctaTextOff: { color: storeLight.text.muted }
});
