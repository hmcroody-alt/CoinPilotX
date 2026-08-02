/**
 * The buyer's horizontal "Buy again" rail — recently delivered/complete orders
 * offered for re-purchase. Whether an item is actually still purchasable is a
 * signal the live surface does NOT carry (declared in ORDERS_MOCK_DATA_GAPS), so
 * every tile is tagged "Preview" and tapping it routes to the listing rather than
 * asserting availability or re-charging. No money moves from this rail.
 */

import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import { UnifiedOrder } from "../../api/ordersDashboard";

export function BuyAgainRail({
  orders,
  onPressItem
}: {
  orders: UnifiedOrder[];
  onPressItem?: (order: UnifiedOrder) => void;
}) {
  const items = orders
    .filter((o) => o.status === "delivered" || o.status === "complete")
    .slice(0, 12);
  if (items.length === 0) return null;

  return (
    <View style={styles.section}>
      <View style={styles.head}>
        <Text style={styles.title}>Buy again</Text>
        <Text style={styles.preview}>Preview</Text>
      </View>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.rail}
      >
        {items.map((order) => (
          <Pressable
            key={order.id}
            style={styles.tile}
            onPress={() => onPressItem?.(order)}
            accessibilityRole="button"
            accessibilityLabel={`Buy ${order.title} again`}
          >
            <View style={styles.thumb} />
            <Text style={styles.name} numberOfLines={2}>
              {order.title}
            </Text>
            <Text style={styles.price}>{order.amountLabel}</Text>
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  section: {
    gap: 10
  },
  head: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: ordersLight.space.gutter
  },
  title: {
    fontSize: 15,
    fontWeight: "800",
    color: ordersLight.text.primary
  },
  preview: {
    fontSize: 9,
    fontWeight: "800",
    color: ordersLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4
  },
  rail: {
    paddingHorizontal: ordersLight.space.gutter,
    gap: 12
  },
  tile: {
    width: 128,
    gap: 6
  },
  thumb: {
    width: 128,
    height: 96,
    borderRadius: ordersLight.radius.thumb,
    backgroundColor: ordersLight.bg.skeleton
  },
  name: {
    fontSize: 12,
    fontWeight: "600",
    color: ordersLight.text.primary
  },
  price: {
    fontSize: 12,
    fontWeight: "800",
    color: ordersLight.text.primary
  }
});
