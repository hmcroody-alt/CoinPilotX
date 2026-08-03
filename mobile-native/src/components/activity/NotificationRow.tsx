/**
 * One row in the Activity feed. Reads a normalized FeedNotification (from
 * activityFeed) and lays it out:
 *
 *   • Leading glyph — an actor avatar with a domain mini-badge for actor rows, or
 *     a standalone domain type-circle for system rows.
 *   • The plain-language sentence (never a raw template), a subject thumbnail
 *     when present, and a "3h ago · Marketplace" suffix.
 *   • Unread rows get a blue left edge + a faint tint and an unread dot.
 *   • Live rows get the pulsing red LIVE NOW treatment.
 *   • Up to two inline actions, which the derivation already trimmed to the
 *     offer's real state (an expired/answered offer arrives with none).
 *
 * The row computes none of this — relativeShort, the sentence, the actions and
 * the unread flag are all the derivation layer's. Tap and action handlers are the
 * parent's; a tapped row is what marks it read.
 */

import { useEffect, useRef } from "react";
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import { type FeedNotification, type InlineAction, domainSuffix, relativeShort } from "../../api/activityFeed";
import { TypeCircle } from "./TypeCircle";

export function NotificationRow({
  row,
  now,
  reducedMotion,
  onPress,
  onAction
}: {
  row: FeedNotification;
  now: number;
  reducedMotion?: boolean;
  onPress?: (row: FeedNotification) => void;
  onAction?: (action: InlineAction, row: FeedNotification) => void;
}) {
  const suffix = `${relativeShort(row.timestamp, now)} · ${row.live ? "Live" : domainSuffix(row.domain)}`;

  return (
    <Pressable
      style={[styles.row, row.unread ? styles.rowUnread : null]}
      accessibilityRole="button"
      accessibilityLabel={row.a11yLabel}
      onPress={() => onPress?.(row)}
    >
      {row.unread ? <View style={styles.unreadEdge} /> : null}

      <Leading row={row} reducedMotion={reducedMotion} />

      <View style={styles.body}>
        {row.live ? <LiveTag reducedMotion={reducedMotion} /> : null}
        <Text style={[styles.sentence, row.unread ? styles.sentenceUnread : null]} numberOfLines={3}>
          {row.sentence}
        </Text>
        <Text style={styles.suffix}>{suffix}</Text>

        {row.inlineActions.length ? (
          <View style={styles.actionRow}>
            {row.inlineActions.slice(0, 2).map((a) => (
              <Pressable
                key={a.key}
                style={[styles.actionBtn, a.intent === "primary" ? styles.actionPrimary : styles.actionNeutral]}
                accessibilityRole="button"
                accessibilityLabel={a.a11yLabel}
                onPress={() => onAction?.(a, row)}
              >
                <Text style={[styles.actionText, a.intent === "primary" ? styles.actionTextPrimary : styles.actionTextNeutral]}>
                  {a.label}
                </Text>
              </Pressable>
            ))}
          </View>
        ) : null}
      </View>

      <View style={styles.trailing}>
        {row.subjectThumbUrl ? <Image source={{ uri: row.subjectThumbUrl }} style={styles.thumb} /> : null}
        {row.unread ? <View style={styles.unreadDot} /> : null}
      </View>
    </Pressable>
  );
}

function Leading({ row, reducedMotion }: { row: FeedNotification; reducedMotion?: boolean }) {
  void reducedMotion;
  if (row.isSystem) {
    return <TypeCircle domain={row.domain} size={40} />;
  }
  return (
    <View style={styles.avatarWrap}>
      {row.actorAvatarUrl ? (
        <Image source={{ uri: row.actorAvatarUrl }} style={styles.avatar} />
      ) : (
        <View style={[styles.avatar, styles.avatarFallback]}>
          <Text style={styles.avatarInitial}>{(row.actorName || "?").slice(0, 1).toUpperCase()}</Text>
        </View>
      )}
      <View style={styles.miniBadge}>
        <TypeCircle domain={row.domain} size={20} mini />
      </View>
    </View>
  );
}

function LiveTag({ reducedMotion }: { reducedMotion?: boolean }) {
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (reducedMotion) return;
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 650, easing: Easing.out(Easing.ease), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 650, easing: Easing.in(Easing.ease), useNativeDriver: true })
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [pulse, reducedMotion]);
  const opacity = reducedMotion ? 1 : pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 0.4] });
  return (
    <Animated.View style={[styles.liveTag, { opacity }]}>
      <View style={styles.liveDot} />
      <Text style={styles.liveText}>LIVE NOW</Text>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    gap: 12,
    alignItems: "flex-start",
    paddingVertical: 12,
    paddingHorizontal: eventsLight.space.card,
    backgroundColor: eventsLight.bg.card
  },
  rowUnread: { backgroundColor: eventsLight.bg.unread },
  unreadEdge: {
    position: "absolute",
    left: 0,
    top: 0,
    bottom: 0,
    width: 3,
    backgroundColor: eventsLight.border.unreadEdge
  },
  avatarWrap: { width: 40, height: 40 },
  avatar: { width: 40, height: 40, borderRadius: 20, backgroundColor: eventsLight.bg.skeleton },
  avatarFallback: { alignItems: "center", justifyContent: "center" },
  avatarInitial: { fontSize: 15, fontWeight: "800", color: eventsLight.text.muted },
  miniBadge: { position: "absolute", right: -4, bottom: -4 },
  body: { flex: 1, gap: 3 },
  sentence: { fontSize: 14, color: eventsLight.text.primary, lineHeight: 19 },
  sentenceUnread: { fontWeight: "600" },
  suffix: { fontSize: 12, color: eventsLight.text.muted },
  actionRow: { flexDirection: "row", gap: 8, marginTop: 6 },
  actionBtn: {
    minHeight: 34,
    paddingHorizontal: 14,
    borderRadius: eventsLight.radius.control,
    alignItems: "center",
    justifyContent: "center"
  },
  actionPrimary: { backgroundColor: eventsLight.cta.from },
  actionNeutral: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.secondaryButton,
    backgroundColor: eventsLight.bg.card
  },
  actionText: { fontSize: 13, fontWeight: "800" },
  actionTextPrimary: { color: eventsLight.cta.text },
  actionTextNeutral: { color: eventsLight.text.primary },
  trailing: { alignItems: "center", gap: 6 },
  thumb: { width: 44, height: 44, borderRadius: eventsLight.radius.thumb, backgroundColor: eventsLight.bg.skeleton },
  unreadDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: eventsLight.border.unreadEdge },
  liveTag: { flexDirection: "row", alignItems: "center", gap: 5 },
  liveDot: { width: 7, height: 7, borderRadius: 3.5, backgroundColor: eventsLight.live.dot },
  liveText: { fontSize: 11, fontWeight: "900", color: eventsLight.live.label, letterSpacing: 0.5 }
});
