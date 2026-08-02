/**
 * "3 new matches for 'oak dining table'".
 *
 * A single card that appears only when a saved search has picked something up
 * since it was last opened. When it has nothing to say it renders nothing —
 * `matchCount === 0` returns null rather than an empty shell, because a card
 * that says "0 new matches" is worse than silence.
 *
 * ## Collapsing
 *
 * Several saved searches with matches collapse into one card reading "New
 * matches for 2 saved searches", not a stack. Three of these in a row would push
 * the actual feed below the fold, which inverts the screen: the feed is the
 * content and the alert is the interruption.
 *
 * The collapsed form deliberately drops the individual counts. "New matches for
 * 2 saved searches" is honest and short; "7 new matches across 2 saved searches"
 * invites arithmetic about which search found what, and the answer is one tap
 * away anyway.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { storeLight } from "../../theme/storeLight";
import { marketplaceLight } from "../../theme/marketplaceLight";
import { useStoreAmbient, useStorePress } from "../../theme/storeMotion";
import { MARKETPLACE_AMBIENT } from "../../theme/marketplaceMotion";

export type SavedSearchAlertProps = {
  /** How many saved searches have new matches. Drives the collapse. */
  searchCount: number;
  /** Total new matches. Only shown when exactly one search matched. */
  matchCount: number;
  /** The query text. Only used when `searchCount === 1`. */
  query?: string;
  onPress: () => void;
  reducedMotion: boolean;
};

export function SavedSearchAlert({
  searchCount,
  matchCount,
  query,
  onPress,
  reducedMotion
}: SavedSearchAlertProps) {
  const ping = useStoreAmbient(MARKETPLACE_AMBIENT.savedSearchPing, reducedMotion, {
    resetTo: 0
  });
  const press = useStorePress(reducedMotion, 0.985);

  if (searchCount < 1 || matchCount < 1) return null;

  const headline =
    searchCount === 1 && query
      ? `${matchCount} new ${matchCount === 1 ? "match" : "matches"} for “${query}”`
      : `New matches for ${searchCount} saved searches`;

  return (
    <Animated.View style={press.style}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={headline}
        accessibilityHint="Opens the matching items"
      >
        <LinearGradient
          colors={[marketplaceLight.savedSearch.from, marketplaceLight.savedSearch.to]}
          style={styles.card}
        >
          <Animated.View
            style={[
              styles.dot,
              {
                opacity: ping.interpolate({
                  inputRange: [0, 0.5, 1],
                  outputRange: [0.35, 1, 0.35]
                }),
                transform: [
                  {
                    scale: ping.interpolate({
                      inputRange: [0, 0.5, 1],
                      outputRange: [0.8, 1.3, 0.8]
                    })
                  }
                ]
              }
            ]}
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
          <View style={styles.text}>
            <Text style={styles.headline} numberOfLines={2}>
              {headline}
            </Text>
            <Text style={styles.sub}>From your saved searches</Text>
          </View>
          <Ionicons name="chevron-forward" size={18} color={storeLight.text.link} />
        </LinearGradient>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    minHeight: 56,
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 10,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: marketplaceLight.offer.freshBorder
  },
  dot: { width: 9, height: 9, borderRadius: 5, backgroundColor: storeLight.status.success },
  text: { flex: 1, gap: 1 },
  headline: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary },
  sub: { fontSize: 11, color: storeLight.text.muted }
});
