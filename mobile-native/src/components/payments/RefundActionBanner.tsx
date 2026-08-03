/**
 * The refund / dispute banner — a request for a response, stated truthfully.
 *
 * What the design asked for, and why this differs
 * ----------------------------------------------
 * The brief specifies "respond within N days or it's auto-approved", with N and
 * the consequence taken from the actual policy — and adds that if the policy
 * differs, say what's true.
 *
 * The policy differs by being absent. There is no auto-approval rule, no
 * deadline field, and no timer anywhere on either money surface. So there is no
 * N to render. Inventing one would be the worst possible failure mode for this
 * component: a seller who believes they have three days, when nothing is
 * counting, may deprioritise a real dispute; a seller who believes a refund
 * auto-approves may abandon one they would have contested.
 *
 * So the banner states the dispute, its amount, the buyer, the item and its
 * real status, and asks for a response without asserting a deadline or a
 * consequence. `deadlineNote` exists so the honest sentence can be supplied by
 * the caller, and the moment a real policy is implemented this component needs
 * no change — the note becomes the real deadline and `urgent` starts meaning
 * something.
 *
 * Counting and sourcing: these come from the same query that feeds Orders'
 * "Returns & issues" tile. Two screens disagreeing about how many disputes are
 * open is the kind of bug a seller notices and cannot explain.
 *
 * The shimmer is seven seconds and purely atmospheric. Nothing in it carries
 * information, so reduce-motion removes it with no loss.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { paymentsLight } from "../../theme/paymentsLight";

export type RefundActionBannerProps = {
  /** Pre-formatted amount, or "" when the figure is not known. */
  formattedAmount: string;
  buyerName?: string;
  itemTitle?: string;
  /** The real status word from the backend record. */
  status?: string;
  /**
   * The true sentence about timing. Empty when nothing is counting — which is
   * the case today. Never fabricate a countdown here.
   */
  deadlineNote?: string;
  /** More than one open dispute; the banner then speaks for the set. */
  additionalCount?: number;
  /** A 0 → 1 sweep driver from `usePaymentsRefundShimmer`. */
  shimmer?: Animated.Value | null;
  onRespond?: () => void;
};

export function RefundActionBanner({
  formattedAmount,
  buyerName,
  itemTitle,
  status,
  deadlineNote = "",
  additionalCount = 0,
  shimmer = null,
  onRespond
}: RefundActionBannerProps) {
  const heading =
    additionalCount > 0
      ? `${additionalCount + 1} refund requests need a response`
      : "A refund request needs your response";

  const detail = [
    formattedAmount || null,
    itemTitle ? `for ${itemTitle}` : null,
    buyerName ? `from ${buyerName}` : null
  ]
    .filter(Boolean)
    .join(" ");

  // Assertive, because this is a request with a consequence for ignoring it —
  // even though the consequence is currently unspecified, the money is real.
  const announcement = [heading, detail, status ? `Status ${status}` : null, deadlineNote]
    .filter(Boolean)
    .join(", ");

  return (
    <View
      style={styles.banner}
      accessible
      accessibilityRole="alert"
      accessibilityLiveRegion="assertive"
      accessibilityLabel={announcement}
    >
      {shimmer ? (
        <Animated.View
          pointerEvents="none"
          style={[
            styles.shimmer,
            {
              opacity: shimmer.interpolate({
                inputRange: [0, 0.5, 1],
                outputRange: [0, 1, 0]
              }),
              transform: [
                {
                  translateX: shimmer.interpolate({
                    inputRange: [0, 1],
                    outputRange: [-220, 320]
                  })
                }
              ]
            }
          ]}
        />
      ) : null}

      <Text style={styles.heading} allowFontScaling numberOfLines={2}>
        {heading}
      </Text>

      {detail ? (
        <Text style={styles.detail} allowFontScaling numberOfLines={3}>
          {detail}
        </Text>
      ) : null}

      {status ? (
        <Text style={styles.status} allowFontScaling>
          {status}
        </Text>
      ) : null}

      {/* Rendered only when there is something true to say. An empty deadline
          line is better than a confident invented one. */}
      {deadlineNote ? (
        <Text style={styles.deadline} allowFontScaling numberOfLines={3}>
          {deadlineNote}
        </Text>
      ) : null}

      {onRespond ? (
        <Pressable
          onPress={onRespond}
          style={styles.action}
          accessibilityRole="button"
          accessibilityLabel="Review refund requests in Orders"
        >
          <Text style={styles.actionText}>Review in Orders</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    marginHorizontal: paymentsLight.space.gutter,
    padding: paymentsLight.space.card,
    borderRadius: paymentsLight.radius.card,
    backgroundColor: paymentsLight.refundBanner.from,
    borderWidth: 1,
    borderColor: paymentsLight.refundBanner.border,
    overflow: "hidden"
  },
  shimmer: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: 140,
    backgroundColor: paymentsLight.refundBanner.shimmer
  },
  heading: {
    color: paymentsLight.refundBanner.heading,
    fontSize: 15,
    fontWeight: "700"
  },
  detail: {
    marginTop: 6,
    color: paymentsLight.refundBanner.body,
    fontSize: 13,
    lineHeight: 18,
    fontVariant: ["tabular-nums"]
  },
  status: {
    marginTop: 4,
    color: paymentsLight.text.muted,
    fontSize: 12,
    fontWeight: "600"
  },
  deadline: {
    marginTop: 6,
    color: paymentsLight.refundBanner.body,
    fontSize: 12,
    lineHeight: 17
  },
  action: {
    marginTop: 12,
    alignSelf: "flex-start",
    minHeight: paymentsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 18,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    borderColor: paymentsLight.refundBanner.border,
    backgroundColor: paymentsLight.bg.card
  },
  actionText: {
    color: paymentsLight.refundBanner.heading,
    fontSize: 14,
    fontWeight: "700"
  }
});
