/**
 * The "this post is outperforming — promote it?" nudge (Post ads = violet).
 *
 * A soft violet-to-white gradient so it reads as an opportunity, not a warning
 * (warnings are the gold/orange attention banner). A slow spark bobs beside the
 * headline; it is atmosphere only and settles still under reduce-motion, and it
 * never conveys state — the copy does.
 *
 * Like everything on the Post-ads side, this is a preview: the suggestion engine
 * is unbacked, so the card is tagged and its reason line is sample copy. The
 * primary action leads into the (also-preview) promote flow; a dismiss keeps the
 * nudge from being sticky.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { adsLight } from "../../theme/adsLight";
import { useStorePress } from "../../theme/storeMotion";
import { useAdsSuggestionSpark } from "../../theme/adsMotion";
import type { PromotedContentType } from "../../api/adsDashboard";

export type SuggestionCardProps = {
  contentType: PromotedContentType;
  title: string;
  /** One line of preview reasoning, e.g. "3× your usual reach in 24h". */
  reason: string;
  onPromote: () => void;
  onDismiss: () => void;
  reducedMotion: boolean;
};

const TYPE_WORD: Record<PromotedContentType, string> = {
  post: "post",
  reel: "Reel",
  live: "live replay"
};

export function SuggestionCard({
  contentType,
  title,
  reason,
  onPromote,
  onDismiss,
  reducedMotion
}: SuggestionCardProps) {
  const spark = useAdsSuggestionSpark(reducedMotion, true);
  const press = useStorePress(reducedMotion, 0.98);

  return (
    <View
      style={styles.wrap}
      accessible
      accessibilityLabel={`Suggestion: your ${TYPE_WORD[contentType]} “${title}” is ${reason}. Promote it? Preview.`}
    >
      <LinearGradient
        colors={[adsLight.suggestion.from, adsLight.suggestion.to]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.card}
      >
        <View style={styles.headRow}>
          <Animated.View
            accessibilityElementsHidden
            importantForAccessibility="no"
            style={{
              transform: [
                {
                  translateY: spark.interpolate({ inputRange: [0, 1], outputRange: [0, -3] })
                }
              ]
            }}
          >
            <Ionicons name="sparkles" size={18} color={adsLight.post.base} />
          </Animated.View>
          <Text style={styles.headline}>This {TYPE_WORD[contentType]} is outperforming</Text>
          <View style={styles.previewBadge}>
            <Text style={styles.previewText}>Preview</Text>
          </View>
        </View>

        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
        <Text style={styles.reason} numberOfLines={2}>
          {reason}
        </Text>

        <View style={styles.actions}>
          <Animated.View style={[styles.promoteWrap, press.style]}>
            <Pressable
              onPress={onPromote}
              onPressIn={press.onPressIn}
              onPressOut={press.onPressOut}
              accessibilityRole="button"
              accessibilityLabel={`Promote ${title}`}
            >
              <LinearGradient
                colors={[adsLight.post.from, adsLight.post.to]}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={styles.promote}
              >
                <Text style={styles.promoteText}>Promote</Text>
              </LinearGradient>
            </Pressable>
          </Animated.View>
          <Pressable
            onPress={onDismiss}
            style={styles.dismiss}
            accessibilityRole="button"
            accessibilityLabel="Dismiss suggestion"
            hitSlop={6}
          >
            <Text style={styles.dismissText}>Not now</Text>
          </Pressable>
        </View>
      </LinearGradient>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderRadius: adsLight.radius.card, overflow: "hidden" },
  card: {
    padding: adsLight.space.card,
    gap: 8,
    borderRadius: adsLight.radius.card,
    borderWidth: 1,
    borderColor: adsLight.suggestion.border
  },
  headRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  headline: { flex: 1, fontSize: 14, fontWeight: "800", color: adsLight.text.primary },
  previewBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.post.tint
  },
  previewText: { fontSize: 10, fontWeight: "800", color: adsLight.post.base },
  title: { fontSize: 13, fontWeight: "700", color: adsLight.text.primary },
  reason: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 },
  actions: { flexDirection: "row", alignItems: "center", gap: 12, marginTop: 4 },
  promoteWrap: { borderRadius: adsLight.radius.pill, overflow: "hidden" },
  promote: {
    minHeight: adsLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 22,
    borderRadius: adsLight.radius.pill
  },
  promoteText: { fontSize: 14, fontWeight: "800", color: adsLight.post.onViolet },
  dismiss: { minHeight: adsLight.size.tapTarget, justifyContent: "center", paddingHorizontal: 8 },
  dismissText: { fontSize: 13, fontWeight: "700", color: adsLight.text.muted }
});
