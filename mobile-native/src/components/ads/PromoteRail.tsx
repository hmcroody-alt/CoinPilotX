/**
 * The horizontally scrolling "Promote a recent post" rail (Post ads = violet).
 *
 * It is a chooser, not a report: each tile names one of the author's recent
 * posts and offers to promote it. One tile may wear a "HOT" badge when the post
 * is outrunning its author's usual reach by `HOT_POST_MULTIPLE` or more — the
 * multiple is shown as a number next to the word, because a badge that only
 * says HOT tells the person nothing they can weigh.
 *
 * Like everything on the Post side this is a preview, so the rail states that
 * once in its heading rather than tagging every tile and burying the posts.
 */

import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import type { PromotedContentType } from "../../api/adsDashboard";

export type PromoteRailItem = {
  id: string;
  contentType: PromotedContentType;
  title: string;
  /** Already formatted, e.g. "31.2k reached". */
  reachLabel: string;
  /** e.g. "5×". Null on ordinary posts. */
  hotLabel: string | null;
};

export type PromoteRailProps = {
  items: PromoteRailItem[];
  onPromote: (id: string) => void;
  reducedMotion: boolean;
};

const GLYPH: Record<PromotedContentType, string> = { post: "▦", reel: "▶", live: "◉" };
const TYPE_WORD: Record<PromotedContentType, string> = {
  post: "Post",
  reel: "Reel",
  live: "Live replay"
};

export function PromoteRail({ items, onPromote }: PromoteRailProps) {
  if (!items.length) return null;

  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.row}
      accessibilityRole="list"
    >
      {items.map((item) => (
        <Pressable
          key={item.id}
          style={styles.tile}
          onPress={() => onPromote(item.id)}
          accessibilityRole="button"
          accessibilityLabel={
            `${TYPE_WORD[item.contentType]}: ${item.title}. ${item.reachLabel}.` +
            `${item.hotLabel ? ` Outperforming, ${item.hotLabel} your usual reach.` : ""}` +
            " Promote."
          }
        >
          <View style={styles.thumb}>
            <Text style={styles.glyph}>{GLYPH[item.contentType]}</Text>
            {item.hotLabel ? (
              <View style={styles.hot}>
                <Text style={styles.hotText}>{item.hotLabel} HOT</Text>
              </View>
            ) : null}
          </View>
          <Text style={styles.title} numberOfLines={2}>
            {item.title}
          </Text>
          <Text style={styles.reach} numberOfLines={1}>
            {item.reachLabel}
          </Text>
          <Text style={styles.action}>Promote ›</Text>
        </Pressable>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 10, paddingHorizontal: adsLight.space.card, paddingVertical: 4 },
  tile: {
    width: 132,
    gap: 4,
    padding: 10,
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline
  },
  thumb: {
    height: 74,
    borderRadius: adsLight.radius.thumb,
    backgroundColor: adsLight.post.tint,
    alignItems: "center",
    justifyContent: "center"
  },
  glyph: { fontSize: 24, color: adsLight.post.base },
  hot: {
    position: "absolute",
    top: 6,
    left: 6,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.post.base
  },
  hotText: { fontSize: 9, fontWeight: "900", color: adsLight.post.onViolet, letterSpacing: 0.3 },
  title: { fontSize: 12, fontWeight: "700", color: adsLight.text.primary, lineHeight: 16 },
  reach: { fontSize: 11, color: adsLight.text.muted },
  action: { fontSize: 11, fontWeight: "800", color: adsLight.post.base, marginTop: 2 }
});
