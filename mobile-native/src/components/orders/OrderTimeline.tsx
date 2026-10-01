/**
 * The order timeline — the single component that makes the two perspectives read
 * as one order. It renders the SAME step model (SHIPPING_STEPS / PICKUP_STEPS)
 * for seller and buyer; only the per-step label swaps (sellerLabel vs buyerLabel),
 * never the shape or the reached position. A step the seller sees as "Shipped" the
 * buyer sees as "On its way" — same dot, same fill, same index.
 *
 * Colour is the surface's green "progress / arrival" rule: filled dots and the
 * connecting fill are green (`timeline.fill`) on a neutral track (`timeline.track`),
 * 11px dots. A `mock: true` step (one the live surface cannot confirm) is drawn
 * dimmed and tagged "Preview" so provisional progress never masquerades as fact.
 *
 * The reached index comes only from `reachedStepIndex` over the live status, so
 * both screens fill to the exact same point for the same order.
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import {
  OrderPerspective,
  OrderStep,
  OrderTimelineVariant,
  PICKUP_STEPS,
  SHIPPING_STEPS,
  reachedStepIndex
} from "../../api/ordersDashboard";

export function OrderTimeline({
  status,
  variant,
  perspective
}: {
  status: string;
  variant: OrderTimelineVariant;
  perspective: OrderPerspective;
}) {
  const steps = variant === "pickup" ? PICKUP_STEPS : SHIPPING_STEPS;
  const reached = reachedStepIndex(status, variant);

  return (
    <View style={styles.wrap} accessibilityLabel={accessibilityLine(steps, reached, perspective)}>
      {steps.map((step, index) => {
        const isReached = reached >= 0 && index <= reached;
        const isMock = step.mock;
        const dotColor = isReached
          ? isMock
            ? ordersLight.timeline.pending
            : ordersLight.timeline.fill
          : ordersLight.timeline.track;
        const label = perspective === "seller" ? step.sellerLabel : step.buyerLabel;
        const last = index === steps.length - 1;

        return (
          <View key={step.key} style={styles.step}>
            <View style={styles.railRow}>
              <View style={[styles.dot, { backgroundColor: dotColor }]} />
              {last ? null : (
                <View
                  style={[
                    styles.connector,
                    { backgroundColor: reached > index ? ordersLight.timeline.fill : ordersLight.timeline.track }
                  ]}
                />
              )}
            </View>
            <View style={styles.labelWrap}>
              <Text
                style={[
                  styles.label,
                  {
                    color: isReached ? ordersLight.text.primary : ordersLight.timeline.pending,
                    fontWeight: isReached ? "800" : "600"
                  }
                ]}
                numberOfLines={2}
              >
                {label}
              </Text>
              {isMock ? <Text style={styles.preview}>Preview</Text> : null}
            </View>
          </View>
        );
      })}
    </View>
  );
}

function accessibilityLine(steps: OrderStep[], reached: number, perspective: OrderPerspective): string {
  if (reached < 0) return "Order timeline not started";
  const label = perspective === "seller" ? steps[reached]?.sellerLabel : steps[reached]?.buyerLabel;
  return `Order progress: ${label || "in progress"}`;
}

const DOT = ordersLight.size.dot;

const styles = StyleSheet.create({
  wrap: {
    flexDirection: "row",
    alignItems: "flex-start"
  },
  step: {
    flex: 1,
    alignItems: "flex-start"
  },
  railRow: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "stretch",
    height: DOT
  },
  dot: {
    width: DOT,
    height: DOT,
    borderRadius: DOT / 2
  },
  connector: {
    flex: 1,
    height: 2,
    marginHorizontal: 2
  },
  labelWrap: {
    marginTop: 6,
    paddingRight: 6
  },
  label: {
    fontSize: 11,
    letterSpacing: 0.1
  },
  preview: {
    marginTop: 2,
    fontSize: 9,
    fontWeight: "800",
    color: ordersLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4
  }
});
