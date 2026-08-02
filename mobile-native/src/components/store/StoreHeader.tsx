/**
 * The navy header and the status strip beneath it.
 *
 * The header is the one place on this light screen that is dark, and it earns
 * that by carrying the three things a seller needs before they read anything:
 * how to get back, whether anything is waiting for them, and a way to find one
 * listing among hundreds.
 *
 * The search field searches **the seller's own** listings, orders and SKUs. It
 * is not the buyer-facing marketplace search, and the placeholder says so —
 * a seller typing a competitor's product name here and getting results would
 * be a quietly wrong screen.
 */

import { forwardRef } from "react";
import {
  Animated,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type TextInput as TextInputType
} from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { storeLight } from "../../theme/storeLight";
import {
  STORE_AMBIENT,
  useStoreAmbient,
  useStoreBadgePop,
  useStorePress
} from "../../theme/storeMotion";
import { StoreLiveDot } from "./StoreStatusLed";

export type StoreHeaderProps = {
  title: string;
  query: string;
  onQueryChange: (next: string) => void;
  onSubmitSearch: () => void;
  onBack: () => void;
  onNotifications: () => void;
  unreadCount: number;
  searchPlaceholder: string;
  reducedMotion: boolean;
};

export const StoreHeader = forwardRef<TextInputType, StoreHeaderProps>(function StoreHeader(
  {
    title,
    query,
    onQueryChange,
    onSubmitSearch,
    onBack,
    onNotifications,
    unreadCount,
    searchPlaceholder,
    reducedMotion
  },
  ref
) {
  const insets = useSafeAreaInsets();
  const sheen = useStoreAmbient(STORE_AMBIENT.headerSheen, reducedMotion, { resetTo: 0 });
  const wiggle = useStoreAmbient(STORE_AMBIENT.bellWiggle, reducedMotion, {
    // The bell only moves while something is actually waiting. A bell that
    // wiggles at an empty inbox is the animation equivalent of crying wolf.
    enabled: unreadCount > 0,
    resetTo: 0,
    pingPong: true
  });
  const badge = useStoreBadgePop(reducedMotion, unreadCount > 0);
  const searchPress = useStorePress(reducedMotion, 0.94);

  return (
    <LinearGradient
      colors={[storeLight.bg.headerFrom, storeLight.bg.headerTo]}
      style={[styles.header, { paddingTop: insets.top + 8 }]}
    >
      {/* Slow diagonal sheen. Ambient only — it carries no state, so it is the
          first thing removed under reduce-motion. */}
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
          <Ionicons name="chevron-back" size={24} color={storeLight.text.onDark} />
        </Pressable>

        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>

        <Pressable
          onPress={onNotifications}
          style={styles.iconButton}
          accessibilityRole="button"
          accessibilityLabel={
            unreadCount > 0
              ? `Notifications, ${unreadCount} unread`
              : "Notifications, none unread"
          }
          hitSlop={6}
        >
          <Animated.View
            style={{
              transform: [
                {
                  rotate: wiggle.interpolate({
                    inputRange: [0, 1],
                    outputRange: ["-7deg", "7deg"]
                  })
                }
              ]
            }}
          >
            <Ionicons name="notifications-outline" size={22} color={storeLight.text.onDark} />
          </Animated.View>
          {unreadCount > 0 ? (
            <Animated.View
              style={[styles.badge, { transform: [{ scale: badge }] }]}
              accessibilityElementsHidden
              importantForAccessibility="no"
            >
              <Text style={styles.badgeText}>{unreadCount > 99 ? "99+" : unreadCount}</Text>
            </Animated.View>
          ) : null}
        </Pressable>
      </View>

      <View style={styles.searchRow}>
        <TextInput
          ref={ref}
          style={styles.search}
          value={query}
          onChangeText={onQueryChange}
          onSubmitEditing={onSubmitSearch}
          placeholder={searchPlaceholder}
          placeholderTextColor={storeLight.text.muted}
          returnKeyType="search"
          autoCorrect={false}
          accessibilityLabel={searchPlaceholder}
        />
        <Animated.View style={searchPress.style}>
          <Pressable
            style={styles.searchButton}
            onPress={onSubmitSearch}
            onPressIn={searchPress.onPressIn}
            onPressOut={searchPress.onPressOut}
            accessibilityRole="button"
            accessibilityLabel="Search your store"
          >
            <Ionicons name="search" size={18} color={storeLight.text.primary} />
          </Pressable>
        </Animated.View>
      </View>
    </LinearGradient>
  );
});

export type StoreStatusStripProps = {
  /** e.g. "Bright Coffee Co · Open for orders" — already assembled by the caller. */
  text: string;
  open: boolean;
  actionLabel: string;
  onAction: () => void;
  reducedMotion: boolean;
};

/**
 * The strip directly under the header. Two variants, and the difference is
 * stated in words as well as colour: open reads "Open for orders" beside a
 * pulsing green dot; paused reads "Paused — buyers can't order" beside a still
 * grey one, with the action changing from "Manage" to "Reopen".
 */
export function StoreStatusStrip({
  text,
  open,
  actionLabel,
  onAction,
  reducedMotion
}: StoreStatusStripProps) {
  return (
    <View style={styles.strip}>
      <StoreLiveDot open={open} reducedMotion={reducedMotion} />
      <Text style={styles.stripText} numberOfLines={1}>
        {text}
      </Text>
      <Pressable
        onPress={onAction}
        hitSlop={10}
        accessibilityRole="link"
        accessibilityLabel={`${actionLabel}. ${text}`}
      >
        <Text style={styles.stripAction}>{actionLabel}</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    paddingHorizontal: storeLight.space.card,
    paddingBottom: 12,
    gap: 10,
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
    minWidth: storeLight.size.tapTarget,
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: {
    flex: 1,
    fontSize: 20,
    fontWeight: "700",
    color: storeLight.text.onDark
  },
  badge: {
    position: "absolute",
    top: 6,
    right: 6,
    minWidth: 16,
    height: 16,
    paddingHorizontal: 4,
    borderRadius: 8,
    backgroundColor: storeLight.accent.orange,
    alignItems: "center",
    justifyContent: "center"
  },
  badgeText: { fontSize: 10, fontWeight: "800", color: storeLight.text.primary },
  searchRow: { flexDirection: "row", alignItems: "center", gap: 0 },
  search: {
    flex: 1,
    height: storeLight.size.tapTarget,
    paddingHorizontal: 12,
    backgroundColor: storeLight.bg.card,
    color: storeLight.text.primary,
    borderTopLeftRadius: storeLight.radius.control,
    borderBottomLeftRadius: storeLight.radius.control,
    fontSize: 14
  },
  searchButton: {
    width: 52,
    height: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: storeLight.accent.orange,
    borderTopRightRadius: storeLight.radius.control,
    borderBottomRightRadius: storeLight.radius.control
  },
  strip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: storeLight.space.card,
    minHeight: 40,
    backgroundColor: storeLight.bg.strip
  },
  stripText: { flex: 1, fontSize: 12, color: storeLight.text.onDarkMuted, fontWeight: "600" },
  stripAction: { fontSize: 12, fontWeight: "700", color: storeLight.accent.orange }
});
