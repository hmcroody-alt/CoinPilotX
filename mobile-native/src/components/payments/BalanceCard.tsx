/**
 * A secondary balance card: Processing, Held in escrow, or Ad wallet.
 *
 * Same contract as the hero — the figure arrives finished, `null` means the
 * read failed and draws an em dash, and the number never animates after it
 * arrives. The card itself fades and lifts in on the cascade; the figure inside
 * it does not move independently.
 *
 * The `accent` prop tints the label and the optional indicator, never the
 * amount. That asymmetry is deliberate: the amount is a fact and takes the ink
 * colour, while the accent is a category marker. If the figure itself were
 * violet, a held balance would look like a different *kind* of money rather
 * than the same money in a different state.
 *
 * `indicator` is the breathing dot the escrow card uses. It is available to any
 * card, but the caller must pass an animated value — this component will not
 * start a loop on its own, because a card that animates whenever it is rendered
 * would eventually animate for a reason nobody chose.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";

export type BalanceCardProps = {
  label: string;
  /** Pre-formatted; `formatMoney` renders a null figure as "—". */
  formattedAmount: string;
  /** True when the underlying figure could not be read. Mutes the amount. */
  unavailable?: boolean;
  /** A short clarifying line, e.g. "Clears after the buyer confirms". */
  caption?: string;
  accent?: string;
  /** Violet-tinted surface for the escrow card. */
  tinted?: boolean;
  /** A 0 → 1 driver from `usePaymentsEscrowIndicator`, if this card has a dot. */
  indicator?: Animated.Value | null;
  /** A 0 → 1 driver from the balance cascade. */
  entrance?: Animated.Value | null;
  /**
   * Spoken instead of the assembled default. The escrow card overrides this so
   * a hold is announced as "still yours", which is the fact a screen-reader
   * user most needs and the one the violet colour alone cannot convey.
   */
  accessibilityLabel?: string;
  onPress?: () => void;
  hint?: string;
};

export function BalanceCard({
  label,
  formattedAmount,
  unavailable = false,
  caption,
  accent = paymentsLight.balance.processingAccent,
  tinted = false,
  indicator = null,
  entrance = null,
  accessibilityLabel,
  onPress,
  hint
}: BalanceCardProps) {
  const enter = entrance
    ? {
        opacity: entrance,
        transform: [
          { translateY: entrance.interpolate({ inputRange: [0, 1], outputRange: [8, 0] }) }
        ]
      }
    : undefined;

  const announcement =
    accessibilityLabel ||
    [label, unavailable ? "Unavailable" : formattedAmount, caption].filter(Boolean).join(", ");

  const inner = (
    <View style={[styles.card, tinted && styles.cardTinted]}>
      <View style={styles.labelRow}>
        {indicator ? (
          <Animated.View
            style={[
              styles.indicator,
              { backgroundColor: accent },
              {
                opacity: indicator.interpolate({ inputRange: [0, 1], outputRange: [0.45, 1] })
              }
            ]}
          />
        ) : null}
        <Text style={[styles.label, { color: accent }]} allowFontScaling numberOfLines={2}>
          {label}
        </Text>
      </View>

      <Text
        style={[styles.amount, unavailable && styles.amountUnavailable]}
        allowFontScaling
        adjustsFontSizeToFit
        numberOfLines={1}
      >
        {formattedAmount}
      </Text>

      {caption ? (
        <Text style={styles.caption} allowFontScaling numberOfLines={3}>
          {caption}
        </Text>
      ) : null}
    </View>
  );

  const content = enter ? <Animated.View style={enter}>{inner}</Animated.View> : inner;

  if (!onPress) {
    return (
      <View style={styles.slot} accessible accessibilityLabel={announcement}>
        {content}
      </View>
    );
  }

  return (
    <Pressable
      style={styles.slot}
      onPress={onPress}
      accessible
      accessibilityRole="button"
      accessibilityLabel={announcement}
      accessibilityHint={hint}
    >
      {content}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  slot: {
    flex: 1,
    minWidth: 150
  },
  card: {
    minHeight: 92,
    padding: paymentsLight.space.card,
    borderRadius: paymentsLight.radius.card,
    backgroundColor: paymentsLight.bg.card,
    borderWidth: 1,
    borderColor: paymentsLight.border.hairline
  },
  cardTinted: {
    backgroundColor: paymentsLight.bg.escrowCard,
    borderColor: paymentsLight.border.escrowCard
  },
  labelRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6
  },
  indicator: {
    width: 7,
    height: 7,
    borderRadius: 4
  },
  label: {
    flex: 1,
    fontSize: 12,
    fontWeight: "700",
    letterSpacing: 0.2
  },
  amount: {
    marginTop: 8,
    color: paymentsLight.balance.value,
    fontSize: paymentsLight.money.balance.fontSize,
    fontWeight: paymentsLight.money.balance.fontWeight,
    fontVariant: ["tabular-nums"]
  },
  amountUnavailable: {
    color: paymentsLight.text.muted
  },
  caption: {
    marginTop: 5,
    color: paymentsLight.text.muted,
    fontSize: 11,
    lineHeight: 15
  }
});
