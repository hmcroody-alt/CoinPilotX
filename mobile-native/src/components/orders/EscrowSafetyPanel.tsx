/**
 * The "your payment is held until you confirm handoff" reassurance on a pickup
 * order. This is money-critical copy, so it is guarded twice:
 *
 *   1. It only renders when `order.escrowPresentable` is true — which itself is
 *      only true when the escrow feature flag is on AND the order is a pickup.
 *   2. The caller is expected to honour that flag; the component additionally
 *      no-ops if `escrowPresentable` is false, so it can never leak.
 *
 * Because the reachable live surface does NOT expose escrow state today (the
 * canonical hold lives behind the dark `/api/business-os/orders` routes), every
 * figure this panel shows is provisional and it wears a "Preview" tag. It never
 * asserts a specific held amount as fact unless the backend supplied one. The
 * soft violet (`safety.*`) reads as a trust note, not a warning.
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import { OrderPerspective, UnifiedOrder } from "../../api/ordersDashboard";

export function EscrowSafetyPanel({
  order,
  perspective
}: {
  order: UnifiedOrder;
  perspective: OrderPerspective;
}) {
  // Guard #2: never render unless the model marked escrow presentable.
  if (!order.escrowPresentable) return null;

  const released = order.status === "delivered" || order.status === "complete";
  const heading = released
    ? perspective === "seller"
      ? "Payout released"
      : "Handoff confirmed"
    : perspective === "seller"
      ? "Payment held in escrow"
      : "Your payment is protected";

  const body = released
    ? perspective === "seller"
      ? "The buyer confirmed pickup, so the held funds have been released to your payout balance."
      : "You confirmed pickup. The seller has now been paid from the funds you had held safely."
    : perspective === "seller"
      ? "The buyer has paid, and PulseSoc is holding the funds until they confirm the handoff in person."
      : "PulseSoc is holding your payment. It is only released to the seller once you confirm you have the item.";

  return (
    <View style={styles.panel} accessibilityLabel={`${heading}. ${body}`}>
      <View style={styles.headRow}>
        <View style={styles.shield} />
        <Text style={styles.heading}>{heading}</Text>
        <Text style={styles.preview}>Preview</Text>
      </View>
      <Text style={styles.body}>{body}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  panel: {
    backgroundColor: ordersLight.safety.panelBg,
    borderColor: ordersLight.safety.panelBorder,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: ordersLight.radius.control,
    paddingHorizontal: 12,
    paddingVertical: 11,
    gap: 6
  },
  headRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  shield: {
    width: 10,
    height: 10,
    borderRadius: 3,
    backgroundColor: ordersLight.pickup.status
  },
  heading: {
    flex: 1,
    fontSize: 13,
    fontWeight: "800",
    color: ordersLight.safety.panelText
  },
  preview: {
    fontSize: 9,
    fontWeight: "800",
    color: ordersLight.safety.panelText,
    textTransform: "uppercase",
    letterSpacing: 0.4,
    opacity: 0.75
  },
  body: {
    fontSize: 12,
    lineHeight: 17,
    color: ordersLight.safety.panelText
  }
});
