/**
 * The ad-wallet chip that sits on the navy header, visible in both modes.
 *
 * There is one wallet and it funds both ad products, so this chip is the single
 * source of money truth on the screen. The balance it shows is the server's
 * spendable balance, passed in already formatted — this component computes no
 * money and holds no arithmetic. When funding is not live (the backend
 * hardcodes that today), the chip still shows the real balance but its action
 * says "Wallet" rather than "Add funds", because an Add-funds button that
 * cannot charge would be a control that lies.
 *
 * Because it lives on the navy header, its surface and border are light-on-dark
 * rather than the light-palette hairline, and the balance is gold — the money
 * colour used nowhere else.
 */

import { Pressable, StyleSheet, Text, View } from "react-native";
import { Animated } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { adsLight } from "../../theme/adsLight";
import { useStorePress } from "../../theme/storeMotion";

export type WalletChipProps = {
  /** Already formatted, e.g. "$142.00". */
  balanceLabel: string;
  /** Whether funding can actually charge. Drives the action affordance. */
  fundingLive: boolean;
  /** Opens the wallet / billing surface. */
  onPress: () => void;
  reducedMotion: boolean;
  /** Shown while the wallet is still loading, so the chip reserves its space. */
  loading?: boolean;
};

export function WalletChip({
  balanceLabel,
  fundingLive,
  onPress,
  reducedMotion,
  loading = false
}: WalletChipProps) {
  const press = useStorePress(reducedMotion, 0.96);

  const actionLabel = fundingLive ? "Add funds" : "Wallet";
  const a11yLabel = loading
    ? "Ad wallet, loading balance"
    : `Ad wallet balance ${balanceLabel}. ${
        fundingLive ? "Tap to add funds." : "Tap to open wallet."
      }`;

  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.chip}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={a11yLabel}
        hitSlop={6}
      >
        <Ionicons name="wallet-outline" size={16} color={adsLight.wallet.amount} />
        <View style={styles.body}>
          <Text style={styles.label} numberOfLines={1}>
            Ad wallet
          </Text>
          <Text style={styles.amount} numberOfLines={1}>
            {loading ? "—" : balanceLabel}
          </Text>
        </View>
        <View style={styles.action}>
          <Ionicons
            name={fundingLive ? "add-circle" : "chevron-forward"}
            size={16}
            color={adsLight.text.onDark}
          />
          <Text style={styles.actionText} numberOfLines={1}>
            {actionLabel}
          </Text>
        </View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: adsLight.size.tapTarget,
    paddingHorizontal: 12,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.wallet.chipBg,
    borderWidth: 1,
    borderColor: adsLight.wallet.chipBorder
  },
  body: { gap: 1 },
  label: { fontSize: 10, fontWeight: "600", color: adsLight.wallet.label },
  amount: { fontSize: 15, fontWeight: "800", color: adsLight.wallet.amount },
  action: {
    flexDirection: "row",
    alignItems: "center",
    gap: 3,
    marginLeft: 4,
    paddingLeft: 8,
    borderLeftWidth: StyleSheet.hairlineWidth,
    borderLeftColor: adsLight.wallet.chipBorder
  },
  actionText: { fontSize: 12, fontWeight: "700", color: adsLight.text.onDark }
});
