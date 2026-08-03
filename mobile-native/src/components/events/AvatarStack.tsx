/**
 * The RSVP avatar stack: the first few attendees overlapping, then a "+N" chip.
 * Reads a derived AttendeeSummary (from eventsManager) which has ALREADY applied
 * the privacy rule — a `visible:false` attendee never reaches this component but
 * is still counted in the overflow, so the numbers stay honest.
 */

import { Image, StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import type { AttendeeSummary } from "../../api/eventsManager";

export function AvatarStack({ summary, size = 28 }: { summary: AttendeeSummary; size?: number }) {
  if (summary.shown.length === 0 && summary.overflow === 0) return null;
  return (
    <View style={styles.row} accessibilityLabel={summary.a11yLabel}>
      {summary.shown.map((a, i) => (
        <View
          key={a.id}
          style={[
            styles.avatarWrap,
            { width: size, height: size, borderRadius: size / 2, marginLeft: i === 0 ? 0 : eventsLight.avatarStack.overlap }
          ]}
        >
          {a.avatarUrl ? (
            <Image source={{ uri: a.avatarUrl }} style={styles.avatar} />
          ) : (
            <View style={[styles.avatar, styles.avatarFallback]}>
              <Text style={styles.initial}>{a.name.slice(0, 1).toUpperCase()}</Text>
            </View>
          )}
        </View>
      ))}
      {summary.overflow > 0 ? (
        <View
          style={[
            styles.more,
            { width: size, height: size, borderRadius: size / 2, marginLeft: summary.shown.length ? eventsLight.avatarStack.overlap : 0 }
          ]}
        >
          <Text style={styles.moreText}>+{summary.overflow}</Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center" },
  avatarWrap: {
    borderWidth: eventsLight.avatarStack.ringWidth,
    borderColor: eventsLight.avatarStack.ring,
    overflow: "hidden",
    backgroundColor: eventsLight.bg.skeleton
  },
  avatar: { width: "100%", height: "100%" },
  avatarFallback: { alignItems: "center", justifyContent: "center", backgroundColor: eventsLight.avatarStack.moreBg },
  initial: { fontSize: 12, fontWeight: "800", color: eventsLight.avatarStack.moreText },
  more: {
    borderWidth: eventsLight.avatarStack.ringWidth,
    borderColor: eventsLight.avatarStack.ring,
    backgroundColor: eventsLight.avatarStack.moreBg,
    alignItems: "center",
    justifyContent: "center"
  },
  moreText: { fontSize: 11, fontWeight: "800", color: eventsLight.avatarStack.moreText }
});
