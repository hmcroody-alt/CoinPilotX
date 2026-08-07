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

import { forwardRef, type ReactNode } from "react";
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
  /**
   * Extra controls for the top row, placed between the title and the bell.
   *
   * The Marketplace screen's buying mode needs a saved-items heart and a cart
   * badge up here. They are passed in rather than built in because they are not
   * things a *store* header has — Store passes nothing and renders exactly as it
   * did before this prop existed.
   */
  accessories?: ReactNode;
  /**
   * Rendered below the search row, still inside the navy gradient. The
   * Marketplace mode toggle and location strip live here: they belong to the
   * header visually, and putting them in the scroll view would let them slide
   * away from the search field they modify.
   */
  below?: ReactNode;
  /** Hides the bell entirely — buying mode has no seller notifications. */
  hideNotifications?: boolean;
  /**
   * Rendered immediately to the right of the title text, inside the same flex
   * row, so it reads as part of the title rather than as a separate control.
   *
   * The Business Hub is the case this exists for: its title is the seller's
   * business name and the verification tick belongs against the name, the way a
   * tick does everywhere else in the product. `accessories` could not do it —
   * that slot sits after the title's flex spacer and lands at the far right,
   * beside the bell. When omitted the row renders exactly as it did before.
   */
  titleAdornment?: ReactNode;
  /**
   * Hides the search row entirely.
   *
   * Insights is the case this exists for: it is one screen showing one period,
   * with nothing on it to search. A search field that filtered nothing would be
   * a promise the screen cannot keep, and leaving it there "for consistency"
   * would be consistency with a control rather than with a behaviour.
   */
  hideSearch?: boolean;
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
    reducedMotion,
    accessories,
    below,
    titleAdornment,
    hideNotifications = false,
    hideSearch = false
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

        <View style={styles.titleWrap}>
          <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
            {title}
          </Text>
          {titleAdornment}
        </View>

        {accessories}

        <Pressable
          onPress={onNotifications}
          style={[styles.iconButton, hideNotifications && styles.hidden]}
          accessibilityRole="button"
          accessibilityElementsHidden={hideNotifications}
          importantForAccessibility={hideNotifications ? "no-hide-descendants" : "auto"}
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

      <View style={[styles.searchRow, hideSearch && styles.hidden]}>
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

      {below}
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
  /** `display: none` rather than a conditional render, so the row's spacing is
      identical in both modes and the title does not shift when modes swap. */
  hidden: { display: "none" },
  /** Takes the flex the title Text used to hold, so layout is unchanged when
      `titleAdornment` is absent. */
  titleWrap: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6 },
  title: {
    flexShrink: 1,
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
    backgroundColor: storeLight.accent.brand,
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
    backgroundColor: storeLight.accent.brand,
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
  stripAction: { fontSize: 12, fontWeight: "700", color: storeLight.accent.brand }
});
