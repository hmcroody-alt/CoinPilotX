/**
 * The navy header for the Advertising manager.
 *
 * It is the one dark band on this light screen and it carries the three things
 * that must be true in *both* ad products before anything below is read: how to
 * get back, how much is in the one ad wallet, and which product you are looking
 * at. The wallet chip and the mode toggle live here — not in the scroll view —
 * because they govern everything beneath them and must not scroll away from it.
 *
 * The wallet is the single source of money truth and funds both products, so the
 * chip is rendered once, here, and shown in every mode. When the wallet has not
 * loaded (or its call failed) the chip is absent rather than showing a zero — a
 * money figure that is wrong is worse than none.
 */

import { type ReactNode } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { adsLight } from "../../theme/adsLight";
import { STORE_AMBIENT, useStoreAmbient } from "../../theme/storeMotion";
import type { AdsMode } from "../../api/adsDashboard";
import { ModeToggle } from "./ModeToggle";
import { WalletChip } from "./WalletChip";

export type AdsHeaderProps = {
  title: string;
  mode: AdsMode;
  onChangeMode: (next: AdsMode) => void;
  onBack: () => void;
  postIsPreview: boolean;
  /** Wallet chip data. Null hides the chip (wallet not loaded / no account). */
  wallet: { balanceLabel: string; fundingLive: boolean; loading: boolean } | null;
  onWallet: () => void;
  reducedMotion: boolean;
  /** Optional extra row rendered under the toggle, still inside the navy band. */
  below?: ReactNode;
};

export function AdsHeader({
  title,
  mode,
  onChangeMode,
  onBack,
  postIsPreview,
  wallet,
  onWallet,
  reducedMotion,
  below
}: AdsHeaderProps) {
  const insets = useSafeAreaInsets();
  const sheen = useStoreAmbient(STORE_AMBIENT.headerSheen, reducedMotion, { resetTo: 0 });

  return (
    <LinearGradient
      colors={[adsLight.bg.headerFrom, adsLight.bg.headerTo]}
      style={[styles.header, { paddingTop: insets.top + 8 }]}
    >
      <Animated.View
        pointerEvents="none"
        style={[
          styles.sheen,
          {
            opacity: sheen.interpolate({
              inputRange: [0, 0.4, 0.5, 0.6, 1],
              outputRange: [0, 0, 0.09, 0, 0]
            }),
            transform: [
              { translateX: sheen.interpolate({ inputRange: [0, 1], outputRange: [-320, 420] }) },
              { rotate: "16deg" }
            ]
          }
        ]}
      />

      <View style={styles.topRow}>
        <Pressable
          onPress={onBack}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel="Go back"
          hitSlop={6}
        >
          <Ionicons name="chevron-back" size={24} color={adsLight.text.onDark} />
        </Pressable>

        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>

        {wallet ? (
          <WalletChip
            balanceLabel={wallet.balanceLabel}
            fundingLive={wallet.fundingLive}
            loading={wallet.loading}
            onPress={onWallet}
            reducedMotion={reducedMotion}
          />
        ) : null}
      </View>

      <ModeToggle
        mode={mode}
        onChange={onChangeMode}
        reducedMotion={reducedMotion}
        postIsPreview={postIsPreview}
      />

      {below}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: adsLight.space.card,
    paddingBottom: 12,
    gap: 12,
    overflow: "hidden"
  },
  sheen: {
    position: "absolute",
    top: -80,
    bottom: -80,
    width: 60,
    backgroundColor: "#FFFFFF"
  },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  iconButton: {
    minWidth: adsLight.size.tapTarget,
    minHeight: adsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: {
    flex: 1,
    fontSize: 20,
    fontWeight: "700",
    color: adsLight.text.onDark
  }
});
