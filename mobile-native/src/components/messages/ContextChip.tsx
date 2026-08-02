/**
 * The context chip — the one thing that makes this a *commerce* inbox rather than
 * a chat list. Every row that is about a money object (offer, order, pickup,
 * listing question, completed sale) carries exactly one of these, and its colour
 * is continuous with the rest of the Business surface: violet = offers/Marketplace,
 * blue = orders/Store, green = done, gray = neutral question.
 *
 * Two contracts the rest of the app depends on:
 *   • The chip is ALWAYS icon + text, so colour reinforces meaning but is never
 *     the sole signal (a colour-blind seller reads the same fact).
 *   • Tapping the chip deep-links to the OBJECT, not the thread — so the chip is a
 *     separately focusable control with its own label, distinct from the row.
 *
 * The data comes from `ContextChipData` (api/commerceInbox), which the thread
 * view's pinned card will reuse verbatim, so this component takes the resolved
 * shape and renders it — it never resolves anything itself.
 */

import { Pressable, StyleSheet, Text, View, type StyleProp, type ViewStyle } from "react-native";
import { MESSAGES_CHIP_VARIANTS } from "../../theme/messagesLight";
import { ContextChipData } from "../../api/commerceInbox";

export function ContextChip({
  chip,
  onPress,
  style
}: {
  chip: ContextChipData;
  /** Called with the chip when tapped; only wired when the chip has a target. */
  onPress?: (chip: ContextChipData) => void;
  style?: StyleProp<ViewStyle>;
}) {
  const variant = MESSAGES_CHIP_VARIANTS[chip.kind];
  const tappable = Boolean(chip.target) && Boolean(onPress);

  const body = (
    <View style={[styles.chip, { backgroundColor: variant.bg, borderColor: variant.border }, style]}>
      <Text style={styles.icon} accessibilityElementsHidden importantForAccessibility="no">
        {variant.icon}
      </Text>
      <Text style={[styles.line, { color: variant.text }]} numberOfLines={1} ellipsizeMode="tail">
        {chip.line}
      </Text>
    </View>
  );

  if (!tappable) {
    // Non-tappable chip: still a labelled, focusable node for assistive tech, but
    // not a button (its object has no reachable screen).
    return (
      <View accessible accessibilityLabel={chip.a11yLabel} accessibilityRole="text">
        {body}
      </View>
    );
  }

  return (
    <Pressable
      onPress={() => onPress?.(chip)}
      accessibilityRole="button"
      accessibilityLabel={chip.a11yLabel}
      accessibilityHint="Opens the linked item"
      hitSlop={4}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    maxWidth: "100%",
    gap: 5,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3
  },
  icon: { fontSize: 12 },
  line: { flexShrink: 1, fontSize: 12, fontWeight: "600" }
});
