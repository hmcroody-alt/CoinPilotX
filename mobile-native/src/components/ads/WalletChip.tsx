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
import { absentValueText } from "../../api/stateLanguage";

export type WalletChipProps = {
  /**
   * Already formatted, e.g. "$142.00". `null` when no balance has arrived yet —
   * see the note on `loading`. It is never a placeholder string: a caller with
   * no figure passes null and lets this component say so in words.
   */
  balanceLabel: string | null;
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

  /*
   * What the amount slot says when there is no amount.
   *
   * The chip used to render `loading ? "—" : balanceLabel` while its
   * accessibility label already said "Ad wallet, loading balance". Two readers
   * of the same chip were being told different things, and the spoken one was
   * the honest one — the sighted reader got a glyph that means "not loaded",
   * "failed", "zero" and "not set up" all at once, on the one figure on this
   * screen where confusing those is most expensive. §31 prohibits the universal
   * dash for exactly this, and prohibits it hardest on money.
   *
   * Both states now say what they are, and they say different things:
   * "Checking…" is a request in flight and resolves on its own, "Couldn't load"
   * is a request that failed. Neither can be mistaken for the balance being
   * zero, which is `"$0.00"` and is a real answer with real consequences.
   *
   * `balanceLabel` being null while `loading` is false is the wallet call
   * having failed; the screen normally swaps in `AdsWalletUnavailable` for that
   * case, and this branch is what keeps the chip honest if it does not.
   */
  const amountText = loading
    ? absentValueText("loading")
    : balanceLabel ?? absentValueText("unavailable");
  // Words are not money, so they do not wear the money treatment. Gold at 15/800
  // is reserved for a figure; a status word at the same weight reads as a
  // balance called "Checking…".
  const amountIsFigure = Boolean(!loading && balanceLabel);

  const actionLabel = fundingLive ? "Add funds" : "Wallet";
  const a11yLabel = amountIsFigure
    ? `Ad wallet balance ${balanceLabel}. ${
        fundingLive ? "Tap to add funds." : "Tap to open wallet."
      }`
    : loading
    ? "Ad wallet, checking balance. Tap to open wallet."
    : "Ad wallet balance couldn’t load. Tap to open wallet.";

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
          <Text
            style={[styles.amount, amountIsFigure ? null : styles.amountWord]}
            numberOfLines={1}
          >
            {amountText}
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
  // The status wording, not a balance. Smaller and muted so it cannot be read as
  // a figure, and small enough that "Couldn't load" fits the chip at a large
  // text scale rather than clipping — §37 forbids the truncation more than it
  // minds the size.
  amountWord: { fontSize: 12, fontWeight: "700", color: adsLight.text.onDarkMuted },
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
