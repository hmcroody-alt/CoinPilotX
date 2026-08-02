/**
 * Where an order came from — Store or Marketplace — as a small badge that is
 * ALWAYS paired with its text label. The colour (blue for Store, violet for
 * Marketplace) reinforces the word; it never carries the meaning alone, so the
 * badge reads the same to a colour-blind user and to a screen reader.
 *
 * The two products keep their family colours across the whole surface: Store is
 * the blue "in transit / shipping" family, Marketplace the violet "local pickup"
 * family, so a glance at the badge already hints how the order will arrive.
 */

import { StyleSheet, Text, View } from "react-native";
import { ordersLight } from "../../theme/ordersLight";
import type { OrderSource } from "../../api/ordersDashboard";

export function SourceBadge({ source }: { source: OrderSource }) {
  const isStore = source === "store";
  const bg = isStore ? ordersLight.source.storeBg : ordersLight.source.marketplaceBg;
  const border = isStore ? ordersLight.source.storeBorder : ordersLight.source.marketplaceBorder;
  const color = isStore ? ordersLight.source.storeText : ordersLight.source.marketplaceText;
  const label = isStore ? "Store" : "Marketplace";
  return (
    <View
      style={[styles.badge, { backgroundColor: bg, borderColor: border }]}
      accessibilityLabel={`Source: ${label}`}
    >
      <Text style={[styles.text, { color }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    borderRadius: ordersLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: "flex-start"
  },
  text: { fontSize: 11, fontWeight: "800", letterSpacing: 0.2 }
});
