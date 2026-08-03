/**
 * The hero balance — the largest number on the screen and the one a seller
 * opens Payments to see.
 *
 * Three rules govern this component, and each exists because the obvious
 * implementation gets money wrong in a specific way.
 *
 * **It renders; it never computes.** `availableCents` arrives as a finished
 * total from the server. There is no addition here, no fallback that sums
 * something else when the figure is missing. If the balance could not be read,
 * the prop is `null` and an em dash is drawn — see below.
 *
 * **A failed read is a dash, never a zero.** `$0.00` is a measurement: it tells
 * the seller their money is gone. An em dash tells them we do not currently
 * know, which is the true statement, and it comes with a retry. This is the one
 * place on the screen where the distinction is load-bearing enough that the
 * component refuses to accept a defaulted number at all — `availableCents` is
 * `number | null`, so a caller cannot pass `?? 0` without having written the
 * bug in plain sight.
 *
 * **The number holds still.** It slides up once on arrival and then never moves
 * again. No shimmer, no count-up, no ambient pulse. The only animated element
 * near it is the payout dot, which is a sibling, and which runs only while a
 * payout is genuinely in flight.
 *
 * Accessibility: the whole hero is one focusable element with one label, so an
 * AT user hears "Available for payout, $0.00, $240.00 is still processing" as a
 * single coherent sentence rather than three fragments they must assemble. The
 * sub-line is included in that label precisely because it is the part that
 * explains an otherwise alarming zero.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";
import { usePaymentsHeroArrival, usePaymentsPayoutDot } from "../../theme/paymentsMotion";

export type BalanceHeroProps = {
  label?: string;
  /** A finished server total, or null when the read failed. Never defaulted. */
  availableCents: number | null;
  /** Pre-formatted by the caller via `formatMoney`, which renders null as "—". */
  formattedAmount: string;
  /** The one honest sentence about where the money is. */
  subline: string;
  /** True only while a real payout row is in flight. Drives the pinging dot. */
  payoutScheduled?: boolean;
  /**
   * Set when the figure came from cache. The hero then labels itself "as of
   * {time}" — the only circumstance in which a stale balance may be shown at
   * all, and it must never be silent.
   */
  asOfLabel?: string | null;
  reducedMotion: boolean;
  ready: boolean;
  onRetry?: () => void;
  onPress?: () => void;
};

export function BalanceHero({
  label = "Available for payout",
  availableCents,
  formattedAmount,
  subline,
  payoutScheduled = false,
  asOfLabel = null,
  reducedMotion,
  ready,
  onRetry,
  onPress
}: BalanceHeroProps) {
  const arrival = usePaymentsHeroArrival(reducedMotion, ready);
  const dot = usePaymentsPayoutDot(reducedMotion, payoutScheduled);

  const unavailable = availableCents === null;

  const enter = {
    opacity: arrival,
    transform: [
      {
        translateY: arrival.interpolate({ inputRange: [0, 1], outputRange: [10, 0] })
      }
    ]
  };

  // One announcement, assembled in reading order. The stale label comes first
  // when present: "as of 09:14" changes how everything after it should be
  // understood, so hearing it last would be hearing it too late.
  const announcement = [
    asOfLabel ? `As of ${asOfLabel}` : null,
    label,
    unavailable ? "Unavailable" : formattedAmount,
    subline,
    payoutScheduled ? "Payout in progress" : null
  ]
    .filter(Boolean)
    .join(", ");

  const body = (
    <View style={styles.wrap}>
      <Text style={styles.label}>{label}</Text>

      <Animated.View style={enter}>
        <Text
          style={[styles.amount, unavailable && styles.amountUnavailable]}
          // The figure is inside a labelled container, so it must not also
          // announce itself — otherwise the balance is read twice.
          accessibilityElementsHidden
          importantForAccessibility="no-hide-descendants"
          allowFontScaling
          // Money must never be truncated into a different number. Shrinking is
          // the only acceptable overflow behaviour for a currency figure; an
          // ellipsis could turn $1,240.00 into "$1,24…".
          adjustsFontSizeToFit
          numberOfLines={1}
        >
          {formattedAmount}
        </Text>
      </Animated.View>

      <View style={styles.sublineRow}>
        {payoutScheduled ? (
          <Animated.View
            style={[
              styles.dot,
              {
                opacity: dot.interpolate({ inputRange: [0, 1], outputRange: [0.35, 1] }),
                transform: [
                  { scale: dot.interpolate({ inputRange: [0, 1], outputRange: [0.85, 1.15] }) }
                ]
              }
            ]}
          />
        ) : null}
        <Text style={styles.subline} allowFontScaling numberOfLines={2}>
          {subline}
        </Text>
      </View>

      {asOfLabel ? (
        <Text style={styles.stale} allowFontScaling>
          {`as of ${asOfLabel}`}
        </Text>
      ) : null}

      {unavailable && onRetry ? (
        <Pressable
          onPress={onRetry}
          style={styles.retry}
          accessibilityRole="button"
          accessibilityLabel="Retry loading your balance"
          hitSlop={12}
        >
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      ) : null}
    </View>
  );

  if (!onPress || unavailable) {
    return (
      <View accessible accessibilityRole="summary" accessibilityLabel={announcement}>
        {body}
      </View>
    );
  }

  return (
    <Pressable
      onPress={onPress}
      accessible
      accessibilityRole="button"
      accessibilityLabel={announcement}
      accessibilityHint="Opens payout details"
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: {
    paddingHorizontal: paymentsLight.space.gutter,
    paddingTop: 14,
    paddingBottom: 18
  },
  label: {
    color: paymentsLight.hero.label,
    fontSize: 13,
    fontWeight: "600",
    letterSpacing: 0.2
  },
  amount: {
    marginTop: 6,
    color: paymentsLight.hero.amount,
    fontSize: paymentsLight.money.hero.fontSize,
    fontWeight: paymentsLight.money.hero.fontWeight,
    letterSpacing: paymentsLight.money.hero.letterSpacing,
    fontVariant: ["tabular-nums"]
  },
  amountUnavailable: {
    color: paymentsLight.hero.unavailable
  },
  sublineRow: {
    marginTop: 6,
    flexDirection: "row",
    alignItems: "center",
    gap: 7
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
    backgroundColor: paymentsLight.hero.scheduledDot
  },
  subline: {
    flex: 1,
    color: paymentsLight.hero.subline,
    fontSize: 13
  },
  stale: {
    marginTop: 4,
    color: paymentsLight.hero.staleLabel,
    fontSize: 12,
    fontWeight: "600"
  },
  retry: {
    marginTop: 12,
    alignSelf: "flex-start",
    minHeight: paymentsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 16,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    borderColor: paymentsLight.hero.label
  },
  retryText: {
    color: paymentsLight.hero.amount,
    fontSize: 14,
    fontWeight: "700"
  }
});
