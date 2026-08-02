/**
 * The time-critical banner: a linked offer is about to expire. It names the buyer,
 * the amount, the item and the time remaining, deep-links "Open conversation ›" to
 * that thread, and — when more than one offer qualifies — shows "+N more".
 *
 * Every fact here is read from the Marketplace offer state machine via
 * `deriveExpiryBanner` (there is exactly one expiry clock in the app, and it is not
 * here). With no offers backend the whole banner is dark by default, so this
 * component only ever renders real, urgent data.
 *
 * A slow ~7s shimmer sweeps the warm wash to draw a glance without nagging; under
 * reduce-motion it is not scheduled and the banner is a calm static card.
 */

import { useEffect, useRef } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { messagesLight } from "../../theme/messagesLight";
import { useAppForegrounded } from "../../theme/storeMotion";
import { ExpiryBanner as ExpiryBannerData } from "../../api/commerceInbox";

const SHIMMER_MS = 7000;

export function ExpiryBanner({
  banner,
  onOpen,
  reducedMotion
}: {
  banner: ExpiryBannerData;
  onOpen: (banner: ExpiryBannerData) => void;
  reducedMotion: boolean;
}) {
  const shimmer = useRef(new Animated.Value(0)).current;
  const foreground = useAppForegrounded();

  useEffect(() => {
    if (reducedMotion || !foreground) {
      shimmer.setValue(0);
      return;
    }
    const loop = Animated.loop(
      Animated.timing(shimmer, { toValue: 1, duration: SHIMMER_MS, useNativeDriver: true })
    );
    loop.start();
    return () => loop.stop();
  }, [shimmer, reducedMotion, foreground]);

  const label =
    `${banner.buyerName}'s ${banner.amountLabel} offer on ${banner.itemTitle} expires in ${banner.remainingLabel}` +
    (banner.moreCount > 0 ? `, plus ${banner.moreCount} more expiring soon` : "");

  return (
    <Pressable
      onPress={() => onOpen(banner)}
      style={styles.banner}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint="Opens the conversation"
    >
      {!reducedMotion ? (
        <Animated.View
          pointerEvents="none"
          style={[
            styles.shimmer,
            {
              opacity: shimmer.interpolate({
                inputRange: [0, 0.5, 1],
                outputRange: [0, 0.35, 0]
              }),
              transform: [
                { translateX: shimmer.interpolate({ inputRange: [0, 1], outputRange: [-220, 220] }) },
                { rotate: "18deg" }
              ]
            }
          ]}
        />
      ) : null}

      <View style={styles.iconWrap}>
        <Ionicons name="time-outline" size={18} color={messagesLight.banner.accent} />
      </View>
      <View style={styles.textCol}>
        <Text style={styles.line} numberOfLines={2}>
          <Text style={styles.strong}>{banner.buyerName}</Text>
          {"'s "}
          <Text style={styles.strong}>{banner.amountLabel}</Text>
          {" offer on "}
          <Text style={styles.strong}>{banner.itemTitle}</Text>
          {" expires in "}
          <Text style={styles.accent}>{banner.remainingLabel}</Text>
        </Text>
        <View style={styles.footRow}>
          <Text style={styles.cta}>Open conversation ›</Text>
          {banner.moreCount > 0 ? <Text style={styles.more}>+{banner.moreCount} more</Text> : null}
        </View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 10,
    marginHorizontal: messagesLight.space.gutter,
    padding: 12,
    borderRadius: messagesLight.radius.card,
    backgroundColor: messagesLight.banner.bg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: messagesLight.banner.border,
    overflow: "hidden"
  },
  shimmer: {
    position: "absolute",
    top: -20,
    bottom: -20,
    width: 60,
    backgroundColor: "#FFFFFF"
  },
  iconWrap: { paddingTop: 1 },
  textCol: { flex: 1, gap: 4 },
  line: { fontSize: 13, lineHeight: 18, color: messagesLight.banner.text },
  strong: { fontWeight: "800" },
  accent: { color: messagesLight.banner.accent, fontWeight: "800" },
  footRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  cta: { fontSize: 13, fontWeight: "800", color: messagesLight.banner.accent },
  more: { fontSize: 12, fontWeight: "700", color: messagesLight.banner.text }
});
