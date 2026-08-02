/**
 * Loading, error and empty treatments for the Store screen.
 *
 * Three rules, each of which is a reaction to a specific failure of the screen
 * this replaces:
 *
 * * **Skeletons match the final layout.** A spinner over a blank screen tells
 *   the seller nothing and then reflows everything underneath it when the data
 *   lands. These blocks sit where the real content will sit, so the page does
 *   not jump.
 * * **Errors are per-section and say what failed.** "Orders didn't load" with a
 *   retry beside it is actionable; "Something went wrong" over the whole screen
 *   is not, and it throws away the half of the page that loaded fine.
 * * **Empty is an invitation, not an error.** A seller with no listings has not
 *   hit a problem — they have not started yet, and the screen should say so and
 *   offer the one thing that helps.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { STORE_AMBIENT, useStoreAmbient, useStorePress } from "../../theme/storeMotion";

/**
 * A single skeleton block. Breathes slowly rather than sweeping, so a screen
 * full of them does not read as a strobe. Settles solid under reduce-motion.
 */
export function StoreSkeletonBlock({
  width,
  height,
  radius = 6,
  reducedMotion
}: {
  width: number | `${number}%`;
  height: number;
  radius?: number;
  reducedMotion: boolean;
}) {
  const pulse = useStoreAmbient(STORE_AMBIENT.bannerTilt, reducedMotion, {
    resetTo: 1,
    pingPong: true
  });
  return (
    <Animated.View
      accessibilityElementsHidden
      importantForAccessibility="no"
      style={[
        { width, height, borderRadius: radius, backgroundColor: storeLight.bg.skeleton },
        { opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.55, 1]}) }
      ]}
    />
  );
}

/** Placeholder shaped like a KPI tile. */
export function StoreKpiSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <View style={styles.kpiSkeleton}>
      <StoreSkeletonBlock width="55%" height={10} reducedMotion={reducedMotion} />
      <StoreSkeletonBlock width="70%" height={20} reducedMotion={reducedMotion} />
      <StoreSkeletonBlock width="40%" height={10} reducedMotion={reducedMotion} />
    </View>
  );
}

/** Placeholder shaped like a listing row, thumbnail included. */
export function StoreRowSkeleton({ reducedMotion }: { reducedMotion: boolean }) {
  return (
    <View style={styles.rowSkeleton}>
      <StoreSkeletonBlock
        width={storeLight.size.thumb}
        height={storeLight.size.thumb}
        radius={storeLight.radius.thumb}
        reducedMotion={reducedMotion}
      />
      <View style={styles.rowSkeletonBody}>
        <StoreSkeletonBlock width="85%" height={12} reducedMotion={reducedMotion} />
        <StoreSkeletonBlock width="45%" height={12} reducedMotion={reducedMotion} />
        <StoreSkeletonBlock width="60%" height={10} reducedMotion={reducedMotion} />
      </View>
    </View>
  );
}

/**
 * Inline failure for one section. Names what failed — the caller passes
 * "Listings didn't load", never a generic apology — and offers the retry right
 * where the missing content would have been.
 */
export function StoreSectionError({
  message,
  onRetry,
  reducedMotion
}: {
  message: string;
  onRetry: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.97);
  return (
    <View style={styles.error} accessibilityLiveRegion="polite">
      <Text style={styles.errorText}>{message}</Text>
      <Animated.View style={press.style}>
        <Pressable
          style={styles.retry}
          onPress={onRetry}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          accessibilityRole="button"
          accessibilityLabel={`Retry. ${message}`}
        >
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

/**
 * Shown in place of the listings section when the seller has none. The header,
 * status strip and KPI grid stay — the screen is still their store, it is just
 * empty — so only this block changes.
 */
export function StoreEmptyListings({
  onAddListing,
  reducedMotion
}: {
  onAddListing: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.97);
  return (
    <View style={styles.empty}>
      <Text style={styles.emptyTitle}>Your store is ready. It just needs something to sell.</Text>
      <Text style={styles.emptyBody}>
        Add your first listing and it will appear here with its stock, views and sales.
      </Text>
      <Animated.View style={press.style}>
        <Pressable
          style={styles.emptyCta}
          onPress={onAddListing}
          onPressIn={press.onPressIn}
          onPressOut={press.onPressOut}
          accessibilityRole="button"
          accessibilityLabel="Add your first listing"
        >
          <Text style={styles.emptyCtaText}>＋ Add a listing</Text>
        </Pressable>
      </Animated.View>
    </View>
  );
}

/** The "showing cached data" note, used when the network is unavailable. */
export function StoreOfflineNote({ text }: { text: string }) {
  return (
    <View style={styles.offline} accessibilityLiveRegion="polite">
      <Text style={styles.offlineText}>{text}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  kpiSkeleton: {
    flex: 1,
    gap: 8,
    minHeight: 96,
    padding: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    justifyContent: "center"
  },
  rowSkeleton: {
    flexDirection: "row",
    gap: storeLight.space.gutter,
    padding: storeLight.space.card,
    minHeight: 88,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  rowSkeletonBody: { flex: 1, gap: 8, justifyContent: "center" },
  error: {
    padding: storeLight.space.card,
    gap: 10,
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    alignItems: "flex-start"
  },
  errorText: { fontSize: 13, color: storeLight.text.primary, fontWeight: "600" },
  retry: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 16,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton
  },
  retryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary },
  empty: {
    padding: 20,
    gap: 10,
    alignItems: "flex-start",
    backgroundColor: storeLight.bg.card
  },
  emptyTitle: { fontSize: 16, fontWeight: "700", color: storeLight.text.primary },
  emptyBody: { fontSize: 13, color: storeLight.text.muted, lineHeight: 18 },
  emptyCta: {
    marginTop: 6,
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 20,
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from
  },
  emptyCtaText: { fontSize: 14, fontWeight: "700", color: storeLight.cta.text },
  offline: {
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 8
  },
  offlineText: { fontSize: 11, color: storeLight.text.muted }
});
