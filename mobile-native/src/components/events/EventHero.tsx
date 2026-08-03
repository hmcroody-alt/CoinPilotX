/**
 * The next-event hero: the cover, the type tag, title, when/where lines, the
 * countdown, the RSVP avatar stack + capacity bar, and the Manage / Share
 * actions. Every value is derived upstream (countdown, capacity, attendee
 * summary) so this component only lays them out — it computes no time, no fill,
 * and no attendee visibility itself.
 */

import { ImageBackground, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { eventsLight } from "../../theme/eventsLight";
import type { AttendeeSummary, Capacity, Countdown, HostedEvent } from "../../api/eventsManager";
import { CountdownUnits } from "./CountdownUnits";
import { CapacityBar } from "./CapacityBar";
import { AvatarStack } from "./AvatarStack";

export function EventHero({
  event,
  typeTag,
  whenLine,
  whereLine,
  countdown,
  capacity,
  attendees,
  onManage,
  onShare
}: {
  event: HostedEvent;
  typeTag: string;
  whenLine: string;
  whereLine?: string;
  countdown: Countdown;
  capacity: Capacity;
  attendees: AttendeeSummary;
  onManage?: (event: HostedEvent) => void;
  onShare?: (event: HostedEvent) => void;
}) {
  return (
    <View style={styles.card}>
      <Cover coverUrl={event.coverUrl} emoji={event.emoji} typeTag={typeTag} />

      <View style={styles.body}>
        <Text style={styles.title} numberOfLines={2}>
          {event.title}
        </Text>
        <Text style={styles.when} numberOfLines={1}>
          {whenLine}
        </Text>
        {whereLine ? (
          <Text style={styles.where} numberOfLines={1}>
            {whereLine}
          </Text>
        ) : null}

        <View style={styles.countdownRow}>
          <CountdownUnits countdown={countdown} />
        </View>

        {attendees.shown.length || attendees.overflow ? (
          <View style={styles.rsvpRow}>
            <AvatarStack summary={attendees} />
          </View>
        ) : null}

        <CapacityBar capacity={capacity} />

        <View style={styles.actionRow}>
          <Pressable
            style={[styles.btn, styles.btnPrimary]}
            accessibilityRole="button"
            accessibilityLabel={`Manage ${event.title}`}
            onPress={() => onManage?.(event)}
          >
            <Text style={[styles.btnText, styles.btnTextPrimary]}>Manage</Text>
          </Pressable>
          <Pressable
            style={[styles.btn, styles.btnSecondary]}
            accessibilityRole="button"
            accessibilityLabel={`Share ${event.title}`}
            onPress={() => onShare?.(event)}
          >
            <Text style={[styles.btnText, styles.btnTextSecondary]}>Share</Text>
          </Pressable>
        </View>
      </View>
    </View>
  );
}

function Cover({ coverUrl, emoji, typeTag }: { coverUrl?: string; emoji?: string; typeTag: string }) {
  const tag = (
    <View style={styles.tagPill}>
      <Text style={styles.tagText}>{typeTag}</Text>
    </View>
  );
  if (coverUrl) {
    return (
      <ImageBackground source={{ uri: coverUrl }} style={styles.cover} imageStyle={styles.coverImage}>
        <View style={styles.coverScrim}>{tag}</View>
      </ImageBackground>
    );
  }
  return (
    <LinearGradient
      colors={[eventsLight.cover.from, eventsLight.cover.mid, eventsLight.cover.to]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={styles.cover}
    >
      {emoji ? <Text style={styles.emoji}>{emoji}</Text> : null}
      <View style={styles.tagAbsolute}>{tag}</View>
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: eventsLight.bg.card,
    borderRadius: eventsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.hairline,
    overflow: "hidden"
  },
  cover: { height: 140, justifyContent: "flex-end" },
  coverImage: { resizeMode: "cover" },
  coverScrim: { flex: 1, justifyContent: "flex-start", padding: 10 },
  emoji: { fontSize: 44, textAlign: "center", marginTop: 34 },
  tagAbsolute: { position: "absolute", top: 10, left: 10 },
  tagPill: {
    alignSelf: "flex-start",
    backgroundColor: eventsLight.cover.tagBg,
    borderColor: eventsLight.cover.tagBorder,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: eventsLight.radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 4
  },
  tagText: { color: eventsLight.cover.tagText, fontSize: 11, fontWeight: "800", letterSpacing: 0.4 },
  body: { padding: eventsLight.space.card, gap: 8 },
  title: { fontSize: 18, fontWeight: "800", color: eventsLight.text.primary },
  when: { fontSize: 13, fontWeight: "700", color: eventsLight.text.primary },
  where: { fontSize: 12, color: eventsLight.text.muted },
  countdownRow: { marginTop: 2 },
  rsvpRow: { flexDirection: "row", alignItems: "center" },
  actionRow: { flexDirection: "row", gap: 8, marginTop: 2 },
  btn: {
    flex: 1,
    minHeight: eventsLight.size.tapTarget,
    borderRadius: eventsLight.radius.control,
    alignItems: "center",
    justifyContent: "center"
  },
  btnPrimary: { backgroundColor: eventsLight.cta.from },
  btnSecondary: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.secondaryButton,
    backgroundColor: eventsLight.bg.card
  },
  btnText: { fontSize: 14, fontWeight: "800" },
  btnTextPrimary: { color: eventsLight.cta.text },
  btnTextSecondary: { color: eventsLight.text.primary }
});
